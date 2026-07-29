# AGENTS.md — Practice Room

Read this before changing anything in this repo (or its private sibling,
`practice-room-data`).

## What this is

Practice Room is a personal practice companion for one pianist (Lox) preparing a
final recital: ~35 minutes of repertoire (Mendelssohn Songs Without Words,
Bach C♯ minor P&F from WTC I, Scriabin Sonata No. 2, Ligeti Musica ricercata
1–2) on a hard 28-day countdown. It is not a generic to-do app and not a product
— it is one musician's cockpit for one month of high-stakes work, built around an
evidence-based practice plan (Chang's *Fundamentals of Piano Practice*
adjudicated against motor-learning and memory research; the full verdicts live in
`practice-room-data/context/research.md`).

## The vision

One glance answers "what do I do right now, and how bad is it?" One conversation
a day keeps the whole month on course.

- **The countdown is the spine.** Every screen orients around Day N of 28 and
  days to curtain. The month has weekly phases (Triage → Integration →
  Performance building → Landing) with measurable gates — the "This week" pill.
- **The coach owns the trajectory, the pianist owns the piano.** The AI coach
  (Claude, Opus 5, run through the Claude CLI) knows the plan, the research, the
  prescriptions table, a bar-level spot archive, and a persistent memory file.
  Evening debriefs rebuild tomorrow; reported weaknesses get specific,
  research-backed prescriptions with numbers (tempo %, minutes, reps, bar
  numbers); everything the pianist tells it is remembered across days.
- **Zero friction, zero auth, zero jargon.** Locally: double-click, it works —
  no tokens, no accounts. Phone: QR pairing, once. Language everywhere is plain,
  blunt, specific — "50–60% of performance tempo", never "practise it slowly".
- **Trust through honesty.** Security meters move on evidence (cold tests,
  filmed runs), not optimism. Red flags mean something. The site should feel
  like a calm, slightly theatrical green-room the pianist *wants* to open every
  morning — not an app nagging them.

## Architecture (two repos, three run modes)

- **This repo (public):** app shell only — `index.html`, `app.css`, `app.js`
  (vanilla, no build step), `server.py`, `Practice Room.bat`. Never put personal
  data here. GitHub Pages serves it at `/practice-room/`.
- **`practice-room-data` (private):** all state and the coach's brain —
  `data/state.json` (day, week outline, blocks, pieces/meters/flags),
  `data/chat.json`, `data/journal.json`, `data/spots.json` (date-stamped
  bar-level issue archive), `memory/MEMORY.md` (coach's cross-session memory),
  `context/` (plan, research verdicts, prescriptions, repertoire), `CLAUDE.md`
  (the coach's contract — the authoritative spec for what the coach may edit),
  and a GitHub Actions workflow as a PC-off fallback coach.
- **Run modes:** (1) local — `server.py` on :8977, files on disk, coach via
  local `claude -p` with a resumed session, background sync to GitHub;
  (2) hosted — same app on Pages talking to the private repo via the GitHub API
  (token delivered by the `/pair` QR flow), messages picked up by the local
  server within seconds via ETag polling; (3) Actions — `coach.yml` answers when
  no local server does (requires `CLAUDE_CODE_OAUTH_TOKEN` secret).

## Rules for agents working here

- The pianist's taste, learned the hard way: no twee naming, no hand-holding
  copy, no auth friction, big readable text, concrete numbers everywhere.
- Data contracts live in `practice-room-data/CLAUDE.md`. The site renders what
  the schema defines; if you change one side, change the other in the same
  commit. Top-level `state.json` keys are fixed; content is coach-owned.
- Color never carries meaning alone — every flag/status pairs color with an
  icon + word (accessibility ruling; keep it).
- Keep the app dependency-free and build-free. It must work served by
  `python -m http.server` equivalents and GitHub Pages alike.
- Don't break the zero-auth local path. It is the whole point.
- Test messages in the live chat must identify themselves as coming from a
  setup assistant, instruct the coach to change nothing, and be cleaned up
  afterwards — the thread belongs to the pianist.

## The finish line

Day 28 (2026-08-25): the pianist walks on stage with a programme that has
survived three mock performances, a coach that knows exactly how it got there,
and a journal of the whole month. If a feature doesn't serve that day, it
doesn't belong.
