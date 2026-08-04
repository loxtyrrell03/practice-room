/* Practice Room — app */
"use strict";

const PRIVATE_ORIGIN = "https://lox.tail89d19b.ts.net:10000";
const FILES = {
  state:"data/state.json",
  dayPlans:"data/day-plans.json",
  weekly:"data/weekly-plan.json",
  chat:"data/chat.json",
  journal:"data/journal.json",
  memory:"memory/MEMORY.md",
  spots:"data/spots.json",
  obs:"data/observations.json"
};
const $ = (id) => document.getElementById(id);

let cfg = null;           // {name}
let docs = {};            // path -> {obj|text, sha}
let pollTimer = null;
let currentView = "today";
let coachQueue = {pending:0, processing:0, failed:0, jobs:[]};
let coachActivity = {};
let coachModels = [];
let coachSelection = {provider:"anthropic", model:"claude-opus-5", effort:"medium"};
const COACH_MODEL_STORAGE_KEY = "practice-room-coach-model-v2";
const expandedActivities = new Set();
const phaseOpenState = new Map();
let selectedPlanDay = null;

/* ── Private backend ─────────────────────────────────────── */
async function ghGet(path, {fresh=false} = {}){
  const suffix = fresh ? `&t=${Date.now()}` : "";
  const r = await fetch(`/api/file?path=${encodeURIComponent(path)}${suffix}`, {cache:"no-store"});
  if (!r.ok) throw new Error(`Read failed (${r.status}) for ${path}`);
  const text = (await r.json()).content;
  return { obj: path.endsWith(".json") ? JSON.parse(text) : text, sha: null };
}

/* mutate = fn(freshObj) -> newObj */
async function ghPut(path, mutate, message){
  const cur = await ghGet(path, {fresh:true});
  const next = mutate(structuredClone(cur.obj));
  const r = await fetch("/api/file", { method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({ path, content: JSON.stringify(next, null, 2) + "\n" }) });
  if (!r.ok) throw new Error(`Save failed (${r.status})`);
  docs[path] = { obj: next, sha: null };
  return next;
}

/* ── boot ────────────────────────────────────────────────── */
window.addEventListener("DOMContentLoaded", async () => {
  wireChrome();
  localStorage.removeItem("practice-room-config");
  if (location.hash) history.replaceState(null, "", location.pathname + location.search);
  try {
    const r = await fetch(`/api/meta?t=${Date.now()}`, {cache:"no-store"});
    const m = r.ok ? await r.json() : null;
    if (!m || m.mode !== "local") throw new Error("private backend unavailable");
    cfg = { name: m.name || "you", practiceLogs: m.practiceLogs || null };
    coachQueue = m.coachQueue || coachQueue;
    coachActivity = m.coachActivity || {};
    configureCoachModels(m);
    return start();
  } catch {
    if (location.origin !== PRIVATE_ORIGIN) location.replace(PRIVATE_ORIGIN + "/");
  }
});

/* live refresh: keep the page current when the coach updates files */
setInterval(() => {
  if (cfg && !pollTimer && document.visibilityState === "visible"
      && focusIdx === null && !document.activeElement.matches("input, textarea")){
    refreshQuiet();
  }
}, 25000);

function wireChrome(){
  document.querySelectorAll(".tab").forEach(btn =>
    btn.addEventListener("click", () => switchView(btn.dataset.view)));
  $("refreshBtn").addEventListener("click", () => start());
  $("send").addEventListener("click", sendMessage);
  wireModelPicker();
  $("input").addEventListener("keydown", e => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) sendMessage();
  });
  $("debriefBtn").addEventListener("click", () => {
    switchView("coach");
    $("input").value = "Debrief: ";
    $("input").focus();
  });
  document.querySelectorAll(".chip.q").forEach(c =>
    c.addEventListener("click", () => { $("input").value = c.dataset.q; $("input").focus(); }));
  $("memBtn").addEventListener("click", toggleMemory);
  $("focusBtn").addEventListener("click", () => openFocus());
  $("mobileFocusBtn").addEventListener("click", () => openFocus());
  $("viewPlanBtn").addEventListener("click", () => {
    selectedPlanDay = dayInfo().day + 1;
    renderWeekPlan();
    switchView("week");
  });
  window.addEventListener("focus", () => { if (cfg && !pollTimer) refreshQuiet(); });
  window.addEventListener("pageshow", tickTimer);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") tickTimer();
  });
}

async function loadAll(){
  const [st, ch, jr] = await Promise.all([
    ghGet(FILES.state, {fresh:true}), ghGet(FILES.chat, {fresh:true}), ghGet(FILES.journal, {fresh:true}),
  ]);
  docs[FILES.state] = st; docs[FILES.chat] = ch; docs[FILES.journal] = jr;
  try { docs[FILES.dayPlans] = await ghGet(FILES.dayPlans, {fresh:true}); } catch { docs[FILES.dayPlans] = {obj:{version:1,plans:[]}, sha:null}; }
  try { docs[FILES.weekly] = await ghGet(FILES.weekly, {fresh:true}); } catch { docs[FILES.weekly] = {obj:{phases:[]}, sha:null}; }
  try { docs[FILES.spots] = await ghGet(FILES.spots, {fresh:true}); } catch { docs[FILES.spots] = {obj:{spots:[]}, sha:null}; }
  try { docs[FILES.obs] = await ghGet(FILES.obs, {fresh:true}); } catch { docs[FILES.obs] = {obj:{obs:[]}, sha:null}; }
  try { localStorage.setItem("pr-cache", JSON.stringify({
    s: st.obj, dp: docs[FILES.dayPlans].obj, w: docs[FILES.weekly].obj,
    c: ch.obj, j: jr.obj, sp: docs[FILES.spots].obj })); } catch {}
}

function showApp(){
  $("tabs").hidden = false;
  restoreTimer();
  renderAll();
  switchView(currentView);
  tickTimer();
  if (coachQueue.pending || coachQueue.processing) startPolling();
}

async function start(){
  banner("");
  let lastErr = null;
  for (let i = 0; i < 3; i++){
    try { await loadAll(); return showApp(); }
    catch (e){ lastErr = e; await new Promise(r => setTimeout(r, 1200 * (i + 1))); }
  }
  // Never replace the working surface with setup UI. Use the last complete snapshot.
  let cached = null;
  try { cached = JSON.parse(localStorage.getItem("pr-cache")); } catch {}
  if (cached){
    docs[FILES.state]   = { obj: cached.s,  sha: null };
    docs[FILES.dayPlans] = { obj: cached.dp || {version:1,plans:[]}, sha: null };
    docs[FILES.weekly]  = { obj: cached.w || {phases:[]}, sha: null };
    docs[FILES.chat]    = { obj: cached.c,  sha: null };
    docs[FILES.journal] = { obj: cached.j,  sha: null };
    docs[FILES.spots]   = { obj: cached.sp || {spots:[]}, sha: null };
    showApp();
    banner(`Can't reach the Practice Room server (${lastErr.message}) — showing your last-loaded data. Tap refresh to retry.`, true);
  } else {
    $("tabs").hidden = false;
    banner(`${lastErr.message} — the laptop server is not reachable. Tap refresh after it wakes.`, true);
  }
}

async function refreshQuiet(){
  try {
    const [st, ch, jr, meta] = await Promise.all([
      ghGet(FILES.state, {fresh:true}), ghGet(FILES.chat, {fresh:true}), ghGet(FILES.journal, {fresh:true}),
      fetch(`/api/meta?t=${Date.now()}`, {cache:"no-store"}).then(r => r.json()),
    ]);
    docs[FILES.state] = st; docs[FILES.chat] = ch; docs[FILES.journal] = jr;
    coachQueue = meta.coachQueue || coachQueue;
    coachActivity = meta.coachActivity || coachActivity;
    try { docs[FILES.dayPlans] = await ghGet(FILES.dayPlans, {fresh:true}); } catch {}
    try { docs[FILES.weekly] = await ghGet(FILES.weekly, {fresh:true}); } catch {}
    try { docs[FILES.spots] = await ghGet(FILES.spots, {fresh:true}); } catch {}
    try { docs[FILES.obs] = await ghGet(FILES.obs, {fresh:true}); } catch {}
    renderAll();
  } catch {}
}

