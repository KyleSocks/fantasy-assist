"use strict";
/* Player drill-down panel (right side, non-modal).
   Opens on board-row click; fetches GET /api/player/{id} and renders a
   uPlot trend chart with confidence band, the deterministic breakdown
   table, and schedule strength. No JS computation: every number shown
   comes straight from the API payload.

   Row clicks only open the panel. Draft controls (the per-row Me /
   Taken buttons, handled in board.js) never open it: clicks originating
   from interactive elements are ignored here, and board.js stops
   propagation on them. */

const DETAIL_CHART_H = 220;
let detail = null;       // current /api/player/{id} payload
let activeCat = 0;       // index into detail.categories
let uplot = null;        // uPlot instance
let resObserver = null;  // ResizeObserver for the chart host
let openToken = 0;       // guards stale fetch responses

function detailLeague() {
  const el = $("league");
  return el && el.value ? el.value : "kyle_12_points";
}

/* Format a breakdown value: numbers with up to 4 decimals,
   strings (e.g. "0.50 / 0.30 / 0.20") passed through escaped. */
function fmtNum(v) {
  if (typeof v !== "number") return esc(String(v));
  return String(Math.round(v * 10000) / 10000);
}

/* uPlot positional data: [labels, line, band-hi, band-lo].
   Band edge series overlap the line on the actual points and fan out
   only at the "proj" point, so the band reads as projection uncertainty. */
function chartData() {
  const cat = detail.categories[activeCat];
  return [
    detail.labels.map((_, i) => i),   // x = index; axis shows detail.labels
    cat.actual.concat([cat.proj]),
    cat.actual.concat([cat.hi]),
    cat.actual.concat([cat.lo]),
  ];
}

function destroyChart() {
  if (uplot) { uplot.destroy(); uplot = null; }
  if (resObserver) { resObserver.disconnect(); resObserver = null; }
}

function updateBandNote() {
  const cat = detail.categories[activeCat];
  let note = detail.band_note || "";
  if (cat.hi === cat.lo) {
    note = (note ? note + " " : "") + "Band equals the projection for this category.";
  }
  $("band-note").textContent = note;
}

function mountChart() {
  const host = $("uplot-host");
  const ACCENT = "#2f7cf6";                  // --accent (rink blue)
  const BAND = "rgba(47,124,246,0.22)";
  const BAND_EDGE = "rgba(47,124,246,0.45)";
  const AXIS = { stroke: "#8a9bb0", grid: { stroke: "#22344a", width: 1 },
                 ticks: { stroke: "#22344a", width: 1 } };   // --muted / --line
  const label = (i) => (Number.isInteger(i) ? detail.labels[i] ?? "" : "");
  uplot = new uPlot({
    width: host.clientWidth || 340,
    height: DETAIL_CHART_H,
    // x is the season index (even spacing); not a time scale, or uPlot
    // formats the labels as dates ("NaN")
    scales: { x: { time: false, range: (u, lo, hi) => [lo - 0.3, hi + 0.3] } },
    series: [
      {},                                                        // x
      { stroke: ACCENT, width: 2, points: { show: true, size: 6 } },  // actual -> projection
      { stroke: BAND_EDGE, width: 1, points: { show: false } },      // band high
      { stroke: BAND_EDGE, width: 1, points: { show: false } },      // band low
    ],
    bands: [{ series: [2, 3], fill: BAND }],
    legend: { show: false },
    cursor: { drag: false },
    axes: [
      { ...AXIS, splits: () => detail.labels.map((_, i) => i), values: (u, v) => v.map(label) },
      { ...AXIS, size: 44 },
    ],
  }, chartData(), host);

  // autoSize is not in this build: track the host width manually.
  resObserver = new ResizeObserver((entries) => {
    const w = Math.floor(entries[0].contentRect.width);
    // uPlot takes an object here; setSize(w, h) left width undefined and
    // blanked the chart the moment the observer first fired.
    if (w > 0 && uplot) uplot.setSize({ width: w, height: DETAIL_CHART_H });
  });
  resObserver.observe(host);
}

