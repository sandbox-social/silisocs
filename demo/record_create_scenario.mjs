// Record the "Create a scenario in Studio" video against a live silisocs-studio
// server.
//
//   STUDIO_URL=http://127.0.0.1:8797 CHROME=/path/to/chromium \
//   node demo/record_create_scenario.mjs
//
// Expects: the server already warm (poll /api/ready), a real OPENAI_API_KEY in
// the environment the server was started with, and NO scenario named
// `campus_rumor` in the workspace yet — the video creates it.
//
// The tour is the whole authoring loop, nothing staged: New scenario -> name it
// -> the scaffolded YAML tree -> edit world / agents / sim / eval in the editor
// -> Save -> Preflight -> Launch a real gpt-4o-mini run (4 agents x 4 episodes,
// about a cent) -> watch it stream -> the finished run's Overview, platform feed
// and the probe view the scenario just declared.
//
// Output: build/create-scenario.webm + build/create-scenario.segments.json.
//
// The marks are LABELS here, not compression spans, and assemble.py is called
// WITHOUT --segments for this video. Two reasons: this run is short enough
// (about half a minute from Launch to a finished artifact, all of it either
// streaming log or live counters) that there is no LLM wait worth compressing,
// and the composer round trips make the page recorder lag the wall clock by
// enough (10-16 s over a three-minute take — the script prints the residual)
// that a wall-clock span would compress the wrong footage.
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { Segments, caption, recordingContext } from "./lib.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const build = path.join(here, "build");
mkdirSync(build, { recursive: true });

const STUDIO = process.env.STUDIO_URL ?? "http://127.0.0.1:8797";
const NAME = process.env.SCENARIO_NAME ?? "campus_rumor";
const SCENARIO_SOURCE = process.env.SCENARIO_SOURCE
  ?? path.join(here, "..", "scenarios", NAME, "conf");
const sourceYaml = (relative) => readFileSync(path.join(SCENARIO_SOURCE, relative), "utf8");
const WORLD_YAML = sourceYaml("world/default.yaml");
const AGENTS_YAML = sourceYaml("agents/default.yaml");
const SIM_YAML = sourceYaml("sim.yaml");
const EVAL_YAML = sourceYaml("eval.yaml");

const { browser, context } = await recordingContext(build);
const page = await context.newPage();
const segments = new Segments();
page.setDefaultTimeout(60_000);

// Studio ships no favicon; stub it so the recording carries no 404 noise.
await page.route("**/favicon.ico", (route) => route.fulfill({ status: 204, body: "" }));
const browserErrors = [];
page.on("console", (message) => {
  if (message.type() === "error") browserErrors.push(`[console] ${page.url()} :: ${message.text()}`);
});
page.on("pageerror", (error) => browserErrors.push(`[pageerror] ${page.url()} :: ${error.message}`));

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

/** Bring one document of the YAML mirror to the front. */
async function openYaml(relative) {
  await page.locator(".yaml-tabs button", { hasText: relative }).first().click();
  await page.waitForSelector(`textarea[data-file="${relative}"]`, { state: "visible" });
  await page.waitForTimeout(600);
}

/** Write a document into the YAML mirror as if it were being typed.
 *
 *  One page.evaluate rather than a keystroke stream: Playwright's per-key round
 *  trip would take minutes on a 3 kB document, and the change event the composer
 *  listens for is dispatched explicitly at the end either way.
 *
 *  `delay` is deliberately coarse (a handful of repaints per second, not one per
 *  character): the page recorder is a screencast, and a textarea repainting at
 *  30 ms falls behind, which slides every later segment mark off the video clock
 *  that assemble.py compresses against. */
async function writeYaml(relative, text, { durationMs = 3000, delay = 220 } = {}) {
  const selector = `textarea[data-file="${relative}"]`;
  const chunk = Math.max(1, Math.ceil(text.length / Math.max(1, Math.round(durationMs / delay))));
  await page.evaluate(
    async ({ target, body, size, tick }) => {
      const editor = document.querySelector(target);
      editor.value = "";
      editor.focus();
      for (let index = 0; index < body.length; index += size) {
        editor.value = body.slice(0, index + size);
        editor.scrollTop = editor.scrollHeight;
        editor.dispatchEvent(new Event("input", { bubbles: true }));
        await new Promise((resolve) => setTimeout(resolve, tick));
      }
      editor.scrollTop = 0;
      editor.dispatchEvent(new Event("change", { bubbles: true }));
    },
    { target: selector, body: text, size: chunk, tick: delay }
  );
  await page.waitForTimeout(900);
}

