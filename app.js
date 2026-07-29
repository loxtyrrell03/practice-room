/* Practice Room — app */
"use strict";

const LS_KEY = "practice-room-config";
const FILES = { state:"data/state.json", chat:"data/chat.json", journal:"data/journal.json", memory:"memory/MEMORY.md" };
const $ = (id) => document.getElementById(id);

let cfg = null;           // {name, token, owner, repo}
let docs = {};            // path -> {obj|text, sha}
let pollTimer = null;
let currentView = "today";

/* ── GitHub API ──────────────────────────────────────────── */
function api(path){ return `https://api.github.com/repos/${cfg.owner}/${cfg.repo}/contents/${path}`; }
function headers(){ return { "Authorization":`Bearer ${cfg.token}`, "Accept":"application/vnd.github+json", "X-GitHub-Api-Version":"2022-11-28" }; }

function b64decode(b64){
  const bin = atob(b64.replace(/\n/g,""));
  const bytes = Uint8Array.from(bin, c => c.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}
function b64encode(str){
  const bytes = new TextEncoder().encode(str);
  let bin = "";
  for (let i=0;i<bytes.length;i+=0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i,i+0x8000));
  return btoa(bin);
}

async function ghGet(path, {fresh=false} = {}){
  const url = api(path) + (fresh ? `?t=${Date.now()}` : "");
  const r = await fetch(url, { headers: headers(), cache:"no-store" });
  if (r.status === 404) throw new Error(`Missing file: ${path}`);
  if (r.status === 401) throw new Error("Token rejected (401). Reconnect with a fresh token.");
  if (!r.ok) throw new Error(`GitHub error ${r.status} on ${path}`);
  const j = await r.json();
  const text = b64decode(j.content);
  const isJson = path.endsWith(".json");
  return { obj: isJson ? JSON.parse(text) : text, sha: j.sha };
}

/* mutate = fn(freshObj) -> newObj ; retries once on conflict */
async function ghPut(path, mutate, message){
  for (let attempt = 0; attempt < 3; attempt++){
    const cur = await ghGet(path, {fresh:true});
    const next = mutate(structuredClone(cur.obj));
    const body = JSON.stringify({
      message, sha: cur.sha,
      content: b64encode(JSON.stringify(next, null, 2) + "\n"),
    });
    const r = await fetch(api(path), { method:"PUT", headers: headers(), body });
    if (r.ok){ docs[path] = { obj: next, sha: (await r.json()).content.sha }; return next; }
    if (r.status !== 409 && r.status !== 422) throw new Error(`Save failed (${r.status})`);
    await new Promise(res => setTimeout(res, 800));
  }
  throw new Error("Save failed after retries — refresh and try again.");
}

/* ── boot ────────────────────────────────────────────────── */
window.addEventListener("DOMContentLoaded", () => {
  try { cfg = JSON.parse(localStorage.getItem(LS_KEY)); } catch {}
  wireChrome();
  if (cfg && cfg.token) start(); else showSetup();
});

function wireChrome(){
  document.querySelectorAll(".tab").forEach(btn =>
    btn.addEventListener("click", () => switchView(btn.dataset.view)));
  $("refreshBtn").addEventListener("click", () => start());
  $("resetBtn").addEventListener("click", () => {
    if (confirm("Disconnect this browser? (Your data stays safe in the repo.)")){
      localStorage.removeItem(LS_KEY); location.reload();
    }
  });
  $("setupGo").addEventListener("click", connect);
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
  window.addEventListener("focus", () => { if (cfg && cfg.token && !pollTimer) refreshQuiet(); });
}

function showSetup(){
  ["today","programme","coach","journal"].forEach(v => $("view-"+v).hidden = true);
  $("tabs").hidden = true;
  $("view-setup").hidden = false;
}

async function connect(){
  const err = $("setupErr"); err.hidden = true;
  cfg = {
    name:  $("setupName").value.trim() || "pianist",
    token: $("setupToken").value.trim(),
    owner: $("setupOwner").value.trim(),
    repo:  $("setupRepo").value.trim(),
  };
  if (!cfg.token){ err.textContent = "Paste a token first."; err.hidden = false; return; }
  $("setupGo").disabled = true; $("setupGo").textContent = "Connecting…";
  try {
    await ghGet(FILES.state);
    localStorage.setItem(LS_KEY, JSON.stringify(cfg));
    await start();
  } catch (e) {
    err.textContent = e.message; err.hidden = false;
    $("setupGo").disabled = false; $("setupGo").textContent = "Open the practice room";
  }
}

