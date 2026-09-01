/* The stage machine, alive: a work item travels TRIAGE → DESIGN → BUILD →
   REVIEW → HANDOFF; gates flash as it passes. Every other loop the run is
   stopped at BUILD and the refusal appears — the product's argument in one
   glance. Prefers-reduced-motion renders the finished run, statically. */
(function () {
  "use strict";

  var host = document.querySelector("[data-pipeline]");
  if (!host) return;

  var token = host.querySelector("[data-token]");
  var live = host.querySelector("[data-pipe-live]");
  var chip = host.querySelector("[data-pipe-chip]");
  var refusal = host.querySelector("[data-pipe-refusal]");
  var gates = Array.prototype.slice.call(host.querySelectorAll("[data-gate-x]"));
  var stages = Array.prototype.slice.call(host.querySelectorAll("[data-stage-x]"));
  var X0 = 74;
  var X1 = 706;
  var BUILD_GATE_X = 311;

  function setX(x) {
    token.setAttribute("transform", "translate(" + x + " 60)");
    live.setAttribute("x2", String(x));
  }

  function reset() {
    gates.forEach(function (gate) {
      gate.classList.remove("pass", "block");
    });
    stages.forEach(function (stage) {
      stage.classList.remove("active", "done");
    });
    token.classList.remove("stopped");
    chip.hidden = true;
    refusal.hidden = true;
    setX(X0);
  }

  function finishStatic() {
    reset();
    gates.forEach(function (gate) {
      gate.classList.add("pass");
    });
    stages.forEach(function (stage) {
      stage.classList.add("done");
    });
    setX(X1);
    chip.hidden = false;
  }

  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    finishStatic();
    return;
  }

  /* Pause off-screen: an animation nobody is watching is a battery tax. */
  var visible = true;
  if ("IntersectionObserver" in window) {
    new IntersectionObserver(
      function (entries) {
        visible = entries[0].isIntersecting;
      },
      { threshold: 0.05 }
    ).observe(host);
  }

  function ease(p) {
    return p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2;
  }

  function animate(ms, render) {
    return new Promise(function (resolve) {
      var t = 0;
      var last = null;
      function frame(now) {
        if (last === null) last = now;
        var dt = now - last;
        last = now;
        if (visible && !document.hidden) t += dt;
        render(Math.min(1, t / ms));
        if (t >= ms) resolve();
        else requestAnimationFrame(frame);
      }
      requestAnimationFrame(frame);
    });
  }

  function markStages(x) {
    stages.forEach(function (stage) {
      var sx = Number(stage.getAttribute("data-stage-x"));
      stage.classList.toggle("active", Math.abs(x - sx) < 60 && x < X1 - 30);
      if (x > sx + 50) stage.classList.add("done");
    });
  }

  function passGates(x, upto) {
    gates.forEach(function (gate) {
      var gx = Number(gate.getAttribute("data-gate-x"));
      if (x >= gx && gx < upto) gate.classList.add("pass");
    });
  }

  function passRun() {
    reset();
    return animate(4600, function (p) {
      var x = X0 + (X1 - X0) * ease(p);
      setX(x);
      passGates(x, X1);
      markStages(x);
    })
      .then(function () {
        stages.forEach(function (stage) {
          stage.classList.remove("active");
          stage.classList.add("done");
        });
        chip.hidden = false;
        return animate(1700, function () {});
      })
      .then(function () {
        token.style.opacity = "0";
        chip.hidden = true;
        return animate(450, function () {});
      });
  }

  function blockedRun() {
    reset();
    return animate(2500, function (p) {
      var x = X0 + (BUILD_GATE_X - X0) * ease(p);
      setX(x);
      passGates(x, BUILD_GATE_X);
      markStages(x);
    })
      .then(function () {
        gates.forEach(function (gate) {
          if (Number(gate.getAttribute("data-gate-x")) === BUILD_GATE_X) {
            gate.classList.add("block");
          }
        });
        token.classList.add("stopped");
        refusal.hidden = false;
        return animate(3000, function () {});
      })
      .then(function () {
        token.style.opacity = "0";
        refusal.hidden = true;
        return animate(450, function () {});
      });
  }

  function loop(blocked) {
    var run = blocked ? blockedRun : passRun;
    return run().then(function () {
      reset();
      token.style.opacity = "";
      return loop(!blocked);
    });
  }

  reset();
  loop(false);
})();