function wireTabs() {
  $("panel-body").querySelectorAll(".dtab").forEach((btn) => {
    btn.addEventListener("click", () => {
      const i = Number(btn.dataset.i);
      if (i === activeCat) return;
      activeCat = i;
      $("panel-body").querySelectorAll(".dtab").forEach((b) => {
        b.classList.toggle("active", b.dataset.i === String(i));
      });
      uplot.setData(chartData());
      updateBandNote();
    });
  });
}

function renderPanel() {
  const tabs = detail.categories
    .map((c, i) => `<button type="button" class="dtab${i === activeCat ? " active" : ""}" data-i="${i}">${esc(c.label)}</button>`)
    .join("");

  const breakdown = detail.breakdown
    .map((r) => `<tr><td>${esc(r.term)}</td><td>${fmtNum(r.value)}</td></tr>`)
    .join("");

  let scheduleHtml;
  if (detail.schedule) {
    const s = detail.schedule;
    const rows = s.monthly
      .map((m) => `<div class="sched-row"><span>${esc(m.month)}</span><span>${m.games} games</span></div>`)
      .join("");
    scheduleHtml = `
      <section class="sched">
        <h3>Schedule — ${esc(s.team)} (${s.total} games)</h3>
        ${rows}
        <div class="sched-note">Heaviest ${esc(s.heaviest_month)} (${s.heaviest_games}) · Lightest ${esc(s.lightest_month)} (${s.lightest_games})</div>
      </section>`;
  } else {
    scheduleHtml = `<section class="sched"><h3>Schedule — ${esc(detail.team)}</h3><div class="sched-note">No schedule data for the upcoming season.</div></section>`;
  }

  $("panel-body").innerHTML = `
    <div class="dtabs">${tabs}</div>
    <div class="uplot-host" id="uplot-host"></div>
    <div id="band-note" class="band-note"></div>
    <table class="breakdown"><tbody>${breakdown}</tbody></table>
    ${scheduleHtml}
  `;
  wireTabs();
  updateBandNote();
  mountChart();
}

function openPanel(id) {
  destroyChart();
  const p = state.byId.get(id);
  if (p) {
    $("panel-name").textContent = p.name;
    const bits = [p.pos, p.team];
    const rank = rankOf.get(id);
    if (rank != null) bits.push(`#${rank}`, `${p.vorp} VORP`, `$${p.dollars}`);
    $("panel-meta").textContent = bits.join(" · ");
  }
  $("player-panel").hidden = false;
  $("panel-body").innerHTML = `<div class="panel-loading">Loading…</div>`;

  const token = ++openToken;
  const url = `/api/player/${id}?league=${encodeURIComponent(detailLeague())}`;
  fetch(url)
    .then((res) => {
      if (!res.ok) {
        return res.json().catch(() => ({})).then((d) => {
          throw new Error(d.detail || res.statusText);
        });
      }
      return res.json();
    })
    .then((payload) => {
      if (token !== openToken) return;  // a newer row click superseded us
      detail = payload;
      activeCat = 0;
      if (Object.keys(payload).length === 0) {
        $("panel-body").innerHTML = `<div class="panel-empty">No projection data for this player.</div>`;
        return;
      }
      renderPanel();
    })
    .catch((err) => {
      if (token !== openToken) return;
      detail = null;
      $("panel-body").innerHTML = `<div class="panel-empty">Failed to load: ${esc(err.message)}</div>`;
    });
}

function closePanel() {
  openToken++;
  detail = null;
  destroyChart();
  $("panel-body").innerHTML = "";
  $("player-panel").hidden = true;
}

function initDetail() {
  /* Clicking a row opens the panel and does nothing else; clicking a
     draft button (Me / Taken) drafts without opening the panel. */
  $("board-body").addEventListener("click", (e) => {
    if (e.target.closest("button, input")) return;
    const tr = e.target.closest("tr[data-id]");
    if (tr) openPanel(Number(tr.dataset.id));
  });
  $("panel-close").addEventListener("click", closePanel);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("player-panel").hidden) closePanel();
  });
}

initDetail();
