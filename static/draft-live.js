"use strict";
/* Live draft sync on the board. Polls GET /api/draft/live every few
   seconds; when the browser extension (extension/) is posting from a Yahoo
   draft room -- or after "Pull from Yahoo" for a public league -- every
   pick Yahoo shows is applied to the board: taken players leave it, yours
   go to your roster, and recommendations refresh.

   Sync only ever adds picks (and fixes the Me/Taken side of a synced
   pick); picks you logged by hand stay. Reset draft (or "Stop syncing")
   pauses the room as it stands: its picks aren't re-applied until it gets
   a new pick (a live draft carries on) or you press "Sync again". The pause
   is kept per league in localStorage. Your Yahoo team comes from the
   league config; for another league's room pick it in the panel (kept
   per league in localStorage). Uses board.js globals: state, saveState,
   render, refetchRecs, goalLight, esc. */

(function () {
  const POLL_LIVE_MS = 2000, POLL_IDLE_MS = 8000;   // room open / not
  const ME_LS = "nhl-draft-board:me:";
  const PAUSE_LS = "nhl-draft-board:sync-paused:";
  let last = null, timer = null;

  const meKey = () => ME_LS + state.meta.league;
  const pauseKey = () => PAUSE_LS + state.meta.league;
  function paused(v) {
    try {
      const p = JSON.parse(localStorage.getItem(pauseKey()) || "null");
      return !!p && v && p.room === v.room && p.updated_at === v.updated_at;
    } catch { return false; }
  }
  function pause(v) {
    if (!v || v.room == null) return;
    try { localStorage.setItem(pauseKey(), JSON.stringify({ room: v.room, updated_at: v.updated_at })); } catch { /* session only */ }
  }
  function unpause() { try { localStorage.removeItem(pauseKey()); } catch { /* ignore */ } }

  function savedMe() { try { const v = localStorage.getItem(meKey()); return v == null ? null : Number(v); } catch { return null; } }

  function panel() {
    let box = document.getElementById("live-sync");
    if (!box) {
      box = document.createElement("div");
      box.id = "live-sync";
      box.className = "live-sync";
      const round = document.getElementById("round");
      round.parentNode.insertBefore(box, round);
      box.addEventListener("change", (e) => {
        if (e.target.id !== "live-me") return;
        try { localStorage.setItem(meKey(), e.target.value); } catch { /* session only */ }
        poll();
      });
      box.addEventListener("click", (e) => {
        const b = e.target.closest("button");
        if (!b) return;
        if (b.dataset.act === "pull") pull(b);
        if (b.dataset.act === "token") showToken(b);
        if (b.dataset.act === "stop") { pause(last); poll(); }
        if (b.dataset.act === "resume") { unpause(); poll(); }
      });
    }
    return box;
  }

  function apply(log) {
    let changed = false, newest = null;
    for (const e of log) {
      if (!state.byId.has(e.player_id)) continue;
      const i = state.drafted_all.indexOf(e.player_id);
      if (i < 0) {
        state.drafted_all.push(e.player_id);
        if (e.dest === "me") { state.drafted_by_me.push(e.player_id); newest = e.player_id; }
        state.log.push({ player_id: e.player_id, dest: e.dest });
        changed = true;
      } else {
        const entry = state.log.find((x) => x.player_id === e.player_id);
        if (entry && entry.dest !== e.dest) {
          entry.dest = e.dest;
          state.drafted_by_me = state.log.filter((x) => x.dest === "me").map((x) => x.player_id);
          changed = true;
        }
      }
    }
    if (!changed) return;
    saveState();
    render();
    refetchRecs();
    if (newest != null) goalLight(state.byId.get(newest).name);
  }

  function draw(v) {
    const box = panel();
    const fresh = v.room != null;
    const dot = v.connected ? "on" : fresh ? "idle" : "off";
    let head;
    if (v.connected && v.source === "extension") head = `Yahoo draft room open · ${v.count} pick${v.count === 1 ? "" : "s"} synced`;
    else if (fresh) head = `Synced ${v.count} pick${v.count === 1 ? "" : "s"} from Yahoo`;
    else head = "Draft sync: no Yahoo draft room";
    const other = fresh && v.yahoo_league_id && String(v.room) !== String(v.yahoo_league_id);
    const parts = [`<div class="live-head"><span class="live-dot ${dot}"></span>${esc(head)}${v.mock ? ' <span class="live-tag">Mock</span>' : ""}</div>`];
    if (v.settings) parts.push(settingsHtml(v.settings));
    else if (v.settings_error) parts.push(`<div class="live-miss">Couldn't read this league's settings: ${esc(v.settings_error)}</div>`);
    if (other && !v.settings) parts.push(`<div class="muted">Following ${v.room === "room" ? "a draft room with no league id" : "Yahoo league " + esc(v.room)}, not this board's league.</div>`);
    const teams = Object.entries(v.teams || {});
    if (fresh && teams.length && (other || v.my_team_id == null || savedMe() != null)) {
      const me = v.my_team_id;
      parts.push(`<label class="live-me">You are <select id="live-me"><option value="0">— not in this draft —</option>${teams.map(([id, name]) => `<option value="${id}"${Number(id) === me ? " selected" : ""}>${esc(name)}</option>`).join("")}</select></label>`);
    }
    const isPaused = paused(v);
    if (fresh && isPaused) parts.push(`<div class="muted">Not syncing this room: you reset the draft or stopped syncing. It picks up again on the room's next pick.</div>`);
    if (v.unmatched && v.unmatched.length && !isPaused) parts.push(`<div class="live-miss">Not on the board (log by name if needed): ${v.unmatched.map(esc).join(", ")}</div>`);
    const btns = [];
    if (fresh) btns.push(isPaused
      ? `<button type="button" class="quiet" data-act="resume" title="Apply this room's picks to the board again">Sync again</button>`
      : `<button type="button" class="quiet" data-act="stop" title="Leave this room's picks off the board (until its next pick)">Stop syncing</button>`);
    if (v.yahoo_league_id) btns.push(`<button type="button" class="quiet" data-act="pull" title="Read this league's public draft results from the server (no extension)">Pull from Yahoo</button>`);
    btns.push(`<button type="button" class="quiet" data-act="token" title="For the Draft Sync browser extension">Extension token</button>`);
    parts.push(`<div class="live-btns">${btns.join(" ")}</div><div id="live-token" class="muted"></div>`);
    const html = parts.join("");
    if (box.dataset.html !== html) { box.innerHTML = html; box.dataset.html = html; }
  }

  /* The room's Yahoo settings: which board league holds them, and whether
     your own config for that league still matches Yahoo. */
  function settingsHtml(st) {
    const kind = st.scoring_type === "points" ? "points" : st.scoring_type === "roto" ? "roto" : "categories";
    const out = [`<div class="muted">${esc(st.name || "Yahoo league")}: ${st.team_count} teams, ${kind}</div>`];
    const here = st.board_league === state.meta.league;
    if (here && !st.differences.length) out.push(`<div class="live-ok">✓ This board uses ${st.imported ? "these Yahoo settings" : "a config matching Yahoo's settings"}</div>`);
    if (st.differences.length) out.push(`<div class="live-miss">Your <code>${esc(st.board_league)}</code> config differs from Yahoo: ${st.differences.map(esc).join("; ")}</div>`);
    if (!here && st.board_league) {
      const q = new URLSearchParams(window.location.search);
      q.set("league", st.board_league);
      out.push(`<a class="live-switch" href="?${esc(q.toString())}">Use ${st.imported ? "this room's settings" : "the " + esc(st.board_league) + " board"} →</a>`);
    }
    if (st.notes.length) out.push(`<div class="muted">${st.notes.map(esc).join("; ")}</div>`);
    return out.join("");
  }

  async function poll() {
    clearTimeout(timer);
    try {
      const p = new URLSearchParams({ league: state.meta.league });
      const me = savedMe();
      if (me != null) p.set("me", String(me));
      const r = await fetch("/api/draft/live?" + p.toString());
      if (r.ok) {
        last = await r.json();
        draw(last);
        if (!paused(last)) apply(last.log || []);
      }
    } catch { /* server restarting: try again */ }
    timer = setTimeout(poll, last && last.connected ? POLL_LIVE_MS : POLL_IDLE_MS);
  }

  async function pull(btn) {
    btn.disabled = true; btn.textContent = "Pulling…";
    try {
      const r = await fetch("/api/draft/live/pull?league=" + encodeURIComponent(state.meta.league), { method: "POST" });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.detail || r.statusText);
      unpause();
      await poll();
    } catch (e) { const t = document.getElementById("live-token"); if (t) t.textContent = "Pull failed: " + e.message; }
    btn.disabled = false; btn.textContent = "Pull from Yahoo";
  }

  async function showToken() {
    const t = document.getElementById("live-token");
    try {
      const r = await fetch("/api/draft/token");
      const { token } = await r.json();
      t.innerHTML = `Paste into the extension's options, with board address <code>${esc(location.origin)}</code>: <code class="live-tok">${esc(token)}</code>`;
    } catch (e) { t.textContent = "Couldn't read the token: " + e.message; }
  }

  document.addEventListener("board:ready", poll);
  document.addEventListener("board:reset", () => { pause(last); if (last) draw(last); });
})();
