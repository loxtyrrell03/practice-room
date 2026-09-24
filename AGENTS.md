<!-- USER-BROWSER-COMPUTER-POLICY -->
## Chrome plugin and Computer Use

- Chrome plugin use and Chrome browser control are allowed at will for the user's tasks; no separate request or permission is required.
- Computer Use (native desktop/app control) remains prohibited unless the user explicitly asks for it in the current prompt. Chrome plugin permission does not authorize Computer Use.
- Do not infer Computer Use permission from a task needing a GUI, an application or webpage being mentioned, an existing session, or permission in an earlier prompt. Use Chrome plugin tools, commands, scripts, APIs, connectors, or direct file operations where appropriate; if Computer Use is essential, explain the limitation and ask before invoking it.

Updated at the user's request on 2026-09-12.
<!-- /USER-BROWSER-COMPUTER-POLICY -->

<!-- USER-UI-DESIGN-POLICY -->
## UI and app design: standing user requirements

- Apply these requirements to all UI/app design work and all agents, in this repository and its delivery targets. Use the application's own visual language: inspect its current screens, colour/theme tokens, typography, spacing and reusable controls before designing. Do not invent a new palette or visual identity unless the user asks for it.
- Make interfaces simple, coherent and well organised around the user's tasks. Keep the default surface concise; remove filler, repeated explanations, implementation jargon and decorative panels that do not help the next action.
- Give every number a clear label, unit and scope. Explain percentages, probabilities, scores, sample sizes and estimates in plain language; distinguish an estimate from a confirmed fact, and missing data from zero. Keep detailed calculation/method copy off the default surface.
- Put short explanations behind a small, adjacent, hoverable question mark. Reuse the app's help component; support keyboard focus and touch as well as hover, with dismissible, viewport-bounded help. Prefer one or two short sentences. Keep essential errors, costs, destructive consequences and required decisions visible rather than hiding them in a tooltip.
- Make the immediate next action obvious and close to its item. Use explicit, state-appropriate verbs such as Import games, Import & prep, Open Prep or their domain equivalent. Separate acquiring data from opening already-ready content; expose progress, cancellation, failure and retry beside the action. Do not make users hunt through unrelated screens to begin their task.
- For a substantial new interface or redesign, map every affected surface and state first, then present three genuinely different SVG/Figma prototypes within the existing app style unless the user specifies another count or has already chosen a direction. Include setup, main views, details, settings, help, loading, empty, error, progress, cancellation and relevant confirmations, plus narrow/mobile layouts where applicable. Do not present one attractive main screen as the complete design.
- Make prototypes concrete, reviewable and editable; show them to the user and label invented example data. Honour the chosen design and subsequent feedback consistently across all affected surfaces. Once the user says to implement a direction, proceed without asking for the same approval again. Small fixes within an approved design do not require a fresh three-option exercise.
- Verify rendered layouts and the real interaction path, including action wiring, help behaviour and narrow widths. Fix overlap, clipping, unclear labels and state inconsistencies. Preserve active sessions, unsaved edits and existing data. Clearly distinguish prototype/source/test evidence from deployed or physical-device verification.

Adopted as cross-repository user guidance on 2026-09-08. Project-specific architecture and safety rules still apply; these requirements describe design and delivery, not authorization for unrelated actions.
<!-- /USER-UI-DESIGN-POLICY -->

# Practice Room - repository guidance

## Application and data ownership

Practice Room is a private, personal piano practice planner for an academic year. The active programme, dated evidence, monthly/exact deadlines, research and coaching context live in the private sibling `practice-room-data` through `data-repo/`. Do not put personal repertoire, bookings, logs or reports in this public source repository.

The previous single-recital countdown is retired. Exact assessment dates may be null; display deadline windows honestly. Completed timers are not learning evidence. Previous plans/context are archived privately, while chat, journal, observations and server ledgers retain their history.

## Architecture

