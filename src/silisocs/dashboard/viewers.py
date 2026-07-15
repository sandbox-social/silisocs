"""Launch plans for companion viewer processes and live-run event tailing.

The Streamlit launcher can open two kinds of companion viewers next to a run:

- the per-backend platform visualizer (FastAPI, ``viz`` extra) — a read-only
  replica of the platform UI rendered straight from the run's SQLite database
- the Dash analysis dashboard (``analysis`` extra) on the run's output directory

This module is pure (no Streamlit import) so the path/command/discovery logic
is unit-testable; ``launch_app.py`` owns the widgets and the actual
``subprocess.Popen`` calls.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from silisocs.environments.backends.factory import registered_backend_types, resolve_backend_class
from silisocs.evaluations.action_events import resolve_action_event_files

# backend_type -> (database env var, server module, default port). Run manifests
# extend this map for custom backends; callers may override the port.
VISUALIZER_PORT_ENVS: dict[str, str] = {}
VISUALIZER_BACKENDS: dict[str, tuple[str, str, int]] = {}


def has_visualizer(backend_type: str) -> bool:
    """Discover one built-in backend's viewer declaration on demand."""
    if backend_type in VISUALIZER_BACKENDS:
        return True
    if backend_type not in registered_backend_types():
        return False
    backend_class = resolve_backend_class(backend_type)
    spec = getattr(backend_class, "visualizer", None)
    if spec is None:
        return False
    VISUALIZER_BACKENDS[backend_type] = (spec.env_var, spec.module, spec.default_port)
    VISUALIZER_PORT_ENVS[backend_type] = spec.port_env
    return True


def _register_manifest_visualizers(root: Path) -> list[tuple[str, Path]]:
    """Load self-describing visualizers and database paths from a run manifest."""
    manifest_path = root / "run_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    found: list[tuple[str, Path]] = []
    for gm in manifest.get("game_masters", []):
        if not isinstance(gm, dict):
            continue
        backend_type = str(gm.get("backend_type") or "").strip()
        visualizer = gm.get("visualizer")
        if backend_type and isinstance(visualizer, dict):
            env_var = str(visualizer.get("env_var") or "").strip()
            module = str(visualizer.get("module") or "").strip()
            port = visualizer.get("default_port")
            if env_var and module and isinstance(port, int):
                VISUALIZER_BACKENDS[backend_type] = (env_var, module, port)
                VISUALIZER_PORT_ENVS[backend_type] = str(
                    visualizer.get("port_env") or "SILISOCS_VIEWER_PORT"
                )
        database = gm.get("database")
        if backend_type and isinstance(database, str) and database:
            path = Path(database)
            path = path if path.is_absolute() else root / path
            if path.is_file() and (backend_type, path) not in found:
                found.append((backend_type, path))
    return found


ANALYSIS_PORT = 8050  # Dash default; the analysis CLI takes no port flag.


@dataclass(frozen=True)
class LaunchPlan:
    """Everything needed to spawn a viewer: argv, extra env, and its URL."""

    cmd: list[str]
    env: dict[str, str]
    url: str
    missing_extra: str | None = None  # pip extra to install when deps are absent


def find_backend_dbs(run_dir: str | Path) -> list[tuple[str, Path]]:
    """Locate visualizable backend databases in a run output directory.

    Returns ``(backend_type, db_path)`` pairs, covering both the flat
    single-GM layout (``<run>/<backend_type>.db``) and the per-GM multi-GM
    layout (``<run>/<gm_name>/<backend_type>.db``).
    """
    root = Path(run_dir)
    if not root.is_dir():
        return []
    found = _register_manifest_visualizers(root)
    for pattern in ("*.db", "*/*.db"):
        for path in sorted(root.glob(pattern)):
            if has_visualizer(path.stem) and (path.stem, path) not in found:
                found.append((path.stem, path))
    return found


def _extra_missing(extra: str, modules: tuple[str, ...]) -> str | None:
    ok = all(importlib.util.find_spec(module) is not None for module in modules)
    return None if ok else extra


