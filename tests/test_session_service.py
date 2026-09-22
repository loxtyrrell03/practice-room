"""Service/API boundaries for private booking-based plans; synthetic data only."""
import copy
from datetime import datetime, timedelta
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import academic_sessions
import server
import session_service
from coach_queue import CoachQueue
from practice_logs import ObservationPipeline
from session_service import SessionService, YEAR, SESSIONS


NOW = datetime(2026, 9, 22, 9, tzinfo=academic_sessions.UK)


def fixture_year():
    return {
        "schemaVersion": 1, "startDate": "2026-09-22", "title": "Test year",
        "dailyTargetMinutes": {"min": 240, "max": 360},
        "deadlines": [
            {"id": "february", "label": "February", "month": "2027-02", "date": None,
             "status": "provisional", "pieceIds": ["near"], "movementIdsByPiece": {"near": ["mvt-1"]}},
            {"id": "final", "label": "Final", "month": "2027-05", "date": None,
             "status": "provisional", "pieceIds": ["later"]},
        ],
    }


def fixture_state():
    return {"startDate": "2026-09-22", "recitalDate": None, "pieces": [
        {"id": "near", "title": "Near piece", "short": "Near", "deadlineIds": ["february"],
         "planning": {"weight": 1, "focus": "Connect the unfinished phrase", "passTest": "Return in context", "deadlineMonth": "2027-02"},
         "movements": [{"id": "mvt-1", "title": "I", "deadlineIds": ["february"], "planning": {"weight": 1, "focus": "Original focus", "passTest": "Original check", "deadlineMonth": "2027-02"}}]},
        {"id": "later", "title": "Later piece", "short": "Later", "deadlineIds": ["final"],
         "planning": {"weight": 1, "focus": "Learn the later phrase", "passTest": "A delayed return", "deadlineMonth": "2027-05"}},
    ], "today": {"date": "2026-09-22", "blocks": []}}


def fixture_snapshot():
    rows = []
    for day in ("2026-09-23", "2026-09-24", "2026-09-25"):
        for hour in (9, 12, 15):
            event_id = len(rows) + 1
            rows.append({"id": f"asimut:{event_id}", "eventId": event_id, "date": day,
                         "startTime": f"{hour:02}:00", "endTime": f"{hour+2:02}:00", "room": "Test Room",
                         "sourceUrl": f"https://example.invalid/event/{event_id}"})
    return {"status": "ready", "observedAt": NOW.isoformat(), "completeCoveredDates": ["2026-09-23", "2026-09-24", "2026-09-25"],
            "reservations": rows, "conflicts": []}


class Importer:
    def __init__(self, value=None):
        self.value = fixture_snapshot() if value is None else value
        self.calls = 0

    def refresh(self):
        self.calls += 1
        if isinstance(self.value, Exception):
            raise self.value
        return copy.deepcopy(self.value)