function switchView(v){
  currentView = v;
  document.querySelectorAll(".tab").forEach(b => b.classList.toggle("active", b.dataset.view === v));
  ["today","week","programme","coach","journal"].forEach(x => $("view-"+x).hidden = (x !== v));
  window.scrollTo({top:0, behavior:"auto"});
  if (v === "coach"){ $("coachDot").hidden = true; scrollThread(); }
}

function banner(text, isErr){
  const b = $("banner");
  if (!text){ b.hidden = true; return; }
  b.textContent = text; b.hidden = false;
  b.style.borderColor = isErr ? "rgba(210,105,79,.5)" : "rgba(226,169,79,.4)";
}

/* ── rendering ───────────────────────────────────────────── */
function state(){ return docs[FILES.state].obj; }
function dayPlans(){ return ((docs[FILES.dayPlans] || {}).obj || {version:1, plans:[]}); }
function chat(){ return docs[FILES.chat].obj; }
function journal(){ return docs[FILES.journal].obj; }

function dayInfo(){
  const s = state();
  const one = 24*3600*1000;
  const today = new Date(); today.setHours(0,0,0,0);
  const startD = new Date(s.startDate + "T00:00:00");
  const recD = new Date(s.recitalDate + "T00:00:00");
  const day = Math.floor((today - startD)/one) + 1;
  const left = Math.round((recD - today)/one);
  return { day, left, total: Math.round((recD - startD)/one) + 1 };
}

function isBreakBlock(block){
  return String(block && block.id).startsWith("break");
}

function blockMinutes(block){
  const mins = Number(block && block.mins);
  return Number.isFinite(mins) && mins > 0 ? mins : 0;
}

function formatPracticeMinutes(mins){
  const whole = Math.max(0, Math.ceil(mins));
  const hours = Math.floor(whole / 60);
  const rest = whole % 60;
  if (!hours) return `${rest} min`;
  return rest ? `${hours}h ${rest}m` : `${hours}h`;
}

function practiceTimeInfo(){
  const practiceBlocks = (state().today.blocks || []).filter(b => !isBreakBlock(b));
  const totalMins = practiceBlocks.reduce((sum, b) => sum + blockMinutes(b), 0);
  let leftMins = practiceBlocks
    .filter(b => !b.done)
    .reduce((sum, b) => sum + blockMinutes(b), 0);

  const active = timer && practiceBlocks.find(b => b.id === timer.blockId && !b.done);
  if (active){
    const activeMs = timer.paused ? timer.remainMs : timer.endsAt - Date.now();
    leftMins += Math.max(0, activeMs) / 60000 - blockMinutes(active);
  }
  return {totalMins, leftMins};
}

function renderPracticeTime(){
  const total = $("practiceTotal"), left = $("practiceLeft");
  if (!total || !left) return;
  const time = practiceTimeInfo();
  total.textContent = formatPracticeMinutes(time.totalMins);
  left.textContent = formatPracticeMinutes(time.leftMins);
}

function renderAll(){
  const broken = [];
  [["header", renderTop], ["today", renderToday], ["week plan", renderWeekPlan], ["programme", renderProgramme],
   ["coach", renderCoach], ["journal", renderJournal]].forEach(([name, fn]) => {
    try { fn(); } catch (e){ broken.push(`${name}: ${e.message}`); }
  });
  try {
    const n = (state().today.blocks || []).length;
    $("footLeft").textContent = `Practice Room · loaded ${new Date().toLocaleTimeString()} · ${n} blocks today`;
  } catch {}
  if (broken.length) banner("Display error (data is intact) — " + broken.join(" · "), true);
}

function renderTop(){
  const {day, left} = dayInfo();
  $("topCount").textContent = left > 0 ? `Day ${Math.max(day,1)} · ${left} day${left===1?"":"s"} to curtain`
                          : left === 0 ? "Recital day" : "Post-recital";
}

function renderToday(){
  const s = state(); const {day, left, total} = dayInfo();
  const planDate = new Date(s.today.date + "T12:00:00");
  const planIsToday = s.today.date === todayISO();
  const blocks = s.today.blocks || [];
  const completed = blocks.filter(block => block.done).length;
  $("todayDate").textContent = fullPlanDate(planDate);
  $("todayProgress").textContent =
    `${completed} of ${blocks.length} blocks complete`;
  $("todayState").hidden = planIsToday;
  $("todayState").textContent = planIsToday
    ? ""
    : `✕ Wrong date · ${s.today.date}`;
  $("dayNum").textContent = left === 0 ? "Recital day" : `Day ${Math.max(day,1)}`;
  $("curtain").textContent = left > 0 ? `${left} day${left===1?"":"s"} to curtain` :
    (left === 0 ? "Tonight. Trust the work." : "The bow has been taken.");
  const dots = $("dots"); dots.innerHTML = "";
  for (let i=1; i<=total; i++){
    const el = document.createElement("i");
    if (i < day) el.className = "past";
    if (i === day) el.className = "now";
    dots.appendChild(el);
  }
  $("focus").textContent = s.today.focus || "";
  renderPracticeTime();

  const gates = {7:"Gate day — run the Week 1 checklist with your coach tonight.",
                 14:"Gate day — Week 2 checklist tonight. Programme decisions get made on today's numbers.",
                 24:"Gate day — whole programme memorised + first full filmed run due. Checklist tonight.",
                 34:"Final gate — last mock done, freeze tomorrow. Readiness check with your coach tonight."};
  $("gateBanner").hidden = !gates[day];
  if (gates[day]) $("gateBanner").textContent = gates[day];

  // cold chips
  const chipbox = $("coldChips"); chipbox.innerHTML = "";
  s.pieces.forEach(p => {
    const c = document.createElement("button");
    const res = (p.lastCold && p.lastCold.date === todayISO()) ? p.lastCold.result : null;
    c.className = "chip" + (res === "pass" ? " pass" : res === "fail" ? " fail" : "");
    c.textContent = (res === "pass" ? "✓ " : res === "fail" ? "✕ " : "") + p.short;
    c.addEventListener("click", () => cycleCold(p.id));
    chipbox.appendChild(c);
  });

  // blocks
  const wrap = $("blocks"); wrap.innerHTML = "";
  blocks.forEach(b => {
    try { renderBlockCard(wrap, b); }
    catch (e){
      const c = document.createElement("div");
      c.className = "card block";
      c.textContent = (b && b.title ? b.title : "block") + " — display error: " + e.message;
      wrap.appendChild(c);
    }
  });
}

