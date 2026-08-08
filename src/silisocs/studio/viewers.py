"""Backend-neutral platform-viewer discovery, mounting, and launch planning for Studio."""

from __future__ import annotations

import importlib
import importlib.util
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from silisocs.environments.backends.base import VisualizerSpec
from silisocs.environments.backends.factory import registered_backend_types, resolve_backend_class
from silisocs.evaluations.run_artifact import load_run

# A backend's declared visualizer is a ``VisualizerSpec``. The same shape is
# reconstructable from a run manifest (see ``_manifest_databases``), so a run
# recorded by a backend this Studio cannot import still resolves.
_VISUALIZERS: dict[str, VisualizerSpec] = {}


def _register_backend_visualizer(backend_type: str) -> bool:
    """Resolve a backend's optional visualizer declaration through its capability seam."""
    if backend_type in _VISUALIZERS:
        return True
    if backend_type not in registered_backend_types():
        return False
    spec = getattr(resolve_backend_class(backend_type), "visualizer", None)
    if spec is None:
        return False
    _VISUALIZERS[backend_type] = spec
    return True


def viewer_app_factory(backend_type: str):
    """Return a backend's in-process ASGI viewer factory, or None for subprocess-only.

    Importing the declared factory is the same trust level Studio already takes
    on the sibling ``module`` field, which it executes via ``python -m``.
    """
    if not _register_backend_visualizer(backend_type):
        return None
    reference = _VISUALIZERS[backend_type].app_factory
    if not reference or ":" not in reference:
        return None
    module_name, _, attribute = reference.partition(":")
    try:
        factory = getattr(importlib.import_module(module_name), attribute)
    except (ImportError, AttributeError):
        return None  # Fall back to the subprocess launch plan.
    return factory if callable(factory) else None


