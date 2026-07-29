#!/usr/bin/env python3
"""Practice Room - private server for the laptop and phone.

The browser never receives a GitHub credential. Tailscale Serve provides the
phone's stable private HTTPS route; this process reads and writes the data repo
on disk, runs the coach, and syncs GitHub only as a backup.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

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

coach_lock = threading.Lock()
sync_lock = threading.Lock()
pipeline_init_lock = threading.Lock()
coach_running = False
observation_pipeline = None
SESSION_FILE = HERE / ".coach-session.json"


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


def sync_push(reason):
    """Best-effort backup. Local durability does not depend on GitHub."""
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
                )
    return observation_pipeline


def chat_last_role():
    try:
        pipeline = get_observation_pipeline()
        with pipeline.lock:
            doc = json.loads(safe_path("data/chat.json").read_text(encoding="utf-8"))
        return doc["messages"][-1]["role"] if doc["messages"] else "coach"
    except Exception:
        return "coach"


def _latest_user_message():
    pipeline = get_observation_pipeline()
    with pipeline.lock:
        doc = json.loads(safe_path("data/chat.json").read_text(encoding="utf-8"))
    for message in reversed(doc.get("messages", [])):
        if message.get("role") == "user":
            return message
    return None


def _run_staged_claude(stage, batch):
    """Run one isolated coach turn. The pipeline applies its outputs."""
    prompt_name = (
        "daily-log-prompt.md" if batch["source"] == "daily" else "coach-prompt.md"
    )
    prompt_file = stage / ".github" / prompt_name
    batch_header = (
        "PRACTICE ROOM SERVER BATCH\n"
        + json.dumps(
            {
                "id": batch["id"],
                "source": batch["source"],
                "routeObservationIds": batch.get("routeObservationIds", []),
                "reviewObservationIds": batch.get("reviewObservationIds", []),
                "acknowledge": batch.get("acknowledge", False),
            },
            ensure_ascii=False,
        )
        + "\nEND SERVER BATCH\n\n"
    )
    prompt = batch_header + prompt_file.read_text(encoding="utf-8")
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
    last_error = "unknown coach failure"
    for extra in attempts:
        kind = "resumed session" if "--resume" in extra else "fresh session"
        model = extra[-1] if "--model" in extra else "default"
        log(f"coach thinking... ({batch['source']}, {kind}, model: {model})")
        result = subprocess.run(
            base + extra,
            input=prompt,
            cwd=str(stage),
            capture_output=True,
            text=True,
            timeout=900,
            shell=(os.name == "nt"),
        )
        if result.returncode == 0:
            new_session_id = None
            try:
                new_session_id = json.loads(result.stdout).get("session_id")
            except Exception:
                import re

                match = re.search(
                    r'"session_id"\s*:\s*"([^"]+)"', result.stdout or ""
                )
                new_session_id = match.group(1) if match else None
            if new_session_id:
                save_session(new_session_id)
                log(f"coach finished (session {new_session_id[:8]}...).")
            else:
                log(f"coach finished (no session id: {(result.stdout or '')[:120]!r})")
            return
        last_error = (result.stderr or result.stdout or "coach command failed")[:500]
        log(f"coach run failed (rc={result.returncode}): {last_error[:200]}")
    raise RuntimeError(last_error)


def run_coach():
    """Run a normal chat turn, including any eligible practice logs."""
    global coach_running
    with coach_lock:
        if coach_running:
            return
        coach_running = True
    try:
        message = _latest_user_message()
        if not message:
            return
        text = str(message.get("text") or "")
        source_key = str(message.get("id") or f"{message.get('ts', '')}|{text}")
        result = get_observation_pipeline().process_for_coach(
            _run_staged_claude,
            source_key=source_key,
            is_debrief=text.strip().lower().startswith("debrief"),
        )
        if result.get("status") in {"failed", "recovering"}:
            detail = result.get("error") or "unknown error"
            log(f"coach batch did not finish: {detail}")
            if result.get("status") == "failed":
                _append_coach_error(
                    "The coach couldn't run - check the server window "
                    "(the saved practice logs will retry safely)."
                )
                sync_push("coach error")
    except Exception as exc:
        log(f"coach error: {exc}")
        _append_coach_error(f"The coach hit an error: {exc}")
        sync_push("coach error")
    finally:
        coach_running = False


def _append_coach_error(text):
    try:
        pipeline = get_observation_pipeline()
        path = safe_path("data/chat.json")
        with pipeline.lock:
            doc = json.loads(path.read_text(encoding="utf-8"))
            doc["messages"].append(
                {
                    "role": "coach",
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "text": text,
                }
            )
            atomic_write_json(path, doc)
    except Exception:
        pass


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
            summary = get_observation_pipeline().summary()
            return self._json(
                200,
                {
                    "mode": "local",
                    "name": name.split()[0] if ok and name else "you",
                    "coachRunning": coach_running,
                    "practiceLogs": summary,
                },
            )
        if url.path == "/api/file":
            rel = parse_qs(url.query).get("path", [""])[0]
            try:
                pipeline = get_observation_pipeline()
                with pipeline.lock:
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
                path = safe_path(rel)
                pipeline = get_observation_pipeline()
                with pipeline.lock:
                    atomic_write_text(path, payload["content"])
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
            text = (payload.get("text") or "").strip()
            if not text:
                return self._json(400, {"error": "empty"})
            try:
                path = safe_path("data/chat.json")
                pipeline = get_observation_pipeline()
                with pipeline.lock:
                    doc = json.loads(path.read_text(encoding="utf-8"))
                    doc["messages"].append(
                        {
                            "role": "user",
                            "text": text,
                            "ts": time.strftime(
                                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                            ),
                        }
                    )
                    atomic_write_json(path, doc)
            except Exception as exc:
                return self._json(500, {"error": str(exc)})
            threading.Thread(target=run_coach, daemon=True).start()
            return self._json(200, {"ok": True})

        return self._json(404, {"error": "unknown endpoint"})


def background_loop():
    """Catch up daily logs after start/wake, retry safely, and back up quietly."""
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
                if chat_last_role() != "user":
                    sync_push("autosave")
            except Exception:
                pass


def main():
    if not DATA.exists():
        log("data-repo/ is missing - clone practice-room-data into it first.")
        return
    log("pulling latest...")
    git(HERE, "pull", "--rebase")
    git(DATA, "pull", "--rebase")
    pipeline = get_observation_pipeline()
    pipeline.migrate()
    recovered = pipeline.recover()
    if recovered["recovered"]:
        log(f"recovered practice-log batches: {', '.join(recovered['recovered'])}")
    threading.Thread(target=background_loop, daemon=True).start()
    log(f"daily practice-log batch -> {pipeline.daily_time_text} Europe/London")
    log(f"Practice Room -> http://localhost:{PORT}")
    log(f"Phone (no login) -> {PHONE_URL}")
    if not os.environ.get("BROWSER_NONE") and "--no-browser" not in sys.argv:
        try:
            webbrowser.open(f"http://localhost:{PORT}")
        except Exception:
            pass
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
