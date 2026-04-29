# Installation

## Prerequisites

- Python 3.10 or newer
- `uv` 0.10 or newer

If `uv` is not installed yet, use one of the installation methods from the
[uv documentation](https://docs.astral.sh/uv/getting-started/installation/).

## Project Setup

1. Clone the repository:

   ```sh
   git clone https://github.com/social-sandbox/silisocs.git
   cd silisocs
   ```

2. Sync the default environment:

   ```sh
   uv sync
   ```

   This installs the project and the default `test` dependency group used by CI.

3. For the full contributor environment, including local tooling such as
   `poethepoet`, `commitizen`, notebooks, and documentation helpers, run:

   ```sh
   uv sync --group dev
   ```

4. If you need the documentation toolchain (MkDocs) as well, include the docs group:

   ```sh
   uv sync --group dev --group docs
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
- Generate API docs with `pdoc`: `uv run poe docs`

## Environment Variables

The application relies on a `.env` file to manage sensitive information and configuration settings. This file should be placed in the root directory of the project and contain the key-value pairs required by your chosen backend.

### Example `.env` File

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

## Next Steps

Once installed, head to the [Quick Start](quickstart.md) to run your first simulation.