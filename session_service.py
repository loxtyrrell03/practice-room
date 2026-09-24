"""Private persisted session planning; ASIMUT remains the reservation owner."""
from __future__ import annotations

import copy
from datetime import date
import json
import math
from pathlib import Path
import threading

from academic_sessions import UK, apply_action, overview, reconcile
from asimut_bookings import BookingImporter
from practice_logs import atomic_write_json
from practice_notebook import PracticeNotebook, preparation_routes
from performance_deadlines import update_performance

YEAR = "data/academic-year.json"
SESSIONS = "data/sessions.json"


def _calendar_date(value, label):
    if not isinstance(value, str):
        raise ValueError(f"{label} must use YYYY-MM-DD.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be a valid YYYY-MM-DD date.") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{label} must use YYYY-MM-DD.")
    return value


def _month(value, label):
    if not isinstance(value, str) or len(value) != 7:
        raise ValueError(f"{label} must use YYYY-MM.")
    _calendar_date(value + "-01", label)


def validate_academic_year(academic, state=None):
    """Validate the small persisted year contract before accepting an update."""
    if not isinstance(academic, dict) or type(academic.get("schemaVersion")) is not int or academic["schemaVersion"] != 1:
        raise ValueError("Academic year must use schemaVersion 1.")
    _calendar_date(academic.get("startDate"), "Year start")
    if not isinstance(academic.get("title"), str) or not academic["title"].strip():
        raise ValueError("Academic year needs a title.")
    target = academic.get("dailyTargetMinutes")
    if not isinstance(target, dict) or any(type(target.get(k)) is not int for k in ("min", "max")) or not 0 <= target["min"] <= target["max"] <= 600:
        raise ValueError("Use a daily practice range between 0 and 600 minutes.")
    deadlines = academic.get("deadlines")
    if not isinstance(deadlines, list):
        raise ValueError("Deadlines must be a list.")
    known_pieces = {p.get("id") for p in state.get("pieces", []) if isinstance(p, dict)} if state is not None else None
    seen = set()
    for deadline in deadlines:
        if not isinstance(deadline, dict) or not isinstance(deadline.get("id"), str) or not deadline["id"].strip() or deadline["id"] in seen:
            raise ValueError("Every deadline needs a unique identity.")
        seen.add(deadline["id"])
        if not isinstance(deadline.get("label"), str) or not deadline["label"].strip() or len(deadline["label"]) > 120:
            raise ValueError("A performance needs a name, up to 120 characters.")
        if deadline.get("priority", "high") not in {"low", "medium", "high"}:
            raise ValueError("Choose low, medium or high importance.")
        if type(deadline.get("revision", 0)) is not int or deadline.get("revision", 0) < 0:
            raise ValueError("Invalid performance revision.")
        if type(deadline.get("archived", False)) is not bool:
            raise ValueError("Invalid performance archive state.")
        _month(deadline.get("month"), "Deadline month")
        exact = deadline.get("date")
        if exact is not None:
            _calendar_date(exact, "Assessment date")
            if deadline["month"] != exact[:7]:
                raise ValueError("An exact assessment date must match its month.")
        if deadline.get("status") != ("confirmed" if exact else "provisional"):
            raise ValueError("An unknown date is provisional; an exact date is confirmed.")
        piece_ids = deadline.get("pieceIds")
        if not isinstance(piece_ids, list) or not piece_ids or any(not isinstance(pid, str) or not pid for pid in piece_ids) or len(set(piece_ids)) != len(piece_ids):
            raise ValueError("Deadline pieces must be distinct piece identities.")
        if known_pieces is not None and not set(piece_ids) <= known_pieces:
            raise ValueError("A deadline refers to a piece outside the active programme.")
        scope = deadline.get("movementIdsByPiece", {})
        if not isinstance(scope, dict) or not set(scope) <= set(piece_ids):
            raise ValueError("Deadline movement scope must refer to its pieces.")
        for pid, movement_ids in scope.items():
            if not isinstance(movement_ids, list) or not movement_ids or any(not isinstance(mid, str) or not mid for mid in movement_ids) or len(set(movement_ids)) != len(movement_ids):
                raise ValueError("Deadline movement identities must be distinct.")
            if state is not None:
                piece = next(p for p in state["pieces"] if p.get("id") == pid)
                known = {m.get("id") for m in piece.get("movements", []) if isinstance(m, dict)}
                if not set(movement_ids) <= known:
                    raise ValueError("A deadline refers to an unknown movement.")
    limits = academic.get("sessionLimits", {})
    if not isinstance(limits, dict) or any(not isinstance(sid, str) or not sid or type(mins) is not int or not 0 <= mins <= 1440 for sid, mins in limits.items()):
        raise ValueError("Session limits must be whole minutes from 0 to 1440.")


