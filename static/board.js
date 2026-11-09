"use strict";
/* NHL Draft Board client: fetches /api/board once per page load.
   All filtering, sorting, drafting, and persistence happen here.

   Draft model:
   - every player is in exactly one of three states: available, mine,
     taken, tracked as drafted_by_me and drafted_all (drafted_all
     includes the own picks; both names match recommend()'s signature).
   - drafted players leave the board entirely; ranks renumber so the
     best available player is always rank 1.
   - an ordered action log [{player_id, dest}] makes every pick undoable
     (per-entry Undo buttons + Ctrl+Z).
   - round = floor(drafted_all.length / team_count) + 1, with
     team_count read from the /api/board payload.
   - recommendations refetch after every state change (draft, undo,
     reset); the previous three stay visible and dim while in flight. */

const LS_PREFIX = "nhl-draft-board:";
const LS_VERSION = 2;
const state = {
  meta: null,
  players: [],
  byId: new Map(),
  drafted_by_me: [],
  drafted_all: [],
  log: [],
  posFilter: new Set(),
  search: "",
  sortKey: "vorp",
  sortDir: "desc",
  recs: null,
};
let rankOf = new Map();    // renumbered rank of the available pool
let pasterDest = "taken"; // name input commits to this destination
let recToken = 0;          // guards stale recommendation responses
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const storageKey = () => LS_PREFIX + state.meta.league;
const norm = (s) => s.toLowerCase().replace(/[^a-z]/g, "");

function saveState() {
  try {
    localStorage.setItem(storageKey(), JSON.stringify({
      version: LS_VERSION,
      drafted_by_me: state.drafted_by_me,
      drafted_all: state.drafted_all,
      log: state.log,
    }));
  } catch { /* storage unavailable: session-only state */ }
}

/* localStorage migration: the stored shape is now {version: 2,
   drafted_by_me, drafted_all, log}. The old shape ({drafted, roster})
   has no version key, so anything that is not version 2 or that
   cross-checks wrong against the log is discarded and we start clean
   rather than misreading it. */
function restoreState() {
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(storageKey())); } catch { saved = null; }
  if (!saved || saved.version !== LS_VERSION) return;
  const okIds = (a) => Array.isArray(a) && a.every((x) => typeof x === "number" && state.byId.has(x));
  const logOk = Array.isArray(saved.log) && saved.log.every(
    (e) => e && typeof e.player_id === "number" && state.byId.has(e.player_id)
      && (e.dest === "me" || e.dest === "taken"));
  if (!okIds(saved.drafted_by_me) || !okIds(saved.drafted_all) || !logOk) return;
  const seen = new Set();
  for (const e of saved.log) { if (seen.has(e.player_id)) return; seen.add(e.player_id); }
  const sameOrder = (a, b) => a.length === b.length && a.every((v, i) => b[i] === v);
  const meFromLog = saved.log.filter((e) => e.dest === "me").map((e) => e.player_id);
  if (!sameOrder(saved.drafted_all, saved.log.map((e) => e.player_id))) return;
  if (!sameOrder(saved.drafted_by_me, meFromLog)) return;
  if (!saved.drafted_by_me.every((id) => saved.drafted_all.includes(id))) return;
  state.log = saved.log.map((e) => ({ player_id: e.player_id, dest: e.dest }));
  state.drafted_all = saved.drafted_all.slice();
  state.drafted_by_me = saved.drafted_by_me.slice();
}

/* Roster slot assignment mirrors src/valuation/league.py: primary
   starter slot first, then UTIL, then BENCH. */
function assignSlot(pos, counts, limits) {
  const primary = { C: "C", L: "LW", R: "RW", D: "D", G: "G" }[pos];
  if (primary && counts[primary] < limits[primary]) return primary;
  if (pos !== "G" && counts.UTIL < limits.UTIL) return "UTIL";
  return "BENCH";
}

function totalSlots() {
  return Object.values(state.meta.roster_slots).reduce((a, b) => a + b, 0);
}

function currentRound() {
  const teamCount = state.meta.team_count || 1;
  return Math.floor(state.drafted_all.length / teamCount) + 1;
}

