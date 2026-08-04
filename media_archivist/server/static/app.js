// SPDX-License-Identifier: Apache-2.0
(function () {
  "use strict";

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
  }

  function initTheme() {
    var saved = localStorage.getItem("ma-theme");
    if (saved) applyTheme(saved);
  }

  function toggleTheme() {
    var current = document.documentElement.dataset.theme === "light" ? "light" : "dark";
    var next = current === "light" ? "dark" : "light";
    applyTheme(next);
    localStorage.setItem("ma-theme", next);
  }

  document.addEventListener("DOMContentLoaded", function () {
    initTheme();
    var btn = document.getElementById("theme-toggle");
    if (btn) btn.addEventListener("click", toggleTheme);
  });

  document.body && document.body.addEventListener("htmx:responseError", function (evt) {
    // Let inline error fragments render; nothing else to do here.
  });

  window.maToggleTheme = toggleTheme;
})();
