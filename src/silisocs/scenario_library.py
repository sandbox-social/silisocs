"""Locate bundled scenario configurations by name.

Scenarios live in the repository's top-level ``scenarios/`` directory during
development and are copied into the wheel as ``silisocs/scenarios/`` at build
time (see ``setup.py``), so pip-installed users get the full scenario library
without a repo checkout.

Resolution order prefers a repository checkout (live, editable tree) over the
packaged copy, so developers always run what they are editing.
"""

from __future__ import annotations

from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent


def packaged_scenarios_root() -> Path | None:
    """Return the scenario tree shipped inside the installed package, if any."""
    candidate = _PACKAGE_DIR / "scenarios"
    return candidate if candidate.is_dir() else None


def repo_scenarios_root() -> Path | None:
    """Return ``<repo>/scenarios`` when running from a repository checkout."""
    for parent in (_PACKAGE_DIR, *_PACKAGE_DIR.parents):
        if (parent / "pyproject.toml").is_file():
            candidate = parent / "scenarios"
            return candidate if candidate.is_dir() else None
    cwd_candidate = Path.cwd() / "scenarios"
    return cwd_candidate if cwd_candidate.is_dir() else None


def scenarios_root() -> Path:
    """Return the active scenario library root.

    Raises
    ------
        FileNotFoundError: When neither a repo checkout nor a packaged
            scenario tree is available.
    """
    root = repo_scenarios_root() or packaged_scenarios_root()
    if root is None:
        raise FileNotFoundError(
            "No scenario library found: expected a repository `scenarios/` "
            "directory or the packaged `silisocs/scenarios/` data."
        )
    return root


def list_scenarios() -> list[str]:
    """Return names of all scenarios that ship a `conf/` directory."""
    try:
        root = scenarios_root()
    except FileNotFoundError:
        return []
    return sorted(
        entry.name for entry in root.iterdir() if entry.is_dir() and (entry / "conf").is_dir()
    )


def scenario_conf_path(name: str) -> Path:
    """Return the Hydra config directory for a bundled scenario by name.

    Accepts a bare scenario name (``election``), a ``scenarios/<name>`` form,
    or a ``scenarios/<name>/conf`` form, so documented repo-relative paths
    keep working after a pip install from any working directory.

    Raises
    ------
        FileNotFoundError: When the scenario does not exist; the message lists
            the available scenario names.
    """
    cleaned = str(name).strip().strip("/").replace("\\", "/")
    parts = [part for part in cleaned.split("/") if part]
    if parts and parts[0] == "scenarios":
        parts = parts[1:]
    if parts and parts[-1] == "conf":
        parts = parts[:-1]
    if len(parts) != 1 or not parts[0]:
        raise FileNotFoundError(f"Cannot interpret scenario reference: {name!r}")
    scenario_name = parts[0]
    conf = scenarios_root() / scenario_name / "conf"
    if not conf.is_dir():
        available = ", ".join(list_scenarios()) or "<none>"
        raise FileNotFoundError(
            f"Unknown scenario '{scenario_name}'. Available scenarios: {available}"
        )
    return conf