function renderWeekPlan(){
  const wrap = $("weeks");
  wrap.innerHTML = "";
  const day = dayInfo().day;
  const phases = resolvedPlanPhases(day);
  renderForecast(phases, day);

  if (!phases.length){
    wrap.innerHTML = '<p class="sub">The weekly plan is not available in this snapshot.</p>';
    return;
  }

  const current = phases.find(p => day >= Number(p.startDay) && day <= Number(p.endDay));
  if (current){
    $("weekLede").textContent = `Day ${Math.max(day,1)} · ${current.title}`;
  } else {
    $("weekLede").textContent = `Day ${Math.max(day,1)}`;
  }

  phases.forEach(phase => {
    const status = day > Number(phase.endDay) ? "complete"
      : day >= Number(phase.startDay) ? "current" : "upcoming";
    const details = document.createElement("details");
    details.className = `phase ${status}`;
    details.dataset.phase = phase.id;
    details.open = phaseOpenState.has(phase.id) ? phaseOpenState.get(phase.id) : false;
    details.innerHTML = `
      <summary>
        <div class="phase-topline">
          <span class="phase-label"></span>
          <span class="phase-status ${status}"></span>
        </div>
        <h2 class="phase-title"></h2>
        <div class="phase-range"></div>
        <p class="phase-headline"></p>
      </summary>
      <div class="phase-details">
        <section class="phase-section goals"><h3>Work of the phase</h3><ul></ul></section>
        <section class="phase-section plan-gate"><h3></h3><ul></ul></section>
      </div>`;
    details.querySelector(".phase-label").textContent = phase.label || `Week ${phase.week}`;
    const statusText = status === "complete" ? "✓ complete" : status === "current" ? "◆ current" : "○ upcoming";
    details.querySelector(".phase-status").textContent = statusText;
    details.querySelector(".phase-title").textContent = phase.title;
    details.querySelector(".phase-range").textContent =
      `Days ${phase.startDay}–${phase.endDay} · ${phase.dates}`;
    details.querySelector(".phase-headline").textContent = phase.headline || "";
    fillPlanList(details.querySelector(".goals ul"), phase.goals || []);
    const gate = phase.gate || {};
    details.querySelector(".plan-gate h3").textContent = gate.label || "End state";
    const criteria = gate.criteria || [];
    if (criteria.length) fillPlanList(details.querySelector(".plan-gate ul"), criteria);
    else details.querySelector(".plan-gate").insertAdjacentHTML("beforeend", '<p class="no-gate">No separate gate. Protect the work already banked.</p>');
    details.addEventListener("toggle", () => phaseOpenState.set(phase.id, details.open));
    wrap.appendChild(details);
  });
}

function resolvedPlanPhases(day){
  const s = state();
  let phases = ((((docs[FILES.weekly] || {}).obj || {}).phases) || [])
    .map(p => structuredClone(p));

  if (!phases.length && s.week){
    phases = [{
      id:`week-${s.week.num}`,
      week:s.week.num,
      label:`Week ${s.week.num}`,
      title:s.week.title,
      dates:s.week.dates,
      startDay:day,
      endDay:day,
      headline:s.week.headline,
      goals:s.week.goals || [],
      gate:{label:"Current gate", criteria:s.week.gate || []}
    }];
  }

  const currentWeek = s.week;
  if (currentWeek){
    const active = phases.find(p =>
      Number(p.week) === Number(currentWeek.num) &&
      day >= Number(p.startDay) && day <= Number(p.endDay));
    if (active){
      active.title = currentWeek.title || active.title;
      active.dates = currentWeek.dates || active.dates;
      active.headline = currentWeek.headline || active.headline;
      active.goals = currentWeek.goals || active.goals;
      active.gate = {
        ...(active.gate || {}),
        criteria:currentWeek.gate || (active.gate || {}).criteria || []
      };
    }
  }
  return phases;
}

function renderForecast(phases, currentDay){
  const strip = $("dayStrip");
  const panel = $("dayPlan");
  strip.innerHTML = "";
  panel.innerHTML = "";

  const total = dayInfo().total;
  const firstDay = Math.max(1, currentDay);
  const lastDay = Math.min(total, currentDay + 6);
  if (firstDay > lastDay){
    panel.innerHTML = '<p class="forecast-empty">The recital has passed. The journal holds the completed run.</p>';
    return;
  }

  if (!selectedPlanDay || selectedPlanDay < firstDay || selectedPlanDay > lastDay){
    selectedPlanDay = firstDay;
  }

  for (let planDay = firstDay; planDay <= lastDay; planDay++){
    const date = dateForPlanDay(planDay);
    const isoDate = localISODate(date);
    const dated = datedPlan(isoDate);
    const isToday = planDay === currentDay;
    const isTomorrow = planDay === currentDay + 1;
    const phase = phases.find(p =>
      planDay >= Number(p.startDay) && planDay <= Number(p.endDay));
    const tabMeta = isToday
      ? `${(state().today.blocks || []).filter(block => block.done).length}/${(state().today.blocks || []).length} done`
      : dated && ["ready","active"].includes(dated.status)
        ? `${(dated.blocks || []).length} blocks`
        : phase ? phase.title : "";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.id = `plan-day-${planDay}`;
    btn.className = "day-tab" + (planDay === selectedPlanDay ? " selected" : "");
    btn.setAttribute("role", "tab");
    btn.setAttribute("aria-controls", "dayPlan");
    btn.setAttribute("aria-selected", String(planDay === selectedPlanDay));
    btn.innerHTML = `
      <span class="day-tab-weekday"></span>
      <strong></strong>
      <span class="day-tab-meta"></span>`;
    btn.querySelector(".day-tab-weekday").textContent =
      isToday ? "Today" : isTomorrow ? "Tomorrow"
        : date.toLocaleDateString("en-GB", {weekday:"short"});
    btn.querySelector("strong").textContent =
      date.toLocaleDateString("en-GB", {day:"numeric", month:"short"});
    btn.querySelector(".day-tab-meta").textContent = tabMeta;
    btn.addEventListener("click", () => {
      selectedPlanDay = planDay;
      renderForecast(phases, currentDay);
      $(`plan-day-${planDay}`).scrollIntoView({block:"nearest", inline:"center"});
    });
    strip.appendChild(btn);
  }

  const selectedDate = dateForPlanDay(selectedPlanDay);
  const selectedISO = localISODate(selectedDate);
  const selectedDatedPlan = datedPlan(selectedISO);
  const phase = phases.find(p =>
    selectedPlanDay >= Number(p.startDay) && selectedPlanDay <= Number(p.endDay));
  if (selectedPlanDay === currentDay){
    renderActivePlan(panel, selectedDate, selectedPlanDay);
  } else if (
    selectedDatedPlan &&
    ["ready","active"].includes(selectedDatedPlan.status) &&
    (selectedDatedPlan.blocks || []).length
  ){
    renderReadyPlan(panel, selectedDate, selectedPlanDay, selectedDatedPlan);
  } else if (selectedPlanDay === currentDay + 1){
    renderTomorrowPlan(panel, selectedDate, selectedPlanDay);
  } else {
    renderRoughPlan(
      panel, selectedDate, selectedPlanDay, phase, selectedDatedPlan
    );
  }
  panel.setAttribute("aria-labelledby", `plan-day-${selectedPlanDay}`);
}

function localISODate(date){
  const shifted = new Date(date);
  shifted.setMinutes(shifted.getMinutes() - shifted.getTimezoneOffset());
  return shifted.toISOString().slice(0, 10);
}

function datedPlan(date){
  return (dayPlans().plans || []).find(plan => plan.date === date) || null;
}

function dateForPlanDay(planDay){
  const date = new Date(state().startDate + "T12:00:00");
  date.setDate(date.getDate() + planDay - 1);
  return date;
}

function fullPlanDate(date){
  return date.toLocaleDateString("en-GB", {
    weekday:"long", day:"numeric", month:"long"
  });
}

function renderActivePlan(panel, date, planDay){
  const blocks = state().today.blocks || [];
  const completed = blocks.filter(block => block.done).length;
  const mins = blocks.filter(block => !isBreakBlock(block))
    .reduce((sum, block) => sum + blockMinutes(block), 0);
  panel.className = "day-plan active";
  panel.innerHTML = `
    <div class="day-plan-top">
      <div>
        <h2></h2>
      </div>
      <div class="plan-day-number"></div>
    </div>
    <div class="active-plan-summary">
      <strong></strong>
      <span></span>
      <button class="plan-link">Open today's working view →</button>
    </div>
    <div class="plan-schedule compact"></div>`;
  panel.querySelector("h2").textContent = fullPlanDate(date);
  panel.querySelector(".plan-day-number").textContent = `Day ${planDay}`;
  panel.querySelector(".active-plan-summary strong").textContent =
    `${completed} of ${blocks.length} done`;
  panel.querySelector(".active-plan-summary span").textContent =
    `${formatPracticeMinutes(mins)} total`;
  panel.querySelector(".plan-link").textContent = "Open today →";
  panel.querySelector(".plan-link").addEventListener("click", () => switchView("today"));
  renderPlanSchedule(panel.querySelector(".plan-schedule"), blocks, {compact:true});
}

