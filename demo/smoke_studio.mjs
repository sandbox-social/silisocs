// Browser smoke for Silisocs Studio: boot every page a first-time user touches
// and fail on ANY JavaScript error along the way.
//
//   STUDIO_URL=http://127.0.0.1:8765 CHROME=/path/to/chromium \
//   SMOKE_SCENARIO=misinformation node smoke_studio.mjs
//
// This is the browser half of `tests/e2e/test_studio_browser_smoke.py` — that test
// owns the server (offline scripted provider, temp workspace, random port) and
// shells out to this script so the smoke can reuse demo's pinned
// `playwright-core` without adding a Python browser dependency.
//
// The flow (each step re-checked for console errors before moving on):
//   home -> scenarios -> scenario editor -> preflight+launch (UI button)
//   -> live page -> Play -> finished run -> overview / watch / analyze tabs.
//
// Exit code 0 = clean. Anything else prints the failing step and the collected
// browser errors.
import { chromium } from "playwright-core";

const STUDIO = process.env.STUDIO_URL ?? "http://127.0.0.1:8765";
const SCENARIO = process.env.SMOKE_SCENARIO ?? "misinformation";
const EXECUTABLE = process.env.CHROME;
const HEADED = process.env.SMOKE_HEADED === "1";
const SELECTOR_TIMEOUT = Number(process.env.SMOKE_SELECTOR_TIMEOUT ?? 30_000);
const JOB_TIMEOUT = Number(process.env.SMOKE_JOB_TIMEOUT ?? 180_000);

if (!EXECUTABLE) {
  console.error("[smoke] CHROME is not set — no browser to drive.");
  process.exit(2);
}

// Two classes of console noise are not JavaScript problems and would make the
// smoke flaky, so they are excluded by pattern (everything else fails the run):
//   - /favicon.ico: Studio ships no favicon, so Chromium logs a 404 per page.
//     Also stubbed by a route below; the pattern is the belt to that braces.
//   - net::ERR_ABORTED: a navigation cancels the previous page's in-flight
//     requests (the SSE job stream, most of all). That is the navigation
//     working, not the page failing.
const IGNORED = [/favicon\.ico/, /net::ERR_ABORTED/];

const t0 = Date.now();
const since = () => `${((Date.now() - t0) / 1000).toFixed(1)}s`;
const errors = [];
let stepIndex = 0;

function log(message) {
  console.log(`[smoke ${since()}] ${message}`);
}

const browser = await chromium.launch({
  executablePath: EXECUTABLE,
  headless: !HEADED,
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});
const context = await browser.newContext({ viewport: { width: 1360, height: 900 } });
const page = await context.newPage();
page.setDefaultTimeout(SELECTOR_TIMEOUT);

await page.route("**/favicon.ico", route => route.fulfill({ status: 204, body: "" }));

const record = (kind, text) => {
  if (IGNORED.some(pattern => pattern.test(text))) return;
  errors.push({ kind, url: page.url(), text });
};
page.on("console", message => {
  if (message.type() === "error") record("console.error", message.text());
});
page.on("pageerror", error => record("pageerror", `${error.message}`));

/** Run one named step, then assert the browser stayed error-free. */
async function step(name, body) {
  stepIndex += 1;
  const before = errors.length;
  log(`${stepIndex}. ${name} …`);
  const result = await body();
  const fresh = errors.slice(before);
  if (fresh.length) {
    throw new Error(
      `${name}: ${fresh.length} browser error(s)\n` +
        fresh.map(item => `  [${item.kind}] ${item.url}\n    ${item.text}`).join("\n")
    );
  }
  log(`${stepIndex}. ${name} ok`);
  return result;
}

/** Poll the job API from Node — a page-side async predicate is not awaited.
 *  Returns the matching job, or null on timeout; a dead job raises. */
async function pollJob(jobId, predicate, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const job = await (await fetch(`${STUDIO}/api/jobs/${jobId}`)).json();
    if (predicate(job)) return job;
    if (["failed", "killed", "orphaned"].includes(job.status)) {
      throw new Error(`job ${jobId} is ${job.status} (exit ${job.exit_code})`);
    }
    if (Date.now() >= deadline) return null;
    await new Promise(resolve => setTimeout(resolve, 250));
  }
}

async function waitForJob(jobId, predicate, timeoutMs) {
  const job = await pollJob(jobId, predicate, timeoutMs);
  if (!job) throw new Error(`job ${jobId} never satisfied the wait within ${timeoutMs}ms`);
  return job;
}

/** Press Play until the held run actually advances.
 *
 *  An interactive run starts paused, and the runner DELETES its control file
 *  when its controller comes up (stale-command protection — see
 *  ControlFileController.start). Studio marks the job "running" at process
 *  spawn, well before that, so the first Play can legitimately be discarded.
 *  Re-pressing is idempotent ({"target": null} = run freely), so the loop just
 *  presses again until the run finishes. */
async function playUntilFinished(jobId) {
  const deadline = Date.now() + JOB_TIMEOUT;
  let presses = 0;
  do {
    await page.click('[data-testid="ctl-play"]');
    presses += 1;
    const job = await pollJob(jobId, item => item.status === "finished", 4000);
    if (job) {
      log(`   released after ${presses} Play press(es)`);
      return job;
    }
  } while (Date.now() < deadline);
  throw new Error(`job ${jobId} never finished after ${presses} Play presses`);
}

