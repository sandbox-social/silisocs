/* THE CLIENT PANEL RENDERER.
 *
 * A PanelOutput (figure / table / markdown / html / grid) is rendered in
 * exactly two places in this codebase, and this is one of them:
 *
 *   1. SERVER — `studio/templates/_output.html` (`render_output` macro).
 *      Used for first paint of the run and study pages, and by the static
 *      report exporter (`silisocs.analysis.report`), which renders through the
 *      very same macro rather than a third copy.
 *   2. CLIENT — this file. Used wherever a panel changes WITHOUT a navigation:
 *      a panel control (`setPanelParam`), the Watch tab's SSE refresh, the
 *      study board's live refresh, and both Explore surfaces, which build
 *      panels from JSON the server hands back.
 *
 * Two exist because one surface has no DOM to patch (the server renders a
 * string) and the other must not re-navigate (a full page load would re-fetch
 * every panel plus the ~1.4MB plot bundle to move one dropdown). They are kept
 * in lockstep by hand: any output type must render in BOTH or in neither.
 *
 * This file also owns hydration of server-rendered panel HTML — turning
 * `[data-figure]` / `[data-cy-graph]` placeholders into live Plotly/Cytoscape
 * views — which is why the report exporter inlines this same file instead of
 * carrying its own copy. */

/* ---- lazy chart runtimes -------------------------------------------------
 * The plot and network bundles are large and most pages never draw one, so
 * they are fetched on first use rather than linked from every page. */
const studioAssetPromises = new Map();
function loadStudioAsset(name, globalName) {
  if (window[globalName]) return Promise.resolve(window[globalName]);
  if (studioAssetPromises.has(name)) return studioAssetPromises.get(name);
  const promise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = `/assets/${name}`;
    script.async = true;
    script.onload = () =>
      window[globalName] ? resolve(window[globalName]) : reject(new Error(`${name} loaded without ${globalName}`));
    script.onerror = () => reject(new Error(`Unable to load ${name}`));
    document.head.append(script);
  });
  studioAssetPromises.set(name, promise);
  promise.catch(() => studioAssetPromises.delete(name));
  return promise;
}
const loadPlotly = () => loadStudioAsset("plotly.js", "Plotly");
const loadCytoscape = () => loadStudioAsset("cytoscape.js", "cytoscape");

/* ---- theming -------------------------------------------------------------
 * Chart runtimes cannot read CSS custom properties, so the active theme is
 * resolved against the document and handed to them as plain values. */
function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
function chartTheme() {
  const muted = cssVar("--muted");
  const border = cssVar("--border");
  return {
    "font.color": muted,
    "xaxis.gridcolor": border,
    "xaxis.zerolinecolor": border,
    "yaxis.gridcolor": border,
    "yaxis.zerolinecolor": border,
  };
}
// Cytoscape can't parse var(--x): resolve every var() value against the active theme.
const cyInstances = [];
function resolveCyStyle(style) {
  return (style || []).map(rule => ({
    ...rule,
    style: Object.fromEntries(
      Object.entries(rule.style || {}).map(([key, value]) => [
        key,
        typeof value === "string" && value.startsWith("var(") ? cssVar(value.slice(4, -1)) || value : value,
      ])
    ),
  }));
}
window.themeCyInstances = () => cyInstances.forEach(({cy, rawStyle}) => cy.style(resolveCyStyle(rawStyle)));

