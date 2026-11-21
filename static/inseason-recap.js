// Recap tab: last week in review and next week (/api/inseason/recap,
// src/api/inseason_recap.py). Uses window.insKit from inseason.js.
(function () {
  "use strict";
  const { $, esc, num, api, status, md, dow, loaders, table, signed, cls } = window.insKit;
  let n = 0;

  const who = (p) => `<button type="button" class="plink" data-pid="${p.id}">${esc(p.name)}</button>
    <span class="muted">${esc(p.pos)}, ${esc(p.team)}</span>`;
  const owner = (p) => (p.owner ? `${esc(p.owner)}${p.mine ? ' <span class="flag ok">you</span>' : ""}` : '<span class="flag good">free agent</span>');
  const role = (r) => (r && r.label ? esc(r.label) : '<span class="muted">–</span>');

  // one titled table; ids are made up per draw
  function section(title, note, cols, rows, empty) {
    const id = `rc-${++n}`;
    return { html: `<section class="recap-sec"><h3>${esc(title)}</h3>${note ? `<p class="muted">${note}</p>` : ""}
      <div class="scroll"><table id="${id}"></table></div></section>`,
    draw: () => table($(id), cols, rows, { empty, page: 10 }) };
  }

  loaders.recap = async () => {
    status("Building the recap…");
    try {
      const r = await api(`/api/inseason/recap?league=${encodeURIComponent($("league").value)}`);
      draw(r);
      status("");
    } catch (e) {
      $("recap-body").innerHTML = `<div class="feed-empty">${esc(e.message)}</div>`;
      status("");
    }
  };

  function draw(r) {
    const u = r.unit;
    const w = r.week_window;
    $("recap-meta").textContent = (w ? `Last 7 days: ${md(w.start)} to ${md(w.end)}. ` : "")
      + `Next week: ${md(r.next_week.dates[0])} to ${md(r.next_week.dates[6])}.`;
    const pl = { key: "name", label: "Player", left: true, stick: true, sort: (p) => p.name, fmt: who };
    const ow = { key: "owner", label: "Team", left: true, sort: (p) => p.owner || "~", fmt: owner };
    const vs = (p) => `<b>${num(p.window.per_game)}</b> <span class="${cls(p.window.per_game - p.window.base)}">${signed(p.window.per_game - p.window.base, 2)}</span>`;
    const secs = [
      section("Top 10 of the week", `Most ${esc(u)} over the last 7 days.`, [pl, ow,
        { key: "games", label: "GP", sort: (p) => p.window.games, fmt: (p) => p.window.games },
        { key: "total", label: "Total", sort: (p) => p.window.total, fmt: (p) => `<b>${num(p.window.total, 1)}</b>` },
        { key: "role", label: "Role", left: true, sort: (p) => p.role?.label || "~", fmt: (p) => role(p.role) }],
      r.top_week, "No games in the last 7 days."),
      section("Heating up", `Last 14 days per game vs his blend.`, [pl, ow,
        { key: "gp", label: "GP", sort: (p) => p.window.games, fmt: (p) => p.window.games },
        { key: "pg", label: "Per game", sort: (p) => p.window.per_game - p.window.base, fmt: vs }],
      r.heating_up, "Nobody is running 20% above his rate."),
      section("Cooling off", null, [pl, ow,
        { key: "gp", label: "GP", sort: (p) => p.window.games, fmt: (p) => p.window.games },
        { key: "pg", label: "Per game", sort: (p) => p.window.per_game - p.window.base, fmt: vs },
        { key: "luck", label: "Luck", left: true, sort: (p) => p.luck?.status || "~",
          fmt: (p) => (p.luck?.status === "cold" ? '<span class="flag good">due to bounce back</span>' : '<span class="muted">–</span>') }],
      r.cooling_off, "Nobody is running 20% below his rate."),
      section("Line and power-play changes", "From the last game's shift data vs the games before.", [pl, ow,
        { key: "now", label: "Now", left: true, sort: (p) => p.role.label || "~", fmt: (p) => `<b>${role(p.role)}</b>` },
        { key: "was", label: "Was", left: true, sort: (p) => p.role.usual_line || "~",
          fmt: (p) => esc([p.role.usual_line, p.role.usual_pp || "no PP"].filter(Boolean).join(", ")) },
        { key: "chg", label: "", sort: (p) => p.role.change, fmt: (p) => (p.role.change === "up" ? '<span class="pos">▲ up</span>' : '<span class="neg">▼ down</span>') }],
      r.role_changes, "No line or PP changes."),
      section("Luck", "Shooting or save % far from his usual.", [pl, ow,
        { key: "st", label: "", left: true, sort: (p) => p.luck.status,
          fmt: (p) => (p.luck.status === "hot" ? '<span class="flag">running hot</span>' : '<span class="flag good">due to bounce back</span>') },
        { key: "txt", label: "Detail", left: true, wide: true, sort: (p) => -Math.abs(p.luck.z), fmt: (p) => esc(p.luck.text || "") }],
      r.luck, "Nobody is far from his usual."),
      section("Bandwagon adds", "Free agents in your league whose % rostered across Yahoo rose most this week.", [pl,
        { key: "pct", label: "% Ros", sort: (p) => p.rostered.pct, fmt: (p) => `${Math.round(p.rostered.pct)}% <span class="pos">▲${Math.round(p.rostered.d7)}</span>` },
        { key: "pg", label: "Value", sort: (p) => p.per_game, fmt: (p) => num(p.per_game) },
        { key: "backed", label: "Numbers say", left: true, sort: (p) => (p.backed ? 1 : 0),
          fmt: (p) => (p.backed ? '<span class="flag good">worth it</span>' : `<span class="flag">not yet</span> <span class="muted">bar ${num(p.bar)}</span>`) }],
      r.bandwagon, "Needs two refreshes a few days apart to see a change."),
      section("Next week: skater streams", "Free agents whose team plays 4+ games, by projected points for the week.", [pl,
        { key: "games", label: "Games" },
        { key: "value", label: "Projected", fmt: (p) => `<b>${num(p.value, 1)}</b>` },
        { key: "role", label: "Role", left: true, sort: (p) => p.role?.label || "~", fmt: (p) => role(p.role) }],
      r.next_week.skater_streams, "No free agent plays 4 games next week."),
      section("Next week: goalie streams", null, [pl,
        { key: "games", label: "Games" },
        { key: "ss", label: "Starts", title: "share of his team's games he starts", fmt: (p) => `${Math.round(p.start_share * 100)}%` },
        { key: "value", label: "Projected", fmt: (p) => `<b>${num(p.value, 1)}</b>` }],
      r.next_week.goalie_streams, "No free-agent goalies to stream."),
    ];
    const teams = r.next_week.teams;
    const nights = r.next_week.dates.map((d) => `<li><b>${dow(d)} ${md(d)}</b> ${r.next_week.nights[d] || 0} games</li>`).join("");
    const busy = teams.filter((t) => t.games === teams[0]?.games).map((t) => t.team);
    const light = teams.filter((t) => t.games === teams[teams.length - 1]?.games).map((t) => t.team);
    const sched = `<section class="recap-sec"><h3>Next week's schedule</h3>
      <p>Busiest: <b>${esc(busy.join(", ") || "–")}</b> (${teams[0]?.games ?? 0} games).
        Lightest: <b>${esc(light.join(", ") || "–")}</b> (${teams[teams.length - 1]?.games ?? 0}).</p>
      <ul class="nights">${nights}</ul></section>`;
    $("recap-body").innerHTML = secs.map((x) => x.html).join("") + sched;
    secs.forEach((x) => x.draw());
  }
})();
