// Settings gear: edit the in-season settings (GET/PUT /api/inseason/settings,
// src/inseason/settings.py). Only changed fields are sent; a field set back
// to its default is dropped from the saved file.
(function () {
  "use strict";
  const { $, esc, api, status } = window.insKit;
  let groups = [];
  const dirty = new Map();      // key -> new value (null = back to default)

  const fmt = (v) => (typeof v === "boolean" ? (v ? "on" : "off") : v === "" ? "empty" : String(v));

  function input(it) {
    const id = `set-${it.key.replace(/\W/g, "-")}`;
    const val = dirty.has(it.key) ? (dirty.get(it.key) ?? it.default) : it.value;
    let ctl;
    if (it.type === "bool") {
      ctl = `<input type="checkbox" id="${id}" data-key="${it.key}" ${val ? "checked" : ""}>`;
    } else if (it.type === "choice") {
      ctl = `<select id="${id}" data-key="${it.key}">${it.choices.map((c) =>
        `<option ${c === val ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>`;
    } else if (it.type === "str") {
      ctl = `<input type="text" id="${id}" data-key="${it.key}" value="${esc(val ?? "")}" spellcheck="false" autocomplete="off">`;
    } else {
      const lim = [it.min != null ? `min="${it.min}"` : "", it.max != null ? `max="${it.max}"` : "",
        `step="${it.step ?? (it.type === "int" ? 1 : "any")}"`].join(" ");
      ctl = `<input type="number" id="${id}" data-key="${it.key}" value="${esc(val)}" ${lim} inputmode="decimal">`;
    }
    const changed = dirty.has(it.key) ? dirty.get(it.key) !== null && dirty.get(it.key) !== it.default : it.changed;
    return `<div class="set-row${changed ? " changed" : ""}${it.type === "bool" ? " bool" : ""}">
      <label for="${id}">${esc(it.label)}</label>
      <div class="set-ctl">${ctl}${changed ? `<button type="button" class="set-undo quiet" data-key="${it.key}" title="Back to ${esc(fmt(it.default))}">Default (${esc(fmt(it.default))})</button>` : ""}</div>
      ${it.help ? `<p class="muted">${esc(it.help)}</p>` : ""}</div>`;
  }

  function draw() {
    const open = new Set([...document.querySelectorAll("#set-body details[open]")].map((d) => d.dataset.i));
    $("set-body").innerHTML = groups.map((g, i) => {
      const n = g.items.filter((it) => (dirty.has(it.key) ? dirty.get(it.key) !== null : it.changed)).length;
      return `<details data-i="${i}" ${open.has(String(i)) || (!open.size && i === 0) ? "open" : ""}>
        <summary>${esc(g.title)}${n ? ` <span class="flag">${n} changed</span>` : ""}</summary>
        ${g.note ? `<p class="muted">${esc(g.note)}</p>` : ""}
        <div class="set-grid">${g.items.map(input).join("")}</div></details>`;
    }).join("");
    $("set-save").disabled = !dirty.size;
    $("set-save").textContent = dirty.size ? `Save ${dirty.size} change${dirty.size > 1 ? "s" : ""}` : "Save";
  }

  const item = (key) => groups.flatMap((g) => g.items).find((it) => it.key === key);

  async function open() {
    dirty.clear();
    $("set-status").textContent = "";
    $("set-body").innerHTML = '<p class="muted">Loading…</p>';
    $("set-dlg").showModal();
    try {
      const r = await api("/api/inseason/settings");
      groups = r.groups;
      $("set-file").textContent = r.file;
      draw();
    } catch (e) {
      $("set-body").innerHTML = `<p class="status err">${esc(e.message)}</p>`;
    }
  }

  function read(el) {
    const it = item(el.dataset.key);
    let v;
    if (it.type === "bool") v = el.checked;
    else if (it.type === "number" || it.type === "int" || it.type === "float") v = el.value === "" ? null : Number(el.value);
    else v = el.value;
    if (it.type === "str") v = v.trim();
    if (v === null || v === it.default) {           // back to default
      if (it.changed) dirty.set(it.key, null); else dirty.delete(it.key);
    } else if (v === it.value) dirty.delete(it.key); // what's saved already
    else dirty.set(it.key, v);
  }

  $("set-body").addEventListener("change", (e) => {
    if (!e.target.dataset.key) return;
    read(e.target);
    draw();
  });
  $("set-body").addEventListener("click", (e) => {
    const b = e.target.closest(".set-undo");
    if (!b) return;
    const it = item(b.dataset.key);
    if (it.changed) dirty.set(it.key, null); else dirty.delete(it.key);
    draw();
  });

  async function save(body, done) {
    $("set-status").textContent = "Saving…";
    $("set-save").disabled = true;
    try {
      const r = await api("/api/inseason/settings", {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      groups = r.groups;
      dirty.clear();
      draw();
      $("set-status").textContent = done;
      window.dispatchEvent(new Event("ins-settings-changed"));
    } catch (e) {
      $("set-status").textContent = e.message;
      $("set-save").disabled = !dirty.size;
    }
  }

  $("set-form").addEventListener("submit", (e) => {
    e.preventDefault();
    if (!dirty.size) return;
    const bad = [...document.querySelectorAll("#set-body input[type=number]")].find((x) => !x.checkValidity());
    if (bad) { bad.reportValidity(); return; }
    save({ values: Object.fromEntries(dirty) }, "Saved. Numbers on every tab now use the new settings.");
  });
  // two clicks: the first asks
  let armed = null;
  $("set-reset").addEventListener("click", () => {
    if (!groups.some((g) => g.items.some((it) => it.changed)) && !dirty.size) {
      $("set-status").textContent = "Everything is already at its default.";
      return;
    }
    if (!armed) {
      $("set-reset").textContent = "Click again to reset all";
      armed = setTimeout(() => { armed = null; $("set-reset").textContent = "Reset all"; }, 4000);
      return;
    }
    clearTimeout(armed); armed = null; $("set-reset").textContent = "Reset all";
    save({ reset_all: true }, "Everything is back to config/inseason.yaml.");
  });
  $("settings-btn").addEventListener("click", open);
  $("set-close").addEventListener("click", () => $("set-dlg").close());
  $("set-dlg").addEventListener("click", (e) => { if (e.target === $("set-dlg")) $("set-dlg").close(); });
  $("set-dlg").addEventListener("close", () => {
    if (!$("set-status").textContent.startsWith("Saved") && !$("set-status").textContent.startsWith("Everything is back")) return;
    status("");
    window.insKit.reload?.();
  });
})();