/* Draft a player into one of the two destinations. Drafted players
   leave the board entirely; every action is logged so it is undoable. */
function draft(id, dest) {
  const p = state.byId.get(id);
  if (!p || state.drafted_all.includes(id)) return false;
  if (dest === "me" && state.drafted_by_me.length >= totalSlots()) {
    $("status").textContent = "Roster is full — undraft a player first";
    return false;
  }
  state.drafted_all.push(id);
  if (dest === "me") state.drafted_by_me.push(id);
  state.log.push({ player_id: id, dest });
  saveState();
  render();
  refetchRecs();
  if (dest === "me") goalLight(p.name);
  return true;
}

/* Goal light: the header flashes red and a banner welcomes the pick.
   The banner is a live region, so screen readers announce it too. */
let goalTimer = null;
function goalLight(name) {
  const bar = document.querySelector(".bar");
  let toast = $("goal-toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "goal-toast";
    toast.className = "goal-toast";
    toast.setAttribute("role", "status");
    document.body.appendChild(toast);
  }
  toast.textContent = `Welcome to Marshall & Co., ${name}`;
  toast.hidden = false;
  bar.classList.add("goal");
  clearTimeout(goalTimer);
  goalTimer = setTimeout(() => { bar.classList.remove("goal"); toast.hidden = true; }, 1800);
}

/* Undo one logged action: return the player to the board (ranks are
   derived from the available pool, so they reappear in the correct
   rank position) and refetch the recommendations. */
function undo(index) {
  const entry = state.log[index];
  if (!entry) return;
  const id = entry.player_id;
  state.drafted_by_me = state.drafted_by_me.filter((x) => x !== id);
  state.drafted_all = state.drafted_all.filter((x) => x !== id);
  state.log.splice(index, 1);
  saveState();
  render();
  refetchRecs();
}

/* Reset clears everything: both draft arrays, the action log, storage. */
function resetDraft() {
  state.drafted_by_me = [];
  state.drafted_all = [];
  state.log = [];
  saveState();
  render();
  refetchRecs();
}

/* ─── Board table ─────────────────────────────────────────────── */

/* Ranks renumber after every draft action: the available pool, sorted
   by VORP desc (ties by id), gets positions 1..N. Best available is
   always rank 1. */
function computeRank() {
  const taken = new Set(state.drafted_all);
  const pool = state.players.filter((p) => !taken.has(p.id));
  pool.sort((a, b) => (b.vorp - a.vorp) || (a.id - b.id));
  rankOf = new Map(pool.map((p, i) => [p.id, i + 1]));
}

function cols() {
  const c = [["rank", "Rank"], ["draft", "Draft"], ["name", "Player"], ["pos", "Pos"], ["team", "Team"], ["vorp", "VORP"], ["vorp_regular", "VORP RS"], ["vorp_playoff", "VORP PO"], ["dollars", "$"]];
  for (const k of state.meta.categories) c.push([k, k]);
  if (state.meta.has_adp) c.push(["adp", "ADP"], ["delta", "+/-"]);
  return c;
}

function headerHtml() {
  return cols().map(([k, label]) => {
    if (k === "draft") return `<th class="draft">Draft</th>`;
    return `<th data-key="${k}" class="${k === "name" ? "name" : ""}">${esc(label)}${state.sortKey === k ? `<span class="caret ${state.sortDir === "asc" ? "up" : "down"}"></span>` : ""}</th>`;
  }).join("");
}

function cellHtml(p, k) {
  if (k === "draft") {
    return `<td class="draft-cell"><button type="button" class="draft-btn" data-dest="me" data-id="${p.id}">Me</button> <button type="button" class="draft-btn" data-dest="taken" data-id="${p.id}">Taken</button></td>`;
  }
  if (k === "name") return `<td class="name">${esc(p.name)}</td>`;
  let v, cls = "";
  if (k === "rank") v = rankOf.get(p.id);
  else if (k === "pos") v = p.pos;
  else if (k === "team") v = p.team;
  else if (k === "vorp") v = p.vorp.toFixed(1);
  else if (k === "vorp_regular") v = p.vorp_regular.toFixed(1);
  else if (k === "vorp_playoff") v = p.vorp_playoff.toFixed(1);
  else if (k === "dollars") v = p.dollars.toFixed(1);
  else if (k === "adp") v = p.adp == null ? "—" : p.adp.toFixed(1);
  else if (k === "delta") { v = p.delta == null ? "—" : (p.delta > 0 ? "+" : "") + p.delta; cls = p.delta > 0 ? " pos" : p.delta < 0 ? " neg" : ""; }
  else v = p.stats[k].toFixed(1);
  return `<td class="${cls}">${v}</td>`;
}