- Dependency-free browser: index.html, app.css, app.js, manifest.webmanifest, icon.svg. The owner selected refinement B: a short daily task list and month-by-month repertoire overview, with expandable preparation stages. The owner explicitly replaced the forest/amber identity with a light, modern neutral-and-blue theme. Preserve that approved direction across desktop and phone; no further layout approval is needed.
- server.py uses Python's standard library plus tzdata on Windows. Install `python -m pip install -r requirements.txt`; default loopback port remains 8977. PRACTICE_DATA_ROOT/PRACTICE_PORT support isolated fixtures.
- asimut_bookings.py imports only the canonical Booker's read-only agenda/mutation context. It never starts a booking scan or changes a reservation. Cache stays private. Missing, stale, pending or incomplete evidence never means zero available time or permission to retire bookings.
- academic_sessions.py allocates work deterministically inside verified reservations after subtracting overlaps and timetable conflicts. It handles exact deadline scope, relative work/movement weights, recent work, breaks, daily caps and user session limits. Preserve active/paused/completed history through all replans. A session action is durable before follow-up replanning.
- session_service.py polls the existing Booker context every 60 seconds. The browser polls sessions every 25 seconds and refreshes on focus. data/sessions.json is the authoritative server-owned plan; state.today/day-plans are legacy suggestions and never proof of booking capacity.
- The coach changes planning focus/passTest/weight/deadlineMonth in current piece/movement records. Those changes drive deterministic replanning. Academic settings, phases and context survive both nested transaction layers. The coach cannot write sessions or reservations.
- Automatic observation routing, review and badges use academic-year.startDate (legacy state.startDate fallback) plus active piece IDs; archived evidence stays unchanged. Lifecycle guards use processingTotal so archived in-flight work still prevents an unsafe restart or backup.
- The durable FIFO and observation pipeline retain acceptance order, idempotent replies, isolated staged edits and restart recovery. Per-block notes keep stable work/movement IDs. AI is not needed for routine booking changes.
- Codex GPT-5.6 Terra, medium, is the default local coach. Catalog availability reflects the local executable; missing providers fail once with an actionable message. Do not silently change an accepted message's model.

## Phone service contract

<!-- USER-PHONE-SERVICE-CONTRACT -->
### Universal phone-service identity and ownership

- Owner instruction, 2026-09-14: this PC's permanent Tailscale name is `lox-pc`, with canonical DNS `lox-pc.tail89d19b.ts.net`. Use this one hostname for all hosted phone apps. Do not rename the Tailscale device, adopt the Windows computer name, use `windows-t8v5137`, `gaming-pc`, or `lox-1` for new links/builds, or change the Windows hostname to repair an app. A future hostname/port/path/runtime migration requires an explicit owner request for that migration; ordinary app work, deployment, troubleshooting, and requests to make a service work do not authorize it.
- Preserve these existing private HTTPS routes and their loopback services:
  - En Croissant: `https://lox-pc.tail89d19b.ts.net/` -> `127.0.0.1:8786` controller -> `127.0.0.1:8787` home server.
  - Show Streamer / Stream Finder: `https://lox-pc.tail89d19b.ts.net/streamer/` -> `127.0.0.1:8792`.
  - AsimutBooker: `https://lox-pc.tail89d19b.ts.net:10443/` -> `127.0.0.1:8794`.
  - Supper: `https://lox-pc.tail89d19b.ts.net:11443/` -> `127.0.0.1:4318`.
  - Practice Room: `https://lox-pc.tail89d19b.ts.net:10000/` -> `127.0.0.1:8790`; its proxied API uses the En Croissant home server.
  - Outpost promotional site: `https://lox-pc.tail89d19b.ts.net:4174/` -> `127.0.0.1:4174`.
  - Jellyfin: `https://lox-pc.tail89d19b.ts.net:8096/` -> `127.0.0.1:8096`.
- Before any hosting change, compare live `tailscale status --json` and `tailscale serve status --json` with this contract. A mismatch is a fault to diagnose, never a reason to silently redefine the contract. Never reset Serve or Funnel, reassign occupied routes, enable public Funnel, reinstall shared hosting, or move/copy a live runtime as incidental project work. Preserve unrelated routes, including legacy compatibility handlers and the separate engine/SMS endpoints.
- Preserve the existing scheduled tasks, headless supervisors, runtime directories, ownership, credentials, sessions, databases, downloads and browser-origin storage. Discover the actual listener and launcher before restarting only the intended service. Source checkouts and serving copies can differ; a deploy must preserve the serving copy's state and intended version. Check active work before a restart; Streamer requires 60 continuous seconds of complete idle evidence and an immediate pre-stop recheck. Explicit user On/Off choices remain authoritative; do not routinely force disabled services on.
- Configure each app's allowed Host/Origin, advertised phone URL and compiled PWA origin consistently. Verify the actual HTTPS session/bootstrap/data API and a rendered connected app after a change; a page title or HTTP 200 from the static shell/health endpoint alone does not prove the app works. Never claim physical-phone verification from a PC browser check. Write out full canonical URLs when the owner asks for links.
- The 2026-09-14 repair aligns Booker's server and PWA to `lox-pc`, refreshes its expired local assistant executable path, and restores En Croissant's requested enabled state. Historical per-repository notes using other hostnames are superseded by this contract. Keep this block in the universal guidance and the canonical repository root guidance.
<!-- /USER-PHONE-SERVICE-CONTRACT -->

