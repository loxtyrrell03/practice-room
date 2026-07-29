#!/usr/bin/env python3
"""Practice Room — private server for the laptop and phone.

The browser never receives a GitHub credential. Tailscale Serve provides the
phone's stable private HTTPS route; this process reads and writes the data repo
on disk, runs the coach, and syncs GitHub only as a backup.
"""
import json, os, shutil, subprocess, sys, threading, time, webbrowser
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

HERE = Path(__file__).resolve().parent
DATA = HERE / "data-repo"
PORT = 8977
MODEL = os.environ.get("COACH_MODEL", "claude-opus-5")
os.environ.setdefault("MAX_THINKING_TOKENS", "10000")  # medium reasoning
PHONE_URL = "https://lox.tail89d19b.ts.net:10000/"

coach_lock = threading.Lock()
coach_running = False
SESSION_FILE = HERE / ".coach-session.json"

def load_session():
    try:
        d = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        if time.time() - d.get("created", 0) < 7 * 86400:
            return d.get("id")
    except Exception:
        pass
    return None

def save_session(sid):
    try:
        cur = {}
        try: cur = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        except Exception: pass
        created = cur.get("created", time.time()) if cur.get("id") == sid else time.time()
        SESSION_FILE.write_text(json.dumps({"id": sid, "created": created}), encoding="utf-8")
    except Exception:
        pass

def log(msg): print(f"[practice-room] {msg}", flush=True)

def git(repo, *args):
    try:
        r = subprocess.run(["git", "-C", str(repo), *args],
                           capture_output=True, text=True, timeout=90)
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except Exception as e:
        return False, str(e)

def sync_push(reason):
    git(DATA, "add", "-A")
    changed, _ = git(DATA, "diff", "--cached", "--quiet")
    if changed:  # rc=0 → no changes staged
        return
    git(DATA, "commit", "-m", f"[local] {reason}")
    git(DATA, "pull", "--rebase")
    ok, out = git(DATA, "push")
    log(f"synced ({reason})" if ok else f"push failed (offline is fine): {out[:120]}")

def safe_path(rel):
    p = (DATA / rel).resolve()
    if not str(p).startswith(str(DATA.resolve())):
        raise ValueError("bad path")
    return p

def chat_last_role():
    try:
        doc = json.loads(safe_path("data/chat.json").read_text(encoding="utf-8"))
        return doc["messages"][-1]["role"] if doc["messages"] else "coach"
    except Exception:
        return "coach"

def run_coach():
    global coach_running
    with coach_lock:
        if coach_running:
            return
        coach_running = True
    try:
        prompt_file = DATA / ".github" / "coach-prompt.md"
        claude = shutil.which("claude") or "claude"
        base = [claude, "-p", "--output-format", "json",
                "--allowedTools", "Read,Write,Edit,Glob,Grep",
                "--permission-mode", "acceptEdits", "--max-turns", "40"]
        sid = load_session()
        attempts = []
        if sid: attempts.append(["--resume", sid, "--model", MODEL])
        attempts += [["--model", MODEL], []]
        for extra in attempts:
            kind = "resumed session" if "--resume" in extra else "fresh session"
            log(f"coach thinking… ({kind}, model: {extra[-1] if '--model' in extra else 'default'})")
            with open(prompt_file, "r", encoding="utf-8") as f:
                r = subprocess.run(base + extra, stdin=f, cwd=str(DATA),
                                   capture_output=True, text=True, timeout=900,
                                   shell=(os.name == "nt"))
            if r.returncode == 0:
                sid_new = None
                try:
                    sid_new = json.loads(r.stdout).get("session_id")
                except Exception:
                    import re
                    m = re.search(r'"session_id"\s*:\s*"([^"]+)"', r.stdout or "")
                    sid_new = m.group(1) if m else None
                if sid_new:
                    save_session(sid_new)
                    log(f"coach replied (session {sid_new[:8]}…).")
                else:
                    log(f"coach replied (no session id in output: {(r.stdout or '')[:120]!r})")
                break
            log(f"coach run failed (rc={r.returncode}): {(r.stderr or r.stdout)[:200]}")
        else:
            _append_coach_error("The coach couldn't run — check the server window "
                                "(is the Claude CLI logged in? run `claude` once in a terminal).")
        sync_push("coach session")
    except Exception as e:
        log(f"coach error: {e}")
        _append_coach_error(f"The coach hit an error: {e}")
    finally:
        coach_running = False

def _append_coach_error(text):
    try:
        p = safe_path("data/chat.json")
        doc = json.loads(p.read_text(encoding="utf-8"))
        doc["messages"].append({"role": "coach",
                                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                "text": text})
        p.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(HERE), **kw)

    def log_message(self, *a):
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
        u = urlparse(self.path)
        if u.path == "/api/meta":
            ok, name = git(DATA, "config", "user.name")
            return self._json(200, {"mode": "local", "name": (name.split()[0] if ok and name else "you"),
                                    "coachRunning": coach_running})
        if u.path == "/api/file":
            rel = parse_qs(u.query).get("path", [""])[0]
            try:
                return self._json(200, {"content": safe_path(rel).read_text(encoding="utf-8")})
            except FileNotFoundError:
                return self._json(404, {"error": f"missing {rel}"})
            except Exception as e:
                return self._json(400, {"error": str(e)})
        if u.path == "/pair":
            self.send_response(302)
            self.send_header("Location", "/")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        return super().do_GET()

    def do_POST(self):
        u = urlparse(self.path)
        try:
            n = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._json(400, {"error": "bad json"})

        if u.path == "/api/file":
            try:
                p = safe_path(payload["path"])
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(payload["content"], encoding="utf-8")
                return self._json(200, {"ok": True})
            except Exception as e:
                return self._json(400, {"error": str(e)})

        if u.path == "/api/chat":
            text = (payload.get("text") or "").strip()
            if not text:
                return self._json(400, {"error": "empty"})
            try:
                p = safe_path("data/chat.json")
                doc = json.loads(p.read_text(encoding="utf-8"))
                doc["messages"].append({"role": "user", "text": text,
                                        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
                p.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
            except Exception as e:
                return self._json(500, {"error": str(e)})
            threading.Thread(target=run_coach, daemon=True).start()
            return self._json(200, {"ok": True})

        return self._json(404, {"error": "unknown endpoint"})

def background_loop():
    """Push a quiet backup after local or phone writes."""
    while True:
        time.sleep(120)
        try:
            if chat_last_role() != "user":
                sync_push("autosave")
        except Exception:
            pass

def main():
    if not DATA.exists():
        log("data-repo/ is missing — clone practice-room-data into it first.")
        return
    log("pulling latest…")
    git(HERE, "pull", "--rebase"); git(DATA, "pull", "--rebase")
    threading.Thread(target=background_loop, daemon=True).start()
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