class ServiceFixture(unittest.TestCase):
    def setUp(self):
        self.now = NOW
        self.temp = tempfile.TemporaryDirectory(prefix="practice-service-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "data").mkdir()
        self.write(YEAR, fixture_year())
        self.write("data/state.json", fixture_state())
        self.write("data/spots.json", {"spots": []})
        self.write("data/asimut-bookings.json", fixture_snapshot())
        # Fix domain time without invoking a live importer or changing the host clock.
        for name in ("reconcile", "overview", "apply_action"):
            original = getattr(academic_sessions, name)
            replacement = lambda *a, _fn=original, **kw: _fn(*a, now=self.now, **kw)
            mocker = patch.object(session_service, name, side_effect=replacement)
            mocker.start()
            self.addCleanup(mocker.stop)
        self.importer = Importer()
        self.service = SessionService(self.root, importer=self.importer)

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def read(self, relative):
        return json.loads((self.root / relative).read_text(encoding="utf-8"))

    def first_playing(self):
        doc = self.service.get()
        session = next(s for s in doc["sessions"] if any(b["kind"] == "playing" for b in s["blocks"]))
        return session, next(b for b in session["blocks"] if b["kind"] == "playing")

    def begin_current_playing(self):
        snapshot = fixture_snapshot()
        snapshot["reservations"] = [{**snapshot["reservations"][0], "date": NOW.date().isoformat()}]
        snapshot["completeCoveredDates"] = [NOW.date().isoformat()]
        self.service.snapshot = snapshot
        self.write("data/asimut-bookings.json", snapshot)
        session, block = self.first_playing()
        self.service.action({"sessionId": session["id"], "blockId": block["id"], "action": "start"})
        self.now += timedelta(minutes=7)
        return session, block

    def prepare_coach_files(self):
        for relative, value in {
            "data/chat.json": {"messages": []}, "data/journal.json": {"entries": []},
            "data/observations.json": {"version": 2, "obs": []},
            "data/observation-jobs.json": {"version": 1, "batches": {}},
            "data/repertoire-changes.json": {"version": 1, "pending": [], "changes": []},
            "data/day-plans.json": {"version": 1, "plans": []},
            "data/weekly-plan.json": {"updated": "2026-09-22", "phases": []},
        }.items():
            self.write(relative, value)
        for relative in ("context/plan.md", "context/repertoire.md", "memory/MEMORY.md"):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# Original\n", encoding="utf-8")

    @staticmethod
    def allocations(doc):
        totals = {}
        for session in doc["sessions"]:
            for block in session["blocks"]:
                if block.get("pieceId"):
                    totals[block["pieceId"]] = totals.get(block["pieceId"], 0) + block["mins"]
        return totals


class SessionServiceTests(ServiceFixture):
    def test_fresh_read_persists_plan_without_ai_or_booking_mutation(self):
        with patch.object(server, "run_coach") as coach:
            result = self.service.get()
        self.assertEqual("current", result["sync"]["status"])
        self.assertEqual(9, len(result["sessions"]))
        saved = self.read(SESSIONS)
        self.assertEqual(result["inputSignature"], saved["inputSignature"])
        self.assertEqual([s["id"] for s in result["sessions"]], [s["id"] for s in saved["sessions"]])
        self.assertEqual([[b["id"] for b in s["blocks"]] for s in result["sessions"]], [[b["id"] for b in s["blocks"]] for s in saved["sessions"]])
        self.assertEqual(0, self.importer.calls)
        coach.assert_not_called()

    def test_stale_or_failed_refresh_retains_existing_plan(self):
        original = self.service.get()["sessions"]
        for changed in (
            {**fixture_snapshot(), "status": "stale", "reservations": []},
            {**fixture_snapshot(), "observedAt": (NOW-timedelta(minutes=31)).isoformat(), "reservations": []},
            RuntimeError("private bridge failure text must not be exposed"),
        ):
            with self.subTest(changed=type(changed).__name__):
                self.importer.value = changed
                self.service.refresh()
                result = self.service.get()
                self.assertEqual(original, result["sessions"])
                self.assertIn(result["sync"]["status"], {"stale", "unavailable"})
                self.assertNotIn("private bridge failure", json.dumps(result))

    def test_missing_snapshot_does_not_manufacture_booked_sessions(self):
        (self.root / "data/asimut-bookings.json").unlink()
        service = SessionService(self.root, importer=Importer({}))
        result = service.get()
        self.assertEqual([], result["sessions"])
        self.assertEqual("unavailable", result["sync"]["status"])

    def test_concurrent_refresh_has_one_import_and_releases_its_guard(self):
        entered, release = threading.Event(), threading.Event()
        importer = self.importer
        original = importer.refresh
        def delayed():
            entered.set()
            if not release.wait(3):
                raise TimeoutError("test release was not signalled")
            return original()
        with patch.object(importer, "refresh", side_effect=delayed):
            worker = threading.Thread(target=self.service.refresh)
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertTrue(self.service.get()["refreshing"])
                self.assertFalse(self.service.refresh())
            finally:
                release.set()
                worker.join(3)
            self.assertFalse(worker.is_alive())
        self.assertEqual(1, importer.calls)
        self.assertFalse(self.service.refreshing)
        self.assertTrue(self.service.refresh())

    def test_repeated_replan_failure_cannot_escape_and_kill_periodic_sync(self):
        self.service.get()
        saved = (self.root / SESSIONS).read_bytes()
        with patch.object(self.service, "_plan", side_effect=OSError("synthetic persistent write failure")):
            self.assertFalse(self.service.refresh())
        self.assertEqual(saved, (self.root / SESSIONS).read_bytes())
        self.assertFalse(self.service.refreshing)
        self.assertFalse(self.service.refresh_lock.locked())
        self.assertTrue(self.service.refresh())

    def test_periodic_worker_retries_unexpected_failures_and_start_is_idempotent(self):
        retried = threading.Event()
        calls = []
        def refresh():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("unexpected first refresh failure")
            retried.set()
            return True
        with patch.object(self.service, "refresh", side_effect=refresh):
            first = self.service.start(interval_seconds=.01)
            try:
                self.assertIs(first, self.service.start(interval_seconds=.01))
                self.assertTrue(retried.wait(2))
                self.assertTrue(first.is_alive())
            finally:
                self.service.stop()
        self.assertFalse(first.is_alive())
        self.assertGreaterEqual(len(calls), 2)

    def test_current_coach_priorities_replan_unstarted_work(self):
        before = self.service.get()
        state = self.read("data/state.json")
        state["pieces"][0]["planning"]["weight"] = 10
        state["pieces"][0]["movements"][0]["planning"].update(focus="Listen for the returning bass", passTest="Bass line survives the delayed check")
        self.write("data/state.json", state)
        after = self.service.get()
        self.assertNotEqual(before["inputSignature"], after["inputSignature"])
        self.assertGreater(self.allocations(after)["near"], self.allocations(before)["near"])
        blocks = [b for s in after["sessions"] for b in s["blocks"] if b.get("pieceId") == "near"]
        self.assertTrue(all("Listen for the returning bass" in json.dumps(b["steps"]) for b in blocks))

    def test_exact_deadline_changes_urgency_without_rewriting_coach_fields(self):
        before = self.service.get()
        self.service.preferences({"deadlines": [{"id": "february", "date": "2026-10-02"}]})
        after = self.service.get()
        deadline = self.service.year()["deadlines"][0]
        self.assertEqual("confirmed", deadline["status"])
        self.assertEqual("2026-10", deadline["month"])
        self.assertEqual("2027-02", self.read("data/state.json")["pieces"][0]["planning"]["deadlineMonth"])
        self.assertGreater(self.allocations(after)["near"], self.allocations(before)["near"])

    def test_preferences_invalid_input_is_atomic(self):
        initial = (self.root / YEAR).read_bytes()
        invalid = [
            {"dailyTargetMinutes": {"min": True, "max": 360}},
            {"dailyTargetMinutes": {"min": -1, "max": 360}},
            {"dailyTargetMinutes": {"min": 300, "max": 240}},
            {"dailyTargetMinutes": {"min": 240, "max": 601}},
            {"dailyTargetMinutes": {"min": 240.0, "max": 360}},
            {"dailyTargetMinutes": {"min": 200, "max": 300}, "deadlines": [{"id": "missing", "date": "2027-02-01"}]},
            {"deadlines": [{"id": "february", "date": "2027-02-30"}]},
            {"deadlines": [{"id": "february", "date": "20270201"}]},
            {"deadlines": [{"id": "february", "date": True}]},
            {"deadlines": [{"id": "february", "date": "2027-02-01"}, {"id": "missing", "date": "2027-05-01"}]},
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises((ValueError, TypeError)):
                    self.service.preferences(payload)
                self.assertEqual(initial, (self.root / YEAR).read_bytes())

    def test_academic_schema_rejects_invalid_dates_scopes_and_target_types(self):
        mutations = [
            lambda year: year.update(schemaVersion=True),
            lambda year: year.update(startDate="2026-09-31"),
            lambda year: year["dailyTargetMinutes"].update(max=601),
            lambda year: year["deadlines"][0].update(month="2027-13"),
            lambda year: year["deadlines"][0].update(date="2027-02-30", status="confirmed"),
            lambda year: year["deadlines"][0].update(date="2027-03-01", status="confirmed"),
            lambda year: year["deadlines"][0].update(status="confirmed"),
            lambda year: year["deadlines"].append(copy.deepcopy(year["deadlines"][0])),
            lambda year: year["deadlines"][0].update(pieceIds=["missing"]),
            lambda year: year["deadlines"][0].update(movementIdsByPiece={"near": ["missing"]}),
            lambda year: year.update(sessionLimits={"asimut:1": True}),
        ]
        session_service.validate_academic_year(fixture_year(), fixture_state())
        for i, mutate in enumerate(mutations):
            with self.subTest(case=i):
                year = fixture_year()
                mutate(year)
                with self.assertRaises(ValueError):
                    session_service.validate_academic_year(year, fixture_state())

    def test_coach_weights_reject_boolean_nonfinite_negative_and_invalid_movement_sum(self):
        for weight in (True, "10", float("inf"), float("nan"), 0, -1):
            with self.subTest(weight=weight):
                state = fixture_state()
                state["pieces"][0]["planning"]["weight"] = weight
                with self.assertRaises(ValueError):
                    session_service.validate_planning_state(state)
        state = fixture_state()
        state["pieces"][0]["movements"][0]["planning"]["weight"] = .8
        with self.assertRaisesRegex(ValueError, "sum to 1"):
            session_service.validate_planning_state(state)
        session_service.validate_planning_state({"pieces": [{"id": "legacy", "movements": [{"id": "one"}]}]})

    def test_invalid_coach_year_or_weights_do_not_land_in_durable_outputs(self):
        self.prepare_coach_files()
        pipeline = ObservationPipeline(self.root)
        pipeline.migrate()
        protected = {relative: (self.root / relative).read_bytes() for relative in (YEAR, "data/state.json")}
        for invalid_kind in ("target", "date", "weight"):
            def coach(stage, _batch):
                stage = Path(stage)
                year = json.loads((stage / YEAR).read_text(encoding="utf-8"))
                state = json.loads((stage / "data/state.json").read_text(encoding="utf-8"))
                if invalid_kind == "target":
                    year["dailyTargetMinutes"]["max"] = True
                elif invalid_kind == "date":
                    year["deadlines"][0].update(date="2027-02-30", status="confirmed")
                else:
                    state["pieces"][0]["planning"]["weight"] = "huge"
                (stage / YEAR).write_text(json.dumps(year), encoding="utf-8")
                (stage / "data/state.json").write_text(json.dumps(state), encoding="utf-8")
            with self.subTest(invalid_kind=invalid_kind):
                result = pipeline.process_for_coach(coach, source_key=f"invalid-{invalid_kind}", is_debrief=False, now=NOW)
                self.assertEqual("failed", result["status"])
                for relative, original in protected.items():
                    self.assertEqual(original, (self.root / relative).read_bytes())

    def test_preferences_are_durable_and_can_restore_unknown_date(self):
        self.service.preferences({"dailyTargetMinutes": {"min": 120, "max": 240}, "deadlines": [{"id": "final", "date": "2027-05-06"}]})
        restarted = SessionService(self.root, importer=Importer())
        self.assertEqual({"min": 120, "max": 240}, restarted.year()["dailyTargetMinutes"])
        self.assertEqual("2027-05-06", restarted.year()["deadlines"][1]["date"])
        restarted.preferences({"deadlines": [{"id": "final", "date": None}]})
        self.assertIsNone(restarted.year()["deadlines"][1]["date"])
        self.assertEqual("provisional", restarted.year()["deadlines"][1]["status"])

    def test_adjust_rejects_invalid_or_overbooking_time_without_writing(self):
        session, _ = self.first_playing()
        original = (self.root / YEAR).read_bytes()
        for minutes in (True, -1, 1.5, 1441, session["bookedMinutes"] + 1):
            with self.subTest(minutes=minutes), self.assertRaises(ValueError):
                self.service.adjust({"sessionId": session["id"], "availableMinutes": minutes})
        with self.assertRaises(ValueError):
            self.service.adjust({"sessionId": "missing", "availableMinutes": 30})
        self.assertEqual(original, (self.root / YEAR).read_bytes())

    def test_adjust_fits_every_block_to_the_shorter_session_and_persists(self):
        session, _ = self.first_playing()
        result = self.service.adjust({"sessionId": session["id"], "availableMinutes": 40})
        shortened = next(s for s in result["sessions"] if s["id"] == session["id"])
        self.assertLessEqual(sum(b["mins"] for b in shortened["blocks"]), 40)
        self.assertTrue(all(academic_sessions.instant(b["end"]) <= academic_sessions.instant(session["start"]) + timedelta(minutes=40) for b in shortened["blocks"]))
        restarted = SessionService(self.root, importer=Importer())
        self.assertEqual(40, restarted.year()["sessionLimits"][session["id"]])

    def test_zero_available_minutes_leaves_no_unstarted_activity(self):
        session, _ = self.first_playing()
        result = self.service.adjust({"sessionId": session["id"], "availableMinutes": 0})
        adjusted = next(s for s in result["sessions"] if s["id"] == session["id"])
        self.assertEqual([], adjusted["blocks"])

    def test_explicit_completion_survives_replan_and_process_restart(self):
        session, block = self.begin_current_playing()
        result = self.service.action({"sessionId": session["id"], "blockId": block["id"], "action": "complete"})
        saved = next(b for s in result["sessions"] for b in s["blocks"] if b["id"] == block["id"])
        self.assertTrue(saved["done"])
        restarted = SessionService(self.root, importer=Importer())
        self.assertTrue(next(b for s in restarted.get()["sessions"] for b in s["blocks"] if b["id"] == block["id"])["done"])
        self.assertEqual(7, sum(day["doneMinutes"] for day in result["days"]))

    def test_failed_post_action_replan_keeps_the_explicit_action_durable(self):
        session, block = self.begin_current_playing()
        original = self.service._plan
        def transient(*, force=False):
            if force:
                raise OSError("synthetic replan failure")
            return original(force=force)
        with patch.object(self.service, "_plan", side_effect=transient):
            try:
                self.service.action({"sessionId": session["id"], "blockId": block["id"], "action": "complete"})
            except OSError:
                pass
        restarted = SessionService(self.root, importer=Importer())
        self.assertTrue(next(b for s in restarted.get()["sessions"] for b in s["blocks"] if b["id"] == block["id"])["done"])

    def test_coach_year_phases_and_context_survive_both_durable_transactions(self):
        self.prepare_coach_files()
        self.service.get()
        protected_sessions = (self.root / SESSIONS).read_bytes()
        def coach(stage, _batch, job):
            stage = Path(stage)
            academic = json.loads((stage / YEAR).read_text(encoding="utf-8"))
            academic["deadlines"][1].update(date="2027-05-06", status="confirmed")
            (stage / YEAR).write_text(json.dumps(academic), encoding="utf-8")
            (stage / "data/weekly-plan.json").write_text(json.dumps({"updated": "2026-09-22", "phases": [{"id": "may", "title": "New phase"}]}), encoding="utf-8")
            (stage / "context/plan.md").write_text("# Updated trajectory\n", encoding="utf-8")
            (stage / "context/repertoire.md").write_text("# Updated repertoire context\n", encoding="utf-8")
            # An errant stage write to server-owned sessions must never land.
            (stage / SESSIONS).write_text('{"sessions":[]}', encoding="utf-8")
            chat = json.loads((stage / "data/chat.json").read_text(encoding="utf-8"))
            chat["messages"].append({"role": "coach", "ts": job["acceptedAt"], "text": "Updated your final date and preparation phases."})
            (stage / "data/chat.json").write_text(json.dumps(chat), encoding="utf-8")
        queue = CoachQueue(self.root, server.ClaudeCoachRunner(coach_run=coach), retry_base_seconds=0)
        queue.accept("My final recital date is 6 May 2027. Update its preparation phases.", "year-date-test")
        queue.drain_until_idle(ignore_retry_time=True, max_steps=1)
        job = queue.snapshot()["jobs"][0]
        self.assertEqual("done", job["state"], job.get("lastError"))
        self.assertEqual("2027-05-06", self.read(YEAR)["deadlines"][1]["date"])
        self.assertEqual("New phase", self.read("data/weekly-plan.json")["phases"][0]["title"])
        self.assertEqual("# Updated trajectory\n", (self.root / "context/plan.md").read_text(encoding="utf-8"))
        self.assertEqual("# Updated repertoire context\n", (self.root / "context/repertoire.md").read_text(encoding="utf-8"))
        self.assertEqual(protected_sessions, (self.root / SESSIONS).read_bytes())
        self.assertEqual(1, sum(m["role"] == "coach" for m in self.read("data/chat.json")["messages"]))


class SessionHttpTests(ServiceFixture):
    def setUp(self):
        super().setUp()
        self.assets = self.root / "public"
        self.assets.mkdir()
        (self.assets / "index.html").write_text("<title>Fixture</title>", encoding="utf-8")
        (self.assets / "server.py").write_text("private-source-sentinel", encoding="utf-8")
        (self.root / ".git").mkdir()
        (self.root / ".git/config").write_text("private-config-sentinel", encoding="utf-8")
        for name, value in (("DATA", self.root), ("HERE", self.assets), ("session_service", self.service)):
            mocker = patch.object(server, name, value)
            mocker.start()
            self.addCleanup(mocker.stop)
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": .01})
        self.thread.start()
        self.addCleanup(self.stop_http)

    def stop_http(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(3)

    def request(self, method, path, payload=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=3)
        body = json.dumps(payload).encode() if payload is not None else None
        supplied = {"Content-Type": "application/json", **(headers or {})}
        connection.request(method, path, body=body, headers=supplied)
        response = connection.getresponse()
        content = response.read()
        result = (response.status, dict(response.getheaders()), content)
        connection.close()
        return result

    def test_year_and_sessions_endpoints_use_private_service_and_no_store(self):
        for route, key in (("/api/year", "deadlines"), ("/api/sessions", "sessions")):
            status, headers, body = self.request("GET", route)
            self.assertEqual(200, status)
            self.assertIn(key, json.loads(body))
            self.assertIn("no-store", headers["Cache-Control"])

    def test_foreign_origin_cannot_refresh_change_preferences_or_write_files(self):
        original = (self.root / YEAR).read_bytes()
        with patch.object(self.service, "request_refresh") as refresh:
            for route in ("/api/sessions/refresh", "/api/preferences", "/api/file", "/api/sessions/action"):
                status, _, _ = self.request("POST", route, {"dailyTargetMinutes": {"min": 0, "max": 0}}, {"Origin": "https://untrusted.invalid"})
                self.assertEqual(403, status)
            refresh.assert_not_called()
        self.assertEqual(original, (self.root / YEAR).read_bytes())

    def test_foreign_host_cannot_read_or_modify_private_data(self):
        original = (self.root / YEAR).read_bytes()
        for method, route in (("GET", "/api/year"), ("POST", "/api/preferences")):
            status, _, _ = self.request(method, route, {"dailyTargetMinutes": {"min": 0, "max": 0}} if method == "POST" else None,
                                        {"Host": "untrusted.invalid"})
            self.assertEqual(403, status)
        self.assertEqual(original, (self.root / YEAR).read_bytes())

    def test_owned_file_cannot_be_overwritten_through_path_aliases(self):
        self.service.get()
        for relative in (YEAR, SESSIONS, "data/asimut-bookings.json"):
            original = (self.root / relative).read_bytes()
            for alias in (relative, relative.replace("data/", "data/./"), relative.replace("/", "\\"), "./" + relative, relative.upper()):
                with self.subTest(alias=alias):
                    status, _, _ = self.request("POST", "/api/file", {"path": alias, "content": "{}"})
                    self.assertIn(status, {400, 403, 409})
                    self.assertEqual(original, (self.root / relative).read_bytes())

    def test_static_get_and_head_cannot_expose_source_or_directory(self):
        for method in ("GET", "HEAD"):
            for path in ("/server.py", "/data-repo/", "/.git/config", "/%2e%2e/server.py"):
                with self.subTest(method=method, path=path):
                    status, _, body = self.request(method, path)
                    self.assertEqual(404, status)
                    self.assertNotIn(b"private-source-sentinel", body)

    def test_generic_file_read_has_no_private_configuration_or_path_escape(self):
        for value in (".git/config", "../outside", "C:/Windows/win.ini"):
            with self.subTest(value=value):
                status, _, body = self.request("GET", "/api/file?path=" + value)
                self.assertIn(status, {400, 403, 404})
                self.assertNotIn(b"private-config-sentinel", body)

    def test_non_object_payload_rejected_before_mutation(self):
        original = (self.root / YEAR).read_bytes()
        status, _, _ = self.request("POST", "/api/preferences", ["not", "an", "object"])
        self.assertEqual(400, status)
        self.assertEqual(original, (self.root / YEAR).read_bytes())


if __name__ == "__main__":
    unittest.main()
