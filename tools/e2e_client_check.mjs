// Run from the repo root: node tools/e2e_client_check.mjs
// E2E check: loads the real static/board.js + static/detail.js in a vm
// context with a minimal DOM shim, against the live server on port 8123.
// /api/board and /api/player hit the real API; /api/recommend is canned
// (top-3 remaining by VORP) so the run stays fast — the real recommend
// backend was already verified separately.
import fs from "node:fs";
import vm from "node:vm";

const BASE = "http://127.0.0.1:8123";
const LS_KEY = "nhl-draft-board:kyle_12_points";
const boardJs = fs.readFileSync("static/board.js", "utf8");
const detailJs = fs.readFileSync("static/detail.js", "utf8");

// ── tiny DOM shim ─────────────────────────────────────────────────
class FakeElement {
  constructor(id) {
    this.id = id;
    this._html = "";
    this.textContent = "";
    this.hidden = false;
    this.value = "";
    this.dataset = {};
    this._classes = new Set();
    this._listeners = {};
    this.classList = {
      add: (c) => this._classes.add(c),
      remove: (c) => this._classes.delete(c),
      toggle: (c, f) => (f ? this._classes.add(c) : this._classes.delete(c)),
      contains: (c) => this._classes.has(c),
    };
  }
  get innerHTML() { return this._html; }
  set innerHTML(v) { this._html = String(v); }
  addEventListener(t, fn) { (this._listeners[t] ||= []).push(fn); }
  dispatch(t, ev) { for (const fn of this._listeners[t] || []) fn(ev); }
  querySelector(sel) { if (!this._q) this._q = new FakeElement("q:" + sel); return this._q; }
  querySelectorAll() { return []; }
}

const NO_EVENT = { stopPropagation() {}, preventDefault() {} };
const ev = (m) => ({ target: { closest: (sel) => m[sel] || null }, ...NO_EVENT });

function makeContext(ls) {
  const byId = {};
  for (const id of ["status", "board-head", "board-body", "search", "chips", "reset", "league",
    "recs", "tab-recs", "tab-roster", "tab-drafted", "roster", "draft-log", "round",
    "paster", "paster-list", "paster-status", "player-panel", "panel-close", "panel-body",
    "panel-name", "panel-meta", "uplot-host", "band-note"]) byId[id] = new FakeElement(id);
  const tabEls = ["recs", "roster", "drafted"].map((t) => { const e = new FakeElement("tab-" + t); e.dataset.tab = t; return e; });
  tabEls[0]._classes.add("active"); // matches static HTML
  const destEls = ["taken", "me"].map((d) => { const e = new FakeElement("dest-" + d); e.dataset.dest = d; return e; });
  destEls[0]._classes.add("active"); // matches static HTML
  const docListeners = {};
  const documentShim = {
    getElementById: (id) => byId[id] || null,
    querySelectorAll: (sel) => (sel === ".tab" ? tabEls : sel === ".dest" ? destEls : []),
    addEventListener: (t, fn) => { (docListeners[t] ||= []).push(fn); },
  };
  const window = { location: { search: "" } };
  byId["player-panel"].hidden = true;
  byId["tab-roster"].hidden = true;
  byId["tab-drafted"].hidden = true;
  const ctx = vm.createContext({
    document: documentShim, window, localStorage: ls, fetch: fetchShim, performance,
    setTimeout, clearTimeout, URLSearchParams, URL, console: quietConsole, uPlot: UPlotShim,
    ResizeObserver: ROShim, Math, JSON, Array, Object, String, Number, Set, Map, Promise, Error,
  });
  vm.runInContext(boardJs, ctx, { filename: "board.js" });
  vm.runInContext(detailJs, ctx, { filename: "detail.js" });
  return { ctx, byId, tabEls, destEls, docListeners };
}

// ── shims for browser globals the scripts reference ───────────────
class UPlotShim { constructor() {} setData() {} destroy() {} setSize() {} }
class ROShim { constructor() {} observe() {} disconnect() {} }
const quietConsole = { log: () => {}, warn: () => {}, error: (...a) => console.error(...a) };

class LS {
  constructor() { this.m = new Map(); }
  getItem(k) { return this.m.has(k) ? this.m.get(k) : null; }
  setItem(k, v) { this.m.set(k, String(v)); }
  removeItem(k) { this.m.delete(k); }
}