function rowHtml(p) { return `<tr data-id="${p.id}">${cols().map(([k]) => cellHtml(p, k)).join("")}</tr>`; }

function sortValue(p, k) { if (k === "rank") return rankOf.get(p.id); if (k === "name") return p.name.toLowerCase(); if (k in p.stats) return p.stats[k]; return p[k]; }

function sortList(list) {
  const k = state.sortKey, dir = state.sortDir === "asc" ? 1 : -1;
  const strSort = k === "name" || k === "pos" || k === "team";
  return list.slice().sort((a, b) => {
    const va = sortValue(a, k), vb = sortValue(b, k);
    if (va == null && vb == null) return a.id - b.id;
    if (va == null) return 1;
    if (vb == null) return -1;
    return strSort ? (va.localeCompare(vb) * dir || a.id - b.id) : ((va - vb) * dir || a.id - b.id);
  });
}

/* Sort, filter, and search apply to available players only. */
function visiblePlayers() {
  const taken = new Set(state.drafted_all);
  let list = state.players.filter((p) => !taken.has(p.id));
  if (state.posFilter.size > 0) list = list.filter((p) => state.posFilter.has(p.pos));
  if (state.search) { const q = state.search; list = list.filter((p) => fuzzyScore(q, p.name) >= 0.5); }
  return sortList(list);
}

function render() {
  const t0 = performance.now();
  computeRank();
  $("board-head").querySelector("tr").innerHTML = headerHtml();
  $("board-body").innerHTML = visiblePlayers().map(rowHtml).join("");
  renderRoster();
  renderDrafted();
  renderTabCounts();
  const round = currentRound();
  $("round").textContent = `Round ${round}`;
  $("status").textContent =
    `${(state.players.length - state.drafted_all.length).toLocaleString()} available · season ${state.meta.season} · ${state.drafted_all.length} drafted · Round ${round}`;
  window.__lastSortMs = performance.now() - t0;
}

/* MY ROSTER tab: slots from the league config, filled and empty.
   Only drafted_by_me players occupy slots (taken picks do not touch
   the roster). */
function renderRoster() {
  const limits = state.meta.roster_slots;
  const counts = {};
  for (const s of Object.keys(limits)) counts[s] = 0;
  const slotOf = {};
  for (const id of state.drafted_by_me) {
    const p = state.byId.get(id);
    if (!p) continue;
    const slot = assignSlot(p.pos, counts, limits);
    slotOf[id] = slot;
    counts[slot] += 1;
  }
  let html = "";
  for (const slot of Object.keys(limits)) {
    const ids = state.drafted_by_me.filter((id) => slotOf[id] === slot);
    let rows = "";
    for (let i = 0; i < limits[slot]; i++) {
      const id = ids[i];
      const p = id != null ? state.byId.get(id) : null;
      rows += p ? `<div class="slot-player"><span>${esc(p.name)}</span><span class="slot-pos">${esc(p.pos)}</span></div>` : `<div class="slot-player empty">empty</div>`;
    }
    html += `<div class="slot"><div class="slot-head"><span>${esc(slot)}</span><span>${ids.length}/${limits[slot]}</span></div>${rows}</div>`;
  }
  $("roster").innerHTML = html;
}

/* DRAFTED tab: reverse-chronological log of all picks, each tagged me
   or taken, each with its own undo. */
