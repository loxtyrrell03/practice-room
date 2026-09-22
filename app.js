/* Practice Room · academic year, booking-led sessions */
"use strict";
const PRIVATE_ORIGIN = "https://lox-pc.tail89d19b.ts.net:10000";
const FILES = {
  state: "data/state.json",
  chat: "data/chat.json",
  journal: "data/journal.json",
  memory: "memory/MEMORY.md",
  spots: "data/spots.json",
  obs: "data/observations.json",
  weekly: "data/weekly-plan.json",
};
const $ = (id) => document.getElementById(id);
let cfg = { name: "you" },
  docs = {},
  sessionsDoc = { sessions: [], days: [], sync: { status: "unavailable" } },
  academic = {};
let currentView = "week",
  selectedDate = null,
  selectedSessionId = null,
  weekStart = null,
  pieceFilter = "all",
  pollTimer = null;
let coachQueue = { pending: 0, processing: 0, failed: 0, jobs: [] },
  coachActivity = {},
  coachModels = [];
let coachSelection = {
  provider: "anthropic",
  model: "claude-opus-5",
  effort: "medium",
};
const COACH_MODEL_STORAGE_KEY = "practice-room-coach-model-v2",
  expandedActivities = new Set(),
  openBlockIds = new Set();
let focusRef = null,
  focusActionError = "",
  busyAction = false,
  refreshing = false,
  offline = false,
  adjustSessionId = null,
  helpPinned = false,
  helpAnchor = null;
const CACHE_KEY = "practice-room-academic-cache-v1",
  DRAFT_KEY = "practice-room-note-drafts-v1",
  TIMER_KEY = "practice-room-booking-timer-v1";
const statusNames = {
  planned: "Planned",
  active: "In progress",
  paused: "Paused",
  done: "Completed",
  skipped: "Skipped",
  missed: "Missed",
};
const kindNames = {
  playing: "Playing",
  study: "Off-bench study",
  setup: "Preparation",
  break: "Rest",
  close: "Log & leave",
};
function esc(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
}
function readLocal(key, fallback) {
  try {
    return JSON.parse(localStorage.getItem(key)) ?? fallback;
  } catch {
    return fallback;
  }
}
function saveLocal(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {}
}
function state() {
  return docs[FILES.state]?.obj || { pieces: [], today: { blocks: [] } };
}
function chat() {
  return docs[FILES.chat]?.obj || { messages: [] };
}
function journal() {
  return docs[FILES.journal]?.obj || { entries: [] };
}
function ukDate(value = new Date()) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Europe/London",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(value));
}
function dateObj(date) {
  return new Date(date + "T12:00:00Z");
}
function dayOffset(date, days) {
  const d = dateObj(date);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}
