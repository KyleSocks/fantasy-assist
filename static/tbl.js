// Sortable data table shared by the mid-season page's scripts.
//
//   Tbl.table(el, cols, rows, opts)
//
// cols = [{key, label, fmt(row) -> html, sort(row) -> value, title,
//          left, stick, minor, wide}]
//   left   text column (left-aligned)
//   stick  stays put while the table scrolls sideways (default: first left)
//   minor  secondary detail: on narrow screens it sits behind "More"
//   wide   on narrow screens takes the card's full width (long text)
// opts = {empty: "message when there are no rows", rowClass(row) -> class,
//         onRow(row, tr), cards: false to keep a real table on phones,
//         page: rows shown before a "Show more" button (default 40; 0 = all)}
// Sorting always sorts every row; paging only limits how many are drawn.
//
// On screens 900px wide or less, and wherever the full table would need
// sideways scrolling to fit, every row becomes a card (class "fold"):
// each cell shows its column label, nothing is cut off and nothing needs
// sideways scrolling. The clickable headers are hidden there, so a "Sort
// by" picker is drawn above the table instead.
(function () {
  "use strict";
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  function sortRows(cols, rows, state) {
    const sorted = rows.slice();
    const col = state.key && cols.find((c) => c.key === state.key);
    if (!col) return sorted;
    const val = col.sort || ((r) => r[col.key]);
    sorted.sort((a, b) => {
      const x = val(a), y = val(b);
      if (x == null && y == null) return 0;
      if (x == null) return 1;
      if (y == null) return -1;
      return (x < y ? -1 : x > y ? 1 : 0) * state.dir;
    });
    return sorted;
  }

  function table(el, cols, rows, opts = {}, state, keepPage) {
    const page = opts.page ?? 40;
    if (!keepPage || !el._limit) el._limit = page || Infinity;
    state = state || el._sort || {};
    el._sort = state;
    el._args = [cols, rows, opts];
    const stick = (cols.find((c) => c.stick) || cols.find((c) => c.left) || {}).key;
    const cards = opts.cards !== false;
    const hasMinor = cards && cols.some((c) => c.minor);
    const cl = (c) => [c.left ? "l" : "", c.key === stick ? "stick" : "", c.minor ? "minor" : "",
      c.wide ? "wide" : ""].filter(Boolean).join(" ");
    el.classList.add("tbl");
    el.classList.toggle("cards", cards);
    const all = sortRows(cols, rows, state);
    const sorted = all.slice(0, el._limit);
    const caret = (c) => (state.key === c.key ? `<span class="caret ${state.dir > 0 ? "up" : "down"}"></span>` : "");
    const body = sorted.length
      ? sorted.map((r) => `<tr class="${esc(opts.rowClass ? opts.rowClass(r) || "" : "")}">` + cols.map((c) =>
          `<td class="${cl(c)}" data-label="${esc(c.label)}">${c.fmt ? c.fmt(r) : esc(r[c.key])}</td>`).join("")
          + (hasMinor ? '<td class="more-cell"><button type="button" class="more-btn" aria-expanded="false">More</button></td>' : "")
          + "</tr>").join("")
      : `<tr><td class="empty" colspan="${cols.length + 1}">${esc(opts.empty || "Nothing to show.")}</td></tr>`;
    el.innerHTML = `<thead><tr>${cols.map((c) =>
      `<th class="${cl(c)}" data-key="${esc(c.key)}" title="${esc(c.title || "")}" scope="col"`
      + ` aria-sort="${state.key === c.key ? (state.dir > 0 ? "ascending" : "descending") : "none"}">`
      + `${esc(c.label)}${caret(c)}</th>`).join("")}${hasMinor ? '<th class="more-cell"></th>' : ""}</tr></thead>`
      + `<tbody>${body}</tbody>`;
    el.querySelectorAll("th[data-key]").forEach((th) => th.addEventListener("click", () => {
      const k = th.dataset.key;
      table(el, cols, rows, opts, { key: k, dir: state.key === k ? -state.dir : -1 }, true);
    }));
    moreButton(el, cols, rows, opts, state, all.length - sorted.length, page);
    el.querySelectorAll(".more-btn").forEach((b) => b.addEventListener("click", (e) => {
      e.stopPropagation();
      const tr = b.closest("tr");
      const open = tr.classList.toggle("open");
      b.setAttribute("aria-expanded", String(open));
      b.textContent = open ? "Less" : "More";
    }));
    if (opts.onRow) el.querySelectorAll("tbody tr").forEach((tr, i) => sorted[i] && opts.onRow(sorted[i], tr));
    sortPicker(el, cols, rows, opts, state, cards);
    fit(el);
  }

  // cards when the screen is narrow or the full table doesn't fit
  const narrow = () => !!(window.matchMedia && window.matchMedia("(max-width: 900px)").matches);
  function fit(el) {
    if (!el.classList.contains("cards")) return;
    const host = el.closest(".scroll") || el.parentNode;
    let fold = narrow();
    if (!fold && host.clientWidth) {
      el.classList.remove("fold");
      fold = host.scrollWidth > host.clientWidth + 1;
    } else if (!fold) fold = el.classList.contains("fold");   // hidden: keep
    el.classList.toggle("fold", fold);
    const bar = host.previousElementSibling;
    if (bar && bar.classList.contains("tbl-sort")) bar.hidden = !fold || el.querySelectorAll("tbody tr").length < 2;
  }
  let timer = null;
  window.addEventListener("resize", () => {
    clearTimeout(timer);
    timer = setTimeout(() => document.querySelectorAll("table.tbl.cards").forEach(fit), 120);
  });

  // "Show more" under the table when rows were held back
  function moreButton(el, cols, rows, opts, state, left, page) {
    const host = el.closest(".scroll") || el;
    let btn = host.nextElementSibling;
    if (!btn || !btn.classList.contains("tbl-page")) {
      btn = document.createElement("button");
      btn.type = "button";
      btn.className = "tbl-page";
      host.parentNode.insertBefore(btn, host.nextSibling);
    }
    btn.hidden = left <= 0;
    btn.textContent = `Show ${Math.min(left, page)} more (${left} not shown)`;
    btn.onclick = () => { el._limit += page; table(el, cols, rows, opts, state, true); };
  }

  // "Sort by" select above the table, shown only where headers are hidden
  function sortPicker(el, cols, rows, opts, state, cards) {
    const host = el.closest(".scroll") || el;
    let bar = host.previousElementSibling;
    if (!bar || !bar.classList.contains("tbl-sort")) {
      bar = document.createElement("label");
      bar.className = "tbl-sort";
      host.parentNode.insertBefore(bar, host);
    }
    bar.hidden = true;           // fit() shows it when the table folds
    const cur = state.key ? `${state.key}:${state.dir}` : "";
    const word = (c) => (c.label.trim().length > 1 ? c.label : c.title || c.label);
    bar.innerHTML = `Sort by <select>${cur ? "" : '<option value="">as listed</option>'}${cols.flatMap((c) => [
      `<option value="${esc(c.key)}:${c.left ? 1 : -1}">${esc(word(c))}, ${c.left ? "A to Z" : "high to low"}</option>`,
      `<option value="${esc(c.key)}:${c.left ? -1 : 1}">${esc(word(c))}, ${c.left ? "Z to A" : "low to high"}</option>`]).join("")}</select>`;
    const sel = bar.querySelector("select");
    sel.value = cur;
    sel.addEventListener("change", () => {
      const [key, dir] = sel.value.split(":");
      table(el, cols, rows, opts, key ? { key, dir: Number(dir) } : {}, true);
    });
  }

  window.Tbl = { table, esc, fit };
})();