async function main() {
  await step("home page boots", async () => {
    await page.goto(`${STUDIO}/`, { waitUntil: "load" });
    await page.waitForSelector("#main-content");
    // The warming screen is a different document: if it is still up, the
    // server was not ready and every later assertion would be misleading.
    if (await page.locator('[data-testid="studio-warming"]').count()) {
      throw new Error("still on the warming screen — /api/ready lied");
    }
    // studio.js replaces boot.js's palette stub; its dialog proves the shell ran.
    await page.waitForSelector('[data-testid="palette"]', { state: "attached" });
  });

  await step("scenarios list renders", async () => {
    // The sidebar and the command palette both link to /scenarios.
    await page.locator('a[href="/scenarios"]').first().click();
    await page.waitForURL(url => url.pathname === "/scenarios");
    await page.waitForSelector(`a[href^="/scenarios/${SCENARIO}"]`);
  });

  await step("scenario editor renders", async () => {
    await page.click(`a[href^="/scenarios/${SCENARIO}"]`);
    await page.waitForURL(url => url.pathname === `/scenarios/${SCENARIO}`);
    await page.waitForSelector('[data-testid="preflight"]', { state: "visible" });
    await page.waitForSelector('[data-testid="save-scenario"]', { state: "visible" });
    await page.waitForSelector('[data-testid="launch-scenario"]', { state: "visible" });
    // The composer hydrates asynchronously (deferred choices, run history);
    // wait for the history fetch to settle so it cannot land mid-launch. Its
    // placeholder either drops aria-busy or is replaced outright, so the
    // busy node being gone covers both outcomes.
    await page.waitForSelector("[data-scenario-history][aria-busy]", { state: "detached" });
  });

  await step("preflight estimates the run", async () => {
    await page.click('[data-testid="preflight"]');
    await page.waitForSelector("#preflight .stats-band");
  });

  const jobId = await step("UI launch starts an offline run", async () => {
    await page.selectOption('[data-testid="launch-mode"]', "interactive");
    await page.click('[data-testid="launch-scenario"]');
    await page.waitForURL(url => url.pathname === "/live" && url.searchParams.has("job"));
    return new URL(page.url()).searchParams.get("job");
  });

  await step("live page shows job status and controls", async () => {
    await page.waitForSelector("#job-status");
    await page.waitForSelector('[data-testid="run-control"]');
    for (const control of ["ctl-step", "ctl-play", "ctl-pause", "ctl-end"]) {
      await page.waitForSelector(`[data-testid="${control}"]`, { state: "visible" });
    }
    await waitForJob(jobId, job => job.status === "running", JOB_TIMEOUT);
  });

  const runId = await step("Play releases the run and it is indexed", async () => {
    const job = await playUntilFinished(jobId);
    // The run index is served from a short-TTL cache, so a just-finished run
    // can still be listed as "running" for a beat. Poll instead of guessing.
    const deadline = Date.now() + 30_000;
    let seen = null;
    for (;;) {
      const runs = await (await fetch(`${STUDIO}/api/runs`)).json();
      seen = runs.items.find(item => item.path === job.output_dir) ?? null;
      if (seen?.status === "success") return seen.id;
      if (Date.now() >= deadline) break;
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    throw new Error(
      seen ? `run settled as ${seen.status}, not success` : `run ${job.output_dir} was never indexed`
    );
  });

  const runUrl = `${STUDIO}/runs/${runId.split("/").map(encodeURIComponent).join("/")}`;

  await step("run overview tab renders", async () => {
    await page.goto(`${runUrl}?tab=overview`, { waitUntil: "load" });
    await page.waitForSelector('[data-testid="run-tabs"]');
    await page.waitForSelector('[data-testid="run-status"]');
    if (await page.locator('[data-testid="run-error"]').count()) {
      throw new Error("the run page reports a failed run");
    }
  });

  await step("run watch tab renders", async () => {
    await page.click('[data-testid="tab-watch"]');
    await page.waitForURL(url => url.searchParams.get("tab") === "watch");
    await page.waitForSelector('[data-testid="run-tabs"]');
    await page.waitForSelector(".panel-grid");
  });

  await step("run analyze tab renders a panel", async () => {
    await page.click('[data-testid="tab-analyze"]');
    await page.waitForURL(url => url.searchParams.get("tab") === "analyze");
    await page.waitForSelector('[data-testid="run-tabs"]');
    await page.waitForSelector(".panel-grid section.panel[data-panel]");
    const panels = await page.locator(".panel-grid section.panel[data-panel]").count();
    if (panels < 1) throw new Error("the analysis view rendered no panel container");
    log(`   analysis panels: ${panels}`);
  });
}

let failure = null;
try {
  await main();
} catch (error) {
  failure = error;
}
await context.close();
await browser.close();

if (failure) {
  console.error(`[smoke] FAILED: ${failure.message}`);
  if (errors.length) {
    console.error("[smoke] browser errors collected:");
    for (const item of errors) console.error(`  [${item.kind}] ${item.url}\n    ${item.text}`);
  }
  process.exit(1);
}
log(`all ${stepIndex} steps clean — zero console errors`);
