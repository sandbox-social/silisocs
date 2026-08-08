/* Run surfaces: the run archive, the run detail page (platform viewer, process
 * log, Watch ribbon) and the live job page — plus the interactive run-control
 * bar the last two share.
 *
 * They live together because they are one story (launch -> watch -> inspect)
 * and share the log renderer, the SSE plumbing and the control bar. Every URL
 * this file calls comes from the page's data island: the server owns route
 * construction, the client owns behaviour. */

/* ---- run archive: pick two runs, compare them ---------------------------- */
function initRunsArchive() {
  const archive = document.querySelector("[data-archive]");
  if (!archive) return;
  const open = document.querySelector("[data-compare-open]");
  const count = document.querySelector("[data-compare-n]");
  const selected = () => [...archive.querySelectorAll(".run-check:checked")].map(box => box.value);
  archive.addEventListener("change", () => {
    const ids = selected();
    count.textContent = ids.length;
    open.hidden = ids.length < 2;
  });
  open.addEventListener("click", () => {
    const ids = selected();
    if (ids.length >= 2) location.href = "/explore/compare?" + ids.map(id => "runs=" + encodeURIComponent(id)).join("&");
  });
}

/* ---- process log --------------------------------------------------------- */
function logSeverity(line) {
  if (/\b(error|exception|traceback|critical|fatal)\b/i.test(line)) return "error";
  if (/\b(warn|warning)\b/i.test(line)) return "warning";
  if (/\b(debug|trace)\b/i.test(line)) return "debug";
  return "info";
}
// One log view, two hosts: the run page's #run-log and the live page's #live-log.
function logView(rootId, followId) {
  const root = document.getElementById(rootId);
  const follow = () => document.getElementById(followId)?.checked;
  return {
    root,
    append(line) {
      if (!root) return;
      root.querySelector(".muted")?.remove();
      const row = document.createElement("span");
      row.className = `log-${logSeverity(line)}`;
      row.textContent = line;
      root.append(row);
      if (follow()) root.scrollTop = root.scrollHeight;
    },
    // Server-rendered history arrives unclassified; colour it and jump to the end.
    hydrate() {
      if (!root) return;
      root.querySelectorAll("span:not(.muted)").forEach(row => {
        row.className = `log-${logSeverity(row.textContent)}`;
      });
      if (follow()) root.scrollTop = root.scrollHeight;
    },
  };
}

/* ---- interactive run control (Step / Play / Pause / End run) -------------
 * Episode-boundary control. The SSE stream already reports step boundaries;
 * the client tracks them to translate Step/Play/Pause into an absolute target
 * (first episode to hold before) the server writes to the run's control file. */
function initRunControl({url, initialEpisode = -1}, events) {
  let started = initialEpisode;
  let finished = initialEpisode;
  const state = document.getElementById("control-state");
  const done = () => finished + 1;
  const inFlight = () => started > finished;
  const render = () => {
    if (state) state.textContent = inFlight() ? `running episode ${started}` : `paused · ${done()} done`;
  };
  const send = body =>
    fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  window.ctlStep = () => send({target: done() + 1});
  window.ctlPlay = () => send({target: null});
  window.ctlPause = () => send({target: inFlight() ? started + 1 : done()});
  window.ctlStop = () => send({stopped: true});
  events.addEventListener("step_started", event => {
    started = Math.max(started, JSON.parse(event.data).step);
    render();
  });
  events.addEventListener("step_finished", event => {
    finished = Math.max(finished, JSON.parse(event.data).step);
    render();
  });
  // A finished job's control file is dead — remove the bar rather than leave
  // clickable controls that silently do nothing.
  events.addEventListener("done", () => document.querySelector(".control-bar")?.remove());
  render();
}

