# Quick Start

Run and inspect your first simulation in 5 minutes. Unfamiliar words along the
way (scenario, run, step, probe, ...) are defined in the [Glossary](glossary.md).

## Prerequisites

Make sure you have completed the [Installation](installation.md) steps.

## 0. Guided First Run (No API Key Needed)

Two commands verify your setup and show you a complete run end to end:

```sh
uv run silisocs doctor     # environment health checks
uv run silisocs tutorial   # deterministic scripted demo + artifact tour
```

The tutorial runs a small scripted-model simulation, lists the artifacts it
produced (`run_manifest.json`, `action_events.jsonl`, checkpoints, ...), and
prints the next commands to try.

## 1. Run the Default Scenario

The default scenario simulates a small generic social media community using
packaged inline personas. It does not require optional Hugging Face dependencies.

```sh
uv run silisocs
```

For a smoke test without model API calls, use the scripted model provider:

```sh
uv run silisocs sim.llm.provider=scripted
```

The `scripted` provider validates **plumbing, not content**: every agent gets
the same canned model response, so probe answers are unusable (they either fail
to parse and land as `"probe_return": null` or echo the placeholder text) and an
`open_ended` turn policy just repeats one canned action over and over. Use it to
confirm a scenario runs end to end and writes the artifacts you expect — never
to look at results. To check a config without running any steps at all, see
[Validate Before You Run](usage.md#validate-before-you-run).

This uses the built-in `default` preset with 10 agents for 5 steps. Override the
scale when you want a larger run:

```sh
uv run silisocs num_agents=25 num_steps=10
```

### Try Recommendation-Backed Timelines

Run a small social simulation with recommendation-backed timeline updates:

```sh
uv run silisocs env=reddit_like num_agents=10 num_steps=5
```

This uses the Reddit-like backend with hybrid timeline feeds, mixing
recommendations and follower posts, and built-in recommendation system updates.

See [Configuration Reference](configuration.md) for detailed configuration options.

## 2. Check the Output

Simulation output is saved to
`outputs/<scenario_name>/<jobname_format>/<scenario_name>_<timestamp>/`. For the
default run above that is
`outputs/default/N10_T5_independent_run1/default_2026-05-01_12-30-00/`. The
timestamped leaf means a re-run never overwrites the previous one; the CLI
prints the exact path as `Output directory: ...` when it starts.

| File | Content |
|------|---------|
| `run_manifest.json` | Self-describing run index: status, health, artifact paths |
| `run_events.jsonl` | Live progress feed: status transitions and step boundaries |
| `action_events.jsonl` | All agent actions (posts, replies, likes, reposts) |
| `exposure_events.jsonl` | What each agent saw per turn: post ids and their source |
| `probe_events.jsonl` | Probe/survey results (only if probes are configured) |
| `prompts_and_responses.jsonl` | Raw LLM prompts and responses |
| `run_stats.log` | Per-episode timing and worker telemetry |
| `sim_metrics.json` | Structured metrics summary (durations, resource usage) |
| `twitter_like.db` | SQLite database with full social media state |
| `effective_config.yaml` | Runtime-resolved config, API keys masked |

Two more files land one level up, in Hydra's own run directory
`outputs/<scenario_name>/<jobname_format>/` — the parent of the run directory:
the composed-config snapshot in `configs/<jobname_format>/` (so the full path is
`outputs/default/N10_T5_independent_run1/configs/N10_T5_independent_run1/`), and
Hydra's per-job log `<scenario_name>_<timestamp>.log`. Both stay put even when
you override `output_dir`. See
[Usage Overview: Output](usage.md#output) for the complete list.

## 3. Try a Different LLM

Override the LLM model from the command line:

```sh
uv run silisocs sim.llm.name=gpt-4o num_agents=10 num_steps=5
```

## 4. Use Silisocs Studio

Install and launch the unified visual interface:

```sh
uv sync --extra studio
uv run silisocs-studio --output-root outputs
```

Open `http://127.0.0.1:8765`. Studio authors the same scenario YAML used by the
CLI, validates and launches it through a persistent job queue, streams progress,
starts any backend-declared platform viewer, and analyzes run artifacts.

For a guided walkthrough of both workflows — with demo videos — see the
[End-to-End Demo](tutorials/studio_demo.md).

## 5. Analyze a Completed Run

Use the Runs station in Studio, or export a self-contained report from the CLI.
`silisocs-report` needs the `analysis` extra — on a lean install it prints
`silisocs-report requires: pip install "silisocs[analysis]"` and exits 1:

```sh
uv sync --extra analysis   # or: pip install "silisocs[analysis]"

uv run silisocs-report outputs/default/N10_T5_independent_run1/default_2026-05-01_12-30-00 \
  --view overview -o report.html
```

The report embeds its chart libraries and works without a Studio server.

## 6. Run an Example Scenario

The named example scenarios live in the repository's `scenarios/` directory
(they are example content, not part of the installed wheel). From a repo
checkout, run the election scenario (requires the `hf` extra for its persona
dataset: `pip install "silisocs[hf]"`):

```sh
uv run silisocs --config-path election
```

!!! warning "That is a 500-agent, 15-step run"
    The election scenario ships at research scale. For a first look, shrink the
    voter class instead of `num_agents` (which is only the declared total):

    ```sh
    uv run silisocs --config-path election \
      agents.persona_pipeline.classes.voter.count=17 num_agents=20 num_steps=3
    ```

    The [Election Walkthrough](tutorials/election.md) reads the whole scenario
    back key by key.

`--config-path` accepts a bare scenario name, a repo-style path
(`scenarios/election/conf`), or a filesystem path to your own scenario config
directory. The runner auto-detects the scenario name from the YAML files in
the config directory. No need to manually specify a `world=` override unless
you are choosing a non-default semantic world variant from `conf/world/`.

A `pip install` without a repo checkout still runs out of the box via the
packaged base config: omit `--config-path` entirely (see
[Installation](installation.md#run-the-base-config-no-repo-checkout-needed)).

## Next Steps

- [End-to-End Demos](tutorials/studio_demo.md): CLI, Studio, scenario authoring, and study videos
- [Usage Overview](usage.md): Full end-to-end guide
- [Configuration Reference](configuration.md): All config options
- [Building Agents](building_agents.md): Create custom agent populations
- [Election Walkthrough](tutorials/election.md): Complex scenario tutorial
