// Record the CLI quickstart video: replay a captured asciicast in the styled
// terminal player and capture the page as video.
//
//   node record_cli.mjs <basename>   # expects build/<basename>.cast (+ captions)
//
// Output: build/<basename>.webm
import { mkdirSync, renameSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { recordingContext, staticServer } from "./lib.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const base = process.argv[2] ?? "cli";
const build = path.join(here, "build");
mkdirSync(build, { recursive: true });

const server = await staticServer(here);
const { browser, context } = await recordingContext(build);
const page = await context.newPage();
await page.goto(`http://127.0.0.1:${server.port}/terminal/player.html`);
await page.evaluate(
  ([cast, captions]) => playCast(cast, captions),
  [`/build/${base}.cast`, `/build/${base}.captions.json`]
);
await page.waitForFunction(() => window.__castDone, null, { timeout: 30 * 60 * 1000 });
await page.waitForTimeout(1200);

const video = page.video();
await context.close();
await browser.close();
server.close();
const recorded = await video.path();
renameSync(recorded, path.join(build, `${base}.webm`));
console.log(`wrote build/${base}.webm`);
