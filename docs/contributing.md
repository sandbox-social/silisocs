# Contributing

Thank you for considering contributing to this project! We appreciate your efforts to improve and expand our work. Here are the steps and guidelines for contributing.

## Developing

- This project follows the [Conventional Commits](https://www.conventionalcommits.org/) standard to automate [Semantic Versioning](https://semver.org/) and [Keep A Changelog](https://keepachangelog.com/) with [Commitizen](https://github.com/commitizen-tools/commitizen).
- Use `uv add <package>` to add a runtime dependency to `pyproject.toml` and refresh `uv.lock`.
- Use `uv add --group test <package>` to add a CI or quality dependency such as `pytest`, `mypy`, or `ruff`.
- Use `uv add --group dev <package>` to add a local development dependency such as `poethepoet`, `commitizen`, or notebook tooling.
- Use `uv add --group docs <package>` to add an optional documentation dependency.
- Run `uv lock --upgrade` to upgrade dependencies to the newest versions allowed by `pyproject.toml`.
- Run `uv run cz bump` to bump the app version, update `CHANGELOG.md`, and create a git tag.

## Dependency Groups

- `test` is the default uv group for this repository. A plain `uv sync` installs the project plus the test, lint, and type-checking tools used in CI.
- `dev` adds local contributor tooling on top of the default environment: the `poethepoet` task runner, `commitizen` plus `cz-conventional-gitmoji`, `import-linter`, type stubs, and `numpy`/`pandas`/`requests` for tests that need them. It does **not** include notebook or documentation tooling — use the `docs` group for those.
- `docs` is optional and only needed when building the documentation site (ProperDocs/MkDocs). The `poe docs` task lives in `dev` but the toolchain it runs lives here, so building docs through Poe needs both groups.
- `hpc` is an optional **extra** (not a dependency group) and is only needed when testing Submitit/Slurm submission helpers: `uv sync --extra hpc`.
- Full release validation assumes all optional library extras are installed. Tests
  that need a real external service or LLM endpoint remain explicitly skipped or
  opt-in, but pure optional-library coverage is expected in contributor
  environments.

## Common Commands

- Sync the standard contributor environment: `uv sync --group dev`
- Sync the full release-test environment: `uv sync --all-extras --group dev --group docs`
- Run the pre-commit suite: `uv run --group dev poe lint`
- Run tests with coverage (defaults to non-LLM tests): `uv run --group dev poe test`
- Build the documentation site via Poe: `uv run --group dev --group docs poe docs`
- Build the documentation site directly: `uv run --group docs properdocs build --strict`
- Install git hooks: `uv run pre-commit install`
- Create a commit with Commitizen: `uv run cz c`

## Lint Policy

The contributor lint contract is the configured pre-commit workflow:
`uv run --group dev poe lint`. That workflow runs Ruff in the same mode as
`.pre-commit-config.yaml`, including auto-fix-only Ruff checks, Ruff format,
lock-file validation, mypy, and `lint-imports` (import-linter enforcing the
layering contracts declared in `pyproject.toml`: the runtime kernel imports no
upper layer, and the package layers stay one-directional).

Poe commands require the dev dependency group because `poethepoet` lives there,
so use `uv run --group dev poe lint` rather than plain `uv run poe lint`. The
`docs` task additionally needs the `docs` group, which is where the ProperDocs
toolchain it shells out to lives: `uv run --group dev --group docs poe docs`.

Do not treat an ad hoc `uv run ruff check --no-fix .` run as the release gate
unless the project first scopes or cleans the historical Ruff backlog. It is a
useful diagnostic when planning lint debt work, but it is intentionally broader
than the current enforced contributor contract.

## LLM E2E Testing

The repository includes opt-in real-LLM integration tests under the `llm_e2e`
marker. These tests launch short simulation runs and validate runtime artifacts.

- Start or point to any OpenAI-compatible server.
- Set `LLM_SERVER_URL`, for example: `http://localhost:30000/v1`.
- Run only expensive LLM tests:

    ```sh
    LLM_SERVER_URL=http://localhost:30000/v1 \
    uv run pytest -m llm_e2e tests/e2e/test_e2e_multi_gm_llm.py -v -s
    ```

- Note: `uv run --group dev poe test` excludes the `llm_e2e` marker by default.

- Run deterministic simulation-contract mirrors (default CI-safe set):

    ```sh
    uv run pytest tests/agents/test_fixed_entity_runtime.py tests/environments/test_backend_feed_contracts.py -v
    ```

### Required Artifact Checks

Every `llm_e2e` test should verify these generated files:

- `action_events.jsonl`: parseable JSONL and expected event fields (`episode`, `event_type`, `event_index`, action payload fields).
- `prompts_and_responses.jsonl`: parseable JSONL with prompt/output records.
- `run_stats.log`: file exists and contains run output.
- Backend SQLite DB (for example `twitter_like.db` or `reddit_like.db`): file exists and is readable.

### Isolation and Cleanup Policy

- Use per-test temporary output roots (for example pytest `tmp_path`) via Hydra overrides.
- Do not write test artifacts into repository root directories.
- Keep expensive tests assertion-dense and low in count; move broad combinatorics into deterministic tests.

## Contributing Steps

1. Create a feature branch:

    ```sh
    git checkout -b feature/my-new-feature
    ```

2. If you updated `pyproject.toml` manually, refresh the lock file:

    ```sh
    uv lock
    ```

3. Stage your changes:

    ```sh
    git add <file>
    ```

4. Install dependencies and local contributor tooling:

    ```sh
    uv sync --all-extras --group dev --group docs
    ```

    If you intentionally want a lean environment, `uv sync --group dev` is fine
    for core work, but full `uv run pytest` may skip or fail optional-library
    tests until extras are installed. For missing-extra failures, install the
    package with `silisocs[all]` or rerun the all-extras sync command above.

    If you only need to add docs tooling to a lean environment, use:

    ```sh
    uv sync --group dev --group docs
    ```

5. Install pre-commit hooks:

    ```sh
    uv run pre-commit install
    ```

6. Run (and rerun) pre-commit hooks command, fixing issues until all tests pass:

    ```sh
    uv run pre-commit run --all-files --verbose
    ```

    - This will automatically fix issues where possible, but some issues may require manual fixing.
    - You can also run `uv run --group dev poe lint` for the configured lint workflow and `uv run --group dev poe test` for the coverage workflow.

7. Commit using Commitizen:

    ```sh
    uv run cz c
    ```

    - Follow the prompts to create a conventional commit.

8. Push to GitHub:

    ```sh
    git push origin feature/my-new-feature
    ```

9. Go to GitHub and create a pull request from your recent feature branch.
    - Add a reviewer.

Thank you for your contribution!
