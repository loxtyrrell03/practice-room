#!/usr/bin/env python3
"""Practice Room - private server for the laptop and phone.

The browser never receives a GitHub credential. Tailscale Serve provides the
phone's stable private HTTPS route; this process reads and writes the data repo
on disk, runs the coach, and syncs GitHub only as a backup.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from coach_queue import CoachQueue
from practice_logs import (
    JOBS_REL,
    OBSERVATIONS_REL,
    ObservationPipeline,
    atomic_write_json,
    atomic_write_text,
)


HERE = Path(__file__).resolve().parent
DATA = HERE / "data-repo"
PORT = 8977
MODEL = os.environ.get("COACH_MODEL", "claude-opus-5")
os.environ.setdefault("MAX_THINKING_TOKENS", "10000")  # medium reasoning
PHONE_URL = "https://lox.tail89d19b.ts.net:10000/"
SESSION_FILE = HERE / ".coach-session.json"

# Storage writes, whole coach transactions, and the CLI itself have different
# lock scopes. Practice notes remain immediately saveable while Claude works.
data_lock = threading.RLock()
coach_transaction_lock = threading.Lock()
cli_lock = threading.Lock()
sync_lock = threading.Lock()
pipeline_init_lock = threading.Lock()
observation_pipeline = None
coach_queue = None


def load_session():
    try:
        doc = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        if time.time() - doc.get("created", 0) < 7 * 86400:
            return doc.get("id")
    except Exception:
        pass
    return None


def save_session(session_id):
    try:
        current = {}
        try:
            current = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
        created = (
            current.get("created", time.time())
            if current.get("id") == session_id
            else time.time()
        )
        atomic_write_json(SESSION_FILE, {"id": session_id, "created": created})
    except Exception:
        pass


def log(message):
    print(f"[practice-room] {message}", flush=True)


def git(repo, *args):
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=90,
        )
        return result.returncode == 0, (result.stdout + result.stderr).strip()
    except Exception as exc:
        return False, str(exc)


def queue_blocks_backup():
    """Never publish a user message that the local FIFO still owns."""
    if coach_queue:
        queue = coach_queue.snapshot()
        return bool(queue["pending"] or queue["processing"])
    path = DATA / ".coach-queue.json"
    try:
        store = json.loads(path.read_text(encoding="utf-8"))
        return any(job.get("state") != "done" for job in store.get("jobs", []))
    except FileNotFoundError:
        return False
    except Exception:
        # A malformed durable queue needs local inspection, not an Actions race.
        return True


def sync_push(reason):
    """Best-effort backup. Local durability does not depend on GitHub."""
    if queue_blocks_backup():
        log("backup deferred until the local coach queue is fully drained")
        return False
    with sync_lock:
        git(DATA, "add", "-A")
        unchanged, _ = git(DATA, "diff", "--cached", "--quiet")
        if not unchanged:
            committed, output = git(DATA, "commit", "-m", f"[local] {reason}")
            if not committed:
                log(f"backup commit failed: {output[:120]}")
                return False
        pulled, output = git(DATA, "pull", "--rebase")
        if not pulled:
            log(f"backup pull failed (local data is safe): {output[:120]}")
            return False
        pushed, output = git(DATA, "push")
        if pushed:
            log(f"synced ({reason})")
        else:
            log(f"push failed (local data is safe): {output[:120]}")
        return pushed


def safe_path(rel):
    path = (DATA / rel).resolve()
    try:
        path.relative_to(DATA.resolve())
    except ValueError as exc:
        raise ValueError("bad path") from exc
    return path


def get_observation_pipeline():
    global observation_pipeline
    if observation_pipeline is None:
        with pipeline_init_lock:
            if observation_pipeline is None:
                observation_pipeline = ObservationPipeline(
                    DATA,
                    daily_time=os.environ.get("COACH_DAILY_LOG_TIME", "20:30"),
                    sync_callback=sync_push,
                    storage_lock=data_lock,
                    run_lock=coach_transaction_lock,
                )
    return observation_pipeline


def run_claude(repo, prompt, source):
    """Run one CLI turn. Transaction ordering is owned by the caller."""
    claude = shutil.which("claude") or "claude"
    base = [
        claude,
        "-p",
        "--output-format",
        "json",
        "--allowedTools",
        "Read,Write,Edit,Glob,Grep",
        "--permission-mode",
        "acceptEdits",
        "--max-turns",
        "40",
    ]
    session_id = load_session()
    attempts = []
    if session_id:
        attempts.append(["--resume", session_id, "--model", MODEL])
    attempts += [["--model", MODEL], []]
    errors = []

    with cli_lock:
        for extra in attempts:
            kind = "resumed session" if "--resume" in extra else "fresh session"
            model = extra[-1] if "--model" in extra else "default"
            log(f"coach thinking... ({source}, {kind}, model: {model})")
            result = subprocess.run(
                base + extra,
                input=prompt,
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=900,
                shell=(os.name == "nt"),
            )
            if result.returncode == 0:
                new_session = None
                try:
                    new_session = json.loads(result.stdout).get("session_id")
                except Exception:
                    match = re.search(
                        r'"session_id"\s*:\s*"([^"]+)"', result.stdout or ""
                    )
                    new_session = match.group(1) if match else None
                if new_session:
                    save_session(new_session)
                    log(f"coach finished (session {new_session[:8]}...).")
                else:
                    log(
                        "coach finished "
                        f"(no session id: {(result.stdout or '')[:120]!r})"
                    )
                return
            detail = (
                result.stderr or result.stdout or f"coach exit {result.returncode}"
            )[:500]
            errors.append(detail)
            log(f"coach run failed (rc={result.returncode}): {detail[:200]}")
    raise RuntimeError("Claude CLI failed: " + " | ".join(errors))


def _batch_prompt(stage, batch, job=None):
    prompt_name = (
        "daily-log-prompt.md" if batch["source"] == "daily" else "coach-prompt.md"
    )
    header = {
        "id": batch["id"],
        "source": batch["source"],
        "routeObservationIds": batch.get("routeObservationIds", []),
        "reviewObservationIds": batch.get("reviewObservationIds", []),
        "acknowledge": batch.get("acknowledge", False),
    }
    if job:
        header["targetMessageId"] = job["messageId"]
    prompt = (
        "PRACTICE ROOM SERVER BATCH\n"
        + json.dumps(header, ensure_ascii=False)
        + "\nEND SERVER BATCH\n\n"
        + (Path(stage) / ".github" / prompt_name).read_text(encoding="utf-8")
    )
    if job:
        prompt += f"""

