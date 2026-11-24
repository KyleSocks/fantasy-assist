"use strict";
const api = globalThis.browser || globalThis.chrome;
const $ = (id) => document.getElementById(id);

api.storage.local.get(["server", "token", "leagueId"]).then((o) => {
  $("server").value = o.server || "";
  $("token").value = o.token || "";
  $("leagueId").value = o.leagueId || "";
});

$("save").addEventListener("click", async () => {
  const server = $("server").value.trim().replace(/\/+$/, "");
  let origin;
  try { origin = new URL(server).origin; } catch { $("msg").textContent = "That address isn't a URL (e.g. http://marshall-home:8420)."; return; }
  // the board's address is a host the extension must be allowed to reach
  const granted = await api.permissions.request({ origins: [origin + "/*"] }).catch(() => false);
  if (!granted) { $("msg").textContent = "Allow access to " + origin + " so the extension can send picks there."; return; }
  await api.storage.local.set({ server, token: $("token").value.trim(), leagueId: $("leagueId").value.trim() });
  try {
    const r = await fetch(server + "/api/draft/token");
    $("msg").textContent = r.ok ? "Saved. The board is reachable." : "Saved, but the board answered " + r.status + ".";
  } catch (e) { $("msg").textContent = "Saved, but the board isn't reachable: " + e.message; }
});
