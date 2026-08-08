# Demo video pipeline

Fully scripted production of the two bundled demo videos:

- `docs/assets/videos/silisocs-cli-quickstart.mp4` — doctor → tutorial → a real
  misinformation run → artifact tour → `silisocs-study plan`.
- `docs/assets/videos/silisocs-studio-tour.mp4` — scenario editor → preflight →
  interactive launch (Step/Step/Play on a real run) → platform viewer →
  bundled `spread` analysis view → explore → study board/compare.

Everything is driven by code (no human screen capture), so the videos can be
re-produced — by a person or a coding agent — whenever the product changes.

## How it works

| Piece | Role |
|---|---|
| `tapes/cli.yaml` | Terminal "tape": commands, captions, per-step time compression |
| `capture_cli.py` | Runs the tape in a pty → asciicast v2 + caption cues |
| `terminal/player.html` | Styled xterm.js window that replays the cast with captions |
| `record_cli.mjs` | Plays the cast in headless Chromium, records the page as video |
| `record_studio.mjs` | Scripted Playwright tour of a live Studio server (captions injected into the page; emits `segments.json` marks) |
| `assemble.py` | ffmpeg: title card + wait-segment speed-up + 720p H.264 |
| `run_all.sh` | Orchestrates all of the above |
| `chrome-wrapper.sh` | Optional launch shim for hosts that need a custom loader |
| `smoke_studio.mjs` | Headless browser smoke of a live Studio (no recording) — the driver behind `tests/e2e/test_studio_browser_smoke.py` |

## Prerequisites

- `uv sync --group dev --extra studio` (the repo venv provides `silisocs*` CLIs)
- Node >= 18 (`npm install` here pulls `playwright-core`; browsers are NOT
  downloaded — point `CHROME` at any runnable Chromium)
- `ffmpeg` + `ffprobe` on PATH; `fc-match` with DejaVu fonts
- `OPENAI_API_KEY` in the repo `.env` (both videos include one real
  gpt-4o-mini run of `scenarios/misinformation`; ≈ cents)
- The Studio tour expects two pre-built artifacts in the repo:
  `outputs/misinformation/hero_run` (one full run of the scenario) and the
  completed `experiments/studies/misinformation_cta_demo` study:

  ```sh
  uv run silisocs --config-path scenarios/misinformation/conf \
      "++output_dir=$PWD/outputs/misinformation/hero_run"
  uv run silisocs-study --study experiments/studies/misinformation_cta_demo run --yes
  ```

## Produce the videos

```sh
CHROME=/path/to/chromium demo/run_all.sh          # everything
CHROME=... demo/run_all.sh cli                    # just the terminal video
CHROME=... demo/run_all.sh studio                 # just the Studio tour
demo/run_all.sh assemble                          # re-encode from build/*.webm
```

`CHROME` can be a Playwright-cached browser
(`~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome`) or a system
Chromium. On hosts whose system libraries are too old for that binary
(e.g. HPC login nodes), either use `chrome-wrapper.sh` with
`SILISOCS_DEMO_LOADER`/`SILISOCS_DEMO_LIBPATH` pointing at a newer prefix, or
`patchelf --set-interpreter <new ld.so> --set-rpath <new libdirs>` on a *copy*
of the browser and pass that copy as `CHROME` (child processes re-exec
`/proc/self/exe`, so patching beats wrapping).

## Browser smoke (not a video)

The same `playwright-core` install doubles as the repo's only browser-level
test. `tests/e2e/test_studio_browser_smoke.py` starts a Studio server over a
throwaway workspace (a copy of `scenarios/misinformation` pinned to the offline
`scripted` provider and `num_steps: 2`, random port, temp state dir), then runs
`smoke_studio.mjs` through home → scenarios → scenario editor → preflight → the
UI **Launch** button → the live page → Play → the finished run's
Overview/Watch/Analyze tabs, failing on any `console.error` or uncaught page
error on any of them. No API key, ~12 s.

```sh
CHROME=/path/to/chromium uv run pytest tests/e2e/test_studio_browser_smoke.py -q
```

The browser is resolved from `SILISOCS_SMOKE_CHROME`, then `CHROME`, then
`chrome-wrapper.sh` (configured via `SILISOCS_DEMO_CHROME` as above), and each
candidate must actually answer `--version`. With none of them working the test
**skips** — that is the CI default, and no host without a browser ever fails.

To watch it happen, or to point it at a Studio you started yourself:

```sh
SMOKE_HEADED=1 STUDIO_URL=http://127.0.0.1:8765 CHROME=/path/to/chromium \
    node demo/smoke_studio.mjs
```

## Editing the videos

- Change narration/timing: edit `tapes/cli.yaml` captions or the
  `caption(...)` lines in `record_studio.mjs`, re-run the stage.
- LLM wait spans in the Studio tour are marked (`segments.mark(..., {wait: true})`)
  and compressed 12x at assembly — tune with `assemble.py --wait-speed`.
- Intermediate artifacts live in `build/` (gitignored): `.cast` tapes,
  raw `.webm` recordings, and segment marks.