function renderDrafted() {
  const items = [];
  for (let i = state.log.length - 1; i >= 0; i--) {
    const entry = state.log[i];
    const p = state.byId.get(entry.player_id);
    const name = p ? p.name : "Player " + entry.player_id;
    const label = entry.dest === "me" ? "Me" : "Taken";
    items.push(`<li class="draft-entry"><span class="draft-idx">${i + 1}</span><span class="draft-name">${esc(name)}</span><span class="draft-tag ${entry.dest === "me" ? "tag-me" : "tag-taken"}">${label}</span><button type="button" class="undo-btn" data-idx="${i}">Undo</button></li>`);
  }
  $("draft-log").innerHTML = items.length ? items.join("") : `<li class="draft-empty">No picks yet. Tap Me or Taken on any player to log one.</li>`;
}

function renderTabCounts() {
  const total = totalSlots();
  const tabs = document.querySelectorAll(".tab");
  for (const b of tabs) {
    if (b.dataset.tab === "roster") b.textContent = `My Roster (${state.drafted_by_me.length}/${total})`;
    else if (b.dataset.tab === "drafted") b.textContent = `Drafted (${state.log.length})`;
  }
  // phone view switcher: short labels
  for (const b of document.querySelectorAll(".mtab")) {
    if (b.dataset.view === "roster") b.textContent = `Roster ${state.drafted_by_me.length}/${total}`;
    else if (b.dataset.view === "drafted") b.textContent = `Drafted ${state.log.length}`;
  }
}

/* Phones show one view at a time (Board / Picks / Roster / Drafted);
   CSS reads body[data-mview]. The sidebar views reuse showTab(). */
function setMobileView(view) {
  document.body.dataset.mview = view;
  for (const b of document.querySelectorAll(".mtab")) b.classList.toggle("active", b.dataset.view === view);
  if (view !== "board") showTab(view);
}

/* The drill-down panel sits just under the header, whose height changes
   when the controls wrap. */
function trackHeaderHeight() {
  const bar = document.querySelector(".bar");
  const set = () => document.documentElement.style.setProperty("--bar-h", bar.offsetHeight + "px");
  set();
  if (window.ResizeObserver) new ResizeObserver(set).observe(bar);
}

function showTab(name) {
  const tabs = document.querySelectorAll(".tab");
  for (const b of tabs) b.classList.toggle("active", b.dataset.tab === name);
  for (const tab of ["recs", "roster", "drafted"]) $("tab-" + tab).hidden = tab !== name;
}

/* ─── Recommendations ─────────────────────────────────────────── */

/* GET /api/recommend, all parameters in the query string. Refetched
   after every state change (draft, undo, reset). While in flight the
   previous three stay visible and dim — the panel is never blanked. */
function refetchRecs() {
  const token = ++recToken;
  const params = new URLSearchParams();
  params.set("league", state.meta.league);
  params.set("round", String(currentRound()));
  params.set("drafted_by_me", state.drafted_by_me.join(","));
  params.set("drafted_all", state.drafted_all.join(","));
  $("recs").classList.add("loading");
  fetch(`/api/recommend?${params.toString()}`)
    .then((res) => {
      if (!res.ok) throw new Error(res.statusText);
      return res.json();
    })
    .then((json) => {
      if (token !== recToken) return;
      state.recs = json.recommendations;
      renderRecs();
    })
    .catch(() => {
      if (token !== recToken) return;
      renderRecs(); // keep the previous three visible on failure
    })
    .finally(() => {
      if (token === recToken) $("recs").classList.remove("loading");
    });
}

function renderRecs() {
  const list = $("recs");
  if (!state.recs) {
    list.innerHTML = `<div class="rec-empty">Loading recommendations…</div>`;
    return;
  }
  if (state.recs.length === 0) {
    list.innerHTML = `<div class="rec-empty">No recommendations — every player is drafted.</div>`;
    return;
  }
  list.innerHTML = state.recs.map(recHtml).join("");
}

function recHtml(rec) {
  const p = state.byId.get(rec.player_id);
  const pos = p ? p.pos : "—";
  return `<div class="rec" data-id="${rec.player_id}">
    <div class="rec-head"><span class="rec-name">${esc(rec.name)}</span><span class="rec-pos">${esc(pos)}</span></div>
    <div class="rec-value">${rec.marginal_value.toFixed(1)}</div>
    <div class="rec-reason">${esc(rec.reason)}</div>
    <div class="rec-actions">
      <button type="button" class="draft-btn" data-dest="me" data-id="${rec.player_id}">Me</button>
      <button type="button" class="draft-btn" data-dest="taken" data-id="${rec.player_id}">Taken</button>
    </div>
  </div>`;
}