async function start(){
  banner("");
  try {
    const [st, ch, jr] = await Promise.all([
      ghGet(FILES.state, {fresh:true}), ghGet(FILES.chat, {fresh:true}), ghGet(FILES.journal, {fresh:true}),
    ]);
    docs[FILES.state] = st; docs[FILES.chat] = ch; docs[FILES.journal] = jr;
    $("view-setup").hidden = true; $("tabs").hidden = false;
    renderAll();
    switchView(currentView);
  } catch (e) {
    if (!cfg || !cfg.token) return showSetup();
    banner(e.message + " — check the connection.", true);
    showSetup();
    $("setupGo").disabled = false; $("setupGo").textContent = "Open the practice room";
  }
}

async function refreshQuiet(){
  try {
    const [st, ch, jr] = await Promise.all([
      ghGet(FILES.state, {fresh:true}), ghGet(FILES.chat, {fresh:true}), ghGet(FILES.journal, {fresh:true}),
    ]);
    docs[FILES.state] = st; docs[FILES.chat] = ch; docs[FILES.journal] = jr;
    renderAll();
  } catch {}
}

function switchView(v){
  currentView = v;
  document.querySelectorAll(".tab").forEach(b => b.classList.toggle("active", b.dataset.view === v));
  ["setup","today","programme","coach","journal"].forEach(x => $("view-"+x).hidden = (x !== v));
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

function renderAll(){ renderTop(); renderToday(); renderProgramme(); renderCoach(); renderJournal(); }

function renderTop(){
  const {day, left} = dayInfo();
  $("topCount").textContent = left > 0 ? `Day ${day} · ${left} day${left===1?"":"s"} to curtain`
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

  const gates = {7:"Gate day — run the Week 1 checklist with your coach tonight.",
                 14:"Gate day — Week 2 checklist tonight. Programme decisions get made on today's numbers.",
                 21:"Gate day — Week 3 checklist tonight.",
                 26:"Final gate — readiness check with your coach tonight."};
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
    const card = document.createElement("div");
    const flag = FLAGS[b.flag] ? b.flag : null;
    card.className = "card block" + (b.done ? " done" : "") + (flag ? " f-" + flag : "");
    card.innerHTML = `
      <button class="tick" aria-label="mark done">${b.done ? "✓" : ""}</button>
      <div class="b-body">
        <div class="card-head"><h2></h2>
          <span class="head-right">${flag ? `<span class="ftag f-${flag}">${FLAGS[flag]}</span>` : ""}<span class="mins">${b.mins} min</span></span>
        </div>
        <p class="detail"></p>
        <div class="b-actions">
          <button class="timerbtn" data-block="${b.id}"></button>
          <button class="whybtn">why this? →</button>
        </div>
      </div>`;
    card.querySelector("h2").textContent = b.title;
    card.querySelector(".detail").textContent = b.detail;
    card.querySelector(".tick").addEventListener("click", () => toggleBlock(b.id));
    card.querySelector(".whybtn").addEventListener("click", () => {
      switchView("coach");
      $("input").value = `Why am I doing “${b.title}” today — what is it for, and how do I know it's working?`;
      $("input").focus();
    });
    wireTimerButton(card.querySelector(".timerbtn"), b);
    wrap.appendChild(card);
  });
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
    toggleBlockTo(finished, true);
    return;
  }
  document.title = `${fmtMs(left)} · Practice Room`;
  const btn = document.querySelector(`.timerbtn[data-block="${timer.blockId}"]`);
  if (btn){
    const s = state();
    const b = (s.today.blocks || []).find(x => x.id === timer.blockId);
    if (b) paintTimerBtn(btn, b);
  }
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
      <p class="p-note"></p>`;
    div.querySelector("h2").textContent = p.title;
    div.querySelector(".p-note").textContent = p.note || "";
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
    await ghPut(FILES.chat, c => {
      c.messages.push({ role:"user", text, ts: new Date().toISOString() });
      return c;
    }, "message from " + (cfg.name || "pianist"));
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
      if (p) p.textContent = "No reply yet. If this is the first run, make sure the CLAUDE_CODE_OAUTH_TOKEN secret is set in the data repo (README has the steps) — then check the Actions tab for errors.";
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
