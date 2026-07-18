(() => {
  "use strict";

  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");

  window.openRepositoryDialog = () => {
    const dialog = document.getElementById("connect-project");
    dialog?.showModal();
    dialog?.querySelector('[name="nickname"]')?.focus();
  };

  window.connectRepository = async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const path = form.elements.path.value.trim();
    const nickname = form.elements.nickname.value.trim();
    const submit = form.querySelector('[type="submit"]');
    submit.disabled = true;
    try {
      const response = await fetch("/api/repositories", {
        method: "POST",
        headers: {"content-type": "application/json"},
        body: JSON.stringify({path, nickname}),
      });
      if (!response.ok) {
        notify(await response.text(), "danger");
        return;
      }
      location.reload();
    } finally {
      submit.disabled = false;
    }
  };

  function initObservatory(root) {
    const stage = root.querySelector(".observatory-stage");
    const lines = root.querySelector(".field-lines");
    const nodes = [...root.querySelectorAll("[data-field-node]")];
    const title = root.querySelector("[data-readout-title]");
    const meta = root.querySelector("[data-readout-meta]");
    if (!stage || !lines || !nodes.length) return;

    const ns = "http://www.w3.org/2000/svg";
    const draw = () => {
      const bounds = stage.getBoundingClientRect();
      const center = {x: bounds.width / 2, y: bounds.height / 2};
      lines.replaceChildren();
      nodes.forEach((node, index) => {
        const box = node.querySelector(".node-core").getBoundingClientRect();
        const point = {
          x: box.left + box.width / 2 - bounds.left,
          y: box.top + box.height / 2 - bounds.top,
        };
        const line = document.createElementNS(ns, "path");
        const bend = index % 2 ? -22 : 22;
        const midX = (center.x + point.x) / 2 + bend;
        const midY = (center.y + point.y) / 2 - bend;
        line.setAttribute("d", `M${center.x} ${center.y} Q${midX} ${midY} ${point.x} ${point.y}`);
        line.classList.add("field-line", `signal-${index % 4}`);
        line.style.setProperty("--delay", `${index * -0.37}s`);
        lines.append(line);
      });
    };

    const focus = node => {
      nodes.forEach(item => item.classList.toggle("is-muted", item !== node));
      title.textContent = node.dataset.title;
      meta.textContent = node.dataset.meta;
      node.classList.add("is-focused");
    };
    const reset = () => nodes.forEach(item => item.classList.remove("is-muted", "is-focused"));
    nodes.forEach(node => {
      node.addEventListener("pointerenter", () => focus(node));
      node.addEventListener("focus", () => focus(node));
      node.addEventListener("pointerleave", reset);
      node.addEventListener("blur", reset);
    });

    let frame = 0;
    stage.addEventListener("pointermove", event => {
      if (reducedMotion.matches) return;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const box = stage.getBoundingClientRect();
        stage.style.setProperty("--field-x", `${((event.clientX - box.left) / box.width - 0.5) * 8}px`);
        stage.style.setProperty("--field-y", `${((event.clientY - box.top) / box.height - 0.5) * 8}px`);
      });
    });
    stage.addEventListener("pointerleave", () => {
      stage.style.setProperty("--field-x", "0px");
      stage.style.setProperty("--field-y", "0px");
    });

    const resize = new ResizeObserver(draw);
    resize.observe(stage);
    draw();
    if (!reducedMotion.matches) {
      let index = 0;
      setInterval(() => {
        nodes.forEach(node => node.classList.remove("has-signal"));
        nodes[index++ % nodes.length].classList.add("has-signal");
      }, 1800);
    }
  }

  function initCommandPalette() {
    const dialog = document.getElementById("command-palette");
    const search = document.getElementById("command-search");
    if (!dialog || !search) return;
    dialog.addEventListener("click", event => {
      if (event.target === dialog) dialog.close();
    });
    search.addEventListener("keydown", event => {
      const items = [...dialog.querySelectorAll("nav > a:not([hidden]), nav > button:not([hidden])")];
      const active = document.activeElement;
      const index = items.indexOf(active);
      if (event.key === "ArrowDown") {
        event.preventDefault();
        items[index < items.length - 1 ? index + 1 : 0]?.focus();
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        items[index > 0 ? index - 1 : items.length - 1]?.focus();
      }
    });
  }

  addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-observatory]").forEach(initObservatory);
    initCommandPalette();
  });
})();
