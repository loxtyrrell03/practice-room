# AGENTS.md — Practice Room

Read this before changing anything in this repo (or its private sibling,
`practice-room-data`).

## What this is

Practice Room is a personal practice companion for one pianist (Lox) preparing a
final recital: ~35 minutes of repertoire (Mendelssohn Songs Without Words Op. 30/1,
30/2, 19b/1; Bach C♯ minor P&F from WTC I; Scriabin Sonata No. 2; Ligeti Musica
ricercata 1–2) on a hard countdown — **Day 1 = 29 Jul 2026, recital Day 38 =
Fri 4 Sep 2026**. It is not a generic to-do app and not a product — it is one
musician's cockpit for one high-stakes run, built on an evidence-based plan
(Chang's *Fundamentals of Piano Practice* adjudicated against motor-learning and
memory research; verdicts in `practice-room-data/context/research.md`).

## The vision

One glance answers "what do I do right now, and how bad is it?" One conversation
a day keeps the whole run on course. Everything the pianist notices at the piano
is captured in seconds, dated, and turned into tomorrow's instructions — and
nothing stale ever masquerades as current.

- **The countdown is the spine.** Day N, days to curtain, a 38-dot strip, weekly
  phases (Triage → Integration → Consolidation → Performance weeks → Freeze →
  Taper → Recital) with measurable gates on Days 7/14/24/34 — surfaced by the
  "This week" pill and gate-day banners.
- **The coach owns the trajectory, the pianist owns the piano.** Evening
  debriefs rebuild tomorrow; mid-practice observations get routed; weaknesses
  get research-backed prescriptions with numbers (tempo % of PT, minutes, reps,
  bar numbers) — never a technique name without a how-to on the actual passage.
- **Zero friction, zero auth, zero jargon.** Locally: double-click, it works.
  Phone: open the permanent private Tailscale URL; no QR, token, pairing, or
  site login. Blunt, plain, specific language everywhere.
- **Trust through honesty.** Meters move on evidence (cold tests, filmed runs),
  not optimism. Dated facts only; stale ones are retired, not repeated.

## Feature inventory (current)

**Navigation + forward plan** — a fixed desktop sidebar (compact bottom navigation
on phone) for Today, Plan, Programme, Coach and Journal, with Focus mode ready in
the rail. Plan opens with a selectable seven-day calendar: Today is the fixed
active plan, tomorrow is a separate timed plan with every source log visibly
scheduled or explicitly deferred, and later dates are rough phase snapshots
that sharpen as daily evidence arrives. A ready future plan is promoted only
when its UK calendar date begins. The complete 38-day trajectory remains below
as an expandable timeline; the coach's live current-week outline overrides its
matching phase and repertoire changes rewrite all unfinished phases.

**Today view** — countdown hero + focus line; morning cold-test card (per-piece
✓/✕ chips); practice blocks with per-block countdown **timers** (start/pause, chime, auto-done),
with multi-movement works split into movement-specific cards whose stable
movement IDs carry through to quick logs and trouble spots,
attention flags (red *needs work* / amber *focus* / green *secure* — always
color + word), "why this?" → coach, and scheduled **break blocks** (slim dashed
cards, 8–12 min per ~50–60 min of playing; tedious work — fingerings, note
learning, fresh memorisation — sequenced early in the session as a soft rule).
Future days also carry explicit timed **score-study** blocks, marked *off bench*,
for forming and testing interpretation rather than passive score reading.
New block instructions are structured as short bullets: each bold lead names
the technique and its dose (minutes, reps, passes or runs), followed by one
plain-language action and an observable pass/stop test.

**Focus mode** — one block at a time, full screen: title, instructions, big
timer, quick-log field, Done→next / Skip / Exit; timer completion auto-advances;
breaks render as rest screens. Entered via the amber pill or any block's
"focus →".

**Practice log (observations)** — a one-line field on every block (list and
focus mode): "RH too loud b.57" → saved instantly through a concurrency-safe
server endpoint with a durable ID, timestamp, day, block and visible lifecycle
(saved/pending/processing/processed/failed). At 20:30 Europe/London, one staged
daily coach batch routes everything still eligible; start/wake catches up a
missed slot. Earlier debrief routing makes the daily batch skip those IDs.
Prepared transactions make retries/restarts idempotent across spot and memory
effects. Daily routing never fabricates chat, a debrief or a journal entry;
general notes remain available for acknowledgement at the next real debrief.

**Programme view** — per piece: security meter (0–100, evidence-driven),
reliable-tempo %, last cold result, attention tag, 2–4 short status bullets
with bold plain-language leads, and the piece's **open spots** (bar + issue +
date + status). Long status paragraphs are not permitted.

