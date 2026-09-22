/* B: a short practice list, a piece notebook and expandable preparation routes. */
let notebook = { notes: [], tasks: [], routes: [], history: [], revision: 0 },
  notebookError = "", noteSaving = false;
const NOTE_DRAFTS = "practice-room-free-notes-v1", NOTE_CHOICE = "practice-room-note-choice-v1";
let noteChoice = readLocal(NOTE_CHOICE, { pieceId: "", movementId: "" });
const expandedStages = new Set(), expandedPractice = new Set(), expandedSections = new Set(), expandedPieces = new Set();

function activePieces() {
  return (state().pieces || []).filter(p => p.active !== false && p.status !== "retired");
}
function practicePiece(id) { return activePieces().find(p => p.id === id); }
function noteKey(choice = noteChoice) { return `${choice.pieceId}:${choice.movementId || ""}`; }
function taskById(id) { return (notebook.tasks || []).find(t => t.id === id); }
function stageById(id) { return (notebook.routes || []).flatMap(r => r.stages).find(s => s.id === id); }
function noteTime(n) { return n.createdAt || n.ts || n.date || ""; }
function notesForPiece(id) {
  return [...(notebook.notes || []), ...(notebook.history || [])].filter(n => n.pieceId === id)
    .sort((a,b) => noteTime(b).localeCompare(noteTime(a)));
}
function noteRows(notes) {
  return notes.map(n => `<article class="saved-note"><p>${esc(n.text)}</p><small>${esc([n.bars ? `Bars ${n.bars}` : "", n.movement, noteTime(n) ? new Date(noteTime(n)).toLocaleDateString("en-GB", {day:"numeric",month:"short"}) : ""].filter(Boolean).join(" · "))}</small></article>`).join("");
}
function notebookProblem() {
  return notebookError ? `<div class="inline-error" role="status">${esc(notebookError)} <button class="text-button" data-notebook-retry>Retry</button></div>` : "";
}
function taskButtons(t) {
  return `<div class="task-actions"><button class="text-button" data-task-edit="${esc(t.id)}">Edit</button><button class="text-button" data-task-state="dismissed" data-task-id="${esc(t.id)}">Dismiss</button><button class="text-button" data-task-state="resolved" data-task-id="${esc(t.id)}">Improved</button></div>`;
}
function followupRow(t, compact=false) {
  const p = practicePiece(t.pieceId);
  return `<details class="practice-item" data-practice-id="${esc(t.id)}" ${expandedPractice.has(t.id) ? "open" : ""}><summary><span><strong>${esc(p?.short || p?.title || "Piece")}</strong><span>${esc(t.title)}</span></span><span class="task-duration">${fmt(t.minutes)}<span class="expand-icon" aria-hidden="true">⌄</span></span></summary><div class="practice-item-body"><p>${esc(t.instruction)}</p>${t.notBefore > ukDate() ? `<small>From ${esc(dateLabel(t.notBefore))}</small>` : ""}${taskButtons(t)}${!compact ? `<button class="text-button" data-note-piece="${esc(t.pieceId)}" data-note-movement="${esc(t.movementId || "")}">Write a note</button>` : ""}</div></details>`;
}
function renderPracticeList(date, rows, next) {
  const now = new Date();
  const scheduled = rows.flatMap(s => (s.blocks || []).filter(b => ["playing", "study"].includes(b.kind) && ["planned", "active", "paused"].includes(b.status) && (new Date(b.end) > now || b.status !== "planned")).map(b => ({s,b})));
  const preview = scheduled.slice(0, 4);
  const tasks = (notebook.tasks || []).filter(t => t.status === "open" && t.notBefore <= date && practicePiece(t.pieceId));
  const room = next ? `${clockLabel(next.start)}–${clockLabel(next.end)} · ${next.room}` : "";
  let html = `<div class="list-heading"><h2>${scheduled.length ? "Next up" : "Your practice"}</h2>${help("The list is guidance. Choose any piece and save notes at any time; room bookings only shape the scheduled time.", "About today's list")}</div>${room ? `<p class="room-line">${esc(room)}</p>` : ""}${notebookProblem()}`;
  if (preview.length) {
    const occurrences = new Map();
    html += preview.map(({s,b}) => {
      const t = taskById(b.taskId), p = practicePiece(b.pieceId);
      const base = `${s.id}:${b.taskId || `${b.pieceId}:${b.movementId}:${b.kind}`}`;
      const occurrence = occurrences.get(base) || 0; occurrences.set(base, occurrence+1);
      const viewId = `${base}:${occurrence}`;
      const target = t?.title || b.movement || p?.planning?.focus || "Continue the next section";
      return `<details class="practice-item" data-practice-id="${esc(viewId)}" ${expandedPractice.has(viewId) ? "open" : ""}><summary><span><strong>${esc(p?.short || p?.title || b.title)}</strong><span>${esc(target)}</span></span><span class="task-duration">${fmt(b.mins)}<span class="expand-icon" aria-hidden="true">⌄</span></span></summary><div class="practice-item-body">${instructions(b)}<div class="task-actions">${canStart(s,b) || ["active","paused"].includes(b.status) ? button(b.status === "planned" ? "Start" : "Open session", `data-focus-session="${esc(s.id)}" data-focus-block="${esc(b.id)}"`) : ""}<button class="text-button" data-note-piece="${esc(b.pieceId)}" data-note-movement="${esc(b.movementId || "")}">Write a note</button></div>${t ? taskButtons(t) : ""}</div></details>`;
    }).join("");
    if (scheduled.length > 4) html += `<button class="text-button" data-switch="week">View all ${scheduled.length} planned tasks</button>`;
  } else if (tasks.length) html += tasks.slice(0,4).map(t => followupRow(t)).join("");
  else html += `<p class="quiet-empty">Choose a piece and start where you need to.</p>`;
  html += `<button class="button secondary choose-piece" data-choose-piece>Choose another piece</button>`;
  $("todayContent").innerHTML = html;
}

