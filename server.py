#!/usr/bin/env python3
"""Practice Room — local server. Zero auth: serves the site, reads/writes the
data repo on disk, runs the coach via your logged-in Claude CLI, syncs to
GitHub in the background, picks up messages sent from your phone, and pairs
your phone via QR (/pair) so the hosted site works anywhere."""
import json, os, shutil, subprocess, threading, time, webbrowser
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

HERE = Path(__file__).resolve().parent
DATA = HERE / "data-repo"
PORT = 8977
MODEL = os.environ.get("COACH_MODEL", "claude-opus-5")
os.environ.setdefault("MAX_THINKING_TOKENS", "10000")  # medium reasoning
HOSTED = "https://loxtyrrell03.github.io/practice-room/"

coach_lock = threading.Lock()
coach_running = False

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
        base = [claude, "-p", "--allowedTools", "Read,Write,Edit,Glob,Grep",
                "--permission-mode", "acceptEdits", "--max-turns", "40"]
        for extra in (["--model", MODEL], []):
            log(f"coach thinking… (model: {extra[1] if extra else 'default'})")
            with open(prompt_file, "r", encoding="utf-8") as f:
                r = subprocess.run(base + extra, stdin=f, cwd=str(DATA),
                                   capture_output=True, text=True, timeout=900,
                                   shell=(os.name == "nt"))
            if r.returncode == 0:
                log("coach replied.")
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

PAIR_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pair your phone</title>
<style>body{{font-family:system-ui;background:#101613;color:#f4f1e8;display:flex;
flex-direction:column;align-items:center;padding:40px 20px;gap:18px;text-align:center}}
#qr{{background:#fff;padding:14px;border-radius:12px}}
a{{color:#e2a94f;word-break:break-all;font-size:.85rem}}
p{{max-width:34em;color:#c9c5b6}}
button{{background:#e2a94f;border:none;border-radius:10px;padding:12px 22px;
font-weight:700;cursor:pointer}}</style></head><body>
<h1>Pair your phone</h1>
<p>Scan with your phone camera. It opens the hosted Practice Room already signed
in — nothing to type. The link contains your GitHub access, so don't share it.</p>
<div id="qr"></div>
<button onclick="navigator.clipboard.writeText(LINK).then(()=>this.textContent='Copied!')">Copy link instead</button>
<a id="fallback" href="{link}">{link}</a>
<script>const LINK={link_js};</script>
<script src="https://cdn.jsdelivr.net/npm/qrcodejs@1.0.0/qrcode.min.js"
onload="new QRCode(document.getElementById('qr'),{{text:LINK,width:240,height:240}});document.getElementById('fallback').style.display='none'"></script>
</body></html>"""

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
            try:
                r = subprocess.run(["gh", "auth", "token"], capture_output=True,
                                   text=True, timeout=20, shell=(os.name == "nt"))
                tok = r.stdout.strip()
                if r.returncode != 0 or not tok:
                    raise RuntimeError(r.stderr.strip() or "gh auth token failed")
                ok, name = git(DATA, "config", "user.name")
                first = name.split()[0] if ok and name else "you"
                link = f"{HOSTED}#t={tok}&o=loxtyrrell03&r=practice-room-data&n={first}"
                html = PAIR_HTML.format(link=link, link_js=json.dumps(link))
                body = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                self._json(500, {"error": f"pairing failed: {e}"})
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
    """Every 60 s: pull; if a message arrived from the phone (hosted site),
    answer it locally; otherwise push any local changes."""
    while True:
        time.sleep(60)
        try:
            git(DATA, "pull", "--rebase")
            if chat_last_role() == "user" and not coach_running:
                log("picked up a message from the hosted site — answering locally.")
                threading.Thread(target=run_coach, daemon=True).start()
            elif chat_last_role() != "user":
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
    log(f"Practice Room → http://localhost:{PORT}")
    log(f"Pair your phone → http://localhost:{PORT}/pair")
    if not os.environ.get("BROWSER_NONE"):
        try:
            webbrowser.open(f"http://localhost:{PORT}")
        except Exception:
            pass
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()

if __name__ == "__main__":
    main()
