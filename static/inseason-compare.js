// Compare up to 3 players side by side: "Add to compare" in a player's
// dialog puts him in the tray at the bottom; Compare opens the table and
// an overlaid weekly chart. Uses window.insKit from inseason.js.
(function () {
  "use strict";
  const { $, esc, num, api, params, md, verdict, signed, cls } = window.insKit;
  const MAX = 3;
  const KEY = "fa-compare";
  let picked = [];
  try { picked = JSON.parse(sessionStorage.getItem(KEY) || "[]"); } catch { picked = []; }
  let current = null;           // {id, name} of the open player dialog

  const save = () => { try { sessionStorage.setItem(KEY, JSON.stringify(picked)); } catch { /* fine */ } };

  function tray() {
    const t = $("cmp-tray");
    t.hidden = !picked.length;
    t.innerHTML = `<span class="muted">Compare</span>${picked.map((p) =>
      `<span class="cmp-chip">${esc(p.name)}<button type="button" data-rm="${p.id}" aria-label="Remove ${esc(p.name)}">×</button></span>`).join("")}
      <button type="button" class="primary" id="cmp-go" ${picked.length < 2 ? "disabled" : ""}>Compare ${picked.length}</button>
      <button type="button" id="cmp-clear" class="quiet">Clear</button>`;
  }
  function syncButton() {
    const b = $("pd-compare");
    if (!current) return;
    const on = picked.some((p) => String(p.id) === String(current.id));
    b.textContent = on ? "Remove from compare" : picked.length >= MAX ? `Compare is full (${MAX})` : "Add to compare";
    b.disabled = !on && picked.length >= MAX;
  }

  // the player dialog tells us who's open by its plink clicks
  document.addEventListener("click", (e) => {
    const b = e.target.closest(".plink");
    if (b) current = { id: b.dataset.pid, name: b.textContent.trim() };
    setTimeout(syncButton);
  }, true);
  new MutationObserver(() => {
    if (current && $("pd-name").textContent && $("pd-name").textContent !== "Loading…") current.name = $("pd-name").textContent;
  }).observe($("pd-name"), { childList: true, characterData: true, subtree: true });

  $("pd-compare").addEventListener("click", () => {
    if (!current) return;
    const i = picked.findIndex((p) => String(p.id) === String(current.id));
    if (i >= 0) picked.splice(i, 1);
    else if (picked.length < MAX) picked.push({ id: current.id, name: current.name });
    save(); tray(); syncButton();
  });
  $("cmp-tray").addEventListener("click", (e) => {
    const rm = e.target.closest("[data-rm]");
    if (rm) { picked = picked.filter((p) => String(p.id) !== rm.dataset.rm); save(); tray(); syncButton(); }
    if (e.target.id === "cmp-clear") { picked = []; save(); tray(); syncButton(); }
    if (e.target.id === "cmp-go") open();
  });
  $("cmp-close").addEventListener("click", () => $("cmp-dlg").close());
  $("cmp-dlg").addEventListener("click", (e) => { if (e.target === $("cmp-dlg")) $("cmp-dlg").close(); });

  async function open() {
    const dlg = $("cmp-dlg");
    $("cmp-body").innerHTML = '<p class="muted">Loading…</p>';
    if ($("player-dlg").open) $("player-dlg").close();
    dlg.showModal();
    try {
      const ps = await Promise.all(picked.map((p) => api(`/api/inseason/player/${p.id}?${params()}`)));
      const gs = await Promise.all(picked.map((p, i) => api(`/api/inseason/player/${p.id}/games?league=${encodeURIComponent(ps[i].league)}`).catch(() => null)));
      draw(ps, gs);
    } catch (e) {
      $("cmp-body").innerHTML = `<p class="status err">${esc(e.message)}</p>`;
    }
  }

  const COLORS = ["var(--blue)", "var(--red)", "var(--pos)"];

  function draw(ps, gs) {
    const u = ps[0].unit;
    const w = (p, d) => { const x = p.windows?.[d]; return x && x.per_game != null ? `${num(x.per_game)} <span class="muted">${x.games} gp</span>` : "–"; };
    const rows = [
      ["Team, position", (p) => esc(`${p.team || "–"}, ${p.position}`)],
      ["Verdict", (p) => verdict(p.verdict)],
      ["Value per team game", (p) => `<b>${num(p.form?.team_game)}</b>`],
      ["Blend (per game played)", (p) => num(p.form?.blended)],
      ["This season", (p) => num(p.form?.observed)],
      ["Last 7 days", (p) => w(p, 7)], ["Last 14 days", (p) => w(p, 14)],
      ["Last 28 days", (p) => w(p, 28)], ["Last 56 days", (p) => w(p, 56)],
      ["Rest of season vs a free agent", (p) => `<span class="${cls(p.ros_vs_replacement)}">${signed(p.ros_vs_replacement, 0)}</span>`],
      ["Role", (p) => esc(p.role?.label || "–")],
      ["Corsi % (5v5)", (p) => (p.adv?.sat_pct != null ? `${(p.adv.sat_pct * 100).toFixed(1)}%` : "–")],
      ["Shots / 60", (p) => (p.adv?.ishots_per60 != null ? Number(p.adv.ishots_per60).toFixed(1) : "–")],
      ["On-ice SH% (5v5)", (p) => (p.adv?.onice_sh_pct != null ? `${(p.adv.onice_sh_pct * 100).toFixed(1)}%` : "–")],
      ["Expected goals (goals)", (p) => (p.xg ? `${num(p.xg.ixg, 1)} <span class="muted">(${p.xg.goals})</span>` : "–")],
      ["PDO", (p) => (p.adv?.pdo != null ? Number(p.adv.pdo).toFixed(3) : "–")],
      ["Quality start %", (p) => (p.adv?.qs_pct != null ? `${Math.round(p.adv.qs_pct * 100)}%` : "–")],
      ["Luck", (p) => esc(p.luck?.text || "normal")],
      ["% rostered (Yahoo)", (p) => (p.rostered ? `${Math.round(p.rostered.pct)}%${p.rostered.d7 != null ? ` <span class="${cls(p.rostered.d7)}">${signed(p.rostered.d7, 0)} this week</span>` : ""}` : "–")],
    ];
    const head = `<tr><th></th>${ps.map((p, i) => `<th><span class="cmp-dot" style="background:${COLORS[i]}"></span>${esc(p.name)}</th>`).join("")}</tr>`;
    const body = rows.map(([label, f]) => `<tr><th scope="row">${esc(label)}</th>${ps.map((p) => `<td>${f(p)}</td>`).join("")}</tr>`).join("");
    $("cmp-title").textContent = ps.map((p) => p.name).join(" vs ");
    $("cmp-body").innerHTML = `<div class="scroll"><table class="cmp-table">${head}${body}</table></div>
      <h3>${esc(u)} by week</h3><div class="cmp-chart">${chart(gs)}</div>
      <div class="pd-legend">${ps.map((p, i) => `<span><i class="cmp-dot" style="background:${COLORS[i]}"></i>${esc(p.name)}</span>`).join("")}</div>`;
  }

  // one line per player over the same weeks (missing weeks = no point)
  function chart(gs) {
    const weeks = [...new Set(gs.flatMap((g) => (g ? g.weeks.map((w) => w.week) : [])))].sort();
    if (!weeks.length) return '<p class="muted">No games yet.</p>';
    const W = 640, H = 220, L = 34, R = 10, T = 10, B = 26;
    const vals = gs.flatMap((g) => (g ? g.weeks.map((w) => w.value) : []));
    const max = Math.max(1, ...vals), min = Math.min(0, ...vals);
    const x = (i) => L + (weeks.length === 1 ? (W - L - R) / 2 : (i * (W - L - R)) / (weeks.length - 1));
    const y = (v) => T + ((max - v) * (H - T - B)) / (max - min || 1);
    const ticks = [min, (min + max) / 2, max];
    const grid = ticks.map((t) => `<line class="grid" x1="${L}" x2="${W - R}" y1="${y(t)}" y2="${y(t)}"/><text class="ax" x="${L - 6}" y="${y(t) + 4}" text-anchor="end">${Math.round(t)}</text>`).join("");
    const step = Math.ceil(weeks.length / 8);
    const xl = weeks.map((wk, i) => (i % step ? "" : `<text class="ax" x="${x(i)}" y="${H - 8}" text-anchor="middle">${md(wk)}</text>`)).join("");
    const lines = gs.map((g, k) => {
      if (!g) return "";
      const by = Object.fromEntries(g.weeks.map((w) => [w.week, w]));
      const pts = weeks.map((wk, i) => (by[wk] && by[wk].games ? [x(i), y(by[wk].value)] : null));
      const d = pts.reduce((acc, p) => acc + (p ? `${acc && pts[pts.indexOf(p) - 1] ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}` : ""), "");
      return `<path d="${d}" fill="none" stroke="${COLORS[k]}" stroke-width="2.5" stroke-linejoin="round"/>`
        + pts.map((p) => (p ? `<circle cx="${p[0]}" cy="${p[1]}" r="3" fill="${COLORS[k]}"/>` : "")).join("");
    }).join("");
    return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="weekly ${esc("points")}">${grid}${xl}${lines}</svg>`;
  }

  tray();
})();
