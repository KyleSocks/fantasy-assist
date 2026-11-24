// Injury fallout (Recap tab) and the goalie grid (Schedule tab):
// /api/inseason/fallout and /api/inseason/goalie-grid
// (src/inseason/fallout.py, src/inseason/goalie_grid.py).
(function () {
  "use strict";
  const { $, esc, num, api, md, dow, loaders, signed, cls } = window.insKit;
  const mmss = (s) => { const t = Math.round(Math.abs(s)); return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`; };
  const owner = (x) => (x.mine ? '<span class="flag ok">yours</span>'
    : x.free_agent ? '<span class="flag good">free agent</span>'
      : x.owner ? `<span class="muted">${esc(x.owner)}</span>` : "");
  const plink = (x) => `<button type="button" class="plink" data-pid="${x.id}">${esc(x.name)}</button>`;
  const q = () => `league=${encodeURIComponent($("league").value)}&start=${encodeURIComponent($("start").value)}`;

  // ── injury fallout ────────────────────────────────────────────────
  async function drawFallout() {
    const el = $("fallout");
    try {
      const r = await api(`/api/inseason/fallout?${q()}`);
      const rows = r.players.filter((p) => p.takers.length);
      el.innerHTML = `<h3>Injury fallout</h3>
        <p class="muted">Regulars who are out (missing from their team's latest game, or listed injured) and the
          teammates who took their line or power-play spot since, from the NHL shift data. "a game" is what the new
          ice time adds to his projection.</p>`
        + (rows.length ? `<div class="take-list">${rows.map((p) => card(p, r.unit)).join("")}</div>`
          : '<p class="muted">No regular is out with a clear replacement right now.</p>');
    } catch (e) {
      el.innerHTML = `<p class="muted">Injury fallout: ${esc(e.message)}</p>`;
    }
  }
  function card(p, unit) {
    const status = [p.tag, p.back ? `back ~${md(p.back)}` : null, p.missing && p.last_game ? `last played ${md(p.last_game)}` : null]
      .filter(Boolean).join(", ");
    const takers = p.takers.map((t) => `<li>${plink(t)} ${owner(t)}<br>
      <span class="muted">${esc(t.role || "–")}${t.was ? ` (was ${esc(t.was)})` : ""}${
        Math.abs(t.pp_gain_sec) >= 15 ? `, PP time ${t.pp_gain_sec > 0 ? "+" : "−"}${mmss(t.pp_gain_sec)}` : ""}</span>
      ${t.gain ? `<b class="${cls(t.gain)}">${signed(t.gain, 2)}</b> ${esc(unit)} a game` : ""}</li>`).join("");
    return `<article class="take fallout-card">
      <header>${plink(p)} <span class="muted">${esc(p.team)}, usually ${esc(p.slot.join(", "))}</span> ${owner(p)}</header>
      ${status ? `<p class="muted">${esc(status)}</p>` : ""}
      <ul class="fallout-takers">${takers}</ul></article>`;
  }
  const prevRecap = loaders.recap;
  loaders.recap = async () => { await prevRecap(); drawFallout(); };

  // ── goalie grid ───────────────────────────────────────────────────
  let grid = null;
  async function loadGrid() {
    try {
      grid = await api(`/api/inseason/goalie-grid?${q()}`);
      drawGrid();
    } catch (e) {
      $("gg-table").innerHTML = `<tbody><tr><td>${esc(e.message)}</td></tr></tbody>`;
    }
  }
  function drawGrid() {
    if (!grid) return;
    const f = $("gg-filter").value;
    const keep = (g) => f === "all" || (f === "fa" ? g.free_agent : g.mine);
    const head = `<thead><tr><th class="l">Team</th>${grid.dates.map((d) =>
      `<th>${dow(d)}<br><span class="muted">${md(d)}</span></th>`).join("")}</tr></thead>`;
    const rows = grid.teams.map((t) => {
      const cells = grid.dates.map((d) => {
        const c = t.cells[d];
        if (!c) return "<td></td>";
        const gs = c.goalies.filter(keep);
        if (!gs.length) return `<td class="gg-off"><span class="muted">vs ${esc(c.opp)}</span></td>`;
        return `<td><span class="muted gg-opp">vs ${esc(c.opp)}</span>${gs.map((g) =>
          `<div class="gg-g${g.chance >= 0.5 ? " likely" : ""}"><span class="gg-grade g${g.grade}">${g.grade}</span>
            <button type="button" class="plink" data-pid="${g.id}">${esc(g.name.split(" ").slice(-1)[0])}</button>
            <span class="gg-pct">${Math.round(g.chance * 100)}%</span>${g.b2b ? '<span class="gg-b2b" title="back-to-back">B2B</span>' : ""}
            ${g.mine ? '<span class="flag ok">you</span>' : g.free_agent ? '<span class="flag good">FA</span>' : ""}</div>`).join("")}</td>`;
      }).join("");
      return `<tr><th class="l" scope="row">${esc(t.team)} <span class="muted">${t.games}</span></th>${cells}</tr>`;
    }).filter((r) => f === "all" || /gg-g/.test(r));
    $("gg-table").innerHTML = head + `<tbody>${rows.join("") || `<tr><td colspan="${grid.dates.length + 1}" class="muted">No goalies match.</td></tr>`}</tbody>`;
  }
  $("gg-filter").addEventListener("change", drawGrid);
  const prevSched = loaders.schedule;
  loaders.schedule = async () => { await prevSched(); loadGrid(); };
})();
