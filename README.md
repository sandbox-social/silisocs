[![CI](https://github.com/social-sandbox/mastodon-sim/actions/workflows/test.yml/badge.svg)](https://github.com/social-sandbox/mastodon-sim/actions/workflows/test.yml)
[![Docs](https://github.com/social-sandbox/mastodon-sim/actions/workflows/docs.yml/badge.svg)](https://social-sandbox.github.io/mastodon-sim)
[![Open in Dev Containers](https://img.shields.io/static/v1?label=Dev%20Containers&message=Open&color=blue&logo=visualstudiocode)](https://vscode.dev/redirect?url=vscode://ms-vscode-remote.remote-containers/cloneInVolume?url=https://github.com/social-sandbox/mastodon-sim)

# Social Simulation Sandbox for Social Media Scenarios

Configurable generative agent simulation of social media using the [Concordia framework](https://github.com/google-deepmind/concordia).
- 2024 NeurIPS Workshop Paper: [http://arxiv.org/abs/2410.13915](http://arxiv.org/abs/2410.13915).
- 2025 Version 1 demo paper: [https://www.ijcai.org/proceedings/2025/1271](https://www.ijcai.org/proceedings/2025/1271).
- Version 2 complete (structured scenario configuration compatible with Concordia v.2)

**[Read the full documentation](https://social-sandbox.github.io/mastodon-sim)**

## Code Overview

### Runtime Entry Point

The canonical runtime entry point is:

- `src/mastodon_sim/runtime/runner.py`

It composes Hydra config, builds agents + game masters, initializes memory,
runs the simulation engine, and writes outputs.

### Config Layout

Hydra composes these config groups:

- `src/mastodon_sim/conf/sim/base.yaml`
- `src/mastodon_sim/conf/social_media/{twitter_like|reddit_like|mastodon}.yaml`
- `src/mastodon_sim/conf/scenario/{name}.yaml`

External scenarios can be placed at:

- `scenarios/<name>/conf/scenario/<name>.yaml`

and launched with `--config-path`.

### Output Artifacts

Simulation output is written under:

- `scenarios/<scenario_name>/outputs/<jobname>/...`

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

- Streamlit launcher: `src/mastodon_sim/dashboard/launch_app.py`
- Dash analytics app: `src/mastodon_sim/evaluations/analysis/dashboard/main.py`

The Streamlit app is for scenario authoring and run launch. The Dash app is
for post-run analysis of `action_events.jsonl` and `probe_events.jsonl`.

Here is a snapshot:

![alt text](https://github.com/social-sandbox/mastodon-sim/blob/main/docs/img/dashboard_screenshot.png?raw=true)

## High-Level Structure

```text
mastodon-sim/
├── src/mastodon_sim/
│   ├── runtime/            # runner, simulation composition, config validation
│   ├── environments/       # engine, game master, backend implementations
│   ├── agents/             # entities, builders, initialization
│   ├── evaluations/        # probes, plotting, analytics dashboard
│   └── dashboard/          # streamlit launcher
├── src/mastodon_sim/conf/  # Hydra config groups (sim/social_media/scenario)
├── scenarios/              # external scenarios and outputs
├── docs/                   # mkdocs user/developer documentation
└── tests/                  # unit and integration tests
```
<!--
## Hidden Section

## Installing

To install this package, run:

```sh
pip install mastodon-sim
```

-->

## Development Installation

1. Clone the repository:

    ```sh
    git clone https://github.com/social-sandbox/mastodon-sim.git
    cd mastodon-sim
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

4. Install the full contributor environment:

    ```sh
    uv sync --group dev
    ```

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

The application relies on a `.env` file to manage sensitive information and configuration settings. This file should be placed in the root directory of your project (`mastodon-sim/`) and contain key-value pairs for required environment variables. The `dotenv` library is used to load these variables into the environment.

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
mastodon-sim --help
```

-->