/* ---- run page: platform viewer ------------------------------------------- */
function initPlatformTab(data) {
  const platformEmpty = () => document.getElementById("platform-empty");
  function showViewer(url) {
    const frame = document.getElementById("platform-frame");
    const target = new URL(url, location.origin);
    target.searchParams.set("theme", document.documentElement.dataset.theme);
    platformEmpty().classList.add("hidden");
    frame.src = target;
    frame.classList.remove("hidden");
  }
  function viewerFailed(backend, detail) {
    const empty = platformEmpty();
    empty.classList.remove("skeleton", "loading", "hidden");
    empty.removeAttribute("aria-busy");
    empty.replaceChildren();
    const title = document.createElement("b");
    title.textContent = "Viewer did not start";
    const note = document.createElement("p");
    note.textContent = detail;
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "button";
    retry.textContent = "Retry";
    retry.onclick = () => startViewer(backend);
    empty.append(title, note, retry);
  }
  // A subprocess viewer is ready when its port accepts connections, not when the
  // process spawns: the server binds only after its imports finish. The server
  // owns that distinction; we poll it until ready or the budget runs out.
  async function awaitViewer(backend, statusUrl) {
    const started = Date.now();
    const budgetMs = 180000;
    for (;;) {
      let status;
      try {
        status = await (await fetch(statusUrl)).json();
      } catch {
        status = {state: "starting"};
      }
      if (status.state === "ready") {
        showViewer(status.url);
        return;
      }
      if (status.state === "failed") {
        viewerFailed(backend, status.detail || "The viewer process stopped.");
        return;
      }
      const seconds = Math.floor((Date.now() - started) / 1000);
      if (seconds * 1000 > budgetMs) {
        viewerFailed(backend, `The viewer did not start within ${Math.floor(budgetMs / 1000)}s.`);
        return;
      }
      platformEmpty().replaceChildren(
        Object.assign(document.createElement("b"), {textContent: "Starting viewer"}),
        Object.assign(document.createElement("p"), {
          textContent: `Waiting for the viewer server — ${seconds}s elapsed.`,
        })
      );
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
  }
  window.startViewer = async backend => {
    const empty = platformEmpty();
    empty.classList.remove("hidden");
    empty.classList.add("skeleton", "loading");
    empty.setAttribute("aria-busy", "true");
    const response = await fetch(`${data.viewerApi}/${encodeURIComponent(backend)}`, {method: "POST"});
    if (!response.ok) {
      empty.classList.remove("skeleton", "loading");
      empty.removeAttribute("aria-busy");
      notify(await response.text(), "danger");
      return;
    }
    const result = await response.json();
    // Embedded viewers are mounted inside Studio — already serving, nothing to await.
    if (result.mode === "embedded") {
      showViewer(result.url);
      return;
    }
    await awaitViewer(backend, result.status_url);
  };
  if (data.autoStartViewer) startViewer(data.autoStartViewer);
}

/* ---- run page: Watch ribbon --------------------------------------------- */
function initWatchStream(data, log) {
  const watch = data.watch;
  const events = new EventSource(watch.stream);
  const updateElapsed = () => {
    const seconds = Math.max(0, Math.floor(Date.now() / 1000 - watch.started));
    document.getElementById("watch-elapsed").textContent =
      `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")} elapsed`;
  };
  async function updateUsage() {
    const response = await fetch(data.runApi);
    if (!response.ok) return;
    const usage = (await response.json()).llm_usage || {};
    const tokens = usage.totals?.total_tokens;
    const cost = usage.estimated_cost_usd;
    document.getElementById("watch-usage").textContent =
      cost != null
        ? `$${Number(cost).toFixed(4)} estimated`
        : tokens != null
          ? `${Number(tokens).toLocaleString()} tokens`
          : "Usage pending";
  }
  updateElapsed();
  const clock = setInterval(updateElapsed, 1000);
  events.addEventListener("log_line", event => log.append(JSON.parse(event.data).line || ""));
  events.addEventListener("artifact_grown", event => {
    const payload = JSON.parse(event.data);
    if (payload.stream === "action") {
      const sources = Object.entries(payload.sources || {}).filter(([name]) => name);
      const detail = sources.length > 1 ? ` (${sources.map(([name, count]) => `${name} ${count}`).join(" · ")})` : "";
      document.getElementById("watch-actions").textContent = `${payload.new_count} actions${detail}`;
    }
    // Live placeholders: a panel skipped only because its stream had no rows yet
    // matches on data-requires and refreshes in as soon as that stream grows.
    document.querySelectorAll(`[data-requires*="${payload.stream}_events"]`).forEach(refreshPanel);
    updateUsage();
  });
  events.addEventListener("step_started", event => {
    document.getElementById("watch-step").textContent = `Episode ${JSON.parse(event.data).step} running`;
  });
  events.addEventListener("step_finished", event => {
    document.getElementById("watch-step").textContent = `Episode ${JSON.parse(event.data).step} complete`;
  });
  events.addEventListener("status_changed", event => {
    document.getElementById("watch-status").textContent = JSON.parse(event.data).status;
  });
  events.addEventListener("done", () => {
    clearInterval(clock);
    updateElapsed();
    updateUsage();
    events.close();
  });
  window.stopWatchJob = () => fetch(watch.stopUrl, {method: "POST"});
  return events;
}

function initRunPage(data) {
  if (data.tab === "platform") initPlatformTab(data);
  const log = logView("run-log", "logs-follow");
  if (data.wireLog) log.hydrate();
  let events = null;
  if (data.watch) events = initWatchStream(data, log);
  if (data.control && events) initRunControl(data.control, events);
}

/* ---- live job page ------------------------------------------------------- */
function initLivePage(data) {
  const log = logView("live-log", "live-follow");
  const autoWatch = new URL(location).searchParams.has("autowatch");
  const events = new EventSource(data.stream);
  events.addEventListener("log_line", event => log.append(JSON.parse(event.data).line || ""));
  events.addEventListener("status_changed", event => {
    document.getElementById("job-status").textContent = JSON.parse(event.data).status;
  });
  // The job knows its output directory before the run index does; poll until the
  // artifact is discoverable, then offer (or take) the handoff to the run page.
  async function attachRun() {
    const job = await (await fetch(data.jobApi)).json();
    if (!job.output_dir) return;
    const runs = await (await fetch("/api/runs")).json();
    const run = runs.items.find(item => item.path === job.output_dir);
    if (!run) return;
    const href = "/runs/" + run.id + "?tab=watch&view=overview";
    const link = document.getElementById("run-link");
    link.href = href;
    link.classList.remove("hidden");
    if (autoWatch) location.replace(href);
  }
  events.addEventListener("output_discovered", attachRun);
  events.addEventListener("artifact_grown", attachRun);
  events.addEventListener("done", () => {
    events.close();
    attachRun().then(() => setTimeout(() => location.reload(), 700));
  });
  attachRun();
  window.stopJob = () => fetch(data.stopUrl, {method: "POST"});
  if (data.control) initRunControl(data.control, events);
}

const runsPageData = studioPageData();
initRunsArchive();
if (runsPageData.page === "run") initRunPage(runsPageData);
if (runsPageData.page === "live") initLivePage(runsPageData);
