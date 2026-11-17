// Moves tab: every team's adds, drops, trades and IR moves
// (/api/inseason/moves, built from the roster snapshot each Refresh takes;
// see src/inseason/moves.py). Uses window.insKit from static/inseason.js.
(function () {
  "use strict";
  const { $, esc, num, api, status, md, dow, loaders, unit } = window.insKit;
  let data = null;
  let team = "";                   // "" = every team, else a team id

  // filter group for each event type
  const GROUP = { add: "add", claim: "add", drop: "drop", trade: "trade", ir: "ir", activate: "ir" };
  const LABEL = { add: "Add", claim: "Claim", drop: "Drop", trade: "Trade", ir: "To IR", activate: "Off IR" };

  const when = (ts) => {
    if (!ts) return "?";
    const d = new Date(ts);
    if (Number.isNaN(+d)) return ts;
    return `${dow(ts.slice(0, 10))} ${d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;
  };
  const dayTitle = (ts) => new Date(ts.slice(0, 10) + "T12:00:00")
    .toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });
  const who = (p) => (p.id != null
    ? `<button type="button" class="plink" data-pid="${p.id}">${esc(p.name)}</button>` : esc(p.name));
  const desc = (p) => [p.elig.join(","), p.team].filter(Boolean).map(esc).join(", ");
  const pg = (p) => (p.per_game == null ? '<span class="muted">no stats</span>'
    : `<b>${num(p.per_game)}</b><span class="muted">${esc(unit())} per game</span>`);

  loaders.moves = async () => {
    status("Loading roster moves…");
    try {
      data = await api(`/api/inseason/moves?${new URLSearchParams({
        league: $("league").value, days: $("moves-days").value })}`);
      const sel = $("moves-team");
      if (team && !data.teams.some((t) => String(t.id) === team)) team = "";
      sel.innerHTML = '<option value="">Every team</option>' + data.teams.slice()
        .sort((a, b) => (b.mine - a.mine) || a.name.localeCompare(b.name))
        .map((t) => `<option value="${t.id}">${esc(t.name)}${t.mine ? " (you)" : ""}</option>`).join("");
      sel.value = team;
      draw();
      status("");
    } catch (e) {
      data = null;
      $("moves-teams").innerHTML = "";
      $("moves-feed").innerHTML = `<div class="feed-empty">${esc(e.message)}</div>`;
      $("moves-meta").textContent = "";
      status("");
    }
  };

  function draw() {
    if (!data) return;
    const since = data.tracking_since ? `${md(data.tracking_since.slice(0, 10))}` : "the first refresh";
    $("moves-meta").textContent = `Changes Refresh data has seen on every team since ${since}, newest first.`
      + " A player added and dropped again between two refreshes won't show.";
    drawTally();
    const types = new Set([...document.querySelectorAll("#moves-types input:checked")].map((x) => x.value));
    const keep = data.events.filter((e) => types.has(GROUP[e.type])
      && (!team || String(e.team_id) === team || String(e.from_team_id) === team));
    $("moves-feed").innerHTML = keep.length ? feed(keep)
      : `<div class="feed-empty">${data.events.length
        ? "No moves match these filters."
        : `No roster moves in the last ${esc($("moves-days").selectedOptions[0].textContent)}.`}</div>`;
  }

  function drawTally() {
    $("moves-teams").innerHTML = data.teams.map((t) => {
      const n = t.add + t.drop + t.trade + t.ir;
      const bits = [[t.add, "add"], [t.drop, "drop"], [t.trade, "trade"], [t.ir, "IR move"]]
        .filter(([k]) => k).map(([k, w]) => `${k} ${w}${k === 1 ? "" : "s"}`);
      return `<button type="button" class="tally${t.mine ? " mine" : ""}${String(t.id) === team ? " on" : ""}${n ? "" : " quiet"}"
        data-team="${t.id}" aria-pressed="${String(t.id) === team}">
        <b>${esc(t.name)}</b><span class="muted">${bits.join(", ") || "no moves"}</span></button>`;
    }).join("");
  }

  // one list per day; a trade's players are gathered into one entry
  function feed(events) {
    const days = new Map();
    const trades = new Map();
    for (const e of events) {
      const day = (e.at || "").slice(0, 10);
      if (!days.has(day)) days.set(day, []);
      if (e.type === "trade") {
        const pair = [e.team_id, e.from_team_id].sort((a, b) => a - b).join("-");
        const key = `${e.at}|${pair}`;
        if (!trades.has(key)) {
          const t = { type: "trade", at: e.at, since: e.since, sides: new Map() };
          trades.set(key, t);
          days.get(day).push(t);
        }
        const sides = trades.get(key).sides;
        if (!sides.has(e.team_id)) sides.set(e.team_id, { team: e.team, players: [] });
        if (!sides.has(e.from_team_id)) sides.set(e.from_team_id, { team: e.from_team, players: [] });
        sides.get(e.team_id).players.push(e.player);
      } else days.get(day).push(e);
    }
    const mine = data.my_team_id;
    return [...days].map(([day, list]) => `<section class="feed-day">
      <h4>${day ? esc(dayTitle(day)) : "Unknown date"}</h4>
      <ul>${list.map((e) => (e.type === "trade" ? tradeItem(e, mine) : moveItem(e, mine))).join("")}</ul></section>`).join("");
  }

  function moveItem(e, mine) {
    const p = e.player;
    const verb = { add: "added", claim: "claimed", drop: "dropped", ir: "moved to IR", activate: "brought back from IR" }[e.type];
    const from = e.type === "claim" && e.from_team ? `, let go by ${esc(e.from_team)}` : "";
    return `<li class="move${e.team_id === mine ? " mine" : ""}">
      <span class="kind ${e.type}">${LABEL[e.type]}</span>
      <div class="move-what"><span class="move-team">${esc(e.team)}</span> ${verb} ${who(p)}
        <div class="move-detail">${desc(p)}${from}. Seen ${esc(when(e.at))}, after ${esc(when(e.since))}</div></div>
      <div class="move-pg">${pg(p)}</div></li>`;
  }

  function tradeItem(t, mine) {
    const sides = [...t.sides.entries()];
    const involves = sides.some(([id]) => id === mine);
    return `<li class="move${involves ? " mine" : ""}">
      <span class="kind trade">Trade</span>
      <div class="move-what"><span class="move-team">${sides.map(([, s]) => esc(s.team)).join(" and ")}</span>
        <div class="trade-sides">${sides.map(([, s]) => `<div><span class="move-detail">${esc(s.team)} got</span><br>${
          s.players.length ? s.players.map((p) => `${who(p)} <span class="muted">${desc(p)}${
            p.per_game == null ? "" : `, ${num(p.per_game)} per game`}</span>`).join("<br>")
            : '<span class="muted">nobody</span>'}</div>`).join("")}</div>
        <div class="move-detail">Seen ${esc(when(t.at))}, after ${esc(when(t.since))}</div></div>
      <div class="move-pg"></div></li>`;
  }

  $("moves-days").addEventListener("change", () => loaders.moves());
  $("moves-team").addEventListener("change", () => { team = $("moves-team").value; draw(); });
  $("moves-types").addEventListener("change", draw);
  $("moves-teams").addEventListener("click", (e) => {
    const b = e.target.closest(".tally");
    if (!b) return;
    team = team === b.dataset.team ? "" : b.dataset.team;
    $("moves-team").value = team;
    draw();
  });
})();
