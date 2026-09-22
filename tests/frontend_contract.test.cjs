/* Synthetic browser-contract checks. No live server, files or coach writes. */
const { test } = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const source = fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8");

function fixture() {
  const storage = new Map(),
    elements = {};
  const context = vm.createContext({
    console,
    Date,
    Intl,
    Math,
    JSON,
    Number,
    String,
    Array,
    Map,
    Set,
    Promise,
    crypto,
    localStorage: {
      getItem: (key) => storage.get(key) || null,
      setItem: (key, v) => storage.set(key, String(v)),
      removeItem: (key) => storage.delete(key),
    },
    window: { addEventListener() {}, scrollTo() {}, innerWidth: 1200 },
    document: {
      getElementById: (id) => elements[id],
      activeElement: { tagName: "BODY", closest: () => null },
    },
    requestAnimationFrame: (fn) => fn(),
    setInterval() {},
    clearInterval() {},
    setTimeout,
    fixtureElements: elements,
  });
  vm.runInContext(source, context);
  const run = (code) => vm.runInContext(code, context);
  run(`sessionsDoc={sessions:[],sync:{status:'current'}};offline=false;`);
  return { context, run, elements, storage };
}

test("start eligibility follows time, freshness, booking state and authoritative block permission", () => {
  const { context, run } = fixture();
  context.session = {
    id: "fixture",
    start: new Date(Date.now() - 60000).toISOString(),
    end: new Date(Date.now() + 3600000).toISOString(),
    bookingStatus: "confirmed",
  };
  assert.equal(run('canStart(session,{status:"planned",canStart:true})'), true);
  assert.equal(
    run('canStart(session,{status:"planned",canStart:false})'),
    false,
  );
  assert.equal(
    run('sessionsDoc.sync.status="stale";canStart(session,{canStart:true})'),
    false,
  );
  assert.equal(
    run(
      'sessionsDoc.sync.status="current";offline=true;canStart(session,{canStart:true})',
    ),
    false,
  );
  run("offline=false");
  context.session.start = new Date(Date.now() + 60000).toISOString();
  assert.equal(run("canStart(session,{canStart:true})"), false);
  context.session.start = new Date(Date.now() - 60000).toISOString();
  context.session.needsAttention = true;
  assert.equal(run("canStart(session,{canStart:true})"), false);
});

test("active timer uses server-capped end and never automatically completes a block", () => {
  const { context, run, elements } = fixture();
  elements.focusTimer = {};
  elements.timerStatus = {};
  context.block = {
    id: "block",
    status: "active",
    mins: 25,
    end: new Date(Date.now() + 90000).toISOString(),
  };
  run(
    'sessionsDoc.sessions=[{id:"session",blocks:[block]}];focusRef={sessionId:"session",blockId:"block"};tickTimer()',
  );
  assert.match(elements.focusTimer.textContent, /^1:(29|30)$/);
  context.block.end = new Date(Date.now() - 1000).toISOString();
  run("tickTimer()");
  assert.equal(elements.focusTimer.textContent, "0:00");
  assert.equal(context.block.status, "active");
  assert.match(elements.timerStatus.textContent, /finish when ready/);
});

test("paused timing survives a fresh device without local timer state", () => {
  const { context, run, elements } = fixture();
  elements.focusTimer = {};
  elements.timerStatus = {};
  context.block = {
    id: "block",
    status: "paused",
    mins: 25,
    elapsedSeconds: 420,
  };
  run(
    'sessionsDoc.sessions=[{id:"session",blocks:[block]}];focusRef={sessionId:"session",blockId:"block"};tickTimer()',
  );
  assert.equal(elements.focusTimer.textContent, "18:00");
  assert.equal(elements.timerStatus.textContent, "Paused");
});

test("missed and skipped work do not inflate totals or become the next task", () => {
  const { context, run } = fixture();
  context.sample = {
    id: "session",
    room: "Fixture room",
    start: new Date(Date.now() - 60000).toISOString(),
    end: new Date(Date.now() + 3600000).toISOString(),
    bookingStatus: "confirmed",
    bookedMinutes: 60,
    blocks: [
      {
        id: "missed",
        status: "missed",
        kind: "playing",
        mins: 25,
        title: "Missed",
        start: new Date().toISOString(),
      },
      {
        id: "done",
        status: "done",
        done: true,
        kind: "playing",
        mins: 25,
        actualMinutes: 7,
        title: "Done",
        start: new Date().toISOString(),
      },
      {
        id: "skipped",
        status: "skipped",
        kind: "playing",
        mins: 25,
        title: "Skipped",
        start: new Date().toISOString(),
      },
      {
        id: "next",
        status: "planned",
        kind: "playing",
        mins: 10,
        title: "Next",
        start: new Date().toISOString(),
      },
    ],
  };
  assert.equal(run("sessionTotals(sample).playing"), 17);
  const html = run("sessionDetail(sample)");
  assert.match(html, /data-focus-block="next"/);
  assert.doesNotMatch(html, /data-focus-block="missed"/);
  assert.match(html, /Playing · Missed/);
});