function renderNotebook(force = false) {
  if (!$("notePiece")) return;
  const pieces = activePieces();
  if (!pieces.some(p => p.id === noteChoice.pieceId)) noteChoice = {pieceId: pieces[0]?.id || "", movementId: ""};
  const piece = practicePiece(noteChoice.pieceId);
  const editing = document.activeElement?.closest?.(".quick-notebook");
  if (!editing && !noteSaving) {
    $("notePiece").innerHTML = pieces.map(p => `<option value="${esc(p.id)}">${esc(p.short || p.title)}</option>`).join("");
    $("notePiece").value = noteChoice.pieceId;
    renderMovementChoice(piece);
    restoreFreeDraft();
  }
  const notes = notesForPiece(noteChoice.pieceId);
  $("pieceNotes").innerHTML = notes.length ? noteRows(notes) : '<p class="quiet-empty">No notes yet.</p>';
  const tasks = (notebook.tasks || []).filter(t => practicePiece(t.pieceId));
  const open = tasks.filter(t => t.status === "open");
  const closed = tasks.filter(t => t.status !== "open");
  if ($("taskLibrary") && (force || !document.activeElement?.closest?.("#taskLibrary"))) $("taskLibrary").innerHTML = open.map(t => followupRow(t,true)).join("") +
    (closed.length ? `<details class="closed-tasks"><summary>Dismissed & improved (${closed.length})</summary>${closed.map(t => `<div class="closed-task"><span>${esc(t.title)}<small>${t.status === "resolved" ? "Improved" : "Dismissed"}</small></span><button class="text-button" data-task-state="open" data-task-id="${esc(t.id)}">Restore</button></div>`).join("")}</details>` : "") +
    (!tasks.length ? '<p class="quiet-empty">Future work from your notes will appear here.</p>' : "");
}
function renderMovementChoice(piece) {
  const movements = piece?.movements || [];
  if (!movements.some(m => m.id === noteChoice.movementId)) noteChoice.movementId = "";
  $("noteMovement").innerHTML = '<option value="">Whole piece</option>' + movements.map(m => `<option value="${esc(m.id)}">${esc(m.title)}</option>`).join("");
  $("noteMovement").value = noteChoice.movementId;
  $("noteMovement").hidden = $("noteMovementLabel").hidden = !movements.length;
  $("freeNoteSave").disabled = !piece || noteSaving;
}
function restoreFreeDraft() {
  const draft = readLocal(NOTE_DRAFTS, {})[noteKey()] || {};
  $("freeNoteText").value = draft.text || "";
  $("freeNoteBars").value = draft.bars || "";
}
function captureFreeDraft() {
  const all = readLocal(NOTE_DRAFTS, {}), prior = all[noteKey()];
  const text = $("freeNoteText").value, bars = $("freeNoteBars").value;
  const draft = {text, bars, clientId: prior?.text === text && prior?.bars === bars ? prior.clientId : crypto.randomUUID()};
  all[noteKey()] = draft;
  try {
    localStorage.setItem(NOTE_DRAFTS, JSON.stringify(all));
    $("freeNoteResult").textContent = "";
  } catch { $("freeNoteResult").textContent = "Could not keep a local draft. Leave this page open until saved."; }
  return draft;
}
async function saveFreeNote() {
  if (noteSaving || !$("freeNoteText").value.trim()) return;
  const choice = {...noteChoice}, key = noteKey(choice), draft = captureFreeDraft();
  noteSaving = true;
  $("freeNoteSave").disabled = true;
  $("freeNoteResult").textContent = "Saving…";
  try {
    const result = await api("/api/notebook/note", {...choice, text: draft.text.trim(), bars: draft.bars.trim(), clientId: draft.clientId});
    notebook = result.notebook;
    notebookError = "";
    const all = readLocal(NOTE_DRAFTS, {});
    const unchanged = all[key]?.clientId === draft.clientId && (noteKey() !== key || ($("freeNoteText").value === draft.text && $("freeNoteBars").value === draft.bars));
    if (unchanged) {
      delete all[key]; saveLocal(NOTE_DRAFTS, all);
      if (noteKey() === key) { $("freeNoteText").value = ""; $("freeNoteBars").value = ""; }
    }
    $("freeNoteResult").textContent = unchanged ? "Saved" : "Saved. Your new draft is kept.";
    saveCache();
    await refreshQuiet();
  } catch (error) { $("freeNoteResult").textContent = `${error.message} Tap Save note to retry.`; }
  finally { noteSaving = false; $("freeNoteSave").disabled = false; renderNotebook(); }
}
function chooseNotePiece(id, movementId = "", focus = true) {
  if (!practicePiece(id)) return;
  noteChoice = {pieceId:id, movementId};
  saveLocal(NOTE_CHOICE, noteChoice);
  switchView("today");
  $("notePiece").value = id;
  renderMovementChoice(practicePiece(id)); restoreFreeDraft(); renderNotebook();
  $("freeNoteResult").textContent = "";
  if (focus) { $("freeNoteText").focus(); $("freeNoteText").scrollIntoView({block:"center",behavior:"smooth"}); }
}

