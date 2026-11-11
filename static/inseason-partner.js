// League tab, "Trade with a team": every 1-1 .. 2-2 package with one team
// (/api/inseason/partner-trades) and scoring an offer you got. Uses the
// helpers static/inseason.js exposes as window.insKit.
(function () {
  "use strict";
  const { $, esc, num, signed, winner, table, api, status, unit, league } = window.insKit;
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
      `<label><input type="checkbox" class="${cls}" value="${p.id}">${esc(p.name)}
        <span class="muted">${esc(p.elig.join(","))} ${num(p.per_game)}</span></label>`).join("")}</div>`;
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
    const P = Object.fromEntries([...ptData.mine, ...ptData.theirs].map((p) => [p.id, p]));
    const pl = (ids) => ids.map((i) => `${esc(P[i].name)} <span class="muted">${esc(P[i].elig.join(","))}</span>`).join("<br>");
    const cut = (ids, who) => (ids.length ? `<span class="deal-cut muted">${who} cut ${ids.map((i) => esc(P[i].name)).join(", ")}</span>` : "");
    const cols = [
      { key: "star", label: "★", sort: (x) => (x.sweet_spot ? 1 : 0), fmt: (x) => (x.sweet_spot ? "★" : "") },
      { key: "give", label: "You give", left: true, stick: true, sort: (x) => P[x.give[0]].name,
        fmt: (x) => pl(x.give) + cut(x.their_cut, "they") },
      { key: "get", label: "You get", left: true, sort: (x) => P[x.get[0]].name,
        fmt: (x) => pl(x.get) + cut(x.my_cut, "you") },
      { key: "my_gain", label: "Winner", title: `your lineup, ${unit()}, through ${ptData.ros_end} (playoff games x${ptData.playoff_game_weight})`,
        fmt: (x) => winner(x.my_gain, them) },
      { key: "tilt", label: "They'll see", title: "how it looks to a typical manager (Yahoo % rostered / Fan Pts / rank)",
        sort: (x) => -x.their_view, fmt: (x) => lookCell(x, them) },
      { key: "fit", label: `Next ${nd} days`, title: "change in each lineup's total", sort: (x) => x.my_fit - x.their_fit,
        fmt: (x) => `${winner(x.my_fit - x.their_fit, them, 1)}<span class="deal-cut muted">you ${signed(x.my_fit)} · them ${signed(x.their_fit)}</span>` },
    ];
    const o = ptData.offer;
    if (o) {
      const head = Math.abs(o.my_gain) < 0.5 ? "Even trade"
        : o.my_gain > 0 ? `<span class="pos">You win by ${num(o.my_gain, 0)} ${unit()}</span>`
        : `<span class="neg">${esc(them)} wins by ${num(-o.my_gain, 0)} ${unit()}</span>`;
      $("pt-offer").innerHTML = `<div class="team-card"><h4>${head}</h4>
        <div class="muted">your best nightly lineup through ${esc(ptData.ros_end)}, playoff games x${num(ptData.playoff_game_weight, 1)}</div>
        <div>Give ${pl(o.give).replace(/<br>/g, " + ")} · get ${pl(o.get).replace(/<br>/g, " + ")}</div>
        <div>They'll probably see it as: <b>${sentence(-o.their_view, them)}</b></div>
        <div>Next ${nd} days: <b>${sentence(o.my_fit - o.their_fit, them)}</b>
          <span class="muted">(you ${signed(o.my_fit)}, them ${signed(o.their_fit)})</span></div>
        <div class="muted">
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
    table(el, cols, rows);
    if (!rows.length) el.innerHTML += '<tbody><tr><td class="l" colspan="6">No deals pass these filters.</td></tr></tbody>';
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
  $("pt-team").addEventListener("change", () => { ptData = null; drawPick(); $("pt-table").innerHTML = ""; $("pt-meta").textContent = ""; });
  $("pt-sweet").addEventListener("change", drawDeals);

  window.insPartner = { fill };
})();
