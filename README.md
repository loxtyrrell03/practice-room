# Practice Room

Thirty-eight days to the recital. A fixed navigation rail keeps Today, the full
week-by-week trajectory, Programme, Coach and Journal in reach. Today's practice
blocks have built-in timers, piece security meters and a coach (Claude, via the
Claude CLI) that knows the whole plan, remembers what you tell it, and rebuilds
tomorrow around how today actually went.

Today and tomorrow are separate dated records. The active `state.today` plan
cannot be advanced early; the coach writes future work to `data/day-plans.json`,
and the server promotes a ready plan only when that Europe/London date begins.
The Plan view shows the current day beside future dates, and ready plans expose
the exact block attached to every practice log or the dated reason it was
deferred.

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

While a reply is running, **show activity** expands a bounded live trace of the
model, elapsed time, file reads/edits, searches, verification steps, and
high-level reasoning phases. Raw private reasoning and tool inputs are never
sent to the browser.

## Practice-log durability

Multi-movement works use one daily block per movement. Each block carries stable
piece and movement IDs, so its one-line logs and any bar-level trouble spots are
movement-specific even when two movements use the same bar number.

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

## Working-repertoire changes

A definite coach message such as “I’m dropping X” updates the canonical active
repertoire inside the same prepared queue transaction as the reply. Ambiguous
language, an unknown piece, or an incomplete addition changes nothing and gets
one focused clarification question. Additions retain the pianist’s exact title,
version, current state, duration, deadline and target tempo; missing essentials
are never guessed.

Every confirmed change has one dated audit record. The coach must also rebuild
unfinished blocks, week goals and gates, remaining workload, learning and
memorisation deadlines, performance exposure, physical-risk limits, cut order,
and prescriptions before the transaction can commit. Completed journal and
trouble-spot history stays intact. The source PDFs on the Desktop are never
edited.

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