function timelineMonths() {
  const stages = (notebook.routes || []).flatMap(r => r.stages);
  const start = [academic.startDate || ukDate(), ...stages.map(s => s.startDate)].sort()[0].slice(0,7);
  const end = [start, ...stages.map(s => s.endDate.slice(0,7))].sort().at(-1);
  const months = [];
  let d = dateObj(start + "-01");
  while (d.toISOString().slice(0,7) <= end && months.length < 36) {
    months.push(d.toISOString().slice(0,7)); d.setUTCMonth(d.getUTCMonth()+1);
  }
  return months;
}
function stageDetails(route, s) {
  const taskList = (notebook.tasks || []).filter(t => t.pieceId === route.pieceId && t.status === "open" && (!t.movementId || s.movementIds.includes(t.movementId)));
  const notes = notesForPiece(route.pieceId).filter(n => !n.movementId || s.movementIds.includes(n.movementId)).slice(0,3);
  return `<div class="stage-content"><div class="stage-goals"><div><h3>Goal</h3><p>${esc(s.focus)}</p></div><div><h3>Checkpoint</h3><p>${esc(s.checkpoint)}</p></div></div>${s.targets?.length ? `<details class="stage-movements"><summary>Movement priorities</summary>${s.targets.map(m => `<div><strong>${esc(m.title)}</strong><p>${esc(m.focus)}</p>${m.checkpoint ? `<p class="muted">Check: ${esc(m.checkpoint)}</p>` : ""}</div>`).join("")}</details>` : ""}${taskList.length ? `<details><summary>Related practice tasks (${taskList.length})</summary>${taskList.map(t => followupRow(t,true)).join("")}</details>` : ""}${notes.length ? `<details><summary>Recent notes</summary>${noteRows(notes)}</details>` : ""}<div class="task-actions"><button class="text-button" data-stage-edit="${esc(s.id)}">Edit stage</button><button class="text-button" data-note-piece="${esc(route.pieceId)}">Write a note</button></div></div>`;
}
function stageDate(s) {
  const opts = {day:"numeric",month:"short"};
  return `${dateLabel(s.startDate,opts)} – ${dateLabel(s.endDate,opts)}`;
}
function renderTimeline() {
  document.querySelectorAll?.("#pieces details[data-section-key]").forEach(el => {
    if (el.open) expandedSections.add(el.dataset.sectionKey); else expandedSections.delete(el.dataset.sectionKey);
  });
  const months = timelineMonths();
  const ds = deadlines();
  $("deadlineOverview").innerHTML = ds.map(d => `<button class="deadline-link" data-open-dates><span>${esc(deadlineName(d))}</span><strong>${esc(d.date ? dateLabel(d.date) : monthLabel(deadlineMonth(d)))}</strong>${!d.date ? '<small>Date to confirm</small>' : ""}</button>`).join("");
  const routes = (notebook.routes || []).filter(r => pieceFilter === "all" || r.deadlineMonth === pieceFilter);
  const pieces = activePieces().filter(p => routes.some(r => r.pieceId === p.id) || pieceFilter === "all");
  const monthHead = `<div class="timeline-header"><span>Piece / preparation stage</span><div class="month-grid" style="--months:${months.length}">${months.map(m => `<span>${esc(dateLabel(m+"-01",{month:"short"}))}</span>`).join("")}</div></div>`;
  const rows = pieces.map(p => {
    const ownRoutes = routes.filter(r => r.pieceId === p.id);
    const current = ownRoutes.flatMap(r => r.stages).find(s => s.startDate <= ukDate() && s.endDate >= ukDate());
    const dueText = [...new Set(ownRoutes.map(r => dateLabel(r.deadlineMonth+"-01",{month:"short"})))].join(" / ");
    return `<article class="timeline-piece ${expandedPieces.has(p.id) ? "piece-expanded" : ""}" data-timeline-piece="${esc(p.id)}"><div class="timeline-piece-title"><h2>${esc(p.short || p.title)}</h2><button class="piece-expand" data-piece-plan="${esc(p.id)}" aria-expanded="${expandedPieces.has(p.id)}" aria-controls="piece-plan-${esc(p.id)}">${esc(p.short || p.title)}<span aria-hidden="true">${expandedPieces.has(p.id) ? "−" : "+"}</span></button><button class="text-button" data-note-piece="${esc(p.id)}">Notes</button></div><p class="phone-route-summary">${esc([current?.title, dueText ? `Due ${dueText}` : "Date to confirm"].filter(Boolean).join(" · "))}</p><div class="piece-plan-body" id="piece-plan-${esc(p.id)}">${ownRoutes.length ? ownRoutes.map(route => {
      const bars = route.stages.map(s => {
        const start = Math.max(0, months.indexOf(s.startDate.slice(0,7))), end = Math.max(start, months.indexOf(s.endDate.slice(0,7)));
        const active = s.startDate <= ukDate() && s.endDate >= ukDate();
        return `<button class="stage-bar ${s.kind === "due" ? "due" : ""} ${active ? "current" : ""}" style="grid-column:${start+1}/${end+2}" data-stage-toggle="${esc(s.id)}" aria-expanded="${expandedStages.has(s.id)}" aria-controls="stage-${esc(s.id)}" aria-label="${esc(`${p.short || p.title}: ${s.title}, ${stageDate(s)}`)}">${esc(s.edited ? s.title : s.label)}<span aria-hidden="true">${expandedStages.has(s.id) ? "−" : "+"}</span></button>`;
      }).join("");
      return `<div class="timeline-lane"><span class="lane-scope">${esc(route.scope ? `Movements ${route.scope}` : "Whole work")}<small>${esc(route.deadlineDate ? dateLabel(route.deadlineDate,{day:"numeric",month:"short"}) : monthLabel(route.deadlineMonth))}</small></span><div class="month-grid stage-track" style="--months:${months.length}">${bars}</div></div><div class="stage-list">${route.stages.map(s => `<details id="stage-${esc(s.id)}" class="stage-detail" data-stage-id="${esc(s.id)}" ${expandedStages.has(s.id) ? "open" : ""}><summary><span>${esc(s.title)}</span><span>${esc(stageDate(s))}</span></summary>${stageDetails(route,s)}</details>`).join("")}</div>`;
    }).join("") : `<p class="quiet-empty">Add a performance window in settings to plan the stages.</p>`}</div></article>`;
  }).join("");
  $("pieces").innerHTML = notebookProblem() + (pieces.length ? monthHead+rows : '<p class="quiet-empty">No repertoire in this window.</p>');
  document.querySelectorAll?.("#pieces .stage-content > details").forEach((el) => {
    const parent = el.closest("[data-stage-id]");
    const key = parent.dataset.stageId + ":" + el.querySelector("summary").textContent;
    el.dataset.sectionKey = key; el.open = expandedSections.has(key);
  });
}