/* ---- hydration of server-rendered panels --------------------------------- */
// Network panels emit a payload attribute; the shell owns initialization and theming.
window.initNetwork = async root => {
  const elements = [...root.querySelectorAll("[data-cy-graph]:not([data-cy-ready])")];
  if (!elements.length) return;
  try {
    await loadCytoscape();
  } catch (error) {
    elements.forEach(el => {
      el.textContent = "Network renderer could not be loaded.";
    });
    return;
  }
  elements.forEach(el => {
    const graph = JSON.parse(el.dataset.cyGraph);
    const cy = cytoscape({
      container: el,
      elements: graph.elements || [],
      style: resolveCyStyle(graph.style || []),
      layout: {name: "preset"},
      wheelSensitivity: 0.2,
    });
    el.dataset.cyReady = "true";
    cyInstances.push({cy, rawStyle: graph.style || []});
  });
};
// Figure layouts arrive fully templated from the server (design.plotly).
window.initFigures = async (root, {theme = true} = {}) => {
  const elements = [...root.querySelectorAll("[data-figure]:not([data-figure-ready])")];
  if (!elements.length) return;
  try {
    await loadPlotly();
  } catch (error) {
    elements.forEach(el => {
      el.textContent = "Chart renderer could not be loaded.";
    });
    return;
  }
  elements.forEach(el => {
    const figure = JSON.parse(el.dataset.figure);
    const layout = figure.layout || {};
    if (theme) {
      const palette = chartTheme();
      layout.font = {...(layout.font || {}), color: palette["font.color"]};
      layout.xaxis = {
        ...(layout.xaxis || {}),
        gridcolor: palette["xaxis.gridcolor"],
        zerolinecolor: palette["xaxis.zerolinecolor"],
      };
      layout.yaxis = {
        ...(layout.yaxis || {}),
        gridcolor: palette["yaxis.gridcolor"],
        zerolinecolor: palette["yaxis.zerolinecolor"],
      };
    }
    Plotly.newPlot(el, figure.data || [], layout, {
      responsive: true,
      displaylogo: false,
      modeBarButtonsToRemove: ["lasso2d", "select2d"],
    });
    el.dataset.figureReady = "true";
  });
};

/* ---- client-side PanelOutput rendering ----------------------------------- */
// Mirrors _output.html's render_grid_item: markdown becomes a stat tile,
// html is injected, and everything else renders as its own output type.
function renderGridItem(item) {
  if (item.type === "html") {
    const wrap = document.createElement("div");
    wrap.innerHTML = item.html;
    return wrap.firstElementChild || wrap;
  }
  if (item.type === "markdown") {
    const tile = document.createElement("div");
    tile.className = "stat-tile";
    (item.text || "").split("\n").forEach((line, index) => {
      const el = document.createElement(index === 0 ? "strong" : "span");
      el.textContent = line.replace(/\*\*/g, "");
      tile.append(el);
    });
    return tile;
  }
  const holder = document.createElement("div");
  renderPanel(holder, item);
  return holder;
}

window.renderPanel = async (body, output) => {
  if (output.type === "figure") {
    body.innerHTML = '<div class="plot"></div>';
    try {
      await loadPlotly();
    } catch (error) {
      body.textContent = "Chart renderer could not be loaded.";
      return;
    }
    const layout = output.figure.layout || {};
    await Plotly.newPlot(body.firstElementChild, output.figure.data || [], layout, {
      responsive: true,
      displaylogo: false,
      modeBarButtonsToRemove: ["lasso2d", "select2d"],
    });
    await Plotly.relayout(body.firstElementChild, chartTheme());
    return;
  }
  if (output.type === "html") {
    body.innerHTML = output.html;
    await initNetwork(body);
    return;
  }
  if (output.type === "markdown") {
    const note = document.createElement("p");
    note.className = "panel-note";
    note.textContent = output.text;
    body.replaceChildren(note);
    return;
  }
  if (output.type === "table") {
    const wrap = document.createElement("div");
    const table = document.createElement("table");
    const head = table.createTHead().insertRow();
    const tbody = table.createTBody();
    wrap.className = "table-scroll";
    for (const column of output.columns) {
      const th = document.createElement("th");
      th.textContent = typeof column === "string" ? column : column.name;
      head.append(th);
    }
    for (const row of output.rows) {
      const tr = tbody.insertRow();
      for (const column of output.columns) {
        const value = row[typeof column === "string" ? column : column.name];
        const cell = tr.insertCell();
        // Mapping cells are references: {text, href} is a link, {text} alone is
        // muted text — the same rule _output.html applies server-side.
        if (value && typeof value === "object") {
          const el = document.createElement(value.href ? "a" : "span");
          if (value.href) el.href = value.href;
          else el.className = "muted";
          el.textContent = value.text ?? "";
          cell.append(el);
        } else cell.textContent = value ?? "";
      }
    }
    wrap.append(table);
    body.replaceChildren(wrap);
    return;
  }
  if (output.type === "grid") {
    const grid = document.createElement("div");
    // The enclosing grid may name the class its scope uses for stat grids (the
    // study board's `hypothesis-grid`), matching `_output.html`'s `grid_class`
    // argument — without it a refreshed panel silently re-laid itself out.
    grid.className = body.closest("[data-grid-class]")?.dataset.gridClass || "stat-grid";
    if (output.items.some(item => item.type !== "markdown")) grid.classList.add("rich");
    for (const item of output.items) grid.append(renderGridItem(item));
    body.replaceChildren(grid);
    return;
  }
  // An output type this renderer does not know (a custom panel, or one half of
  // a lockstep change) must SAY so — rendering nothing looks like a panel with
  // nothing to report. Mirrors _output.html's else branch.
  const note = document.createElement("p");
  note.className = "panel-note muted";
  note.dataset.unknownOutput = output?.type ?? "unknown";
  note.textContent = `This panel returned a “${output?.type ?? "unknown"}” output, which this Studio build cannot render.`;
  body.replaceChildren(note);
};

