/* The Studio shell: everything every page gets, and nothing page-specific.
 *
 * Loaded blocking in <head>, after boot.js and panels.js and BEFORE any page
 * module, so `notify`, `renderPanel` and the auth-attaching `fetch` wrapper are
 * defined by the time a page module's first response comes back. (When this
 * code lived inline at the end of <body> that was true by accident — an inline
 * script costs no round trip — and it stopped being true the moment it became a
 * fetched asset.) Everything that needs the DOM therefore waits for
 * DOMContentLoaded, including the palette: page modules register commands into
 * the boot.js queue while the document parses, and the flush below is what
 * turns them into dialog entries.
 *
 * Depends on: boot.js (theme, palette queue, studioPageData) and panels.js
 * (chartTheme, initFigures, initNetwork, themeCyInstances). */

const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");

/* ---- control-plane auth --------------------------------------------------
 * A browser cannot attach a header to a page load, so the token is stored in
 * this browser and attached to every mutating fetch instead. */
const nativeFetch = window.fetch.bind(window);
window.fetch = (input, options = {}) => {
  const method = (options.method || "GET").toUpperCase();
  if (["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
    const token = localStorage.getItem("silisocs-auth-token");
    if (token) {
      options.headers = new Headers(options.headers || {});
      options.headers.set("authorization", `Bearer ${token}`);
    }
  }
  return nativeFetch(input, options);
};

/* ---- theme --------------------------------------------------------------- */
window.toggleTheme = () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("silisocs-theme", next);
  document.querySelectorAll("[data-figure]").forEach(el => window.Plotly?.relayout(el, chartTheme()));
  themeCyInstances();
  document.getElementById("platform-frame")?.contentWindow?.postMessage({type: "silisocs-theme", theme: next}, "*");
};

/* ---- toasts --------------------------------------------------------------
 * A success toast is a receipt and may fade. A danger toast is often the ONLY
 * report of a failure — three seconds is not long enough to read a stack of
 * YAML positions in — so it stays until the reader dismisses it. Live toasts
 * are stacked upwards, because a persistent one that lands underneath another
 * hides exactly what it was raised to show. */
const TOAST_GAP = 12;
function restackToasts() {
  let offset = 0;
  [...document.querySelectorAll(".toast")].reverse().forEach(el => {
    el.style.bottom = `calc(var(--space-xl) + ${offset}px)`;
    offset += el.offsetHeight + TOAST_GAP;
  });
}
window.notify = (message, tone = "neutral") => {
  const el = document.createElement("div");
  el.className = `toast ${tone}`;
  el.dataset.testid = "toast";
  // The toast is built here and nowhere else, so its internal layout is set
  // here too (in design tokens): the dismiss control must sit beside a message
  // that wraps, never inside it.
  el.style.display = "flex";
  el.style.alignItems = "center";
  el.style.gap = "var(--space-md)";
  const text = document.createElement("span");
  text.textContent = message;
  el.append(text);
  const dismiss = () => {
    el.remove();
    restackToasts();
  };
  if (tone === "danger") {
    const close = document.createElement("button");
    close.type = "button";
    close.className = "icon-button";
    close.title = "Dismiss";
    close.ariaLabel = "Dismiss";
    close.dataset.testid = "toast-close";
    close.textContent = "×";
    close.style.flex = "0 0 auto";
    close.style.marginLeft = "auto";
    close.onclick = dismiss;
    el.append(close);
  } else setTimeout(dismiss, 3000);
  document.body.append(el);
  restackToasts();
  return el;
};

/* ---- the one fetch --------------------------------------------------------
 * Every Studio call goes through `apiFetch`, so no failure can end as a button
 * that just did nothing. A non-ok response becomes a danger toast carrying the
 * server's own `detail` (the API answers `{"detail": ...}` for both deliberate
 * errors and unhandled ones), and the caller gets null so it stops instead of
 * proceeding on a body it never received. Saves stay on raw `fetch`: a 409 is a
 * conflict dialog, not a toast — they use `apiError` for their message. */
window.apiError = async response => {
  const text = await response.text();
  try {
    const {detail} = JSON.parse(text);
    if (detail) return typeof detail === "string" ? detail : JSON.stringify(detail);
  } catch {
    /* not JSON: the raw body is the best message there is */
  }
  return text.trim() || `${response.status} ${response.statusText}`;
};
/* The in-flight contract for an action button: it is visibly out of action
 * while its work runs, and it comes back however that work ends. */
