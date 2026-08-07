// Shared helpers for the demo recorders.
import http from "node:http";
import { createReadStream, existsSync, statSync } from "node:fs";
import path from "node:path";
import { chromium } from "playwright-core";

const MIME = {
  ".html": "text/html", ".js": "text/javascript", ".css": "text/css",
  ".json": "application/json", ".cast": "text/plain", ".woff2": "font/woff2",
};

/** Serve a directory on an ephemeral port; returns { port, close }. */
export function staticServer(root) {
  const server = http.createServer((req, res) => {
    const clean = decodeURIComponent(new URL(req.url, "http://x").pathname);
    const file = path.join(root, clean.replace(/^\/+/, ""));
    if (!file.startsWith(path.resolve(root)) || !existsSync(file) || !statSync(file).isFile()) {
      res.writeHead(404).end("not found");
      return;
    }
    res.writeHead(200, { "content-type": MIME[path.extname(file)] ?? "application/octet-stream" });
    createReadStream(file).pipe(res);
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () =>
      resolve({ port: server.address().port, close: () => server.close() })
    );
  });
}

/** Launch the recording browser + context. CHROME env names the binary. */
export async function recordingContext(videoDir) {
  const executablePath = process.env.CHROME;
  if (!executablePath) throw new Error("Set CHROME to a runnable Chromium binary (see README)");
  const browser = await chromium.launch({ executablePath, args: ["--no-sandbox"] });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 720 },
    recordVideo: { dir: videoDir, size: { width: 1280, height: 720 } },
  });
  return { browser, context };
}

/** Inject the fixed caption bar used on live Studio pages. */
export async function installCaptionBar(page) {
  await page.addStyleTag({
    content: `
      #demo-caption { position: fixed; left: 0; right: 0; bottom: 22px; display: flex;
                      justify-content: center; z-index: 99999; pointer-events: none; }
      #demo-caption div { max-width: 900px; background: rgba(13,17,23,.92); color: #e8ecf4;
                          border: 1px solid #2a3040; border-radius: 8px; padding: 10px 22px;
                          font-size: 21px; line-height: 1.35; font-family: sans-serif;
                          opacity: 0; transition: opacity .35s; }`,
  });
  await page.evaluate(() => {
    const bar = document.createElement("div");
    bar.id = "demo-caption";
    bar.innerHTML = "<div></div>";
    document.body.appendChild(bar);
  });
}

/** Show a caption (re-installing the bar after a navigation if needed). */
export async function caption(page, text, holdMs = 900) {
  if (!(await page.locator("#demo-caption").count())) await installCaptionBar(page);
  await page.evaluate((value) => {
    const el = document.querySelector("#demo-caption div");
    el.textContent = value;
    el.style.opacity = value ? 1 : 0;
  }, text);
  await page.waitForTimeout(holdMs);
}

/** Segment marks for assemble.sh: [{name, start, wait}] in video seconds. */
export class Segments {
  constructor() { this.t0 = Date.now(); this.marks = []; }
  mark(name, { wait = false } = {}) {
    this.marks.push({ name, start: (Date.now() - this.t0) / 1000, wait });
  }
}
