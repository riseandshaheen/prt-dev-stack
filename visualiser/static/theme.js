// Light or dark, stored per browser. With no stored choice the OS setting picks the
// starting theme. Loaded in <head> so the page never paints in the wrong theme.
(function () {
  var KEY = "vis-theme";
  var root = document.documentElement;
  function initial() {
    try {
      var saved = localStorage.getItem(KEY);
      if (saved === "light" || saved === "dark") return saved;
    } catch (e) { /* storage blocked: fall through */ }
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  }
  function apply(theme) {
    root.setAttribute("data-theme", theme);
    var button = document.getElementById("theme-toggle");
    if (button) button.setAttribute("aria-label", theme === "dark" ? "Switch to light theme" : "Switch to dark theme");
  }
  apply(initial());
  document.addEventListener("DOMContentLoaded", function () {
    apply(root.getAttribute("data-theme"));
    document.getElementById("theme-toggle").addEventListener("click", function () {
      var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
      try { localStorage.setItem(KEY, next); } catch (e) { /* private window: this page only */ }
      apply(next);
    });
  });
})();