## Authoritative queued-message dispatch

Answer the user message whose `id` is `{job["messageId"]}` and whose exact text
is {json.dumps(job["text"], ensure_ascii=False)}. Other user messages may appear
after it; ignore them during this run. Append exactly one coach reply and do not
rewrite chat history. The server attaches the durable reply ID and places the
reply beside this target after validation.
"""
    return prompt


def _run_staged_claude(stage, batch, job=None):
    source = "daily practice logs" if batch["source"] == "daily" else "coach message"
    run_claude(stage, _batch_prompt(stage, batch, job), source)


class ClaudeCoachRunner:
    """Route observations and answer one queued message in an outer snapshot."""

    def __call__(self, stage, job):
        staged_pipeline = ObservationPipeline(
            stage,
            daily_time=os.environ.get("COACH_DAILY_LOG_TIME", "20:30"),
        )
        staged_pipeline.migrate()
        staged_pipeline.recover()
        result = staged_pipeline.process_for_coach(
            lambda inner_stage, batch: _run_staged_claude(inner_stage, batch, job),
            source_key=job["messageId"],
            is_debrief=job["text"].strip().lower().startswith("debrief"),
        )
        if result.get("status") in {"failed", "recovering"}:
            raise RuntimeError(result.get("error") or "coach batch failed")
        if result.get("status") == "skipped":
            raise RuntimeError(
                "coach batch was already marked processed without a reply"
            )


def queue_completed(_job_id):
    queue = coach_queue.snapshot()
    if not queue["pending"] and not queue["processing"]:
        threading.Thread(
            target=sync_push, args=("coach session",), daemon=True
        ).start()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(HERE), **kwargs)

    def log_message(self, *args):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/api/meta":
            ok, name = git(DATA, "config", "user.name")
            practice_logs = get_observation_pipeline().summary()
            queue = coach_queue.snapshot() if coach_queue else {
                "pending": 0,
                "processing": 0,
                "failed": 0,
                "jobs": [],
            }
            log_processing = practice_logs["counts"].get("processing", 0)
            return self._json(
                200,
                {
                    "mode": "local",
                    "name": name.split()[0] if ok and name else "you",
                    "coachRunning": bool(queue["processing"] or log_processing),
                    "coachQueue": queue,
                    "practiceLogs": practice_logs,
                },
            )
        if url.path == "/api/file":
            rel = parse_qs(url.query).get("path", [""])[0]
            try:
                with data_lock:
                    content = safe_path(rel).read_text(encoding="utf-8")
                return self._json(200, {"content": content})
            except FileNotFoundError:
                return self._json(404, {"error": f"missing {rel}"})
            except Exception as exc:
                return self._json(400, {"error": str(exc)})
        if url.path == "/pair":
            self.send_response(302)
            self.send_header("Location", "/")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        return super().do_GET()

    def do_POST(self):
        url = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self._json(400, {"error": "bad json"})

        if url.path == "/api/file":
            try:
                rel = payload["path"]
                if rel in {OBSERVATIONS_REL, JOBS_REL}:
                    return self._json(
                        409, {"error": "practice logs use the observation endpoint"}
                    )
                with data_lock:
                    atomic_write_text(safe_path(rel), payload["content"])
                return self._json(200, {"ok": True})
            except Exception as exc:
                return self._json(400, {"error": str(exc)})

        if url.path == "/api/observations":
            try:
                entry, created = get_observation_pipeline().submit(payload)
                return self._json(
                    201 if created else 200,
                    {"ok": True, "created": created, "observation": entry},
                )
            except ValueError as exc:
                return self._json(400, {"error": str(exc)})
            except Exception as exc:
                log(f"practice log save failed: {exc}")
                return self._json(500, {"error": "practice log could not be saved"})

        if url.path == "/api/chat":
            try:
                job = coach_queue.accept(
                    payload.get("text"), payload.get("requestId")
                )
                return self._json(202, {"ok": True, "job": job})
            except ValueError as exc:
                code = 409 if "requestId" in str(exc) else 400
                return self._json(code, {"error": str(exc)})
            except Exception as exc:
                return self._json(500, {"error": str(exc)})

        return self._json(404, {"error": "unknown endpoint"})


def background_loop():
    """Catch up daily logs, retry safely, and back up quiet snapshots."""
    last_autosave = 0.0
    first = True
    while True:
        if not first:
            time.sleep(30)
        first = False
        try:
            pipeline = get_observation_pipeline()
            result = pipeline.run_due(_run_staged_claude)
            if result.get("status") not in {
                "skipped",
                "retry-wait",
                "busy",
                "processed",
            }:
                log(f"daily practice-log batch: {result}")
            pipeline.retry_sync()
        except Exception as exc:
            log(f"daily practice-log scheduler error: {exc}")
        if time.monotonic() - last_autosave >= 120:
            last_autosave = time.monotonic()
            try:
                queue = coach_queue.snapshot()
                logs = get_observation_pipeline().summary()["counts"]
                if (
                    not queue["pending"]
                    and not queue["processing"]
                    and not logs.get("processing", 0)
                ):
                    sync_push("autosave")
            except Exception:
                pass


def main():
    global coach_queue
    if not DATA.exists():
        log("data-repo/ is missing - clone practice-room-data into it first.")
        return
    log("pulling latest...")
    git(HERE, "pull", "--rebase")
    git(DATA, "pull", "--rebase")

    pipeline = get_observation_pipeline()
    pipeline.migrate()
    coach_queue = CoachQueue(
        DATA,
        ClaudeCoachRunner(),
        lock=data_lock,
        on_completed=queue_completed,
        transaction_lock=coach_transaction_lock,
    )
    recovered = pipeline.recover()
    if recovered["recovered"]:
        log(f"recovered practice-log batches: {', '.join(recovered['recovered'])}")
    coach_queue.start()
    threading.Thread(target=background_loop, daemon=True).start()
    log(f"daily practice-log batch -> {pipeline.daily_time_text} Europe/London")
    log(f"Practice Room -> http://localhost:{PORT}")
    log(f"Phone (no login) -> {PHONE_URL}")
    if not os.environ.get("BROWSER_NONE") and "--no-browser" not in sys.argv:
        try:
            webbrowser.open(f"http://localhost:{PORT}")
        except Exception:
            pass
    try:
        ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    finally:
        coach_queue.stop()


if __name__ == "__main__":
    main()
