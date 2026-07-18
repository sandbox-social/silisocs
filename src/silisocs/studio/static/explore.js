/* Unified exploration client. One shared query state drives every scene, the
 * inspector, the evidence rail, the run story, and playback — so a selection in
 * one surface reloads the others without a page change. Backend-neutral: scenes
 * and lenses come entirely from the capability document, never a backend name. */
function initExplore(config) {
  const { run, capabilities } = config;
  const $ = (sel) => document.querySelector(sel);
  const sceneHost = $("[data-scene-host]");
  const sceneTitle = $("[data-scene-title]");
  const sceneKicker = $("[data-scene-kicker]");
  const sceneCount = $("[data-scene-count]");
  const inspector = $("[data-inspector]");
  const evidenceHost = $("[data-evidence]");
  const storyHost = $("[data-story]");
  const transport = $("[data-transport]");
  const liveBadge = $("[data-live]");

  const runSegments = run.split("/").map(encodeURIComponent).join("/");
  const apiPath = (kind) => `/api/explore/runs/${runSegments}/${kind}`;
  const sceneById = (id) => capabilities.scenes.find((scene) => scene.id === id);
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const episodes = Number(capabilities.artifact.episodes) || 0;

  let controller = null;
  let activeScene = null;
  let selection = null; // {entity} | {episode} | null
  let playTimer = null;

  /* ---- shared query state (URL is the single source of truth) ---- */
  function queryParams() {
    const query = new URLSearchParams(location.search);
    for (const key of ["lens", "scene", "entity", "event", "cursor"]) query.delete(key);
    return query;
  }
  function updateUrl(values, { push = false } = {}) {
    const url = new URL(location);
    for (const [key, value] of Object.entries(values)) {
      url.searchParams.delete(key);
      for (const item of Array.isArray(value) ? value : [value])
        if (item !== "" && item != null) url.searchParams.append(key, item);
    }
    history[push ? "pushState" : "replaceState"](null, "", url);
  }
  function setBusy(value) {
    sceneHost.classList.toggle("loading", value);
    sceneHost.setAttribute("aria-busy", String(value));
  }
  async function getSceneData(kind, extra = {}) {
    controller?.abort();
    controller = new AbortController();
    const query = queryParams();
    for (const [key, value] of Object.entries(extra)) if (value != null) query.set(key, value);
    const response = await fetch(`${apiPath(kind)}?${query}`, { signal: controller.signal });
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  }
  function empty(title, note) {
    const box = document.createElement("div");
    box.className = "empty";
    const heading = document.createElement("b");
    const copy = document.createElement("p");
    heading.textContent = title;
    copy.textContent = note;
    box.append(heading, copy);
    sceneHost.replaceChildren(box);
  }
  function showError(error) {
    if (error.name === "AbortError") return;
    empty("Scene unavailable", error.message || String(error));
    notify(error.message || String(error), "danger");
  }
  function svg(name, attrs = {}) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", name);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    return node;
  }

  /* ---- selection + linked cross-filtering ---- */
  function inspect(kind, item) {
    const title = document.createElement("h3");
    const meta = document.createElement("p");
    const data = document.createElement("dl");
    title.textContent = item.name || item.label || item.id;
    meta.textContent = kind;
    for (const [key, value] of Object.entries(item)) {
      if (["data", "name", "label"].includes(key)) continue;
      const dt = document.createElement("dt");
      const dd = document.createElement("dd");
      dt.textContent = key.replaceAll("_", " ");
      dd.textContent = Array.isArray(value) ? value.join(", ") : String(value ?? "—");
      data.append(dt, dd);
    }
    const nodes = [title, meta, data];
    if (item.data && Object.keys(item.data).length) {
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      const pre = document.createElement("pre");
      summary.textContent = "Event payload";
      pre.textContent = JSON.stringify(item.data, null, 2);
      details.append(summary, pre);
      nodes.push(details);
    }
    inspector.replaceChildren(...nodes);
  }
  // Selecting an entity refocuses every scene on that actor; selecting an
  // interval narrows the time window. Both then pull selection-scoped evidence.
  function selectEntity(item) {
    selection = { entity: item.id };
    inspect("entity", item);
    setFilter("actor", [item.id]);
    updateUrl({ actor: [item.id] }, { push: true });
    loadEvidence();
    renderScene(activeScene?.id);
  }
  function selectEvent(item) {
    selection = { episode: item.episode, entity: item.actor };
    inspect("event", item);
    loadEvidence();
  }
  function selectInterval(episode, { push = true, extra = {} } = {}) {
    selection = { episode };
    inspect("event", { id: `interval:${episode}`, label: `Episode ${episode}`, ...extra });
    setFilter("start", [episode]);
    setFilter("end", [episode]);
    updateUrl({ start: episode, end: episode }, { push });
    loadEvidence();
    renderScene(activeScene?.id);
  }
  function setFilter(key, values) {
    const input = document.querySelector(`[data-query="${key}"]`);
    if (input) input.value = values.join(", ");
  }

  /* ---- scene renderers ---- */
  async function renderSeries() {
    const data = await getSceneData("series");
    const width = 960, height = 430, pad = 48;
    const max = Math.max(1, ...data.series.flatMap((item) => item.values));
    const step = data.episodes.length > 1 ? (width - pad * 2) / (data.episodes.length - 1) : 0;
    const chart = svg("svg", { viewBox: `0 0 ${width} ${height}`, role: "img" });
    chart.setAttribute("aria-label", "Events by episode");
    chart.classList.add("pulse-chart");
    for (let line = 0; line <= 4; line++) {
      const y = pad + ((height - pad * 2) * line) / 4;
      chart.append(svg("line", { x1: pad, y1: y, x2: width - pad, y2: y, class: "chart-grid" }));
    }
    const coord = (value, i) => [pad + i * step, height - pad - (value / max) * (height - pad * 2)];
    data.series.forEach((series, index) => {
      const points = series.values.map((value, i) => coord(value, i).join(","));
      chart.append(svg("polyline", { points: points.join(" "), class: `pulse-line tone-${index % 6}` }));
      series.values.forEach((value, i) => {
        const [cx, cy] = coord(value, i);
        const point = svg("circle", { cx, cy, r: 5, class: `pulse-point tone-${index % 6}`, tabindex: 0 });
        point.setAttribute("role", "button");
        point.setAttribute("aria-label", `${series.label}: ${value} at episode ${data.episodes[i]}`);
        const pick = () => selectInterval(data.episodes[i], { extra: { value, series: series.label } });
        point.addEventListener("click", pick);
        point.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") { event.preventDefault(); pick(); }
        });
        chart.append(point);
      });
    });
    const legend = document.createElement("div");
    legend.className = "scene-legend";
    data.series.forEach((item, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.innerHTML = `<i class="tone-${index % 6}"></i>`;
      button.append(document.createTextNode(item.label));
      button.onclick = () => { setFilter("label", [item.label]); applyFilterInputs(); };
      legend.append(button);
    });
    sceneHost.replaceChildren(chart, seriesSummary(data), legend);
    sceneCount.textContent = `${data.series.length} signals`;
  }
  // A screen-reader- and no-JS-friendly text equivalent of the chart (step 8).
  function seriesSummary(data) {
    const totals = data.series
      .map((item) => `${item.label}: ${item.values.reduce((a, b) => a + b, 0)}`)
      .join("; ");
    const figure = document.createElement("p");
    figure.className = "visually-hidden";
    figure.textContent = `Event totals across ${data.episodes.length} episodes — ${totals}.`;
    return figure;
  }
  async function renderEvents(cursor = null, append = false) {
    const data = await getSceneData("events", cursor ? { cursor } : {});
    const list = append ? sceneHost.querySelector(".event-stream") : document.createElement("div");
    if (!append) { list.className = "event-stream"; sceneHost.replaceChildren(list); }
    for (const item of data.items) {
      const button = document.createElement("button");
      const head = document.createElement("span");
      const body = document.createElement("span");
      const episode = document.createElement("b");
      button.type = "button";
      button.className = "event-frame";
      episode.textContent = `E${item.episode}`;
      head.append(episode, document.createTextNode(item.label));
      body.textContent = [item.actor, item.backend_type, ...(item.tags || [])].filter(Boolean).join(" · ");
      button.append(head, body);
      button.onclick = () => selectEvent(item);
      list.append(button);
    }
    sceneHost.querySelector("[data-load-more]")?.remove();
    if (data.next_cursor) {
      const more = document.createElement("button");
      more.type = "button";
      more.className = "button quiet load-more";
      more.dataset.loadMore = "";
      more.textContent = "Load more";
      more.onclick = () => renderEvents(data.next_cursor, true);
      sceneHost.append(more);
    }
    sceneCount.textContent = `${list.children.length} events`;
    if (!list.children.length) empty("No matching events", "Widen the time range or clear a filter.");
  }
  async function renderEntities() {
    const data = await getSceneData("entities", { limit: 200 });
    const field = document.createElement("div");
    field.className = "entity-field";
    data.items.forEach((item, index) => {
      const button = document.createElement("button");
      const size = Math.min(88, 46 + Math.sqrt(item.event_count) * 9);
      button.type = "button";
      button.className = `entity-node tone-${index % 6}`;
      button.style.setProperty("--entity-size", `${size}px`);
      button.style.setProperty("--entity-delay", `${index * -0.18}s`);
      const name = document.createElement("b");
      const count = document.createElement("span");
      name.textContent = item.name;
      count.textContent = `${item.event_count} events`;
      button.append(name, count);
      button.onclick = () => selectEntity(item);
      field.append(button);
    });
    sceneHost.replaceChildren(field);
    sceneCount.textContent = `${data.total} entities`;
    if (!data.items.length) empty("No matching entities", "This selection contains no attributed events.");
  }
  async function awaitViewer(statusUrl) {
    for (let attempt = 0; attempt < 180; attempt++) {
      const status = await (await fetch(statusUrl)).json();
      if (status.state === "ready") return status.url;
      if (status.state === "failed") throw new Error(status.detail);
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
    throw new Error("Viewer start timed out");
  }
  async function renderViewer() {
    const choices = capabilities.viewer_backends || [];
    if (!choices.length) { empty("No spatial scene", "This run did not declare a viewer capability."); return; }
    const toolbar = document.createElement("div");
    const frame = document.createElement("iframe");
    toolbar.className = "viewer-choices segmented";
    frame.className = "explore-viewer";
    frame.title = "Environment scene";
    sceneHost.replaceChildren(toolbar, frame);
    async function open(backend) {
      setBusy(true);
      const response = await fetch(`/api/viewers/${runSegments}/${encodeURIComponent(backend)}`, { method: "POST" });
      if (!response.ok) throw new Error(await response.text());
      const result = await response.json();
      frame.src = result.mode === "embedded" ? result.url : await awaitViewer(result.status_url);
      setBusy(false);
    }
    for (const backend of choices) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = backend;
      button.onclick = () => open(backend).catch(showError);
      toolbar.append(button);
    }
    await open(choices[0]);
    sceneCount.textContent = `${choices.length} scenes`;
  }
  async function renderExtension(scene) {
    if (!scene.renderer.startsWith("/")) {
      empty("Renderer unavailable", `Scene ${scene.title} is registered, but its renderer is not an API path.`);
      return;
    }
    const response = await fetch(`${scene.renderer}?${queryParams()}`);
    const data = await response.json();
    if (data.output) await renderPanel(sceneHost, data.output);
    else { const pre = document.createElement("pre"); pre.textContent = JSON.stringify(data, null, 2); sceneHost.replaceChildren(pre); }
  }
  const RENDERERS = { series: renderSeries, events: () => renderEvents(), entities: renderEntities, viewer: renderViewer };

  async function renderScene(id) {
    const scene = sceneById(id) || capabilities.scenes[0];
    if (!scene) { empty("Nothing to explore", "This artifact advertises no compatible scenes."); return; }
    activeScene = scene;
    document.querySelectorAll("[data-scene]").forEach((button) => {
      const on = button.dataset.scene === scene.id;
      button.classList.toggle("active", on);
      button.setAttribute("aria-pressed", String(on));
    });
    sceneTitle.textContent = scene.title;
    sceneKicker.textContent = `${scene.lens} lens`;
    sceneCount.textContent = "";
    setBusy(true);
    updateUrl({ lens: scene.lens, scene: scene.id });
    try {
      await (RENDERERS[scene.renderer] || (() => renderExtension(scene)))();
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  /* ---- evidence rail (selection-scoped, deep-linked) ---- */
  async function loadEvidence() {
    if (!evidenceHost) return;
    const query = queryParams();
    if (selection?.entity) query.set("entity", selection.entity);
    if (selection?.episode != null) query.set("episode", selection.episode);
    let payload;
    try {
      payload = await (await fetch(`${apiPath("evidence")}?${query}`)).json();
    } catch {
      return;
    }
    evidenceHost.replaceChildren(...payload.items.map(evidenceItem));
    if (!payload.items.length) evidenceHost.replaceChildren(note("No supporting evidence for this selection."));
  }
  function evidenceItem(item) {
    const row = document.createElement("div");
    row.className = `evidence-item kind-${item.kind}`;
    const mark = document.createElement("span");
    mark.className = item.kind === "health" ? "health-pip error" : "signal-mark";
    const body = document.createElement("span");
    const title = document.createElement("b");
    const detail = document.createElement("small");
    title.textContent = item.title;
    detail.textContent = item.detail;
    body.append(title, detail);
    if (item.refs?.length) {
      const refs = document.createElement("span");
      refs.className = "evidence-refs";
      for (const ref of item.refs) {
        const link = document.createElement("button");
        link.type = "button";
        link.className = "chip";
        link.textContent = ref.label;
        link.onclick = () => applyRef(ref.params);
        refs.append(link);
      }
      body.append(refs);
    }
    row.append(mark, body);
    return row;
  }
  function note(text) {
    const box = document.createElement("div");
    box.className = "empty compact";
    const copy = document.createElement("p");
    copy.textContent = text;
    box.append(copy);
    return box;
  }

  /* ---- run story (deterministic, evidence-linked) ---- */
  async function loadStory() {
    if (!storyHost || !capabilities.evidence.has_story) return;
    let payload;
    try {
      payload = await (await fetch(apiPath("story"))).json();
    } catch {
      return;
    }
    storyHost.replaceChildren(...payload.beats.map(storyBeat));
    if (!payload.beats.length) storyHost.replaceChildren(note("No story beats yet."));
  }
  function storyBeat(beat) {
    const row = document.createElement("article");
    row.className = `story-beat tone-${beat.tone}`;
    const title = document.createElement("b");
    const detail = document.createElement("p");
    title.textContent = beat.title;
    detail.textContent = beat.detail;
    row.append(title, detail);
    for (const ref of beat.refs || []) {
      const link = document.createElement("button");
      link.type = "button";
      link.className = "chip";
      link.textContent = ref.label;
      link.onclick = () => applyRef(ref.params);
      row.append(link);
    }
    return row;
  }
  // A deep-link: apply an evidence/story ref's params to the shared state.
  function applyRef(params) {
    for (const [key, value] of Object.entries(params)) setFilter(key, Array.isArray(value) ? value : [value]);
    updateUrl(params, { push: true });
    const actor = Array.isArray(params.actor) ? params.actor[0] : params.actor;
    if (actor) selection = { entity: actor };
    else if (params.start != null) selection = { episode: params.start };
    else selection = null;
    loadEvidence();
    renderScene(activeScene?.id);
  }

  /* ---- playback transport ---- */
  function setupTransport() {
    if (!transport || episodes <= 1) { transport?.remove(); return; }
    const scrub = transport.querySelector("[data-transport-scrub]");
    const label = transport.querySelector("[data-transport-label]");
    const play = transport.querySelector("[data-transport-play]");
    scrub.max = String(episodes - 1);
    // Playback drives the shared window with replaceState (not push), so scrubbing
    // and auto-play don't flood history — only a deliberate chart/entity click pushes.
    const seek = (episode) => {
      const clamped = Math.max(0, Math.min(episodes - 1, episode));
      scrub.value = String(clamped);
      label.textContent = `Episode ${clamped}`;
      selectInterval(clamped, { push: false });
    };
    // While dragging, only move the label; commit (fetch + re-render) on release.
    scrub.addEventListener("input", () => (label.textContent = `Episode ${scrub.value}`));
    scrub.addEventListener("change", () => seek(Number(scrub.value)));
    transport.querySelector("[data-transport-prev]").onclick = () => seek(Number(scrub.value) - 1);
    transport.querySelector("[data-transport-next]").onclick = () => seek(Number(scrub.value) + 1);
    if (reducedMotion) {
      play.disabled = true;
      play.title = "Auto-play disabled by your reduced-motion preference";
    } else {
      play.onclick = () => {
        if (playTimer) { clearInterval(playTimer); playTimer = null; play.dataset.playing = ""; play.textContent = "▶"; return; }
        play.dataset.playing = "on";
        play.textContent = "⏸";
        playTimer = setInterval(() => {
          const next = Number(scrub.value) + 1;
          if (next >= episodes) { play.click(); return; }
          seek(next);
        }, 1100);
      };
    }
  }

  /* ---- live convergence (same scenes for a running artifact) ---- */
  function setupLive() {
    const terminal = (status) => ["success", "failed", "error"].includes(status);
    if (terminal(capabilities.artifact.status) || !liveBadge) { liveBadge?.remove(); return; }
    liveBadge.hidden = false;
    let fingerprint = capabilities.artifact.fingerprint;
    // Poll the (cheap, log-free) capability document. Re-render only when the
    // artifact fingerprint moves — i.e. new events actually landed — so a live
    // view neither resets the scene nor aborts a user action on every tick.
    const timer = setInterval(async () => {
      let latest;
      try {
        latest = await (await fetch(apiPath("capabilities"))).json();
      } catch {
        return; // transient read while the run writes; try again next tick
      }
      if (latest.artifact.fingerprint !== fingerprint) {
        fingerprint = latest.artifact.fingerprint;
        renderScene(activeScene?.id);
        loadEvidence();
      }
      if (terminal(latest.artifact.status)) {
        clearInterval(timer);
        liveBadge.hidden = true;
      }
    }, 3000);
  }

  /* ---- filters, lenses, wiring ---- */
  function applyFilterInputs() {
    const values = {};
    document.querySelectorAll("[data-query]").forEach((input) => {
      values[input.dataset.query] = input.value.split(",").map((value) => value.trim()).filter(Boolean);
    });
    // Editing the scope is a filter change, not a selection: drop any stale
    // entity/episode focus so the evidence rail matches the new inputs.
    selection = null;
    updateUrl(values);
    renderScene(activeScene?.id);
    loadEvidence();
  }
  let filterTimer;
  document.querySelectorAll("[data-query]").forEach((input) =>
    input.addEventListener("input", () => {
      clearTimeout(filterTimer);
      filterTimer = setTimeout(applyFilterInputs, 250);
    })
  );
  $("[data-clear-scope]").onclick = () => {
    document.querySelectorAll("[data-query]").forEach((input) => (input.value = ""));
    selection = null;
    updateUrl({ start: "", end: "", actor: "", label: "", tag: "", backend_type: "", entity: "", event: "" }, { push: true });
    inspector.replaceChildren(note("Select an event, entity, or point in the scene."));
    renderScene(activeScene?.id);
    loadEvidence();
  };

  const lensButtons = [...document.querySelectorAll("[data-scene]")];
  lensButtons.forEach((button, index) => {
    button.addEventListener("click", () => renderScene(button.dataset.scene));
    // Keyboard-operable lens strip: arrow keys move focus and activate (step 8).
    button.addEventListener("keydown", (event) => {
      const delta = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
      if (delta == null) return;
      event.preventDefault();
      const next = lensButtons[(index + delta + lensButtons.length) % lensButtons.length];
      next.focus();
      renderScene(next.dataset.scene);
    });
  });
  addEventListener("popstate", () => location.reload());

  capabilities.scenes.forEach((scene) =>
    registerPaletteCommand({
      label: `Explore: ${scene.title}`,
      href: `?lens=${encodeURIComponent(scene.lens)}&scene=${encodeURIComponent(scene.id)}`,
      keywords: `${scene.renderer} ${(scene.selection_kinds || []).join(" ")}`,
    })
  );

  /* ---- boot ---- */
  const params = new URL(location).searchParams;
  const initial =
    params.get("scene") ||
    capabilities.scenes.find((scene) => scene.lens === params.get("lens"))?.id ||
    capabilities.scenes[0]?.id;
  setupTransport();
  renderScene(initial);
  loadEvidence();
  loadStory();
  setupLive();
}
