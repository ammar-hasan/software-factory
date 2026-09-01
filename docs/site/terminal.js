/* The refusal transcript, typed out. The lines are the README's own; the
   verdict line is highlighted at the end. Reduced motion renders the whole
   transcript at once; the typing pauses while the card is off-screen. */
(function () {
  "use strict";

  var term = document.querySelector("[data-terminal]");
  if (!term) return;

  var out = term.querySelector("[data-term-out]");
  var cursor = term.querySelector(".term-cursor");
  var replay = term.querySelector(".term-replay");

  var LINES = [
    {
      t: '$ sf work "The CSV importer mangles BOM headers" --factory myfactory --repo ~/code/payments',
      c: "t-cmd",
      mode: "type",
      speed: 22,
      pause: 500,
    },
    { t: "  ok   TRIAGE   scout", c: "t-ok", mode: "line", pause: 420 },
    { t: "  ok   DESIGN   architect", c: "t-ok", mode: "line", pause: 650 },
    { t: "  stop BUILD    builder", c: "t-stop", mode: "line", pause: 300 },
    {
      t: "       · the parent-commit failure is about behaviour at tests/test_importer.py::test_bom:",
      c: "t-dim",
      mode: "line",
      pause: 260,
    },
    {
      t: "         observed import_error failure; expected an assertion failure",
      c: "t-dim",
      mode: "line",
      pause: 550,
    },
    { t: "", c: "", mode: "line", pause: 150 },
    {
      t: "blocked (gate_failed_terminal): The test failed before its body ran, so it proves the code",
      c: "t-block",
      mode: "type",
      speed: 14,
      pause: 0,
    },
    {
      t: "did not exist, not that the behaviour was wrong. Assert on behaviour that the parent commit",
      c: "t-block",
      mode: "type",
      speed: 14,
      pause: 0,
    },
    { t: "gets wrong.", c: "t-block", mode: "type", speed: 14, pause: 0 },
  ];

  function renderAll() {
    out.innerHTML = "";
    LINES.forEach(function (line) {
      var span = document.createElement("span");
      if (line.c) span.className = line.c;
      span.textContent = line.t + "\n";
      out.appendChild(span);
    });
    term.classList.add("done");
    replay.hidden = false;
  }

  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    renderAll();
  } else {
    var visible = false;
    var started = false;
    var timer = null;

    function later(fn, ms) {
      timer = window.setTimeout(function () {
        if (visible) fn();
        else later(fn, 200);
      }, ms);
    }

    function typeLine(index) {
      if (index >= LINES.length) {
        term.classList.add("done");
        replay.hidden = false;
        return;
      }
      var line = LINES[index];
      var span = document.createElement("span");
      if (line.c) span.className = line.c;
      out.appendChild(span);
      if (line.mode === "line") {
        span.textContent = line.t + "\n";
        later(function () {
          typeLine(index + 1);
        }, line.pause);
        return;
      }
      var i = 0;
      (function tick() {
        i += 1;
        span.textContent = line.t.slice(0, i);
        if (i < line.t.length) later(tick, line.speed);
        else {
          span.textContent = line.t + "\n";
          later(function () {
            typeLine(index + 1);
          }, line.pause);
        }
      })();
    }

    function start() {
      term.classList.remove("done");
      out.innerHTML = "";
      replay.hidden = true;
      typeLine(0);
    }

    replay.addEventListener("click", function () {
      window.clearTimeout(timer);
      start();
    });

    if ("IntersectionObserver" in window) {
      new IntersectionObserver(
        function (entries) {
          visible = entries[0].isIntersecting;
          if (visible && !started) {
            started = true;
            start();
          }
        },
        { threshold: 0.2 }
      ).observe(term);
    } else {
      visible = true;
      start();
    }
  }
})();
