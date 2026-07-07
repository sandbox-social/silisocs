"""Helpers for loading dashboard run results."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)[:limit]


def load_run_results(run_dir: Path | str) -> dict[str, Any]:
    """Load action events, probe events, and metrics for one run directory."""
    path = Path(run_dir)
    out: dict[str, Any] = {"actions": [], "probes": [], "metrics": None}
    # _read_jsonl streams; the dashboard renders whole runs, so materialize here.
    if (path / "action_events.jsonl").is_file():
        out["actions"] = list(_read_jsonl(path / "action_events.jsonl"))
    if (path / "probe_events.jsonl").is_file():
        out["probes"] = list(_read_jsonl(path / "probe_events.jsonl"))
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


def total_health_issues(summary: dict[str, int] | None) -> int:
    """Return total degraded-run issue count from a normalized summary."""
    return sum(summary.values()) if summary else 0
