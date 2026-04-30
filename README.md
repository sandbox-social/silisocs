[![CI](https://github.com/social-sandbox/silisocs/actions/workflows/test.yml/badge.svg)](https://github.com/social-sandbox/silisocs/actions/workflows/test.yml)
[![Docs](https://github.com/social-sandbox/silisocs/actions/workflows/docs.yml/badge.svg)](https://social-sandbox.github.io/silisocs)
[![Open in Dev Containers](https://img.shields.io/static/v1?label=Dev%20Containers&message=Open&color=blue&logo=visualstudiocode)](https://vscode.dev/redirect?url=vscode://ms-vscode-remote.remote-containers/cloneInVolume?url=https://github.com/social-sandbox/silisocs)

# Social Simulation Sandbox for Social Media Scenarios

Configurable generative agent simulation of social media using the [Concordia framework](https://github.com/google-deepmind/concordia).
- 2024 NeurIPS Workshop Paper: [http://arxiv.org/abs/2410.13915](http://arxiv.org/abs/2410.13915).
- 2025 Version 1 demo paper: [https://www.ijcai.org/proceedings/2025/1271](https://www.ijcai.org/proceedings/2025/1271).
- Version 2 complete (structured scenario configuration compatible with Concordia v.2)

**[Read the full documentation](https://social-sandbox.github.io/silisocs)**

## Code Overview

### Runtime Entry Point

The canonical runtime entry point is:

- `src/silisocs/runtime/runner.py`

It composes Hydra config, builds agents + game masters, initializes memory,
runs the simulation engine, and writes outputs.

### Config Layout

Hydra composes these config groups:

- `src/silisocs/conf/sim/base.yaml`
- `src/silisocs/conf/social_media/{twitter_like|reddit_like|mastodon}.yaml`
- `src/silisocs/conf/scenario/{name}.yaml`

External scenarios can be placed at:

- `scenarios/<name>/conf/scenario/<name>.yaml`

and launched with `--config-path`.

### Output Artifacts

Simulation output is written under:

- `outputs/<scenario_name>/<jobname>/...`

Key files:

| Output file | Description |
| ----------- | ----------- |
| `action_events.jsonl` | Social-media actions with episode, source user, label, and data |
| `probe_events.jsonl` | Probe responses by episode and source user |
| `prompts_and_responses.jsonl` | LLM prompts and responses |
| `run_stats.log` | Startup and per-episode runtime statistics |
| `sim_metrics.json` | Structured metrics summary |
| `logs.html` | Concordia HTML logs |
| `<platform>.db` | SQLite platform state for local backends |
| `.hydra/config.yaml` | Resolved config snapshot |

### Dashboards

The project currently has two dashboard apps:

- Streamlit launcher: `src/silisocs/dashboard/launch_app.py`
- Dash analytics app: `src/silisocs/evaluations/analysis/dashboard/main.py`

The Streamlit app is for scenario authoring and run launch. The Dash app is
for post-run analysis of `action_events.jsonl` and `probe_events.jsonl`.

Here is a snapshot:

![alt text](https://github.com/social-sandbox/silisocs/blob/main/docs/img/dashboard_screenshot.png?raw=true)

## High-Level Structure

```text
silisocs/
├── src/silisocs/
│   ├── runtime/            # runner, simulation composition, config validation
│   ├── environments/       # engine, game master, backend implementations
│   ├── agents/             # entities, builders, initialization
│   ├── evaluations/        # probes, plotting, analytics dashboard
│   └── dashboard/          # streamlit launcher
├── src/silisocs/conf/  # Hydra config groups (sim/social_media/scenario)
├── scenarios/              # external scenarios and outputs
├── docs/                   # mkdocs user/developer documentation
└── tests/                  # unit and integration tests
```
```text
simsandbox/
  ├── src/
  │   └── EASE/              # Main package
  │       ├── agents/
  │       │   ├── components/        # Agent action/reasoning components
  │       │   └── initialization/    # Agent setup
  │       ├── environments/
  │       │   ├── backends/          # Mastodon/Twitter-like app backend
  │       │   └── gm/                # Game master (observe, recommend, etc.)
  │       ├── evaluations/
  │       │   ├── probes/            # Probe types and deployment
  │       │   └── analysis/          # Post-hoc analysis utilities
  │       ├── simulation/            # Simulation runner and telemetry
  │       |   ├── runner.py          # Probe types and deployment
  │       |   ├── simulators/        # wrapper object
  │       │   └── engines/           # simulation engine object
  │       └── conf/                  # Base Hydra config (sim, scenario, social_media)
  │
  ├── scenarios/                     # Per-scenario configs and inputs
  │   └── {scenario_name}
  │
  ├── studies/                       # Study orchestration
  │   ├── scripts/
  │   │   ├── run_study.py           # Orchestrator (simulate → eval → register → organize)
  │   │   ├── organize_experiments.py # Builds experiments/ tree from study.yaml
  │   │   └── study_io.py            # Shared IO utilities
  |   ├── study_schema.md
  │   └── {study_name}/              # Study data (study.yaml, eval.py, results)
  │
  └── outputs/                       # Raw simulation outputs (gitignored)
      ├── {scenario}_experiment/{timestamp}/
      └── eval_{study}/
```
<!--
## Hidden Section

## Installing

To install this package, run:

```sh
pip install silisocs
```

-->

## Development Installation

1. Clone the repository:

    ```sh
    git clone https://github.com/social-sandbox/silisocs.git
    cd silisocs
    ```

2. Install `uv`:

    ```sh
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ```

    See the [uv installation guide](https://docs.astral.sh/uv/getting-started/installation/) for alternative installation methods.

3. Sync the default project environment:

    ```sh
    uv sync
    ```

    This installs the project together with the default `test` group used by CI.

4. Install the contributor environment (required for committing):

    ```sh
    uv sync --group dev
    ```

    This adds `commitizen`, which is required by the pre-commit hooks that
    run on every commit. Skipping this step causes all commits to fail.

5. If you need the Sphinx docs toolchain too:

    ```sh
    uv sync --group dev --group docs
    ```

6. Run project commands inside the uv-managed environment:

    ```sh
    uv run poe lint
    uv run poe test
    ```

## Environment Variables

The application relies on a `.env` file to manage sensitive information and configuration settings. This file should be placed in the root directory of your project (`silisocs/`) and contain key-value pairs for required environment variables. The `dotenv` library is used to load these variables into the environment.

### Example `.env` File

Below is an scenario of what your `.env` file might look like. Make sure to replace the placeholder values with your actual configuration. Remember not to add this file to the project so as not to expose this sensitive information like client IDs, secrets, and passwords.

```dotenv
# Mastodon API base URL
API_BASE_URL=https://<domain_name>

# Mastodon client credentials
MASTODON_CLIENT_ID=*************************0
MASTODON_CLIENT_SECRET=*********************************o

# Email prefix for user accounts
EMAIL_PREFIX=<email_prefix>

# Bot user passwords
USER001_PASSWORD=***************************5
USER002_PASSWORD=***************************8
```

<!--
## Hidden Section

## Using

To view the CLI help information, run:

```sh
silisocs --help
```

-->