window.withBusy = async (button, work) => {
  if (!button) return work();
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try {
    return await work();
  } finally {
    button.disabled = false;
    button.removeAttribute("aria-busy");
  }
};
window.apiFetch = async (url, options) => {
  let response;
  try {
    response = await fetch(url, options);
  } catch (error) {
    notify(`Could not reach ${url}: ${error.message}`, "danger");
    return null;
  }
  if (response.ok) return response;
  notify(await apiError(response), "danger");
  return null;
};

/* ---- save conflicts ------------------------------------------------------
 * Two editors on one document used to overwrite each other in silence. A save
 * whose fingerprint is stale now comes back 409, and this dialog is the whole
 * UX: it names the file, shows what the other editor changed, and offers the
 * two honest ways out. Nothing is discarded without the user saying so. */
window.saveConflict = async response => {
  if (response.status !== 409) return null;
  try {
    const detail = (await response.clone().json()).detail;
    return detail && detail.error === "conflict" ? detail : null;
  } catch {
    return null;
  }
};

function diffLineClass(line) {
  if (line.startsWith("+++") || line.startsWith("---")) return "diff-header";
  if (line.startsWith("@@")) return "diff-hunk";
  if (line.startsWith("+")) return "diff-addition";
  if (line.startsWith("-")) return "diff-removal";
  return "diff-context";
}

// Resolves with the outcome of the user's choice so callers can await the
// conflict like any other save result: Overwrite resolves with the retried
// save's own result (a caller mid-action — Launch, add-evaluator — then
// continues that action instead of silently abandoning it); Keep editing,
// Reload, and Escape resolve false.
window.showSaveConflict = (detail, {onReload, onOverwrite}) => new Promise(resolve => {
  document.querySelector('[data-testid="save-conflict"]')?.remove();
  let settled = false;
  let chosen = false;
  const settle = value => {
    if (!settled) {
      settled = true;
      resolve(value);
    }
  };
  const dialog = document.createElement("dialog");
  dialog.className = "command-palette compact-dialog";
  dialog.dataset.testid = "save-conflict";
  const form = document.createElement("form");
  form.className = "form-group";
  const eyebrow = document.createElement("p");
  eyebrow.className = "eyebrow";
  eyebrow.textContent = "Conflicting edit";
  const title = document.createElement("h2");
  title.textContent = detail.file;
  const message = document.createElement("p");
  message.textContent = `${detail.message} Your save was not written — nothing on disk changed.`;
  const diff = document.createElement("pre");
  diff.className = "config-diff";
  diff.style.height = "220px";
  diff.replaceChildren(
    ...(detail.diff || "").split("\n").map(line => {
      const span = document.createElement("span");
      span.className = diffLineClass(line);
      span.textContent = line;
      return span;
    }),
  );
  const actions = document.createElement("div");
  actions.className = "composer-actions";
  const button = (label, className, handler) => {
    const el = document.createElement("button");
    el.type = "button";
    el.className = className;
    el.textContent = label;
    el.onclick = async () => {
      chosen = true; // the queued close event must not settle before the handler
      dialog.close();
      dialog.remove();
      settle(handler ? await handler() : false);
    };
    return el;
  };
  actions.append(
    button("Keep editing", "button quiet"),
    button("Reload their version", "button", async () => {
      onReload?.();
      return false;
    }),
    button("Overwrite", "button primary", onOverwrite),
  );
  form.append(eyebrow, title, message, diff, actions);
  dialog.append(form);
  // Dismissing with Escape means "keep editing": the dialog goes, the unsaved
  // edits stay exactly where they are.
  dialog.addEventListener("close", () => {
    dialog.remove();
    if (!chosen) settle(false);
  });
  document.body.append(dialog);
  dialog.showModal();
});

/* ---- command palette -----------------------------------------------------
 * The dialog only exists once the body is parsed, so the real registration
 * function replaces boot.js's queueing stub at DOMContentLoaded. */
function appendPaletteCommand({label, href, keywords = "", action}) {
  const nav = document.querySelector("#command-palette nav");
  const item = document.createElement(href ? "a" : "button");
  if (href) item.href = href;
  else item.type = "button";
  item.textContent = label;
  item.dataset.keywords = keywords;
  if (action)
    item.addEventListener("click", () => {
      document.getElementById("command-palette").close();
      action();
    });
  nav.append(item);
  return item;
}
function flushPaletteCommands() {
  const pending = window.paletteCommands;
  window.registerPaletteCommand = appendPaletteCommand;
  pending.forEach(appendPaletteCommand);
  // Pages declare their palette commands as DATA in the page island, so the
  // labels stay in the server-rendered HTML (searchable, testable) while the
  // wiring lives here. `action` names a global on this page's module.
  for (const command of studioPageData().paletteCommands || [])
    appendPaletteCommand({...command, action: command.action ? window[command.action] : undefined});
}

