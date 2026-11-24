"use strict";
const api = globalThis.browser || globalThis.chrome;
const el = document.getElementById("status");
document.getElementById("opts").addEventListener("click", (e) => { e.preventDefault(); api.runtime.openOptionsPage(); });

api.storage.local.get(["status", "server", "token"]).then(({ status, server, token }) => {
  if (!server || !token) { el.className = "bad"; el.textContent = "Not set up: add the board address and token in Options."; return; }
  if (!status) return;
  const ago = Math.round((Date.now() - Date.parse(status.at)) / 1000);
  const when = ago < 90 ? `${ago}s ago` : `${Math.round(ago / 60)} min ago`;
  if (!status.ok) { el.className = "bad"; el.textContent = `Last try ${when}: ${status.error}`; return; }
  el.className = ago < 30 ? "ok" : "muted";
  const where = status.mock ? `mock room ${status.league_id}` : `league ${status.league_id}`;
  el.textContent = status.sent_html
    ? `Synced ${when}: ${status.picks} pick${status.picks === 1 ? "" : "s"} (${where}).`
      + (status.settings_error ? ` Couldn't read its settings: ${status.settings_error}` : "")
    : `Draft room seen ${when}, but no league id: set it in Options.`;
});
