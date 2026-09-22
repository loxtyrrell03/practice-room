"""Read-only ASIMUT bookings for Practice Room, independent of its UI.

The canonical Booker owns login, agenda scans and reservation changes. This
adapter imports its validated display context through the installed skill;
it never launches a booking worker or reads credentials. Keep its cache in the
private data repo, never alongside the public app shell.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from zoneinfo import ZoneInfo


LONDON = ZoneInfo("Europe/London")
MAX_AGE = timedelta(minutes=30)
DEFAULT_PYTHON = Path("C:/Users/Lox/Desktop/repo/AsimutBooker/.venv/Scripts/python.exe")
DEFAULT_BRIDGE = Path("C:/Users/Lox/.codex/skills/asimut/scripts/booker.py")


class BookingImportError(ValueError):
    """Source evidence cannot safely replace the last accepted snapshot."""


def _timestamp(value):
    if not isinstance(value, str):
        raise BookingImportError("The agenda observation needs a timestamp.")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BookingImportError("The agenda timestamp is invalid.") from exc
    if result.tzinfo is None:
        raise BookingImportError("The agenda timestamp needs its timezone.")
    return result


def _date(value):
    try:
        result = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise BookingImportError("The agenda contains an invalid date.") from exc
    if result.isoformat() != value:
        raise BookingImportError("The agenda dates must use YYYY-MM-DD.")
    return result


def _clock(value):
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        raise BookingImportError("The agenda contains an invalid time.")
    try:
        hour, minute = int(value[:2]), int(value[3:])
    except ValueError as exc:
        raise BookingImportError("The agenda contains an invalid time.") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59) or value != f"{hour:02}:{minute:02}":
        raise BookingImportError("The agenda contains an invalid time.")
    return hour * 60 + minute


def _now(value=None):
    result = value or datetime.now(timezone.utc)
    if not isinstance(result, datetime) or result.tzinfo is None:
        raise ValueError("now must be a timezone-aware datetime")
    return result


def normalize_booker_context(context, *, now=None):
    """Validate complete finite coverage and retain exact reservation identity.

    Only isReservation=true supplies practice capacity. Other agenda entries
    are retained as conflicts. No title, proposed booking plan or daily target
    is interpreted as a real reservation. Missing coverage remains unknown.
    """
    now = _now(now)
    if not isinstance(context, dict):
        raise BookingImportError("Booker returned no readable context.")
    if context.get("timezone") != "Europe/London":
        raise BookingImportError("Booker returned an unexpected timezone.")
    sections = context.get("sections")
    if not isinstance(sections, dict):
        raise BookingImportError("Booker returned no agenda section.")
    agenda = sections.get("agenda")
    if not isinstance(agenda, dict) or agenda.get("available") is not True:
        raise BookingImportError("No complete Booker agenda is available yet.")
    if type(agenda.get("stale")) is not bool:
        raise BookingImportError("Booker did not report agenda freshness.")
    observed = _timestamp(agenda.get("observed_at"))
    age = now - observed
    if age < -timedelta(minutes=1):
        raise BookingImportError("The agenda observation is in the future.")
    covered = agenda.get("window")
    if not isinstance(covered, list) or not covered:
        raise BookingImportError("Booker did not prove which dates were scanned.")
    parsed_dates = [_date(value) for value in covered]
    if parsed_dates != sorted(set(parsed_dates)):
        raise BookingImportError("The agenda coverage is duplicated or out of order.")
    events = agenda.get("events")
    if not isinstance(events, list):
        raise BookingImportError("The agenda events are unreadable.")
    mutations = sections.get("mutations")
    if not isinstance(mutations, dict) or not isinstance(mutations.get("pending"), list):
        raise BookingImportError("Booker could not verify pending reservation changes.")
    reservations, conflicts, identities = [], [], set()
    for raw in events:
        if not isinstance(raw, dict) or type(raw.get("isReservation")) is not bool:
            raise BookingImportError("The agenda contains an unclassified event.")
        event_date = _date(raw.get("date"))
        if raw["date"] not in covered:
            raise BookingImportError("An event is outside the proven agenda coverage.")
        start, end = _clock(raw.get("startTime")), _clock(raw.get("endTime"))
        if end <= start:
            raise BookingImportError("An agenda event ends before it begins.")
        event_id = raw.get("eventId")
        if event_id is not None and (type(event_id) is not int or event_id <= 0):
            raise BookingImportError("An agenda event has an invalid identity.")
        if event_id is not None:
            if event_id in identities:
                raise BookingImportError("The agenda contains duplicate event identities.")
            identities.add(event_id)
        room = raw.get("room")
        if room is not None and (not isinstance(room, str) or not room.strip()):
            raise BookingImportError("An agenda event has an invalid room.")
        if raw["isReservation"] and (event_id is None or room is None):
            raise BookingImportError("A reservation is missing its exact ID or room.")
        title = raw.get("title")
        if not isinstance(title, str) or not title.strip():
            raise BookingImportError("An agenda event is missing its title.")
        start_at = datetime.combine(event_date, datetime.min.time(), LONDON) + timedelta(minutes=start)
        end_at = datetime.combine(event_date, datetime.min.time(), LONDON) + timedelta(minutes=end)
        event = {
            "id": f"asimut:{event_id}" if event_id is not None else None,
            "eventId": event_id, "date": raw["date"], "weekday": event_date.strftime("%A"),
            "startTime": raw["startTime"], "endTime": raw["endTime"],
            "startsAt": start_at.isoformat(), "endsAt": end_at.isoformat(),
            "room": room, "title": title, "bookedMinutes": end - start,
            "sourceUrl": f"https://rwcmd.asimut.net/arrangement?eventId={event_id}" if event_id else None,
        }
        (reservations if raw["isReservation"] else conflicts).append(event)
    for rows in (reservations, conflicts):
        rows.sort(key=lambda item: (item["date"], item["startTime"], item["eventId"] or 0))
    pending = len(mutations["pending"])
    stale = agenda["stale"] or age > MAX_AGE
    status = "pending" if pending else "stale" if stale else "ready"
    reason = (
        "Booker is verifying a reservation change. Existing plans need rechecking."
        if pending else "Bookings need a fresh complete ASIMUT check." if stale else ""
    )
    return {
        "version": 1, "source": "AsimutBooker", "timezone": "Europe/London",
        "observedAt": observed.isoformat(), "importedAt": now.isoformat(),
        "status": status, "reason": reason, "freshnessMaxAgeMinutes": 30,
        "completeCoveredDates": covered[:], "pendingMutationCount": pending,
        "reservations": reservations, "conflicts": conflicts,
    }


def reconcile_bookings(previous, current):
    """Retire missing future IDs only within a newer complete fresh scan.

    Disappearance means 'no longer present', not necessarily cancellation: a
    room upgrade can replace an ID. The app must preserve completed practice
    history and explicit piece assignments outside these returned changes.
    """
    result = deepcopy(current)
    result["changes"] = {"removedEventIds": [], "changedEventIds": [], "addedEventIds": []}
    if not previous or current["status"] != "ready":
        return result
    if _timestamp(current["observedAt"]) < _timestamp(previous["observedAt"]):
        raise BookingImportError("An older agenda cannot replace the accepted bookings.")
    old = {item["eventId"]: item for item in previous["reservations"]}
    new = {item["eventId"]: item for item in current["reservations"]}
    if _timestamp(current["observedAt"]) == _timestamp(previous["observedAt"]):
        if old != new or current["completeCoveredDates"] != previous["completeCoveredDates"]:
            raise BookingImportError("The same agenda observation changed unexpectedly.")
        return result
    scope = set(current["completeCoveredDates"])
    observed = _timestamp(current["observedAt"])
    result["changes"] = {
        "removedEventIds": sorted(event_id for event_id, item in old.items()
                                  if event_id not in new and item["date"] in scope
                                  and _timestamp(item["endsAt"]) > observed),
        "changedEventIds": sorted(event_id for event_id in old.keys() & new.keys()
                                  if any(old[event_id][key] != new[event_id][key]
                                         for key in ("date", "startTime", "endTime", "room"))),
        "addedEventIds": sorted(new.keys() - old.keys()),
    }
    return result


def booking_day(snapshot, day, *, now=None):
    """Return honest capacity for a date; overlaps count once, conflicts subtract.

    usableMinutes is room time before breaks/setup, never a playing target.
    An uncovered, stale or unavailable date deliberately has null capacity.
    """
    _date(day)
    now = _now(now)
    covered = day in snapshot.get("completeCoveredDates", [])
    fresh = (snapshot.get("status") == "ready" and
             timedelta(minutes=-1) <= now - _timestamp(snapshot["observedAt"]) <= MAX_AGE)
    reservations = [deepcopy(item) for item in snapshot.get("reservations", []) if item["date"] == day]
    conflicts = [deepcopy(item) for item in snapshot.get("conflicts", []) if item["date"] == day]
    available = set()
    if covered and fresh:
        for item in reservations:
            available.update(range(_clock(item["startTime"]), _clock(item["endTime"])))
        for item in conflicts:
            available.difference_update(range(_clock(item["startTime"]), _clock(item["endTime"])))
    status = snapshot.get("status", "unavailable")
    if status == "ready" and not fresh:
        status = "stale"
    return {"date": day, "covered": covered, "status": status if covered else "unknown",
            "reservations": reservations, "conflicts": conflicts,
            "usableMinutes": len(available) if covered and fresh else None}


def read_booker_context(*, python_path=DEFAULT_PYTHON, bridge_path=DEFAULT_BRIDGE, timeout=15):
    """Bounded display-only read; a timeout cannot interrupt a booking worker.

    Do not replace get_booker_context with refresh_booker_data here. The latter
    owns a long-running worker; it belongs in Booker's existing refresh UI.
    """
    python_path, bridge_path = Path(python_path), Path(bridge_path)
    if not python_path.is_file() or not bridge_path.is_file():
        raise BookingImportError("The canonical ASIMUT bridge is not installed on this PC.")
    with tempfile.TemporaryDirectory(prefix="practice-asimut-") as directory:
        args_path = Path(directory) / "args.json"
        args_path.write_text(json.dumps({"sections": ["agenda", "mutations"]}), encoding="utf-8")
        try:
            completed = subprocess.run(
                [str(python_path), str(bridge_path), "--read-only", "call", "get_booker_context",
                 "--args-file", str(args_path)],
                cwd=str(python_path.parent), capture_output=True, text=True, encoding="utf-8",
                timeout=timeout, check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except subprocess.TimeoutExpired as exc:
            raise BookingImportError("Reading Booker timed out. Your last bookings are kept.") from exc
        except OSError as exc:
            raise BookingImportError("The ASIMUT bridge could not start.") from exc
    try:
        payload = json.loads(completed.stdout)
        if completed.returncode or payload.get("dispatch_completed") is not True:
            raise ValueError("Bridge did not complete")
        rows = payload["results"]
        if len(rows) != 1 or rows[0]["tool"] != "get_booker_context":
            raise ValueError("Unexpected bridge response")
        return rows[0]["data"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BookingImportError("Booker did not return a complete readable agenda.") from exc


class BookingImporter:
    """Serialize refreshes and preserve the private last-good snapshot on failure."""

    def __init__(self, cache_path, *, reader=read_booker_context):
        self.cache_path = Path(cache_path)
        self.reader = reader
        self._lock = threading.Lock()

    def _cached(self):
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            # Round-trip through the source validator rather than trusting cache JSON.
            context = {"timezone": data["timezone"], "sections": {
                "agenda": {"available": True, "stale": False, "observed_at": data["observedAt"],
                           "window": data["completeCoveredDates"],
                           "events": [{**row, "isReservation": flag} for flag, key in
                                      ((True, "reservations"), (False, "conflicts")) for row in data[key]]},
                "mutations": {"pending": []},
            }}
            normalize_booker_context(context, now=_timestamp(data["observedAt"]))
            return data
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def refresh(self, *, now=None):
        now = _now(now)
        with self._lock:
            previous = self._cached()
            try:
                current = normalize_booker_context(self.reader(), now=now)
                if current["status"] != "ready":
                    return self._fallback(previous or current, current["reason"], now,
                                          status=current["status"], pending=current["pendingMutationCount"])
                result = reconcile_bookings(previous, current)
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.cache_path.parent,
                                                 prefix=self.cache_path.name + ".", suffix=".tmp", delete=False) as handle:
                    temp = Path(handle.name)
                    try:
                        json.dump(result, handle, ensure_ascii=False, indent=2)
                        handle.write("\n")
                    except BaseException:
                        handle.close()
                        temp.unlink(missing_ok=True)
                        raise
                try:
                    os.replace(temp, self.cache_path)
                finally:
                    temp.unlink(missing_ok=True)
                return result
            except (BookingImportError, OSError) as exc:
                return self._fallback(previous, str(exc), now)

    @staticmethod
    def _fallback(previous, reason, now, *, status="error", pending=None):
        result = deepcopy(previous) if previous else {
            "version": 1, "source": "AsimutBooker", "timezone": "Europe/London",
            "observedAt": None, "completeCoveredDates": [], "reservations": [], "conflicts": [],
            "freshnessMaxAgeMinutes": 30, "pendingMutationCount": None,
        }
        result.update(status=status, reason=reason, checkedAt=now.isoformat(),
                      changes={"removedEventIds": [], "changedEventIds": [], "addedEventIds": []})
        if pending is not None:
            result["pendingMutationCount"] = pending
        return result
