"""Durable practice-log intake and daily coach consolidation.

The browser talks to this module through server.py.  Claude never edits the
live data tree directly: each batch runs in an isolated copy, and its validated
outputs are persisted as a prepared transaction before they are applied.  A
restart can therefore finish the same output instead of asking Claude to
produce the effects again.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import uuid
from datetime import date, datetime, time as clock_time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


SCHEMA_VERSION = 3
JOBS_VERSION = 1
UK_TZ = ZoneInfo("Europe/London")
DEFAULT_DAILY_TIME = "20:30"
OBSERVATIONS_REL = "data/observations.json"
JOBS_REL = "data/observation-jobs.json"
DAY_PLANS_REL = "data/day-plans.json"
DAILY_OUTPUTS = (
    "data/spots.json",
    "data/state.json",
    "memory/MEMORY.md",
)
COACH_OUTPUTS = (
    "data/chat.json",
    "data/state.json",
    DAY_PLANS_REL,
    "data/journal.json",
    "data/spots.json",
    "memory/MEMORY.md",
)
JSON_OUTPUTS = {
    "data/chat.json",
    "data/state.json",
    DAY_PLANS_REL,
    "data/journal.json",
    "data/spots.json",
}
DAILY_STATE_PIECE_FIELDS = {
    "security",
    "tempoPct",
    "attention",
    "note",
    "lastCold",
}


class PipelineError(RuntimeError):
    """A recoverable observation-pipeline failure."""


class TransactionConflict(PipelineError):
    """A live file changed after the staged coach run."""


def parse_daily_time(value: str | None) -> clock_time:
    value = (value or DEFAULT_DAILY_TIME).strip()
    match = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", value)
    if not match:
        raise ValueError("COACH_DAILY_LOG_TIME must be HH:MM (24-hour UK time)")
    return clock_time(int(match.group(1)), int(match.group(2)))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def iso_z(value: datetime) -> str:
    return ensure_aware(value).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return ensure_aware(parsed)
    except (TypeError, ValueError):
        return None


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_text(path: Path, default: str | None = None) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if default is None:
            raise
        return default


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def atomic_write_json(path: Path, value: object) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def validate_json_text(rel: str, text: str) -> None:
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        raise PipelineError(f"{rel} is not valid JSON: {exc}") from exc


def _short_text(value: object, field: str, maximum: int, required: bool = True) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise ValueError(f"{field} is required")
    if len(text) > maximum:
        raise ValueError(f"{field} is too long")
    return text


class ObservationPipeline:
    """Thread-safe durable observation store and batch processor."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        daily_time: str | None = None,
        now_fn=utc_now,
        sync_callback=None,
        storage_lock: threading.RLock | None = None,
        run_lock: threading.Lock | None = None,
        after_apply_hook=None,
    ):
        self.data_dir = Path(data_dir).resolve()
        self.observations_path = self.data_dir / OBSERVATIONS_REL
        self.jobs_path = self.data_dir / JOBS_REL
        self.daily_time = parse_daily_time(daily_time)
        self.daily_time_text = self.daily_time.strftime("%H:%M")
        self.now_fn = now_fn
        self.sync_callback = sync_callback
        self.lock = storage_lock or threading.RLock()
        self.run_lock = run_lock or threading.Lock()
        self.after_apply_hook = after_apply_hook

    def _now(self, supplied: datetime | None = None) -> datetime:
        return ensure_aware(supplied or self.now_fn()).astimezone(timezone.utc)

    def _path(self, rel: str) -> Path:
        path = (self.data_dir / rel).resolve()
        try:
            path.relative_to(self.data_dir)
        except ValueError as exc:
            raise ValueError("bad data path") from exc
        return path

    def _empty_observations(self) -> dict:
        return {"version": SCHEMA_VERSION, "obs": []}

    def _empty_jobs(self) -> dict:
        return {
            "version": JOBS_VERSION,
            "timezone": "Europe/London",
            "dailyTime": self.daily_time_text,
            "batches": {},
        }

    def _load_observations_unlocked(self) -> tuple[dict, bool]:
        try:
            doc = json.loads(self.observations_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return self._empty_observations(), True
        if not isinstance(doc, dict):
            raise PipelineError("observations.json must contain an object")
        rows = doc.get("obs")
        if not isinstance(rows, list):
            raise PipelineError("observations.json must contain an obs array")

        changed = doc.get("version") != SCHEMA_VERSION
        doc["version"] = SCHEMA_VERSION
        seen_ids: set[str] = set()
        seen_clients: set[str] = set()
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise PipelineError(f"observation {index} must be an object")
            legacy_key = "|".join(
                str(row.get(key, "")) for key in ("ts", "day", "blockId", "block", "text")
            )
            observation_id = row.get("id")
            if not observation_id or observation_id in seen_ids:
                observation_id = f"legacy-{sha_text(f'{legacy_key}|{index}')[:20]}"
                row["id"] = observation_id
                changed = True
            seen_ids.add(observation_id)

            client_id = row.get("clientId")
            if not client_id or client_id in seen_clients:
                client_id = observation_id
                row["clientId"] = client_id
                changed = True
            seen_clients.add(client_id)

            original_status = row.get("status")
            status = {
                "new": "pending",
                "queued": "pending",
            }.get(original_status, original_status)
            if status not in {"pending", "processing", "processed", "failed"}:
                status = "pending"
            if row.get("status") != status:
                row["status"] = status
                changed = True

            ts = parse_iso(row.get("ts")) or self._now()
            local_date = ts.astimezone(UK_TZ).date().isoformat()
            defaults = {
                "pieceId": None,
                "movementId": None,
                "movement": None,
                "localDate": local_date,
                "savedAt": row.get("ts") or iso_z(ts),
                "attempts": 0,
                "batchId": None,
                "processingStartedAt": None,
                "processedAt": None,
                "processedBy": None,
                "acknowledgedAt": None,
                "lastError": None,
                "nextAttemptAt": None,
            }
            if status == "processed":
                defaults["processedAt"] = row.get("processedAt") or row.get("ts") or iso_z(ts)
                defaults["processedBy"] = row.get("processedBy") or "legacy-coach"
                defaults["acknowledgedAt"] = (
                    row.get("acknowledgedAt") or defaults["processedAt"]
                )
            for key, value in defaults.items():
                if key not in row:
                    row[key] = value
                    changed = True
        return doc, changed

    def _load_jobs_unlocked(self) -> tuple[dict, bool]:
        try:
            doc = json.loads(self.jobs_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return self._empty_jobs(), True
        if not isinstance(doc, dict) or not isinstance(doc.get("batches", {}), dict):
            raise PipelineError("observation-jobs.json has an invalid shape")
        changed = False
        if doc.get("version") != JOBS_VERSION:
            doc["version"] = JOBS_VERSION
            changed = True
        if doc.get("timezone") != "Europe/London":
            doc["timezone"] = "Europe/London"
            changed = True
        if doc.get("dailyTime") != self.daily_time_text:
            doc["dailyTime"] = self.daily_time_text
            changed = True
        doc.setdefault("batches", {})
        return doc, changed

    def migrate(self) -> None:
        with self.lock:
            observations, observations_changed = self._load_observations_unlocked()
            jobs, jobs_changed = self._load_jobs_unlocked()
            day_plans_path = self._path(DAY_PLANS_REL)
            if not day_plans_path.exists():
                atomic_write_json(day_plans_path, {"version": 1, "plans": []})
            if observations_changed:
                atomic_write_json(self.observations_path, observations)
            if jobs_changed:
                atomic_write_json(self.jobs_path, jobs)

    def submit(self, payload: dict, *, now: datetime | None = None) -> tuple[dict, bool]:
        """Persist one accepted log. Returns (entry, newly_created)."""
        if not isinstance(payload, dict):
            raise ValueError("bad observation")
        client_id = _short_text(payload.get("clientId"), "clientId", 128, required=False)
        if not client_id:
            client_id = uuid.uuid4().hex
        text = _short_text(payload.get("text"), "text", 300)
        block_id = _short_text(payload.get("blockId"), "blockId", 120)
        block = _short_text(payload.get("block"), "block", 200)
        piece_id = _short_text(
            payload.get("pieceId"), "pieceId", 120, required=False
        ) or None
        movement_id = _short_text(
            payload.get("movementId"), "movementId", 120, required=False
        ) or None
        movement = _short_text(
            payload.get("movement"), "movement", 200, required=False
        ) or None
        if bool(movement_id) != bool(movement):
            raise ValueError("movementId and movement must be supplied together")
        try:
            day = int(payload.get("day"))
        except (TypeError, ValueError) as exc:
            raise ValueError("day must be a number") from exc
        if day < -1000 or day > 10000:
            raise ValueError("day is out of range")

        current = self._now(now)
        with self.lock:
            observations, changed = self._load_observations_unlocked()
            for existing in observations["obs"]:
                if existing.get("clientId") == client_id:
                    if changed:
                        atomic_write_json(self.observations_path, observations)
                    return copy.deepcopy(existing), False
            entry = {
                "id": f"obs-{current.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:12]}",
                "clientId": client_id,
                "ts": iso_z(current),
                "localDate": current.astimezone(UK_TZ).date().isoformat(),
                "day": day,
                "blockId": block_id,
                "block": block,
                "pieceId": piece_id,
                "movementId": movement_id,
                "movement": movement,
                "text": text,
                "status": "pending",
                "savedAt": iso_z(current),
                "attempts": 0,
                "batchId": None,
                "processingStartedAt": None,
                "processedAt": None,
                "processedBy": None,
                "acknowledgedAt": None,
                "lastError": None,
                "nextAttemptAt": None,
            }
            observations["obs"].append(entry)
            atomic_write_json(self.observations_path, observations)
            return copy.deepcopy(entry), True

    def due_slot(self, supplied: datetime | None = None) -> tuple[str, datetime]:
        current = self._now(supplied).astimezone(UK_TZ)
        due_today = datetime.combine(current.date(), self.daily_time, tzinfo=UK_TZ)
        due_at = due_today if current >= due_today else due_today - timedelta(days=1)
        return due_at.date().isoformat(), due_at

    def next_due(self, supplied: datetime | None = None) -> datetime:
        current = self._now(supplied).astimezone(UK_TZ)
        candidate = datetime.combine(current.date(), self.daily_time, tzinfo=UK_TZ)
        if current >= candidate:
            candidate += timedelta(days=1)
        return candidate

    def summary(self, *, now: datetime | None = None) -> dict:
        with self.lock:
            observations, changed = self._load_observations_unlocked()
            if changed:
                atomic_write_json(self.observations_path, observations)
            counts = {key: 0 for key in ("pending", "processing", "processed", "failed")}
            for row in observations["obs"]:
                counts[row["status"]] = counts.get(row["status"], 0) + 1
        return {
            "counts": counts,
            "timezone": "Europe/London",
            "dailyTime": self.daily_time_text,
            "nextDueAt": iso_z(self.next_due(now)),
        }

    def acknowledge_processed(
        self,
        observation_ids,
        *,
        now: datetime | None = None,
    ) -> int:
        """Repair acknowledgment after evidence is visibly placed in a plan."""
        wanted = {str(observation_id) for observation_id in observation_ids}
        if not wanted:
            return 0
        acknowledged_at = iso_z(self._now(now))
        changed = 0
        with self.lock:
            observations, migrated = self._load_observations_unlocked()
            for row in observations["obs"]:
                if (
                    row.get("id") in wanted
                    and row.get("status") == "processed"
                    and not row.get("acknowledgedAt")
                ):
                    row["acknowledgedAt"] = acknowledged_at
                    changed += 1
            if migrated or changed:
                atomic_write_json(self.observations_path, observations)
        return changed

    def _eligible(
        self,
        observations: dict,
        cutoff: datetime,
        *,
        include_failed: bool = True,
    ) -> list[dict]:
        cutoff_utc = ensure_aware(cutoff).astimezone(timezone.utc)
        eligible = []
        for row in observations["obs"]:
            if row.get("status") == "pending" or (
                include_failed and row.get("status") == "failed"
            ):
                timestamp = parse_iso(row.get("savedAt") or row.get("ts"))
                if timestamp is None or timestamp.astimezone(timezone.utc) <= cutoff_utc:
                    eligible.append(row)
        return eligible

    def _claim_batch_unlocked(
        self,
        *,
        batch_id: str,
        source: str,
        route_rows: list[dict],
        review_rows: list[dict],
        current: datetime,
        due_local_date: str | None,
        scheduled_for: datetime | None,
        acknowledge: bool,
        jobs: dict,
        observations: dict,
    ) -> dict:
        prior = jobs["batches"].get(batch_id, {})
        attempts = int(prior.get("attempts", 0)) + 1
        route_ids = [row["id"] for row in route_rows]
        review_ids = [row["id"] for row in review_rows]
        batch = {
            "id": batch_id,
            "source": source,
            "dueLocalDate": due_local_date,
            "scheduledFor": iso_z(scheduled_for) if scheduled_for else None,
            "status": "processing",
            "attempts": attempts,
            "routeObservationIds": route_ids,
            "reviewObservationIds": review_ids,
            "acknowledge": bool(acknowledge),
            "startedAt": iso_z(current),
            "preparedAt": None,
            "processedAt": None,
            "lastError": None,
            "nextAttemptAt": None,
            "syncStatus": prior.get("syncStatus"),
            "syncError": prior.get("syncError"),
            "transaction": None,
        }
        jobs["batches"][batch_id] = batch
        for row in route_rows:
            row["status"] = "processing"
            row["batchId"] = batch_id
            row["attempts"] = int(row.get("attempts", 0)) + 1
            row["processingStartedAt"] = iso_z(current)
            row["lastError"] = None
            row["nextAttemptAt"] = None
        # The batch record lands first. If power fails before observations, recovery
        # can safely re-claim; the reverse order could strand "processing" rows.
        atomic_write_json(self.jobs_path, jobs)
        atomic_write_json(self.observations_path, observations)
        return copy.deepcopy(batch)

    def _retry_ready(self, batch: dict, current: datetime) -> bool:
        next_attempt = parse_iso(batch.get("nextAttemptAt"))
        return next_attempt is None or current >= next_attempt.astimezone(timezone.utc)

    def run_due(
        self,
        runner,
        *,
        now: datetime | None = None,
        ignore_backoff: bool = False,
    ) -> dict:
        """Run (or catch up) the latest UK-local daily slot."""
        current = self._now(now)
        if not self.run_lock.acquire(blocking=False):
            return {"status": "busy"}
        try:
            self._recover_locked(current)
            latest_due_date, latest_scheduled_for = self.due_slot(current)
            with self.lock:
                jobs, jobs_changed = self._load_jobs_unlocked()
                observations, observations_changed = self._load_observations_unlocked()
                if jobs_changed:
                    atomic_write_json(self.jobs_path, jobs)
                if observations_changed:
                    atomic_write_json(self.observations_path, observations)
                failed_daily = sorted(
                    (
                        batch
                        for batch in jobs["batches"].values()
                        if batch.get("source") == "daily"
                        and batch.get("status") == "failed"
                    ),
                    key=lambda batch: (
                        batch.get("scheduledFor") or "",
                        batch.get("id") or "",
                    ),
                )
                if failed_daily:
                    selected = failed_daily[0]
                    batch_id = selected["id"]
                    due_local_date = selected.get("dueLocalDate")
                    scheduled_for = (
                        parse_iso(selected.get("scheduledFor"))
                        or latest_scheduled_for
                    )
                else:
                    due_local_date = latest_due_date
                    scheduled_for = latest_scheduled_for
                    batch_id = f"daily-{due_local_date}"
                prior = jobs["batches"].get(batch_id)
                if prior and prior.get("status") == "processed":
                    return {
                        "status": "skipped",
                        "reason": "already-processed",
                        "batchId": batch_id,
                    }
                if prior and prior.get("status") == "prepared":
                    # Recovery could not apply it (normally a real file conflict).
                    # Never ask the runner to generate the effects a second time.
                    return {
                        "status": "recovering",
                        "batchId": batch_id,
                        "error": prior.get("lastError"),
                    }
                elif (
                    prior
                    and prior.get("status") == "failed"
                    and not ignore_backoff
                    and not self._retry_ready(prior, current)
                ):
                    return {
                        "status": "retry-wait",
                        "batchId": batch_id,
                        "nextAttemptAt": prior.get("nextAttemptAt"),
                    }
                else:
                    route_rows = self._eligible(observations, scheduled_for)
                    batch = self._claim_batch_unlocked(
                        batch_id=batch_id,
                        source="daily",
                        route_rows=route_rows,
                        review_rows=[],
                        current=current,
                        due_local_date=due_local_date,
                        scheduled_for=scheduled_for,
                        acknowledge=False,
                        jobs=jobs,
                        observations=observations,
                    )
                    if not route_rows:
                        self._finish_empty_batch(batch_id, current)
                        self._sync_processed()
                        return {
                            "status": "processed",
                            "batchId": batch_id,
                            "count": 0,
                            "runnerCalled": False,
                        }
            result = self._execute_batch(batch_id, runner, current)
            if result.get("status") == "processed":
                self._sync_processed()
            return result
        finally:
            self.run_lock.release()

    def process_for_coach(
        self,
        runner,
        *,
        source_key: str,
        is_debrief: bool,
        now: datetime | None = None,
    ) -> dict:
        """Run one normal coach turn and transactionally route its observations."""
        current = self._now(now)
        digest = sha_text(source_key)[:20]
        batch_id = f"coach-{digest}"
        with self.run_lock:
            self._recover_locked(current)
            with self.lock:
                jobs, jobs_changed = self._load_jobs_unlocked()
                observations, observations_changed = self._load_observations_unlocked()
                if jobs_changed:
                    atomic_write_json(self.jobs_path, jobs)
                if observations_changed:
                    atomic_write_json(self.observations_path, observations)
                prior = jobs["batches"].get(batch_id)
                if prior and prior.get("status") == "processed":
                    return {
                        "status": "skipped",
                        "reason": "already-processed",
                        "batchId": batch_id,
                    }
                route_rows = self._eligible(observations, current)
                review_rows = []
                if is_debrief:
                    route_ids = {row["id"] for row in route_rows}
                    review_rows = [
                        row
                        for row in observations["obs"]
                        if row.get("status") == "processed"
                        and not row.get("acknowledgedAt")
                        and row["id"] not in route_ids
                    ]
                self._claim_batch_unlocked(
                    batch_id=batch_id,
                    source="coach",
                    route_rows=route_rows,
                    review_rows=review_rows,
                    current=current,
                    due_local_date=None,
                    scheduled_for=None,
                    acknowledge=is_debrief,
                    jobs=jobs,
                    observations=observations,
                )
            result = self._execute_batch(batch_id, runner, current)
            if result.get("status") == "processed":
                self._sync_processed()
            return result

    def _finish_empty_batch(self, batch_id: str, current: datetime) -> None:
        with self.lock:
            jobs, _ = self._load_jobs_unlocked()
            batch = jobs["batches"][batch_id]
            batch["status"] = "processed"
            batch["processedAt"] = iso_z(current)
            batch["syncStatus"] = "pending"
            batch["transaction"] = None
            atomic_write_json(self.jobs_path, jobs)

    def _copy_stage(self, batch_id: str) -> tuple[Path, dict[str, str]]:
        with self.lock:
            day_plans_path = self._path(DAY_PLANS_REL)
            if not day_plans_path.exists():
                atomic_write_json(day_plans_path, {"version": 1, "plans": []})
        stage_parent = Path(tempfile.mkdtemp(prefix=f"practice-room-{batch_id}-"))
        stage = stage_parent / "data-repo"
        shutil.copytree(
            self.data_dir,
            stage,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
        )
        baseline: dict[str, str] = {}
        for rel in set(COACH_OUTPUTS) | {
            OBSERVATIONS_REL,
            "data/chat.json",
            "data/journal.json",
        }:
            baseline[rel] = read_text(stage / rel, "")
        return stage, baseline

    def _batch_snapshot(self, batch_id: str) -> dict:
        with self.lock:
            jobs, _ = self._load_jobs_unlocked()
            return copy.deepcopy(jobs["batches"][batch_id])

    def _execute_batch(
        self,
        batch_id: str,
        runner,
        current: datetime,
    ) -> dict:
        stage = None
        stage_parent = None
        try:
            batch = self._batch_snapshot(batch_id)
            stage, baseline = self._copy_stage(batch_id)
            stage_parent = stage.parent
            runner(stage, copy.deepcopy(batch))
            self._validate_stage(stage, baseline, batch)
            self._prepare_transaction(batch_id, stage, baseline, current)
            self._apply_prepared_batch(batch_id, current)
            return {
                "status": "processed",
                "batchId": batch_id,
                "count": len(batch.get("routeObservationIds", [])),
                "runnerCalled": True,
            }
        except Exception as exc:
            with self.lock:
                jobs, _ = self._load_jobs_unlocked()
                status = jobs.get("batches", {}).get(batch_id, {}).get("status")
            if status == "prepared":
                self._record_prepared_error(batch_id, exc)
                return {
                    "status": "recovering",
                    "batchId": batch_id,
                    "error": str(exc),
                }
            self._fail_batch(batch_id, exc, current)
            return {"status": "failed", "batchId": batch_id, "error": str(exc)}
        finally:
            if stage_parent:
                shutil.rmtree(stage_parent, ignore_errors=True)

    def _validate_stage(self, stage: Path, baseline: dict[str, str], batch: dict) -> None:
        observation_text = read_text(stage / OBSERVATIONS_REL, "")
        if observation_text != baseline[OBSERVATIONS_REL]:
            raise PipelineError("the coach changed server-owned observation lifecycle state")
        if batch["source"] == "daily":
            for forbidden in ("data/chat.json", "data/journal.json"):
                if read_text(stage / forbidden, "") != baseline[forbidden]:
                    raise PipelineError(f"daily processing must not change {forbidden}")
        targets = DAILY_OUTPUTS if batch["source"] == "daily" else COACH_OUTPUTS
        for rel in targets:
            text = read_text(stage / rel, "")
            if rel in JSON_OUTPUTS:
                validate_json_text(rel, text)

    def _merge_daily_state(self, current_text: str, staged_text: str) -> str:
        current = json.loads(current_text)
        staged = json.loads(staged_text)
        current_pieces = current.get("pieces")
        staged_pieces = staged.get("pieces")
        if not isinstance(current_pieces, list) or not isinstance(staged_pieces, list):
            raise PipelineError("state.json pieces must be arrays")
        staged_by_id = {
            piece.get("id"): piece
            for piece in staged_pieces
            if isinstance(piece, dict) and piece.get("id")
        }
        merged = copy.deepcopy(current)
        for piece in merged["pieces"]:
            if not isinstance(piece, dict):
                continue
            staged_piece = staged_by_id.get(piece.get("id"))
            if not staged_piece:
                continue
            for field in DAILY_STATE_PIECE_FIELDS:
                if field in staged_piece:
                    piece[field] = copy.deepcopy(staged_piece[field])
        return json.dumps(merged, ensure_ascii=False, indent=2) + "\n"

    def _prepare_transaction(
        self,
        batch_id: str,
        stage: Path,
        baseline: dict[str, str],
        current: datetime,
    ) -> None:
        with self.lock:
            jobs, _ = self._load_jobs_unlocked()
            batch = jobs["batches"][batch_id]
            targets = DAILY_OUTPUTS if batch["source"] == "daily" else COACH_OUTPUTS
            outputs: dict[str, str] = {}
            base_hashes: dict[str, str] = {}
            for rel in targets:
                staged_text = read_text(stage / rel, "")
                current_text = read_text(self._path(rel), "")
                if batch["source"] == "daily" and rel == "data/state.json":
                    output_text = self._merge_daily_state(current_text, staged_text)
                    base_text = current_text
                else:
                    base_text = baseline[rel]
                    if current_text != base_text:
                        raise TransactionConflict(f"{rel} changed during the coach run")
                    output_text = staged_text
                if rel in JSON_OUTPUTS:
                    validate_json_text(rel, output_text)
                if output_text != current_text:
                    outputs[rel] = output_text
                    base_hashes[rel] = sha_text(base_text)
            transaction = {
                "baseHashes": base_hashes,
                "outputHashes": {rel: sha_text(text) for rel, text in outputs.items()},
                "outputs": outputs,
            }
            batch["transaction"] = transaction
            batch["status"] = "prepared"
            batch["preparedAt"] = iso_z(current)
            batch["lastError"] = None
            atomic_write_json(self.jobs_path, jobs)

    def _apply_prepared_batch(self, batch_id: str, current: datetime) -> None:
        with self.lock:
            jobs, _ = self._load_jobs_unlocked()
            batch = jobs["batches"].get(batch_id)
            if not batch or batch.get("status") != "prepared":
                raise PipelineError(f"{batch_id} is not prepared")
            transaction = batch.get("transaction") or {}
            outputs = transaction.get("outputs") or {}
            base_hashes = transaction.get("baseHashes") or {}
            output_hashes = transaction.get("outputHashes") or {}

            # Preflight every file before writing any. During crash recovery, an
            # already-written output hash is accepted and simply not written again.
            for rel, output in outputs.items():
                current_text = read_text(self._path(rel), "")
                current_hash = sha_text(current_text)
                if current_hash not in {base_hashes.get(rel), output_hashes.get(rel)}:
                    raise TransactionConflict(f"{rel} conflicts with prepared batch {batch_id}")
                if sha_text(output) != output_hashes.get(rel):
                    raise PipelineError(f"prepared output checksum failed for {rel}")
            for rel, output in outputs.items():
                path = self._path(rel)
                if sha_text(read_text(path, "")) != output_hashes[rel]:
                    atomic_write_text(path, output)

            # Tests use a BaseException here to model process death. It deliberately
            # bypasses _execute_batch's Exception handler and leaves "prepared"
            # durable for a new process to finish.
            if self.after_apply_hook:
                self.after_apply_hook(batch_id)

            observations, _ = self._load_observations_unlocked()
            route_ids = set(batch.get("routeObservationIds") or [])
            review_ids = set(batch.get("reviewObservationIds") or [])
            for row in observations["obs"]:
                if row["id"] in route_ids:
                    row["status"] = "processed"
                    row["batchId"] = batch_id
                    row["processingStartedAt"] = None
                    row["processedAt"] = iso_z(current)
                    row["processedBy"] = batch.get("source")
                    row["lastError"] = None
                    row["nextAttemptAt"] = None
                if batch.get("acknowledge") and row["id"] in route_ids | review_ids:
                    row["acknowledgedAt"] = iso_z(current)
            atomic_write_json(self.observations_path, observations)

            batch["status"] = "processed"
            batch["processedAt"] = iso_z(current)
            batch["lastError"] = None
            batch["nextAttemptAt"] = None
            batch["syncStatus"] = "pending"
            batch["syncError"] = None
            batch["transaction"] = {
                "baseHashes": base_hashes,
                "outputHashes": output_hashes,
                "outputs": {},
            }
            atomic_write_json(self.jobs_path, jobs)

    def _retry_delay(self, attempts: int) -> timedelta:
        minutes = min(60, 5 * (2 ** max(0, attempts - 1)))
        return timedelta(minutes=minutes)

    def _fail_batch(self, batch_id: str, error: Exception, current: datetime) -> None:
        message = str(error)[:500] or error.__class__.__name__
        with self.lock:
            jobs, _ = self._load_jobs_unlocked()
            batch = jobs["batches"].get(batch_id)
            if not batch:
                return
            next_attempt = current + self._retry_delay(int(batch.get("attempts", 1)))
            batch["status"] = "failed"
            batch["lastError"] = message
            batch["nextAttemptAt"] = iso_z(next_attempt)
            batch["transaction"] = None
            observations, _ = self._load_observations_unlocked()
            route_ids = set(batch.get("routeObservationIds") or [])
            for row in observations["obs"]:
                if row["id"] in route_ids and row.get("status") != "processed":
                    row["status"] = "failed"
                    row["batchId"] = None
                    row["processingStartedAt"] = None
                    row["lastError"] = message
                    row["nextAttemptAt"] = iso_z(next_attempt)
            atomic_write_json(self.observations_path, observations)
            atomic_write_json(self.jobs_path, jobs)

    def _record_prepared_error(self, batch_id: str, error: Exception) -> None:
        with self.lock:
            jobs, _ = self._load_jobs_unlocked()
            batch = jobs["batches"].get(batch_id)
            if batch and batch.get("status") == "prepared":
                message = str(error)[:500]
                batch["lastError"] = message
                observations, _ = self._load_observations_unlocked()
                route_ids = set(batch.get("routeObservationIds") or [])
                for row in observations["obs"]:
                    if row["id"] in route_ids and row.get("status") != "processed":
                        row["status"] = "failed"
                        row["lastError"] = message
                atomic_write_json(self.observations_path, observations)
                atomic_write_json(self.jobs_path, jobs)

    def recover(self, *, now: datetime | None = None) -> dict:
        current = self._now(now)
        with self.run_lock:
            recovered = self._recover_locked(current)
        if recovered:
            self._sync_processed()
        return {"recovered": recovered}

    def _recover_locked(self, current: datetime) -> list[str]:
        recovered: list[str] = []
        with self.lock:
            jobs, jobs_changed = self._load_jobs_unlocked()
            observations, observations_changed = self._load_observations_unlocked()
            if jobs_changed:
                atomic_write_json(self.jobs_path, jobs)
            if observations_changed:
                atomic_write_json(self.observations_path, observations)
            prepared_ids = [
                batch_id
                for batch_id, batch in jobs["batches"].items()
                if batch.get("status") == "prepared"
            ]
        for batch_id in prepared_ids:
            try:
                self._apply_prepared_batch(batch_id, current)
                recovered.append(batch_id)
            except Exception as exc:
                self._record_prepared_error(batch_id, exc)

        # A processing batch without a prepared result died while Claude was
        # running. No live effect was applied, so it is safe to retry from scratch.
        with self.lock:
            jobs, _ = self._load_jobs_unlocked()
            interrupted = [
                batch_id
                for batch_id, batch in jobs["batches"].items()
                if batch.get("status") == "processing"
            ]
        for batch_id in interrupted:
            self._fail_batch(batch_id, PipelineError("interrupted; retrying safely"), current)

        with self.lock:
            jobs, _ = self._load_jobs_unlocked()
            known = set(jobs["batches"])
            observations, _ = self._load_observations_unlocked()
            changed = False
            for row in observations["obs"]:
                if row.get("status") == "processing" and row.get("batchId") not in known:
                    row["status"] = "failed"
                    row["batchId"] = None
                    row["processingStartedAt"] = None
                    row["lastError"] = "interrupted; retrying safely"
                    row["nextAttemptAt"] = iso_z(current)
                    changed = True
            if changed:
                atomic_write_json(self.observations_path, observations)
        return recovered

    def _sync_processed(self) -> bool:
        if not self.sync_callback:
            return True
        with self.lock:
            jobs, _ = self._load_jobs_unlocked()
            pending_ids = [
                batch_id
                for batch_id, batch in jobs["batches"].items()
                if batch.get("status") == "processed"
                and batch.get("syncStatus") in {None, "pending", "failed"}
            ]
        if not pending_ids:
            return True
        try:
            synced = self.sync_callback("practice logs")
            ok = True if synced is None else bool(synced)
            error = None if ok else "backup push failed; local data is safe"
        except Exception as exc:
            ok = False
            error = str(exc)[:500]
        with self.lock:
            jobs, _ = self._load_jobs_unlocked()
            for batch_id in pending_ids:
                batch = jobs["batches"].get(batch_id)
                if not batch or batch.get("status") != "processed":
                    continue
                batch["syncStatus"] = "synced" if ok else "failed"
                batch["syncError"] = error
            atomic_write_json(self.jobs_path, jobs)
        return ok

    def retry_sync(self) -> bool:
        """Retry backup only; never re-run the coach or re-apply effects."""
        return self._sync_processed()