**Coach chat** — debriefs (asks 2–3 questions, writes the journal, rebuilds
tomorrow, updates meters/flags/memory), why-questions, instant restructuring
("I've got 2 hours" / pain → 48 h protocol), prescriptions from
`context/prescriptions.md`, gate checklists on gate days. "What your coach
remembers" panel shows `memory/MEMORY.md`. Quick-action chips for common asks.
Definite add/drop instructions update the canonical active repertoire and
recompute the whole remaining plan in one audited transaction; ambiguous,
unknown, or incomplete requests clarify without mutation. Completed history is
preserved and Desktop source PDFs are never edited.
Accepted messages enter a durable FIFO before HTTP success; each has a stable
ID, visible saved/processing/retrying state, one targeted coach turn and one
deterministic reply. Prepared results and queued work recover after restart.
Repo-wide backup waits for the FIFO to drain so Actions cannot answer a message
that the local coach still owns.

**Journal** — entries written by the coach from debriefs, newest first.

**Coach runtime** — Claude CLI, **Opus 5, medium thinking budget**
(`MAX_THINKING_TOKENS=10000`); locally it **resumes a persistent session**
(`.coach-session.json`, weekly rotation, fallback to fresh) so conversation
context carries across messages; the full transcript + memory file cover
continuity across sessions and devices.

**Memory & freshness** — `memory/MEMORY.md` updated after every conversation
(facts, decisions, PTs, trajectory — curated, dated, <120 lines). Freshness
contract: nothing older than ~10 days asserted as current without re-testing;
spots quiet 14 days get verified or marked stale on gate days; superseded facts
are replaced, not accumulated.

**Resilience** — a configured browser is never bounced to the connect screen:
loads retry ×3 with backoff, then fall back to cached data with an offline
banner. Cache-busted assets (`?v=N` — bump on every deploy).

## Architecture (two repos, three run modes)

- **This repo (public):** app shell only — `index.html`, `app.css`, `app.js`
  (vanilla, no build step), `server.py`, `Practice Room.bat`, this file. Never
  put personal data here. GitHub Pages serves it at `/practice-room/`.
- **`practice-room-data` (private):** all state and the coach's brain —
  `data/state.json` (dates, week outline, blocks, pieces), `data/chat.json`,
  `data/journal.json`, `data/spots.json`, `data/observations.json`,
  `data/day-plans.json`, `data/repertoire-changes.json`,
  `.coach-queue.json`, `.coach-results/`, `memory/MEMORY.md`, `context/`
  (plan with the authoritative 38-day timeline
  map, research verdicts, prescriptions, repertoire), `CLAUDE.md` (the coach's
  contract — authoritative for what the coach may edit; the coach owns ALL
  state content including programme changes, block fields, flags and the week
  outline), and the Actions fallback workflow.
- **Run modes:** (1) **local** — `server.py` on :8977 (double-click the .bat;
  local install lives at `C:\Users\loxty\Desktop\Repos\practice-room` with the
  data repo cloned into `data-repo/`): files on disk, coach via local
  `claude -p`, background GitHub backup sync.
  (2) **phone** — same local server through the permanent, tailnet-only
  `https://lox.tail89d19b.ts.net:10000/` Tailscale Serve route; no browser
  credential. GitHub Pages forwards old bookmarks there. (3) **Actions** —
  `coach.yml` answers when no local server does (needs `CLAUDE_CODE_OAUTH_TOKEN`
  secret; not yet configured).

## Rules for agents working here

- The pianist's taste, learned the hard way: no twee naming, no hand-holding
  copy, no auth friction, big readable text, concrete numbers everywhere, and
  never ship a "fix" you haven't verified in the actual output.
- Data contracts live in `practice-room-data/CLAUDE.md`. Site and schema change
  together, in the same commit. Top-level `state.json` keys are fixed; content
  is coach-owned.
- Color never carries meaning alone — every flag/status pairs color with an
  icon + word.
- Keep the app dependency-free and build-free; bump the `?v=` asset query on
  every deploy.
- Every completed change in this repo must be committed and pushed directly to
  `origin/main` before the task is considered finished. `origin/main` is the
  live GitHub Pages source. Do not leave verified work only in the local
  worktree; if a push is genuinely blocked, report the blocker explicitly.
- Don't break the zero-auth local path. It is the whole point.
- Beware the escaping layers when patching files from a shell: heredoc + JSON
  transforms mangle `\uXXXX` sequences — patch via script files and match on
  ASCII anchors, and verify changes in the built artifact, not the patch log.
- Test messages in the live chat must identify themselves as from a setup
  assistant, instruct the coach to change nothing, and be cleaned up afterwards
  — the thread belongs to the pianist.

## The finish line

Day 38 (4 Sep 2026): the pianist walks on stage with a programme that has
survived three mock performances (Days 29/32/34), a coach that knows exactly
how it got there, and a journal of the whole run. If a feature doesn't serve
that day, it doesn't belong.
