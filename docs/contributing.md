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
- `dev` adds local contributor tooling on top of the default environment, including `poethepoet`, `commitizen`, notebooks, and documentation generation helpers.
- `docs` is optional and only needed when building the Sphinx documentation.

## Common Commands

- Sync the standard contributor environment: `uv sync --group dev`
- Sync the contributor environment with docs tooling: `uv sync --group dev --group docs`
- Run the pre-commit suite: `uv run poe lint`
- Run tests with coverage: `uv run poe test`
- Generate API docs with `pdoc`: `uv run poe docs`
- Install git hooks: `uv run pre-commit install`
- Create a commit with Commitizen: `uv run cz c`

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
    uv sync --group dev
    ```

    If you need to build the Sphinx docs too, use:

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
    - You can also run `uv run poe lint` for the configured lint workflow and `uv run poe test` for the coverage workflow.

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
