"use strict";
/* Draft-board modes: a Timeframe picker (full season, rest of season,
   next 7 / 30 days, fantasy playoffs) and Keeper value (0-3 future
   seasons, per-season discount). The mode lives in the page URL (?tf=,
   ?keeper=, ?kdisc=); board.js forwards it to /api/board and
   /api/recommend. Changing it reloads the page, and the draft picks stay
   (they are stored per league, not per mode).

   Keeper settings are remembered per league in localStorage, because
   keeper rules differ between leagues. */

(function () {
  const KEEPER_LS = "nhl-draft-board:keeper:";
  const q = () => new URLSearchParams(window.location.search);
  const el = (tag, attrs, html) => { const e = document.createElement(tag); Object.assign(e, attrs || {}); if (html != null) e.innerHTML = html; return e; };
  const escHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function go(changes) {
    const p = q();
    for (const [k, v] of Object.entries(changes)) { if (v == null || v === "" || v === "season" || v === "0") p.delete(k); else p.set(k, v); }
    window.location.search = p.toString() ? "?" + p.toString() : "";
  }

  function fmtDate(iso) {
    const d = new Date(iso + "T12:00:00");
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }

  function savedKeeper(league) {
    try { return JSON.parse(localStorage.getItem(KEEPER_LS + league)) || null; } catch { return null; }
  }
  function saveKeeper(league, years, disc) {
    try { localStorage.setItem(KEEPER_LS + league, JSON.stringify({ years, disc })); } catch { /* session only */ }
  }

  function build() {
    const meta = state.meta, mode = meta.mode;
    if (!mode) return;
    const controls = document.querySelector(".controls");
    const wrap = el("div", { className: "mode-controls" });

    const tf = el("select", { id: "timeframe", title: "What stretch of the season to value" });
    tf.setAttribute("aria-label", "Timeframe");
    tf.innerHTML = Object.entries(mode.timeframes).map(([k, label]) =>
      `<option value="${k}"${k === mode.timeframe ? " selected" : ""}>${escHtml(label)}</option>`).join("");
    tf.addEventListener("change", () => go({ tf: tf.value }));

    const kp = el("select", { id: "keeper", title: "Keeper league: add the value of the seasons you could keep him" });
    kp.setAttribute("aria-label", "Keeper value");
    kp.innerHTML = [["0", "Keeper: off"], ["1", "Keeper: +1 season"], ["2", "Keeper: +2 seasons"], ["3", "Keeper: +3 seasons"]]
      .map(([v, l]) => `<option value="${v}"${Number(v) === mode.keeper.years ? " selected" : ""}>${l}</option>`).join("");

    const kd = el("select", { id: "kdisc", title: "How much each later season counts (1.00 = as much as this one)" });
    kd.setAttribute("aria-label", "Keeper discount");
    const discs = ["0.5", "0.6", "0.75", "0.9", "1"];
    if (!discs.includes(String(mode.keeper.discount))) discs.push(String(mode.keeper.discount));
    kd.innerHTML = discs.map((v) => `<option value="${v}"${Number(v) === mode.keeper.discount ? " selected" : ""}>× ${Number(v).toFixed(2)}/yr</option>`).join("");
    kd.hidden = mode.keeper.years === 0;

    const keeperGo = () => { saveKeeper(meta.league, kp.value, kd.value); go({ keeper: kp.value, kdisc: kp.value === "0" ? null : kd.value }); };
    kp.addEventListener("change", keeperGo);
    kd.addEventListener("change", keeperGo);

    wrap.append(tf, kp, kd);
    const reset = document.getElementById("reset");
    controls.insertBefore(wrap, reset);

    // a league whose keeper settings were saved opens in keeper mode
    const saved = savedKeeper(meta.league);
    if (!q().has("keeper") && saved && saved.years && saved.years !== "0" && !sessionStorage.getItem("keeper-applied:" + meta.league)) {
      sessionStorage.setItem("keeper-applied:" + meta.league, "1");
      go({ keeper: saved.years, kdisc: saved.disc });
      return;
    }

    const bits = [];
    if (mode.window) bits.push(`${escHtml(mode.timeframe_label)}: ${fmtDate(mode.window.start)} – ${fmtDate(mode.window.end)}`);
    if (mode.keeper.years) bits.push(`Keeper value: this season + ${mode.keeper.years} more × ${mode.keeper.discount.toFixed(2)}/yr`);
    if (bits.length) {
      const round = document.getElementById("round");
      round.insertAdjacentElement("afterend", el("div", { className: "mode-note muted" }, bits.join(" · ")));
    }
  }

  document.addEventListener("board:ready", build);
})();
