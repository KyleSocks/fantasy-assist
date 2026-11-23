// League tab trade tools: "Shop a player" (/api/inseason/trade-options) and
// "Trade with a team" -- every 1-1 .. 2-2 package with one team
// (/api/inseason/partner-trades) and scoring an offer you got. Uses the
// helpers static/inseason.js exposes as window.insKit.
(function () {
  "use strict";
  const { $, esc, num, signed, cls, winner, tag, extras, table, api, status, unit, league, md } = window.insKit;
  let ptData = null;
  function fill(L) {
    const sel = $("pt-team"), prev = sel.value;
    sel.innerHTML = L.teams.filter((t) => !t.mine).map((t) =>
      `<option value="${t.id}">${esc(t.name)}</option>`).join("");
    if (prev && [...sel.options].some((o) => o.value === prev)) sel.value = prev;
    drawPick();
  }
  function drawPick() {
    if (!league()) return;
    const me = league().teams.find((t) => t.mine);
    const them = league().teams.find((t) => String(t.id) === $("pt-team").value);
    const side = (t, cls, title) => `<div><h4>${esc(title)}</h4>${(t ? t.players : []).map((p) =>
      `<label><input type="checkbox" class="${cls}" value="${p.id}">${esc(p.name)}${tag(p)}
        <span class="muted">${esc(p.elig.join(","))}, ${num(p.per_game)} per game${extras(p) ? `, ${extras(p)}` : ""}</span></label>`).join("")}</div>`;
    $("pt-pick").innerHTML = side(me, "pt-give", "You give (tick up to 2)") +
      side(them, "pt-get", `You get from ${them ? them.name : "?"} (up to 2)`);
    $("pt-offer").innerHTML = "";
  }
  const ticked = (cls) => [...document.querySelectorAll(`.${cls}:checked`)].map((x) => x.value);
  // their_view = what they gain as the market sees it, so their view of
  // "your gain" is -their_view
  const lookCell = (x, them) => winner(-x.their_view, them);
  const sentence = (g, them) => (Math.abs(g) < 0.5 ? "an even trade"
    : g > 0 ? `you win by ${num(g, 0)}` : `${esc(them)} wins by ${num(-g, 0)}`);
  function drawDeals() {
    if (!ptData) return;
    const them = ptData.partner.name, nd = ptData.dates.length;
    const P = Object.fromEntries([...ptData.mine, ...ptData.theirs, ...(ptData.free_agents || [])].map((p) => [p.id, p]));
    const pl = (ids) => ids.map((i) => `${esc(P[i].name)}${tag(P[i])} <span class="muted">${esc(P[i].elig.join(","))}</span>`).join("<br>");
    const fa = (ids) => ((ids || []).length ? `<span class="deal-cut muted">you add FA ${ids.map((i) => esc(P[i].name)).join(", ")}</span>` : "");
    const cut = (ids, who) => (ids.length ? `<span class="deal-cut muted">${who} cut ${ids.map((i) => esc(P[i].name)).join(", ")}</span>` : "");
    const cols = [
      { key: "star", label: "★", title: "you win and they should see it as even or better", sort: (x) => (x.sweet_spot ? 1 : 0),
        fmt: (x) => (x.sweet_spot ? '<span title="worth proposing">★</span>' : '<span class="muted">–</span>') },
      { key: "give", label: "You give", left: true, stick: true, sort: (x) => P[x.give[0]].name,
        fmt: (x) => pl(x.give) + cut(x.their_cut, "they") },
      { key: "get", label: "You get", left: true, sort: (x) => P[x.get[0]].name,
        fmt: (x) => pl(x.get) + cut(x.my_cut, "you") + fa(x.my_adds) },
      { key: "my_gain", label: "Winner", left: true, title: `your lineup, ${unit()}, through ${ptData.ros_end} (playoff games x${ptData.playoff_game_weight})`,
        fmt: (x) => winner(x.my_gain, them, 0, x.close) },
      { key: "tilt", label: "They'll see", left: true, title: "how it looks to a typical manager (Yahoo % rostered / Fan Pts / rank)",
        sort: (x) => -x.their_view, fmt: (x) => lookCell(x, them) },
      { key: "fit", label: `Next ${nd} days`, left: true, wide: true, title: "change in each lineup's total", sort: (x) => x.my_fit - x.their_fit,
        fmt: (x) => `${winner(x.my_fit - x.their_fit, them, 1)}<span class="deal-cut muted">you ${signed(x.my_fit)} · them ${signed(x.their_fit)}</span>` },
    ];
    const o = ptData.offer;
    if (o) {
      const head = Math.abs(o.my_gain) < 0.5 ? "Even trade"
        : o.close ? `Too close to call <span class="muted">(${o.my_gain > 0 ? "you" : esc(them)} by ${num(Math.abs(o.my_gain), 0)} ${unit()})</span>`
        : o.my_gain > 0 ? `<span class="pos">You win by ${num(o.my_gain, 0)} ${unit()}</span>`
        : `<span class="neg">${esc(them)} wins by ${num(-o.my_gain, 0)} ${unit()}</span>`;
      $("pt-offer").innerHTML = `<div class="team-card offer"><h4>${head}</h4>
        <div class="muted">your best nightly lineup through ${esc(ptData.ros_end)}, playoff games x${num(ptData.playoff_game_weight, 1)}</div>
        <div>Give ${pl(o.give).replace(/<br>/g, " + ")} · get ${pl(o.get).replace(/<br>/g, " + ")}</div>
        <div>They'll probably see it as: <b>${sentence(-o.their_view, them)}</b></div>
        <div>Next ${nd} days: <b>${sentence(o.my_fit - o.their_fit, them)}</b>
          <span class="muted">(you ${signed(o.my_fit)}, them ${signed(o.their_fit)})</span></div>
        <div class="muted">
          ${(o.my_adds || []).length ? `You'd add free agent ${o.my_adds.map((i) => esc(P[i].name)).join(", ")}. ` : ""}
          ${o.my_cut.length ? `You'd cut ${o.my_cut.map((i) => esc(P[i].name)).join(", ")}` : ""}
          ${o.their_cut.length ? ` They'd cut ${o.their_cut.map((i) => esc(P[i].name)).join(", ")}` : ""}</div></div>`;
    } else $("pt-offer").innerHTML = "";
    const rows = ptData.deals.filter((x) => !$("pt-sweet").checked || x.sweet_spot);
    const P2 = (ids) => ids.map((i) => P[i].name).join(", ");
    const req = ptData.constrained
      ? `${ptData.found} deals including your ticks (${[P2(ticked("pt-give").map(Number).filter((i) => P[i])),
          P2(ticked("pt-get").map(Number).filter((i) => P[i]))].filter(Boolean).join(" / ")}), best for you first${$("pt-sweet").checked ? ", likely-accept only" : ""}`
      : `${ptData.found} deals you win with ${ptData.partner.name}`
        + ` (best ${ptData.scored} of ${ptData.considered} packages lineup-simulated)`;
    $("pt-meta").textContent = `${req}; ${rows.length} shown`
      + ` · market data ${ptData.market_fetched || "none (press Refresh data)"}`;
    const el = $("pt-table");
    el._sort = {};
    table(el, cols, rows, { page: 20, empty: "No deals pass these filters. Untick \"only ones they'd likely take\" to see more." });
  }
  $("pt-run").addEventListener("click", async () => {
    const give = ticked("pt-give"), get = ticked("pt-get");
    if (give.length > 2 || get.length > 2) return status("Tick at most 2 players a side.", true);
    if (Math.max(give.length, get.length) > Number($("pt-size").value))
      return status("Ticked more players than the deal size allows.", true);
    status("Trying every package with this team (a few seconds)…");
    try {
      ptData = await api(`/api/inseason/partner-trades?${new URLSearchParams({
        league: $("league").value, start: $("start").value, days: $("league-days").value,
        partner: $("pt-team").value, size: $("pt-size").value,
        give: give.join(","), get: get.join(",") })}`);
      drawDeals();
      status("");
    } catch (e) { status(e.message, true); }
  });
  $("pt-team").addEventListener("change", () => { ptData = null; drawPick(); $("pt-table").innerHTML = ""; $("pt-meta").textContent = ""; clearSort("pt-table"); });
  const clearSort = (id) => { const bar = $(id).closest(".scroll").previousElementSibling; if (bar && bar.classList.contains("tbl-sort")) bar.hidden = true; };
  $("pt-sweet").addEventListener("change", drawDeals);

  // ── shop a player: deals from "looks like I win" to "looks like they win"
  let shopData = null;
  function fillShop(L) {
    const me = L.teams.find((t) => t.mine);
    const sel = $("shop-give"), prev = sel.value;
    sel.innerHTML = (me ? me.players : []).map((p) =>
      `<option value="${p.id}">${esc(p.name)} (${esc(p.elig.join(","))}, ${num(p.per_game)}${p.out_until ? `, injured, back ~${md(p.out_until)}` : ""})</option>`).join("");
    if (prev && [...sel.options].some((o) => o.value === prev)) sel.value = prev;
  }
  const leanCell = (x) => `<span title="${esc(x.lean)} ${signed(100 * x.tilt, 0)}% of your player's value">${
    winner(x.market_edge, x.partner)}</span>`;
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
      { key: "star", label: "★", title: "you gain, and to them it looks even or in their favour", sort: (x) => (x.sweet_spot ? 1 : 0),
        fmt: (x) => (x.sweet_spot ? '<span title="worth proposing">★</span>' : '<span class="muted">–</span>') },
      { key: "partner", label: "With", left: true },
      { key: "get", label: "You get", left: true, stick: true, sort: (x) => x.get.name,
        fmt: (x) => `${esc(x.get.name)} <span class="muted">${esc(x.get.team)} ${esc(x.get.elig.join(","))}</span>` },
      { key: "pg", label: "Model / mkt", title: "per game: your model vs the market's view",
        sort: (x) => x.get.per_game, fmt: (x) => `${num(x.get.per_game)} / ${num(x.get.market_pg)}` },
      { key: "pct_ros", label: "% Ros", minor: true, title: "% of Yahoo leagues rostering him",
        sort: (x) => x.get.pct_ros, fmt: (x) => (x.get.pct_ros == null ? "–" : `${num(x.get.pct_ros, 0)}%`) },
      { key: "pre_rank", label: "Pre", minor: true, title: "Yahoo pre-season rank (name value)",
        sort: (x) => (x.get.pre_rank == null ? null : -x.get.pre_rank),
        fmt: (x) => (x.get.pre_rank == null ? "–" : `#${num(x.get.pre_rank, 0)}`) },
      { key: "market_edge", label: "They'll see", left: true, title: "how a typical manager sees it (tilt vs the value of your player)",
        fmt: leanCell },
      { key: "my_gain", label: "Winner", left: true, title: `your lineup, ${unit()}, through ${shopData.ros_end} (playoff games x${shopData.playoff_game_weight})`,
        fmt: (x) => winner(x.my_gain, x.partner, 0, x.close) },
      { key: "my_fit", label: "Fit me", fmt: (x) => `<span class="${cls(x.my_fit)}">${signed(x.my_fit)}</span>` },
      { key: "their_fit", label: "Fit them", minor: true, fmt: (x) => `<span class="${cls(x.their_fit)}">${signed(x.their_fit)}</span>` },
    ], rows, { page: 20, empty: "No deals pass these filters." });
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

  // Deep link from the Recap takes: open the League tab and run "Trade with
  // a team" (partner + players to get ticked) or "Shop a player" (give).
  let pending = null;
  function applyPending() {
    const p = pending; pending = null;
    if (!p) return;
    if (p.partner != null) {
      if (![...$("pt-team").options].some((o) => o.value === String(p.partner))) {
        status("That team isn't in the league rosters any more: press Refresh data.", true);
        return;
      }
      $("pt-team").value = String(p.partner);
      drawPick();
      const missing = (p.get || []).filter((id) => {
        const b = document.querySelector(`.pt-get[value="${id}"]`);
        if (b) b.checked = true;
        return !b;
      });
      if (missing.length) {
        status("That player isn't on their roster any more (traded or dropped since the last refresh): press Refresh data.", true);
        return;
      }
      $("pt-pick").scrollIntoView({ behavior: "smooth", block: "start" });
      $("pt-run").click();
    } else if (p.give != null) {
      if (![...$("shop-give").options].some((o) => o.value === String(p.give))) {
        status("That player isn't on your roster any more: press Refresh data.", true);
        return;
      }
      $("shop-give").value = String(p.give);
      $("shop-give").scrollIntoView({ behavior: "smooth", block: "center" });
      $("shop-run").click();
    }
  }
  // "Playing for": the trade_focus setting (settings gear) -- saved, then
  // the trade tools recompute with the new game weights
  const FOCUS = { playoffs: "the title", balanced: "balanced", make_playoffs: "making the playoffs" };
  async function loadFocus() {
    try {
      const r = await api(`/api/inseason/standings?league=${encodeURIComponent($("league").value)}`);
      $("trade-focus").value = r.trade_focus;
      drawStandings(r);
    } catch { /* the select keeps its value */ }
  }
  // standings + playoff odds (standings.py), and what "auto" picked
  function drawStandings(r) {
    const s = r.standings;
    const note = $("trade-focus-note");
    if (!s) {
      $("standings-meta").textContent = "No standings yet: press Refresh data (head-to-head leagues only).";
      $("standings-table").innerHTML = "";
      note.textContent = r.trade_focus === "auto" ? "Auto needs standings: using balanced until you refresh." : note.textContent;
      return;
    }
    const pct = (x) => (x == null ? "–" : x >= 0.995 ? ">99%" : x <= 0.005 ? "<1%" : `${Math.round(x * 100)}%`);
    $("standings-meta").textContent = `${s.weeks_played} weeks played, ${s.weeks_left} left before the playoffs`
      + `${s.spots ? ` (top ${s.spots} make it)` : ""}. Odds simulate the rest of the regular season from points for per week;`
      + ` read from Yahoo ${String(s.fetched || "").replace("T", " at ")}.`;
    table($("standings-table"), [
      { key: "name", label: "Team", left: true, stick: true, sort: (t) => t.rank,
        fmt: (t) => `${t.rank}. ${esc(t.name)}${t.mine ? ' <span class="flag ok">you</span>' : ""}` },
      { key: "wlt", label: "W-L-T", sort: (t) => t.w + 0.5 * t.t, fmt: (t) => `${t.w}-${t.l}-${t.t}` },
      { key: "odds", label: "Playoff odds", fmt: (t) => `<b>${pct(t.odds)}</b>` },
      { key: "pts", label: "Pts for", minor: true, fmt: (t) => num(t.pts, 0) },
      { key: "per_week", label: "Per week", minor: true, fmt: (t) => num(t.per_week, 0) },
      { key: "mean_seed", label: "Avg finish", minor: true, title: "average final regular-season rank", fmt: (t) => (t.mean_seed ? num(t.mean_seed, 1) : "–") },
    ], s.teams, { rowClass: (t) => (t.mine ? "mine" : "") });
    if (r.trade_focus === "auto") {
      note.innerHTML = s.my_odds == null ? "Auto: your team isn't in the standings, so balanced."
        : `Auto: your playoff odds are <b>${pct(s.my_odds)}</b>, so trades play for <b>${FOCUS[r.focus]}</b>.`
          + (r.focus === "playoffs" ? " An injured star back before your playoffs is worth the weeks without him." : "");
    } else {
      note.textContent = `Set by hand to ${FOCUS[r.trade_focus]}${s.my_odds != null ? ` (your playoff odds: ${pct(s.my_odds)})` : ""}.`;
    }
  }
  $("trade-focus").addEventListener("change", async () => {
    const sel = $("trade-focus");
    sel.disabled = true;
    try {
      await api("/api/inseason/settings", { method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ values: { trade_focus: sel.value } }) });
      window.insKit.reload?.();
    } catch (e) { status(e.message, true); } finally { sel.disabled = false; }
  });
  window.addEventListener("ins-settings-changed", loadFocus);

  window.insPartner = {
    fill: (L) => { fillShop(L); fill(L); applyPending(); loadFocus(); },
    open: (req) => { pending = req; window.insKit.showTab("league"); },
  };
})();
