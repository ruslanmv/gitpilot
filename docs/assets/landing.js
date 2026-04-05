/* GitPilot landing page — progressive enhancements.
 * Self-contained, no dependencies. Everything degrades gracefully
 * if JS is disabled: nav works as a static list, tabs fall back to
 * the first panel, code blocks stay readable without a copy button.
 */
(function () {
  "use strict";

  // ── 1. Mobile nav toggle ─────────────────────────────────────
  var toggle = document.querySelector(".nav__toggle");
  var links = document.getElementById("primary-nav");
  if (toggle && links) {
    toggle.addEventListener("click", function () {
      var isOpen = links.classList.toggle("is-open");
      toggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
    });
    // Close when a link is tapped
    links.addEventListener("click", function (e) {
      if (e.target.tagName === "A") {
        links.classList.remove("is-open");
        toggle.setAttribute("aria-expanded", "false");
      }
    });
  }

  // ── 2. Install tabs ──────────────────────────────────────────
  document.querySelectorAll("[data-tabs]").forEach(function (root) {
    var tabs = root.querySelectorAll(".tabs__tab");
    var panels = root.querySelectorAll(".tabs__panel");

    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        var target = tab.getAttribute("data-tab");

        tabs.forEach(function (t) {
          var active = t === tab;
          t.classList.toggle("is-active", active);
          t.setAttribute("aria-selected", active ? "true" : "false");
        });

        panels.forEach(function (p) {
          var match = p.getAttribute("data-panel") === target;
          p.classList.toggle("is-active", match);
          if (match) {
            p.removeAttribute("hidden");
          } else {
            p.setAttribute("hidden", "");
          }
        });
      });
    });
  });

  // ── 3. Copy-to-clipboard on code blocks ──────────────────────
  document.querySelectorAll("[data-copy]").forEach(function (wrapper) {
    var btn = wrapper.querySelector(".code__copy");
    var code = wrapper.querySelector("pre code");
    if (!btn || !code) return;

    btn.addEventListener("click", function () {
      var text = code.innerText || code.textContent || "";
      var done = function () {
        var original = btn.textContent;
        btn.textContent = "Copied";
        btn.classList.add("is-copied");
        setTimeout(function () {
          btn.textContent = original;
          btn.classList.remove("is-copied");
        }, 1600);
      };

      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done).catch(fallback);
      } else {
        fallback();
      }

      function fallback() {
        var textarea = document.createElement("textarea");
        textarea.value = text;
        textarea.setAttribute("readonly", "");
        textarea.style.position = "absolute";
        textarea.style.left = "-9999px";
        document.body.appendChild(textarea);
        textarea.select();
        try { document.execCommand("copy"); done(); } catch (_) { /* no-op */ }
        document.body.removeChild(textarea);
      }
    });
  });

  // ── 4. Footer year ───────────────────────────────────────────
  var year = document.getElementById("year");
  if (year) { year.textContent = String(new Date().getFullYear()); }
})();
