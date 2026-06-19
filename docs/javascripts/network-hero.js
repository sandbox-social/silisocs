/* ---------------------------------------------------------------------------
 * network-hero.js — animated "society of agents" backdrop for the docs hero.
 * Self-contained (no dependencies). Mounts onto <canvas id="sx-net"> when the
 * home page is shown. Honors prefers-reduced-motion (static frame) and pauses
 * while offscreen. Re-initialises on Material's instant navigation.
 * ------------------------------------------------------------------------- */
(function () {
  "use strict";

  // Muted teal -> slate so the network reads as quiet texture.
  var NODE_A = [13, 148, 136];
  var NODE_B = [100, 116, 139];
  var LINK_DIST = 150; // px at which two agents are "connected"
  var running = null; // current animation handle so we can tear down on nav

  function rgba(c, a) {
    return "rgba(" + c[0] + "," + c[1] + "," + c[2] + "," + a + ")";
  }

  function lerp(a, b, t) {
    return [
      Math.round(a[0] + (b[0] - a[0]) * t),
      Math.round(a[1] + (b[1] - a[1]) * t),
      Math.round(a[2] + (b[2] - a[2]) * t),
    ];
  }

  function setup(canvas) {
    var ctx = canvas.getContext("2d");
    if (!ctx) return null;

    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    var w = 0;
    var h = 0;
    var nodes = [];

    function resize() {
      var rect = canvas.getBoundingClientRect();
      w = rect.width;
      h = rect.height;
      canvas.width = Math.max(1, Math.floor(w * dpr));
      canvas.height = Math.max(1, Math.floor(h * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      // Node count scales with area, capped for performance. Sparse on purpose.
      var target = Math.min(40, Math.max(16, Math.round((w * h) / 20000)));
      nodes = [];
      for (var i = 0; i < target; i++) {
        nodes.push({
          x: Math.random() * w,
          y: Math.random() * h,
          vx: (Math.random() - 0.5) * 0.2,
          vy: (Math.random() - 0.5) * 0.2,
          r: 1.3 + Math.random() * 1.8,
          c: lerp(NODE_A, NODE_B, Math.random()),
        });
      }
    }

    function draw() {
      ctx.clearRect(0, 0, w, h);

      // Edges
      for (var i = 0; i < nodes.length; i++) {
        for (var j = i + 1; j < nodes.length; j++) {
          var a = nodes[i];
          var b = nodes[j];
          var dx = a.x - b.x;
          var dy = a.y - b.y;
          var d = Math.sqrt(dx * dx + dy * dy);
          if (d < LINK_DIST) {
            var alpha = (1 - d / LINK_DIST) * 0.28;
            ctx.strokeStyle = rgba(lerp(a.c, b.c, 0.5), alpha);
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(a.x, a.y);
            ctx.lineTo(b.x, b.y);
            ctx.stroke();
          }
        }
      }

      // Nodes
      for (var k = 0; k < nodes.length; k++) {
        var n = nodes[k];
        ctx.fillStyle = rgba(n.c, 0.6);
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    function step() {
      for (var i = 0; i < nodes.length; i++) {
        var n = nodes[i];
        n.x += n.vx;
        n.y += n.vy;
        if (n.x < 0 || n.x > w) n.vx *= -1;
        if (n.y < 0 || n.y > h) n.vy *= -1;
      }
      draw();
    }

    return { resize: resize, step: step, draw: draw };
  }

  function start(canvas) {
    var sim = setup(canvas);
    if (!sim) return null;
    sim.resize();

    var reduce =
      window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    var raf = null;
    var visible = true;

    function loop() {
      if (visible) sim.step();
      raf = window.requestAnimationFrame(loop);
    }

    var onResize = function () {
      sim.resize();
      if (reduce) sim.draw();
    };
    window.addEventListener("resize", onResize);

    var io = null;
    if ("IntersectionObserver" in window) {
      io = new IntersectionObserver(
        function (entries) {
          visible = entries[0].isIntersecting;
        },
        { threshold: 0 }
      );
      io.observe(canvas);
    }

    if (reduce) {
      sim.draw(); // single static frame, no animation
    } else {
      loop();
    }

    return function teardown() {
      if (raf) window.cancelAnimationFrame(raf);
      window.removeEventListener("resize", onResize);
      if (io) io.disconnect();
    };
  }

  function init() {
    if (running) {
      running();
      running = null;
    }
    var canvas = document.getElementById("sx-net");
    if (canvas) {
      running = start(canvas);
    }
  }

  // Material instant navigation exposes an RxJS observable `document$`.
  if (typeof window.document$ !== "undefined" && window.document$.subscribe) {
    window.document$.subscribe(init);
  } else if (document.readyState !== "loading") {
    init();
  } else {
    document.addEventListener("DOMContentLoaded", init);
  }
})();