def validate_planning_state(state):
    """Keep coach scheduling fields typed; tolerate legacy pieces without them."""
    if not isinstance(state, dict) or not isinstance(state.get("pieces"), list):
        raise ValueError("Practice state needs a pieces list.")
    for piece in state["pieces"]:
        if not isinstance(piece, dict):
            raise ValueError("Every piece must be an object.")
        movements = piece.get("movements") or []
        if not isinstance(movements, list) or any(not isinstance(m, dict) for m in movements):
            raise ValueError("Piece movements must be objects in a list.")
        movement_weights = []
        for item in [piece, *movements]:
            plan = item.get("planning")
            if plan is None:
                continue
            if not isinstance(plan, dict):
                raise ValueError("Planning must be an object.")
            if "weight" in plan:
                weight = plan["weight"]
                if type(weight) not in (int, float) or not math.isfinite(weight) or weight <= 0:
                    raise ValueError("Practice weights must be positive finite numbers.")
                if item is not piece:
                    movement_weights.append(weight)
            if plan.get("deadlineMonth") is not None:
                _month(plan["deadlineMonth"], "Planning deadline month")
            for field in ("focus", "passTest"):
                if field in plan and not isinstance(plan[field], str):
                    raise ValueError(f"Planning {field} must be text.")
            if "offBench" in plan and type(plan["offBench"]) is not bool:
                raise ValueError("Off-bench planning must be true or false.")
        if movement_weights and (len(movement_weights) != len(movements) or abs(sum(movement_weights) - 1) > .0001):
            raise ValueError("Movement weights must all be supplied and sum to 1.")


def read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return copy.deepcopy(default)


