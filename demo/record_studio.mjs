// Record the Studio tour video against a live silisocs-studio server.
//
//   STUDIO_URL=http://127.0.0.1:8765 HERO_RUN=misinformation/hero_run \
//   STUDY_ID=misinformation_cta_demo node record_studio.mjs
//
// Expects: the server already warm, the hero run + demo study already
// completed under the server's roots. Launches ONE real interactive run
// through the scenario editor UI (Step / Step / Play) as part of the tour.
//
// Output: build/studio.webm + build/studio.segments.json (wait spans that
// assemble.sh time-compresses).
import { mkdirSync, renameSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { Segments, caption, recordingContext } from "./lib.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const build = path.join(here, "build");
mkdirSync(build, { recursive: true });

const STUDIO = process.env.STUDIO_URL ?? "http://127.0.0.1:8765";
const HERO = process.env.HERO_RUN ?? "misinformation/hero_run";
const STUDY = process.env.STUDY_ID ?? "misinformation_cta_demo";

const { browser, context } = await recordingContext(build);
const page = await context.newPage();
const segments = new Segments();

async function go(url, name, { settle = 1200 } = {}) {
  segments.mark(name);
  await page.goto(`${STUDIO}${url}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(settle);
}

async function scrollTour(steps = 3, distance = 420, pause = 1400) {
  for (let i = 0; i < steps; i += 1) {
    await page.mouse.wheel(0, distance);
    await page.waitForTimeout(pause);
  }
}

// Poll the job API from Node (page.waitForFunction does not reliably await an
// async predicate — a returned Promise is truthy, which resolves the wait
// immediately).
async function waitForJob(jobId, predicate, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const job = await (await fetch(`${STUDIO}/api/jobs/${jobId}`)).json();
    if (predicate(job)) return job;
    await new Promise((resolve) => setTimeout(resolve, 2000));
  }
  throw new Error(`timed out waiting on job ${jobId}`);
}

// ---- 1. Home -----------------------------------------------------------------
await go("/", "home");
await caption(page, "Silisocs Studio — scenarios, live runs, platform inspection, and studies in one workspace.", 4200);

// ---- 2. Scenario editor ------------------------------------------------------
await go("/scenarios", "scenarios", { settle: 900 });
await caption(page, "Every scenario is plain YAML on disk — Studio edits it in place.", 3200);
await go("/scenarios/misinformation", "editor");
await caption(page, "The misinformation scenario: six agents, a false health claim, a hand-drawn follow graph.", 4000);
await scrollTour(2);
await page.mouse.wheel(0, -2000);
await page.waitForTimeout(600);

// ---- 3. Preflight ------------------------------------------------------------
await caption(page, "Preflight validates the config and estimates scale before anything runs.", 1500);
await page.click('button:has-text("Preflight")');
await page.waitForSelector("#preflight .stats-band", { timeout: 30_000 });
await page.waitForTimeout(3200);

// ---- 4. Interactive launch ---------------------------------------------------
await caption(page, "Launch interactively: the run starts paused, under your control.", 2000);
await page.selectOption("#launch-mode", "interactive");
segments.mark("launch");
await page.click('button:has-text("Launch")');
await page.waitForURL("**/live**", { timeout: 60_000 });
await page.waitForSelector("#control-state", { timeout: 60_000 });
const launchedJob = new URL(page.url()).searchParams.get("job");
// The queue may hold the job briefly (max_concurrent_runs); step only once the
// runner process is actually up, or the step-wait below times out unfairly.
await waitForJob(launchedJob, (job) => job.status === "running", 120_000);
await caption(page, "A real gpt-4o-mini run, held before episode 0.", 2500);

segments.mark("step-wait-1", { wait: true });
await caption(page, "Step advances exactly one episode…", 800);
await page.click('button:has-text("Step")');
await page.waitForFunction(
  () => document.getElementById("control-state")?.textContent?.includes("1 done"),
  null, { timeout: 300_000 }
);
segments.mark("step-2");
await page.waitForTimeout(1500);
segments.mark("step-wait-2", { wait: true });
await caption(page, "…the log and artifact counters follow along.", 800);
await page.click('button:has-text("Step")');
await page.waitForFunction(
  () => document.getElementById("control-state")?.textContent?.includes("2 done"),
  null, { timeout: 300_000 }
);
segments.mark("play");
await page.waitForTimeout(1200);
await caption(page, "Play lets it run to completion while we look around.", 2200);
await page.click('button:has-text("Play")');
await page.waitForTimeout(2500);

// ---- 5. Completed run: watch ribbon ------------------------------------------
await go(`/runs/${HERO}?tab=watch&view=overview`, "hero-watch");
await caption(page, "A completed run: status, episodes, actions, and elapsed time at a glance.", 4000);

// ---- 6. Platform viewer ------------------------------------------------------
await go(`/runs/${HERO}?tab=platform`, "platform", { settle: 800 });
await page.click(".segmented button");
await page.waitForSelector("#platform-frame:not(.hidden)", { timeout: 60_000 });
await page.waitForTimeout(1500);
await caption(page, "The platform tab opens the run's social feed — the world as the agents saw it.", 4500);
await scrollTour(2, 380);

// ---- 7. Analysis view --------------------------------------------------------
await go(`/runs/${HERO}?tab=analyze&view=spread`, "analyze", { settle: 2500 });
await caption(page, "Analysis views ship with the scenario: this one tracks how the claim spread.", 3800);
await scrollTour(6, 420, 2000);

// ---- 8. Explore --------------------------------------------------------------
await go(`/explore/run/${HERO}`, "explore", { settle: 2200 });
await caption(page, "Explore cross-filters the same run: time, actors, labels, and events.", 4200);

// ---- 9. Study ----------------------------------------------------------------
await go(`/studies/${STUDY}?tab=board`, "study-board", { settle: 1800 });
await caption(page, "Studies fan one scenario across conditions and seeds — here, two CTA framings × two seeds.", 4500);
await go(`/studies/${STUDY}?tab=compare`, "study-compare", { settle: 2500 });
await caption(page, "Replicates aggregate automatically: compare conditions with per-metric statistics.", 4500);
await scrollTour(2, 420);

// ---- 10. The launched run finished meanwhile ---------------------------------
await go("/runs", "runs-list", { settle: 1000 });
await caption(page, "Meanwhile, the run we launched has been playing on…", 1500);
segments.mark("finish-wait", { wait: true });
await waitForJob(launchedJob, (job) => job.status === "finished", 480_000);
await page.waitForTimeout(1500); // let the final manifest land on disk
segments.mark("outro");
await page.reload({ waitUntil: "networkidle" });
await caption(page, '…and is already on the board. Get started: pip install "silisocs[studio]", then silisocs-studio.', 5000);

segments.mark("end");
const video = page.video();
await context.close();
await browser.close();
const recorded = await video.path();
renameSync(recorded, path.join(build, "studio.webm"));
writeFileSync(path.join(build, "studio.segments.json"), JSON.stringify(segments.marks, null, 2));
console.log("wrote build/studio.webm + build/studio.segments.json");