function renderReadyPlan(panel, date, planDay, plan){
  const blocks = plan.blocks || [];
  const mins = blocks.reduce((sum, block) => sum + blockMinutes(block), 0);
  panel.className = "day-plan ready";
  panel.innerHTML = `
    <div class="day-plan-top">
      <div>
        <h2></h2>
      </div>
      <div class="plan-day-number"></div>
    </div>
    <div class="ready-plan-meta"></div>
    <div class="plan-schedule"></div>
    <section class="deferred-evidence" hidden>
      <div>
        <h3>For later</h3>
      </div>
      <div class="deferred-list"></div>
    </section>`;
  panel.querySelector("h2").textContent = fullPlanDate(date);
  panel.querySelector(".plan-day-number").textContent = `Day ${planDay}`;
  panel.querySelector(".ready-plan-meta").textContent =
    `${formatPracticeMinutes(mins)} session · ${blocks.length} blocks`;
  renderPlanSchedule(panel.querySelector(".plan-schedule"), blocks);
  renderDeferredLogs(panel, plan.deferredLogs || []);
}

function renderPlanSchedule(root, blocks, {compact=false} = {}){
  root.innerHTML = "";
  blocks.forEach((block, index) => {
    const row = document.createElement("section");
    const isBreak = isBreakBlock(block);
    row.className = `plan-block${isBreak ? " break" : ""}${block.done ? " complete" : ""}`;
    row.innerHTML = `
      <div class="plan-block-order"></div>
      <div class="plan-block-main">
        <div class="plan-block-heading">
          <h3></h3>
          <span class="plan-block-mins"></span>
        </div>
        <div class="plan-block-detail"></div>
        <div class="plan-log-refs" hidden>
          <strong>From today</strong>
          <ul></ul>
        </div>
      </div>`;
    row.querySelector(".plan-block-order").textContent =
      block.done ? "✓" : String(index + 1).padStart(2, "0");
    row.querySelector("h3").textContent = block.title || "Untitled block";
    row.querySelector(".plan-block-mins").textContent = `${block.mins} min`;
    const detail = row.querySelector(".plan-block-detail");
    if (compact && !isBreak){
      detail.textContent = block.done
        ? "Completed on this date."
        : "Still ahead on this date.";
    } else {
      renderBlockInstructions(detail, block);
    }
    const references = block.logRefs || [];
    if (references.length){
      const evidence = row.querySelector(".plan-log-refs");
      evidence.hidden = false;
      const list = evidence.querySelector("ul");
      references.forEach(reference => {
        const item = document.createElement("li");
        item.textContent = reference.note;
        list.appendChild(item);
      });
    }
    root.appendChild(row);
  });
}

function renderBlockInstructions(root, block){
  const steps = Array.isArray(block.steps)
    ? block.steps.filter(step => step && step.lead && step.text)
    : [];
  if (!steps.length){
    root.textContent = block.detail || "";
    return;
  }
  renderInstructionSteps(root, steps);
}

function renderInstructionSteps(root, steps){
  root.innerHTML = "";
  const list = document.createElement("ul");
  list.className = "instruction-list";
  steps.forEach(step => {
    const item = document.createElement("li");
    const lead = document.createElement("strong");
    lead.textContent = step.lead;
    const text = document.createElement("span");
    text.textContent = step.text;
    item.append(lead, text);
    list.appendChild(item);
  });
  root.appendChild(list);
}

function renderProgrammeStatus(root, piece){
  const points = Array.isArray(piece.statusPoints)
    ? piece.statusPoints.filter(point => point && point.lead && point.text)
    : [];
  if (!points.length){
    root.textContent = piece.note || "";
    return;
  }
  renderInstructionSteps(root, points);
  root.querySelector(".instruction-list")?.classList.add("programme-points");
}

function renderDeferredLogs(panel, deferredLogs){
  if (!deferredLogs.length) return;
  const section = panel.querySelector(".deferred-evidence");
  section.hidden = false;
  const list = section.querySelector(".deferred-list");
  deferredLogs.forEach(item => {
    const row = document.createElement("div");
    row.className = "deferred-row";
    const note = document.createElement("strong");
    note.textContent = item.note || "Practice log";
    const reason = document.createElement("span");
    const target = new Date(item.targetDate + "T12:00:00");
    reason.textContent =
      `${fullPlanDate(target)} · ${item.reason}`;
    row.append(note, reason);
    list.appendChild(row);
  });
}

function renderTomorrowPlan(panel, date, planDay){
  const preview = String(state().tomorrowPreview || "").trim();
  panel.className = "day-plan tomorrow";
  panel.innerHTML = `
    <div class="day-plan-top">
      <div>
        <h2></h2>
      </div>
      <div class="plan-day-number"></div>
    </div>
    <div class="tomorrow-body"></div>`;
  panel.querySelector("h2").textContent = fullPlanDate(date);
  panel.querySelector(".plan-day-number").textContent = `Day ${planDay}`;
  const body = panel.querySelector(".tomorrow-body");
  if (!preview){
    body.innerHTML = '<p class="forecast-empty">No plan yet. Debrief tonight to build it.</p>';
    return;
  }
  appendTomorrowPreview(body, preview);
}

function appendTomorrowPreview(body, preview){
  const chunks = preview.split(/\n\s*\n/).map(x => x.trim()).filter(Boolean);
  chunks.forEach((chunk, index) => {
    const lines = chunk.split(/\n+/).map(x => x.trim()).filter(Boolean);
    if (!lines.length) return;
    if (index === 0){
      const intro = document.createElement("p");
      intro.className = "tomorrow-intro";
      intro.textContent = lines.join(" ");
      body.appendChild(intro);
      return;
    }

    const section = document.createElement("section");
    section.className = "tomorrow-section";
    const headingMatch = lines[0].match(/^([A-Z][A-Z\s]+)\s+[—-]\s+(.+)$/);
    if (headingMatch){
      const heading = document.createElement("h3");
      heading.textContent = headingMatch[1].trim();
      section.appendChild(heading);
      const lead = document.createElement("p");
      lead.className = "tomorrow-section-lead";
      lead.textContent = headingMatch[2].trim();
      section.appendChild(lead);
    }

    const items = headingMatch ? lines.slice(1) : lines;
    const list = document.createElement("ul");
    items.forEach(text => {
      const li = document.createElement("li");
      li.textContent = text;
      list.appendChild(li);
    });
    section.appendChild(list);
    body.appendChild(section);
  });
}

function renderRoughPlan(panel, date, planDay, phase, plan=null){
  panel.className = "day-plan rough";
  panel.innerHTML = `
    <div class="day-plan-top">
      <div>
        <h2></h2>
      </div>
      <div class="plan-day-number"></div>
    </div>
    <div class="rough-phase">
      <div class="rough-phase-label"></div>
    </div>
    <section class="rough-targets">
      <h3>Current outline</h3>
      <div class="rough-target-list"></div>
    </section>`;
  panel.querySelector("h2").textContent = fullPlanDate(date);
  panel.querySelector(".plan-day-number").textContent = `Day ${planDay}`;
  const targetRoot = panel.querySelector(".rough-target-list");
  const outline = Array.isArray((plan || {}).outline) ? plan.outline : [];

  if (!phase){
    panel.querySelector(".rough-phase-label").textContent =
      outline.length ? "Outline" : "No outline yet";
    if (outline.length) renderInstructionSteps(targetRoot, outline);
    else panel.querySelector(".rough-targets").remove();
    return;
  }

  panel.querySelector(".rough-phase-label").textContent =
    phase.title;

  if (outline.length){
    renderInstructionSteps(targetRoot, outline);
  } else {
    const goals = (phase.goals || [])
      .filter(goal => !String(goal).trim().startsWith("✓"));
    const exactDay = new RegExp(`\\bDay\\s+${planDay}\\b`, "i");
    goals.sort((a, b) => Number(exactDay.test(b)) - Number(exactDay.test(a)));
    const list = document.createElement("ul");
    targetRoot.appendChild(list);
    goals.slice(0, 4).forEach(goal => {
      const li = document.createElement("li");
      li.textContent = goal;
      list.appendChild(li);
    });
    if (!list.children.length){
      const li = document.createElement("li");
      li.textContent =
        "Protect the phase gains and use the next cold test to choose the work.";
      list.appendChild(li);
    }
  }

  const gate = phase.gate || {};
  if (Number(gate.day) === planDay && (gate.criteria || []).length){
    const callout = document.createElement("div");
    callout.className = "plan-gate-callout";
    const label = document.createElement("strong");
    label.textContent = `◆ ${gate.label || "Gate day"}`;
    const copy = document.createElement("span");
    copy.textContent = "The coach will turn these criteria into tonight's checklist.";
    callout.append(label, copy);
    panel.querySelector(".rough-phase").after(callout);
  }
}