The existing MusicPracticeHomeServer gateway stays on 127.0.0.1:8790. `scripts/music-home-server.mjs` routes only the explicit planner API set to 8977 and supervises that backend; all other API requests keep their existing En Croissant 8787 destination. Stockfish proxy routing remains intact. Static planner assets live in the existing service's site/planner subdirectory; old score-app files and browser storage stay preserved.

Before any deployment, inspect listener/launcher ownership, compare live Tailscale status and Serve JSON, verify the actual AppData backing path through an unpackaged broker, and check for active coach/timer work. `scripts/deploy-phone.ps1` checks exact executable, script, PID and process creation time before a scoped gateway/backend restart. It keeps one small rollback slot and restores the previous launcher, assets and owned service if startup fails, without changing routes. Four focused deployment tests cover rollback, ownership, supervisor recovery and static containment. A live connected rendered HTTPS browser check is required; a shell or health 200 is insufficient. PC browser verification is not physical-phone proof.

## Verification and milestones

Use tiny synthetic fixtures and `python -m unittest discover -s tests -v`. Include changed/new bookings, shorter slots, cancellation, stale sources, overlap/class conflicts, active/completed preservation, model selection, nested coach persistence and duplicate note submission. Browser QA must cover desktop/narrow layouts, session actions, help, settings, coach scroll and offline/draft states. Pre-year or undated coach messages remain in the collapsed previous-programme archive; they must not appear to be current guidance. Tests must not send messages into the live user's coach history.

At every coherent verified milestone inspect Git state, commit only relevant files, update this guidance, and push main to its configured remote. Preserve concurrent changes. Public code and private data have separate repository boundaries. Never include dependencies, generated preview/PDF files, local runtime state or large datasets in source commits.

## Current verification milestone

The academic-year reset is committed in the private repository. The backend and deployment checks passed 159 Python tests; the frontend passed 11 contract tests and the gateway routing test. Background refresh must release the refresh button in its finally path.

Deployed 22 September 2026 to the existing MusicPracticeHomeServer gateway on 8790, supervising the planner on 8977. Canonical HTTPS year, session and coach metadata APIs, matching static assets, the legacy score-app /home route, and the connected rendered app were verified. Chrome checks covered desktop and 390-pixel width, actual booking refresh, session/repertoire/settings/help/journal surfaces, previous-programme archives and a composer above the phone navigation. No browser console errors were observed. A synthetic coach runtime request completed successfully; live user messages, notes and timers were not fabricated for QA. Tailscale routes and the En Croissant listener were unchanged. Physical iPhone and on-screen keyboard behaviour remain unverified.


## Personal practice workflow refinement - 22 September 2026

The owner wants a quiet personal practice notebook: choose any piece freely, jot passage-specific notes while playing, and have those notes lead automatically to editable/dismissible future work. The plan guides practice rather than restricting it to a booking or timer. Per-piece learning stages and timeline must be immediately clear in Repertoire. Refinement B is approved, including expandable section detail and a light theme.

Cancelled bookings are hidden from both Today and Plan and cannot remain the selected booking, while their stored practice history is retained. Repetitive explanatory footers, empty-allocation jargon, zero counters on empty sessions and unsolicited empty-day suggestion panels were removed. Essential booking-change warnings remain visible.


The small cleanup is deployed and verified against the real HTTPS site: cancelled reservations are absent from both planning views, the empty ended-session details retain only their time/room, and Today omits the redundant capacity/target strip. No service restart or reservation mutation was needed. The editable review boards and state coverage are in the task workspace's design/refinement directory; B is selected for implementation.

### Notebook backend milestone

`practice_notebook.py` owns private `data/notebook.json`: free-practice notes and generated tasks are accepted in one atomic write with retry identity, stable piece/movement scope, and server-side validation. A small deterministic interpreter handles common musical observations; other notes remain in history. Task edits, dismissals and explicit improvement persist across replanning; timers never resolve tasks. Optimistic revisions protect cross-device task and stage edits. The allocator includes open follow-ups once per task per day, within existing capacity and history guards. Preparation routes use deadline scope and structured editable stages, with unknown exact dates retained as windows. The notebook API is explicitly routed through the existing phone gateway. Nine synthetic notebook tests cover note-to-schedule, cancellation, bounded duration, retries, edits, dismissal, timeline scope and corrupt-data preservation. This backend milestone is source-only until the coordinated UI deployment.

### Approved light B interface

`notebook-ui.js` shares the dependency-free app context and owns Today notes/tasks and Repertoire timelines. Load it after `app.js`; include it in both static allowlists and the phone deployment asset set. Today is the initial view, with a short expandable practice list and a piece/movement note composer independent of sessions. Plan retains booked sessions; Coach and Journal are under More. White/pale-grey surfaces, Inter typography and a blue accent replace the old palette, including dialogs, help, coach and PWA assets.

