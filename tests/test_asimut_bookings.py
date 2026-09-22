from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from asimut_bookings import (
    BookingImporter, BookingImportError, booking_day, normalize_booker_context,
    read_booker_context, reconcile_bookings,
)


NOW = datetime(2026, 9, 22, 16, 0, tzinfo=timezone.utc)


def event(event_id=101, *, reservation=True, day="2026-09-23", start="12:00", end="14:00", room="Room A"):
    return {"eventId": event_id, "isReservation": reservation, "date": day,
            "startTime": start, "endTime": end, "room": room,
            "title": "Reservation" if reservation else "Lesson"}


def context(*, events=None, observed=NOW, days=None, stale=False, pending=None):
    return {"timezone": "Europe/London", "sections": {
        "agenda": {"available": True, "stale": stale, "observed_at": observed.isoformat(),
                   "window": days or ["2026-09-22", "2026-09-23", "2026-09-24"],
                   "events": [event(), event(202, reservation=False, start="13:00", end="13:30")] if events is None else events},
        "mutations": {"pending": pending or []},
    }}


class NormalizationTests(unittest.TestCase):
    def test_booking_identity_conflicts_and_london_time(self):
        result = normalize_booker_context(context(), now=NOW)
        self.assertEqual("ready", result["status"])
        booking = result["reservations"][0]
        self.assertEqual("asimut:101", booking["id"])
        self.assertEqual(120, booking["bookedMinutes"])
        self.assertEqual("2026-09-23T12:00:00+01:00", booking["startsAt"])
        self.assertEqual([202], [item["eventId"] for item in result["conflicts"]])
        self.assertEqual(90, booking_day(result, "2026-09-23", now=NOW)["usableMinutes"])

    def test_winter_london_offset_not_fixed_bst(self):
        winter = datetime(2027, 1, 2, 10, tzinfo=timezone.utc)
        result = normalize_booker_context(context(events=[event(day="2027-01-02")], observed=winter,
                                                  days=["2027-01-02"]), now=winter)
        self.assertTrue(result["reservations"][0]["startsAt"].endswith("+00:00"))

    def test_unknown_date_is_not_zero_capacity(self):
        result = normalize_booker_context(context(), now=NOW)
        self.assertIsNone(booking_day(result, "2026-09-25", now=NOW)["usableMinutes"])
        self.assertEqual("unknown", booking_day(result, "2026-09-25", now=NOW)["status"])
        self.assertEqual(0, booking_day(result, "2026-09-24", now=NOW)["usableMinutes"])

    def test_staleness_ages_even_if_source_calls_it_fresh(self):
        result = normalize_booker_context(context(), now=NOW + timedelta(minutes=31))
        self.assertEqual("stale", result["status"])
        self.assertIsNone(booking_day(result, "2026-09-23", now=NOW + timedelta(minutes=31))["usableMinutes"])
        fresh = normalize_booker_context(context(), now=NOW)
        self.assertIsNone(booking_day(fresh, "2026-09-23", now=NOW + timedelta(minutes=31))["usableMinutes"])
        self.assertEqual("stale", booking_day(fresh, "2026-09-23", now=NOW + timedelta(minutes=31))["status"])

    def test_overlap_does_not_double_count_capacity(self):
        result = normalize_booker_context(context(events=[event(), event(102, start="13:00", end="15:00")]), now=NOW)
        self.assertEqual(180, booking_day(result, "2026-09-23", now=NOW)["usableMinutes"])

    def test_title_never_turns_lesson_into_reservation(self):
        raw = event(202, reservation=False)
        raw["title"] = "Reservation"
        result = normalize_booker_context(context(events=[raw]), now=NOW)
        self.assertEqual([], result["reservations"])

    def test_pending_mutations_block_planning_capacity(self):
        result = normalize_booker_context(context(pending=[{"id": "pending-change"}]), now=NOW)
        self.assertEqual("pending", result["status"])
        self.assertIsNone(booking_day(result, "2026-09-23", now=NOW)["usableMinutes"])

    def test_incomplete_or_malformed_evidence_fails_closed(self):
        mutations = [
            lambda raw: raw["sections"]["agenda"].update(available=False),
            lambda raw: raw["sections"]["agenda"].update(window=[]),
            lambda raw: raw["sections"]["agenda"].update(window=["2026-09-23", "2026-09-23"]),
            lambda raw: raw["sections"]["agenda"].update(observed_at="2026-09-22T16:00:00"),
            lambda raw: raw["sections"]["agenda"].update(observed_at=(NOW + timedelta(minutes=10)).isoformat()),
            lambda raw: raw["sections"]["agenda"]["events"][0].update(eventId=None),
            lambda raw: raw["sections"]["agenda"]["events"][0].update(eventId=True),
            lambda raw: raw["sections"]["agenda"]["events"][0].update(isReservation="true"),
            lambda raw: raw["sections"]["agenda"]["events"][0].update(date="2026-09-30"),
            lambda raw: raw["sections"]["agenda"]["events"][0].update(startTime="14:00"),
            lambda raw: raw["sections"]["agenda"]["events"][1].update(eventId=101),
            lambda raw: raw["sections"].pop("mutations"),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                raw = context()
                mutate(raw)
                with self.assertRaises(BookingImportError):
                    normalize_booker_context(raw, now=NOW)


class ReconciliationTests(unittest.TestCase):
    def test_new_complete_scan_reports_edit_add_and_removed(self):
        before = normalize_booker_context(context(events=[event(101), event(102)]), now=NOW)
        later = NOW + timedelta(minutes=2)
        after = normalize_booker_context(context(events=[event(101, end="13:00"), event(103)], observed=later), now=later)
        result = reconcile_bookings(before, after)
        self.assertEqual({"removedEventIds": [102], "changedEventIds": [101], "addedEventIds": [103]}, result["changes"])

    def test_missing_date_does_not_claim_cancellation(self):
        before = normalize_booker_context(context(events=[event(101, day="2026-09-24")]), now=NOW)
        later = NOW + timedelta(minutes=2)
        after = normalize_booker_context(context(events=[], days=["2026-09-22", "2026-09-23"], observed=later), now=later)
        self.assertEqual([], reconcile_bookings(before, after)["changes"]["removedEventIds"])

    def test_expired_reservation_not_reported_as_removed(self):
        before = normalize_booker_context(context(events=[event(day="2026-09-22")]), now=NOW)
        later = NOW + timedelta(minutes=2)
        after = normalize_booker_context(context(events=[], observed=later), now=later)
        self.assertEqual([], reconcile_bookings(before, after)["changes"]["removedEventIds"])

    def test_pending_or_stale_never_retires_booking(self):
        before = normalize_booker_context(context(), now=NOW)
        later = NOW + timedelta(minutes=2)
        for options in ({"stale": True}, {"pending": [{"id": "change"}]}):
            after = normalize_booker_context(context(events=[], observed=later, **options), now=later)
            self.assertEqual([], reconcile_bookings(before, after)["changes"]["removedEventIds"])

    def test_backwards_or_same_observation_changed_is_rejected(self):
        before = normalize_booker_context(context(), now=NOW)
        for observed in (NOW, NOW - timedelta(minutes=1)):
            after = normalize_booker_context(context(events=[], observed=observed), now=NOW)
            with self.assertRaises(BookingImportError):
                reconcile_bookings(before, after)


class ImportTests(unittest.TestCase):
    def test_failure_preserves_bytes_and_last_good_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private" / "bookings.json"
            importer = BookingImporter(path, reader=lambda: context())
            first = importer.refresh(now=NOW)
            saved = path.read_bytes()
            def unavailable():
                raise BookingImportError("Booker could not be reached")
            importer.reader = unavailable
            failed = importer.refresh(now=NOW + timedelta(minutes=2))
            self.assertEqual("error", failed["status"])
            self.assertEqual(first["reservations"], failed["reservations"])
            self.assertEqual(saved, path.read_bytes())
            self.assertEqual([], failed["changes"]["removedEventIds"])

    def test_stale_empty_agenda_cannot_erase_cached_bookings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bookings.json"
            importer = BookingImporter(path, reader=lambda: context())
            first = importer.refresh(now=NOW)
            importer.reader = lambda: context(events=[], stale=True)
            stale = importer.refresh(now=NOW + timedelta(minutes=31))
            self.assertEqual("stale", stale["status"])
            self.assertEqual(first["reservations"], stale["reservations"])
            self.assertEqual(first, json.loads(path.read_text()))

    def test_valid_fresh_empty_agenda_can_retire_cached_booking(self):
        with tempfile.TemporaryDirectory() as directory:
            importer = BookingImporter(Path(directory) / "bookings.json", reader=lambda: context())
            importer.refresh(now=NOW)
            later = NOW + timedelta(minutes=2)
            importer.reader = lambda: context(events=[], observed=later)
            result = importer.refresh(now=later)
            self.assertEqual([], result["reservations"])
            self.assertEqual([101], result["changes"]["removedEventIds"])

    def test_corrupt_cache_not_presented_as_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bookings.json"
            path.write_text('{"reservations": []}')
            importer = BookingImporter(path, reader=lambda: {})
            result = importer.refresh(now=NOW)
            self.assertEqual("error", result["status"])
            self.assertEqual([], result["completeCoveredDates"])
            self.assertIsNone(result["observedAt"])

    def test_bridge_is_read_only_bounded_and_never_starts_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            python, bridge = Path(directory) / "python", Path(directory) / "bridge.py"
            python.touch()
            bridge.touch()
            completed = subprocess.CompletedProcess([], 0, json.dumps({"dispatch_completed": True,
                "results": [{"tool": "get_booker_context", "data": context()}]}), "")
            with patch("asimut_bookings.subprocess.run", return_value=completed) as run:
                self.assertEqual(context(), read_booker_context(python_path=python, bridge_path=bridge))
                command = run.call_args.args[0]
                self.assertIn("--read-only", command)
                self.assertIn("get_booker_context", command)
                self.assertNotIn("refresh_booker_data", command)
                self.assertEqual(15, run.call_args.kwargs["timeout"])

    def test_bridge_timeout_becomes_specific_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bridge"
            path.touch()
            with patch("asimut_bookings.subprocess.run", side_effect=subprocess.TimeoutExpired([], 15)):
                with self.assertRaisesRegex(BookingImportError, "timed out"):
                    read_booker_context(python_path=path, bridge_path=path)


if __name__ == "__main__":
    unittest.main()