function fillPlanList(list, items){
  items.forEach(item => {
    const li = document.createElement("li");
    li.textContent = item;
    list.appendChild(li);
  });
}

function renderBlockCard(wrap, b){
  {
    const card = document.createElement("div");
    const flag = FLAGS[b.flag] ? b.flag : null;
    const isBreak = isBreakBlock(b);
    const isStudy = String(b.id).startsWith("score-study");
    const movementContext = blockMovementContext(b);
    card.className = "card block" + (b.done ? " done" : "") + (flag ? " f-" + flag : "") +
      (isBreak ? " breakblk" : "") + (isStudy ? " studyblk" : "");
    card.innerHTML = `
      <button class="tick" aria-label="mark done">${b.done ? "✓" : ""}</button>
      <div class="b-body">
        ${movementContext ? '<div class="movement-label"></div>' : ""}
        <div class="card-head"><h2></h2>
          <span class="head-right">${flag ? `<span class="ftag f-${flag}">${FLAGS[flag]}</span>` : ""}${isStudy ? '<span class="block-mode">off bench</span>' : ""}<span class="mins">${b.mins} min</span></span>
        </div>
        <div class="detail"></div>
        <div class="b-actions">
          <button class="timerbtn" data-block="${b.id}"></button>
          ${isBreak ? "" : '<button class="whybtn">why this? →</button><button class="whybtn focuslink">focus →</button>'}
        </div>
        ${isBreak ? "" : NOTEBAR_HTML}
      </div>`;
    if (movementContext) card.querySelector(".movement-label").textContent = movementContext;
    card.querySelector("h2").textContent = b.title;
    renderBlockInstructions(card.querySelector(".detail"), b);
    card.querySelector(".tick").addEventListener("click", () => toggleBlock(b.id));
    const fl = card.querySelector(".focuslink");
    if (fl) fl.addEventListener("click", () => openFocus(b.id));
    wireNotebar(card, b);
    const why = card.querySelector(".whybtn:not(.focuslink)");
    if (why) {
      why.addEventListener("click", () => {
        switchView("coach");
        $("input").value = `Why am I doing “${b.title}” today — what is it for, and how do I know it's working?`;
        $("input").focus();
      });
    }
    wireTimerButton(card.querySelector(".timerbtn"), b);
    wrap.appendChild(card);
  }
}

const NOTEBAR_HTML = '<div class="notebar"><input type="text" placeholder="Log it: e.g. RH too loud b.57" maxlength="300"><button class="notebtn">log</button></div><div class="obslist"></div>';

function blockMovementContext(b){
  if (!b.movementId || !b.movement) return "";
  const piece = (state().pieces || []).find(p => p.id === b.pieceId);
  return `${piece ? piece.short : b.pieceId} · ${b.movement}`;
}

function todaysObs(blockId){
  const day = dayInfo().day;
  return (((docs[FILES.obs] || {}).obj || {}).obs || []).filter(o => o.blockId === blockId && o.day === day);
}

function wireNotebar(root, b){
  const bar = root.querySelector(".notebar");
  if (!bar) return;
  const input = bar.querySelector("input"), btn = bar.querySelector(".notebtn");
  if (b.movement) input.placeholder = `Log ${b.movement}: e.g. RH too loud b.57`;
  const submit = async () => {
    const text = input.value.trim();
    if (!text) return;
    input.dataset.pendingId = input.dataset.pendingId || crypto.randomUUID();
    btn.disabled = true;
    const saved = await logObservation(b, text, input.dataset.pendingId);
    btn.disabled = false;
    if (saved){
      input.value = "";
      delete input.dataset.pendingId;
    }
  };
  btn.addEventListener("click", submit);
  input.addEventListener("keydown", e => { if (e.key === "Enter") submit(); });
  const list = root.querySelector(".obslist");
  if (list) todaysObs(b.id).slice(-3).forEach(o => {
    const d = document.createElement("div");
    d.className = "obsrow";
    const note = document.createElement("span");
    note.className = "obstext";
    note.textContent = o.text;
    const status = document.createElement("span");
    status.className = "obsstatus s-" + (o.status || "pending");
    const labels = {
      pending: "✓ saved · pending",
      processing: "↻ processing",
      processed: "✓ processed",
      failed: "! saved · failed · retrying"
    };
    status.textContent = labels[o.status] || labels.pending;
    d.append(note, status);
    list.appendChild(d);
  });
}

async function logObservation(b, text, clientId){
  text = (text || "").trim();
  if (!text) return false;
  try {
    const r = await fetch("/api/observations", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({
        clientId,
        day:dayInfo().day,
        blockId:b.id,
        block:b.title,
        pieceId:b.pieceId || null,
        movementId:b.movementId || null,
        movement:b.movement || null,
        text
      })
    });
    if (!r.ok) throw new Error("Practice log was not saved — try again.");
    const result = await r.json();
    const entry = result.observation;
    const rows = (docs[FILES.obs].obj.obs = docs[FILES.obs].obj.obs || []);
    const existing = rows.findIndex(o => o.id === entry.id);
    if (existing >= 0) rows[existing] = entry;
    else rows.push(entry);
    renderToday();
    if (focusIdx !== null) renderFocus();
    return true;
  } catch (e){
    banner(e.message, true);
    return false;
  }
}

/* ── focus mode ── */
let focusIdx = null;

function openFocus(startId){
  const bs = state().today.blocks || [];
  if (!bs.length) return;
  let idx = bs.findIndex(x => !x.done);
  if (startId){ const i = bs.findIndex(x => x.id === startId); if (i >= 0) idx = i; }
  if (idx < 0) idx = bs.length - 1;
  focusIdx = idx;
  document.body.classList.add("focusing");
  $("focusOverlay").hidden = false;
  renderFocus();
}

function closeFocus(){
  focusIdx = null;
  document.body.classList.remove("focusing");
  $("focusOverlay").hidden = true;
  renderToday();
}

function focusAdvance(){
  const bs = state().today.blocks || [];
  for (let i = focusIdx + 1; i < bs.length; i++){
    if (!bs[i].done){ focusIdx = i; renderFocus(); return; }
  }
  for (let i = 0; i < bs.length; i++){
    if (!bs[i].done){ focusIdx = i; renderFocus(); return; }
  }
  closeFocus();
}

