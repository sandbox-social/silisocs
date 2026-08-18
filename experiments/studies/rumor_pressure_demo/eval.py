#!/usr/bin/env python3
"""Per-run evaluator: how far the false claim travelled in one run.

The contract (docs/study_schema.md -> "The eval.py Contract"): read
``--run-dir``, write ``--output``, exit 0. Only the flat numbers under
``aggregated`` reach the cross-condition surface -- ``summary.json``'s
``metrics_by_condition`` / ``metrics_stats_by_condition``.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from silisocs.evaluations.run_artifact import load_run

# Committed action labels this backend logs, mapped to metric names.
SPREAD = {"post": "posts", "reply": "replies", "repost": "shares", "like": "likes"}


def evaluate_run_dir(run_dir: Path) -> dict[str, Any]:
    """Count one run's committed spread actions into flat scalars."""
    run = load_run(run_dir)  # manifest-first loader; never guess the file layout
    actions = [r for r in run.actions if r.get("event_type") == "action"]
    # episode > 0 drops world setup (init_create_user, init_follow, ...).
    counts = Counter(str(r["label"]) for r in actions if (r.get("episode") or 0) > 0)
    total = sum(counts[label] for label in SPREAD)
    reach = counts["repost"] + counts["like"]  # actions that extend reach
    agents = max(int(run.num_agents or 0), 1)

    # Only flat numbers under "aggregated" are compared across conditions.
    aggregated = {f"{name}_total": float(counts[label]) for label, name in SPREAD.items()}
    aggregated["shares_per_agent"] = counts["repost"] / agents
    aggregated["spread_actions_per_agent"] = total / agents
    aggregated["amplification_share"] = reach / total if total else None
    return {
        "source": str(run_dir),
        "aggregated": aggregated,
        "summary": {"agents": agents, "steps": run.num_steps, "spread_actions": total},
    }


def main() -> None:
    """Parse ``--run-dir``/``--output`` and write the eval JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(evaluate_run_dir(Path(args.run_dir)), indent=2), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