function openTaskEditor(id) {
  const t = taskById(id); if (!t) return;
  for (const [element,field] of Object.entries({taskId:"id",taskRevision:"revision",taskTitle:"title",taskInstruction:"instruction",taskMinutes:"minutes",taskDate:"notBefore",taskKind:"kind"})) $(element).value = t[field];
  $("taskResult").textContent = ""; $("taskDialog").showModal();
}
function openStageEditor(id) {
  const s = stageById(id); if (!s) return;
  for (const [element,field] of Object.entries({stageId:"id",stageTitle:"title",stageStart:"startDate",stageEnd:"endDate",stageFocus:"focus",stageCheckpoint:"checkpoint"})) $(element).value = s[field];
  $("stageRevision").value = notebook.revision;
  $("stageResult").textContent = ""; $("stageDialog").showModal();
}
async function changeTaskState(id, status, control) {
  const t = taskById(id); if (!t) return;
  control.disabled = true;
  try {
    notebook = await api("/api/notebook/task", {id,revision:t.revision,status});
    saveCache(); renderToday(); renderNotebook(true); renderProgramme();
    if (status !== "open") {
      banner("");
      $("banner").hidden = false;
      $("banner").innerHTML = `${status === "dismissed" ? "Task dismissed." : "Marked improved."} <button class="text-button" data-task-state="open" data-task-id="${esc(id)}">Undo</button>`;
    } else banner("");
    await refreshQuiet();
  } catch(e) { banner(e.message,true); }
  finally { control.disabled = false; }
}
function wireNotebook() {
  $("notePiece").addEventListener("change", () => chooseNotePiece($("notePiece").value, "", false));
  $("noteMovement").addEventListener("change", () => {
    noteChoice.movementId = $("noteMovement").value;
    saveLocal(NOTE_CHOICE,noteChoice); restoreFreeDraft(); $("freeNoteResult").textContent = "";
  });
  ["freeNoteText","freeNoteBars"].forEach(id => $(id).addEventListener("input",captureFreeDraft));
  $("freeNoteSave").addEventListener("click",saveFreeNote);
  $("freeNoteText").addEventListener("keydown",e => {if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {e.preventDefault(); saveFreeNote();}});
  document.addEventListener("toggle",e => {
    const el = e.target;
    if (!el.isConnected) return;
    if (el.dataset?.stageId) {
      const id = el.dataset.stageId;
      if (el.open) expandedStages.add(id); else expandedStages.delete(id);
      document.querySelectorAll("[data-stage-toggle]").forEach(b => {if (b.dataset.stageToggle === id) {b.setAttribute("aria-expanded",String(el.open)); b.querySelector("span").textContent = el.open ? "−" : "+";}});
    }
    if (el.dataset?.practiceId) {
      if (el.open) expandedPractice.add(el.dataset.practiceId); else expandedPractice.delete(el.dataset.practiceId);
    }
  },true);
  document.addEventListener("click",e => {
    const t = e.target.closest("button"); if (!t) return;
    if (t.hasAttribute("data-choose-piece")) {$("notePiece").focus(); $("notePiece").scrollIntoView({block:"center",behavior:"smooth"});}
    if (t.dataset.notePiece) chooseNotePiece(t.dataset.notePiece,t.dataset.noteMovement || "");
    if (t.dataset.taskEdit) openTaskEditor(t.dataset.taskEdit);
    if (t.dataset.taskState) changeTaskState(t.dataset.taskId,t.dataset.taskState,t);
    if (t.dataset.stageEdit) openStageEditor(t.dataset.stageEdit);
    if (t.dataset.stageToggle) {
      const el = $("stage-"+t.dataset.stageToggle); el.open = !el.open;
      if (el.open) expandedStages.add(t.dataset.stageToggle); else expandedStages.delete(t.dataset.stageToggle);
      t.setAttribute("aria-expanded",String(el.open));
    }
    if (t.dataset.piecePlan) {
      const id = t.dataset.piecePlan, parent = t.closest(".timeline-piece");
      const open = !expandedPieces.has(id);
      if (open) expandedPieces.add(id); else expandedPieces.delete(id);
      parent.classList.toggle("piece-expanded",open); t.setAttribute("aria-expanded",String(open));
      t.querySelector("span").textContent = open ? "−" : "+";
    }
    if (t.hasAttribute("data-notebook-retry")) refreshQuiet();
    if (t.hasAttribute("data-open-dates")) openSettings();
  });
  $("taskForm").addEventListener("submit",async e => {
    e.preventDefault(); const btn=e.target.querySelector('[type="submit"]'); btn.disabled=true;
    try {
      notebook=await api("/api/notebook/task",{id:$("taskId").value,revision:Number($("taskRevision").value),title:$("taskTitle").value,instruction:$("taskInstruction").value,minutes:Number($("taskMinutes").value),notBefore:$("taskDate").value,kind:$("taskKind").value});
      $("taskDialog").close(); saveCache(); renderAll(); await refreshQuiet();
    } catch(e) {$("taskResult").textContent=e.message;} finally {btn.disabled=false;}
  });
  $("stageForm").addEventListener("submit",async e => {
    e.preventDefault(); const btn=e.target.querySelector('[type="submit"]'); btn.disabled=true;
    try {
      notebook=await api("/api/notebook/stage",{id:$("stageId").value,revision:Number($("stageRevision").value),title:$("stageTitle").value,startDate:$("stageStart").value,endDate:$("stageEnd").value,focus:$("stageFocus").value,checkpoint:$("stageCheckpoint").value});
      $("stageDialog").close(); saveCache(); renderProgramme(); await refreshQuiet();
    } catch(e) {$("stageResult").textContent=e.message;} finally {btn.disabled=false;}
  });
}