function renderFocus(){
  if (focusIdx === null) return;
  const bs = state().today.blocks || [];
  const b = bs[focusIdx];
  if (!b) return closeFocus();
  const isBreak = isBreakBlock(b);
  const isStudy = String(b.id).startsWith("score-study");
  const movementContext = blockMovementContext(b);
  const undone = bs.filter(x => !x.done).length;
  const nxt = bs.slice(focusIdx + 1).find(x => !x.done);
  const ov = $("focusOverlay");
  ov.innerHTML = `<div class="focus-inner">
    <div class="f-kicker">Day ${Math.max(dayInfo().day,1)} · ${undone} block${undone===1?"":"s"} left · ${isStudy ? "off bench · " : ""}${b.mins} min</div>
    ${movementContext ? '<div class="movement-label"></div>' : ""}
    <div class="f-title"></div>
    <div class="f-detail"></div>
    ${b.why ? '<div class="f-why"></div>' : ""}
    <div class="f-timer-row"><button class="timerbtn" data-block="${b.id}"></button></div>
    ${isBreak ? "" : NOTEBAR_HTML}
    <div class="f-actions">
      <button class="btn primary" id="fDone">${isBreak ? "Break over → next" : "Done → next"}</button>
      <button class="notebtn" id="fSkip">Skip for now</button>
      <button class="notebtn" id="fExit">Exit focus</button>
    </div>
    ${nxt ? `<div class="f-next">Up next: ${nxt.title} · ${nxt.mins} min</div>` : '<div class="f-next">Last one of the day.</div>'}
  </div>`;
  if (movementContext) ov.querySelector(".movement-label").textContent = movementContext;
  ov.querySelector(".f-title").textContent = b.title;
  renderBlockInstructions(ov.querySelector(".f-detail"), b);
  if (b.why) ov.querySelector(".f-why").textContent = b.why;
  wireTimerButton(ov.querySelector(".timerbtn"), b);
  wireNotebar(ov, b);
  ov.querySelector("#fDone").addEventListener("click", async () => {
    await toggleBlockTo(b.id, true); focusAdvance();
  });
  ov.querySelector("#fSkip").addEventListener("click", focusAdvance);
  ov.querySelector("#fExit").addEventListener("click", closeFocus);
}

/* attention flags: word + color, never color alone */
const FLAGS = { urgent: "✕ needs work", focus: "◆ focus", secure: "✓ secure" };

/* ── block timer ─────────────────────────────────────────── */
const TIMER_STORAGE_KEY = "practice-room-timer";
let timer = null; // {blockId, practiceDate, endsAt, remainMs, paused, iv, mins}

function wireTimerButton(btn, b){
  if (b.done){ btn.remove(); return; }
  paintTimerBtn(btn, b);
  btn.addEventListener("click", () => {
    if (timer && timer.blockId === b.id){
      timer.paused ? resumeTimer() : pauseTimer();
    } else {
      startTimer(b);
    }
    renderToday();
  });
}

function paintTimerBtn(btn, b){
  if (timer && timer.blockId === b.id){
    const mmss = fmtMs(timer.paused ? timer.remainMs : timer.endsAt - Date.now());
    btn.textContent = timer.paused ? `▶ resume · ${mmss}` : `⏸ ${mmss}`;
    btn.classList.add("running");
  } else {
    btn.textContent = `▶ start · ${b.mins} min`;
    btn.classList.remove("running");
  }
}

function startTimer(b){
  stopTimer();
  timer = {
    blockId: b.id,
    practiceDate: state().today.date,
    mins: b.mins,
    endsAt: Date.now() + b.mins*60000,
    remainMs: b.mins*60000,
    paused: false,
    iv: null
  };
  saveTimer();
  timer.iv = setInterval(tickTimer, 1000);
}
function pauseTimer(){
  if (!timer) return;
  timer.remainMs = Math.max(0, timer.endsAt - Date.now());
  timer.paused = true;
  saveTimer();
}
function resumeTimer(){
  if (!timer) return;
  timer.endsAt = Date.now() + timer.remainMs;
  timer.paused = false;
  saveTimer();
}
function stopTimer(){
  if (timer) clearInterval(timer.iv);
  timer = null;
  try { localStorage.removeItem(TIMER_STORAGE_KEY); } catch {}
  document.title = "Practice Room";
}

function saveTimer(){
  if (!timer) return;
  try {
    localStorage.setItem(TIMER_STORAGE_KEY, JSON.stringify({
      blockId: timer.blockId,
      practiceDate: timer.practiceDate,
      endsAt: timer.endsAt,
      remainMs: timer.remainMs,
      paused: timer.paused,
      mins: timer.mins
    }));
  } catch {}
}

function restoreTimer(){
  if (timer) return;
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(TIMER_STORAGE_KEY)); } catch {}
  const blocks = state().today.blocks || [];
  const block = saved && blocks.find(b => b.id === saved.blockId);
  const valid = block && !block.done &&
    saved.practiceDate === state().today.date &&
    Number.isFinite(saved.endsAt) && Number.isFinite(saved.remainMs) &&
    typeof saved.paused === "boolean";
  if (!valid){
    try { localStorage.removeItem(TIMER_STORAGE_KEY); } catch {}
    return;
  }
  timer = {
    blockId: saved.blockId,
    practiceDate: saved.practiceDate,
    mins: Number.isFinite(saved.mins) ? saved.mins : block.mins,
    endsAt: saved.endsAt,
    remainMs: Math.max(0, saved.remainMs),
    paused: saved.paused,
    iv: setInterval(tickTimer, 1000)
  };
}

function tickTimer(){
  if (!timer || timer.paused) return;
  const left = timer.endsAt - Date.now();
  if (left <= 0){
    const finished = timer.blockId;
    chime();
    stopTimer();
    toggleBlockTo(finished, true).then(() => { if (focusIdx !== null) focusAdvance(); });
    return;
  }
  document.title = `${fmtMs(left)} · Practice Room`;
  document.querySelectorAll(`.timerbtn[data-block="${timer.blockId}"]`).forEach(btn => {
    const st = state();
    const bb = (st.today.blocks || []).find(x => x.id === timer.blockId);
    if (bb) paintTimerBtn(btn, bb);
  });
  renderPracticeTime();
}

function fmtMs(ms){
  const t = Math.max(0, Math.round(ms/1000));
  return `${Math.floor(t/60)}:${String(t%60).padStart(2,"0")}`;
}

function chime(){
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    [0, 0.35, 0.7].forEach((delay, i) => {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.connect(g); g.connect(ctx.destination);
      o.frequency.value = [660, 880, 990][i];
      g.gain.setValueAtTime(0.0001, ctx.currentTime + delay);
      g.gain.exponentialRampToValueAtTime(0.2, ctx.currentTime + delay + 0.03);
      g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + delay + 0.5);
      o.start(ctx.currentTime + delay); o.stop(ctx.currentTime + delay + 0.55);
    });
  } catch {}
}

async function toggleBlockTo(blockId, value){
  try {
    await ghPut(FILES.state, s => {
      const b = s.today.blocks.find(x => x.id === blockId);
      if (b) b.done = value;
      return s;
    }, "block finished (timer)");
    renderToday();
  } catch (e){ banner(e.message, true); }
}

function todayISO(){ const d = new Date(); d.setMinutes(d.getMinutes()-d.getTimezoneOffset()); return d.toISOString().slice(0,10); }

async function cycleCold(pieceId){
  try {
    await ghPut(FILES.state, s => {
      const p = s.pieces.find(x => x.id === pieceId);
      const today = todayISO();
      const cur = (p.lastCold && p.lastCold.date === today) ? p.lastCold.result : null;
      if (cur === null) p.lastCold = { result:"pass", date: today };
      else if (cur === "pass") p.lastCold = { result:"fail", date: today };
      else p.lastCold = null;
      return s;
    }, "cold test result");
    renderToday(); renderProgramme();
  } catch (e){ banner(e.message, true); }
}

async function toggleBlock(blockId){
  try {
    await ghPut(FILES.state, s => {
      const b = s.today.blocks.find(x => x.id === blockId);
      if (b) b.done = !b.done;
      return s;
    }, "block toggled");
    renderToday();
  } catch (e){ banner(e.message, true); }
}

