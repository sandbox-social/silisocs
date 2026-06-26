"""Locate scenario configurations by name within a repository checkout.

The example scenarios live in the repository's top-level ``scenarios/``
directory and are *not* bundled into the wheel — they are domain content, not
framework. A ``pip install`` ships only the engine and its base config
(``silisocs/conf/``), which is runnable on its own. Use these helpers from a
repo checkout to resolve a scenario by name (e.g. ``--config-path election``).
"""

from __future__ import annotations

from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent


def scenarios_root() -> Path:
    """Return the repository's scenario library root.

    Resolution prefers ``<repo>/scenarios`` (located by walking up to the
    directory containing ``pyproject.toml``) and falls back to
    ``<cwd>/scenarios``.

    Raises
    ------
        FileNotFoundError: When no ``scenarios/`` directory can be located,
            e.g. in a pip-installed environment without a repo checkout.
    """
    for parent in (_PACKAGE_DIR, *_PACKAGE_DIR.parents):
        if (parent / "pyproject.toml").is_file():
            candidate = parent / "scenarios"
            if candidate.is_dir():
                return candidate
            break
    cwd_candidate = Path.cwd() / "scenarios"
    if cwd_candidate.is_dir():
        return cwd_candidate
    raise FileNotFoundError(
        "No scenario library found. Example scenarios live in the repository's "
        "`scenarios/` directory and are not bundled with the installed package; "
        "run from a repo checkout or pass a filesystem path to --config-path."
    )


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
    """Return the Hydra config directory for a repo scenario by name.

    Accepts a bare scenario name (``election``), a ``scenarios/<name>`` form,
    or a ``scenarios/<name>/conf`` form, so documented repo-relative paths
    keep working regardless of the current working directory.

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
