# Demo video pipeline

Fully scripted production of the four bundled demo videos:

- `docs/assets/videos/silisocs-cli-quickstart.mp4` — doctor → tutorial → a real
  misinformation run → artifact tour → `silisocs-study plan`.
- `docs/assets/videos/silisocs-studio-tour.mp4` — scenario editor → preflight →
  interactive launch (Step/Step/Play on a real run) → platform viewer →
  bundled `spread` analysis view → explore → study board/compare.
- `docs/assets/videos/silisocs-create-scenario.mp4` — blank Studio workspace →
  scaffold → world/agents/simulation/probes → preflight → live run → results.
- `docs/assets/videos/silisocs-design-study.mp4` — hypothesis and conditions →
  validated plan → four real replicates → custom evaluation → statistics.

Everything is driven by code (no human screen capture), so the videos can be
re-produced — by a person or a coding agent — whenever the product changes.

## How it works

| Piece | Role |
|---|---|
| `tapes/*.yaml` | Terminal "tapes": commands, captions, per-step time compression |
| `capture_cli.py` | Runs the tape in a pty → asciicast v2 + caption cues |
| `terminal/player.html` | Styled xterm.js window that replays the cast with captions |
| `record_cli.mjs` | Plays the cast in headless Chromium, records the page as video |
| `record_studio.mjs` | Scripted Playwright tour of a live Studio server (captions injected into the page; emits `segments.json` marks) |
| `record_create_scenario.mjs` | Authors and launches `campus_rumor` through a live Studio API and UI |
| `assemble.py` | ffmpeg: title card + wait-segment speed-up + 720p H.264 |
| `run_all.sh` | Orchestrates all of the above |
| `chrome-wrapper.sh` | Optional launch shim for hosts that need a custom loader |
| `smoke_studio.mjs` | Headless browser smoke of a live Studio (no recording) — the driver behind `tests/e2e/test_studio_browser_smoke.py` |

## Prerequisites

- `uv sync --group dev --extra studio` (the repo venv provides `silisocs*` CLIs)
- Node >= 18 (`npm ci --prefix demo` pulls `playwright-core`; browsers are NOT
  downloaded — point `CHROME` at any runnable Chromium)
- `ffmpeg` + `ffprobe` on PATH; `fc-match` with DejaVu fonts
- `OPENAI_API_KEY` in the repo `.env` (the recordings include several small
  real gpt-4o-mini runs; total cost is still measured in cents)
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
CHROME=... demo/run_all.sh scenario               # scenario authoring
CHROME=... demo/run_all.sh study                  # study design and execution
demo/run_all.sh assemble                          # re-encode from build/*.webm
```

The scenario stage starts Studio against `demo/build/scenario-workspace`, not
the checkout, then diffs the authored YAML against the committed
`scenarios/campus_rumor` example. The study stage deletes only that demo's
gitignored `generated/` and `runs/` directories before recording. Neither stage
changes tracked source files.

## Dependency boundary

The recording stack is repository tooling, not a library feature:

- Browser packages live only in the private `demo/package.json` and lockfile.
- Chromium, ffmpeg, ffprobe, and fontconfig are host tools, not Python package
  dependencies.
- The Python scripts use the installed Silisocs development environment; they
  add no runtime dependency.
- `demo/`, `scenarios/`, `experiments/`, and `docs/` are excluded from the
  built wheel. Packaging tests enforce both boundaries.

## Product coverage

| Key workflow | Recording |
|---|---|
| Environment check, tutorial, real CLI run, and artifact inspection | CLI quickstart |
| Scenario editing, preflight, stepped execution, platform view, analysis, exploration, and study comparison | Studio tour |
| New-scenario scaffold, YAML authoring, probes, preflight, launch, and result inspection | Create a scenario |
| Hypothesis/condition design, plan validation, replicates, custom evaluator, and aggregate statistics | Design a study |

Advanced extension APIs such as custom backends, components, multi-GM routing,
interventions, checkpoint recovery, and harness adapters are reference and test
suite material rather than separate product-tour videos.

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

- Change narration/timing: edit `tapes/*.yaml` captions or the `caption(...)`
  lines in either Studio recorder, then re-run the stage.
- LLM wait spans in the Studio tour are marked (`segments.mark(..., {wait: true})`)
  and compressed 12x at assembly — tune with `assemble.py --wait-speed`.
- Intermediate artifacts live in `build/` (gitignored): `.cast` tapes,
  raw `.webm` recordings, and segment marks.
