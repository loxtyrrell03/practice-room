"""Durable, ordered coach-message queue.

The coach edits an isolated copy of the private data repository. A successful
result is persisted before it is applied to the live files, so retries and
restarts cannot append the same reply twice.
"""
from __future__ import annotations

import hashlib
import copy
import json
import os
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path


EDITABLE_FILES = (
    "data/chat.json",
    "data/state.json",
    "data/day-plans.json",
    "data/weekly-plan.json",
    "data/journal.json",
    "data/spots.json",
    "data/observations.json",
    "data/observation-jobs.json",
    "data/repertoire-changes.json",
    "context/plan.md",
    "context/repertoire.md",
    "memory/MEMORY.md",
)


def _iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(temp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        if default is None:
            raise
        return default


def _item_key(item):
    if not isinstance(item, dict):
        return None
    for key in ("id", "messageId"):
        if item.get(key):
            return (key, str(item[key]))
    if item.get("date") is not None and item.get("day") is not None:
        return ("date-day", str(item["date"]), str(item["day"]))
    if item.get("ts") is not None and item.get("blockId") is not None:
        return ("ts-block", str(item["ts"]), str(item["blockId"]))
    return None


def _three_way(base, result, current):
    """Apply the staged base→result change without dropping newer live input."""
    if result == base:
        return copy.deepcopy(current)
    if current == base or current == result:
        return copy.deepcopy(result)

    if isinstance(base, dict) and isinstance(result, dict) and isinstance(current, dict):
        merged = copy.deepcopy(current)
        for key in set(base) | set(result):
            if key not in result:
                if key in base and merged.get(key) == base[key]:
                    merged.pop(key, None)
                continue
            if key not in base:
                merged[key] = copy.deepcopy(result[key])
                continue
            merged[key] = _three_way(base[key], result[key], merged.get(key))
        return merged

    if isinstance(base, list) and isinstance(result, list) and isinstance(current, list):
        keyed = all(_item_key(item) is not None for item in base + result + current)
        if keyed:
            base_by_key = {_item_key(item): item for item in base}
            result_by_key = {_item_key(item): item for item in result}
            current_by_key = {_item_key(item): item for item in current}
            order = [_item_key(item) for item in current]
            for key in result_by_key:
                if key not in order:
                    order.append(key)
            merged = []
            for key in order:
                if key not in result_by_key:
                    if key not in base_by_key or current_by_key.get(key) != base_by_key[key]:
                        merged.append(copy.deepcopy(current_by_key[key]))
                    continue
                if key not in base_by_key:
                    merged.append(copy.deepcopy(result_by_key[key]))
                else:
                    merged.append(
                        _three_way(
                            base_by_key[key],
                            result_by_key[key],
                            current_by_key.get(key, base_by_key[key]),
                        )
                    )
            return merged

        merged = copy.deepcopy(current)
        additions = result[len(base) :] if result[: len(base)] == base else result
        for item in additions:
            if item not in merged:
                merged.append(copy.deepcopy(item))
        return merged

    # If both sides deliberately changed the same scalar, the coach result wins.
    return copy.deepcopy(result)


def _has_confirmed_repertoire_change(prepared):
    change = prepared.get("files", {}).get("data/repertoire-changes.json")
    if not change:
        return False
    try:
        before = json.loads(change["base"]).get("changes", [])
        after = json.loads(change["result"]).get("changes", [])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False
    before_ids = {
        item.get("id")
        for item in before
        if isinstance(item, dict) and item.get("id")
    }
    return any(
        isinstance(item, dict)
        and item.get("id") not in before_ids
        and item.get("action") in {"add", "drop"}
        and item.get("status") == "planned"
        for item in after
    )


def _preserve_completed_blocks(source, target):
    """Keep same-day completed cards as immutable evidence in a new plan."""
    if not all(isinstance(item, dict) for item in (source, target)):
        return target
    source_today = source.get("today")
    target_today = target.get("today")
    if not all(isinstance(item, dict) for item in (source_today, target_today)):
        return target
    if source_today.get("date") != target_today.get("date"):
        return target

    source_blocks = source_today.get("blocks")
    target_blocks = target_today.get("blocks")
    if not isinstance(source_blocks, list) or not isinstance(target_blocks, list):
        return target

    completed = [
        (index, block)
        for index, block in enumerate(source_blocks)
        if isinstance(block, dict) and block.get("id") and block.get("done") is True
    ]
    if not completed:
        return target

    guarded = copy.deepcopy(target)
    completed_ids = {block["id"] for _, block in completed}
    guarded_blocks = [
        block
        for block in guarded["today"]["blocks"]
        if not (
            isinstance(block, dict)
            and block.get("id") in completed_ids
        )
    ]
    for source_index, block in completed:
        guarded_blocks.insert(
            min(source_index, len(guarded_blocks)),
            copy.deepcopy(block),
        )
    guarded["today"]["blocks"] = guarded_blocks
    return guarded


def _merge_repertoire_state(base, result, current):
    """Keep live evidence without resurrecting superseded future planning."""
    merged = _three_way(base, result, current)
    if not all(
        isinstance(item, dict) for item in (base, result, current, merged)
    ):
        return merged

    result_piece_ids = {
        piece.get("id")
        for piece in result.get("pieces", [])
        if isinstance(piece, dict) and piece.get("id")
    }
    merged["pieces"] = [
        piece
        for piece in merged.get("pieces", [])
        if isinstance(piece, dict) and piece.get("id") in result_piece_ids
    ]

    # Confirmed replanning owns future summaries. Cold-test and programme
    # evidence on retained pieces still comes from the general merge above.
    for key in ("tomorrowPreview", "flags", "week"):
        if key in result:
            merged[key] = copy.deepcopy(result[key])

    result_today = result.get("today")
    merged_today = merged.get("today")
    current_today = current.get("today")
    if all(
        isinstance(item, dict)
        for item in (result_today, merged_today, current_today)
    ):
        if "focus" in result_today:
            merged_today["focus"] = copy.deepcopy(result_today["focus"])
        result_blocks = [
            block
            for block in result_today.get("blocks", [])
            if isinstance(block, dict)
        ]
        merged_by_id = {
            block.get("id"): block
            for block in merged_today.get("blocks", [])
            if isinstance(block, dict) and block.get("id")
        }
        planned_ids = {block.get("id") for block in result_blocks}
        blocks = [
            copy.deepcopy(merged_by_id.get(block.get("id"), block))
            for block in result_blocks
        ]
        # A block completed while the coach was planning is historical
        # evidence, even if the new repertoire no longer schedules it.
        blocks.extend(
            copy.deepcopy(block)
            for block in current_today.get("blocks", [])
            if isinstance(block, dict)
            and block.get("id") not in planned_ids
            and block.get("done") is True
        )
        merged_today["blocks"] = blocks
    return merged


class CoachQueue:
    """A single FIFO drain with durable intake and idempotent result apply."""

    def __init__(
        self,
        data_dir,
        runner,
        *,
        lock=None,
        retry_base_seconds=15,
        retry_max_seconds=300,
        on_completed=None,
        clock=time.time,
        transaction_lock=None,
    ):
        self.data_dir = Path(data_dir)
        self.runner = runner
        self.lock = lock or threading.RLock()
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds
        self.on_completed = on_completed
        self.clock = clock
        self.transaction_lock = transaction_lock
        self.queue_path = self.data_dir / ".coach-queue.json"
        self.results_dir = self.data_dir / ".coach-results"
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        with self.lock:
            queue = self._load_queue()
            self._migrate_legacy_unanswered(queue)
            changed = False
            for job in queue["jobs"]:
                if job["state"] == "processing":
                    job["state"] = "queued"
                    job["lastError"] = "Server restarted while the coach was replying; safely retrying."
                    job["processingStartedAt"] = None
                    job["nextAttemptAt"] = 0
                    changed = True
            if changed:
                self._save_queue(queue)
            self._reconcile_messages(queue)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._worker, name="coach-queue", daemon=True)
        self._thread.start()
        self._wake.set()

    def stop(self, timeout=5):
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)

    def accept(self, text, request_id=None):
        text = (text or "").strip()
        if not text:
            raise ValueError("empty")
        request_id = (request_id or "").strip() or str(uuid.uuid4())
        if len(request_id) > 200:
            raise ValueError("requestId is too long")
        durable = False
        try:
            with self.lock:
                queue = self._load_queue()
                existing = next(
                    (j for j in queue["jobs"] if j["requestId"] == request_id),
                    None,
                )
                if existing:
                    if existing["text"] != text:
                        raise ValueError(
                            "requestId was already used for a different message"
                        )
                    durable = True
                    self._ensure_user_message(existing)
                    result = self._public_job(existing, queue)
                else:
                    sequence = queue["nextSequence"]
                    queue["nextSequence"] += 1
                    accepted = _iso_now()
                    job_id = f"job-{sequence}-{uuid.uuid4().hex[:12]}"
                    job = {
                        "id": job_id,
                        "requestId": request_id,
                        "messageId": f"message-{job_id}",
                        "text": text,
                        "acceptedAt": accepted,
                        "sequence": sequence,
                        "state": "queued",
                        "attempts": 0,
                        "processingStartedAt": None,
                        "nextAttemptAt": 0,
                        "lastError": None,
                        "completedAt": None,
                        "replyId": None,
                        "resultFile": None,
                    }
                    # The queue is canonical. Startup or the drain reconciles a
                    # chat write interrupted after this atomic acceptance.
                    queue["jobs"].append(job)
                    self._save_queue(queue)
                    durable = True
                    self._ensure_user_message(job)
                    result = self._public_job(job, queue)
        finally:
            if durable:
                self._wake.set()
        return result

    def snapshot(self):
        with self.lock:
            queue = self._load_queue()
            active = [j for j in queue["jobs"] if j["state"] != "done"]
            processing = sum(j["state"] in ("processing", "prepared") for j in active)
            return {
                "pending": sum(j["state"] in ("queued", "failed") for j in active),
                "processing": processing,
                "failed": sum(j["state"] == "failed" for j in active),
                "jobs": [self._public_job(j, queue) for j in queue["jobs"][-100:]],
            }

    def drain_once(self, ignore_retry_time=False):
        if self.transaction_lock:
            with self.transaction_lock:
                return self._drain_once_transaction(ignore_retry_time)
        return self._drain_once_transaction(ignore_retry_time)

    def _drain_once_transaction(self, ignore_retry_time=False):
        with self.lock:
            queue = self._load_queue()
            job = self._first_unfinished(queue)
            if not job:
                return False
            if job["state"] == "failed" and not ignore_retry_time:
                if job.get("nextAttemptAt", 0) > self.clock():
                    return False
            try:
                self._ensure_user_message(job)
            except Exception as exc:
                self._mark_failed(job["id"], exc)
                return True
            if self._reply_already_live(job):
                self._mark_done(queue, job, f"reply-{job['id']}")
                return True
            if job["state"] == "prepared":
                result_file = job.get("resultFile")
            else:
                job["state"] = "processing"
                job["attempts"] += 1
                job["processingStartedAt"] = _iso_now()
                job["lastError"] = None
                self._save_queue(queue)
                result_file = None

        if result_file:
            return self._apply_prepared(job["id"])
        return self._run_and_prepare(job["id"])

    def drain_until_idle(self, *, ignore_retry_time=False, max_steps=1000):
        steps = 0
        while steps < max_steps and self.drain_once(ignore_retry_time=ignore_retry_time):
            steps += 1
        return steps

    def wait_idle(self, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            snap = self.snapshot()
            if not snap["pending"] and not snap["processing"]:
                return True
            time.sleep(0.01)
        return False

    def _worker(self):
        while not self._stop.is_set():
            progressed = self.drain_once()
            if progressed:
                continue
            timeout = self._seconds_until_retry()
            self._wake.wait(timeout)
            self._wake.clear()

    def _seconds_until_retry(self):
        with self.lock:
            job = self._first_unfinished(self._load_queue())
            if job and job["state"] == "failed":
                return max(0.05, min(30, job.get("nextAttemptAt", 0) - self.clock()))
        return 30

    def _run_and_prepare(self, job_id):
        try:
            with self.lock:
                queue = self._load_queue()
                job = self._job(queue, job_id)
                if not job or job["state"] != "processing":
                    return False
                baseline = self._snapshot_editable()

            with tempfile.TemporaryDirectory(prefix="practice-room-coach-") as temp:
                stage = Path(temp) / "data-repo"
                shutil.copytree(
                    self.data_dir,
                    stage,
                    ignore=shutil.ignore_patterns(".git", ".coach-queue.json", ".coach-results"),
                )
                for rel, text in baseline.items():
                    _atomic_write(stage / rel, text)
                self.runner(stage, dict(job))
                result = self._build_result(job, baseline, stage)

            result_path = self.results_dir / f"{job_id}.json"
            _atomic_write(result_path, json.dumps(result, indent=2) + "\n")
            with self.lock:
                queue = self._load_queue()
                job = self._job(queue, job_id)
                if not job or job["state"] == "done":
                    return True
                job["state"] = "prepared"
                job["resultFile"] = str(result_path.relative_to(self.data_dir))
                job["processingStartedAt"] = None
                self._save_queue(queue)
            return self._apply_prepared(job_id)
        except Exception as exc:
            self._mark_failed(job_id, exc)
            return True

    def _build_result(self, job, baseline, stage):
        before_chat = json.loads(baseline["data/chat.json"])
        after_chat = _read_json(stage / "data/chat.json")
        before_messages = before_chat.get("messages", [])
        after_messages = after_chat.get("messages", [])
        if len(after_messages) != len(before_messages) + 1:
            raise RuntimeError("coach must append exactly one reply")
        reply = after_messages[-1]
        if reply.get("role") != "coach" or not str(reply.get("text", "")).strip():
            raise RuntimeError("coach did not append a valid reply")
        # Only the new reply is ever carried into the prepared transaction.
        # Any incidental rewrite of older staged messages is discarded, so
        # the live append-only history remains byte-for-byte canonical.
        reply = dict(reply)
        reply["id"] = f"reply-{job['id']}"
        reply["replyTo"] = job["messageId"]

        files = {}
        for rel in baseline:
            if rel == "data/chat.json":
                continue
            path = stage / rel
            after = path.read_text(encoding="utf-8")
            before = baseline[rel]
            if rel == "data/state.json":
                before_state = json.loads(before)
                after_state = json.loads(after)
                guarded_state = _preserve_completed_blocks(
                    before_state, after_state
                )
                after = (
                    json.dumps(guarded_state, indent=2, ensure_ascii=False) + "\n"
                )
            if after != before:
                if rel.endswith(".json"):
                    json.loads(after)
                files[rel] = {
                    "beforeHash": _digest(before),
                    "afterHash": _digest(after),
                    "base": before,
                    "result": after,
                }
        return {"version": 1, "jobId": job["id"], "reply": reply, "files": files}

    def _apply_prepared(self, job_id):
        completed = False
        with self.lock:
            queue = self._load_queue()
            job = self._job(queue, job_id)
            if not job or job["state"] == "done":
                return True
            if job["state"] != "prepared" or not job.get("resultFile"):
                return False
            result_path = self.data_dir / job["resultFile"]
            result = _read_json(result_path)
            repertoire_changed = _has_confirmed_repertoire_change(result)

            for rel, change in result["files"].items():
                path = self.data_dir / rel
                current = path.read_text(encoding="utf-8")
                if _digest(current) == change["afterHash"]:
                    continue
                if _digest(current) == change["beforeHash"]:
                    merged = change["result"]
                elif rel.endswith(".json"):
                    base_obj = json.loads(change["base"])
                    result_obj = json.loads(change["result"])
                    current_obj = json.loads(current)
                    if rel == "data/state.json" and repertoire_changed:
                        merged_obj = _merge_repertoire_state(
                            base_obj, result_obj, current_obj
                        )
                    else:
                        merged_obj = _three_way(
                            base_obj, result_obj, current_obj
                        )
                    if rel == "data/state.json":
                        merged_obj = _preserve_completed_blocks(
                            current_obj, merged_obj
                        )
                    merged = json.dumps(merged_obj, indent=2, ensure_ascii=False) + "\n"
                else:
                    # The coach runners are serialized, so MEMORY.md cannot be
                    # changed by another coach transaction during this apply.
                    merged = change["result"]
                _atomic_write(path, merged)
            self._insert_reply(job, result["reply"])
            self._mark_done(queue, job, result["reply"]["id"], save=False)
            self._save_queue(queue)
            try:
                result_path.unlink()
            except FileNotFoundError:
                pass
            completed = True
        if completed and self.on_completed:
            self.on_completed(job_id)
        return True

    def _mark_failed(self, job_id, exc):
        with self.lock:
            queue = self._load_queue()
            job = self._job(queue, job_id)
            if not job or job["state"] == "done":
                return
            delay = min(self.retry_max_seconds, self.retry_base_seconds * (2 ** max(0, job["attempts"] - 1)))
            job["state"] = "failed"
            job["processingStartedAt"] = None
            job["nextAttemptAt"] = self.clock() + delay
            job["lastError"] = str(exc)[:500]
            self._save_queue(queue)
        self._wake.set()

    def _mark_done(self, queue, job, reply_id, save=True):
        job["state"] = "done"
        job["replyId"] = reply_id
        job["completedAt"] = job.get("completedAt") or _iso_now()
        job["processingStartedAt"] = None
        job["nextAttemptAt"] = 0
        job["lastError"] = None
        job["resultFile"] = None
        if save:
            self._save_queue(queue)

    def _insert_reply(self, job, reply):
        path = self.data_dir / "data/chat.json"
        doc = _read_json(path)
        messages = doc.setdefault("messages", [])
        if any(m.get("id") == reply["id"] or m.get("replyTo") == job["messageId"] for m in messages):
            return
        target = next((i for i, m in enumerate(messages) if m.get("id") == job["messageId"]), None)
        if target is None:
            self._ensure_user_message(job)
            doc = _read_json(path)
            messages = doc["messages"]
            target = next(i for i, m in enumerate(messages) if m.get("id") == job["messageId"])
        insert_at = target + 1
        while insert_at < len(messages) and messages[insert_at].get("replyTo") == job["messageId"]:
            insert_at += 1
        messages.insert(insert_at, reply)
        _atomic_write(path, json.dumps(doc, indent=2, ensure_ascii=False) + "\n")

    def _reply_already_live(self, job):
        try:
            messages = _read_json(self.data_dir / "data/chat.json").get("messages", [])
            return any(m.get("replyTo") == job["messageId"] for m in messages)
        except Exception:
            return False

    def _ensure_user_message(self, job):
        path = self.data_dir / "data/chat.json"
        doc = _read_json(path, {"messages": []})
        messages = doc.setdefault("messages", [])
        if any(m.get("id") == job["messageId"] for m in messages):
            return
        legacy = next(
            (
                message
                for message in messages
                if message.get("role") == "user"
                and not message.get("id")
                and message.get("text") == job["text"]
                and message.get("ts") == job["acceptedAt"]
            ),
            None,
        )
        if legacy is not None:
            legacy["id"] = job["messageId"]
        else:
            messages.append(
                {
                    "id": job["messageId"],
                    "role": "user",
                    "text": job["text"],
                    "ts": job["acceptedAt"],
                }
            )
        _atomic_write(path, json.dumps(doc, indent=2, ensure_ascii=False) + "\n")

    def _reconcile_messages(self, queue):
        for job in queue["jobs"]:
            self._ensure_user_message(job)

    def _migrate_legacy_unanswered(self, queue):
        """Recover user messages stranded by the pre-queue early-return race."""
        try:
            messages = _read_json(self.data_dir / "data/chat.json").get("messages", [])
        except FileNotFoundError:
            return
        last_coach = max(
            (index for index, message in enumerate(messages) if message.get("role") == "coach"),
            default=-1,
        )
        represented = {job["messageId"] for job in queue["jobs"]}
        represented_legacy = {
            (job.get("text"), job.get("acceptedAt")) for job in queue["jobs"]
        }
        migrated = []
        for index, message in enumerate(messages[last_coach + 1 :], start=last_coach + 1):
            if message.get("role") != "user":
                continue
            if message.get("id") in represented:
                continue
            text = str(message.get("text") or "").strip()
            if not text:
                continue
            accepted = message.get("ts") or _iso_now()
            if (text, accepted) in represented_legacy:
                continue
            fingerprint = hashlib.sha256(
                f"{accepted}\n{text}\n{index}".encode("utf-8")
            ).hexdigest()
            sequence = queue["nextSequence"]
            queue["nextSequence"] += 1
            job_id = f"job-{sequence}-legacy-{fingerprint[:12]}"
            job = {
                "id": job_id,
                "requestId": f"legacy-{fingerprint}",
                "messageId": message.get("id") or f"message-legacy-{fingerprint[:20]}",
                "text": text,
                "acceptedAt": accepted,
                "sequence": sequence,
                "state": "queued",
                "attempts": 0,
                "processingStartedAt": None,
                "nextAttemptAt": 0,
                "lastError": "Recovered unanswered message from the previous server.",
                "completedAt": None,
                "replyId": None,
                "resultFile": None,
            }
            queue["jobs"].append(job)
            represented.add(job["messageId"])
            represented_legacy.add((text, accepted))
            migrated.append(job)
        if migrated:
            self._save_queue(queue)
            for job in migrated:
                self._ensure_user_message(job)

    def _snapshot_editable(self):
        return {
            rel: (self.data_dir / rel).read_text(encoding="utf-8")
            for rel in EDITABLE_FILES
            if (self.data_dir / rel).is_file()
        }

    def _load_queue(self):
        queue = _read_json(self.queue_path, {"version": 1, "nextSequence": 1, "jobs": []})
        queue.setdefault("version", 1)
        queue.setdefault("nextSequence", 1)
        queue.setdefault("jobs", [])
        return queue

    def _save_queue(self, queue):
        _atomic_write(self.queue_path, json.dumps(queue, indent=2, ensure_ascii=False) + "\n")

    @staticmethod
    def _job(queue, job_id):
        return next((j for j in queue["jobs"] if j["id"] == job_id), None)

    @staticmethod
    def _first_unfinished(queue):
        return next((j for j in sorted(queue["jobs"], key=lambda x: x["sequence"]) if j["state"] != "done"), None)

    @staticmethod
    def _public_job(job, queue):
        unfinished = [j for j in sorted(queue["jobs"], key=lambda x: x["sequence"]) if j["state"] != "done"]
        position = next((i + 1 for i, item in enumerate(unfinished) if item["id"] == job["id"]), None)
        return {
            "id": job["id"],
            "messageId": job["messageId"],
            "requestId": job["requestId"],
            "sequence": job["sequence"],
            "state": job["state"],
            "position": position,
            "attempts": job["attempts"],
            "lastError": job.get("lastError"),
            "acceptedAt": job["acceptedAt"],
            "completedAt": job.get("completedAt"),
            "replyId": job.get("replyId"),
            "message": {
                "id": job["messageId"],
                "role": "user",
                "text": job["text"],
                "ts": job["acceptedAt"],
            },
        }
