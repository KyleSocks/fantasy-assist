// Player detail dialog: tap a player's name in the roster / free-agent
// tables. Summary + per-game stats (/api/inseason/player/<id>), then this
// season's weekly fantasy points chart and game log
// (/api/inseason/player/<id>/games). Uses window.insKit.
(function () {
  "use strict";
  const { $, esc, num, signed, api, verdict, pct, md, params, cls, trendTxt } = window.insKit;
  // the weekly chart re-reads its width when the theme or window changes
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
      `${t.games} ${per}s, vs ${num(t.base)} blend`];
  }

  function summary(p) {
    const f = p.form, u = p.unit;
    const tiles = [
      ["Preseason", num(f.prior), `${u} / game`],
      ["This season", num(f.observed), f.vs_prior_pct == null ? `${u} / game` : `${pct(f.vs_prior_pct)} vs preseason`],
      ["Blend", num(f.blended), `${u} / game played`],
      p.position !== "G" && f.avail === 1
        ? ["Games", `${p.gp}`, "played; values count every team game (IR covers injuries)"]
        : ["Plays", f.avail == null ? "–" : `${Math.round(f.avail * 100)}%`, "of team games"],
      ["Value", num(f.team_game), `vs ${num(p.replacement_pg)} free-agent bar`],
      ["Rest of season", `<span class="${cls(p.ros_vs_replacement)}">${signed(p.ros_vs_replacement, 0)}</span>`,
        `${u} vs a free agent, ${p.ros_games} games to ${md(p.ros_end)}`],
      trendTile(p),
    ];
    const keys = Object.keys(p.blended).filter((k) => LABELS[k]);
    return `<p class="pd-sub">${verdict(p.verdict)} <span>${esc(p.position)}, ${esc(p.team || "–")}, ${p.gp} games played</span>
        <span class="muted">NHL stats through ${esc(p.as_of)}</span></p>
      ${injuryBlock(p)}
      <div class="pd-tiles">${tiles.map(([l, v, s]) =>
        `<div><span class="muted">${l}</span><b>${v}</b><span class="muted">${esc(s)}</span></div>`).join("")}</div>
      ${deployment(p)}${luckBlock(p)}${advBlock(p)}${inputs(p)}
      <h3>Per ${p.position === "G" ? "start" : "game"}</h3>
      <div class="scroll"><table class="pd-stats"><thead><tr><th class="l"></th>${keys.map((k) => `<th>${LABELS[k]}</th>`).join("")}</tr></thead>
        <tbody>${[["Preseason", p.prior], ["This season", p.observed], ["Blend", p.blended]].map(([l, d]) =>
          `<tr><td class="l">${l}</td>${keys.map((k) => `<td>${stat(k, (d || {})[k])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  }

  // Line, PP unit and linemates from the last games' shift charts, and
  // ice time over the last week vs the three weeks before (lines.py)
  function deployment(p) {
    const r = p.role;
    if (!r || p.position === "G") return "";
    const mmss = (s) => (s == null ? "–" : `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`);
    const usual = [r.usual_line, r.usual_pp || (r.games > 1 ? "no PP" : null)].filter(Boolean).join(", ");
    const move = r.change ? ` <span class="flag ${r.change === "up" ? "good" : ""}">${r.change === "up" ? "promoted" : "demoted"}</span>` : "";
    const rows = [];
    if (r.label || r.line || r.pp) rows.push(`<b>${esc(r.label || "Depth role")}</b> in his last game${r.date ? ` (${md(r.date)})` : ""}${move}`
      + (usual && r.change ? `<br><span class="muted">Before that, usually ${esc(usual)}</span>` : ""));
    if (r.mates && r.mates.length) rows.push(`Linemates at 5-on-5: ${r.mates.map((m) =>
      `<button type="button" class="plink" data-pid="${m.id}">${esc(m.name)}</button>`).join(", ")}`);
    if (r.recent_pp_toi != null || r.prior_pp_toi != null) {
      const d = (r.recent_pp_toi ?? 0) - (r.prior_pp_toi ?? 0);
      rows.push(`Power-play time per game: <b>${mmss(r.recent_pp_toi)}</b> last 7 days, ${mmss(r.prior_pp_toi)} the 3 weeks before`
        + (Math.abs(d) >= 30 ? ` <span class="${d > 0 ? "pos" : "neg"}">(${d > 0 ? "+" : "−"}${mmss(Math.abs(d))})</span>` : ""));
    }
    if (r.recent_toi != null) rows.push(`Ice time per game: <b>${mmss(r.recent_toi)}</b> last 7 days, ${mmss(r.prior_toi)} before`);
    return rows.length ? `<h3>Deployment</h3><ul class="pd-list">${rows.map((x) => `<li>${x}</li>`).join("")}</ul>` : "";
  }

  // Shooting / save luck (luck.py)
  function luckBlock(p) {
    const l = p.luck;
    if (!l) return "";
    const g = p.position === "G";
    const pctTxt = (x) => (g ? `.${String(Math.round(x * 1000)).padStart(3, "0")}` : `${(x * 100).toFixed(1)}%`);
    const goals = Math.abs(l.luck).toFixed(1);
    const say = g
      ? (l.luck >= 0 ? `${goals} fewer goals against than his usual save % would give` : `${goals} more goals against than his usual save % would give`)
      : (l.luck >= 0 ? `${goals} more goals than his shots usually produce` : `${goals} fewer goals than his shots usually produce`);
    const pts = l.points != null && Math.abs(l.points) >= 1 ? `, about ${signed(l.points, 0)} ${p.unit}` : "";
    const verdictTxt = l.status === "hot" ? "Expect this to cool off: a sell-high, not one to pay for."
      : l.status === "cold" ? "Expect this to come back: a buy-low, not one to drop." : "Within normal luck.";
    return `<h3>Luck</h3><ul class="pd-list"><li>${g ? "Save %" : "Shooting %"} <b>${pctTxt(l.pct)}</b> this season,
      ${pctTxt(l.usual_pct)} usual: ${say}${pts}.</li><li>${verdictTxt}</li></ul>`;
  }

  // Injured: when the trade tools expect him back, and your own date
  // (e.g. "out about 12 weeks" from the news) -- PUT /api/inseason/injury-return
  function injuryBlock(p) {
    const i = p.injured;
    if (!i) return "";
    const how = i.yours ? "your date" : `an estimate from his ${i.tag ? `${esc(i.tag)} ` : ""}tag; change it here`;
    const owner = i.owner ? ` on ${esc(i.owner.replace(/\.$/, ""))}` : "";
    return `<div class="pd-injury" data-pid="${p.player_id}">
      <p><b>Injured</b>${owner}. Trades and takes count him from <b>${md(i.back)}</b>
        <span class="muted">(${how})</span>: nothing before that, every game after.</p>
      <div class="pd-injury-set"><label>Expected back <input type="date" value="${esc(i.back)}"></label>
        <button type="button" class="pd-back-save">Save date</button>
        ${i.yours ? '<button type="button" class="pd-back-clear quiet">Use the estimate</button>' : ""}
        <span class="muted pd-back-msg" role="status"></span></div></div>`;
  }
  $("pd-body").addEventListener("click", async (e) => {
    const save = e.target.closest(".pd-back-save"), clear = e.target.closest(".pd-back-clear");
    if (!save && !clear) return;
    const box = e.target.closest(".pd-injury");
    const date = clear ? null : box.querySelector("input[type=date]").value;
    if (save && !date) { box.querySelector(".pd-back-msg").textContent = "Pick a date first."; return; }
    try {
      await api("/api/inseason/injury-return", { method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ player_id: Number(box.dataset.pid), date }) });
      open(box.dataset.pid);
      window.insKit.reload?.();
    } catch (err) { box.querySelector(".pd-back-msg").textContent = err.message; }
  });

  // Advanced stats, season to date (advanced.py)
  function advBlock(p) {
    const a = p.adv;
    if (!a) return "";
    const pc = (x, d = 1) => (x == null ? "–" : `${(x * 100).toFixed(d)}%`);
    const n1 = (x) => (x == null ? "–" : Number(x).toFixed(1));
    const mm = (x) => (x == null ? "–" : `${Math.floor(x / 60)}:${String(Math.round(x % 60)).padStart(2, "0")}`);
    let rows;
    if (p.position === "G") {
      rows = [["Quality starts", a.quality_starts == null ? "–" : `${a.quality_starts} of ${a.gs}`, `${pc(a.qs_pct, 0)} (about 53% is average)`],
        ["Shots against / 60", n1(a.shots_against_per60), "how busy he is"],
        ["Team goals for", n1(a.goals_for_avg), "a game in his starts (wins)"]];
    } else {
      const rel = a.sat_rel == null ? "" : ` (${a.sat_rel >= 0 ? "+" : "−"}${Math.abs(a.sat_rel * 100).toFixed(1)} vs team without him)`;
      rows = [["Corsi (shot attempts) %", pc(a.sat_pct), `at 5v5${rel}`],
        ["Offensive-zone starts", pc(a.oz_start_pct, 0), "of his offensive + defensive zone starts at 5v5"],
        ["Shots / 60", n1(a.ishots_per60), `his own at 5v5; ${n1(a.isat_per60)} attempts`],
        ["5v5 points / 60", a.points_per60_5v5 == null ? "–" : Number(a.points_per60_5v5).toFixed(2), `${mm(a.toi_5v5_pg)} a game at 5v5`],
        ["On-ice shooting %", pc(a.onice_sh_pct), "5v5, everyone's shots with him on"],
        ["PDO", a.pdo == null ? "–" : Number(a.pdo).toFixed(3), "on-ice SH% + SV%; 1.000 is normal, far off it is luck"]];
    }
    return `<h3>Advanced</h3><div class="scroll"><table class="pd-adv"><tbody>${rows.map(([l, v, s]) =>
      `<tr><th scope="row">${l}</th><td><b>${v}</b></td><td class="muted">${esc(s)}</td></tr>`).join("")}</tbody></table></div>`;
  }

  // What the projection uses beyond the season blend (deploy.py, model.py)
  function inputs(p) {
    const i = p.inputs || {};
    const rows = [];
    const pc = (x) => `${(x * 100).toFixed(1)}%`;
    if (i.sh_pct && i.sh_pct.season != null) {
      rows.push(`Goals = shots x shooting %. Shooting % used: <b>${pc(i.sh_pct.blended)}</b>
        (${pc(i.sh_pct.season)} this season, ${pc(i.sh_pct.prior)} projected), so a hot or cold
        finishing run counts for little until it lasts.`);
    }
    const o = i.onice;
    if (o) {
      const who = o.mates && o.mates.length
        ? ` (mostly ${o.mates.slice(0, 3).map((m) => `<button type="button" class="plink" data-pid="${m.id}">${esc(m.name)}</button>`).join(", ")})`
        : "";
      const base = o.basis === "linemates"
        ? `The players he's been out with${who} usually shoot <b>${pc(o.baseline)}</b> (career, at 5v5 levels; league ${pc(o.league)}).`
        : `No linemate data yet, so it's compared with the league average, ${pc(o.league)}.`;
      rows.push(`Assists: linemates shoot <b>${pc(o.pct)}</b> at 5v5 with him on the ice. ${base}
        The gap mostly doesn't last, so it's counted as ${pc(o.regressed)}: ${o.assists_5v5} assists at 5v5 are worth
        ${Number(o.expected_5v5).toFixed(1)}, moving his projection <span class="${cls(o.value_delta)}">${signed(o.value_delta, 2)}</span> ${esc(p.unit)} a game.`);
    }
    const d = i.deploy;
    if (d) {
      const f = (x) => `${x >= 1 ? "+" : "−"}${Math.round(Math.abs(x - 1) * 100)}%`;
      const mm = (x) => `${Math.floor(x / 60)}:${String(Math.round(x % 60)).padStart(2, "0")}`;
      const big = Math.abs(d.pp - 1) >= 0.05 || Math.abs(d.ev - 1) >= 0.05;
      rows.push(`Ice time from here: PP <b>${mm(d.exp_pp)}</b> a game (season ${mm(d.season_pp)}, ${f(d.pp)}),
        even strength <b>${mm(d.exp_ev)}</b> (season ${mm(d.season_ev)}, ${f(d.ev)}).`
        + (big ? ` That moves his projection <span class="${cls(d.value_delta)}">${signed(d.value_delta, 2)}</span> ${esc(p.unit)} a game.` : ""));
    }
    if (i.b2b && i.b2b.length) {
      rows.push(`Back-to-backs in the next two weeks change his start chance: ${i.b2b.map((b) =>
        `${md(b.date)} ${Math.round(b.share * 100)}%`).join(", ")} (usually ${Math.round(p.start_share * 100)}%).`);
    }
    return rows.length ? `<h3>What the projection uses</h3><ul class="pd-list">${rows.map((x) => `<li>${x}</li>`).join("")}</ul>` : "";
  }

  // Weekly bars: one series (fantasy points per Mon-Sun week). Skaters also
  // get a tick per bar at preseason pace x games that week.
  function chart(g) {
    const W = Math.max(300, Math.min(700, ($("pd-body").clientWidth || 640) - 36)), H = 230, L = 30, R = 6, T = 18, B = 36;
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