/** Set one composer form field and let it compose back into the YAML mirror. */
async function setField(key, value, { select = false } = {}) {
  const selector = `[data-field="${key}"]`;
  if (select) await page.selectOption(selector, value);
  else await page.fill(selector, String(value));
  await page.dispatchEvent(selector, "change");
  await page.waitForTimeout(1200);
}

async function jobIds() {
  const data = await (await fetch(`${STUDIO}/api/jobs`)).json();
  return new Set((data.items ?? []).map((item) => item.id));
}

async function waitForJob(jobId, predicate, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const job = await (await fetch(`${STUDIO}/api/jobs/${jobId}`)).json();
    if (predicate(job)) return job;
    if (["failed", "killed", "orphaned"].includes(job.status)) {
      throw new Error(`job ${jobId} is ${job.status} (exit ${job.exit_code})`);
    }
    if (Date.now() >= deadline) throw new Error(`timed out waiting on job ${jobId}`);
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
}

// ---- 1. Home -----------------------------------------------------------------
await go("/", "home");
await caption(page, "Every simulation starts as a scenario. Let's build one from an empty page.", 5000);

// ---- 2. The library, and a brand-new scenario --------------------------------
await go("/scenarios", "scenarios", { settle: 1000 });
await caption(page, "The library is a folder of scenarios — each one a launchable world, not a preset.", 4500);
await page.click('button:has-text("New scenario")');
await page.waitForSelector("#new-scenario[open]");
await page.waitForTimeout(900);
await caption(page, "Name it, and Studio scaffolds the whole config tree for you.", 2600);
await page.fill('#new-scenario input[name="name"]', "");
await page.type('#new-scenario input[name="name"]', NAME, { delay: 110 });
await page.waitForTimeout(900);
segments.mark("scaffold");
await page.click('#new-scenario button[type="submit"]');
await page.waitForSelector('[data-testid="save-scenario"]', { state: "visible" });
await page.waitForTimeout(1600);
await caption(
  page,
  "Five YAML documents on disk: the world, its agents, the engine, the platform, and what gets measured.",
  5200
);

// ---- 3. Compose the scenario -------------------------------------------------
await caption(page, "The form and the YAML are one document — edit either side.", 2600);
segments.mark("form");
await setField("env.gm.backend.type", "twitter_like", { select: true });
await caption(page, "Pick the platform the agents live on: a Twitter-like feed with posts, replies, and boosts.", 4200);
await setField("world.num_steps", 4);
await caption(page, "Four agents, four episodes — small enough to run while we watch.", 3600);

segments.mark("world-yaml");
await openYaml("world/default.yaml");
await caption(page, "world/default.yaml is the premise: a rumor that the campus dining hall is closing for good.", 3400);
await writeYaml("world/default.yaml", WORLD_YAML, { durationMs: 3300 });
await page.waitForTimeout(2600);

segments.mark("agents-yaml");
await openYaml("agents/default.yaml");
await caption(
  page,
  "agents/default.yaml is the cast: an amplifier, a skeptic, an anxious first-year, and the RA who has to answer.",
  3800
);
await writeYaml("agents/default.yaml", AGENTS_YAML, { durationMs: 4200 });
await page.waitForTimeout(2800);

segments.mark("sim-yaml");
await openYaml("sim.yaml");
await caption(page, "sim.yaml tunes the run itself — here it seeds each persona's opening post and prices the model.", 3400);
await writeYaml("sim.yaml", SIM_YAML, { durationMs: 1800 });
await page.waitForTimeout(1800);

segments.mark("eval-yaml");
await openYaml("eval.yaml");
await caption(page, "eval.yaml is the measurement: probes that ask every agent, every episode, whether they believe it.", 3800);
await writeYaml("eval.yaml", EVAL_YAML, { durationMs: 3300 });
await page.waitForTimeout(2800);

// ---- 4. Save -----------------------------------------------------------------
segments.mark("save");
await caption(page, `Save writes plain YAML to scenarios/${NAME} — the same files the CLI reads.`, 2200);
await page.click('[data-testid="save-scenario"]');
await page.waitForFunction(
  () => document.getElementById("save-state")?.textContent?.trim() === "Saved",
  null,
  { timeout: 60_000 }
);
await page.waitForTimeout(2600);

// ---- 5. Preflight ------------------------------------------------------------
segments.mark("preflight");
await caption(page, "Preflight validates the composed config and sizes the run before a single call is made.", 2400);
await page.click('[data-testid="preflight"]');
await page.waitForSelector("#preflight .stats-band", { timeout: 60_000 });
await page.waitForTimeout(1500);
await caption(page, "Sixteen agent-steps, sixteen model calls, about twenty thousand tokens — roughly a cent.", 5200);

