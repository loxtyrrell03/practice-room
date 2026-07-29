# Practice Room

Twenty-eight days to the recital. Today's practice blocks with built-in timers,
piece security meters, a journal, and a coach (Claude, via the Claude CLI) that
knows the whole plan, remembers what you tell it, and rebuilds tomorrow around
how today actually went.

## Use it (zero setup)

Double-click **`Practice Room.bat`** in your local folder
(`C:\Users\loxty\Desktop\Repos\practice-room`). That's it — the site opens at
`http://localhost:8977`, data reads/writes go straight to disk, and the coach
runs through your already-logged-in Claude CLI. No tokens, no accounts.
Everything syncs to the private `practice-room-data` repo in the background as
a backup (best effort — offline is fine).

## Coach-message durability

Every accepted chat message is written to a private FIFO before the server
acknowledges it. Rapid phone/laptop sends stay in acceptance order; the UI shows
saved/waiting, processing, and failed/retrying state per message. Claude works
in an isolated snapshot, then the server applies one prepared result with a
deterministic reply ID. A restart resumes queued or prepared work without a
second visible reply. Backup is deferred until the entire chat queue drains, so
the GitHub Actions fallback cannot race the local coach.

## Practice-log durability

The one-line log on each practice block is accepted by a dedicated server
endpoint and written to disk immediately with a stable ID. Each note visibly
moves through `saved · pending`, `processing`, `processed`, or
`saved · failed · retrying`; phone and laptop submissions are serialized so one
device cannot overwrite the other.

Pending notes are consolidated in one coach batch at **20:30 Europe/London**
each day. Set `COACH_DAILY_LOG_TIME=HH:MM` before starting the server to change
that time. If the laptop is asleep or off at the scheduled time, the server runs
the latest missed batch after the next start or wake. A debrief that routes the
notes earlier marks them processed, so the daily batch skips them. Daily routing
never writes a chat reply or journal entry; general notes remain available for
the next real debrief.

Coach output is staged outside the live data tree and recorded as a prepared
transaction before apply. A Claude failure retries the same logical daily batch;
a restart finishes prepared output without generating duplicate spot-history or
memory effects. GitHub backup failure retries only the push, never the coach
effects.

Run the isolated fake-runner suite with:

```bash
python -m unittest discover -s tests -v
```

To set the folder up on another machine:

```bash
git clone https://github.com/loxtyrrell03/practice-room
git clone https://github.com/loxtyrrell03/practice-room-data practice-room/data-repo
```

## Phone / anywhere — no login

Open **https://lox.tail89d19b.ts.net:10000/** on the phone. Bookmark it or add it
to the Home Screen. There is no Practice Room login, token, QR code, or pairing
step: the phone reaches the laptop over the private Tailscale network already
used by both devices. The GitHub credential never enters the browser.

The laptop server starts automatically when Lox signs in to Windows, and
Tailscale Serve keeps the HTTPS address across restarts. The laptop must be
awake and Tailscale must be connected on both devices. The public GitHub Pages
address now forwards to the private address so old bookmarks do not show setup.

This public repo contains only the app shell and server — no personal data.
