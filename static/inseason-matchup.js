// Matchup tab: this week's head-to-head, projected (/api/inseason/matchup;
// model in src/inseason/matchup.py). Uses window.insKit from inseason.js.
(function () {
  "use strict";
  const { $, esc, num, api, status, md, dow, loaders, table } = window.insKit;
  const pctTxt = (x) => `${Math.round(x * 100)}%`;

  loaders.matchup = async () => {
    status("Projecting this week…");
    try {
      const q = new URLSearchParams({ league: $("league").value });
      const s = $("mu-start").value;
      if (s) {
        const d = new Date();
        if (s === "tomorrow") d.setDate(d.getDate() + 1);
        q.set("start", `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`);
      }
      draw(await api(`/api/inseason/matchup?${q}`));
      status("");
    } catch (e) {
      $("mu-board").innerHTML = `<div class="feed-empty">${esc(e.message)}</div>`;
      ["mu-nights", "mu-adds", "mu-league"].forEach((id) => { $(id).innerHTML = ""; });
      $("mu-meta").textContent = "";
      $("mu-adds-note").textContent = "";
      status("");
    }
  };

  function side(t, cls) {
    return `<div class="mu-side ${cls}">
      <div class="mu-name">${esc(t.name)}</div>
      <div class="mu-final">${num(t.final, 1)}</div>
      <div class="muted">${num(t.score, 1)} now + ${num(t.rest, 1)} to come (±${num(t.sd, 0)})</div>
      <div class="mu-win"><b>${pctTxt(t.win)}</b> to win</div>
    </div>`;
  }

  function draw(m) {
    const you = m.you, them = m.them;
    $("mu-title").textContent = `Week ${m.week ?? "?"}: ${you.name} vs ${them.name}`;
    const left = m.remaining.length;
    $("mu-meta").textContent = `${md(m.dates[0])} to ${md(m.dates[m.dates.length - 1])}. `
      + (left ? `${left} day${left === 1 ? "" : "s"} still to play, from ${dow(m.start)} ${md(m.start)}.` : "The week is over.")
      + ` Scores read from Yahoo ${m.scores_read ? m.scores_read.replace("T", " at ") : "(never)"}.`;
    const lead = you.final - them.final;
    $("mu-board").innerHTML = `${side(you, lead >= 0 ? "ahead" : "behind")}
      <div class="mu-mid"><div class="mu-bar" role="img" aria-label="${pctTxt(you.win)} chance you win">
        <span style="width:${Math.round(you.win * 100)}%"></span></div>
        <div class="muted">Projected ${lead >= 0 ? "win" : "loss"} by <b>${num(Math.abs(lead), 1)}</b>.
          Yahoo projects ${num(you.yahoo_proj, 1)} to ${num(them.yahoo_proj, 1)}.</div></div>
      ${side(them, lead < 0 ? "ahead" : "behind")}`;

    const theirs = Object.fromEntries(them.nights.map((n) => [n.date, n]));
    table($("mu-nights"), [
      { key: "date", label: "Night", left: true, fmt: (n) => `<b>${dow(n.date)} ${md(n.date)}</b>` },
      { key: "you_playing", label: "You: playing", sort: (n) => n.playing, fmt: (n) => `${n.playing} <span class="muted">(${n.starters} start)</span>` },
      { key: "you_mean", label: "You: pts", sort: (n) => n.mean, fmt: (n) => `<b>${num(n.mean, 1)}</b>` },
      { key: "them_playing", label: "Them: playing", sort: (n) => theirs[n.date]?.playing,
        fmt: (n) => `${theirs[n.date]?.playing ?? 0} <span class="muted">(${theirs[n.date]?.starters ?? 0} start)</span>` },
      { key: "them_mean", label: "Them: pts", sort: (n) => theirs[n.date]?.mean, fmt: (n) => num(theirs[n.date]?.mean ?? 0, 1) },
      { key: "edge", label: "Edge", sort: (n) => n.mean - (theirs[n.date]?.mean ?? 0), fmt: (n) => {
        const d = n.mean - (theirs[n.date]?.mean ?? 0);
        return `<span class="${d >= 0 ? "pos" : "neg"}">${d >= 0 ? "+" : "−"}${num(Math.abs(d), 1)}</span>`; } },
    ], you.nights, { empty: "No days left to play this week." });

    const mine = you.id === m.my_team_id;
    $("mu-adds-note").textContent = !mine ? "Swing adds are shown for your own team only."
      : m.adds.length ? `Each replaces ${m.drop_pool.length ? m.drop_pool[0].name : "your weakest non-HOLD player"} unless noted.`
        : m.drop_pool.length ? "No free agent adds to your lineup this week."
          : "Everyone on your roster is a HOLD, so nobody is dropped for a one-week stream.";
    table($("mu-adds"), [
      { key: "add", label: "Add", left: true, stick: true, sort: (a) => a.add.name,
        fmt: (a) => `<button type="button" class="plink" data-pid="${a.add.id}">${esc(a.add.name)}</button>
          <span class="muted">${esc(a.add.pos)}, ${esc(a.add.team)}</span>${a.waiver_until ? ` <span class="flag">waivers to ${md(a.waiver_until)}</span>` : ""}` },
      { key: "drop", label: "Drop", left: true, sort: (a) => a.drop.name,
        fmt: (a) => `<button type="button" class="plink" data-pid="${a.drop.id}">${esc(a.drop.name)}</button>` },
      { key: "games", label: "Games", title: "his games left this week" },
      { key: "gain", label: "Adds", title: "points added to your projected total", fmt: (a) => `<b class="pos">+${num(a.gain, 1)}</b>` },
      { key: "win", label: "Win chance", fmt: (a) => `${pctTxt(you.win)} → <b>${pctTxt(a.win)}</b>` },
    ], mine ? m.adds : [], { empty: "Nothing to show.", page: 8 });

    table($("mu-league"), [
      { key: "a", label: "Team", left: true, stick: true, sort: (x) => x.teams[0].name,
        fmt: (x) => `${esc(x.teams[0].name)} <span class="muted">${num(x.teams[0].final, 1)}</span>` },
      { key: "awin", label: "Win", sort: (x) => x.teams[0].win, fmt: (x) => pctTxt(x.teams[0].win) },
      { key: "b", label: "Opponent", left: true, sort: (x) => x.teams[1].name,
        fmt: (x) => `${esc(x.teams[1].name)} <span class="muted">${num(x.teams[1].final, 1)}</span>` },
      { key: "bwin", label: "Win", sort: (x) => x.teams[1].win, fmt: (x) => pctTxt(x.teams[1].win) },
    ], m.league, {
      rowClass: (x) => (x.teams.some((t) => t.id === m.my_team_id) ? "mine" : ""),
      empty: "No matchups this week.",
    });
  }

  $("mu-start").addEventListener("change", () => loaders.matchup());
})();