function renderProgramme(){
  const wrap = $("pieces"); wrap.innerHTML = "";
  state().pieces.forEach(p => {
    const div = document.createElement("div");
    const flag = FLAGS[p.attention] ? p.attention : null;
    const hasSecurity = Number.isFinite(Number(p.security)) && p.security !== null;
    const hasTempo = Number.isFinite(Number(p.tempoPct)) && p.tempoPct !== null;
    const security = hasSecurity ? Number(p.security) : null;
    div.className = "piece" + (flag ? " f-" + flag : "");
    const lastCold = p.lastCold
      ? `${p.lastCold.result === "pass" ? "✓ passed" : "✕ broke down"} · ${p.lastCold.date}`
      : "not yet tested";
    const level = security === null ? "unmeasured"
                : security >= 85 ? "stage-ready" : security >= 65 ? "nearly there"
                : security >= 40 ? "building" : "fragile";
    div.innerHTML = `
      <div class="p-head"><h2></h2><span class="head-right">${flag ? `<span class="ftag f-${flag}">${FLAGS[flag]}</span>` : ""}<span class="p-tag">${level}</span></span></div>
      <div class="meter"><i style="width:${security === null ? 0 : Math.max(3,Math.min(100,security))}%"></i></div>
      <div class="p-row">
        <span>security <b>${security === null ? "—" : security}</b>${security === null ? "" : "/100"}</span>
        <span>reliable tempo <b>${hasTempo ? `${p.tempoPct}%` : "—"}</b>${hasTempo ? " of target" : ""}</span>
        <span>cold test: <b>${lastCold}</b></span>
      </div>
      <div class="p-note"></div><div class="spots"></div>`;
    div.querySelector("h2").textContent = p.title;
    renderProgrammeStatus(div.querySelector(".p-note"), p);
    const spotBox = div.querySelector(".spots");
    const all = ((docs[FILES.spots] || {obj:{spots:[]}}).obj.spots || []).filter(sp => sp.piece === p.id);
    const open = all.filter(sp => sp.status !== "fixed");
    const fixed = all.length - open.length;
    open.forEach(sp => {
      const row = document.createElement("div");
      row.className = "spot" + (sp.status === "watching" ? " watching" : "");
      row.innerHTML = `<span class="s-bars"></span><span class="s-issue"></span><span class="s-meta"></span>`;
      row.querySelector(".s-bars").textContent = (sp.movement ? `${sp.movement} · ` : "") + "b." + sp.bars;
      row.querySelector(".s-issue").textContent = sp.issue;
      row.querySelector(".s-meta").textContent = (sp.status === "watching" ? "◆ watching · " : "✕ open · ") + sp.logged;
      spotBox.appendChild(row);
    });
    if (fixed > 0){
      const row = document.createElement("div");
      row.className = "spot fixedcount";
      row.textContent = `✓ ${fixed} fixed`;
      spotBox.appendChild(row);
    }
    wrap.appendChild(div);
  });
}

/* ── coach / chat ────────────────────────────────────────── */
function renderCoach(){
  const t = $("thread"); t.innerHTML = "";
  const msgs = chat().messages || [];
  msgs.forEach(m => t.appendChild(bubble(m)));
  scrollThread();
}

function configureCoachModels(meta){
  coachModels = Array.isArray(meta.coachModels) ? meta.coachModels : [];
  const fallback = meta.defaultCoachSelection || coachSelection;
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(COACH_MODEL_STORAGE_KEY)); } catch {}
  try { localStorage.removeItem("practice-room-coach-model"); } catch {}
  coachSelection = validCoachSelection(saved) || validCoachSelection(fallback) || coachSelection;
  renderModelPicker();
}

function validCoachSelection(value){
  if (!value || typeof value !== "object") return null;
  const model = coachModels.find(item => item.provider === value.provider && item.id === value.model);
  if (!model) return null;
  const effort = model.efforts.includes(value.effort) ? value.effort : model.defaultEffort;
  return {provider:model.provider, model:model.id, effort};
}

function selectedCoachModel(selection=coachSelection){
  return coachModels.find(item => item.provider === selection.provider && item.id === selection.model) || null;
}

function modelMark(provider){ return provider === "anthropic" ? "CL" : "GPT"; }

function effortLabel(effort){
  return effort === "xhigh" ? "X-high" : effort.charAt(0).toUpperCase() + effort.slice(1);
}

function formatCoachSelection(selection){
  const model = selectedCoachModel(selection || {});
  if (model) return `${model.label} · ${effortLabel(selection.effort)}`;
  return selection && selection.model ? `${selection.model} · ${selection.effort || "default"}` : "coach model";
}

function wireModelPicker(){
  $("modelTrigger").addEventListener("click", event => {
    event.stopPropagation();
    setModelMenu($("modelMenu").hidden);
  });
  $("modelMenuClose").addEventListener("click", () => setModelMenu(false));
  document.addEventListener("pointerdown", event => {
    if (!$("modelMenu").hidden && !$("composer").contains(event.target)) setModelMenu(false);
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && !$("modelMenu").hidden) setModelMenu(false);
  });
  window.visualViewport?.addEventListener("resize", positionModelMenu);
}

function setModelMenu(open){
  $("modelMenu").hidden = !open;
  if (open) positionModelMenu();
  if (!open && $("composer").contains(document.activeElement) && document.activeElement !== $("input")){
    document.activeElement.blur();
  }
  $("modelTrigger").setAttribute("aria-expanded", String(open));
}

function positionModelMenu(){
  if (window.innerWidth > 520 || $("modelMenu").hidden) return;
  const available = Math.max(280, $("composer").getBoundingClientRect().top - 12);
  $("modelMenu").style.setProperty("--model-menu-max-height", String(available) + "px");
}

function chooseCoachModel(model){
  const effort = model.efforts.includes(coachSelection.effort)
    ? coachSelection.effort : model.defaultEffort;
  coachSelection = {provider:model.provider, model:model.id, effort};
  saveCoachSelection();
  renderModelPicker();
}

function chooseCoachEffort(effort){
  const model = selectedCoachModel();
  if (!model || !model.efforts.includes(effort)) return;
  coachSelection = {...coachSelection, effort};
  saveCoachSelection();
  renderModelPicker();
}

function saveCoachSelection(){
  try { localStorage.setItem(COACH_MODEL_STORAGE_KEY, JSON.stringify(coachSelection)); } catch {}
}

