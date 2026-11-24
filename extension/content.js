"use strict";
/* Runs on Yahoo Fantasy hockey pages. In a draft room -- your league's, or
   a mock draft (Yahoo runs each mock room as a temporary league) -- it
   reads the league's pages with your own Yahoo session (so private leagues
   work) and hands them to the background script, which posts them to your
   draft board. The board's server does all the parsing (src/draft/live.py,
   src/draft/yahoo_settings.py).

     every 5 s    /hockey/<id>/draftresults   picks so far (and right
                  after each pick in Yahoo's draft client)
     every 60 s   /hockey/<id>                team names, your team
     every 10 min /hockey/<id>/settings       scoring, roster, playoffs

   Pages that count as a draft room: the mock waiting room
   (/hockey/mock_waiting?mlid=<id>), /hockey/<id>/enterdraft, and Yahoo's
   draft client (/draftclient/hockey/<id>/<your slot>). The league id comes from the address, or the extension options. */

(function () {
  const api = globalThis.browser || globalThis.chrome;
  const EVERY_MS = 5000, LEAGUE_MS = 60000, SETTINGS_MS = 600000;
  const href = location.href;
  const path = location.pathname + location.search;
  const isHockey = /hockey/i.test(location.hostname + location.pathname);
  const isRoom = isHockey && /draft|mock_waiting/i.test(path) && !/draftresults|draftanalysis|mock_lobby|livedraft_selection/i.test(path);
  if (!isRoom) return;

  function leagueIdFrom(url) {
    const u = new URL(url);
    const m = url.match(/[?&](?:mlid|lid|league_id|leagueId)=(\d+)/i)
      || u.pathname.match(/\/hockey\/(\d+)(?:[/?#]|$)/)
      || u.pathname.match(/\/(\d{3,9})(?:\/|$)/);   // e.g. /draftclient/<id>
    return m ? Number(m[1]) : null;
  }
  // your draft slot / team: ?slot=5, /hockey/<id>/5/enterdraft
  function slotFrom(url) {
    const p = new URL(url).pathname;
    const m = url.match(/[?&]slot=(\d+)/) || p.match(/\/hockey\/\d+\/(\d+)\/enterdraft/)
      || p.match(/\/draftclient\/hockey\/\d+\/(\d+)/);   // the draft client itself
    return m ? Number(m[1]) : null;
  }

  const mockHere = /mock/i.test(path);
  let busy = false, lastLeague = 0, lastSettings = 0;

  async function get(url) {
    try {
      const r = await fetch(url, { credentials: "include", cache: "no-store" });
      return r.ok ? await r.text() : null;
    } catch { return null; }
  }

  async function tick() {
    if (busy) return;
    busy = true;
    try {
      const opts = await api.storage.local.get(["leagueId", "mocks"]);
      const lid = Number(opts.leagueId) || leagueIdFrom(href);
      const mocks = opts.mocks || {};
      // remember mock rooms (and your slot) from the waiting room, so the
      // draft client -- whose address doesn't say "mock" -- is known too
      if (lid && mockHere) {
        mocks[lid] = slotFrom(href) || mocks[lid] || null;
        const keep = Object.keys(mocks).slice(-20);
        await api.storage.local.set({ mocks: Object.fromEntries(keep.map((k) => [k, mocks[k]])) });
      }
      const mock = !!(lid && lid in mocks);
      const msg = { type: "draft-room", url: href, room_url: href, league_id: lid, mock, my_team_id: (mock ? mocks[lid] : null) || slotFrom(href), html: null };
      if (lid) {
        const base = `${location.origin}/hockey/${lid}`;
        msg.url = base + "/draftresults";
        msg.html = await get(msg.url);
        const now = Date.now();
        if (!mock && now - lastLeague > LEAGUE_MS) { msg.league_html = await get(base); lastLeague = now; }
        if (now - lastSettings > SETTINGS_MS) {
          msg.settings_html = await get(base + "/settings");
          if (msg.settings_html) lastSettings = now;
        }
      }
      await api.runtime.sendMessage(msg);
    } catch { /* extension reloaded or background asleep: next tick */ }
    busy = false;
  }

  tick();
  setInterval(tick, EVERY_MS);

  // In Yahoo's draft client, react to a pick right away instead of waiting
  // for the next 5 s poll: the tab title ("7 picks until your turn") and the
  // "Last: <player>" line change with every pick.
  if (/draftclient/i.test(location.pathname)) {
    let sig = null;
    setInterval(() => {
      const last = (document.body && document.body.innerText.match(/Last:[^\n]*/)) || [""];
      const now = document.title + "|" + last[0];
      if (sig !== null && now !== sig) setTimeout(tick, 800);   // let Yahoo's page catch up
      sig = now;
    }, 1000);
  }
})();
