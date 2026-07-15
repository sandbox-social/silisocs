"""Backend-neutral platform-viewer discovery and launch planning for Studio."""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from silisocs.environments.backends.factory import registered_backend_types, resolve_backend_class

_VISUALIZERS: dict[str, tuple[str, str, int, str]] = {}


def _register_backend_visualizer(backend_type: str) -> bool:
    """Resolve a backend's optional visualizer declaration through its capability seam."""
    if backend_type in _VISUALIZERS:
        return True
    if backend_type not in registered_backend_types():
        return False
    spec = getattr(resolve_backend_class(backend_type), "visualizer", None)
    if spec is None:
        return False
    _VISUALIZERS[backend_type] = (
        spec.env_var,
        spec.module,
        spec.default_port,
        spec.port_env,
    )
    return True


def _manifest_databases(root: Path) -> list[tuple[str, Path]]:
    """Load self-describing visualizers and database paths from a run manifest."""
    try:
        manifest = json.loads((root / "run_manifest.json").read_text(encoding="utf-8"))
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
            default_port = visualizer.get("default_port")
            if env_var and module and isinstance(default_port, int):
                _VISUALIZERS[backend_type] = (
                    env_var,
                    module,
                    default_port,
                    str(visualizer.get("port_env") or "SILISOCS_VIEWER_PORT"),
                )
        database = gm.get("database")
        if not backend_type or not isinstance(database, str) or not database:
            continue
        path = Path(database)
        path = path if path.is_absolute() else root / path
        if path.is_file() and (backend_type, path) not in found:
            found.append((backend_type, path))
    return found


@dataclass(frozen=True)
class LaunchPlan:
    """Everything the generic job manager needs to start a platform viewer."""

    cmd: list[str]
    env: dict[str, str]
    url: str
    missing_extra: str | None = None


def find_backend_dbs(run_dir: str | Path) -> list[tuple[str, Path]]:
    """Discover visualizable databases in flat, multi-GM, or manifest layouts."""
    root = Path(run_dir)
    if not root.is_dir():
        return []
    found = _manifest_databases(root)
    for pattern in ("*.db", "*/*.db"):
        for path in sorted(root.glob(pattern)):
            if _register_backend_visualizer(path.stem) and (path.stem, path) not in found:
                found.append((path.stem, path))
    return found


def visualizer_plan(
    backend_type: str,
    db_path: str | Path,
    *,
    port: int | None = None,
) -> LaunchPlan:
    """Build a launch plan solely from a backend's declared visualizer capability."""
    if not _register_backend_visualizer(backend_type):
        raise KeyError(f"Backend {backend_type!r} does not declare a visualizer")
    env_var, module, default_port, port_env = _VISUALIZERS[backend_type]
    selected_port = port or default_port
    env = {env_var: str(db_path)}
    if port is not None:
        env[port_env] = str(port)
    missing = None
    if any(importlib.util.find_spec(name) is None for name in ("fastapi", "uvicorn")):
        missing = "studio"
    return LaunchPlan(
        cmd=[sys.executable, "-m", module],
        env=env,
        url=f"http://localhost:{selected_port}",
        missing_extra=missing,
    )


_RUN_ARTIFACT_NAMES = (
    "effective_config.yaml",
    "run_manifest.json",
    "sim_metrics.json",
    "action_events.jsonl",
)


def _looks_like_run_dir(path: Path) -> bool:
    return any((path / name).is_file() for name in _RUN_ARTIFACT_NAMES) or bool(
        find_backend_dbs(path)
    )


def discover_run_dir(scenario: str, started_after: float, root: str | Path = ".") -> Path | None:
    """Find the newest fresh artifact directory for an externally inherited job."""
    base = Path(root) / "outputs" / scenario
    if not base.is_dir():
        return None

    def fresh(path: Path) -> bool:
        return path.stat().st_mtime >= started_after - 2

    candidates = [
        entry
        for job in base.iterdir()
        if job.is_dir() and fresh(job)
        for entry in (job, *job.iterdir())
        if entry.is_dir() and fresh(entry) and _looks_like_run_dir(entry)
    ]
    return max(
        candidates,
        key=lambda entry: (entry.stat().st_mtime, len(entry.parts)),
        default=None,
    )