/* ─── Fuzzy name matching (paster) ─────────────────────────────── */

/* Fuzzy matching: normalized Levenshtein similarity plus
   in-order token containment (so "mcdavid" finds "Connor McDavid"). */
function lev(a, b) {
  const m = a.length, n = b.length;
  if (m === 0) return n;
  if (n === 0) return m;
  let prev = new Array(n + 1), cur = new Array(n + 1);
  for (let j = 0; j <= n; j++) prev[j] = j;
  for (let i = 1; i <= m; i++) { cur[0] = i; for (let j = 1; j <= n; j++) { const cost = a[i - 1] === b[j - 1] ? 0 : 1; cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost); } const tmp = prev; prev = cur; cur = tmp; }
  return prev[n];
}

function fuzzyScore(query, name) {
  const q = norm(query);
  if (!q) return 0;
  const n = norm(name);
  let s = 1 - lev(q, n) / Math.max(q.length, n.length);
  let idx = 0;
  for (const tok of query.toLowerCase().split(/\s+/)) { if (!tok) continue; const found = n.indexOf(tok, idx); if (found < 0) return s; idx = found; }
  return Math.max(s, 0.8);
}

function bestMatch(q, minScore) {
  if (minScore == null) minScore = 0.6;
  const drafted = new Set(state.drafted_all);
  let best = null;
  for (const p of state.players) {
    if (drafted.has(p.id)) continue;
    const s = fuzzyScore(q, p.name);
    if (s >= minScore && (!best || s > best.score)) best = { id: p.id, score: s };
  }
  return best;
}

/* Commit a paster match into one of the two destinations (default
   destination comes from the toggle; Enter / paste / suggestion click
   all funnel through here). The "Drafted …" status is set last, so
   updateSuggestions() cannot clobber it. */
function commitDraft(id) {
  const p = state.byId.get(id);
  if (!p || state.drafted_all.includes(id)) return false;
  if (pasterDest === "me" && state.drafted_by_me.length >= totalSlots()) {
    $("paster-status").textContent = "Roster is full — undraft a player first";
    return false;
  }
  draft(id, pasterDest);
  $("paster").value = "";
  updateSuggestions();
  $("paster-status").textContent = "Drafted " + p.name;
  return true;
}

function updateSuggestions() {
  const input = $("paster"), q = input.value.trim(), list = $("paster-list"), status = $("paster-status");
  if (!q) { list.innerHTML = ""; status.textContent = ""; return; }
  const drafted = new Set(state.drafted_all);
  const scored = [];
  for (const p of state.players) { if (drafted.has(p.id)) continue; const s = fuzzyScore(q, p.name); if (s >= 0.5) scored.push([s, p]); }
  scored.sort((a, b) => b[0] - a[0] || a[1].id - b[1].id);
  const top = scored.slice(0, 5);
  if (top.length === 0) { list.innerHTML = ""; status.textContent = "No match found"; return; }
  list.innerHTML = top.map(([s, p]) => `<div class="paster-item" data-id="${p.id}"><span>${esc(p.name)}</span><span>${esc(p.pos)} · ${esc(p.team)}</span></div>`).join("");
  status.textContent = "";
}

/* ─── Controls and events ─────────────────────────────────────── */

function buildControls() {
  $("league").innerHTML = state.meta.leagues.map((n) => `<option value="${esc(n)}"${n === state.meta.league ? " selected" : ""}>${esc(n)}</option>`).join("");
  const positions = [...new Set(state.players.map((p) => p.pos))].sort();
  $("chips").innerHTML = positions.map((pos) => `<button type="button" class="chip" data-pos="${esc(pos)}">${esc(pos)}</button>`).join("");
}

function setPasterDest(dest) {
  if (dest !== "me" && dest !== "taken") return;
  pasterDest = dest;
  const buttons = document.querySelectorAll(".dest");
  for (const b of buttons) b.classList.toggle("active", b.dataset.dest === dest);
}