Desktop preparation bars expand their goal, checkpoint, movement priorities and related notes/tasks. Phone shows a compact piece list with two levels of expansion. Stage edits persist and change the unstarted plan within their date/movement scope; active and completed history remains untouched. Open sections and focused controls survive polling, and note drafts survive switching piece, refresh, failed save and a concurrent newer edit. Existing block-note history remains available. The coach receives read-only notebook context on later turns. Verification before deployment: 168 Python tests, 16 frontend contract tests and the gateway routing test pass; synthetic Chrome checks covered stage editing and note-to-task editing/dismissal, plus desktop and 390px layouts. Physical-phone keyboard behaviour remains unverified.

The Windows supervisor can end its Python child when the gateway stops. Deployment must accept that only after both the owned listener and the original process have exited; a live process without its listener or any replacement owner still blocks deployment. `Stop-RemainingPlanner` handles this lifecycle and its exit race. Five isolated deployment/gateway tests cover the exited-child case, successor rejection, rollback and unrelated-file preservation.

Every new frontend file must be present in all three delivery paths: the Python static allowlists, gateway `plannerAssets`, and deployment asset list. The gateway routing test checks the notebook script's exact JavaScript body/content type as well as its APIs; an existing file in site/planner alone does not prove the gateway serves it.

Light B is deployed on the canonical HTTPS route with asset version 39. Real connected browser checks confirmed seven works/eight scoped routes, the expanded goal/checkpoint, the 4–6-hour settings and existing booked sessions. Desktop and 390px repertoire, Today, Plan, settings and Coach were checked; no invented notes or timers were saved to the live record. Static files match the source, notebook APIs return real data, and unrelated Tailscale routes and the En Croissant listener were preserved. Version 39 invalidates a cached missing-script response encountered during rollout; keep asset versions moving when correcting a bootstrap response. Test coverage is 168 full-suite Python passes plus the additional passing deployment lifecycle case, 16 frontend checks and the gateway test. Physical iPhone/keyboard behaviour remains unverified.


### Multiple performance dates - backend milestone, 24 September 2026

`performance_deadlines.py` shares event scope, importance and mutation rules. Academic `deadlines` is authoritative for piece/movement membership; stale coach deadline links cannot revive removed events. Existing assessment windows without an explicit importance retain high priority. New editor events default to medium. Scheduling combines existing workload weights with a smooth, bounded importance/proximity factor, evaluated for each booking day; the strongest applicable future event wins so duplicate dates cannot multiply a work's share. Passed events fall back to maintenance only when no future event remains. Capacity, breaks and started/completed history guards remain in force.

`/api/preferences` accepts one `performance` mutation (save/archive/restore) with event revision and stable request identity. Writes are atomic, retries are idempotent, stale edits fail, and archiving preserves stage overrides/history for restoration. Events can share pieces or scope individual movements. Date or month windows and priority flow into preparation routes; near-term stages divide available days, while longer plans retain the established month-aligned overview. Backend verification: 177 synthetic Python tests pass, including scoped dates, revisions/retries, validation atomicity, actual allocation changes, multiple-event bounds and preserved started history.


### Performance editor and timeline integration

Repertoire offers Dates beside every piece and All performances & deadlines above the timeline. One editor handles shared events, any selected movements, exact dates or provisional months, and low/medium/high importance. Existing major assessment windows retain high importance; new events default to medium. Past dates and removed events stay collapsed in the manager; removed events can be restored. Settings links to the same manager rather than maintaining a second date-editing path. Timeline lanes identify each event and importance, and month filters derive from actual dates. Editors retain their input on failures; repeat submissions retain request identity. A mutation requests a fresh snapshot after an already-running poll, preventing an older read from hiding a just-saved date. The mobile editor uses the existing touch/keyboard help and scrollable dialog design. No new top-level navigation or theme was introduced.


Before deployment, 177 Python tests, 20 frontend contract tests and the gateway test pass. Synthetic connected Chrome checks verified per-piece create, movement scope, importance and exact/month editing, removal and restoration, reload persistence, and 390px editor/help/timeline layouts. No test dates were written to live repertoire. Source uses asset version 40; the coordinated live deployment is recorded separately after verification.


Performance dates are deployed on 24 September 2026 at the unchanged canonical HTTPS route, asset version 40. Source and serving assets match. Connected Chrome verified real repertoire (including a piece with two scoped windows), the date manager, editor and settings entry on desktop and a tab-specific 390px viewport without horizontal overflow or browser console errors. Canonical year/notebook/session APIs returned real data; live performance records and notes were not fabricated. The legacy score route, Tailscale configuration and En Croissant listener were preserved. The isolated preview process was stopped. Physical iPhone and its on-screen keyboard remain unverified.
