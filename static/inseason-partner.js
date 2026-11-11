// League tab, "Trade with a team": every 1-1 .. 2-2 package with one team
// (/api/inseason/partner-trades) and scoring an offer you got. Uses the
// helpers static/inseason.js exposes as window.insKit.
(function () {
  "use strict";
  const { $, esc, num, signed, cls, table, api, status, unit, league } = window.insKit;
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
  function lookCell(x) {
    const k = x.lean === "they win" ? "p1" : x.lean === "you win" ? "n1" : "";
    return `<span class="heat ${k}" title="they gain ${signed(x.their_view, 0)} as the market sees it">${esc(x.lean)}
      ${signed(100 * x.tilt, 0)}%</span>`;
  }
  function drawDeals() {
    if (!ptData) return;
    const P = Object.fromEntries([...ptData.mine, ...ptData.theirs].map((p) => [p.id, p]));
    const pl = (ids) => ids.map((i) => `${esc(P[i].name)} <span class="muted">${esc(P[i].elig.join(","))}</span>`).join("<br>");
    const cut = (ids, who) => (ids.length ? `<span class="deal-cut muted">${who} cut ${ids.map((i) => esc(P[i].name)).join(", ")}</span>` : "");
    const cols = [
      { key: "star", label: "★", sort: (x) => (x.sweet_spot ? 1 : 0), fmt: (x) => (x.sweet_spot ? "★" : "") },
      { key: "give", label: "You give", left: true, stick: true, sort: (x) => P[x.give[0]].name,
        fmt: (x) => pl(x.give) + cut(x.their_cut, "they") },
      { key: "get", label: "You get", left: true, sort: (x) => P[x.get[0]].name,
        fmt: (x) => pl(x.get) + cut(x.my_cut, "you") },
      { key: "my_gain", label: "My gain", title: `rest of season, ${unit()} above replacement, your model`,
        fmt: (x) => `<b class="${cls(x.my_gain)}">${signed(x.my_gain, 0)}</b>` },
      { key: "tilt", label: "Looks to them", fmt: lookCell },
      { key: "my_fit", label: "Fit me", fmt: (x) => `<span class="${cls(x.my_fit)}">${signed(x.my_fit)}</span>` },
      { key: "their_fit", label: "Fit them", minor: true, fmt: (x) => `<span class="${cls(x.their_fit)}">${signed(x.their_fit)}</span>` },
    ];
    const o = ptData.offer;
    if (o) {
      const verdictTxt = o.my_gain > 0 ? '<b class="pos">you win</b>' : '<b class="neg">you lose</b>';
      $("pt-offer").innerHTML = `<div class="team-card"><h4>This offer: ${verdictTxt} ${signed(o.my_gain, 0)} ${unit()}</h4>
        <div>Give ${pl(o.give).replace(/<br>/g, " + ")} · get ${pl(o.get).replace(/<br>/g, " + ")}</div>
        <div class="muted">Looks to them: ${esc(o.lean)} (${signed(o.their_view, 0)}) · next ${ptData.dates.length} days:
          you ${signed(o.my_fit)}, them ${signed(o.their_fit)}
          ${o.my_cut.length ? ` · you'd cut ${o.my_cut.map((i) => esc(P[i].name)).join(", ")}` : ""}
          ${o.their_cut.length ? ` · they'd cut ${o.their_cut.map((i) => esc(P[i].name)).join(", ")}` : ""}</div></div>`;
    } else $("pt-offer").innerHTML = "";
    const rows = ptData.deals.filter((x) => !$("pt-sweet").checked || x.sweet_spot);
    const P2 = (ids) => ids.map((i) => P[i].name).join(", ");
    const req = ptData.constrained
      ? `${ptData.found} deals including your ticks (${[P2(ticked("pt-give").map(Number).filter((i) => P[i])),
          P2(ticked("pt-get").map(Number).filter((i) => P[i]))].filter(Boolean).join(" / ")}), best for you first${$("pt-sweet").checked ? ", likely-accept only" : ""}`
      : `${ptData.found} deals you win with ${ptData.partner.name}`;
    $("pt-meta").textContent = `${req}; ${rows.length} shown`
      + ` · market data ${ptData.market_fetched || "none (press Refresh data)"}`;
    const el = $("pt-table");
    el._sort = {};
    table(el, cols, rows);
    if (!rows.length) el.innerHTML += '<tbody><tr><td class="l" colspan="7">No deals pass these filters.</td></tr></tbody>';
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
