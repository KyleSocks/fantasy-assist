// Mid-season assistant page: roster audit, streaming plan, free agents,
// schedule and the editable roster / free-agent lists. Talks to
// /api/inseason/* (src/api/inseason.py).
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const today = new Date(Date.now() - new Date().getTimezoneOffset() * 60000)
    .toISOString().slice(0, 10);
  let rosterPlayers = [];       // YOUR roster (the Plan tab's picker)
  let rosterTeam = "";          // Roster tab: "" = yours, else a Yahoo team id
  let unit = "fpts";
  let trendDays = 21;

  // ── helpers ──────────────────────────────────────────────────────────
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const num = (v, d = 2) => (v == null ? "–" : Number(v).toFixed(d));
  const signed = (v, d = 1) => (v == null ? "–" : (v >= 0 ? "+" : "") + Number(v).toFixed(d));
  const pct = (v) => (v == null ? "–" : (v >= 0 ? "+" : "") + Math.round(v * 100) + "%");
  // ▲ / ▼ / ▬ so the direction isn't carried by colour alone
  const trendTxt = (p, d) => {
    const v = p ?? d;
    if (v == null) return "";
    const up = p != null ? p >= 0.1 : d >= 0.25, down = p != null ? p <= -0.1 : d <= -0.25;
    return `${up ? "▲" : down ? "▼" : "▬"}${p != null ? pct(p) : signed(d, 2)}`;
  };
  const cls = (v) => (v == null ? "" : v > 0 ? "pos" : v < 0 ? "neg" : "");
  // who wins a trade and by how much: g = your gain (+) or loss (-);
  // close = inside projection error (server's close_call)
  const winner = (g, them = "Them", d = 0, close = false) => (Math.abs(g) < 0.5 ? '<span class="muted">Even</span>'
    : close ? `<span class="muted" title="margin is inside projection error">Too close (${g > 0 ? "you" : esc(them)} by ${num(Math.abs(g), d)})</span>`
    : g > 0 ? `<b class="pos">You by ${num(g, d)}</b>` : `<b class="neg">${esc(them)} by ${num(-g, d)}</b>`);
  // Yahoo injury tag, share of games played, fantasy-playoff games
  // injury tag, plus shooting / save luck (hot = sell-high, cold = buy-low)
  // out_until: on IR / OUT, counted from his estimated return (teams.py)
  const tag = (p) => (p.injury ? `<span class="flag">${esc(p.injury)}</span>` : "")
    + (p.out_until ? `<span class="flag" title="injured: counted from his estimated return (by Yahoo tag; set in Settings)">${p.injury ? "" : "IR, "}back ~${md(p.out_until)}</span>` : "")
    + (p.luck === "hot" ? '<span class="flag" title="shooting or saving well above his usual: expect it to cool off">running hot</span>'
      : p.luck === "cold" ? '<span class="flag good" title="shooting or saving well below his usual: expect it to come back">due to bounce back</span>' : "");
  const extras = (p) => [p.avail != null && p.avail < 0.85 ? `plays ${Math.round(p.avail * 100)}%` : "",
    p.playoff_games != null ? `PO ${p.playoff_games}g` : ""].filter(Boolean).join(" · ");
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

  // Sortable table that turns into cards on phones (static/tbl.js)
  const table = (el, cols, rows, opts) => window.Tbl.table(el, cols, rows, opts);

  const verdict = (v) => `<span class="verdict ${esc(v.split(" ")[0])}">${esc(v)}</span>`;
  const flags = (r) => (r.flags || []).filter((f) => !/^(hold|borderline|drop)/.test(f))
    .map((f) => `<span class="flag ${/^(small|injured)/.test(f) ? "ok" : /^(promoted|shooting cold|saving cold)/.test(f) ? "good" : ""}">${esc(f)}</span>`).join("");
  // % rostered (Yahoo-wide) and its 7-day change
  const rosCell = (r) => { const x = r.rostered; if (!x) return '<span class="muted">–</span>';
    const d = x.d7; return `${Math.round(x.pct)}%${d != null && Math.abs(d) >= 1 ? ` <span class="${d > 0 ? "pos" : "neg"}">${d > 0 ? "▲" : "▼"}${Math.round(Math.abs(d))}</span>` : ""}`; };
  // "Stats from" switch: which window the "Last N days" column shows
  let statWindow = 14;
  try { statWindow = Number(localStorage.getItem("fa-stat-window")) || 14; } catch { /* default */ }
  document.querySelectorAll(".win-select").forEach((sel) => {
    sel.value = String(statWindow);
    sel.addEventListener("change", () => {
      statWindow = Number(sel.value);
      try { localStorage.setItem("fa-stat-window", String(statWindow)); } catch { /* session only */ }
      document.querySelectorAll(".win-select").forEach((o) => { o.value = sel.value; });
      if (current === "fa") drawFreeAgents(); else load();
    });
  });
  // deployment: "F2, PP1" with an arrow when it's a change from usual
  const ROLE_RANK = { F1: 1, F2: 2, F3: 3, F4: 4, D1: 1, D2: 2, D3: 3 };
  const roleSort = (r) => (r.role && (r.role.line || r.role.pp)
    ? (r.role.pp === "PP1" ? 0 : r.role.pp === "PP2" ? 10 : 20) + (ROLE_RANK[r.role.line] || 5) : null);
  const roleCell = (r) => (r.role && r.role.label
    ? `${esc(r.role.label)}${r.role.change ? ` <span class="${r.role.change === "up" ? "pos" : "neg"}" title="${r.role.change === "up" ? "promoted" : "demoted"} from ${esc([r.role.usual_line, r.role.usual_pp || "no PP"].filter(Boolean).join(", "))}">${r.role.change === "up" ? "▲" : "▼"}</span>` : ""}`
    : '<span class="muted">–</span>');

  // "Can add tonight": forwards as one count with the positions that work
  // (a LW/RW starter can slide to free the other wing), then D and G.
  function fitText(n) {
    const f = n.can_fit || {};
    const parts = [];
    if (f.F) {
      const at = ["C", "LW", "RW"].filter((p) => f[p]);
      parts.push(`${f.F} F (${at.map((p) => (f[p] < f.F ? `${p} ${f[p]}` : p)).join(" / ")})`);
    }
    if (f.D) parts.push(`${f.D} D`);
    if (f.G) parts.push(`${f.G} G`);
    return parts.length ? parts.join(", ") : '<span class="neg">full</span>';
  }

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
      { key: "name", label: "Player", left: true,
        fmt: (r) => `<button type="button" class="plink" data-pid="${r.id}">${esc(r.name)}</button>${flags(r)}` },
      { key: "team", label: "NHL team", left: true, minor: true },
      { key: "elig", label: "Pos", left: true, fmt: (r) => esc(r.elig.join(",")), sort: posSort },
      { key: "role", label: "Role", left: true, sort: roleSort, title: "line and power-play unit in his last game (from NHL shift data); ▲▼ = changed from usual",
        fmt: roleCell },
      { key: "gp", label: "GP", minor: true },
      { key: "prior", label: "Preseason", minor: true, title: `preseason ${unit}/game`, fmt: (r) => num(r.prior) },
      { key: "now", label: "Now", title: `this season ${unit}/game`, fmt: (r) => num(r.now) },
      { key: "vs_prior_pct", label: "vs pre", minor: true, fmt: (r) => `<span class="${cls(r.vs_prior_pct)}">${pct(r.vs_prior_pct)}</span>` },
      { key: "trend", label: "Trend", sort: (r) => (r.trend_pct ?? r.trend_diff),
        title: `last ${trendDays} days, per game (per start for goalies), vs his blend`,
        fmt: (r) => (r.trend == null ? `<span class="muted" title="${r.trend_games || 0} games in the window">–</span>`
          : `${num(r.trend)} <span class="${cls(r.trend_diff)}">${trendTxt(r.trend_pct, r.trend_diff)}</span>`) },
      { key: "blended", label: "Blend", minor: true, title: "blended value per game played", fmt: (r) => num(r.blended) },
      { key: "win", label: `Last ${statWindow}d`, title: `value per game played (per start for goalies) over the last ${statWindow} days`,
        sort: (r) => r.windows?.[statWindow]?.per_game,
        fmt: (r) => { const w = r.windows?.[statWindow];
          return w && w.per_game != null ? `${num(w.per_game)} <span class="muted">${w.games} gp</span>` : '<span class="muted">–</span>'; } },
      { key: "ros", label: "% Ros", minor: true, title: "% of Yahoo leagues rostering him, and the change over 7 days",
        sort: (r) => r.rostered?.d7 ?? -999, fmt: rosCell },
      { key: "avail", label: "Plays", minor: true, title: "share of team games he plays / starts (injuries, scratches)",
        fmt: (r) => (r.avail == null ? "–" : `<span class="${r.avail < 0.85 ? "neg" : ""}">${Math.round(r.avail * 100)}%</span>`) },
      { key: "team_game", label: "Value", title: "value per team game (blend x plays): what every decision uses",
        fmt: (r) => `<b>${num(r.team_game)}</b>` },
      { key: "ros_vs_repl", label: "Season +/-", title: "long-term value: fantasy points above a replacement free agent from now through the end of your fantasy playoffs (playoff games weighted)",
        fmt: (r) => `<span class="${cls(r.ros_vs_repl)}">${signed(r.ros_vs_repl)}</span>` },
      { key: "toi_prior", label: "TOI pre", minor: true, title: "EV TOI per game", fmt: (r) => mmss(r.toi_prior) },
      { key: "toi_now", label: "TOI now", minor: true, fmt: (r) => {
          const d = r.toi_now != null && r.toi_prior != null ? r.toi_now - r.toi_prior : null;
          return `<span class="${d != null && d <= -90 ? "neg" : ""}">${mmss(r.toi_now)}</span>`; } },
      { key: "rank", label: "Rank", minor: true, title: "rank in F / D by blended value", fmt: (r) => `${r.rank}/${r.pool}` },
      ...extra,
      { key: "verdict", label: "Verdict", left: true, wide: true, fmt: (r) => verdict(r.verdict) },
    ];
  }

  // ── tabs ─────────────────────────────────────────────────────────────
  const loaders = {};
  let current = "roster";
  function showTab(name, push = true) {
    if (!document.getElementById(`tab-${name}`)) name = "roster";
    document.querySelectorAll(".ins-tabs .tab").forEach((x) => {
      x.classList.toggle("active", x.dataset.tab === name);
      x.setAttribute("aria-current", x.dataset.tab === name ? "page" : "false");
    });
    current = name;
    document.querySelectorAll(".panel").forEach((p) => { p.hidden = p.id !== `tab-${current}`; });
    if (push) history.replaceState(null, "", `#${name}`);
    load().then(() => document.querySelectorAll(".panel:not([hidden]) table.tbl.cards").forEach(window.Tbl.fit));
  }
  document.querySelectorAll(".ins-tabs .tab").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  function load() { status(""); return Promise.resolve((loaders[current] || (() => {}))()); }

  // ── roster ───────────────────────────────────────────────────────────
  // Alerts about your team (src/inseason/alerts.py), last 7 days
  async function drawAlerts() {
    const box = $("alerts");
    if (rosterTeam) { box.hidden = true; return; }
    try {
      const r = await api(`/api/inseason/alerts?league=${encodeURIComponent($("league").value)}&days=7`);
      box.hidden = !r.alerts.length;
      box.innerHTML = `<h3>Alerts</h3><ul>${r.alerts.slice(0, 12).map((x) =>
        `<li class="alert ${esc(x.kind)}"><span class="muted">${esc(dow(x.at.slice(0, 10)))} ${esc(md(x.at.slice(0, 10)))}</span> ${
          x.player_id ? `<button type="button" class="plink" data-pid="${x.player_id}">${esc(x.text)}</button>` : esc(x.text)}</li>`).join("")}</ul>`;
    } catch { box.hidden = true; }
  }

  loaders.roster = async () => {
    status("Loading roster…");
    drawAlerts();
    try {
      const q = new URLSearchParams(params(rosterTeam ? { team: rosterTeam } : {}));
      let a;
      try {
        a = await api(`/api/inseason/audit?${q}`);
      } catch (e) {
        if (!rosterTeam) throw e;
        rosterTeam = "";         // that team is gone (league changed): back to yours
        q.delete("team");
        a = await api(`/api/inseason/audit?${q}`);
      }
      unit = a.unit;
      trendDays = a.trend_days || trendDays;
      $("asof").textContent = `NHL stats through ${a.as_of}`;
      const mine = !a.team || a.team.mine;
      if (mine) rosterPlayers = a.players;
      const sel = $("roster-team");
      sel.innerHTML = (a.teams || []).length
        ? a.teams.map((t) => `<option value="${t.mine ? "" : t.id}">${esc(t.name)}${t.mine ? " (you)" : ""}</option>`).join("")
        : '<option value="">Your team</option>';
      sel.value = mine ? "" : String(a.team.id);
      sel.closest(".form-row").hidden = (a.teams || []).length < 2;
      $("roster-title").textContent = mine ? "Your roster" : a.team.name;
      $("repl-note").textContent = "The free agent each verdict measures against: "
        + Object.entries(a.replacement || {}).map(([g, v]) =>
          `${g} ${num(v)}${(a.replacement_from || {})[g] ? ` (${a.replacement_from[g]})` : ""}`).join(", ")
        + ` ${unit} per team game.`;
      table($("roster-table"), playerCols([
        { key: "window_games", label: "Games", title: "team games in the window" },
        { key: "starts", label: "Starts", title: "games he'd actually start in your best lineup" },
        { key: "lineup_adds", label: "Adds", title: `${unit} he adds to your nightly lineup over the window`,
          fmt: (r) => `<b>${num(r.lineup_adds, 1)}</b>` },
      ]), a.players, { empty: "No roster yet. Press Refresh data, or paste your roster on the Lists tab." });
      table($("nights-table"), [
        { key: "date", label: "Night", left: true, fmt: (n) => `<b>${dow(n.date)} ${md(n.date)}</b>` },
        { key: "playing", label: "Playing" },
        { key: "lineup", label: "Lineup", fmt: (n) => num(n.lineup, 1) },
        { key: "open", label: "Can add tonight", left: true, wide: true,
          title: "players you could still start after re-arranging multi-position starters",
          sort: (n) => Object.values(n.open).reduce((a, b) => a + b, 0), fmt: fitText },
        { key: "benched", label: "Benched", left: true, wide: true,
          fmt: (n) => esc(n.benched.join(", ")) || '<span class="muted">nobody</span>' },
      ], a.nights);
      fillHold();
      status(a.warning || "", !!a.warning);
    } catch (e) { status(e.message, true); }
  };

  function fillHold() {
    const sel = $("hold"), prev = sel.value;
    sel.innerHTML = rosterPlayers.map((p) =>
      `<option value="${p.id}">${esc(p.name)} (${esc(p.team)}, blend ${num(p.blended)})</option>`).join("");
    if (prev && rosterPlayers.some((p) => String(p.id) === prev)) sel.value = prev;
  }

  // ── plan ─────────────────────────────────────────────────────────────
  loaders.plan = async () => {
    if (rosterPlayers.length) return;
    const keep = rosterTeam;      // load YOUR roster for the picker, then restore the view
    rosterTeam = "";
    await loaders.roster();
    rosterTeam = keep;
  };
  $("roster-team").addEventListener("change", () => { rosterTeam = $("roster-team").value; loaders.roster(); });
  async function runPlan(force) {
    status("Planning… (simulating every candidate in your nightly lineup)");
    try {
      const r = await api(`/api/inseason/plan?${params({
        hold: $("hold").value, adds: $("adds").value, locked: $("locked").value,
        force: force ? "true" : "false" })}`);
      const max = Math.max(1e-9, ...r.nightly.map((n) => n.value));
      $("plan-out").innerHTML = `
        <div class="summary">
          <div class="stat"><b>+${num(r.keep_value, 1)}</b><span>${esc(unit)} if you keep ${esc(r.hold.name)}</span></div>
          <div class="stat best"><b class="pos">+${num(r.plan_value, 1)}</b><span>${esc(unit)} with the best plan</span></div>
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
            <div><b>${dow(n.date)}</b> ${md(n.date)}</div>
            <div class="nbar" style="width:${Math.max(4, 100 * n.value / max)}%"></div>
            <div class="who" title="${esc(n.holder.name)}">${esc(n.holder.name.split(" ").slice(-1)[0])} <b>${num(n.value, 1)}</b></div>
          </div>`).join("")}</div>
        <h3>Best single adds for this spot</h3>
        <div class="scroll"><table id="singles-table"></table></div>`;
      table($("singles-table"), [
        { key: "name", label: "Player", left: true,
          fmt: (c) => (c.id != null ? `<button type="button" class="plink" data-pid="${c.id}">${esc(c.name)}</button>` : esc(c.name)) },
        { key: "team", label: "NHL team", left: true },
        { key: "elig", label: "Pos", left: true, fmt: (c) => esc(c.elig.join(",")), sort: (c) => c.elig.join(",") },
        { key: "adds", label: "Adds", title: `${unit} added to your lineup over the window`, fmt: (c) => `<b>${num(c.adds, 1)}</b>` },
        { key: "nights", label: "Nights that count", left: true, wide: true, sort: (c) => c.nights.length,
          fmt: (c) => c.nights.map((d) => `${dow(d)} ${md(d)}`).join(", ") },
      ], r.singles, { empty: "No free agent adds anything in this spot." });
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
      trendDays = faData.trend_days || trendDays;
      drawFreeAgents();
      status("");
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
    const role = $("fa-role").value;
    const rows = faData.players.filter((r) => $(`fa-show-${r.status}`).checked
      && (!q || `${r.name} ${r.team}`.toLowerCase().includes(q))
      && (!pos || r.elig.includes(pos))
      && (!role || (role === "up" ? r.role?.change === "up" : role === "pp1" ? r.role?.pp === "PP1"
        : role === "cold" ? r.luck?.status === "cold"
        : role === "rising" ? (r.rostered?.d7 ?? 0) >= 3 : true)));
    const c = faData.counts;
    $("fa-counts").textContent = `${rows.length} match: ${c.FA} free agents, ${c.W} on waivers, ${c.OUT} injured in all.`;
    table($("fa-table"), playerCols([
      { key: "window_games", label: "Games", title: "team games in the window" },
      { key: "status", label: "Status", left: true, title: "FA = add now; W = on waivers until the date; OUT = injured",
        fmt: statusCell },
    ]), rows, { page: 50, empty: "No players match. Clear the search or tick more boxes." });
  }
  ["fa-search", "fa-pos", "fa-role", "fa-show-FA", "fa-show-W", "fa-show-OUT"].forEach((id) =>
    $(id).addEventListener(id === "fa-search" ? "input" : "change", drawFreeAgents));
  async function loadStreams() {
    const q = (group) => api(`/api/inseason/streams?${new URLSearchParams({
      league: $("league").value, date: $("stream-date").value, all: $("stream-all").checked,
      top: 25, group })}`);
    const [sk, gl] = await Promise.all([q("S"), q("G")]);
    const cols = (goalie) => [
      { key: "name", label: "Player", left: true,
        fmt: (r) => ((r.id ?? r.player_id) != null ? `<button type="button" class="plink" data-pid="${r.id ?? r.player_id}">${esc(r.name)}</button>` : esc(r.name)) },
      { key: "pos", label: "Pos", left: true, fmt: (r) => esc((r.elig || [r.pos]).join(",")) },
      { key: "team", label: "Team", left: true, fmt: (r) => `${esc(r.team)} <span class="muted">vs</span> ${esc(r.opp)}`, sort: (r) => r.team },
      { key: "value", label: "Tonight", title: goalie ? "opponent-adjusted, if he starts" : "opponent-adjusted",
        fmt: (r) => `<b>${num(r.value)}</b>` },
      { key: "adds", label: "Adds to you", title: "what he adds to YOUR lineup tonight, after re-arranging multi-position starters (0 = no slot for him)",
        fmt: (r) => (r.adds == null ? "–" : r.adds > 0 ? `<b class="pos">+${num(r.adds)}</b>` : '<span class="muted">no slot</span>') },
      { key: "per_game", label: goalie ? "Per start" : "Neutral", minor: true, fmt: (r) => num(r.per_game) },
      { key: "replacement", label: "Repl", minor: true, fmt: (r) => num(r.replacement) },
      goalie
        ? { key: "start_share", label: "Starts %", title: "share of team games he starts",
            fmt: (r) => Math.round(r.start_share * 100) + "%" }
        : { key: "vs_prior_pct", label: "vs pre",
            fmt: (r) => `<span class="${cls(r.vs_prior_pct)}">${pct(r.vs_prior_pct)}</span>` },
    ];
    table($("streams-table"), cols(false), sk.players, { empty: "No skaters play that night." });
    table($("gstreams-table"), cols(true), gl.players, { empty: "No goalies play that night." });
    $("streams-note").textContent = `${sk.nhl_games} NHL games that night. Showing ${sk.availability}.`;
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
      $("league-meta").textContent = `Lineups ${md(L.dates[0])} to ${md(L.dates[L.dates.length - 1])}.`
        + ` Rosters read from Yahoo ${L.fetched ? L.fetched.replace("T", " at ") : "(never)"}, NHL stats through ${L.as_of}.`;
      const names = Object.fromEntries(L.teams.map((t) => [t.id, t.name]));
      const el = $("league-table");
      table(el, [
        { key: "rank", label: "Rank", sort: (t) => t.rank },
        { key: "name", label: "Team", left: true, stick: true, fmt: (t) => `${esc(t.name)}${t.mine ? ' <span class="flag ok">you</span>' : ""}` },
        { key: "total", label: `Lineup ${unit}`, fmt: (t) => `<b>${num(t.total, 0)}</b>` },
        ...SLOTS.map((s) => ({ key: s, label: s, title: `${s} starters vs league avg (${num(L.league_avg[s], 0)})`,
          sort: (t) => t.vs_avg[s], fmt: (t) => heat(t.vs_avg[s]) })),
        { key: "bench", label: "Bench", title: "points from players who had to sit", fmt: (t) => num(t.bench, 0) },
      ], L.teams.map((t, i) => ({ ...t, rank: i + 1 })), {
        rowClass: (t) => `click${t.mine ? " mine" : ""}`,
        onRow: (t, tr) => { tr.onclick = () => showTeam(t, true); },
      });
      showTeam(L.teams.find((t) => t.mine));
      drawTrades(names);
      if (window.insPartner) window.insPartner.fill(L);
      status("");
    } catch (e) { status(e.message, true); }
  };
  function showTeam(t, scroll = false) {
    if (!t) return;
    const strong = SLOTS.filter((s) => t.vs_avg[s] >= 10), weak = SLOTS.filter((s) => t.vs_avg[s] <= -10);
    $("team-detail").innerHTML = `<div class="team-card"><h4>${esc(t.name)}</h4>
      <button type="button" class="open-roster" data-team="${t.mine ? "" : esc(t.id)}">Open full roster</button>
      <div class="muted">Strong at ${strong.join(", ") || "nothing"}. Weak at ${weak.join(", ") || "nothing"}.
        ${num(t.bench, 0)} ${esc(unit)} left on the bench. Dashed = sits some nights.</div>
      <div class="pills">${t.players.map((p) => `<span class="pill ${p.starts < p.games ? "sit" : ""}"
        title="${esc(p.elig.join(","))}, starts ${p.starts} of ${p.games} games">${esc(p.name)}
        <b>${num(p.per_game)}</b>${p.starts < p.games ? `, sits ${p.games - p.starts}` : ""}</span>`).join("")}</div></div>`;
    if (scroll) $("team-detail").scrollIntoView({ behavior: "smooth", block: "nearest" });
    $("team-detail").querySelector(".open-roster").onclick = (e) => {
      rosterTeam = e.target.dataset.team;
      showTab("roster");
    };
  }
  function drawTrades(names) {
    if (!leagueData) return;
    const rows = leagueData.trades.filter((x) =>
      (!$("tr-mutual").checked || x.mutual) && (!$("tr-pg").checked || x.pg_delta >= -0.1) &&
      (!$("tr-fair").checked || x.pg_delta <= 0.5));
    const pl = (p) => `${esc(p.name)} <span class="muted">${esc(p.team)} ${esc(p.elig.join(","))} ${num(p.per_game)}</span>`;
    table($("trades-table"), [
      { key: "partner", label: "With", left: true, wide: true, sort: (x) => names[x.partner_id], fmt: (x) => `<b>${esc(names[x.partner_id])}</b>` },
      { key: "give", label: "You give", left: true, sort: (x) => x.give.name, fmt: (x) => pl(x.give) },
      { key: "get", label: "You get", left: true, sort: (x) => x.get.name, fmt: (x) => pl(x.get) },
      { key: "win", label: "Winner", left: true, title: "who gains more over the window, and by how much",
        sort: (x) => x.my_gain - x.their_gain, fmt: (x) => winner(x.my_gain - x.their_gain, names[x.partner_id], 1) },
      { key: "my_gain", label: "Me", fmt: (x) => `<span class="${cls(x.my_gain)}">${signed(x.my_gain)}</span>` },
      { key: "their_gain", label: "Them", fmt: (x) => `<span class="${cls(x.their_gain)}">${signed(x.their_gain)}</span>` },
      { key: "pg_delta", label: "Per-game", fmt: (x) => `<span class="${cls(x.pg_delta)}">${signed(x.pg_delta, 2)}</span>` },
    ], rows, { page: 15, empty: "No trades pass these filters. Untick one to see more." });
  }

  ["tr-mutual", "tr-pg", "tr-fair"].forEach((id) => $(id).addEventListener("change", () =>
    drawTrades(Object.fromEntries((leagueData?.teams || []).map((t) => [t.id, t.name])))));
  $("league-days").addEventListener("change", () => loaders.league());

  // shared with static/inseason-partner.js (trade with a team)
  window.insKit = { $, esc, num, signed, cls, winner, tag, extras, table, api, status, verdict, pct, md, params, trendTxt,
    dow, loaders, showTab, reload: () => load(), unit: () => unit, league: () => leagueData };

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
        { key: "opponents", label: "Opponents", left: true, wide: true, sort: (r) => r.opponents.join(" "),
          fmt: (r) => esc(r.opponents.join(" ")) },
      ], s.teams);
      status("");
    } catch (e) { status(e.message, true); }
  };

  // ── lists ────────────────────────────────────────────────────────────
  loaders.lists = async () => {
    try {
      const l = await api(`/api/inseason/lists?league=${encodeURIComponent($("league").value)}`);
      $("roster-text").value = l.roster;
      $("fa-text").value = l.free_agents;
      $("lists-folder").textContent = `${l.folder}/my_roster.txt and ${l.folder}/fa.txt`;
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
        (r.ingest.skater_recent != null ? `, trend for ${r.ingest.skater_recent} skaters` : "") +
        (y ? `; from Yahoo your ${y.roster} players, ${y.free_agents} free agents, ${y.teams} team rosters` : "") +
        (r.moves != null ? `, ${r.moves} roster move${r.moves === 1 ? "" : "s"} since the last refresh` : "") +
        (r.market != null ? `; market data for ${r.market} rostered players` : "");
      leagues().catch(() => {});
      await load();
      const err = [...(r.ingest_notes || []), r.yahoo_error, r.market_error].filter(Boolean).join(". ");
      status(err ? `${msg}. ${err}` : msg, !!err);
    } catch (e) { status(`Refresh failed: ${e.message}`, true); }
    btn.disabled = false;
    btn.textContent = "Refresh data";
  });

  // ── boot ─────────────────────────────────────────────────────────────
  $("start").value = today;
  $("stream-date").value = today;
  ["start", "days"].forEach((id) => $(id).addEventListener("change", () => {
    rosterPlayers = []; load();
  }));
  // League picker: every config in config/leagues. Each one shows your team
  // name in that Yahoo league; the choice is kept (and in the URL).
  let leagueInfo = {};
  const LKEY = "fa-inseason-league";
  function leagueLabel(n) {
    const i = leagueInfo[n] || {};
    if (i.error) return `${n} (config error)`;
    const team = i.team_name ? `${i.team_name}, ` : "";
    const how = i.scoring_type === "points" ? "points" : "categories";
    return `${team}${n.replace(/_/g, " ")}, ${how}`;
  }
  function showLeague() {
    const n = $("league").value, i = leagueInfo[n] || {};
    $("brand-team").textContent = i.team_name || "Your team";
    const y = i.yahoo;
    $("refresh").title = y ? `Re-ingest NHL stats, then read Yahoo league ${y.league_id} (your team ${y.team_id})`
      : "Re-ingest NHL stats. No Yahoo league is set for this league: add a yahoo: block to its config.";
  }
  async function leagues() {
    const l = await api("/api/inseason/leagues");
    leagueInfo = l.info || {};
    const want = new URLSearchParams(location.search).get("league")
      || (() => { try { return localStorage.getItem(LKEY); } catch { return null; } })();
    const cur = $("league").value || (l.leagues.includes(want) ? want : l.default);
    $("league").innerHTML = l.leagues.map((n) =>
      `<option value="${esc(n)}" ${n === cur ? "selected" : ""}>${esc(leagueLabel(n))}</option>`).join("");
    showLeague();
  }
  $("league").addEventListener("change", () => {
    try { localStorage.setItem(LKEY, $("league").value); } catch { /* session only */ }
    const u = new URL(location.href);
    u.searchParams.set("league", $("league").value);
    history.replaceState(null, "", u);
    showLeague();
    rosterPlayers = []; rosterTeam = ""; leagueData = null; faData = null;
    load();
  });
  leagues().then(() => showTab(location.hash.slice(1) || "roster", false))
    .catch((e) => status(e.message, true));
})();