def _manifest_databases(root: Path) -> list[tuple[str, Path]]:
    """Load self-describing visualizers and database paths from a run manifest.

    Reads the per-GM layout through ``RunArtifact.game_masters`` (AGENTS.md §12:
    load runs through ``run_artifact``, never rediscover the file layout).
    """
    if not (root / "run_manifest.json").is_file():
        return []
    found: list[tuple[str, Path]] = []
    for gm in load_run(root).game_masters:
        backend_type = str(gm.get("backend_type") or "").strip()
        visualizer = gm.get("visualizer")
        # A manifest only fills in backends this Studio cannot resolve from its
        # own installed classes. The class is the live declaration — a manifest
        # written by an older version records what that version could do, and
        # letting it win would pin every past run to the stale capability.
        if (
            backend_type
            and isinstance(visualizer, dict)
            and not _register_backend_visualizer(backend_type)
        ):
            env_var = str(visualizer.get("env_var") or "").strip()
            module = str(visualizer.get("module") or "").strip()
            default_port = visualizer.get("default_port")
            if env_var and module and isinstance(default_port, int):
                _VISUALIZERS[backend_type] = VisualizerSpec(
                    env_var=env_var,
                    module=module,
                    default_port=default_port,
                    port_env=str(visualizer.get("port_env") or "SILISOCS_VIEWER_PORT"),
                    app_factory=str(visualizer.get("app_factory") or "") or None,
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
    capability = _VISUALIZERS[backend_type]
    selected_port = port or capability.default_port
    env = {capability.env_var: str(db_path)}
    if port is not None:
        env[capability.port_env] = str(port)
    missing = None
    if any(importlib.util.find_spec(name) is None for name in ("fastapi", "uvicorn")):
        missing = "studio"
    return LaunchPlan(
        cmd=[sys.executable, "-m", capability.module],
        env=env,
        url=f"http://127.0.0.1:{selected_port}",
        missing_extra=missing,
    )


def viewer_mount_path(run_id: str, backend_type: str) -> str:
    """The same-origin URL Studio serves a backend's in-process viewer at."""
    run_segments = "/".join(quote(part) for part in run_id.split("/") if part)
    return f"/viewers/{run_segments}/{quote(backend_type)}/"


async def _send_plain(send, status: int, body: bytes, headers: list[tuple[bytes, bytes]]) -> None:
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def _route_path(scope) -> str:
    """The request path relative to what has already been mounted.

    ASGI keeps ``path`` absolute and tracks consumed prefixes in ``root_path``;
    this is the same subtraction Starlette's own routing performs.
    """
    path = scope.get("path", "")
    root_path = scope.get("root_path", "")
    if not root_path or not path.startswith(root_path):
        return path
    if path == root_path:
        return ""
    return path[len(root_path) :] if path[len(root_path)] == "/" else path


class ViewerDispatch:
    """ASGI sub-app serving backend platform viewers inside the Studio process.

    Mounted once at ``/viewers``; every request resolves a run + backend, builds
    that viewer's ASGI app on first use, and forwards the scope to it. Serving
    in-process is what makes viewers instant: a subprocess would re-import the
    whole web stack Studio already has loaded (~100s cold on a networked venv)
    while the page waits on a port that has not been bound yet.
    """

    def __init__(self, output_root: str | Path) -> None:
        self._root = Path(output_root)
        self._lock = threading.Lock()
        self._apps: dict[tuple[str, str], Any] = {}
        # Every forwarded request — including each iframe asset — otherwise
        # re-runs find_run + find_backend_dbs (manifest reads + globs). A run's
        # segment->viewer mapping is fixed once its viewer exists, so memoize the
        # successful resolution; unresolved paths are left uncached so a viewer
        # that is not ready yet keeps being retried.
        self._resolutions: dict[tuple[str, ...], tuple[Any, int]] = {}
        self._resolve_lock = threading.Lock()

    def _viewer_for(self, backend_type: str, db_path: Path):
        """Return the cached viewer app for one database, building it once."""
        key = (backend_type, str(db_path))
        with self._lock:
            app = self._apps.get(key)
            if app is None:
                factory = viewer_app_factory(backend_type)
                if factory is None:
                    return None
                app = self._apps[key] = factory(str(db_path))
            return app

    def _resolve(self, segments: list[str]):
        """Split ``<run_id...>/<backend_type>/<rest...>`` into (viewer, prefix length).

        Run ids contain slashes, so the boundary is found by scanning: the first
        segment naming a backend whose viewer this run actually has wins. The
        run/db checks keep it unambiguous even for a scenario named after a
        backend.
        """
        from silisocs.studio.catalog import find_run

        key = tuple(segments)
        with self._resolve_lock:
            cached = self._resolutions.get(key)
        if cached is not None:
            return cached

        # Two passes: the first short-circuits on the cheap factory check (the
        # common case, and what keeps unresolvable asset paths from paying
        # catalog scans per segment); the second drops that pre-check so
        # find_backend_dbs can register a MANIFEST-declared visualizer for a
        # backend this process cannot import — the cold-process deep link.
        for factory_first in (True, False):
            for index in range(1, len(segments)):
                backend_type = segments[index]
                if factory_first and viewer_app_factory(backend_type) is None:
                    continue
                try:
                    record = find_run(self._root, "/".join(segments[:index]))
                except (KeyError, FileNotFoundError, ValueError):
                    # Not a run at this prefix: unknown id, a directory with no
                    # manifest (reachable now that this pass runs for segments
                    # that are not known backend names), or a malformed one.
                    continue
                db_path = next(
                    (
                        path
                        for found, path in find_backend_dbs(record.path)
                        if found == backend_type
                    ),
                    None,
                )
                if db_path is None or viewer_app_factory(backend_type) is None:
                    continue
                viewer = self._viewer_for(backend_type, db_path)
                if viewer is not None:
                    resolved = (viewer, index + 1)
                    with self._resolve_lock:
                        self._resolutions[key] = resolved
                    return resolved
        return None

    def close(self) -> None:
        """Release every mounted viewer's database connection."""
        with self._lock:
            for app in self._apps.values():
                closer = getattr(getattr(app, "state", None), "close_viewer", None)
                if callable(closer):
                    closer()
            self._apps.clear()
        with self._resolve_lock:
            self._resolutions.clear()

    async def __call__(self, scope, receive, send) -> None:
        """Forward a request to its viewer, rebased under the mount prefix."""
        route_path = _route_path(scope)
        segments = [segment for segment in route_path.split("/") if segment]
        resolved = self._resolve(segments)
        if resolved is None:
            await _send_plain(
                send, 404, b"No platform viewer for this run", [(b"content-type", b"text/plain")]
            )
            return
        viewer, consumed = resolved
        prefix = "/" + "/".join(segments[:consumed])
        if route_path.rstrip("/") == prefix.rstrip("/") and not route_path.endswith("/"):
            # The viewer's pages address their assets relatively, so without the
            # trailing slash every one of them would resolve a level too high.
            query = scope.get("query_string", b"")
            location = scope.get("path", "") + "/"
            if query:
                location = f"{location}?{query.decode('latin-1')}"
            await _send_plain(send, 307, b"", [(b"location", location.encode("latin-1"))])
            return
        # ASGI convention: the path stays absolute and the consumed prefix moves
        # into root_path, which is what the viewer's own routing subtracts.
        child = dict(scope)
        child["root_path"] = scope.get("root_path", "") + prefix
        await viewer(child, receive, send)


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
