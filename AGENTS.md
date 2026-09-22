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

- Dependency-free browser: index.html, app.css, app.js, manifest.webmanifest, icon.svg. The approved interface is week-first with desktop and narrow layouts in the established forest, cream and amber style.
- server.py uses Python's standard library plus tzdata on Windows. Install `python -m pip install -r requirements.txt`; default loopback port remains 8977. PRACTICE_DATA_ROOT/PRACTICE_PORT support isolated fixtures.
- asimut_bookings.py imports only the canonical Booker's read-only agenda/mutation context. It never starts a booking scan or changes a reservation. Cache stays private. Missing, stale, pending or incomplete evidence never means zero available time or permission to retire bookings.
- academic_sessions.py allocates work deterministically inside verified reservations after subtracting overlaps and timetable conflicts. It handles exact deadline scope, relative work/movement weights, recent work, breaks, daily caps and user session limits. Preserve active/paused/completed history through all replans. A session action is durable before follow-up replanning.
- session_service.py polls the existing Booker context every 60 seconds. The browser polls sessions every 25 seconds and refreshes on focus. data/sessions.json is the authoritative server-owned plan; state.today/day-plans are legacy suggestions and never proof of booking capacity.
- The coach changes planning focus/passTest/weight/deadlineMonth in current piece/movement records. Those changes drive deterministic replanning. Academic settings, phases and context survive both nested transaction layers. The coach cannot write sessions or reservations.
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

Before any deployment, inspect listener/launcher ownership, compare live Tailscale status and Serve JSON, verify the actual AppData backing path through an unpackaged broker, and check for active coach/timer work. `scripts/deploy-phone.ps1` performs a scoped gateway/backend restart with a small rollback copy and no route changes. A live connected rendered HTTPS browser check is required; a shell or health 200 is insufficient. PC browser verification is not physical-phone proof.

## Verification and milestones

Use tiny synthetic fixtures and `python -m unittest discover -s tests -v`. Include changed/new bookings, shorter slots, cancellation, stale sources, overlap/class conflicts, active/completed preservation, model selection, nested coach persistence and duplicate note submission. Browser QA must cover desktop/narrow layouts, session actions, help, settings, coach scroll and offline/draft states. Tests must not send messages into the live user's coach history.

At every coherent verified milestone inspect Git state, commit only relevant files, update this guidance, and push main to its configured remote. Preserve concurrent changes. Public code and private data have separate repository boundaries. Never include dependencies, generated preview/PDF files, local runtime state or large datasets in source commits.

## Current verification milestone

The academic-year reset is committed in the private repository. The backend passed 148 Python tests. The approved week-first frontend passed eight focused contract tests and synthetic browser interaction checks at desktop and 390-pixel width, including explicit timer actions and draft preservation. Deployment and rendered live HTTPS validation are recorded separately after completion; these source checks do not establish the live phone app.
