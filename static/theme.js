// Light ("ice") / dark ("lights out") theme. Runs in <head> so the page never
// flashes the wrong colours: a saved choice wins, else the system setting.
// The header's theme button (any .theme-btn) flips and saves it.
(function () {
  "use strict";
  const KEY = "fa-theme";
  const root = document.documentElement;
  const media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  const saved = () => { try { return localStorage.getItem(KEY); } catch { return null; } };
  const apply = (t) => {
    root.dataset.theme = t;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = t === "dark" ? "#111c2a" : "#ffffff";
    document.querySelectorAll(".theme-btn").forEach((b) => {
      b.setAttribute("aria-pressed", String(t === "dark"));
      b.title = t === "dark" ? "Switch to light" : "Switch to dark";
    });
    document.dispatchEvent(new CustomEvent("themechange", { detail: t }));
  };
  apply(saved() || (media && media.matches ? "dark" : "light"));
  if (media && media.addEventListener) media.addEventListener("change", (e) => { if (!saved()) apply(e.matches ? "dark" : "light"); });
  document.addEventListener("DOMContentLoaded", () => {
    apply(root.dataset.theme);
    document.querySelectorAll(".theme-btn").forEach((b) => b.addEventListener("click", () => {
      const t = root.dataset.theme === "dark" ? "light" : "dark";
      try { localStorage.setItem(KEY, t); } catch { /* session only */ }
      apply(t);
    }));
  });
})();
