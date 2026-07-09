"""Helpers for loading dashboard run results."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from silisocs.evaluations.action_events import (
    resolve_action_event_files,
    resolve_exposure_event_files,
    resolve_probe_event_files,
)
from silisocs.evaluations.default_evaluators import _read_jsonl

HEALTH_COUNTERS = (
    "agent_turn_failures",
    "action_parse_failures",
    "action_invalid_targets",
    "backend_action_errors",
)


def repo_root(start: Path | None = None) -> Path:
    """Locate the repository root, falling back to cwd."""
    here = (start or Path(__file__).resolve()).resolve()
    candidates = (here, *here.parents)
    for parent in candidates:
        if (parent / "pyproject.toml").is_file():
            return parent
    return Path.cwd()


def discover_result_run_dirs(
    *,
    root: Path,
    limit: int = 60,
    extra_roots: list[Path] | None = None,
) -> list[Path]:
    """Find run output directories that hold viewable results."""
    roots = [
        root / "outputs",
        root / "experiments" / "studies",
        root / "scenarios",
    ]
    roots.extend(extra_roots or [])
    found: set[Path] = set()
    for candidate_root in roots:
        if not candidate_root.is_dir():
            continue
        for marker in ("action_events.jsonl", "sim_metrics.json"):
            for path in candidate_root.rglob(marker):
                found.add(path.parent)
    # Multi-GM runs keep per-GM logs in <run>/<gm_name>/ subdirectories; the run
    # root (which holds sim_metrics.json) is the run, not each GM subdir.
    runs = [path for path in found if not any(parent in found for parent in path.parents)]
    return sorted(runs, key=lambda p: p.stat().st_mtime, reverse=True)[:limit]


def load_run_results(run_dir: Path | str) -> dict[str, Any]:
    """Load action events, probe events, and metrics for one run directory."""
    path = Path(run_dir)
    out: dict[str, Any] = {"actions": [], "probes": [], "exposures": [], "metrics": None}
    # _read_jsonl streams; the dashboard renders whole runs, so materialize here.
    # The resolvers cover both the flat single-GM layout and the per-GM
    # <run>/<gm_name>/ multi-GM layout.
    for key, resolver in (
        ("actions", resolve_action_event_files),
        ("probes", resolve_probe_event_files),
        ("exposures", resolve_exposure_event_files),
    ):
        out[key] = [row for file in resolver(path) for row in _read_jsonl(file)]
    if (path / "sim_metrics.json").is_file():
        try:
            out["metrics"] = json.loads((path / "sim_metrics.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            out["metrics"] = None
    return out


def health_counter_summary(metrics: Any) -> dict[str, int] | None:
    """Return normalized degraded-run counters, or None when unavailable."""
    if not isinstance(metrics, dict):
        return None
    counters = metrics.get("counters")
    if not isinstance(counters, dict):
        return None
    return {key: int(counters.get(key, 0) or 0) for key in HEALTH_COUNTERS}


def usage_summary(metrics: Any) -> dict[str, Any] | None:
    """Return the run-level LLM token/cost summary from sim_metrics, or None."""
    if not isinstance(metrics, dict):
        return None
    meta = metrics.get("meta")
    usage = meta.get("llm_usage") if isinstance(meta, dict) else None
    return usage if isinstance(usage, dict) else None


def total_health_issues(summary: dict[str, int] | None) -> int:
    """Return total degraded-run issue count from a normalized summary."""
    return sum(summary.values()) if summary else 0
