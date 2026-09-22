# Practice Room

An adaptive piano practice planner that connects your academic-year repertoire to the rooms you have actually booked.

The week-first view shows confirmed sessions, separate playing/study/rest time, and the next useful task. A session opens into focused instructions, an explicit start/pause/complete timer and a durable quick note. Repertoire, the year trajectory, a model-selectable coach and the practice journal stay close at hand.

## Booking-aware planning

The app reads the existing ASIMUT Booker's validated agenda once a minute. New, moved, shortened or removed bookings automatically redistribute unfinished work without requiring an AI call. The planner respects clashes, room boundaries, daily limits and prior actions. Unknown or stale dates remain visibly uncertain. It never creates or changes an ASIMUT reservation.

The coach shapes priorities and instructions from dated observations. The scheduler fits them into real time. Unknown readiness and exact dates stay unknown; elapsed time is never converted into an invented success percentage.

## Run locally

```powershell
python -m pip install -r requirements.txt
python server.py --no-browser
```

Private state belongs in `data-repo/`, linked or cloned from the private data repository. The local site uses http://127.0.0.1:8977/. It uses the signed-in local Codex or Claude command-line runtime; no API credential is sent to the browser.

The established phone address is https://lox-pc.tail89d19b.ts.net:10000/. Deployment uses the existing phone gateway and preserves unrelated services, score files and browser storage. Read AGENTS.md before deploying.

## Verify

```powershell
python -m unittest discover -s tests -v
node --check app.js
node --check scripts/music-home-server.mjs
```

Tests use small isolated fixtures. Live reservations and the user's coach history must not be used for test writes.
