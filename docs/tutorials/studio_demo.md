# End-to-End Demo: CLI and Studio

A complete tour of silisocs on one bundled scenario — `misinformation`, a
six-agent social network where a false health claim circulates. Two short
videos mirror this page, and everything they show is reproducible from the
commands below.

<video controls preload="metadata" style="max-width: 100%;"
       src="../../assets/videos/silisocs-cli-quickstart.mp4"></video>

*CLI quickstart (69s): doctor → tutorial → a real run → the artifacts → a study plan.*

<video controls preload="metadata" style="max-width: 100%;"
       src="../../assets/videos/silisocs-studio-tour.mp4"></video>

*Studio tour: scenario editor → preflight → interactive launch → platform
viewer → analysis → study comparison.*

Both videos are produced by the scripted pipeline in the repository's `demo/`
directory, so they can be regenerated whenever the product changes.

## Part 1 — the command line

Check the environment, then watch a deterministic scripted demo (no API key
needed):

```sh
uv run silisocs doctor
uv run silisocs tutorial
```

Run the real thing — six gpt-4o-mini agents for ten steps (an `OPENAI_API_KEY`
in `.env` is picked up automatically; the run costs cents):

```sh
uv run silisocs --config-path scenarios/misinformation/conf
```

Every run is self-describing. The manifest carries status plus the
[run-health counters](../usage.md#run-health), and `action_events.jsonl`
records every committed backend action:

```sh
jq '{status, health}' outputs/<run-dir>/run_manifest.json
head outputs/<run-dir>/action_events.jsonl
```

The scenario ships with a small study — two call-to-action framings × two
seeds (see [Study Schema](../study_schema.md) for the YAML):

```sh
uv run silisocs-study --study experiments/studies/misinformation_cta_demo plan
uv run silisocs-study --study experiments/studies/misinformation_cta_demo run --yes
```

`run` fans out the four runs, executes the study's evaluators
(`experiments/studies/misinformation_cta_demo/eval.py` computes spread and
belief metrics into `aggregated`), and aggregates replicates with
mean/stdev/CI95 into `generated/organized/summary.json`.

## Part 2 — Studio

```sh
uv sync --extra studio
uv run silisocs-studio --output-root outputs
```

Open `http://127.0.0.1:8765` and follow the tour:

1. **Scenarios → misinformation.** The form and the YAML mirror edit the same
   files under `scenarios/misinformation/conf/` — Studio never invents a
   second format.
2. **Preflight.** Validates the composed config and estimates agent-steps,
   LLM calls, and tokens before anything runs. (The estimate accounts for the
   scenario's activity gating — agents here are active ~80% of steps.)
3. **Launch → Interactive.** The run starts paused before episode 0 with
   Step / Play / Pause / End-run controls at episode boundaries. Step twice,
   watching the live log and artifact counters; then Play and let it finish.
4. **Runs → Platform.** Opens the run's Twitter-like feed read-only — the
   world exactly as the agents saw it.
5. **Runs → Analyze → Spread.** The scenario bundles an analysis view
   (`scenarios/misinformation/conf/views/spread.yaml`): run health, the
   interaction network, the content feed, probe trends and distributions,
   behavior breakdown, and exposure funnels. Views are plain YAML lists of
   panels — see [Analysis Panels](../analysis_panels.md).
6. **Explore.** The same run, cross-filtered by time, actor, label, and event.
7. **Studies → misinformation_cta_demo.** The board tracks all four
   replicates; Compare charts every `aggregated` metric per condition with
   95% confidence intervals across seeds.

## What this demo exercises

| Surface | Where |
|---|---|
| Scenario config + variants | `scenarios/misinformation/conf/` (`env/cta_*.yaml`) |
| Bundled analysis view | `conf/views/spread.yaml` |
| Probes (binary / rating / free-text) | `conf/eval.yaml` |
| Study fan-out + custom evaluator | `experiments/studies/misinformation_cta_demo/` |
| Interactive run control | `sim.engine.control` (injected by Studio) |
| HTTP control plane | `tests/test_studio_e2e_demo.py` drives the same flow headlessly |

The e2e test is the executable form of this page: it launches the scenario
through `POST /api/launch` with the offline `scripted` model provider, follows
the SSE job stream, and asserts the bundled view renders every panel. If this
tutorial drifts from reality, that test fails.

## Reproducing the videos

See `demo/README.md`. The pipeline is fully scripted (pty capture + a styled
terminal player + Playwright driving a live Studio server + ffmpeg assembly),
so a coding agent can re-produce both videos after UI changes.