let boardCache = null;
async function fetchShim(url) {
  const u = new URL(BASE + url);
  if (u.pathname === "/api/recommend") {
    const drafted = new Set((u.searchParams.get("drafted_all") || "").split(",").filter(Boolean).map(Number));
    const recs = boardCache.players
      .filter((p) => !drafted.has(p.id))
      .sort((a, b) => b.vorp - a.vorp || a.id - b.id)
      .slice(0, 3)
      .map((p) => ({ player_id: p.id, name: p.name, marginal_value: p.vorp, reason: "canned: top remaining by VORP" }));
    return { ok: true, status: 200, statusText: "OK", json: async () => ({ league: u.searchParams.get("league"), round: Number(u.searchParams.get("round")), recommendations: recs }) };
  }
  if (u.pathname === "/api/board") {
    if (!boardCache) boardCache = await (await fetch(u)).json();
    return { ok: true, status: 200, statusText: "OK", json: async () => boardCache };
  }
  const res = await fetch(u);
  return { ok: res.ok, status: res.status, statusText: res.statusText, json: async () => res.json() };
}

// ── harness plumbing ──────────────────────────────────────────────
async function waitReady(art, what) {
  const t0 = Date.now();
  for (;;) {
    if (vm.runInContext(what, art.ctx)) return;
    if (Date.now() - t0 > 90000) throw new Error("timeout waiting: " + what);
    await new Promise((r) => setTimeout(r, 200));
  }
}
const rows = (art) => (art.byId["board-body"].innerHTML.match(/<tr data-id="/g) || []).length;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let failures = 0;
function check(label, cond) {
  console.log((cond ? "PASS " : "FAIL ") + label);
  if (!cond) failures++;
}

process.on("unhandledRejection", (e) => { console.error("UNHANDLED REJECTION:", e && e.message || e); failures++; });

// ── Phase 1: fresh load ───────────────────────────────────────────
const ls = new LS();
const art = makeContext(ls);
await waitReady(art, "state.meta !== null && state.players.length > 0 && state.recs !== null && !document.getElementById('recs').classList.contains('loading')");

check("initial: 977 rows on board", rows(art) === 977);
check("initial: round shows Round 1", art.byId["round"].textContent === "Round 1");
check("initial: status has '977 available' + 'Round 1'", art.byId["status"].textContent.includes("977 available") && art.byId["status"].textContent.includes("Round 1"));
check("initial: recs rendered 3 cards", (art.byId["recs"].innerHTML.match(/class="rec"/g) || []).length === 3);
check("initial: roster tab count 0/18", art.tabEls[1].textContent === "My Roster (0/18)");
check("initial: drafted tab count 0", art.tabEls[2].textContent === "Drafted (0)");
check("initial: recs tab is the active one", art.tabEls[0].classList.contains("active") && !art.tabEls[1].classList.contains("active") && !art.byId["tab-recs"].hidden);
check("initial: roster/drafted panels hidden", art.byId["tab-roster"].hidden && art.byId["tab-drafted"].hidden);
check("initial: dest toggle defaults to Taken", art.destEls[0].classList.contains("active") && !art.destEls[1].classList.contains("active"));

// ── Phase 2: draft via board-row "Me" button ──────────────────────
const topId = vm.runInContext("state.players.filter((p) => !state.drafted_all.includes(p.id)).sort((a, b) => b.vorp - a.vorp || a.id - b.id)[0].id", art.ctx);
const topName = vm.runInContext("state.byId.get(" + topId + ").name", art.ctx);
art.byId["board-body"].dispatch("click", ev({ ".draft-btn": { dataset: { id: String(topId), dest: "me" } }, "button, input": {} }));

check("draft-me: board drops to 976 rows", rows(art) === 976);
check("draft-me: player removed from board entirely", !art.byId["board-body"].innerHTML.includes('data-id="' + topId + '"'));
check("draft-me: drafted_by_me has 1", vm.runInContext("state.drafted_by_me.length", art.ctx) === 1);
check("draft-me: drafted_all has 1", vm.runInContext("state.drafted_all.length", art.ctx) === 1);
check("draft-me: log records the action", vm.runInContext("state.log.length === 1 && state.log[0].dest === 'me'", art.ctx) === true);
check("draft-me: round still 1 (1 drafted / 12 teams)", art.byId["round"].textContent === "Round 1");
check("draft-me: tab counts updated", art.tabEls[1].textContent === "My Roster (1/18)" && art.tabEls[2].textContent === "Drafted (1)");
check("draft-me: roster tab shows the player", art.byId["roster"].innerHTML.includes(topName));
check("draft-me: draft-log shows entry tagged Me", art.byId["draft-log"].innerHTML.includes("tag-me") && art.byId["draft-log"].innerHTML.includes(topName));
check("draft-me: panel did NOT open on draft-button click", art.byId["player-panel"].hidden === true);

const saved = JSON.parse(ls.getItem(LS_KEY));
check("draft-me: localStorage v2 persisted", saved.version === 2 && saved.drafted_by_me.length === 1 && saved.log.length === 1 && saved.log[0].dest === "me");

await waitReady(art, "state.recs !== null && !document.getElementById('recs').classList.contains('loading') && state.recs.every((r) => r.player_id !== " + topId + ")");
check("draft-me: recs refetched and drafted player excluded", !art.byId["recs"].innerHTML.includes('data-id="' + topId + '"'));

// ── Phase 3: draft via recommendation card "Taken" button ──────────
const recId = vm.runInContext("state.recs[0].player_id", art.ctx);
art.byId["recs"].dispatch("click", ev({ ".draft-btn": { dataset: { id: String(recId), dest: "taken" } } }));

check("draft-taken: drafted_all has 2, by_me still 1", vm.runInContext("state.drafted_all.length === 2 && state.drafted_by_me.length === 1", art.ctx) === true);
check("draft-taken: tab roster still 1/18", art.tabEls[1].textContent === "My Roster (1/18)");
check("draft-taken: drafted tab now 2", art.tabEls[2].textContent === "Drafted (2)");
check("draft-taken: draft-log has 2 entries, newest tagged Taken first", (art.byId["draft-log"].innerHTML.match(/draft-entry/g) || []).length === 2 && art.byId["draft-log"].innerHTML.indexOf("tag-taken") < art.byId["draft-log"].innerHTML.indexOf("tag-me"));

// ── Phase 4: row click (not a button) opens the detail panel ───────
// use a player that definitely has projection data (top-VORP skater,
// still available after phases 2-3)
const rowId = vm.runInContext("state.recs[1].player_id", art.ctx);
const rowName = vm.runInContext("state.byId.get(" + rowId + ").name", art.ctx);
art.byId["board-body"].dispatch("click", ev({ "tr[data-id]": { dataset: { id: String(rowId) } } }));
// wait for the panel fetch + renderPanel to finish (detail is set before
// renderPanel runs, so also require the loading placeholder to be gone)
await waitReady(art, "!document.getElementById('player-panel').hidden && state.detail !== null && document.getElementById('panel-body').innerHTML.indexOf('panel-loading') === -1");

check("panel: opened on row click", art.byId["player-panel"].hidden === false);
check("panel: shows player name", art.byId["panel-name"].textContent === rowName);
check("panel: breakdown table rendered", art.byId["panel-body"].innerHTML.includes("breakdown"));
check("panel: rank in meta uses renumbered rank", art.byId["panel-meta"].textContent.includes("#"));

art.docListeners["keydown"].forEach((fn) => fn({ key: "Escape" }));
check("panel: Escape closes it", art.byId["player-panel"].hidden === true);

// ── Phase 5: per-entry undo ────────────────────────────────────────
art.byId["draft-log"].dispatch("click", ev({ ".undo-btn": { dataset: { idx: "1" } } }));
check("undo: most recent (taken) entry removed", vm.runInContext("state.log.length === 1 && state.drafted_all.length === 1", art.ctx) === true);
check("undo: player back on board with renumbered rank", rows(art) === 976 && art.byId["board-body"].innerHTML.includes('data-id="' + recId + '"'));
check("undo: tab counts rolled back", art.tabEls[1].textContent === "My Roster (1/18)" && art.tabEls[2].textContent === "Drafted (1)");

// ── Phase 6: Ctrl+Z undoes everything ──────────────────────────────
art.docListeners["keydown"].forEach((fn) => fn({ ctrlKey: true, key: "z", ...NO_EVENT }));
check("ctrl+z: log empty, board back to 977", rows(art) === 977 && vm.runInContext("state.log.length === 0 && state.drafted_all.length === 0", art.ctx) === true);
check("ctrl+z: round back to 1", art.byId["round"].textContent === "Round 1");

// ── Phase 7: paster input (default dest = Taken) ───────────────────
const pasterId = vm.runInContext("state.players.find((p) => !state.drafted_all.includes(p.id)).id", art.ctx);
const pasterName = vm.runInContext("state.byId.get(" + pasterId + ").name", art.ctx);
art.byId["paster"].value = pasterName;
art.byId["paster"].dispatch("input", {});
check("paster: suggestion list populated", (art.byId["paster-list"].innerHTML.match(/paster-item/g) || []).length >= 1);
art.byId["paster"].dispatch("keydown", { key: "Enter" });
check("paster: Enter drafts the exact match as Taken", vm.runInContext("state.drafted_all.length === 1 && state.drafted_by_me.length === 0", art.ctx) === true);
check("paster: status confirms the draft", art.byId["paster-status"].textContent === "Drafted " + pasterName);
check("paster: input cleared after commit", art.byId["paster"].value === "");

// switch dest to Me, draft another via paste
art.destEls[1].dispatch("click", {});
check("paster: dest toggle now active on Me", art.destEls[1].classList.contains("active") && !art.destEls[0].classList.contains("active"));
const pasteId = vm.runInContext("state.players.filter((p) => !state.drafted_all.includes(p.id))[1].id", art.ctx);
const pasteName = vm.runInContext("state.byId.get(" + pasteId + ").name", art.ctx);
art.byId["paster"].value = pasteName;
art.byId["paster"].dispatch("paste", {});
await sleep(20); // paste handler commits via setTimeout
check("paster: paste path drafts as Me", vm.runInContext("state.drafted_by_me.length === 1 && state.drafted_all.length === 2", art.ctx) === true);

// ── Phase 8: fill the roster to the 18-slot limit ──────────────────
let guard = 0;
while (vm.runInContext("state.drafted_by_me.length", art.ctx) < 18 && guard < 20) {
  const id = vm.runInContext("state.players.filter((p) => !state.drafted_all.includes(p.id))[0].id", art.ctx);
  art.byId["board-body"].dispatch("click", ev({ ".draft-btn": { dataset: { id: String(id), dest: "me" } }, "button, input": {} }));
  guard++;
}
check("roster-full: by_me reached 18", vm.runInContext("state.drafted_by_me.length", art.ctx) === 18);
const before = vm.runInContext("state.drafted_all.length", art.ctx);
const nextId = vm.runInContext("state.players.filter((p) => !state.drafted_all.includes(p.id))[0].id", art.ctx);
art.byId["board-body"].dispatch("click", ev({ ".draft-btn": { dataset: { id: String(nextId), dest: "me" }, "button, input": {} } }));
check("roster-full: 19th 'me' pick rejected with status message", vm.runInContext("state.drafted_all.length", art.ctx) === before && art.byId["status"].textContent.includes("Roster is full"));
// (the rejected pick is not logged; now draft that same player as taken)
art.byId["board-body"].dispatch("click", ev({ ".draft-btn": { dataset: { id: String(nextId), dest: "taken" }, "button, input": {} } }));
check("roster-full: 'taken' works when roster full", vm.runInContext("state.drafted_all.length", art.ctx) === before + 1);

const savedFull = JSON.parse(ls.getItem(LS_KEY));
check("roster-full: persistence has 20 logged picks", savedFull.log.length === 20 && savedFull.drafted_all.length === 20 && savedFull.drafted_by_me.length === 18);

// ── Phase 9: reload restores state (same localStorage, fresh DOM) ─
const art2 = makeContext(ls);
await waitReady(art2, "state.meta !== null && state.players.length > 0 && state.recs !== null && !document.getElementById('recs').classList.contains('loading')");
check("reload: restored 20 drafted / 18 mine", vm.runInContext("state.drafted_all.length === 20 && state.drafted_by_me.length === 18 && state.log.length === 20", art2.ctx) === true);
check("reload: board shows 957 available, renumbered ranks", rows(art2) === 957 && vm.runInContext("[...rankOf.values()].every((v, i) => v === i + 1)", art2.ctx) === true);
check("reload: round = floor(20/12)+1 = 2", art2.byId["round"].textContent === "Round 2");
check("reload: tab counts restored", art2.tabEls[1].textContent === "My Roster (18/18)" && art2.tabEls[2].textContent === "Drafted (20)");
const lastName = vm.runInContext("state.byId.get(state.log[state.log.length - 1].player_id).name", art2.ctx);
const firstEntry = art2.byId["draft-log"].innerHTML.split("</li>")[0];
check("reload: draft-log newest first", firstEntry.includes(lastName) && firstEntry.includes("tag-taken"));

// ── Phase 10: legacy (v1) localStorage is discarded ───────────────
const lsOld = new LS();
lsOld.setItem(LS_KEY, JSON.stringify({ drafted: [topId], roster: [topId], log: [] }));
const art3 = makeContext(lsOld);
await waitReady(art3, "state.meta !== null && state.players.length > 0 && state.recs !== null && !document.getElementById('recs').classList.contains('loading')");
check("migration: old-format state discarded, clean start", vm.runInContext("state.drafted_all.length === 0 && state.log.length === 0", art3.ctx) === true && rows(art3) === 977);

// ── Phase 11: corrupt v2 state (duplicate in log) is discarded ────
const lsBad = new LS();
lsBad.setItem(LS_KEY, JSON.stringify({ version: 2, drafted_by_me: [topId], drafted_all: [topId], log: [{ player_id: topId, dest: "me" }, { player_id: topId, dest: "me" }] }));
const art4 = makeContext(lsBad);
await waitReady(art4, "state.meta !== null && state.players.length > 0 && state.recs !== null && !document.getElementById('recs').classList.contains('loading')");
check("migration: corrupt v2 state discarded, clean start", vm.runInContext("state.drafted_all.length === 0 && state.log.length === 0", art4.ctx) === true && rows(art4) === 977);

console.log(failures === 0 ? "\nALL E2E CHECKS PASSED" : "\n" + failures + " E2E CHECK(S) FAILED");
process.exit(failures === 0 ? 0 : 1);
