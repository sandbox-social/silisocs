# Quick Start

Run your first social media simulation in 5 minutes.

## Prerequisites

Make sure you have completed the [Installation](installation.md) steps.

## 1. Run the Default Scenario

The default scenario simulates a generic social media community with agents
sourced from the [Nemotron Personas](https://huggingface.co/datasets/nvidia/Nemotron-Personas-USA)
dataset.

```sh
uv run mastodon-sim
```

This uses the built-in `default` preset with 500 agents for 200 steps. For a quick test, override the
scale:

```sh
uv run mastodon-sim sim.num_agents=10 sim.num_steps=5
```

### Try the OASIS Preset

Run an OASIS-compatible simulation (smaller scale, optimized for recommendation studies):

```sh
uv run mastodon-sim sim=oasis env=reddit_like sim.num_agents=10 sim.num_steps=5
```

This uses the `oasis` preset configuration with 100 agents, hybrid timeline feeds (mixing recommendations
and follower posts), and built-in recommendation system updates.

See [Preset Configurations](PRESETS.md) for detailed comparison of preset options.

## 2. Check the Output

Simulation output is saved to `scenarios/default/outputs/<jobname>/<timestamp>/`:

| File | Content |
|------|---------|
| `action_events.jsonl` | All agent actions (posts, replies, likes, reposts) |
| `probe_events.jsonl` | Probe/survey results (if probes are configured) |
| `prompts_and_responses.jsonl` | Raw LLM prompts and responses |
| `run_stats.log` | Per-episode timing and worker telemetry |
| `sim_metrics.json` | Structured metrics summary (durations, resource usage) |
| `logs.html` | Browseable Concordia HTML log |
| `twitter_like.db` | SQLite database with full social media state |
| `.hydra/config.yaml` | Resolved Hydra config snapshot |

## 3. Try a Different LLM

Override the LLM model from the command line:

```sh
uv run mastodon-sim sim.llm_name=gpt-4o sim.num_agents=10 sim.num_steps=5
```

## 4. Use the Dashboard

Launch the Streamlit dashboard for a visual interface:

```sh
uv run streamlit run src/mastodon_sim/dashboard/launch_app.py
```

The dashboard lets you configure scenarios, agent classes, network topology,
and probes — then launch simulations with one click.

## 5. Analyze a Completed Run

Launch the analysis dashboard against a run output directory:

```sh
uv run python -m mastodon_sim.evaluations.analysis.dashboard.main \
	--output-dir scenarios/default/outputs/<jobname>/<timestamp>
```

The analytics dashboard expects `action_events.jsonl` and `probe_events.jsonl`
in that folder.

## 6. Run an External Scenario

Run the bundled election scenario:

```sh
uv run mastodon-sim --config-path scenarios/election/conf
```

The runner auto-detects the scenario name from the YAML files in the external
config directory. No need to manually specify a scenario override.

## Next Steps

- [Usage Overview](usage.md) — Full end-to-end guide
- [Configuration Reference](configuration.md) — All config options
- [Building Agents](building_agents.md) — Create custom agent populations
- [Election Walkthrough](tutorials/election.md) — Complex scenario tutorial
