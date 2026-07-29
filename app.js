/* Practice Room — app */
"use strict";

const PRIVATE_ORIGIN = "https://lox.tail89d19b.ts.net:10000";
const FILES = { state:"data/state.json", chat:"data/chat.json", journal:"data/journal.json", memory:"memory/MEMORY.md", spots:"data/spots.json", obs:"data/observations.json" };
const $ = (id) => document.getElementById(id);

let cfg = null;           // {name}
let docs = {};            // path -> {obj|text, sha}
let pollTimer = null;
let currentView = "today";

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
    cfg = { name: m.name || "you" };
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
  $("weekPill").addEventListener("click", () => {
    const w = $("weekPanel"); w.hidden = !w.hidden;
  });
  window.addEventListener("focus", () => { if (cfg && !pollTimer) refreshQuiet(); });
}

async function loadAll(){
  const [st, ch, jr] = await Promise.all([
    ghGet(FILES.state, {fresh:true}), ghGet(FILES.chat, {fresh:true}), ghGet(FILES.journal, {fresh:true}),
  ]);
  docs[FILES.state] = st; docs[FILES.chat] = ch; docs[FILES.journal] = jr;
  try { docs[FILES.spots] = await ghGet(FILES.spots, {fresh:true}); } catch { docs[FILES.spots] = {obj:{spots:[]}, sha:null}; }
  try { docs[FILES.obs] = await ghGet(FILES.obs, {fresh:true}); } catch { docs[FILES.obs] = {obj:{obs:[]}, sha:null}; }
  try { localStorage.setItem("pr-cache", JSON.stringify({
    s: st.obj, c: ch.obj, j: jr.obj, sp: docs[FILES.spots].obj })); } catch {}
}

function showApp(){
  $("tabs").hidden = false;
  renderAll();
  switchView(currentView);
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
    const [st, ch, jr] = await Promise.all([
      ghGet(FILES.state, {fresh:true}), ghGet(FILES.chat, {fresh:true}), ghGet(FILES.journal, {fresh:true}),
    ]);
    docs[FILES.state] = st; docs[FILES.chat] = ch; docs[FILES.journal] = jr;
    try { docs[FILES.spots] = await ghGet(FILES.spots, {fresh:true}); } catch {}
    try { docs[FILES.obs] = await ghGet(FILES.obs, {fresh:true}); } catch {}
    renderAll();
  } catch {}
}