function renderModelPicker(){
  const selected = selectedCoachModel();
  if (!selected) return;
  $("modelProviderMark").textContent = modelMark(selected.provider);
  $("modelProviderMark").classList.toggle("anthropic", selected.provider === "anthropic");
  $("modelTriggerName").textContent = selected.label;
  $("modelTriggerEffort").textContent = effortLabel(coachSelection.effort);

  const list = $("modelMenuList");
  list.innerHTML = "";
  [...new Set(coachModels.map(model => model.provider))].forEach(provider => {
    const models = coachModels.filter(model => model.provider === provider);
    const label = document.createElement("div");
    label.className = "model-group-label";
    label.textContent = models[0].providerLabel;
    list.appendChild(label);
    models.forEach(model => {
      const button = document.createElement("button");
      const active = model.provider === coachSelection.provider && model.id === coachSelection.model;
      button.type = "button";
      button.className = "model-option" + (active ? " selected" : "");
      button.setAttribute("aria-pressed", String(active));
      const mark = document.createElement("span");
      mark.className = "model-option-mark" + (model.provider === "anthropic" ? " anthropic" : "");
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
  });

  $("reasoningHint").textContent = selected.provider === "anthropic"
    ? "adaptive effort" : "quality · speed";
  const options = $("reasoningOptions");
  options.innerHTML = "";
  selected.efforts.forEach(effort => {
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

function bubble(m){
  const d = document.createElement("div");
  d.className = "msg " + (m.role === "user" ? "user" : "coach");
  const head = document.createElement("div"); head.className = "msg-head";
  const who = document.createElement("div"); who.className = "who";
  who.textContent = m.role === "user" ? (cfg.name || "you") : "coach";
  head.appendChild(who);
  if (m.role === "coach"){
    const job = (coachQueue.jobs || []).find(item => item.messageId === m.replyTo || item.replyId === m.id);
    const selection = m.selection || (job && job.selection);
    if (selection){
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
  if (m.role === "user" && m.id){
    const job = (coachQueue.jobs || []).find(j => j.messageId === m.id);
    const activity = job ? coachActivity[job.id] : null;
    if (job && (job.state !== "done" || activity)){
      const row = document.createElement("div");
      row.className = "queue-row";
      if (job.state !== "done"){
        const status = document.createElement("span");
        status.className = "queue-state " + job.state;
        if (job.state === "processing") status.textContent = "coach is replying…";
        else if (job.state === "prepared") status.textContent = "reply ready · saving…";
        else if (job.state === "failed") {
          status.textContent = "✓ saved · coach failed · retrying";
          if (job.lastError) status.title = job.lastError;
        } else {
          status.textContent = `✓ saved · waiting${job.position ? ` · #${job.position}` : ""}`;
        }
        row.appendChild(status);
      }
      if (job.selection){
        const model = document.createElement("span");
        model.className = "queue-model";
        model.textContent = formatCoachSelection(job.selection);
        row.appendChild(model);
      }
      if (activity && (activity.events || []).length){
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
      if (activity && expandedActivities.has(job.id)){
        d.appendChild(renderCoachActivity(activity));
      }
    }
  }
  return d;
}

function renderCoachActivity(activity){
  const panel = document.createElement("div");
  panel.className = "coach-activity";
  const head = document.createElement("div");
  head.className = "activity-head";
  const stateLabels = {
    running:"↻ running", validating:"◆ checking", saving:"◆ saving",
    done:"✓ done", failed:"✕ failed"
  };
  const model = String(activity.model || "coach").replace(/^claude-/, "").replaceAll("-", " ");
  head.textContent = `${model} · ${formatActivityElapsed(activity)} · ${stateLabels[activity.state] || activity.state}`;
  panel.appendChild(head);

  const list = document.createElement("ol");
  list.className = "activity-list";
  (activity.events || []).forEach(event => {
    const item = document.createElement("li");
    item.className = "activity-event kind-" + (event.kind || "event");
    const stamp = document.createElement("time");
    const parsed = new Date(event.at);
    stamp.textContent = Number.isNaN(parsed.getTime()) ? "" :
      parsed.toLocaleTimeString([], {hour:"2-digit", minute:"2-digit", second:"2-digit"});
    const label = document.createElement("span");
    label.textContent = event.label;
    item.append(stamp, label);
    list.appendChild(item);
  });
  panel.appendChild(list);

  const note = document.createElement("p");
  note.className = "activity-note";
  note.textContent = "Tool activity and reasoning stages are shown. Private internal reasoning is not exposed.";
  panel.appendChild(note);
  return panel;
}

function formatActivityElapsed(activity){
  const start = new Date(activity.startedAt).getTime();
  const end = new Date(activity.finishedAt || Date.now()).getTime();
  if (!Number.isFinite(start) || !Number.isFinite(end)) return "time unavailable";
  const seconds = Math.max(0, Math.round((end - start) / 1000));
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return minutes ? `${minutes}m ${String(rest).padStart(2, "0")}s` : `${rest}s`;
}

function scrollThread(){
  if (currentView !== "coach") return;
  requestAnimationFrame(() => { const t = $("thread"); t.scrollTop = t.scrollHeight; window.scrollTo(0, document.body.scrollHeight); });
}

function mdLite(text){
  let h = String(text)
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
  h = h.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
       .replace(/`([^`]+)`/g, "<code>$1</code>");
  const lines = h.split(/\n/);
  let out = "", inList = false;
  for (const ln of lines){
    if (/^\s*[-•] /.test(ln)){
      if (!inList){ out += "<ul>"; inList = true; }
      out += "<li>" + ln.replace(/^\s*[-•] /,"") + "</li>";
    } else {
      if (inList){ out += "</ul>"; inList = false; }
      if (ln.trim()) out += "<p>" + ln + "</p>";
    }
  }
  if (inList) out += "</ul>";
  return out;
}

async function sendMessage(){
  const box = $("input");
  const text = box.value.trim();
  if (!text) return;
  $("send").disabled = true;
  try {
    let outbox = null;
    try { outbox = JSON.parse(localStorage.getItem("practice-room-chat-outbox")); } catch {}
    const sameSelection = outbox && JSON.stringify(outbox.selection) === JSON.stringify(coachSelection);
    const requestId = outbox && outbox.text === text && sameSelection ? outbox.requestId :
      (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`);
    const selection = {...coachSelection};
    try { localStorage.setItem("practice-room-chat-outbox", JSON.stringify({requestId, text, selection})); } catch {}
    const r = await fetch("/api/chat", { method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({ text, requestId, selection }) });
    if (!r.ok) throw new Error("Couldn't reach the coach — is the laptop awake?");
    const accepted = await r.json();
    const message = accepted.job.message;
    if (!(docs[FILES.chat].obj.messages || []).some(m => m.id === message.id)){
      docs[FILES.chat].obj.messages.push(message);
    }
    const idx = (coachQueue.jobs || []).findIndex(j => j.id === accepted.job.id);
    if (idx >= 0) coachQueue.jobs[idx] = accepted.job;
    else coachQueue.jobs.push(accepted.job);
    coachQueue.pending = (coachQueue.jobs || []).filter(j => ["queued","failed"].includes(j.state)).length;
    try { localStorage.removeItem("practice-room-chat-outbox"); } catch {}
    box.value = "";
    renderCoach();
    startPolling();
  } catch (e){ banner(e.message, true); }
  $("send").disabled = false;
}

function startPolling(){
  stopPolling();
  pollTimer = setInterval(async () => {
    try {
      const [fresh, meta] = await Promise.all([
        ghGet(FILES.chat, {fresh:true}),
        fetch(`/api/meta?t=${Date.now()}`, {cache:"no-store"}).then(r => r.json()),
      ]);
      docs[FILES.chat] = fresh;
      coachQueue = meta.coachQueue || coachQueue;
      coachActivity = meta.coachActivity || coachActivity;
      renderCoach();
      if (!coachQueue.pending && !coachQueue.processing){
        stopPolling();
        const [st, wp, jr] = await Promise.all([
          ghGet(FILES.state,{fresh:true}),
          ghGet(FILES.weekly,{fresh:true}).catch(() => docs[FILES.weekly]),
          ghGet(FILES.journal,{fresh:true})
        ]);
        docs[FILES.state] = st; docs[FILES.weekly] = wp; docs[FILES.journal] = jr;
        renderAll();
        if (currentView !== "coach") $("coachDot").hidden = false;
        return;
      }
    } catch {}
  }, 2000);
}
function stopPolling(){ if (pollTimer){ clearInterval(pollTimer); pollTimer = null; } }

async function toggleMemory(){
  const panel = $("memPanel");
  if (!panel.hidden){ panel.hidden = true; return; }
  panel.hidden = false;
  panel.innerHTML = "<em>loading…</em>";
  try {
    const m = await ghGet(FILES.memory, {fresh:true});
    panel.innerHTML = mdLite(m.obj);
  } catch { panel.innerHTML = "<em>No memory file yet — it appears after your first conversation.</em>"; }
}

/* ── journal ─────────────────────────────────────────────── */
function renderJournal(){
  const wrap = $("entries"); wrap.innerHTML = "";
  const entries = (journal().entries || []).slice().reverse();
  if (!entries.length){
    wrap.innerHTML = `<div class="card"><p class="sub">Nothing here yet. After your first
      evening debrief, the coach writes the entry for you.</p></div>`;
    return;
  }
  entries.forEach(e => {
    const d = document.createElement("div");
    d.className = "entry";
    d.innerHTML = `<div class="e-date">${e.date} · day ${e.day}</div><h2></h2><div class="e-body"></div>`;
    d.querySelector("h2").textContent = e.title || "Practice day";
    d.querySelector(".e-body").innerHTML = mdLite(e.body || "");
    wrap.appendChild(d);
  });
}
