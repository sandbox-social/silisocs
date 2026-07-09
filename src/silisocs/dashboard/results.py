"""Helpers for loading dashboard run results.

All loading goes through the Run Artifact Module (``load_run``), so the
dashboard never rediscovers the on-disk layout itself.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from silisocs.evaluations.run_artifact import load_run

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
    """Load action events, probe events, and metrics for one run directory.

    The artifact streams events (flat or per-GM layouts); the dashboard renders
    whole runs, so materialize here.
    """
    artifact = load_run(run_dir)
    return {
        "actions": list(artifact.iter_actions()),
        "probes": list(artifact.iter_probes()),
        "exposures": list(artifact.iter_exposures()),
        "metrics": artifact.metrics,
    }


def run_history_rows(
    *,
    root: Path,
    limit: int = 60,
    extra_roots: list[Path] | None = None,
) -> list[dict[str, Any]]:
    """One display row per recent run for the Run History view (newest first).

    Manifest-backed runs get status/scenario/cost; legacy runs degrade to the
    fields recoverable from ``sim_metrics.json``.
    """
    rows: list[dict[str, Any]] = []
    for run_dir in discover_result_run_dirs(root=root, limit=limit, extra_roots=extra_roots):
        artifact = load_run(run_dir)
        usage = artifact.llm_usage or {}
        rows.append(
            {
                "run": str(run_dir),
                "status": artifact.status or "unknown",
                "scenario": artifact.scenario,
                "steps": artifact.num_steps,
                "agents": artifact.num_agents,
                "issues": total_health_issues(artifact.health or None),
                "total_tokens": (usage.get("totals") or {}).get("total_tokens"),
                "est_cost_usd": usage.get("estimated_cost_usd"),
                "modified": datetime.fromtimestamp(run_dir.stat().st_mtime).isoformat(
                    sep=" ", timespec="seconds"
                ),
            }
        )
    return rows


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
