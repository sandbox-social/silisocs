# Installation

## Prerequisites

- Python 3.11 or newer
- `uv` 0.10 or newer

If `uv` is not installed yet, use one of the installation methods from the
[uv documentation](https://docs.astral.sh/uv/getting-started/installation/).

## Installing the Library

The default PyPI package is a lean runtime install. It includes the core
configuration system, agent interfaces, runtime engine, game-master components,
local backends, and evaluation probes.

```sh
pip install silisocs
```

Install optional integrations only when needed:

| Extra | Key packages | When to use |
|---|---|---|
| `concordia` | `gdm-concordia` | Legacy Concordia agent compatibility |
| `dashboard` | `streamlit` | Streamlit scenario launcher UI |
| `analysis` | matplotlib, plotly, dash, scipy, seaborn, scikit-network | Post-run dashboards and analysis utilities |
| `recsys` | scikit-learn, scipy, sentence-transformers | Recommendation system backends |
| `viz` | FastAPI, uvicorn, jinja2 | Local web visualizer for simulation logs |
| `hf` | `datasets` | Hugging Face persona datasets |
| `mastodon` | `mastodon.py`, loguru, pyvis | Real Mastodon server integration |
| `aws` | `boto3` | AWS storage and services |
| `docs` | ProperDocs, mkdocstrings, MkDocs plugins | Building the documentation site |
| `all` | all of the above except `aws` | Full runtime and documentation feature set |

```sh
pip install "silisocs[dashboard]"           # Streamlit scenario launcher
pip install "silisocs[analysis]"            # Dash analysis app and analysis utilities
pip install "silisocs[mastodon]"            # real Mastodon server integration
pip install "silisocs[all]"                 # all non-AWS extras, including docs
pip install "silisocs[all,aws]"             # all extras + AWS
```

## Run a Bundled Scenario (no repo checkout needed)

The wheel bundles the full scenario library. After `pip install silisocs`,
run any bundled scenario by name from any directory:

```sh
silisocs --config-path misinformation \
  num_agents=6 num_steps=5 \
  output_rootname=./misinfo_run
```

`--config-path` accepts a bare scenario name (`election`, `misinformation`,
...), a repo-style path (`scenarios/election/conf`), or a filesystem path to
your own scenario config directory. An unknown name lists the available
bundled scenarios. Scenario persona pipelines that load Hugging Face datasets
(e.g. `election`) additionally need `pip install "silisocs[hf]"`.

## Contributor Setup

1. Clone the repository:

   ```sh
   git clone https://github.com/sandbox-social/silisocs.git
   cd silisocs
   ```

2. Sync the default environment:

   ```sh
   uv sync --all-extras
   ```

   The default package is lean, while the repository test suite exercises
   optional integrations. `--all-extras` mirrors CI's full test environment.

3. For the full contributor environment, including local tooling such as
   `poethepoet`, `commitizen`, notebooks, and documentation helpers, run:

   ```sh
   uv sync --all-extras --group dev
   ```

4. If you need the documentation toolchain as well, include the docs group:

   ```sh
   uv sync --all-extras --group dev --group docs
   ```

## Common uv Workflows

- Add a runtime dependency: `uv add <package>`
- Add a test dependency: `uv add --group test <package>`
- Add a development dependency: `uv add --group dev <package>`
- Refresh the lockfile: `uv lock`
- Upgrade dependencies within existing bounds: `uv lock --upgrade`
- Run commands inside the project environment: `uv run <command>`

## Development Commands

- Install git hooks: `uv run pre-commit install`
- Run the lint workflow: `uv run poe lint`
- Run the test workflow: `uv run poe test`
- Build the documentation site: `uv run --group docs properdocs build --strict`

## Environment Variables

Silisocs reads `.env` files through `python-dotenv`, but only integrations that
talk to external services require secrets. Keep `.env` local and never commit it.

### Example `.env` File

```dotenv
OPENAI_API_KEY=<your_openai_key>

# Only needed with silisocs[mastodon]
API_BASE_URL=https://<mastodon-domain>
MASTODON_CLIENT_ID=<client_id>
MASTODON_CLIENT_SECRET=<client_secret>
EMAIL_PREFIX=<email_prefix>
USER001_PASSWORD=<user001_password>
USER002_PASSWORD=<user002_password>
```

## Next Steps

Once installed, head to the [Quick Start](quickstart.md) to run your first simulation.