class SessionService:
    def __init__(self, root, lock=None, importer=None):
        self.root = Path(root)
        self.lock = lock or threading.RLock()
        self.importer = importer or BookingImporter(self.root / "data/asimut-bookings.json")
        self.refresh_lock = threading.Lock()
        self.refreshing = False
        self.last_error = None
        self.snapshot = read(self.root / "data/asimut-bookings.json", {})
        self.stop_event = threading.Event()
        self.worker = None
        self.lifecycle_lock = threading.Lock()

    def year(self):
        with self.lock:
            return read(self.root / YEAR, {})

    def _plan(self, *, force=False):
        state = read(self.root / "data/state.json", {})
        academic = read(self.root / YEAR, {})
        previous = read(self.root / SESSIONS, {})
        if not academic:
            return previous
        validate_academic_year(academic, state)
        validate_planning_state(state)
        notebook = PracticeNotebook(self.root, self.lock).load()
        result = reconcile(self.snapshot, state, academic, previous,
                           read(self.root / "data/spots.json", {"spots": []}), force=force,
                           tasks=notebook["tasks"], routes=preparation_routes(state, academic, notebook["stages"]))
        if result != previous:
            atomic_write_json(self.root / SESSIONS, result)
        return result

    def refresh(self):
        if not self.refresh_lock.acquire(blocking=False):
            return False
        self.refreshing = True
        try:
            snapshot = self.importer.refresh()
            with self.lock:
                self.snapshot = snapshot
                self._plan()
            self.last_error = snapshot.get("reason") if snapshot.get("status") != "ready" else None
            return True
        except Exception as exc:
            self.last_error = "Bookings could not be checked. Previous sessions are retained."
            with self.lock:
                self.snapshot = {**self.snapshot, "status": "error"}
                try:
                    self._plan()
                except Exception:
                    # An unavailable disk or invalid new input must not kill the
                    # periodic worker while it is trying to preserve old plans.
                    pass
            return False
        finally:
            self.refreshing = False
            self.refresh_lock.release()

    def request_refresh(self):
        if not self.refreshing:
            threading.Thread(target=self.refresh, daemon=True, name="practice-booking-refresh").start()

    def start(self, interval_seconds=60):
        if interval_seconds <= 0:
            raise ValueError("Refresh interval must be positive.")
        with self.lifecycle_lock:
            if self.worker and self.worker.is_alive():
                return self.worker
            self.stop_event.clear()
            def loop():
                while not self.stop_event.is_set():
                    try:
                        self.refresh()
                    except Exception:
                        self.last_error = "Bookings could not be checked. The next scheduled check will retry."
                    self.stop_event.wait(interval_seconds)
            self.worker = threading.Thread(target=loop, daemon=True, name="practice-booking-sync")
            self.worker.start()
            return self.worker

    def stop(self, timeout=2):
        self.stop_event.set()
        if self.worker and self.worker is not threading.current_thread():
            self.worker.join(timeout)

    def get(self):
        with self.lock:
            result = overview(self._plan(), self.year())
            result["refreshing"] = self.refreshing
            if self.last_error:
                result.setdefault("sync", {})["message"] = self.last_error
            return result

    def action(self, payload):
        with self.lock:
            current = self._plan()
            updated = apply_action(current, payload)
            atomic_write_json(self.root / SESSIONS, updated)
            self._plan(force=True)
            return self.get()

    def adjust(self, payload):
        sid = str(payload.get("sessionId") or "")
        minutes = payload.get("availableMinutes")
        if type(minutes) is not int or not 0 <= minutes <= 1440:
            raise ValueError("Available time must be a whole number of minutes from 0 to 1440.")
        with self.lock:
            doc = read(self.root / SESSIONS, {})
            session = next((s for s in doc.get("sessions", []) if s["id"] == sid), None)
            if not session:
                raise ValueError("Session changed. Refresh the schedule.")
            if minutes > session["bookedMinutes"]:
                raise ValueError("Practice cannot extend past the confirmed reservation.")
            academic = self.year()
            academic.setdefault("sessionLimits", {})[sid] = minutes
            validate_academic_year(academic, read(self.root / "data/state.json", {}))
            atomic_write_json(self.root / YEAR, academic)
            self._plan(force=True)
            return self.get()

    def preferences(self, payload):
        with self.lock:
            academic = self.year()
            if "performance" in payload:
                update_performance(academic, payload["performance"])
            target = payload.get("dailyTargetMinutes")
            if target is not None:
                if not isinstance(target, dict) or any(type(target.get(k)) is not int for k in ("min", "max")) or not 0 <= target["min"] <= target["max"] <= 600:
                    raise ValueError("Use a daily practice range between 0 and 600 minutes.")
                academic["dailyTargetMinutes"] = {k: target[k] for k in ("min", "max")}
            deadlines = payload.get("deadlines")
            if deadlines is not None:
                if not isinstance(deadlines, list):
                    raise ValueError("Deadlines must be a list.")
                for update in deadlines:
                    if not isinstance(update, dict):
                        raise ValueError("Invalid deadline.")
                    item = next((d for d in academic.get("deadlines", []) if d["id"] == update.get("id")), None)
                    if not item:
                        raise ValueError("Unknown deadline.")
                    if "revision" in update and update["revision"] != item.get("revision", 0):
                        raise ValueError("This performance changed on another device. Reopen settings before saving.")
                    if item.get("archived"):
                        raise ValueError("This performance was removed. Reopen settings before saving.")
                    value = update.get("date")
                    if value is not None:
                        _calendar_date(value, "Assessment date")
                    item["revision"] = item.get("revision", 0) + 1
                    item.pop("lastRequestId", None)
                    item.pop("lastRequestSignature", None)
                    item["date"] = value
                    item["status"] = "confirmed" if value else "provisional"
                    if value:
                        item["month"] = value[:7]
            validate_academic_year(academic, read(self.root / "data/state.json", {}))
            atomic_write_json(self.root / YEAR, academic)
            self._plan(force=True)
            return academic