async function hydratePalette() {
  const nav = document.querySelector("#command-palette nav");
  if (nav.dataset.loaded) return;
  const sources = [
    ["/api/runs", "Run", item => "/runs/" + item.id.split("/").map(encodeURIComponent).join("/")],
    [
      "/api/scenarios",
      "Scenario",
      item =>
        "/scenarios/" +
        encodeURIComponent(item.name) +
        (item.source && item.source !== "workspace" ? `?source=${encodeURIComponent(item.source)}` : ""),
    ],
    ["/api/studies", "Study", item => "/studies/" + encodeURIComponent(item.id)],
  ];
  // One report, and the palette is only marked loaded after a fully successful
  // pass: a half-hydrated palette that never retries silently hides everything
  // the failed source owned. A retry re-adds only what this function fetched,
  // so the page's own commands survive and nothing is listed twice.
  nav.querySelectorAll("[data-hydrated]").forEach(item => item.remove());
  let failure = null;
  for (const [url, kind, href] of sources) {
    try {
      const response = await fetch(url);
      if (!response.ok) throw new Error(await apiError(response));
      const data = await response.json();
      for (const item of data.items || [])
        registerPaletteCommand({
          label: `${kind}: ${item.scenario || item.name || item.id}`,
          href: href(item),
          keywords: `${item.id || ""} ${item.source_label || ""}`,
        }).dataset.hydrated = "true";
    } catch (error) {
      failure ??= `${kind} search is incomplete: ${error.message || error}`;
    }
  }
  if (failure) notify(failure, "danger");
  else nav.dataset.loaded = "true";
}
window.openPalette = () => {
  const dialog = document.getElementById("command-palette");
  dialog.showModal();
  document.getElementById("command-search").focus();
  hydratePalette();
};
document.addEventListener("keydown", event => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    openPalette();
  }
  if (event.key === "Escape") document.getElementById("command-palette").close();
});

function initCommandPalette() {
  const dialog = document.getElementById("command-palette");
  const search = document.getElementById("command-search");
  if (!dialog || !search) return;
  search.addEventListener("input", event => {
    const value = event.target.value.toLowerCase();
    document.querySelectorAll("#command-palette nav>a,#command-palette nav>button").forEach(item => {
      item.hidden = !`${item.textContent} ${item.dataset.keywords || ""}`.toLowerCase().includes(value);
    });
  });
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

/* ---- project repositories (the connect dialog and the settings page) ------ */
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
  return withBusy(form.querySelector('[type="submit"]'), async () => {
    const response = await apiFetch("/api/repositories", {
      method: "POST",
      headers: {"content-type": "application/json"},
      body: JSON.stringify({path, nickname}),
    });
    if (response) location.reload();
  });
};

async function removeRepository(source) {
  if (await apiFetch(`/api/repositories/${encodeURIComponent(source)}`, {method: "DELETE"})) location.reload();
}
async function renameRepository(source) {
  const nickname = document.getElementById(`repository-nickname-${source}`).value.trim();
  const response = await apiFetch(`/api/repositories/${encodeURIComponent(source)}`, {
    method: "PATCH",
    headers: {"content-type": "application/json"},
    body: JSON.stringify({nickname}),
  });
  if (response) notify("Project nickname saved.", "success");
}
async function refreshRepositories() {
  if (await apiFetch("/api/repositories/refresh", {method: "POST"})) location.reload();
}
function storeAuthToken() {
  localStorage.setItem("silisocs-auth-token", document.getElementById("auth-token").value);
  notify("Token stored for this browser.", "success");
}
function initSettings() {
  const field = document.getElementById("auth-token");
  if (field) field.value = localStorage.getItem("silisocs-auth-token") || "";
}

/* ---- home observatory ---------------------------------------------------- */
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

/* ---- boot ----------------------------------------------------------------
 * Everything below touches the DOM, so it waits for the parser. Page modules
 * have already run by then; their queued palette commands flush first. */
addEventListener("DOMContentLoaded", () => {
  flushPaletteCommands();
  initCommandPalette();
  initFigures(document);
  initNetwork(document);
  initSettings();
  document.querySelectorAll("[data-observatory]").forEach(initObservatory);
});
