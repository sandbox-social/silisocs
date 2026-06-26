/* ---------------------------------------------------------------------------
 * network-hero.js — animated "society of agents" backdrop for the docs hero.
 * Self-contained (no dependencies). Mounts onto <canvas id="sx-net"> when the
 * home page is shown. Honors prefers-reduced-motion (static frame) and pauses
 * while offscreen. Re-initialises on Material's instant navigation.
 *
 * Aesthetic: a dense, well-connected violet->teal mesh with data "packets"
 * pulsing along active edges — reads as a live network of communicating agents.
 * ------------------------------------------------------------------------- */
(function () {
  "use strict";

  // Violet -> teal so the mesh reads as a cool, technical gradient.
  var NODE_A = [124, 58, 237]; // violet
  var NODE_B = [13, 148, 136]; // teal
  var PACKET = [94, 234, 212]; // bright teal — traveling data packets
  var LINK_DIST = 168; // px at which two agents are "connected"
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
    var packets = [];

    function spawnPacket() {
      // Pick a random pair that is currently connected; ride the edge from a->b.
      for (var tries = 0; tries < 12; tries++) {
        var i = (Math.random() * nodes.length) | 0;
        var j = (Math.random() * nodes.length) | 0;
        if (i === j) continue;
        var a = nodes[i];
        var b = nodes[j];
        var dx = a.x - b.x;
        var dy = a.y - b.y;
        if (dx * dx + dy * dy < LINK_DIST * LINK_DIST) {
          return { i: i, j: j, t: Math.random() * 0.3, speed: 0.006 + Math.random() * 0.012 };
        }
      }
      return null;
    }

    function resize() {
      var rect = canvas.getBoundingClientRect();
      w = rect.width;
      h = rect.height;
      canvas.width = Math.max(1, Math.floor(w * dpr));
      canvas.height = Math.max(1, Math.floor(h * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      // Node count scales with area, capped for performance. Denser than before
      // so the mesh reads as a populous, busy society.
      var target = Math.min(110, Math.max(34, Math.round((w * h) / 8500)));
      nodes = [];
      for (var i = 0; i < target; i++) {
        nodes.push({
          x: Math.random() * w,
          y: Math.random() * h,
          vx: (Math.random() - 0.5) * 0.26,
          vy: (Math.random() - 0.5) * 0.26,
          r: 1.2 + Math.random() * 2.0,
          // A few "hub" nodes glow brighter for a sense of structure.
          hub: Math.random() < 0.12,
          c: lerp(NODE_A, NODE_B, Math.random()),
        });
      }

      // Packet count scales with the mesh; gives the live "traffic" feel.
      var packetTarget = Math.min(26, Math.max(8, (target / 4) | 0));
      packets = [];
      for (var p = 0; p < packetTarget; p++) {
        var pkt = spawnPacket();
        if (pkt) packets.push(pkt);
      }
    }

    function draw() {
      ctx.clearRect(0, 0, w, h);

      // Edges — brighter and reaching further so the network feels connected.
      for (var i = 0; i < nodes.length; i++) {
        for (var j = i + 1; j < nodes.length; j++) {
          var a = nodes[i];
          var b = nodes[j];
          var dx = a.x - b.x;
          var dy = a.y - b.y;
          var d = Math.sqrt(dx * dx + dy * dy);
          if (d < LINK_DIST) {
            var alpha = (1 - d / LINK_DIST) * 0.4;
            ctx.strokeStyle = rgba(lerp(a.c, b.c, 0.5), alpha);
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(a.x, a.y);
            ctx.lineTo(b.x, b.y);
            ctx.stroke();
          }
        }
      }

      // Data packets riding along active edges.
      for (var q = 0; q < packets.length; q++) {
        var pk = packets[q];
        var na = nodes[pk.i];
        var nb = nodes[pk.j];
        if (!na || !nb) continue;
        var px = na.x + (nb.x - na.x) * pk.t;
        var py = na.y + (nb.y - na.y) * pk.t;
        ctx.fillStyle = rgba(PACKET, 0.9);
        ctx.beginPath();
        ctx.arc(px, py, 1.6, 0, Math.PI * 2);
        ctx.fill();
        // soft glow
        ctx.fillStyle = rgba(PACKET, 0.18);
        ctx.beginPath();
        ctx.arc(px, py, 4.5, 0, Math.PI * 2);
        ctx.fill();
      }

      // Nodes — hubs get a faint halo.
      for (var k = 0; k < nodes.length; k++) {
        var n = nodes[k];
        if (n.hub) {
          ctx.fillStyle = rgba(n.c, 0.12);
          ctx.beginPath();
          ctx.arc(n.x, n.y, n.r * 3.2, 0, Math.PI * 2);
          ctx.fill();
        }
        ctx.fillStyle = rgba(n.c, n.hub ? 0.85 : 0.65);
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

      // Advance packets; respawn when they arrive or their edge stretches apart.
      for (var q = 0; q < packets.length; q++) {
        var pk = packets[q];
        pk.t += pk.speed;
        var na = nodes[pk.i];
        var nb = nodes[pk.j];
        var stale = !na || !nb;
        if (!stale) {
          var dx = na.x - nb.x;
          var dy = na.y - nb.y;
          stale = dx * dx + dy * dy > LINK_DIST * LINK_DIST * 1.05;
        }
        if (pk.t >= 1 || stale) {
          var fresh = spawnPacket();
          if (fresh) packets[q] = fresh;
          else pk.t = 0;
        }
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