function switchView(v){
  currentView = v;
  document.querySelectorAll(".tab").forEach(b => b.classList.toggle("active", b.dataset.view === v));
  ["today","programme","coach","journal"].forEach(x => $("view-"+x).hidden = (x !== v));
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

function renderAll(){
  const broken = [];
  [["header", renderTop], ["today", renderToday], ["programme", renderProgramme],
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

  const w = s.week;
  $("weekPill").hidden = !w;
  if (w){
    $("weekPill").textContent = `Week ${w.num} — ${w.title} · this week's plan`;
    const wp = $("weekPanel");
    wp.innerHTML = `<h2></h2><p class="headline"></p>
      <div class="w-head">This week</div><ul class="w-goals"></ul>
      <div class="w-head">Pass the week if</div><ul class="w-gate"></ul>`;
    wp.querySelector("h2").textContent = `Week ${w.num} — ${w.title} (${w.dates})`;
    wp.querySelector(".headline").textContent = w.headline || "";
    (w.goals || []).forEach(g => { const li = document.createElement("li"); li.textContent = g; wp.querySelector(".w-goals").appendChild(li); });
    (w.gate || []).forEach(g => { const li = document.createElement("li"); li.textContent = g; wp.querySelector(".w-gate").appendChild(li); });
  }

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
  (s.today.blocks || []).forEach(b => {
    try { renderBlockCard(wrap, b); }
    catch (e){
      const c = document.createElement("div");
      c.className = "card block";
      c.textContent = (b && b.title ? b.title : "block") + " — display error: " + e.message;
      wrap.appendChild(c);
    }
  });
}

function renderBlockCard(wrap, b){
  {
    const card = document.createElement("div");
    const flag = FLAGS[b.flag] ? b.flag : null;
    const isBreak = String(b.id).startsWith("break");
    card.className = "card block" + (b.done ? " done" : "") + (flag ? " f-" + flag : "") + (isBreak ? " breakblk" : "");
    card.innerHTML = `
      <button class="tick" aria-label="mark done">${b.done ? "✓" : ""}</button>
      <div class="b-body">
        <div class="card-head"><h2></h2>
          <span class="head-right">${flag ? `<span class="ftag f-${flag}">${FLAGS[flag]}</span>` : ""}<span class="mins">${b.mins} min</span></span>
        </div>
        <p class="detail"></p>
        <div class="b-actions">
          <button class="timerbtn" data-block="${b.id}"></button>
          ${isBreak ? "" : '<button class="whybtn">why this? →</button><button class="whybtn focuslink">focus →</button>'}
        </div>
        ${isBreak ? "" : NOTEBAR_HTML}
      </div>`;
    card.querySelector("h2").textContent = b.title;
    card.querySelector(".detail").textContent = b.detail;
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

function todaysObs(blockId){
  const day = dayInfo().day;
  return (((docs[FILES.obs] || {}).obj || {}).obs || []).filter(o => o.blockId === blockId && o.day === day);
}

function wireNotebar(root, b){
  const bar = root.querySelector(".notebar");
  if (!bar) return;
  const input = bar.querySelector("input"), btn = bar.querySelector(".notebtn");
  const submit = () => { logObservation(b, input.value); input.value = ""; };
  btn.addEventListener("click", submit);
  input.addEventListener("keydown", e => { if (e.key === "Enter") submit(); });
  const list = root.querySelector(".obslist");
  if (list) todaysObs(b.id).slice(-3).forEach(o => {
    const d = document.createElement("div"); d.textContent = o.text; list.appendChild(d);
  });
}

async function logObservation(b, text){
  text = (text || "").trim();
  if (!text) return;
  const entry = { ts: new Date().toISOString(), day: dayInfo().day,
                  blockId: b.id, block: b.title, text, status: "new" };
  try {
    await ghPut(FILES.obs, o => { (o.obs = o.obs || []).push(entry); return o; }, "practice note");
    renderToday();
    if (focusIdx !== null) renderFocus();
  } catch (e){ banner(e.message, true); }
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
  const isBreak = String(b.id).startsWith("break");
  const undone = bs.filter(x => !x.done).length;
  const nxt = bs.slice(focusIdx + 1).find(x => !x.done);
  const ov = $("focusOverlay");
  ov.innerHTML = `<div class="focus-inner">
    <div class="f-kicker">Day ${Math.max(dayInfo().day,1)} · ${undone} block${undone===1?"":"s"} left · ${b.mins} min</div>
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
  ov.querySelector(".f-title").textContent = b.title;
  ov.querySelector(".f-detail").textContent = b.detail || "";
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
let timer = null; // {blockId, endsAt, remainMs, paused, iv, mins}

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
  timer = { blockId: b.id, mins: b.mins, endsAt: Date.now() + b.mins*60000, remainMs: b.mins*60000, paused: false, iv: null };
  timer.iv = setInterval(tickTimer, 1000);
}
function pauseTimer(){ if (!timer) return; timer.remainMs = timer.endsAt - Date.now(); timer.paused = true; }
function resumeTimer(){ if (!timer) return; timer.endsAt = Date.now() + timer.remainMs; timer.paused = false; }
function stopTimer(){ if (timer){ clearInterval(timer.iv); timer = null; document.title = "Practice Room"; } }

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
    div.className = "piece" + (flag ? " f-" + flag : "");
    const lastCold = p.lastCold
      ? `${p.lastCold.result === "pass" ? "✓ passed" : "✕ broke down"} · ${p.lastCold.date}`
      : "not yet tested";
    const level = p.security >= 85 ? "stage-ready" : p.security >= 65 ? "nearly there"
                : p.security >= 40 ? "building" : "fragile";
    div.innerHTML = `
      <div class="p-head"><h2></h2><span class="head-right">${flag ? `<span class="ftag f-${flag}">${FLAGS[flag]}</span>` : ""}<span class="p-tag">${level}</span></span></div>
      <div class="meter"><i style="width:${Math.max(3,Math.min(100,p.security))}%"></i></div>
      <div class="p-row">
        <span>security <b>${p.security}</b>/100</span>
        <span>reliable tempo <b>${p.tempoPct}%</b> of target</span>
        <span>cold test: <b>${lastCold}</b></span>
      </div>
      <p class="p-note"></p><div class="spots"></div>`;
    div.querySelector("h2").textContent = p.title;
    div.querySelector(".p-note").textContent = p.note || "";
    const spotBox = div.querySelector(".spots");
    const all = ((docs[FILES.spots] || {obj:{spots:[]}}).obj.spots || []).filter(sp => sp.piece === p.id);
    const open = all.filter(sp => sp.status !== "fixed");
    const fixed = all.length - open.length;
    open.forEach(sp => {
      const row = document.createElement("div");
      row.className = "spot" + (sp.status === "watching" ? " watching" : "");
      row.innerHTML = `<span class="s-bars"></span><span class="s-issue"></span><span class="s-meta"></span>`;
      row.querySelector(".s-bars").textContent = "b." + sp.bars;
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
  if (msgs.length && msgs[msgs.length-1].role === "user") t.appendChild(thinkingBubble());
  scrollThread();
}

function bubble(m){
  const d = document.createElement("div");
  d.className = "msg " + (m.role === "user" ? "user" : "coach");
  const who = document.createElement("div"); who.className = "who";
  who.textContent = m.role === "user" ? (cfg.name || "you") : "coach";
  d.appendChild(who);
  const body = document.createElement("div");
  body.innerHTML = mdLite(m.text);
  d.appendChild(body);
  return d;
}

function thinkingBubble(){
  const d = document.createElement("div");
  d.className = "msg coach pending";
  d.id = "pendingMsg";
  d.textContent = "The coach is at the piano… replies usually land within a minute or two.";
  return d;
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
    const r = await fetch("/api/chat", { method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({ text }) });
    if (!r.ok) throw new Error("Couldn't reach the coach — is the laptop awake?");
    docs[FILES.chat].obj.messages.push({ role:"user", text, ts: new Date().toISOString() });
    box.value = "";
    renderCoach();
    startPolling();
  } catch (e){ banner(e.message, true); }
  $("send").disabled = false;
}

function startPolling(){
  stopPolling();
  const begun = Date.now();
  pollTimer = setInterval(async () => {
    try {
      const fresh = await ghGet(FILES.chat, {fresh:true});
      const msgs = fresh.obj.messages || [];
      if (msgs.length && msgs[msgs.length-1].role !== "user"){
        docs[FILES.chat] = fresh;
        stopPolling();
        const [st, jr] = await Promise.all([ghGet(FILES.state,{fresh:true}), ghGet(FILES.journal,{fresh:true})]);
        docs[FILES.state] = st; docs[FILES.journal] = jr;
        renderAll();
        if (currentView !== "coach") $("coachDot").hidden = false;
        return;
      }
    } catch {}
    if (Date.now() - begun > 5*60*1000){
      stopPolling();
      const p = $("pendingMsg");
      if (p) p.textContent =
        "No reply yet — check the Practice Room server on the laptop (the Claude CLI may need attention).";
    }
  }, 8000);
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
