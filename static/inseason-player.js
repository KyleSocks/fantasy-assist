// Player detail dialog: tap a player's name in the roster / free-agent
// tables. Summary + per-game stats (/api/inseason/player/<id>), then this
// season's weekly fantasy points chart and game log
// (/api/inseason/player/<id>/games). Uses window.insKit.
(function () {
  "use strict";
  const { $, esc, num, api, verdict, pct, md, params, cls, trendTxt } = window.insKit;
  const dlg = $("player-dlg");
  const LABELS = { goals: "G", assists: "A", pp_points: "PPP", sh_points: "SHP", shots: "SOG",
    hits: "HIT", blocks: "BLK", games_started: "GS", wins: "W", saves: "SV",
    shots_against: "SA", goals_against: "GA", shutouts: "SO", save_pct: "SV%" };
  const stat = (k, v) => (v == null ? "–" : k === "save_pct" ? Number(v).toFixed(3) : num(v));
  let seq = 0;

  document.addEventListener("click", (e) => {
    const b = e.target.closest(".plink");
    if (b) open(b.dataset.pid);
  });
  $("pd-close").addEventListener("click", () => dlg.close());
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });  // backdrop

  async function open(pid) {
    const mine = ++seq;
    $("pd-name").textContent = "Loading…";
    $("pd-body").innerHTML = "";
    if (!dlg.open) dlg.showModal();
    try {
      const p = await api(`/api/inseason/player/${pid}?${params()}`);
      if (mine !== seq) return;
      $("pd-name").textContent = p.name;
      $("pd-body").innerHTML = summary(p) + '<div id="pd-games"><p class="hint">Loading games…</p></div>';
      const g = await api(`/api/inseason/player/${pid}/games?league=${encodeURIComponent(p.league)}`);
      if (mine !== seq) return;
      drawGames(g);
    } catch (err) {
      if (mine !== seq) return;
      $("pd-body").insertAdjacentHTML("beforeend", `<p class="status err">${esc(err.message)}</p>`);
    }
  }

  function trendTile(p) {
    const t = p.trend || {}, per = p.position === "G" ? "start" : "game";
    if (t.per_game == null) return [`Last ${p.trend_days} days`, "–", `${t.games || 0} ${per}s: too few to read`];
    return [`Last ${p.trend_days} days`, `${num(t.per_game)} <span class="${cls(t.diff)}">${trendTxt(t.pct, t.diff)}</span>`,
      `${t.games} ${per}s · vs ${num(t.base)} blend`];
  }

  function summary(p) {
    const f = p.form, u = p.unit;
    const tiles = [
      ["Preseason", num(f.prior), `${u} / game`],
      ["This season", num(f.observed), f.vs_prior_pct == null ? `${u} / game` : `${pct(f.vs_prior_pct)} vs preseason`],
      ["Blend", num(f.blended), `${u} / game played`],
      ["Plays", f.avail == null ? "–" : `${Math.round(f.avail * 100)}%`, "of team games"],
      ["Value", num(f.team_game), `vs ${num(p.replacement_pg)} free-agent bar`],
      trendTile(p),
    ];
    const keys = Object.keys(p.blended).filter((k) => LABELS[k]);
    return `<p class="pd-sub">${esc(p.team || "–")} · ${esc(p.position)} · ${p.gp} GP ${verdict(p.verdict)}
        <span class="muted">stats as of ${esc(p.as_of)}</span></p>
      <div class="pd-tiles">${tiles.map(([l, v, s]) =>
        `<div><span class="muted">${l}</span><b>${v}</b><span class="muted">${esc(s)}</span></div>`).join("")}</div>
      <h3>Per ${p.position === "G" ? "start" : "game"}</h3>
      <div class="scroll"><table class="pd-stats"><thead><tr><th class="l"></th>${keys.map((k) => `<th>${LABELS[k]}</th>`).join("")}</tr></thead>
        <tbody>${[["Preseason", p.prior], ["This season", p.observed], ["Blend", p.blended]].map(([l, d]) =>
          `<tr><td class="l">${l}</td>${keys.map((k) => `<td>${stat(k, (d || {})[k])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  }

  // Weekly bars: one series (fantasy points per Mon-Sun week). Skaters also
  // get a tick per bar at preseason pace x games that week.
  function chart(g) {
    const W = Math.max(330, Math.min(700, $("pd-body").clientWidth || 640)), H = 230, L = 30, R = 6, T = 18, B = 36;
    const wk = g.weeks, pace = (w) => (g.goalie ? null : g.pre_per_game * w.games);
    const vals = wk.flatMap((w) => [w.value, pace(w) ?? 0]);
    const hi = Math.max(1, ...vals), lo = Math.min(0, ...vals);
    const step = (W - L - R) / wk.length, bw = Math.min(56, step - 2);
    const y = (v) => T + (hi - v) / (hi - lo) * (H - T - B);
    const few = step >= 38, best = Math.max(...wk.map((w) => w.value));
    const ticks = [lo, (lo + hi) / 2, hi].map((v) =>
      `<line class="grid" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text class="ax" x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${Math.round(v)}</text>`).join("");
    const bars = wk.map((w, i) => {
      const x = L + i * step + (step - bw) / 2, top = Math.min(y(w.value), y(0)), h = Math.abs(y(w.value) - y(0));
      const r = Math.min(4, h), up = w.value >= 0;
      const d = h < 0.5 ? "" : up
        ? `M${x},${y(0)}V${top + r}a${r},${r} 0 0 1 ${r},${-r}H${x + bw - r}a${r},${r} 0 0 1 ${r},${r}V${y(0)}Z`
        : `M${x},${y(0)}V${top + h - r}a${r},${r} 0 0 0 ${r},${r}H${x + bw - r}a${r},${r} 0 0 0 ${r},${-r}V${y(0)}Z`;
      const label = w.games && (few || i === wk.length - 1 || w.value === best)
        ? `<text class="val" x="${x + bw / 2}" y="${up ? top - 5 : top + h + 13}" text-anchor="middle">${num(w.value, 1)}</text>` : "";
      const p = pace(w);
      return `<g class="wk" tabindex="0" data-i="${i}"><rect class="hit" x="${L + i * step}" y="${T}" width="${step}" height="${H - T - B}"/>
        ${d ? `<path class="bar" d="${d}"/>` : ""}${p != null && w.games ? `<line class="pace" x1="${x + 1}" x2="${x + bw - 1}" y1="${y(p)}" y2="${y(p)}"/>` : ""}${label}
        <text class="ax" x="${x + bw / 2}" y="${H - B + 15}" text-anchor="middle">${md(w.week)}</text>
        <text class="ax" x="${x + bw / 2}" y="${H - B + 29}" text-anchor="middle">${w.games} gm</text></g>`;
    }).join("");
    return `<div class="pd-legend"><span><i class="k-bar"></i>Fantasy points per week (Mon–Sun)</span>${
      g.goalie ? "" : '<span><i class="k-pace"></i>Preseason pace for the games he played</span>'}</div>
      <div class="pd-chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Fantasy points per week">${ticks}
        <line class="zero" x1="${L}" x2="${W - R}" y1="${y(0)}" y2="${y(0)}"/>${bars}</svg></div>
      <p class="pd-tip muted">Tap or hover a week for details.</p>`;
  }

  function drawGames(g) {
    const el = $("pd-games");
    if (g.error || !g.games.length) {
      el.innerHTML = `<p class="hint">${esc(g.error || "No games played yet this season.")}</p>`;
      return;
    }
    const keys = Object.keys(g.games[0].stats).filter((k) => LABELS[k]);
    el.innerHTML = `<h3>Weekly performance</h3>${chart(g)}<h3>Game log</h3>
      <div class="scroll"><table class="pd-stats"><thead><tr><th class="l">Date</th><th class="l">Opp</th>${keys.map((k) => `<th>${LABELS[k]}</th>`).join("")}<th>${esc(g.unit)}</th></tr></thead>
      <tbody>${g.games.slice().reverse().map((x) => `<tr><td class="l">${md(x.date)}</td><td class="l">${x.home ? "" : "@"}${esc(x.opp || "")}</td>${
        keys.map((k) => `<td>${Math.round(x.stats[k])}</td>`).join("")}<td><b>${num(x.value, 1)}</b></td></tr>`).join("")}</tbody></table></div>`;
    const tip = el.querySelector(".pd-tip");
    const show = (node) => {
      const w = g.weeks[+node.dataset.i];
      tip.textContent = "";
      const b = document.createElement("b");
      b.textContent = `${num(w.value, 1)} ${g.unit}`;
      const pace = g.goalie ? "" : ` · pace ${num(g.pre_per_game * w.games, 1)}`;
      tip.append(b, ` week of ${md(w.week)} · ${w.games} game${w.games === 1 ? "" : "s"}${pace}`,
        document.createElement("br"),
        keys.filter((k) => w.stats[k]).map((k) => `${Math.round(w.stats[k])} ${LABELS[k]}`).join(" · ") || "did not play");
      tip.classList.remove("muted");
    };
    el.querySelectorAll(".wk").forEach((n) => {
      ["pointerenter", "focus", "click"].forEach((ev) => n.addEventListener(ev, () => show(n)));
    });
  }
})();
