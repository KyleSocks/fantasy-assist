"use strict";
/* Posts what the draft room sends (content.js) to the draft board:
   POST <server>/api/draft/live with the X-Draft-Token header. The board
   server and token are set on the options page. The last result is kept
   for the popup. */

const api = globalThis.browser || globalThis.chrome;

async function post(msg) {
  const { server, token } = await api.storage.local.get(["server", "token"]);
  if (!server || !token) return { ok: false, error: "Set the board address and token in the extension options" };
  const url = server.replace(/\/+$/, "") + "/api/draft/live";
  try {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Draft-Token": token },
      body: JSON.stringify({ url: msg.url, league_id: msg.league_id || null, html: msg.html,
        league_html: msg.league_html || null, settings_html: msg.settings_html || null,
        mock: !!msg.mock, my_team_id: msg.my_team_id || null }),
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) return { ok: false, error: `${r.status}: ${body.detail || r.statusText}` };
    return { ok: true, picks: body.picks, room: body.room, sent_html: msg.html != null, mock: !!msg.mock,
      settings: body.settings || null, settings_error: body.settings_error || null };
  } catch (e) {
    return { ok: false, error: `Can't reach ${server} (${e.message}). Is the server up, and did you allow the address in the options?` };
  }
}

api.runtime.onMessage.addListener((msg, sender, reply) => {
  if (!msg || msg.type !== "draft-room") return false;
  post(msg).then((res) => {
    const status = Object.assign({ at: new Date().toISOString(), room_url: msg.room_url, league_id: msg.league_id }, res);
    api.storage.local.set({ status });
    reply(status);
  });
  return true;   // reply asynchronously
});