function wireEvents() {
  /* Board rows: a click opens the detail panel (wired in detail.js).
     Draft buttons are explicit controls only — stop propagation so the
     panel does not open. */
  $("board-body").addEventListener("click", (e) => {
    const btn = e.target.closest(".draft-btn");
    if (!btn) return;
    e.stopPropagation();
    e.preventDefault();
    draft(Number(btn.dataset.id), btn.dataset.dest);
  });
  $("board-head").addEventListener("click", (e) => {
    const th = e.target.closest("th[data-key]");
    if (!th) return;
    const k = th.dataset.key;
    if (state.sortKey === k) { state.sortDir = state.sortDir === "desc" ? "asc" : "desc"; }
    else { state.sortKey = k; state.sortDir = k === "name" || k === "pos" || k === "team" ? "asc" : "desc"; }
    render();
  });
  let searchTimer = null;
  $("search").addEventListener("input", () => { if (searchTimer) clearTimeout(searchTimer); searchTimer = setTimeout(() => { state.search = $("search").value.trim(); render(); }, 100); });
  $("chips").addEventListener("click", (e) => { const chip = e.target.closest(".chip"); if (!chip) return; const pos = chip.dataset.pos; if (state.posFilter.has(pos)) state.posFilter.delete(pos); else state.posFilter.add(pos); chip.classList.toggle("active", state.posFilter.has(pos)); render(); });
  $("reset").addEventListener("click", resetDraft);
  $("league").addEventListener("change", () => { const v = $("league").value; if (v && v !== state.meta.league) window.location.search = "?league=" + encodeURIComponent(v); });

  /* Recommendations: explicit Me / Taken buttons per player. */
  $("recs").addEventListener("click", (e) => {
    const btn = e.target.closest(".draft-btn");
    if (!btn) return;
    e.stopPropagation();
    e.preventDefault();
    draft(Number(btn.dataset.id), btn.dataset.dest);
  });

  /* DRAFTED tab: per-entry undo. */
  $("draft-log").addEventListener("click", (e) => {
    const btn = e.target.closest(".undo-btn");
    if (!btn) return;
    e.stopPropagation();
    e.preventDefault();
    undo(Number(btn.dataset.idx));
  });

  /* Sidebar tabs. */
  const tabs = document.querySelectorAll(".tab");
  for (const btn of tabs) btn.addEventListener("click", () => showTab(btn.dataset.tab));

  /* Name input with the two-state destination toggle. Default state
     is Taken — most picks are not ours. Commits on Enter. */
  const destButtons = document.querySelectorAll(".dest");
  for (const btn of destButtons) btn.addEventListener("click", () => setPasterDest(btn.dataset.dest));
  const paster = $("paster");
  paster.addEventListener("input", updateSuggestions);
  paster.addEventListener("keydown", (e) => { if (e.key !== "Enter") return; const q = paster.value.trim(); if (!q) return; const best = bestMatch(q); if (best) commitDraft(best.id); else $("paster-status").textContent = "No match found"; });
  paster.addEventListener("paste", () => { setTimeout(() => { const q = paster.value.trim(); if (!q) return; const best = bestMatch(q, 0.8); if (best) commitDraft(best.id); }, 0); });
  $("paster-list").addEventListener("click", (e) => { const item = e.target.closest(".paster-item"); if (!item) return; commitDraft(Number(item.dataset.id)); });

  for (const b of document.querySelectorAll(".mtab")) b.addEventListener("click", () => setMobileView(b.dataset.view));
  trackHeaderHeight();

  /* Ctrl+Z undoes the most recent draft action. */
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
      e.preventDefault();
      if (state.log.length > 0) undo(state.log.length - 1);
    }
  });
}

async function init() {
  const league = new URLSearchParams(window.location.search).get("league");
  const url = "/api/board" + (league ? "?league=" + encodeURIComponent(league) : "");
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    const payload = await res.json();
    state.meta = payload;
    state.players = payload.players;
    for (const p of payload.players) state.byId.set(p.id, p);
    restoreState();
    buildControls();
    wireEvents();
    render();
    refetchRecs();
  } catch (err) { $("status").textContent = "Failed to load board: " + err.message; }
}

init();
