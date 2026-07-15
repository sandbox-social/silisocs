# Quick Start

Run and inspect your first simulation in 5 minutes.

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

Simulation output is saved to `outputs/default/<jobname>/<timestamp>/`:

| File | Content |
|------|---------|
| `action_events.jsonl` | All agent actions (posts, replies, likes, reposts) |
| `probe_events.jsonl` | Probe/survey results (if probes are configured) |
| `prompts_and_responses.jsonl` | Raw LLM prompts and responses |
| `run_stats.log` | Per-episode timing and worker telemetry |
| `sim_metrics.json` | Structured metrics summary (durations, resource usage) |
| `twitter_like.db` | SQLite database with full social media state |
| `.hydra/config.yaml` | Resolved Hydra config snapshot |

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

## 5. Analyze a Completed Run

Use the Runs station in Studio, or export a self-contained report from the CLI:

```sh
uv run silisocs-report outputs/default/<jobname>/<timestamp> \
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

`--config-path` accepts a bare scenario name, a repo-style path
(`scenarios/election/conf`), or a filesystem path to your own scenario config
directory. The runner auto-detects the scenario name from the YAML files in
the config directory. No need to manually specify a `world=` override unless
you are choosing a non-default semantic world variant from `conf/world/`.

A `pip install` without a repo checkout still runs out of the box via the
packaged base config: omit `--config-path` entirely (see
[Installation](installation.md#run-the-base-config-no-repo-checkout-needed)).

## Next Steps

- [Usage Overview](usage.md): Full end-to-end guide
- [Configuration Reference](configuration.md): All config options
- [Building Agents](building_agents.md): Create custom agent populations
- [Election Walkthrough](tutorials/election.md): Complex scenario tutorial