// ---- 6. Launch a real run ----------------------------------------------------
await caption(page, "Launch runs it for real, on gpt-4o-mini.", 2800);
await page.selectOption('[data-testid="launch-mode"]', "continuous");
await page.waitForTimeout(700);
const before = await jobIds();
segments.mark("launch");
await page.click('[data-testid="launch-scenario"]');
await page.waitForURL(
  (url) => url.pathname === "/live" || url.pathname.startsWith("/runs/"),
  { timeout: 180_000 }
);
let jobId = new URL(page.url()).searchParams.get("job");
if (!jobId) {
  const now = await (await fetch(`${STUDIO}/api/jobs`)).json();
  jobId = (now.items ?? []).map((item) => item.id).find((id) => !before.has(id));
}
if (!jobId) throw new Error("could not identify the launched job");

// The live page hands off to the run's Watch tab on its own as soon as the
// artifact is discoverable. Captioning it is best-effort for exactly that
// reason: if the hop lands mid-evaluate the execution context goes away, and a
// missing caption must not lose the take.
segments.mark("live");
try {
  await caption(page, "Studio spawns the run as its own process and streams its log back.", 3200);
} catch {
  /* the hand-off won the race; the run page captions itself below */
}
await page.waitForURL((url) => url.pathname.startsWith("/runs/"), { timeout: 300_000 });
await page.waitForSelector('[data-testid="watch-ribbon"]', { timeout: 60_000 });
segments.mark("watch");
await page.waitForTimeout(2000);
await caption(page, "Studio hands off to the live run: episodes, actions, and spend as they happen.", 5600);

segments.mark("run-wait");
const job = await waitForJob(jobId, (item) => item.status === "finished", 900_000);
await page.waitForTimeout(2500);

// ---- 7. The finished run -----------------------------------------------------
const runs = await (await fetch(`${STUDIO}/api/runs`)).json();
const record = (runs.items ?? []).find((item) => item.path === job.output_dir);
if (!record) throw new Error(`the launched run was never indexed: ${job.output_dir}`);
const runPath = record.id.split("/").map(encodeURIComponent).join("/");

await go(`/runs/${runPath}?tab=overview`, "overview", { settle: 1800 });
await caption(page, "Four episodes, finished clean: no failed turns, no parse errors, no fallbacks.", 5200);
await scrollTour(2, 380, 1600);

await go(`/runs/${runPath}?tab=platform`, "platform", { settle: 900 });
await page.click(".segmented button");
await page.waitForSelector("#platform-frame:not(.hidden)", { timeout: 60_000 });
await page.waitForTimeout(1800);
await caption(page, "The platform tab replays the feed the agents actually wrote — your scenario, in their words.", 5000);
await scrollTour(2, 360, 1800);

await go(`/runs/${runPath}?tab=analyze&view=probes`, "probes", { settle: 2600 });
await caption(page, "And the probe you declared is now a measurement: belief in the rumor, episode by episode.", 5200);
await scrollTour(2, 400, 1800);

segments.mark("outro");
await caption(
  page,
  "From a blank page to a measured world. Next: the study video turns this scenario into an experiment.",
  5600
);

segments.mark("end");
const video = page.video();
await context.close();
await browser.close();
const recorded = await video.path();
const webm = path.join(build, "create-scenario.webm");
renameSync(recorded, webm);
writeFileSync(
  path.join(build, "create-scenario.segments.json"),
  JSON.stringify(segments.marks, null, 2)
);
// Marks are wall-clock; assemble.py cuts on the video clock. The screencast can
// lag under repaint pressure, so report the residual: a drift of a second or two
// is fine (the spans carry that much padding), a large one means the compressed
// span would land on the wrong footage and the marks need recalibrating.
const probed = execFileSync("ffprobe", [
  "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", webm,
]).toString().trim();
const drift = Number(probed) - segments.marks.at(-1).start;
console.log(`video ${Number(probed).toFixed(2)}s vs marks ${segments.marks.at(-1).start.toFixed(2)}s — drift ${drift.toFixed(2)}s`);
if (browserErrors.length) {
  console.error(`browser reported ${browserErrors.length} error(s):`);
  for (const item of browserErrors) console.error(`  ${item}`);
  process.exitCode = 1;
}
console.log("wrote build/create-scenario.webm + build/create-scenario.segments.json");