test("saving an observation preserves text typed while the request is in flight", async () => {
  const { context, run, storage } = fixture();
  const input = {
      value: "First note",
      events: {},
      addEventListener(name, fn) {
        this.events[name] = fn;
      },
    },
    save = {
      events: {},
      addEventListener(name, fn) {
        this.events[name] = fn;
      },
    },
    result = {};
  const row = {
    dataset: { noteBlock: "block", noteSession: "session" },
    querySelector: (selector) =>
      selector === "input" ? input : selector === ".note-save" ? save : result,
  };
  context.noteRoot = { querySelectorAll: () => [row] };
  context.release = null;
  run(
    'sessionsDoc.sessions=[{id:"session",date:"2026-09-22",blocks:[{id:"block",title:"Fixture"}]}];api=()=>new Promise(resolve=>{release=resolve});saveCache=()=>{};wireNotes(noteRoot)',
  );
  input.events.input();
  const pending = save.events.click();
  input.value = "A newer note";
  input.events.input();
  run('release({observation:{id:"saved",text:"First note"}})');
  await pending;
  assert.equal(input.value, "A newer note");
  assert.equal(
    JSON.parse(storage.get("practice-room-note-drafts-v1")).block.text,
    "A newer note",
  );
  assert.match(result.textContent, /new draft is kept/);
});

test("refresh does not replace a note editor while the pianist is typing", () => {
  const { context, run } = fixture();
  context.counts = { week: 0, today: 0 };
  run(
    'document.activeElement={tagName:"INPUT",closest:()=>({closest:selector=>selector==="#view-week"})};renderWeek=()=>counts.week++;renderToday=()=>counts.today++;renderProgramme=()=>{};renderCoach=()=>{};renderJournal=()=>{};renderYear=()=>{};tickTimer=()=>{};renderAll()',
  );
  assert.equal(context.counts.week, 0);
  assert.equal(context.counts.today, 1);
});

test("chat acceptance preserves a newer composer draft", async () => {
  const { context, run, elements, storage } = fixture();
  elements.input = { value: "Submitted text" };
  elements.send = {};
  run(
    "docs[FILES.chat]={obj:{messages:[]}};api=()=>new Promise(resolve=>{release=resolve});renderCoach=()=>{};startPolling=()=>{}",
  );
  const pending = run("sendMessage()");
  elements.input.value = "Newer unsent text";
  storage.set("practice-room-chat-draft", "Newer unsent text");
  run(
    'release({job:{id:"job",state:"queued",message:{id:"message",text:"Submitted text",role:"user"}}})',
  );
  await pending;
  assert.equal(elements.input.value, "Newer unsent text");
  assert.equal(storage.get("practice-room-chat-draft"), "Newer unsent text");
});

test("provisional months and shared deadlines are rendered from data", () => {
  const { run } = fixture();
  assert.equal(run('monthLabel("2027-10")'), "October 2027");
  run(
    'academic.deadlines=[{id:"first",month:"2027-02"},{id:"final",month:"2027-05"}]',
  );
  assert.equal(
    run('pieceDeadlineLabel({deadlineIds:["first","final"]})'),
    "February 2027 + May 2027",
  );
});

test("coach history is partitioned at the academic-year boundary in London time without changing messages", () => {
  const { context, run } = fixture();
  context.messages = [
    { ts: "2026-09-21T22:59:00Z", text: "Earlier programme" },
    { ts: "2026-09-21T23:00:00Z", text: "First message of the new UK day" },
    { ts: "2026-09-22T12:00:00Z", text: "Current practice" },
    { ts: "invalid", text: "Uncertain date" },
    { text: "No date recorded" },
  ];
  const original = JSON.stringify(context.messages);
  assert.equal(
    run('conversationGroups(messages,"2026-09-22").current.length'),
    2,
  );
  assert.equal(
    run('conversationGroups(messages,"2026-09-22").previous.length'),
    3,
  );
  assert.equal(
    run('conversationGroups(messages,"2026-09-22").current[0].text'),
    "First message of the new UK day",
  );
  assert.equal(JSON.stringify(context.messages), original);
});

test("earlier coach messages render only in a collapsed archive with a current-year empty prompt", () => {
  const { context, run, elements } = fixture();
  class Element {
    constructor() {
      this.children = [];
      this.open = false;
      this.scrollHeight = 0;
      this.scrollTop = 0;
      this.clientHeight = 500;
    }
    set innerHTML(value) {
      this.html = value;
      this.children = [];
    }
    get innerHTML() {
      return this.html || "";
    }
    appendChild(child) {
      this.children.push(child);
      return child;
    }
    append(...children) {
      this.children.push(...children);
    }
    querySelector(selector) {
      return (
        this.children.find((child) => selector === "." + child.className) ||
        null
      );
    }
    querySelectorAll() {
      return [];
    }
  }
  elements.thread = new Element();
  context.makeElement = () => new Element();
  run(
    'document.createElement=makeElement;academic.startDate="2026-09-22";docs[FILES.chat]={obj:{messages:[{ts:"2026-08-01T12:00:00Z",text:"Old programme instruction"}]}};bubble=message=>({className:"msg",text:message.text});renderCoach()',
  );
  const archive = elements.thread.children[0];
  assert.equal(archive.className, "conversation-archive");
  assert.equal(archive.open, false);
  assert.match(
    archive.children[0].textContent,
    /Previous programme conversations/,
  );
  assert.equal(
    archive.children[2].children[0].text,
    "Old programme instruction",
  );
  assert.equal(
    elements.thread.children[1].className,
    "empty-panel current-year-coach",
  );
  assert.match(elements.thread.children[1].innerHTML, /CURRENT ACADEMIC YEAR/);
  assert.equal(run("chat().messages.length"), 1);
  archive.open = true;
  run("renderCoach()");
  assert.equal(elements.thread.children[0].open, true);
});


test('background refresh releases the refresh control after completion',async()=>{
  const {run,elements}=fixture();
  elements.banner={textContent:''};
  run('loadAll=async()=>{};renderAll=()=>{};var syncStates=[];renderSync=()=>syncStates.push(refreshing)');
  await run('refreshQuiet()');
  assert.equal(run('refreshing'),false);
  assert.equal(run('syncStates.at(-1)'),false);
});
