/* The shell's behaviour: theme toggle, mobile navigation, diagram embeds,
   scroll-spy for the table of contents, and back-to-top. Vanilla, by policy —
   a documentation site that needs a toolchain stops building. */
(function () {
  "use strict";

  var root = document.documentElement;
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function forEach(list, fn) {
    Array.prototype.forEach.call(list, fn);
  }

  /* Theme. The inline script in <head> sets data-theme before first paint;
     this only persists a manual choice. */
  function setTheme(theme) {
    root.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("sf-theme", theme);
    } catch (e) {
      /* private mode: the toggle still works for the session */
    }
  }
  forEach(document.querySelectorAll(".theme-toggle"), function (button) {
    button.addEventListener("click", function () {
      setTheme(root.getAttribute("data-theme") === "light" ? "dark" : "light");
    });
  });

  /* Mobile slide-over navigation. */
  var navToggle = document.querySelector(".nav-toggle");
  function closeNav() {
    document.body.classList.remove("nav-open");
    if (navToggle) navToggle.setAttribute("aria-expanded", "false");
  }
  if (navToggle) {
    navToggle.addEventListener("click", function () {
      var open = document.body.classList.toggle("nav-open");
      navToggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }
  var scrim = document.querySelector(".scrim");
  if (scrim) scrim.addEventListener("click", closeNav);
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeNav();
  });
  forEach(document.querySelectorAll(".sidebar a"), function (link) {
    link.addEventListener("click", closeNav);
  });

  /* Diagram lazy-embeds: the poster is a PNG; pressing the button swaps in
     the interactive document. */
  forEach(document.querySelectorAll(".diagram-embed"), function (embed) {
    var button = embed.querySelector(".diagram-load");
    if (!button) return;
    button.addEventListener("click", function () {
      var poster = embed.querySelector("img");
      var frame = document.createElement("iframe");
      frame.setAttribute("src", embed.getAttribute("data-diagram"));
      frame.setAttribute("loading", "lazy");
      frame.setAttribute("title", poster ? poster.getAttribute("alt") : "Interactive diagram");
      embed.innerHTML = "";
      embed.appendChild(frame);
    });
  });

  /* Scroll-spy: mark the current section in the "On this page" column — the last
     heading above the reading line. */
  var tocLinks = document.querySelectorAll(".toc nav a");
  var contentHeadings = document.querySelectorAll(".content h2[id]");
  if (tocLinks.length && contentHeadings.length) {
    var spy = function () {
      var current = null;
      forEach(contentHeadings, function (heading) {
        if (heading.getBoundingClientRect().top < 140) current = heading.id;
      });
      forEach(tocLinks, function (link) {
        link.classList.toggle(
          "current",
          current !== null && link.getAttribute("href") === "#" + current
        );
      });
    };
    window.addEventListener("scroll", spy, { passive: true });
    spy();
  }

  /* Back to top. */
  var toTop = document.querySelector(".to-top");
  if (toTop) {
    var onScroll = function () {
      toTop.classList.toggle("show", window.scrollY > 600);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
    toTop.addEventListener("click", function () {
      window.scrollTo({ top: 0, behavior: reduced ? "auto" : "smooth" });
    });
  }
})();
