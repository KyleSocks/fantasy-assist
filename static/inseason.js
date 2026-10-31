// Mid-season assistant page: roster audit, streaming plan, free agents,
// schedule and the editable roster / free-agent lists. Talks to
// /api/inseason/* (src/api/inseason.py).
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const today = new Date(Date.now() - new Date().getTimezoneOffset() * 60000)
    .toISOString().slice(0, 10);
  let rosterPlayers = [];
  let unit = "fpts";

  // ── helpers ──────────────────────────────────────────────────────────
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const num = (v, d = 2) => (v == null ? "–" : Number(v).toFixed(d));
  const signed = (v, d = 1) => (v == null ? "–" : (v >= 0 ? "+" : "") + Number(v).toFixed(d));
  const pct = (v) => (v == null ? "–" : (v >= 0 ? "+" : "") + Math.round(v * 100) + "%");
  const cls = (v) => (v == null ? "" : v > 0 ? "pos" : v < 0 ? "neg" : "");
  const mmss = (s) => (s == null ? "–" : `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`);
  const md = (d) => d.slice(5).replace("-", "/");
  const dow = (d) => new Date(d + "T12:00:00").toLocaleDateString(undefined, { weekday: "short" });

  function status(msg, err = false) {
    const el = $("status");
    el.textContent = msg || "";
    el.className = "status" + (err ? " err" : "");
  }

  function params(extra = {}) {
    const p = new URLSearchParams({
      league: $("league").value, start: $("start").value, days: $("days").value, ...extra,
    });
    return p.toString();
  }

  async function api(path, opts) {
    const res = await fetch(path, opts);
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || `${res.status} ${res.statusText}`);
    return body;
  }

  // Sortable table: cols = [{key, label, fmt(row) -> html, sort(row) -> value, left}]
  function table(el, cols, rows, state = el._sort || {}) {
    el._sort = state;
    const sorted = rows.slice();
    if (state.key) {
      const col = cols.find((c) => c.key === state.key);
      const val = col.sort || ((r) => r[col.key]);
      sorted.sort((a, b) => {
        const x = val(a), y = val(b);
        if (x == null) return 1;
        if (y == null) return -1;
        return (x < y ? -1 : x > y ? 1 : 0) * state.dir;
      });
    }
    el.innerHTML =
      "<thead><tr>" + cols.map((c) =>
        `<th class="${c.left ? "l" : ""}" data-key="${c.key}" title="${esc(c.title || "")}">${esc(c.label)}${
          state.key === c.key ? (state.dir > 0 ? " ▲" : " ▼") : ""}</th>`).join("") +
      "</tr></thead><tbody>" + sorted.map((r) => "<tr>" + cols.map((c) =>
        `<td class="${c.left ? "l" : ""}">${c.fmt ? c.fmt(r) : esc(r[c.key])}</td>`).join("") +
      "</tr>").join("") + "</tbody>";
    el.querySelectorAll("th").forEach((th) => th.addEventListener("click", () => {
      const k = th.dataset.key;
      table(el, cols, rows, { key: k, dir: state.key === k ? -state.dir : -1 });
    }));
  }

  const verdict = (v) => `<span class="verdict ${esc(v.split(" ")[0])}">${esc(v)}</span>`;
  const flags = (r) => (r.flags || []).filter((f) => !/^(hold|borderline|drop)/.test(f))
    .map((f) => `<span class="flag">${esc(f)}</span>`).join("");

  // Columns shared by the roster and free-agent tables.
  function playerCols(extra = []) {
    return [
      { key: "name", label: "Player", left: true, fmt: (r) => `${esc(r.name)}${flags(r)}` },
      { key: "team", label: "Tm", left: true },
      { key: "elig", label: "Pos", left: true, fmt: (r) => esc(r.elig.join(",")), sort: (r) => r.elig.join(",") },
      { key: "gp", label: "GP" },
      { key: "prior", label: "Pre", title: `preseason ${unit}/game`, fmt: (r) => num(r.prior) },
      { key: "now", label: "Now", title: `this season ${unit}/game`, fmt: (r) => num(r.now) },
      { key: "vs_prior_pct", label: "vs pre", fmt: (r) => `<span class="${cls(r.vs_prior_pct)}">${pct(r.vs_prior_pct)}</span>` },
      { key: "blended", label: "Blend", title: "blended per-game value used by every decision", fmt: (r) => `<b>${num(r.blended)}</b>` },
      { key: "toi_prior", label: "TOI pre", title: "EV TOI per game", fmt: (r) => mmss(r.toi_prior) },
      { key: "toi_now", label: "TOI now", fmt: (r) => {
          const d = r.toi_now != null && r.toi_prior != null ? r.toi_now - r.toi_prior : null;
          return `<span class="${d != null && d <= -90 ? "neg" : ""}">${mmss(r.toi_now)}</span>`; } },
      { key: "rank", label: "Rank", title: "rank in F / D by blended value", fmt: (r) => `${r.rank}/${r.pool}` },
      { key: "ros_vs_repl", label: "ROS vs repl", title: "rest of season vs a replacement player on an average schedule",
        fmt: (r) => `<span class="${cls(r.ros_vs_repl)}">${signed(r.ros_vs_repl)}</span>` },
      ...extra,
      { key: "verdict", label: "Verdict", left: true, fmt: (r) => verdict(r.verdict) },
    ];
  }

  // ── tabs ─────────────────────────────────────────────────────────────
  const loaders = {};
  let current = "roster";
  document.querySelectorAll(".ins-tabs .tab").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll(".ins-tabs .tab").forEach((x) => x.classList.toggle("active", x === b));
    current = b.dataset.tab;
    document.querySelectorAll(".panel").forEach((p) => { p.hidden = p.id !== `tab-${current}`; });
    load();
  }));
  function load() { status(""); return Promise.resolve((loaders[current] || (() => {}))()); }

  // ── roster ───────────────────────────────────────────────────────────
  loaders.roster = async () => {
    status("Loading roster…");
    try {
      const a = await api(`/api/inseason/audit?${params()}`);
      unit = a.unit;
      $("asof").textContent = `stats as of ${a.as_of}`;
      rosterPlayers = a.players;
      table($("roster-table"), playerCols([
        { key: "window_games", label: "Games", title: "team games in the window" },
        { key: "starts", label: "Starts", title: "games he'd actually start in your best lineup" },
        { key: "lineup_adds", label: "Adds", title: `${unit} he adds to your nightly lineup over the window`,
          fmt: (r) => `<b>${num(r.lineup_adds, 1)}</b>` },
      ]), a.players);
      table($("nights-table"), [
        { key: "date", label: "Night", left: true, fmt: (n) => `${dow(n.date)} ${md(n.date)}` },
        { key: "playing", label: "Playing" },
        { key: "lineup", label: "Lineup value", fmt: (n) => num(n.lineup, 1) },
        { key: "open", label: "Open slots", left: true, sort: (n) => Object.values(n.open).reduce((a, b) => a + b, 0),
          fmt: (n) => Object.keys(n.open).length ? Object.entries(n.open).map(([s, k]) => `${k} ${s}`).join(", ")
            : '<span class="neg">full</span>' },
        { key: "benched", label: "Benched", left: true, fmt: (n) => esc(n.benched.join(", ")) },
      ], a.nights);
      fillHold();
      status(a.unmatched.length ? `Unmatched names in your lists: ${a.unmatched.join(", ")}` : "");
    } catch (e) { status(e.message, true); }
  };

  function fillHold() {
    const sel = $("hold"), prev = sel.value;
    sel.innerHTML = rosterPlayers.map((p) =>
      `<option value="${p.id}">${esc(p.name)} (${esc(p.team)}, blend ${num(p.blended)})</option>`).join("");
    if (prev && rosterPlayers.some((p) => String(p.id) === prev)) sel.value = prev;
  }

  // ── plan ─────────────────────────────────────────────────────────────
  loaders.plan = async () => { if (!rosterPlayers.length) await loaders.roster(); };
  $("run-plan").addEventListener("click", async () => {
    status("Planning… (simulating every candidate in your nightly lineup)");
    try {
      const r = await api(`/api/inseason/plan?${params({
        hold: $("hold").value, adds: $("adds").value, locked: $("locked").value })}`);
      const max = Math.max(1e-9, ...r.nightly.map((n) => n.value));
      $("plan-out").innerHTML = `
        <div class="summary">
          <div class="stat"><b>+${num(r.keep_value, 1)}</b><span>keep ${esc(r.hold.name)}</span></div>
          <div class="stat"><b class="pos">+${num(r.plan_value, 1)}</b><span>best plan</span></div>
          <div class="stat"><b>${r.steps.length}</b><span>adds used (${Object.entries(r.budget)
            .map(([w, n]) => `wk of ${md(w)}: ${n} left`).join(", ")})</span></div>
        </div>
        <h3>Moves</h3>
        <ul class="steps">${r.steps.length ? r.steps.map((s) =>
          `<li><b>${dow(s.date)} ${md(s.date)}</b>: drop ${esc(s.drop.name)} → add <b>${esc(s.add.name)}</b>
             <span class="muted">(${esc(s.add.team)}, ${esc(s.add.elig.join(","))})</span></li>`).join("")
          : `<li>Keep ${esc(r.hold.name)}: no add beats him with these adds.</li>`}</ul>
        <h3>Who holds the spot each night</h3>
        <div class="strip">${r.nightly.map((n) => `
          <div class="night ${n.value > 0 ? "" : "zero"}">
            <div>${dow(n.date)} ${md(n.date)}</div>
            <div class="bar" style="width:${Math.max(4, 100 * n.value / max)}%"></div>
            <div>${esc(n.holder.name.split(" ").slice(-1)[0])} <b>${num(n.value, 1)}</b></div>
          </div>`).join("")}</div>
        <h3>Best single adds for this spot</h3>
        <div class="scroll"><table id="singles-table"></table></div>`;
      table($("singles-table"), [
        { key: "name", label: "Player", left: true },
        { key: "team", label: "Tm", left: true },
        { key: "elig", label: "Pos", left: true, fmt: (c) => esc(c.elig.join(",")), sort: (c) => c.elig.join(",") },
        { key: "adds", label: "Adds", title: `${unit} added to your lineup over the window`, fmt: (c) => `<b>${num(c.adds, 1)}</b>` },
        { key: "nights", label: "Nights that count", left: true, sort: (c) => c.nights.length,
          fmt: (c) => c.nights.map((d) => `${dow(d)} ${md(d)}`).join(", ") },
      ], r.singles);
      status("");
    } catch (e) { status(e.message, true); }
  });

  // ── free agents + streams ────────────────────────────────────────────
  loaders.fa = async () => {
    status("Loading free agents…");
    try {
      const f = await api(`/api/inseason/free-agents?${params()}`);
      table($("fa-table"), playerCols([{ key: "window_games", label: "Games" }]), f.players);
      status(f.unmatched.length ? `Unmatched names: ${f.unmatched.join(", ")}` : "");
      await loadStreams();
    } catch (e) { status(e.message, true); }
  };
  async function loadStreams() {
    const s = await api(`/api/inseason/streams?${new URLSearchParams({
      league: $("league").value, date: $("stream-date").value, all: $("stream-all").checked, top: 25 })}`);
    table($("streams-table"), [
      { key: "name", label: "Player", left: true },
      { key: "pos", label: "Pos", left: true },
      { key: "team", label: "Tm", left: true },
      { key: "opp", label: "Opp", left: true },
      { key: "value", label: "Tonight", title: "opponent-adjusted", fmt: (r) => `<b>${num(r.value)}</b>` },
      { key: "per_game", label: "Neutral", fmt: (r) => num(r.per_game) },
      { key: "replacement", label: "Repl", fmt: (r) => num(r.replacement) },
      { key: "vs_prior_pct", label: "vs pre", fmt: (r) => `<span class="${cls(r.vs_prior_pct)}">${pct(r.vs_prior_pct)}</span>` },
      { key: "start_share", label: "Start %", fmt: (r) => r.start_share == null ? "" : Math.round(r.start_share * 100) + "%" },
    ], s.players);
  }
  $("stream-date").addEventListener("change", () => loadStreams().catch((e) => status(e.message, true)));
  $("stream-all").addEventListener("change", () => loadStreams().catch((e) => status(e.message, true)));

  // ── schedule ─────────────────────────────────────────────────────────
  loaders.schedule = async () => {
    status("Loading schedule…");
    try {
      const s = await api(`/api/inseason/schedule?${params()}`);
      table($("sched-table"), [
        { key: "team", label: "Team", left: true },
        { key: "games", label: "Games" },
        { key: "off_nights", label: "Off-nights", title: "games on light NHL nights" },
        { key: "opp_ga_factor", label: "Opp GA ×", title: ">1 = weaker defensive opponents",
          fmt: (r) => `<span class="${cls(r.opp_ga_factor == null ? null : r.opp_ga_factor - 1)}">${num(r.opp_ga_factor)}</span>` },
        { key: "opponents", label: "Opponents", left: true, sort: (r) => r.opponents.join(" "),
          fmt: (r) => esc(r.opponents.join(" ")) },
      ], s.teams);
      status("");
    } catch (e) { status(e.message, true); }
  };

  // ── lists ────────────────────────────────────────────────────────────
  loaders.lists = async () => {
    try {
      const l = await api("/api/inseason/lists");
      $("roster-text").value = l.roster;
      $("fa-text").value = l.free_agents;
    } catch (e) { status(e.message, true); }
  };
  $("save-lists").addEventListener("click", async () => {
    try {
      const r = await api(`/api/inseason/lists?league=${encodeURIComponent($("league").value)}`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ roster: $("roster-text").value, free_agents: $("fa-text").value }),
      });
      rosterPlayers = [];
      $("lists-msg").textContent = r.unmatched.length
        ? `Saved. No match for: ${r.unmatched.join(", ")}` : "Saved.";
    } catch (e) { $("lists-msg").textContent = e.message; }
  });

  // ── refresh: re-ingest stats + pull roster / free agents from Yahoo ──
  $("refresh").addEventListener("click", async () => {
    const btn = $("refresh");
    btn.disabled = true;
    btn.textContent = "Refreshing…";
    status("Re-ingesting season stats, rosters and schedule, then reading Yahoo (about a minute)…");
    try {
      const r = await api(`/api/inseason/refresh?league=${encodeURIComponent($("league").value)}`,
        { method: "POST" });
      rosterPlayers = [];
      const y = r.yahoo;
      const msg = `Updated in ${r.seconds}s: ${r.ingest.skater_current} skaters, ` +
        `${r.ingest.goalie_current} goalies` +
        (y ? `; Yahoo roster ${y.roster}, free agents ${y.free_agents}` +
          (y.unmatched.length ? ` (no match: ${y.unmatched.join(", ")})` : "") : "");
      await load();
      status(r.yahoo_error ? `${msg}. ${r.yahoo_error}` : msg, !!r.yahoo_error);
    } catch (e) { status(`Refresh failed: ${e.message}`, true); }
    btn.disabled = false;
    btn.textContent = "Refresh data";
  });

  // ── boot ─────────────────────────────────────────────────────────────
  $("start").value = today;
  $("stream-date").value = today;
  ["league", "start", "days"].forEach((id) => $(id).addEventListener("change", () => {
    rosterPlayers = []; load();
  }));
  api("/api/inseason/leagues").then((l) => {
    $("league").innerHTML = l.leagues.map((n) =>
      `<option ${n === l.default ? "selected" : ""}>${esc(n)}</option>`).join("");
    load();
  }).catch((e) => status(e.message, true));
})();