function monday(date) {
  const d = dateObj(date);
  return dayOffset(date, -((d.getUTCDay() + 6) % 7));
}
function dateLabel(
  date,
  options = { weekday: "long", day: "numeric", month: "long" },
) {
  return dateObj(date).toLocaleDateString("en-GB", options);
}
function clockLabel(value) {
  const d = new Date(value);
  return Number.isNaN(d.getTime())
    ? "Time unknown"
    : d.toLocaleTimeString("en-GB", {
        timeZone: "Europe/London",
        hour: "2-digit",
        minute: "2-digit",
      });
}
function fmt(mins) {
  const m = Math.max(0, Math.round(Number(mins) || 0));
  return m >= 60
    ? `${Math.floor(m / 60)} h${m % 60 ? ` ${m % 60} min` : ""}`
    : `${m} min`;
}
function currentSession() {
  return sessionsDoc.sessions.find((s) => s.id === selectedSessionId);
}
function focusItems() {
  const session = sessionsDoc.sessions.find(
    (s) => s.id === focusRef?.sessionId,
  );
  return {
    session,
    block: session?.blocks.find((b) => b.id === focusRef?.blockId),
  };
}
function help(text, label = "About this") {
  return `<button type="button" class="help-button" data-help="${esc(text)}" aria-label="${esc(label)}">?</button>`;
}
function button(label, attrs = "", primary = false) {
  return `<button class="button ${primary ? "primary" : "secondary"}" ${attrs}>${label}</button>`;
}
async function api(path, body) {
  const r = await fetch(path, {
    cache: "no-store",
    ...(body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }),
  });
  let payload;
  try {
    payload = await r.json();
  } catch {
    payload = {};
  }
  if (!r.ok)
    throw new Error(
      payload.error ||
        payload.message ||
        `Request failed (${r.status}). Your saved work is kept.`,
    );
  return payload;
}
async function ghGet(path) {
  const r = await api(
    `/api/file?path=${encodeURIComponent(path)}&t=${Date.now()}`,
  );
  return { obj: path.endsWith(".json") ? JSON.parse(r.content) : r.content };
}
function banner(text, isErr = false) {
  const el = $("banner");
  el.hidden = !text;
  el.textContent = text || "";
  el.classList.toggle("error", isErr);
}
function syncText() {
  const sync = sessionsDoc.sync || {};
  const time = sync.observedAt
    ? new Date(sync.observedAt).toLocaleString("en-GB", {
        timeZone: "Europe/London",
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "not yet checked";
  return `${sync.status === "current" && !offline ? "ASIMUT · checked" : "Saved bookings · last check"} ${time}`;
}
function canStart(session, block) {
  const now = new Date();
  const valid =
    !offline &&
    sessionsDoc.sync?.status === "current" &&
    session?.bookingStatus === "confirmed" &&
    !session.needsAttention &&
    new Date(session.start) <= now &&
    new Date(session.end) > now;
  if (!valid) return false;
  if (block && typeof block.canStart === "boolean") return block.canStart;
  if (!block && session.blocks?.some((b) => typeof b.canStart === "boolean"))
    return session.blocks.some((b) => b.canStart || b.status === "active");
  return !block || ["planned", "paused"].includes(block.status);
}
function canAdjust(session) {
  return (
    !offline &&
    sessionsDoc.sync?.status === "current" &&
    session?.bookingStatus === "confirmed" &&
    new Date(session.end) > new Date()
  );
}
function noStartReason(session) {
  if (offline) return "Reconnect before starting a room session.";
  if (session?.bookingStatus !== "confirmed" || session.needsAttention)
    return (
      session?.notice ||
      "The booking changed or is no longer confirmed. Refresh before starting."
    );
  if (new Date(session.end) <= new Date())
    return "This booking has ended. Past plans are not completed practice.";
  if (new Date(session.start) > new Date())
    return `Your room booking begins at ${clockLabel(session.start)}. You can review its plan now.`;
  if (sessionsDoc.sync?.status !== "current")
    return "Refresh ASIMUT to confirm the room and time before starting.";
  return "The room is not available for a new block now. Check conflicts and your session limit.";
}
function saveCache() {
  saveLocal(CACHE_KEY, {
    docs,
    sessionsDoc,
    academic,
    at: new Date().toISOString(),
  });
}
async function loadAll() {
  const results = await Promise.allSettled([
    api("/api/sessions"),
    api("/api/year"),
    ghGet(FILES.state),
    ghGet(FILES.chat),
    ghGet(FILES.journal),
    ghGet(FILES.spots),
    ghGet(FILES.obs),
    api("/api/meta"),
    ghGet(FILES.weekly),
  ]);
  if (results[0].status === "rejected") throw results[0].reason;
  sessionsDoc = results[0].value;
  academic = results[1].status === "fulfilled" ? results[1].value : academic;
  [FILES.state, FILES.chat, FILES.journal, FILES.spots, FILES.obs].forEach(
    (file, i) => {
      if (results[i + 2].status === "fulfilled")
        docs[file] = results[i + 2].value;
    },
  );
  if (results[8].status === "fulfilled") docs[FILES.weekly] = results[8].value;
  if (results[7].status === "fulfilled") {
    const meta = results[7].value;
    cfg = { name: meta.name || "you" };
    coachQueue = meta.coachQueue || coachQueue;
    coachActivity = meta.coachActivity || coachActivity;
    configureCoachModels(meta);
  }
  offline = false;
  saveCache();
}
async function start() {
  banner("");
  try {
    await loadAll();
  } catch (e) {
    const cache = readLocal(CACHE_KEY, null);
    if (cache) {
      docs = cache.docs || {};
      sessionsDoc = cache.sessionsDoc || sessionsDoc;
      academic = cache.academic || {};
    }
    offline = true;
    banner(
      "Cannot reach Practice Room. Showing the last saved plan; your drafts are kept on this device.",
      true,
    );
  }
  selectedDate = selectedDate || sessionsDoc.today || ukDate();
  weekStart = weekStart || monday(selectedDate);
  restoreTimer();
  renderAll();
  if (coachQueue.pending || coachQueue.processing) startPolling();
}
async function refreshQuiet() {
  if (refreshing) return;
  refreshing = true;
  try {
    await loadAll();
    renderAll();
    if (!offline && $("banner").textContent.startsWith("Cannot reach"))
      banner("");
    if (
      !sessionsDoc.refreshing &&
      $("banner").textContent.startsWith("Checking ASIMUT")
    )
      banner(
        sessionsDoc.sync?.status === "current"
          ? "Bookings checked. Future sessions reflect the available room time."
          : sessionsDoc.sync?.message ||
              "Bookings could not be confirmed. Your previous plan is kept.",
        sessionsDoc.sync?.status !== "current",
      );
  } catch {
    offline = true;
    renderSync();
  } finally {
    refreshing = false;
    renderSync();
  }
}
async function refreshBookings() {
  const b = $("refreshBtn");
  b.disabled = true;
  b.textContent = "Checking ASIMUT…";
  try {
    await api("/api/sessions/refresh", {});
    banner("Checking ASIMUT. Your current work stays available.");
    await refreshQuiet();
  } catch (e) {
    banner(e.message, true);
  } finally {
    renderSync();
  }
}
function switchView(v) {
  if (!["today", "week", "programme", "coach", "journal"].includes(v)) return;
  currentView = v;
  document
    .querySelectorAll(".tab")
    .forEach((b) => b.classList.toggle("active", b.dataset.view === v));
  document
    .querySelectorAll(".view")
    .forEach((el) => (el.hidden = el.id !== `view-${v}`));
  document.body.classList.toggle("coach-open", v === "coach");
  window.scrollTo({ top: 0, behavior: "instant" });
  if (v === "coach") {
    $("coachDot").hidden = true;
    scrollThread();
  }
  if (v === "today") renderToday();
}
function renderAll() {
  const editingNote = document.activeElement?.closest(".note-control");
  if (!editingNote?.closest("#view-week")) renderWeek();
  if (!editingNote?.closest("#view-today")) renderToday();
  renderProgramme();
  renderCoach();
  renderJournal();
  renderYear();
  if (
    focusRef &&
    !["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)
  )
    renderFocus();
  tickTimer();
}
function renderSync() {
  $("syncStatus").textContent = syncText();
  $("syncStatus").className =
    "status " +
    (sessionsDoc.sync?.status === "current" && !offline ? "good" : "warning");
  $("refreshBtn").disabled = !!sessionsDoc.refreshing;
  $("refreshBtn").textContent = sessionsDoc.refreshing
    ? "Checking ASIMUT…"
    : "Refresh bookings";
}
function weekDates() {
  return Array.from({ length: 7 }, (_, i) => dayOffset(weekStart, i));
}
function renderWeek() {
  renderSync();
  const dates = weekDates(),
    days = (sessionsDoc.days || []).filter((d) => dates.includes(d.date));
  const sum = (k) => days.reduce((n, d) => n + (Number(d[k]) || 0), 0);
  const covered = dates.filter((d) =>
    (sessionsDoc.sync?.coveredDates || []).includes(d),
  );
  $("weekTitle").textContent =
    `${dateLabel(dates[0], { day: "numeric", month: "short" })} – ${dateLabel(dates[6], { day: "numeric", month: "long" })}`;
  $("weekSubtitle").textContent = "Time for the work that matters next.";
  $("weekMetrics").innerHTML =
    `<div><span>Booked in this plan ${help("Booked room time for the sessions shown this week. Earlier bookings without a saved session are not included.")}</span><strong>${fmt(sum("bookedMinutes"))}</strong></div><div><span>Planned playing</span><strong>${fmt(sum("playingMinutes"))}</strong></div><div><span>Also in your sessions</span><strong class="smaller">${fmt(sum("studyMinutes"))} study · ${fmt(sum("restMinutes"))} rest & preparation</strong></div>`;
  $("coverageNote").textContent =
    covered.length === 7
      ? "All 7 days checked"
      : `${covered.length} of 7 days checked · other dates are unknown`;
  $("dayStrip").innerHTML = dates
    .map((date) => {
      const d = days.find((x) => x.date === date);
      const known = covered.includes(date);
      return `<button class="day-tab ${date === selectedDate ? "active" : ""}" data-date="${date}" aria-pressed="${date === selectedDate}"><span>${dateLabel(date, { weekday: "short" })}</span><strong>${dateObj(date).getUTCDate()}</strong><small>${known ? fmt(d?.bookedMinutes || 0) : "Unknown"}</small>${date === ukDate() ? '<i aria-label="Today"></i>' : ""}</button>`;
    })
    .join("");
  const rows = sessionsDoc.sessions.filter((s) => s.date === selectedDate);
  if (!rows.some((s) => s.id === selectedSessionId)) {
    const active = rows.find((s) =>
      s.blocks?.some((b) => ["active", "paused"].includes(b.status)),
    );
    selectedSessionId =
      (active || rows.find((s) => new Date(s.end) > new Date()) || rows[0])
        ?.id || null;
  }
  $("agendaDate").textContent = dateLabel(selectedDate, {
    weekday: "long",
    day: "numeric",
    month: "short",
  });
  $("sessionList").innerHTML = rows.length
    ? rows.map(sessionRow).join("")
    : emptySessions(selectedDate);
  const session = currentSession();
  $("sessionDetail").innerHTML = session
    ? sessionDetail(session)
    : priorityPanel(selectedDate);
  wireNotes($("sessionDetail"));
}
function sessionRow(s) {
  const done = s.blocks?.filter((b) => b.done).length || 0;
  return `<button class="session-row ${s.id === selectedSessionId ? "selected" : ""}" data-session="${esc(s.id)}" aria-pressed="${s.id === selectedSessionId}"><span class="session-time">${clockLabel(s.start)}–${clockLabel(s.end)}</span><span class="session-room">${esc(s.room)}</span><span class="session-summary">${fmt(s.bookedMinutes)} booked${s.bookingStatus !== "confirmed" ? ` · ${esc(s.bookingStatus)}` : done ? ` · ${done} completed` : new Date(s.end) <= new Date() ? " · ended" : ""}</span><span class="row-arrow" aria-hidden="true">→</span></button>`;
}
function emptySessions(date) {
  const covered = sessionsDoc.sync?.coveredDates?.includes(date);
  return `<div class="empty-panel"><h3>${covered ? "No rooms booked" : "Bookings not yet known"}</h3><p>${covered ? "Keep the day useful with score study, or make a room booking in Booker." : "Refresh ASIMUT to see whether room time is available for this date."}</p><a class="text-button" href="https://lox-pc.tail89d19b.ts.net:10443/" target="_blank" rel="noopener">Open Booker ↗</a></div>`;
}
function priorityPanel(date) {
  const pieces = (state().pieces || []).slice(0, 3);
  return `<div class="empty-panel priority-panel"><div class="eyebrow">OFF-BENCH OPTIONS</div><h2>A useful next step</h2><p>These are unscheduled study ideas, not room reservations.</p>${pieces.map((p) => `<div class="priority-row"><h3>${esc(p.short || p.title)}</h3><p>${esc(p.planning?.focus || p.statusPoints?.find((x) => x.lead === "Next checkpoint")?.text || "Map one section in the score and note the question to test at the piano.")}</p></div>`).join("")}${button("Open repertoire →", 'data-switch="programme"')}</div>`;
}
function sessionTotals(s) {
  const blocks = (s.blocks || []).filter(
    (b) => !["missed", "skipped"].includes(b.status),
  );
  const sum = (k) =>
    blocks
      .filter((b) => k.includes(b.kind))
      .reduce(
        (n, b) =>
          n + (Number(b.done ? (b.actualMinutes ?? b.mins) : b.mins) || 0),
        0,
      );
  return {
    playing: sum(["playing"]),
    study: sum(["study"]),
    rest: sum(["break"]),
    prep: sum(["setup", "close"]),
  };
}
function sessionDetail(s) {
  const totals = sessionTotals(s);
  const next =
    s.blocks.find((b) => ["active", "paused"].includes(b.status)) ||
    s.blocks.find((b) => !b.done && !["skipped", "missed"].includes(b.status));
  const valid = canStart(s);
  return `<div class="detail-heading"><div><div class="eyebrow">${esc(s.bookingStatus === "confirmed" ? "BOOKED SESSION" : s.bookingStatus)}</div><h2>${clockLabel(s.start)}–${clockLabel(s.end)}</h2><p>${esc(s.room)} · ${fmt(s.bookedMinutes)} booked</p></div>${help("Plans adapt automatically when confirmed room availability changes. Started and completed blocks, notes and tests are preserved.", "How sessions adapt")}</div>${s.notice ? `<div class="inline-notice">${esc(s.notice)}</div>` : ""}${!valid ? `<p class="inline-notice warning">${esc(noStartReason(s))}</p>` : ""}<div class="session-totals"><span>${fmt(totals.playing)} playing</span><span>${fmt(totals.study)} study</span><span>${fmt(totals.rest)} rest</span><span>${fmt(totals.prep)} preparation</span></div><div class="session-blocks">${s.blocks.map((b) => blockRow(s, b)).join("") || '<p class="muted">No blocks allocated in this window.</p>'}</div><div class="session-actions">${next ? button(["active", "paused"].includes(next.status) ? "Continue session →" : valid ? "Start session →" : "View session →", `data-focus-session="${esc(s.id)}" data-focus-block="${esc(next.id)}"`, true) : '<span class="status good">All blocks accounted for</span>'}${button("Shorten session", `data-adjust="${esc(s.id)}" ${!canAdjust(s) ? "disabled" : ""}`)}</div><p class="small muted">The timetable adapts to your bookings. A completed block records practice, not readiness.</p>`;
}
function blockRow(s, b) {
  const terminal = b.done || ["skipped", "missed"].includes(b.status);
  return `<details data-block-id="${esc(b.id)}" class="schedule-block ${esc(b.kind)} ${terminal ? "complete" : ""}" ${openBlockIds.has(b.id) || ["active", "paused"].includes(b.status) ? "open" : ""}><summary><time>${clockLabel(b.start)}</time><span><strong>${esc(b.title)}</strong><small>${esc(kindNames[b.kind] || b.kind)} · ${esc(statusNames[b.status] || "Planned")}</small></span><span class="block-minutes">${b.mins} min</span></summary><div class="block-detail">${instructions(b)}<div class="block-tools">${!terminal ? button(["active", "paused"].includes(b.status) ? "Continue" : "Open block", `data-focus-session="${esc(s.id)}" data-focus-block="${esc(b.id)}"`) : ""}${help(b.why || "This block fits within your booked time.", "Why this block?")}</div>${["playing", "study"].includes(b.kind) ? noteHTML(s, b) : ""}</div></details>`;
}
function instructions(b) {
  const steps = (b.steps || []).filter((s) => s.text || typeof s === "string");
  return steps.length
    ? `<ol class="instruction-list">${steps.map((step) => `<li>${step.lead ? `<strong>${esc(step.lead)}</strong> ` : ""}${esc(step.text || step)}</li>`).join("")}</ol>`
    : `<p>${esc(b.detail || "Use this time for the named task, then record what changed.")}</p>`;
}
function renderToday() {
  const date = sessionsDoc.today || ukDate();
  $("todayDate").textContent = dateLabel(date).toUpperCase();
  const rows = sessionsDoc.sessions.filter((s) => s.date === date),
    next =
      rows.find((s) =>
        s.blocks?.some((b) => ["active", "paused"].includes(b.status)),
      ) || rows.find((s) => new Date(s.end) > new Date());
  const day = sessionsDoc.days?.find((d) => d.date === date);
  const target = sessionsDoc.dailyTargetMinutes || { min: 240, max: 360 };
  $("todayContent").innerHTML =
    `<div class="today-capacity"><strong>${fmt(day?.bookedMinutes || 0)} booked</strong><span>${fmt(day?.playingMinutes || 0)} planned playing · ${fmt(day?.studyMinutes || 0)} study</span><span>Daily aspiration ${fmt(target.min)}–${fmt(target.max)} ${help("The aspiration includes focused playing and study. Rest and preparation are separate. Missing booked time is not a debt to repay.")}</span></div>${next ? `<div class="session-detail today-session">${sessionDetail(next)}</div>` : (rows.length ? '<div class="empty-panel"><h2>Today’s room bookings have ended.</h2><p>Your recorded practice stays in the week view. A missed block is not marked complete.</p></div>' : emptySessions(date)) + priorityPanel(date)}`;
  wireNotes($("todayContent"));
}
function drafts() {
  return readLocal(DRAFT_KEY, {});
}
function noteHTML(s, b) {
  const inputId = `note-${crypto.randomUUID()}`;
  const draft = drafts()[b.id] || {};
  const notes = (docs[FILES.obs]?.obj?.obs || [])
    .filter((o) => o.blockId === b.id)
    .slice(-3);
  return `<div class="note-control" data-note-block="${esc(b.id)}" data-note-session="${esc(s.id)}"><label for="${inputId}">What happened?</label><div class="notebar"><input id="${inputId}" type="text" maxlength="300" placeholder="Passage, result, or next question…" value="${esc(draft.text || "")}"><button class="button secondary note-save">Save note</button></div><div class="note-result" role="status">${draft.text ? "Draft kept on this device" : ""}</div><div class="obslist">${notes.map((o) => `<div class="obsrow"><span>${esc(o.text)}</span><small>${esc({ pending: "Saved · awaiting coach", processing: "Saved · processing", processed: "Saved · reviewed", failed: "Saved · needs attention" }[o.status] || "Saved")}</small></div>`).join("")}</div>`;
}
function wireNotes(root) {
  root.querySelectorAll(".note-control").forEach((row) => {
    const input = row.querySelector("input"),
      save = row.querySelector(".note-save"),
      id = row.dataset.noteBlock;
    input.addEventListener("input", () => {
      const all = drafts(),
        prior = all[id] || {};
      all[id] = {
        text: input.value,
        clientId:
          prior.text === input.value ? prior.clientId : crypto.randomUUID(),
      };
      saveLocal(DRAFT_KEY, all);
      row.querySelector(".note-result").textContent =
        "Draft kept on this device";
    });
    const submit = async () => {
      const text = input.value.trim();
      if (!text) return;
      save.disabled = true;
      const all = drafts();
      const draft =
        all[id]?.text === input.value
          ? all[id]
          : { text: input.value, clientId: crypto.randomUUID() };
      all[id] = draft;
      saveLocal(DRAFT_KEY, all);
      const session = sessionsDoc.sessions.find(
          (s) => s.id === row.dataset.noteSession,
        ),
        block = session?.blocks.find((b) => b.id === id);
      try {
        if (!block) throw new Error("This block changed. Your draft is kept.");
        const start = state().startDate || "2026-09-22";
        const day =
          Math.floor((dateObj(session.date) - dateObj(start)) / 86400000) + 1;
        const result = await api("/api/observations", {
          clientId: draft.clientId,
          day,
          date: session.date,
          sessionId: session.id,
          blockId: block.id,
          block: block.title,
          pieceId: block.pieceId || null,
          movementId: block.movementId || null,
          movement: block.movement || null,
          text,
        });
        const file =
          docs[FILES.obs] || (docs[FILES.obs] = { obj: { obs: [] } });
        const list = file.obj.obs || (file.obj.obs = []);
        if (
          result.observation &&
          !list.some((o) => o.id === result.observation.id)
        )
          list.push(result.observation);
        const updated = drafts();
        const unchanged =
          updated[id]?.clientId === draft.clientId &&
          input.value === draft.text;
        if (unchanged) {
          delete updated[id];
          saveLocal(DRAFT_KEY, updated);
          input.value = "";
        }
        row.querySelector(".note-result").textContent = unchanged
          ? "Saved · ready for your coach"
          : "Previous note saved · your new draft is kept";
        saveCache();
      } catch (e) {
        row.querySelector(".note-result").textContent =
          `${e.message} Draft kept for retry.`;
      } finally {
        save.disabled = false;
      }
    };
    save.addEventListener("click", submit);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") submit();
    });
  });
}
function openFocus(sessionId, blockId) {
  focusActionError = "";
  focusRef = { sessionId, blockId };
  renderFocus();
  if (!$("focusOverlay").open) $("focusOverlay").showModal();
  document.body.classList.add("focusing");
}
function closeFocus() {
  $("focusOverlay").close();
  document.body.classList.remove("focusing");
  focusRef = null;
}
function renderFocus() {
  const { session: s, block: b } = focusItems();
  if (!s || !b) {
    if ($("focusOverlay").open) closeFocus();
    return;
  }
  const active = b.status === "active",
    paused = b.status === "paused",
    terminal = b.done || ["skipped", "missed"].includes(b.status),
    valid = active ? canStart(s) : canStart(s, b);
  const index = s.blocks.indexOf(b);
  $("focusContent").innerHTML =
    `<div class="focus-top"><button class="text-button" data-focus-close>← Session</button><span>${index + 1} of ${s.blocks.length} blocks</span><button class="icon-button" data-focus-close aria-label="Close focus">×</button></div><div class="focus-progress"><i style="width:${(100 * (index + 1)) / s.blocks.length}%"></i></div><div class="eyebrow">${esc(kindNames[b.kind] || "PRACTICE")} · ${b.mins} MIN PLANNED</div><h1>${esc(b.title)}</h1><p class="muted">${esc(s.room)} · room booking ends ${clockLabel(s.end)}</p>${!valid ? `<div class="inline-notice warning">${esc(noStartReason(s))} Your work and notes remain available.</div>` : ""}${instructions(b)}<div class="timer-area"><output id="focusTimer" aria-label="Time remaining">${b.mins}:00</output><span id="timerStatus" class="muted">${terminal ? statusNames[b.status] : active ? "Timer running" : paused ? "Paused" : "Ready when you are"}</span></div><div class="focus-actions">${!terminal ? button(active ? "Pause" : paused ? "Resume" : "Start block", `data-action="${active ? "pause" : "start"}" ${(!valid && !active) || busyAction ? "disabled" : ""}`, !active) : ""}${!terminal ? button("Mark complete & next →", `data-action="complete" ${busyAction || !["active", "paused"].includes(b.status) ? "disabled" : ""}`, true) : button("Next block →", "data-focus-next", true)}${!terminal ? button("Skip this block", 'data-action="skip"') : ""}</div>${["playing", "study"].includes(b.kind) ? noteHTML(s, b) : ""}<p class="focus-context">${help(b.why || "The duration fits this room booking.", "Why this task?")} The timer never marks a block complete for you.</p><div id="focusResult" class="inline-status" role="status">${esc(focusActionError)}</div>`;
  wireNotes($("focusContent"));
  tickTimer();
}
function restoreTimer() {
  // Active/paused timing lives on the server, so another device can resume it.
  localStorage.removeItem(TIMER_KEY);
}
function tickTimer() {
  if (!focusRef) return;
  const { block: b } = focusItems();
  if (!b) return;
  let remaining = Math.max(
    0,
    Number(b.mins) * 60 - Number(b.elapsedSeconds || 0),
  );
  if (b.status === "active")
    remaining = Math.max(0, (new Date(b.end) - Date.now()) / 1000);
  const seconds = Math.ceil(remaining);
  if ($("focusTimer"))
    $("focusTimer").textContent =
      `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  if ($("timerStatus"))
    $("timerStatus").textContent = b.done
      ? "Completed"
      : b.status === "skipped"
        ? "Skipped"
        : b.status === "active" && !seconds
          ? "Time reached · finish when ready"
          : b.status === "paused"
            ? "Paused"
            : b.status === "active"
              ? "In progress"
              : "Ready when you are";
}
async function sessionAction(action) {
  if (busyAction) return;
  const { session: s, block: b } = focusItems();
  if (!s || !b) return;
  busyAction = true;
  focusActionError = "";
  try {
    const result = await api("/api/sessions/action", {
      sessionId: s.id,
      blockId: b.id,
      action,
    });
    if (Array.isArray(result.sessions)) {
      sessionsDoc = result;
      saveCache();
    } else {
      await loadAll();
    }
    if (action === "complete" || action === "skip") nextFocus();
    else renderFocus();
    renderWeek();
    renderToday();
  } catch (e) {
    focusActionError = e.message;
    if ($("focusResult")) $("focusResult").textContent = e.message;
    else banner(e.message, true);
  } finally {
    busyAction = false;
    if (focusRef) renderFocus();
  }
}
function nextFocus() {
  const { session: s, block: b } = focusItems();
  if (!s) return;
  const next = s.blocks
    .slice(s.blocks.indexOf(b) + 1)
    .find((b) => !b.done && !["skipped", "missed"].includes(b.status));
  if (next) {
    focusRef.blockId = next.id;
    renderFocus();
  } else {
    $("focusContent").innerHTML =
      `<div class="session-finished"><div class="eyebrow">SESSION RECORDED</div><h1>Leave a useful next step.</h1><p>Your completed blocks and notes are saved. Unfinished work stays unfinished.</p>${button("Debrief with coach →", "data-session-debrief", true)}${button("Back to the week", "data-focus-close")}<p class="muted small">Finishing practice does not cancel your room booking.</p></div>`;
    focusRef = null;
  }
}
function deadlines() {
  return Array.isArray(academic.deadlines)
    ? academic.deadlines
    : Array.isArray(academic.academicYear?.deadlines)
      ? academic.academicYear.deadlines
      : [];
}
function deadlineName(d) {
  return d.title || d.label || d.name || d.id || "Performance";
}
function deadlineMonth(d) {
  return d.month || d.targetMonth || d.date?.slice(0, 7) || "";
}
function monthLabel(month) {
  return /^\d{4}-\d{2}$/.test(month)
    ? dateLabel(month + "-01", { month: "long", year: "numeric" })
    : "Date window to confirm";
}
function pieceDeadlineLabel(piece) {
  const matched = deadlines().filter((d) =>
    (piece.deadlineIds || []).includes(d.id),
  );
  if (matched.length)
    return matched.map((d) => monthLabel(deadlineMonth(d))).join(" + ");
  return piece.id === "beethoven-op109"
    ? "FEBRUARY + MAY"
    : monthLabel(piece.planning?.deadlineMonth || "");
}
function renderProgramme() {
  const ds = deadlines();
  $("deadlineOverview").innerHTML = (
    ds.length
      ? ds
      : [
          { title: "February assessments", month: "2027-02" },
          { title: "Final recital", month: "2027-05" },
        ]
  )
    .map(
      (d) =>
        `<div><span class="eyebrow">${esc(deadlineName(d))}</span><strong>${d.date ? esc(dateLabel(d.date)) : esc(monthLabel(deadlineMonth(d)))}</strong><small>${d.date ? "Confirmed date" : "Exact date to confirm"}</small></div>`,
    )
    .join("");
  const list = (state().pieces || []).filter(
    (p) =>
      pieceFilter === "all" ||
      p.planning?.deadlineMonth === pieceFilter ||
      (p.movements || []).some(
        (m) => m.planning?.deadlineMonth === pieceFilter,
      ) ||
      p.id === "beethoven-op109",
  );
  $("pieces").innerHTML =
    list
      .map((p) => {
        const notes = (p.statusPoints || [])
          .map(
            (point) =>
              `<li><strong>${esc(point.lead)}</strong> ${esc(point.text)}</li>`,
          )
          .join("");
        const spots = (docs[FILES.spots]?.obj?.spots || []).filter(
          (sp) => sp.piece === p.id && sp.status !== "fixed",
        );
        const evidence = p.lastCold
          ? `${p.lastCold.result === "pass" ? "Held up" : p.lastCold.result === "fail" ? "Needs another approach" : p.lastCold.result} · ${p.lastCold.date}`
          : "Not assessed yet";
        return `<article class="piece"><div class="piece-heading"><div><div class="eyebrow">${esc(pieceDeadlineLabel(p))}</div><h2>${esc(p.title)}</h2></div><span class="status">${p.id === "lecture-recital" ? "Music undecided" : "Learning in progress"}</span></div>${notes ? `<ul class="instruction-list piece-points">${notes}</ul>` : `<p>${esc(p.note || p.planning?.focus || "Choose a first section and record a baseline.")}</p>`}<details><summary>Learning route & evidence</summary><div class="piece-detail"><p><strong>Last recall check:</strong> ${esc(evidence)} ${help("Record what happened, when, and with which score/tempo conditions. An unassessed piece has missing evidence, not zero ability.")}</p>${p.movements?.map((m) => `<section><h3>${esc(m.title)}</h3><p>${esc(m.planning?.focus || "Map the sections; work on the next unfinished passage.")}</p>${m.planning?.passTest ? `<p class="muted"><strong>Next check:</strong> ${esc(m.planning.passTest)}</p>` : ""}</section>`).join("") || ""}${spots.length ? `<h3>Open observations</h3>${spots.map((sp) => `<p>${esc(sp.movement || "")} ${esc(sp.bars ? `Passage ${sp.bars}: ` : "")}${esc(sp.issue)} <small class="muted">${esc(sp.logged || "")}</small></p>`).join("")}` : ""}<button class="text-button" data-piece-coach="${esc(p.title)}">Discuss the next step with coach →</button></div></details></article>`;
      })
      .join("") ||
    '<div class="empty-panel"><h2>No works in this group yet</h2><p>Your saved repertoire will appear here after the academic-year setup is complete.</p></div>';
}
function renderYear() {
  const phases =
    docs[FILES.weekly]?.obj?.phases ||
    academic.chronology ||
    academic.phases ||
    [];
  $("yearRoute").innerHTML = phases.length
    ? phases
        .map(
          (p) =>
            `<section class="route-phase"><div class="eyebrow">${esc(p.window || p.period || p.month || p.label || "")}</div><h3>${esc(p.title || "Next stage")}</h3><p>${esc(p.headline || p.description || "")}</p>${Array.isArray(p.goals) ? `<ul>${p.goals.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>` : ""}${p.gate?.criteria ? `<div class="route-gate"><strong>${esc(p.gate.label || "Next checkpoint")}</strong><ul>${p.gate.criteria.map((c) => `<li>${esc(c)}</li>`).join("")}</ul></div>` : ""}</section>`,
        )
        .join("")
    : '<p class="muted">Your year chronology is loading. February and May remain planning windows until exact dates are confirmed.</p>';
}
function renderJournal() {
  const q = ($("journalSearch").value || "").toLowerCase();
  const entries = (journal().entries || [])
    .filter((e) => !state().startDate || e.date >= state().startDate)
    .slice()
    .reverse()
    .filter((e) => `${e.title} ${e.body} ${e.date}`.toLowerCase().includes(q));
  $("entries").innerHTML = entries.length
    ? entries
        .map(
          (e) =>
            `<article class="entry"><div class="eyebrow">${esc(e.date || "")}</div><h2>${esc(e.title || "Practice review")}</h2><div class="e-body">${mdLite(e.body || "")}</div></article>`,
        )
        .join("")
    : `<div class="empty-panel"><h2>${q ? "No matching entries" : "Your first useful review starts here"}</h2><p>${q ? "Try a piece name or another date." : "After a session, tell the coach what held up and what needs another approach."}</p>${button("Debrief with coach →", "data-journal-debrief", true)}</div>`;
}
function messageDate(message) {
  if (!message.ts) return null;
  const parsed = new Date(message.ts);
  return Number.isNaN(parsed.getTime()) ? null : ukDate(parsed);
}
function conversationGroups(messages, startDate) {
  const current = [],
    previous = [];
  messages.forEach((message) => {
    const date = messageDate(message);
    (date && (!startDate || date >= startDate) ? current : previous).push(
      message,
    );
  });
  return { current, previous };
}
function renderCoach(forceBottom = false) {
  const t = $("thread"),
    archiveOpen = t.querySelector(".conversation-archive")?.open || false,
    nearBottom = t.scrollHeight - t.scrollTop - t.clientHeight < 90,
    scroll = t.scrollTop,
    activityScroll = new Map(
      [...t.querySelectorAll(".coach-activity")].map((el, i) => [
        i,
        el.scrollTop,
      ]),
    );
  t.innerHTML = "";
  const { current, previous } = conversationGroups(
    chat().messages || [],
    academic.startDate || state().startDate,
  );
  if (previous.length) {
    const archive = document.createElement("details");
    archive.className = "conversation-archive";
    archive.open = archiveOpen;
    const summary = document.createElement("summary");
    summary.textContent = `Previous programme conversations · ${previous.length} messages`;
    const note = document.createElement("p");
    note.className = "archive-context";
    note.textContent =
      "Earlier conversations, preserved for reference. They do not describe your current repertoire or practice plan.";
    const history = document.createElement("div");
    history.className = "archive-messages";
    previous.forEach((message) => history.appendChild(bubble(message)));
    archive.append(summary, note, history);
    t.appendChild(archive);
  }
  if (!current.length) {
    const empty = document.createElement("div");
    empty.className = "empty-panel current-year-coach";
    empty.innerHTML =
      '<div class="eyebrow">CURRENT ACADEMIC YEAR</div><h2>Turn today’s practice into the next useful step.</h2><p>Tell your coach what held up, where it changed, and what you want to work on next.</p>';
    t.appendChild(empty);
  }
  current.forEach((m) => t.appendChild(bubble(m)));
  t.querySelectorAll(".coach-activity").forEach((el, i) => {
    el.scrollTop = activityScroll.get(i) || 0;
  });
  if (forceBottom || nearBottom) t.scrollTop = t.scrollHeight;
  else t.scrollTop = scroll;
}
function openSettings() {
  const target = sessionsDoc.dailyTargetMinutes ||
    academic.dailyTargetMinutes || { min: 240, max: 360 };
  $("targetMin").value = target.min / 60;
  $("targetMax").value = target.max / 60;
  $("settingsSync").textContent = syncText();
  $("deadlineFields").innerHTML =
    deadlines()
      .map(
        (d) =>
          `<label class="field">${esc(deadlineName(d))}<input type="date" data-deadline="${esc(d.id)}" value="${esc(d.date || "")}"></label>`,
      )
      .join("") ||
    '<p class="muted">Deadline settings will appear when your academic year is ready.</p>';
  $("settingsResult").textContent = "";
  $("settingsDialog").showModal();
}
async function saveSettings(e) {
  e.preventDefault();
  const min = Number($("targetMin").value) * 60,
    max = Number($("targetMax").value) * 60;
  if (min > max) {
    $("settingsResult").textContent =
      "The maximum must be at least the minimum.";
    return;
  }
  $("saveSettings").disabled = true;
  try {
    await api("/api/preferences", {
      dailyTargetMinutes: { min, max },
      deadlines: [...document.querySelectorAll("[data-deadline]")].map(
        (el) => ({ id: el.dataset.deadline, date: el.value || null }),
      ),
    });
    await refreshQuiet();
    $("settingsResult").textContent =
      "Saved. Future sessions will adapt to these priorities.";
  } catch (error) {
    $("settingsResult").textContent = error.message;
  } finally {
    $("saveSettings").disabled = false;
  }
}
function showHelp(anchor, pinned = false) {
  helpAnchor = anchor;
  helpPinned = pinned;
  const box = $("helpPopover");
  (anchor.closest("dialog[open]") || document.body).appendChild(box);
  $("helpText").textContent = anchor.dataset.help;
  box.hidden = false;
  anchor.setAttribute("aria-describedby", "helpPopover");
  const rect = anchor.getBoundingClientRect(),
    width = Math.min(340, window.innerWidth - 24);
  box.style.width = width + "px";
  box.style.left =
    Math.max(12, Math.min(rect.left, window.innerWidth - width - 12)) + "px";
  const height = box.getBoundingClientRect().height;
  box.style.top =
    (rect.bottom + height + 12 < window.innerHeight
      ? rect.bottom + 8
      : Math.max(12, rect.top - height - 8)) + "px";
}
function hideHelp() {
  if (helpAnchor) helpAnchor.removeAttribute("aria-describedby");
  $("helpPopover").hidden = true;
  document.body.appendChild($("helpPopover"));
  helpAnchor = null;
  helpPinned = false;
}
async function openArchive() {
  const entries = (journal().entries || [])
    .filter((e) => state().startDate && e.date < state().startDate)
    .slice()
    .reverse();
  $("archiveContent").innerHTML =
    `<p class="muted">Journal entries before the current academic year. Their original observations are preserved.</p>${entries.map((e) => `<article class="entry"><div class="eyebrow">${esc(e.date)}</div><h3>${esc(e.title || "Practice review")}</h3><div class="e-body">${mdLite(e.body || "")}</div></article>`).join("") || "<p>No earlier journal entries are present in this snapshot.</p>"}`;
  $("archiveDialog").showModal();
}
function wireChrome() {
  document.addEventListener(
    "toggle",
    (event) => {
      const detail = event.target;
      if (!detail.matches?.(".schedule-block[data-block-id]")) return;
      if (detail.open) openBlockIds.add(detail.dataset.blockId);
      else openBlockIds.delete(detail.dataset.blockId);
    },
    true,
  );
  document
    .querySelectorAll(".tab")
    .forEach((b) =>
      b.addEventListener("click", () => switchView(b.dataset.view)),
    );
  document.querySelector(".brand")?.addEventListener?.("click", (e) => {
    e.preventDefault();
    switchView("week");
  });
  $("refreshBtn").addEventListener("click", refreshBookings);
  $("settingsBtn").addEventListener("click", openSettings);
  $("settingsForm").addEventListener("submit", saveSettings);
  $("journalSearch").addEventListener("input", renderJournal);
  $("archiveBtn").addEventListener("click", openArchive);
  $("send").addEventListener("click", sendMessage);
  wireModelPicker();
  $("memBtn").addEventListener("click", toggleMemory);
  $("input").value =
    localStorage.getItem("practice-room-chat-draft") ||
    readLocal("practice-room-chat-outbox", {})?.text ||
    "";
  $("input").addEventListener("input", () =>
    localStorage.setItem("practice-room-chat-draft", $("input").value),
  );
  $("input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) sendMessage();
  });
  document.querySelectorAll(".chip.q").forEach((c) =>
    c.addEventListener("click", () => {
      $("input").value = c.dataset.q;
      $("input").focus();
    }),
  );
  $("previousWeek").addEventListener("click", () => {
    weekStart = dayOffset(weekStart, -7);
    selectedDate = weekStart;
    renderWeek();
  });
  $("nextWeek").addEventListener("click", () => {
    weekStart = dayOffset(weekStart, 7);
    selectedDate = weekStart;
    renderWeek();
  });
  $("thisWeek").addEventListener("click", () => {
    selectedDate = sessionsDoc.today || ukDate();
    weekStart = monday(selectedDate);
    renderWeek();
  });
  $("closeHelp").addEventListener("click", hideHelp);
  $("focusOverlay").addEventListener("cancel", () => {
    focusRef = null;
    document.body.classList.remove("focusing");
  });
  $("adjustForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn =
      e.target.querySelector('button[type="submit"]') ||
      e.target.querySelector(".primary");
    btn.disabled = true;
    try {
      await api("/api/sessions/adjust", {
        sessionId: adjustSessionId,
        availableMinutes: Number($("availableMinutes").value),
      });
      await refreshQuiet();
      $("adjustDialog").close();
      banner("Session adapted. Your room reservation is unchanged.");
    } catch (error) {
      $("adjustResult").textContent = error.message;
    } finally {
      btn.disabled = false;
    }
  });
  document.addEventListener("click", (e) => {
    const t = e.target.closest("button,a");
    if (!t) return;
    if (t.dataset.switch) switchView(t.dataset.switch);
    if (t.dataset.date) {
      selectedDate = t.dataset.date;
      renderWeek();
    }
    if (t.dataset.session) {
      selectedSessionId = t.dataset.session;
      renderWeek();
      if (window.innerWidth < 760)
        $("sessionDetail").scrollIntoView({
          behavior: "smooth",
          block: "start",
        });
    }
    if (t.dataset.focusSession)
      openFocus(t.dataset.focusSession, t.dataset.focusBlock);
    if (t.hasAttribute("data-focus-close")) closeFocus();
    if (t.dataset.action) sessionAction(t.dataset.action);
    if (t.hasAttribute("data-focus-next")) nextFocus();
    if (t.dataset.adjust) {
      adjustSessionId = t.dataset.adjust;
      const s = sessionsDoc.sessions.find((s) => s.id === adjustSessionId);
      $("availableMinutes").max = s.bookedMinutes;
      $("availableMinutes").value =
        s.sessionLimitMinutes ??
        academic.sessionLimits?.[s.id] ??
        s.bookedMinutes;
      $("adjustResult").textContent = "";
      $("adjustDialog").showModal();
    }
    if (t.dataset.close) $(t.dataset.close).close();
    if (t.dataset.filter) {
      pieceFilter = t.dataset.filter;
      document
        .querySelectorAll("[data-filter]")
        .forEach((b) => b.classList.toggle("active", b === t));
      renderProgramme();
    }
    if (t.dataset.pieceCoach) {
      switchView("coach");
      $("input").value =
        `Help me plan the next step for ${t.dataset.pieceCoach}. `;
      $("input").focus();
    }
    if (
      t.hasAttribute("data-journal-debrief") ||
      t.hasAttribute("data-session-debrief")
    ) {
      if ($("focusOverlay").open) closeFocus();
      switchView("coach");
      $("input").value = "Debrief: ";
      $("input").focus();
    }
    if (t.dataset.help) {
      showHelp(t, true);
      e.stopPropagation();
    } else if (!e.target.closest(".help-popover")) hideHelp();
  });
  document.addEventListener("pointerover", (e) => {
    const t = e.target.closest("[data-help]");
    if (t && !helpPinned) showHelp(t);
  });
  document.addEventListener("pointerout", (e) => {
    if (
      e.target.closest("[data-help]") &&
      !helpPinned &&
      !e.relatedTarget?.closest(".help-popover")
    )
      hideHelp();
  });
  document.addEventListener("focusin", (e) => {
    if (e.target.matches("[data-help]")) showHelp(e.target);
  });
  document.addEventListener("focusout", (e) => {
    if (e.target.matches("[data-help]") && !helpPinned) hideHelp();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") hideHelp();
  });
  window.addEventListener("resize", () => {
    if (helpAnchor) showHelp(helpAnchor, helpPinned);
  });
  window.addEventListener("focus", () => refreshQuiet());
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      tickTimer();
      refreshQuiet();
    }
  });
}
window.addEventListener("DOMContentLoaded", async () => {
  if (location.hostname.endsWith(".github.io")) {
    location.replace(PRIVATE_ORIGIN + "/");
    return;
  }
  wireChrome();
  await start();
  switchView("week");
  setInterval(() => {
    if (document.visibilityState === "visible") refreshQuiet();
  }, 25000);
  setInterval(tickTimer, 500);
});

function configureCoachModels(meta) {
  coachModels = Array.isArray(meta.coachModels) ? meta.coachModels : [];
  const fallback = meta.defaultCoachSelection || coachSelection;
  let saved = null;
  try {
    saved = JSON.parse(localStorage.getItem(COACH_MODEL_STORAGE_KEY));
  } catch {}
  try {
    localStorage.removeItem("practice-room-coach-model");
  } catch {}
  coachSelection =
    validCoachSelection(saved) ||
    validCoachSelection(fallback) ||
    coachSelection;
  renderModelPicker();
}

function validCoachSelection(value) {
  if (!value || typeof value !== "object") return null;
  const model = coachModels.find(
    (item) => item.provider === value.provider && item.id === value.model,
  );
  if (!model) return null;
  const effort = model.efforts.includes(value.effort)
    ? value.effort
    : model.defaultEffort;
  return { provider: model.provider, model: model.id, effort };
}

function selectedCoachModel(selection = coachSelection) {
  return (
    coachModels.find(
      (item) =>
        item.provider === selection.provider && item.id === selection.model,
    ) || null
  );
}

function modelMark(provider) {
  return provider === "anthropic" ? "CL" : "GPT";
}

function effortLabel(effort) {
  return effort === "xhigh"
    ? "X-high"
    : effort.charAt(0).toUpperCase() + effort.slice(1);
}

function formatCoachSelection(selection) {
  const model = selectedCoachModel(selection || {});
  if (model) return `${model.label} · ${effortLabel(selection.effort)}`;
  return selection && selection.model
    ? `${selection.model} · ${selection.effort || "default"}`
    : "coach model";
}

function wireModelPicker() {
  $("modelTrigger").addEventListener("click", (event) => {
    event.stopPropagation();
    setModelMenu($("modelMenu").hidden);
  });
  $("modelMenuClose").addEventListener("click", () => setModelMenu(false));
  document.addEventListener("pointerdown", (event) => {
    if (!$("modelMenu").hidden && !$("composer").contains(event.target))
      setModelMenu(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("modelMenu").hidden) setModelMenu(false);
    const focusMenu = document.getElementById("focusModelMenu");
    if (event.key === "Escape" && focusMenu && !focusMenu.hidden) {
      focusMenu.hidden = true;
      document
        .getElementById("focusModelTrigger")
        ?.setAttribute("aria-expanded", "false");
    }
  });
  window.visualViewport?.addEventListener("resize", positionModelMenu);
}

function setModelMenu(open) {
  $("modelMenu").hidden = !open;
  if (open) positionModelMenu();
  if (
    !open &&
    $("composer").contains(document.activeElement) &&
    document.activeElement !== $("input")
  ) {
    document.activeElement.blur();
  }
  $("modelTrigger").setAttribute("aria-expanded", String(open));
}

function positionModelMenu() {
  if (window.innerWidth > 520 || $("modelMenu").hidden) return;
  const available = Math.max(
    280,
    $("composer").getBoundingClientRect().top - 12,
  );
  $("modelMenu").style.setProperty(
    "--model-menu-max-height",
    String(available) + "px",
  );
}

function chooseCoachModel(model) {
  if (model.available === false) return;
  const effort = model.efforts.includes(coachSelection.effort)
    ? coachSelection.effort
    : model.defaultEffort;
  coachSelection = { provider: model.provider, model: model.id, effort };
  saveCoachSelection();
  renderModelPicker();
  renderFocusModelPicker();
}

function chooseCoachEffort(effort) {
  const model = selectedCoachModel();
  if (!model || !model.efforts.includes(effort)) return;
  coachSelection = { ...coachSelection, effort };
  saveCoachSelection();
  renderModelPicker();
  renderFocusModelPicker();
}

function saveCoachSelection() {
  try {
    localStorage.setItem(
      COACH_MODEL_STORAGE_KEY,
      JSON.stringify(coachSelection),
    );
  } catch {}
}

function renderModelPicker() {
  const selected = selectedCoachModel();
  if (!selected) return;
  $("modelProviderMark").textContent = modelMark(selected.provider);
  $("modelProviderMark").classList.toggle(
    "anthropic",
    selected.provider === "anthropic",
  );
  $("modelTriggerName").textContent =
    selected.label + (selected.available === false ? " · unavailable" : "");
  $("modelTriggerEffort").textContent = effortLabel(coachSelection.effort);

  const list = $("modelMenuList");
  list.innerHTML = "";
  [...new Set(coachModels.map((model) => model.provider))].forEach(
    (provider) => {
      const models = coachModels.filter((model) => model.provider === provider);
      const label = document.createElement("div");
      label.className = "model-group-label";
      label.textContent = models[0].providerLabel;
      list.appendChild(label);
      models.forEach((model) => {
        const button = document.createElement("button");
        const active =
          model.provider === coachSelection.provider &&
          model.id === coachSelection.model;
        button.type = "button";
        button.className = "model-option" + (active ? " selected" : "");
        button.disabled = model.available === false;
        button.setAttribute("aria-pressed", String(active));
        const mark = document.createElement("span");
        mark.className =
          "model-option-mark" +
          (model.provider === "anthropic" ? " anthropic" : "");
        mark.textContent = modelMark(model.provider);
        const copy = document.createElement("span");
        copy.className = "model-option-copy";
        const name = document.createElement("strong");
        name.textContent = model.label;
        const description = document.createElement("span");
        description.textContent =
          model.available === false
            ? "Unavailable on this PC · choose an available model"
            : model.description;
        copy.append(name, description);
        const check = document.createElement("span");
        check.className = "model-option-check";
        check.textContent = "✓";
        button.append(mark, copy, check);
        button.addEventListener("click", () => chooseCoachModel(model));
        list.appendChild(button);
      });
    },
  );

  $("reasoningHint").textContent =
    selected.provider === "anthropic" ? "adaptive effort" : "quality · speed";
  const options = $("reasoningOptions");
  options.innerHTML = "";
  selected.efforts.forEach((effort) => {
    const button = document.createElement("button");
    const active = effort === coachSelection.effort;
    button.type = "button";
    button.className = "reasoning-option" + (active ? " selected" : "");
    button.textContent = effortLabel(effort);
    button.setAttribute("aria-pressed", String(active));
    button.addEventListener("click", () => chooseCoachEffort(effort));
    options.appendChild(button);
  });
}

function wireFocusModelPicker() {
  const trigger = document.getElementById("focusModelTrigger");
  if (!trigger) return;
  const menu = $("focusModelMenu");
  trigger.addEventListener("click", (event) => {
    event.stopPropagation();
    const open = menu.hidden;
    menu.hidden = !open;
    trigger.setAttribute("aria-expanded", String(open));
  });
  $("focusModelMenuClose").addEventListener("click", () => {
    menu.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
  });
  $("focusOverlay").onpointerdown = (event) => {
    if (!menu.hidden && !event.target.closest(".focus-model-control")) {
      menu.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
    }
  };
  renderFocusModelPicker();
}

function renderFocusModelPicker() {
  const trigger = document.getElementById("focusModelTrigger");
  const selected = selectedCoachModel();
  if (!trigger || !selected) return;
  $("focusModelProviderMark").textContent = modelMark(selected.provider);
  $("focusModelProviderMark").classList.toggle(
    "anthropic",
    selected.provider === "anthropic",
  );
  $("focusModelTriggerName").textContent = selected.label;
  $("focusModelTriggerEffort").textContent = effortLabel(coachSelection.effort);

  const list = $("focusModelMenuList");
  list.innerHTML = "";
  [...new Set(coachModels.map((model) => model.provider))].forEach(
    (provider) => {
      const models = coachModels.filter((model) => model.provider === provider);
      const label = document.createElement("div");
      label.className = "model-group-label";
      label.textContent = models[0].providerLabel;
      list.appendChild(label);
      models.forEach((model) => {
        const button = document.createElement("button");
        const active =
          model.provider === coachSelection.provider &&
          model.id === coachSelection.model;
        button.type = "button";
        button.className = "model-option" + (active ? " selected" : "");
        button.setAttribute("aria-pressed", String(active));
        const mark = document.createElement("span");
        mark.className =
          "model-option-mark" +
          (model.provider === "anthropic" ? " anthropic" : "");
        mark.textContent = modelMark(model.provider);
        const copy = document.createElement("span");
        copy.className = "model-option-copy";
        const name = document.createElement("strong");
        name.textContent = model.label;
        const description = document.createElement("span");
        description.textContent = model.description;
        copy.append(name, description);
        const check = document.createElement("span");
        check.className = "model-option-check";
        check.textContent = "✓";
        button.append(mark, copy, check);
        button.addEventListener("click", () => chooseCoachModel(model));
        list.appendChild(button);
      });
    },
  );

  $("focusReasoningHint").textContent =
    selected.provider === "anthropic" ? "adaptive effort" : "quality · speed";
  const options = $("focusReasoningOptions");
  options.innerHTML = "";
  selected.efforts.forEach((effort) => {
    const button = document.createElement("button");
    const active = effort === coachSelection.effort;
    button.type = "button";
    button.className = "reasoning-option" + (active ? " selected" : "");
    button.textContent = effortLabel(effort);
    button.setAttribute("aria-pressed", String(active));
    button.addEventListener("click", () => chooseCoachEffort(effort));
    options.appendChild(button);
  });
}

function bubble(m) {
  const d = document.createElement("div");
  d.className = "msg " + (m.role === "user" ? "user" : "coach");
  const head = document.createElement("div");
  head.className = "msg-head";
  const who = document.createElement("div");
  who.className = "who";
  who.textContent = m.role === "user" ? cfg.name || "you" : "coach";
  head.appendChild(who);
  const stamp = document.createElement("time");
  stamp.className = "msg-date";
  if (messageDate(m)) {
    stamp.dateTime = m.ts;
    stamp.textContent = new Date(m.ts).toLocaleString("en-GB", {
      timeZone: "Europe/London",
      day: "numeric",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } else {
    stamp.textContent = "Date unknown";
  }
  head.appendChild(stamp);
  if (m.role === "coach") {
    const job = (coachQueue.jobs || []).find(
      (item) => item.messageId === m.replyTo || item.replyId === m.id,
    );
    const selection = m.selection || (job && job.selection);
    if (selection) {
      const model = document.createElement("div");
      model.className = "response-model";
      model.textContent = formatCoachSelection(selection);
      head.appendChild(model);
    }
  }
  d.appendChild(head);
  const body = document.createElement("div");
  body.innerHTML = mdLite(m.text);
  d.appendChild(body);
  if (m.role === "user" && m.id) {
    const job = (coachQueue.jobs || []).find((j) => j.messageId === m.id);
    const activity = job ? coachActivity[job.id] : null;
    if (job && (job.state !== "done" || activity)) {
      const row = document.createElement("div");
      row.className = "queue-row";
      if (job.state !== "done") {
        const status = document.createElement("span");
        status.className = "queue-state " + job.state;
        if (job.state === "processing")
          status.textContent = "coach is replying…";
        else if (job.state === "prepared")
          status.textContent = "reply ready · saving…";
        else if (job.state === "failed") {
          status.textContent = "Saved · coach needs attention";
          if (job.lastError) status.title = job.lastError;
        } else {
          status.textContent = `✓ saved · waiting${job.position ? ` · #${job.position}` : ""}`;
        }
        row.appendChild(status);
      }
      if (job.selection) {
        const model = document.createElement("span");
        model.className = "queue-model";
        model.textContent = formatCoachSelection(job.selection);
        row.appendChild(model);
      }
      if (activity && (activity.events || []).length) {
        const toggle = document.createElement("button");
        const expanded = expandedActivities.has(job.id);
        toggle.className = "activity-toggle";
        toggle.type = "button";
        toggle.setAttribute("aria-expanded", String(expanded));
        toggle.textContent = expanded ? "▼ hide activity" : "▶ show activity";
        toggle.addEventListener("click", () => {
          if (expandedActivities.has(job.id)) expandedActivities.delete(job.id);
          else expandedActivities.add(job.id);
          renderCoach();
        });
        row.appendChild(toggle);
      }
      d.appendChild(row);
      if (activity && expandedActivities.has(job.id)) {
        d.appendChild(renderCoachActivity(activity));
      }
    }
  }
  return d;
}

function renderCoachActivity(activity) {
  const panel = document.createElement("div");
  panel.className = "coach-activity";
  const head = document.createElement("div");
  head.className = "activity-head";
  const stateLabels = {
    running: "↻ running",
    validating: "◆ checking",
    saving: "◆ saving",
    done: "✓ done",
    failed: "✕ failed",
  };
  const model = String(activity.model || "coach")
    .replace(/^claude-/, "")
    .replaceAll("-", " ");
  head.textContent = `${model} · ${formatActivityElapsed(activity)} · ${stateLabels[activity.state] || activity.state}`;
  panel.appendChild(head);

  const list = document.createElement("ol");
  list.className = "activity-list";
  (activity.events || []).forEach((event) => {
    const item = document.createElement("li");
    item.className = "activity-event kind-" + (event.kind || "event");
    const stamp = document.createElement("time");
    const parsed = new Date(event.at);
    stamp.textContent = Number.isNaN(parsed.getTime())
      ? ""
      : parsed.toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        });
    const label = document.createElement("span");
    label.textContent = event.label;
    item.append(stamp, label);
    list.appendChild(item);
  });
  panel.appendChild(list);

  const note = document.createElement("p");
  note.className = "activity-note";
  note.textContent =
    "Tool activity and reasoning stages are shown. Private internal reasoning is not exposed.";
  panel.appendChild(note);
  return panel;
}

function formatActivityElapsed(activity) {
  const start = new Date(activity.startedAt).getTime();
  const end = new Date(activity.finishedAt || Date.now()).getTime();
  if (!Number.isFinite(start) || !Number.isFinite(end))
    return "time unavailable";
  const seconds = Math.max(0, Math.round((end - start) / 1000));
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return minutes ? `${minutes}m ${String(rest).padStart(2, "0")}s` : `${rest}s`;
}

function scrollThread() {
  if (currentView === "coach")
    requestAnimationFrame(() => {
      const t = $("thread");
      t.scrollTop = t.scrollHeight;
    });
}

function mdLite(text) {
  let h = String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  h = h
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
  const lines = h.split(/\n/);
  let out = "",
    inList = false;
  for (const ln of lines) {
    if (/^\s*[-•] /.test(ln)) {
      if (!inList) {
        out += "<ul>";
        inList = true;
      }
      out += "<li>" + ln.replace(/^\s*[-•] /, "") + "</li>";
    } else {
      if (inList) {
        out += "</ul>";
        inList = false;
      }
      if (ln.trim()) out += "<p>" + ln + "</p>";
    }
  }
  if (inList) out += "</ul>";
  return out;
}

async function sendMessage() {
  const box = $("input");
  const text = box.value.trim();
  if (!text) return;
  if (selectedCoachModel()?.available === false) {
    banner(
      "The selected coach model is unavailable on this PC. Choose an available model; your draft is kept.",
      true,
    );
    setModelMenu(true);
    return;
  }
  $("send").disabled = true;
  try {
    let outbox = null;
    try {
      outbox = JSON.parse(localStorage.getItem("practice-room-chat-outbox"));
    } catch {}
    const sameSelection =
      outbox &&
      JSON.stringify(outbox.selection) === JSON.stringify(coachSelection);
    const requestId =
      outbox && outbox.text === text && sameSelection
        ? outbox.requestId
        : crypto.randomUUID
          ? crypto.randomUUID()
          : `${Date.now()}-${Math.random()}`;
    const selection = { ...coachSelection };
    try {
      localStorage.setItem(
        "practice-room-chat-outbox",
        JSON.stringify({ requestId, text, selection }),
      );
    } catch {}
    const accepted = await api("/api/chat", { text, requestId, selection });
    const message = accepted.job.message;
    if (
      !(docs[FILES.chat].obj.messages || []).some((m) => m.id === message.id)
    ) {
      docs[FILES.chat].obj.messages.push(message);
    }
    const idx = (coachQueue.jobs || []).findIndex(
      (j) => j.id === accepted.job.id,
    );
    if (idx >= 0) coachQueue.jobs[idx] = accepted.job;
    else coachQueue.jobs.push(accepted.job);
    coachQueue.pending = (coachQueue.jobs || []).filter((j) =>
      ["queued", "failed"].includes(j.state),
    ).length;
    try {
      localStorage.removeItem("practice-room-chat-outbox");
    } catch {}
    if (box.value.trim() === text) {
      box.value = "";
      localStorage.removeItem("practice-room-chat-draft");
    }
    renderCoach(true);
    startPolling();
  } catch (e) {
    banner(e.message, true);
  }
  $("send").disabled = false;
}

function startPolling() {
  stopPolling();
  pollTimer = setInterval(async () => {
    try {
      const [fresh, meta] = await Promise.all([
        ghGet(FILES.chat, { fresh: true }),
        fetch(`/api/meta?t=${Date.now()}`, { cache: "no-store" }).then((r) =>
          r.json(),
        ),
      ]);
      docs[FILES.chat] = fresh;
      coachQueue = meta.coachQueue || coachQueue;
      coachActivity = meta.coachActivity || coachActivity;
      renderCoach();
      if (!coachQueue.pending && !coachQueue.processing) {
        stopPolling();
        await refreshQuiet();
        if (currentView !== "coach") $("coachDot").hidden = false;
        return;
      }
    } catch {}
  }, 2000);
}
function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function toggleMemory() {
  const panel = $("memPanel");
  if (!panel.hidden) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  panel.innerHTML = "<em>loading…</em>";
  try {
    const m = await ghGet(FILES.memory, { fresh: true });
    panel.innerHTML = mdLite(m.obj);
  } catch {
    panel.innerHTML =
      "<em>No memory file yet — it appears after your first conversation.</em>";
  }
}
