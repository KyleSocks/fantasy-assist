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
    .map((f) => `<span class="flag ${/^(small|injured)/.test(f) ? "ok" : ""}">${esc(f)}</span>`).join("");

  // Pos header: group C, LW, RW, D, G (by first eligible position), best
  // blended value first inside each group.
  const POS_ORDER = ["C", "LW", "RW", "D", "G"];
  const posSort = (r) => {
    const i = POS_ORDER.indexOf((r.elig || [])[0]);
    return -((i < 0 ? POS_ORDER.length : i) * 1e6 - (r.blended || 0));
  };

  // Columns shared by the roster and free-agent tables.
  function playerCols(extra = []) {
    return [
      { key: "name", label: "Player", left: true, fmt: (r) => `${esc(r.name)}${flags(r)}` },
      { key: "team", label: "Tm", left: true },
      { key: "elig", label: "Pos", left: true, fmt: (r) => esc(r.elig.join(",")), sort: posSort },
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
      if (a.warning) status(a.warning, true);
      else status(a.unmatched.length ? `Unmatched names in your lists: ${a.unmatched.join(", ")}` : "");
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
  async function runPlan(force) {
    status("Planning… (simulating every candidate in your nightly lineup)");
    try {
      const r = await api(`/api/inseason/plan?${params({
        hold: $("hold").value, adds: $("adds").value, locked: $("locked").value,
        force: force ? "true" : "false" })}`);
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
          : r.protected ? `<li>Keep ${esc(r.hold.name)}: ${esc(r.protected)}.
              <a href="#" id="plan-force">Plan anyway</a></li>`
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
      const f = $("plan-force");
      if (f) f.addEventListener("click", (e) => { e.preventDefault(); runPlan(true); });
      status("");
    } catch (e) { status(e.message, true); }
  }
  $("run-plan").addEventListener("click", () => runPlan(false));

  // ── free agents + streams ────────────────────────────────────────────
  loaders.fa = async () => {
    status("Loading free agents…");
    try {
      faData = await api(`/api/inseason/free-agents?${params()}`);
      drawFreeAgents();
      status(faData.unmatched_count ? `${faData.unmatched_count} Yahoo names have no NHL stats `
        + "yet (mostly minor-leaguers) and aren't shown" : "");
      await loadStreams();
    } catch (e) { status(e.message, true); }
  };
  let faData = null;
  const statusCell = (r) => r.status === "W"
    ? `<span class="flag">W until ${md(r.waiver_until)}</span>`
    : r.status === "OUT" ? `<span class="flag">OUT${r.tag ? " " + esc(r.tag) : ""}</span>`
    : `FA${r.tag ? ` <span class="muted">${esc(r.tag)}</span>` : ""}`;
  function drawFreeAgents() {
    if (!faData) return;
    const q = $("fa-search").value.trim().toLowerCase(), pos = $("fa-pos").value;
    const rows = faData.players.filter((r) => $(`fa-show-${r.status}`).checked
      && (!q || `${r.name} ${r.team}`.toLowerCase().includes(q))
      && (!pos || r.elig.includes(pos)));
    const c = faData.counts;
    $("fa-counts").textContent = `${rows.length} shown · ${c.FA} free agents, ${c.W} on waivers, ${c.OUT} injured`;
    table($("fa-table"), playerCols([
      { key: "window_games", label: "Games" },
      { key: "status", label: "Status", left: true, title: "FA = add now; W = on waivers until the date; OUT = injured",
        fmt: statusCell },
    ]), rows);
  }
  ["fa-search", "fa-pos", "fa-show-FA", "fa-show-W", "fa-show-OUT"].forEach((id) =>
    $(id).addEventListener(id === "fa-search" ? "input" : "change", drawFreeAgents));
  async function loadStreams() {
    const q = (group) => api(`/api/inseason/streams?${new URLSearchParams({
      league: $("league").value, date: $("stream-date").value, all: $("stream-all").checked,
      top: 25, group })}`);
    const [sk, gl] = await Promise.all([q("S"), q("G")]);
    const cols = (goalie) => [
      { key: "name", label: "Player", left: true },
      { key: "pos", label: "Pos", left: true },
      { key: "team", label: "Tm", left: true },
      { key: "opp", label: "Opp", left: true },
      { key: "value", label: "Tonight", title: goalie ? "opponent-adjusted, if he starts" : "opponent-adjusted",
        fmt: (r) => `<b>${num(r.value)}</b>` },
      { key: "per_game", label: goalie ? "Per start" : "Neutral", fmt: (r) => num(r.per_game) },
      { key: "replacement", label: "Repl", fmt: (r) => num(r.replacement) },
      goalie
        ? { key: "start_share", label: "Starts %", title: "share of team games he starts",
            fmt: (r) => Math.round(r.start_share * 100) + "%" }
        : { key: "vs_prior_pct", label: "vs pre",
            fmt: (r) => `<span class="${cls(r.vs_prior_pct)}">${pct(r.vs_prior_pct)}</span>` },
    ];
    table($("streams-table"), cols(false), sk.players);
    table($("gstreams-table"), cols(true), gl.players);
    $("streams-note").textContent = `${sk.nhl_games} NHL games · showing ${sk.availability}`;
  }
  $("stream-date").addEventListener("change", () => loadStreams().catch((e) => status(e.message, true)));
  $("stream-all").addEventListener("change", () => loadStreams().catch((e) => status(e.message, true)));

  // ── league: team strengths + trades ─────────────────────────────────
  const SLOTS = ["C", "LW", "RW", "D", "Util", "G"];
  let leagueData = null;
  const heat = (v) => {
    const k = v >= 20 ? "p2" : v >= 5 ? "p1" : v <= -20 ? "n2" : v <= -5 ? "n1" : "";
    return `<span class="heat ${k}">${signed(v, 0)}</span>`;
  };
  loaders.league = async () => {
    status("Simulating every team's lineup and every 1-for-1 trade (a few seconds)…");
    try {
      const L = await api(`/api/inseason/league?${new URLSearchParams({
        league: $("league").value, start: $("start").value, days: $("league-days").value })}`);
      leagueData = L;
      $("league-meta").textContent = `${md(L.dates[0])}–${md(L.dates[L.dates.length - 1])}` +
        ` · rosters from Yahoo ${L.fetched || "?"} · stats ${L.as_of}`;
      const names = Object.fromEntries(L.teams.map((t) => [t.id, t.name]));
      const el = $("league-table");
      table(el, [
        { key: "rank", label: "#", sort: (t) => t.rank },
        { key: "name", label: "Team", left: true, fmt: (t) => `${t.mine ? "★ " : ""}${esc(t.name)}` },
        { key: "total", label: `Lineup ${unit}`, fmt: (t) => `<b>${num(t.total, 0)}</b>` },
        ...SLOTS.map((s) => ({ key: s, label: s, title: `${s} starters vs league avg (${num(L.league_avg[s], 0)})`,
          sort: (t) => t.vs_avg[s], fmt: (t) => heat(t.vs_avg[s]) })),
        { key: "bench", label: "Bench", title: "points from players who had to sit", fmt: (t) => num(t.bench, 0) },
      ], L.teams.map((t, i) => ({ ...t, rank: i + 1 })));
      decorateLeague(el, L);
      showTeam(L.teams.find((t) => t.mine));
      drawTrades(names);
      fillShop(L);
      status(L.unmatched.length ? `Unmatched rostered names: ${L.unmatched.join(", ")}` : "");
    } catch (e) { status(e.message, true); }
  };
  function decorateLeague(el, L) {
    el.querySelectorAll("tbody tr").forEach((tr) => {
      const name = tr.children[1].textContent.replace(/^★ /, "");
      const t = L.teams.find((x) => x.name === name);
      if (!t) return;
      tr.classList.add("click");
      tr.classList.toggle("mine", t.mine);
      tr.onclick = () => showTeam(t);
    });
    el.querySelectorAll("th").forEach((th) => th.addEventListener("click", () =>
      setTimeout(() => decorateLeague(el, L), 0)));
  }
  function showTeam(t) {
    if (!t) return;
    const strong = SLOTS.filter((s) => t.vs_avg[s] >= 10), weak = SLOTS.filter((s) => t.vs_avg[s] <= -10);
    $("team-detail").innerHTML = `<div class="team-card"><h4>${t.mine ? "★ " : ""}${esc(t.name)}</h4>
      <div class="muted">Strong: ${strong.join(", ") || "–"} · Weak: ${weak.join(", ") || "–"}
        · bench overflow ${num(t.bench, 0)}</div>
      <div>${t.players.map((p) => `<span class="pill ${p.starts < p.games ? "sit" : ""}"
        title="${esc(p.elig.join(","))} · starts ${p.starts}/${p.games} games">${esc(p.name)}
        <b>${num(p.per_game)}</b>${p.starts < p.games ? ` · sits ${p.games - p.starts}` : ""}</span>`).join("")}</div></div>`;
  }
  function drawTrades(names) {
    if (!leagueData) return;
    const rows = leagueData.trades.filter((x) =>
      (!$("tr-mutual").checked || x.mutual) && (!$("tr-pg").checked || x.pg_delta >= -0.1) &&
      (!$("tr-fair").checked || x.pg_delta <= 0.5));
    const pl = (p) => `${esc(p.name)} <span class="muted">${esc(p.team)} ${esc(p.elig.join(","))} ${num(p.per_game)}</span>`;
    table($("trades-table"), [
      { key: "partner", label: "With", left: true, sort: (x) => names[x.partner_id], fmt: (x) => esc(names[x.partner_id]) },
      { key: "give", label: "You give", left: true, sort: (x) => x.give.name, fmt: (x) => pl(x.give) },
      { key: "get", label: "You get", left: true, sort: (x) => x.get.name, fmt: (x) => pl(x.get) },
      { key: "my_gain", label: "Me", fmt: (x) => `<b class="${cls(x.my_gain)}">${signed(x.my_gain)}</b>` },
      { key: "their_gain", label: "Them", fmt: (x) => `<span class="${cls(x.their_gain)}">${signed(x.their_gain)}</span>` },
      { key: "pg_delta", label: "Per-game", fmt: (x) => `<span class="${cls(x.pg_delta)}">${signed(x.pg_delta, 2)}</span>` },
    ], rows);
    if (!rows.length) $("trades-table").innerHTML += '<tbody><tr><td class="l" colspan="6">No trades pass these filters.</td></tr></tbody>';
  }
  // ── shop a player: deals from "looks like I win" to "looks like they win"
  let shopData = null;
  function fillShop(L) {
    const me = L.teams.find((t) => t.mine);
    const sel = $("shop-give"), prev = sel.value;
    sel.innerHTML = (me ? me.players : []).map((p) =>
      `<option value="${p.id}">${esc(p.name)} (${esc(p.elig.join(","))}, ${num(p.per_game)})</option>`).join("");
    if (prev && [...sel.options].some((o) => o.value === prev)) sel.value = prev;
  }
  const leanCell = (x) => {
    const k = x.lean === "favors you" ? "p1" : x.lean === "favors them" ? "n1" : "";
    return `<span class="heat ${k}" title="market edge ${signed(x.market_edge, 0)}">${esc(x.lean)}
      ${signed(100 * x.tilt, 0)}%</span>`;
  };
  function drawShop() {
    if (!shopData) return;
    const pos = $("shop-pos").value, gain = $("shop-gain").checked;
    const rows = shopData.options.filter((x) =>
      (!pos || x.get.elig.includes(pos)) && (!gain || x.my_gain > 0));
    const g = shopData.give;
    const src = g.market_source === "yahoo"
      ? `${num(g.pct_ros, 0)}% rostered, Yahoo pre-season #${g.pre_rank ?? "–"}, ${num(g.fpts, 1)} Fan Pts`
        + ` in ${g.yahoo_gp ?? "?"} GP (Yahoo data ${esc(shopData.market_fetched || "?")})`
      : `no Yahoo data yet (press Refresh data): season-to-date with ${num(shopData.market_k_games, 0)} games of prior`;
    $("shop-meta").innerHTML = `Trading <b>${esc(g.name)}</b>: your model ${num(g.per_game)}/gm,
      market ~${num(g.market_pg)}/gm &mdash; ${src}.
      ${g.per_game > g.market_pg + 0.1 ? "You value him more than the market: hard to get full value."
        : g.market_pg > g.per_game + 0.1 ? "The market values him more than you do: good time to sell." : ""}
      ${rows.length} deals shown.`;
    const el = $("shop-table");
    el._sort = {};   // default: market order, favours-you first
    table(el, [
      { key: "star", label: "★", sort: (x) => (x.sweet_spot ? 1 : 0), fmt: (x) => (x.sweet_spot ? "★" : "") },
      { key: "partner", label: "With", left: true },
      { key: "get", label: "You get", left: true, sort: (x) => x.get.name,
        fmt: (x) => `${esc(x.get.name)} <span class="muted">${esc(x.get.team)} ${esc(x.get.elig.join(","))}</span>` },
      { key: "pg", label: "Model / mkt", title: "per game: your model vs the market's view",
        sort: (x) => x.get.per_game, fmt: (x) => `${num(x.get.per_game)} / ${num(x.get.market_pg)}` },
      { key: "pct_ros", label: "% Ros", title: "% of Yahoo leagues rostering him",
        sort: (x) => x.get.pct_ros, fmt: (x) => (x.get.pct_ros == null ? "–" : `${num(x.get.pct_ros, 0)}%`) },
      { key: "pre_rank", label: "Pre", title: "Yahoo pre-season rank (name value)",
        sort: (x) => (x.get.pre_rank == null ? null : -x.get.pre_rank),
        fmt: (x) => (x.get.pre_rank == null ? "–" : `#${num(x.get.pre_rank, 0)}`) },
      { key: "market_edge", label: "Looks to them", title: "how a typical manager sees it (tilt vs the value of your player)",
        fmt: leanCell },
      { key: "my_gain", label: "My gain", title: `rest of season, ${unit} above replacement, your model`,
        fmt: (x) => `<b class="${cls(x.my_gain)}">${signed(x.my_gain, 0)}</b>` },
      { key: "my_fit", label: "Fit me", fmt: (x) => `<span class="${cls(x.my_fit)}">${signed(x.my_fit)}</span>` },
      { key: "their_fit", label: "Fit them", fmt: (x) => `<span class="${cls(x.their_fit)}">${signed(x.their_fit)}</span>` },
    ], rows);
    if (!rows.length) el.innerHTML += '<tbody><tr><td class="l" colspan="10">No deals pass these filters.</td></tr></tbody>';
  }
  $("shop-run").addEventListener("click", async () => {
    status("Valuing every player in the league for this trade…");
    try {
      shopData = await api(`/api/inseason/trade-options?${new URLSearchParams({
        league: $("league").value, start: $("start").value, days: $("league-days").value,
        give: $("shop-give").value })}`);
      drawShop();
      status("");
    } catch (e) { status(e.message, true); }
  });
  ["shop-pos", "shop-gain"].forEach((id) => $(id).addEventListener("change", drawShop));

  ["tr-mutual", "tr-pg", "tr-fair"].forEach((id) => $(id).addEventListener("change", () =>
    drawTrades(Object.fromEntries((leagueData?.teams || []).map((t) => [t.id, t.name])))));
  $("league-days").addEventListener("change", () => loaders.league());

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
        (y ? `; Yahoo roster ${y.roster}, free agents ${y.free_agents}, ${y.teams} team rosters` +
          (y.unmatched.length ? ` (no match: ${y.unmatched.join(", ")})` : "") : "") +
        (r.market != null ? `; market data for ${r.market} rostered players` : "");
      await load();
      const err = [r.yahoo_error, r.market_error].filter(Boolean).join(". ");
      status(err ? `${msg}. ${err}` : msg, !!err);
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
