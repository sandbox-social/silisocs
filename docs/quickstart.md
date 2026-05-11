# Quick Start

Run your first social media simulation in 5 minutes.

## Prerequisites

Make sure you have completed the [Installation](installation.md) steps.

## 1. Run the Default Scenario

The default scenario simulates a small generic social media community using
packaged inline personas. It does not require optional Hugging Face dependencies.

```sh
uv run silisocs
```

For a smoke test without model API calls, disable the LLM:

```sh
uv run silisocs sim.llm.disabled=true
```

This uses the built-in `default` preset with 10 agents for 5 steps. Override the
scale when you want a larger run:

```sh
uv run silisocs num_agents=25 num_steps=10
```

### Try the OASIS Preset

Run an OASIS-compatible simulation (smaller scale, optimized for recommendation studies):

```sh
uv run silisocs sim=oasis env=reddit_like num_agents=10 num_steps=5
```

This uses the `oasis` preset configuration with 100 agents, hybrid timeline feeds (mixing recommendations
and follower posts), and built-in recommendation system updates.

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
| `logs.html` | Browseable Concordia HTML log |
| `twitter_like.db` | SQLite database with full social media state |
| `.hydra/config.yaml` | Resolved Hydra config snapshot |

## 3. Try a Different LLM

Override the LLM model from the command line:

```sh
uv run silisocs sim.llm.name=gpt-4o num_agents=10 num_steps=5
```

## 4. Use the Dashboard

Launch the Streamlit dashboard for a visual interface:

```sh
uv sync --extra dashboard
uv run streamlit run src/silisocs/dashboard/launch_app.py
```

The dashboard lets you configure scenarios, agent classes, network topology,
and probes — then launch simulations with one click.

## 5. Analyze a Completed Run

Launch the analysis dashboard against a run output directory:

```sh
uv sync --extra analysis
uv run python -m silisocs.evaluations.analysis.dashboard.main \
	--output-dir outputs/default/<jobname>/<timestamp>
```

The analytics dashboard expects `action_events.jsonl` and `probe_events.jsonl`
in that folder.

## 6. Run an External Scenario

Run the bundled election scenario:

```sh
uv run silisocs --config-path scenarios/election/conf
```

The runner auto-detects the scenario name from the YAML files in the external
config directory. No need to manually specify a scenario override.

## Next Steps

- [Usage Overview](usage.md) — Full end-to-end guide
- [Configuration Reference](configuration.md) — All config options
- [Building Agents](building_agents.md) — Create custom agent populations
- [Election Walkthrough](tutorials/election.md) — Complex scenario tutorial