def visualizer_plan(
    backend_type: str, db_path: str | Path, *, port: int | None = None
) -> LaunchPlan:
    """Launch plan for the read-only platform visualizer of ``backend_type``."""
    if not has_visualizer(backend_type):
        raise KeyError(f"Backend {backend_type!r} does not declare a visualizer")
    env_var, module, default_port = VISUALIZER_BACKENDS[backend_type]
    selected_port = port or default_port
    env = {env_var: str(db_path)}
    if port is not None:
        env[VISUALIZER_PORT_ENVS.get(backend_type, "SILISOCS_VIEWER_PORT")] = str(port)
    return LaunchPlan(
        cmd=[sys.executable, "-m", module],
        env=env,
        url=f"http://localhost:{selected_port}",
        missing_extra=_extra_missing("viz", ("fastapi", "uvicorn")),
    )


def analysis_plan(run_dir: str | Path) -> LaunchPlan:
    """Launch plan for the Dash analysis dashboard on a run output directory."""
    return LaunchPlan(
        cmd=[
            sys.executable,
            "-m",
            "silisocs.evaluations.analysis.dashboard.main",
            "--output_dir",
            str(run_dir),
        ],
        env={},
        url=f"http://localhost:{ANALYSIS_PORT}",
        missing_extra=_extra_missing("analysis", ("dash", "dash_cytoscape", "plotly")),
    )


# Files whose presence marks a directory as a run output dir (any one suffices).
_RUN_ARTIFACT_NAMES = (
    "effective_config.yaml",
    "run_manifest.json",
    "sim_metrics.json",
    "action_events.jsonl",
)


def _looks_like_run_dir(path: Path) -> bool:
    if any((path / name).is_file() for name in _RUN_ARTIFACT_NAMES):
        return True
    return bool(find_backend_dbs(path))


def discover_run_dir(scenario: str, started_after: float, root: str | Path = ".") -> Path | None:
    """Find the newest run output directory created for a fresh launch.

    Used by the live panel to locate the output directory of a run that was
    just started (Hydra names it, so the launcher cannot know it up front).
    The standard layout is ``outputs/<scenario>/<job>/<scenario>_<timestamp>/``
    with the artifacts in the timestamped leaf, but a flat
    ``outputs/<scenario>/<job>/`` layout is accepted too — candidates at both
    depths are ranked by artifact presence. ``started_after`` is the launch
    wall-clock time; a 2s tolerance absorbs filesystem timestamp granularity.
    """
    base = Path(root) / "outputs" / scenario
    if not base.is_dir():
        return None

    def fresh(path: Path) -> bool:
        return path.stat().st_mtime >= started_after - 2

    candidates: list[Path] = []
    for job in base.iterdir():
        if not job.is_dir() or not fresh(job):
            continue
        for entry in (job, *job.iterdir()):
            if entry.is_dir() and fresh(entry) and _looks_like_run_dir(entry):
                candidates.append(entry)
    # Newest wins; on an mtime tie the deeper (timestamped leaf) dir wins.
    return max(
        candidates,
        key=lambda entry: (entry.stat().st_mtime, len(entry.parts)),
        default=None,
    )


def tail_action_events(run_dir: str | Path, limit: int = 8) -> dict[str, Any]:
    """Summarize a run's committed action events for the live panel.

    Reads every ``action_events.jsonl`` (flat or per-GM) tolerantly — a
    partially-written trailing line from a still-running simulation is
    skipped, never raised on.
    """
    rows: list[dict[str, Any]] = []
    for path in resolve_action_event_files(Path(run_dir)):
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(row, dict):
                        rows.append(row)
        except OSError:
            continue

    label_counts: dict[str, int] = {}
    agents: set[str] = set()
    max_episode = -1
    for row in rows:
        label_counts[str(row.get("label", "?"))] = (
            label_counts.get(str(row.get("label", "?")), 0) + 1
        )
        source = row.get("source_user")
        if source:
            agents.add(str(source))
        episode = row.get("episode")
        if isinstance(episode, int):
            max_episode = max(max_episode, episode)

    return {
        "total": len(rows),
        "episodes": max_episode + 1,
        "agents": len(agents),
        "label_counts": dict(sorted(label_counts.items(), key=lambda item: -item[1])),
        "recent": rows[-limit:],
    }