/* ---- in-place panel refresh ---------------------------------------------- */
// The panel's params live in the URL, so a refreshed panel and a reloaded
// page always show the same thing.
function panelQuery(name) {
  const query = new URLSearchParams();
  for (const [key, value] of new URL(location).searchParams)
    if (key.startsWith(`p.${name}.`)) query.set(key.slice(name.length + 3), value);
  return query;
}
// Scope-generic: the enclosing grid names its subject (data-run-id or
// data-study-id) and the matching generic panel endpoint answers, so run
// and study surfaces share one refresh path.
window.refreshPanel = async section => {
  const grid = section.closest("[data-run-id],[data-study-id]");
  if (!grid) return;
  const base = grid.dataset.runId ? `/api/runs/${grid.dataset.runId}` : `/api/studies/${grid.dataset.studyId}`;
  const name = section.dataset.panel;
  const body = section.querySelector(".panel-body");
  body.classList.add("loading");
  body.setAttribute("aria-busy", "true");
  try {
    const response = await fetch(`${base}/panels/${name}?${panelQuery(name)}`);
    if (!response.ok) {
      // Leaving the previous render in place makes a stale chart look freshly
      // refreshed, so the panel says what happened instead. A 409 is not a
      // failure though: it means the panel still has nothing to say (a Watch
      // placeholder requiring two streams, refreshed when only one grew), so
      // it keeps saying that — and stays refreshable for when the rest lands.
      const note = document.createElement("p");
      note.className = "panel-note muted";
      if (response.status === 409) {
        note.dataset.testid = "panel-awaiting";
        note.textContent = await apiError(response);
      } else {
        note.dataset.testid = "panel-error";
        note.textContent = `This panel could not be refreshed: ${await apiError(response)}`;
      }
      body.replaceChildren(note);
      return;
    }
    const data = await response.json();
    await renderPanel(body, data.output);
    for (const control of data.controls || []) {
      const output = section.querySelector(`[data-control-output="${control.param}"]`);
      if (output) output.textContent = control.value === null || control.value === undefined ? "all" : control.value;
    }
  } finally {
    body.classList.remove("loading");
    body.removeAttribute("aria-busy");
  }
};
// Shell-owned panel controls: keep p.<panel>.<param> in the URL (so the view
// stays linkable) but swap only the affected panel — a full navigation would
// re-fetch every panel and the plot bundle to change one dropdown.
window.setPanelParam = (panel, param, value) => {
  const url = new URL(window.location);
  if (value === "" || value === null) url.searchParams.delete(`p.${panel}.${param}`);
  else url.searchParams.set(`p.${panel}.${param}`, value);
  const section = document.querySelector(`[data-panel="${panel}"]`);
  if (!section || !section.closest("[data-run-id],[data-study-id]")) {
    window.location = url;
    return;
  }
  history.replaceState(null, "", url);
  refreshPanel(section);
};
