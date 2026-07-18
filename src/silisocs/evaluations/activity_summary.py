"""Compute lightweight per-run summaries.

Modes:
- activity: action label counts and basic run coverage (reads action_events.jsonl)
- probes: probe label coverage and response counts (reads probe_events.jsonl)
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from silisocs.evaluations.action_events import resolve_action_event_files
from silisocs.evaluations.run_artifact import iter_jsonl

VALID_MODES = {"activity", "probes"}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _summarize_activity(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a summary of action labels, users, and episodes from action events."""
    labels = Counter(str(e.get("label", "")) for e in events if e.get("label") is not None)

    def _agent_event(event: dict[str, Any]) -> bool:
        source = str(event.get("source_user", "")).strip()
        return bool(source) and source.lower() != "system"

    # Count every label an agent committed. Matching against a fixed vocabulary
    # would report an empty breakdown for any backend that isn't social.
    action_counts = {
        label: int(count)
        for label, count in Counter(
            str(e.get("label", ""))
            for e in events
            if e.get("label") is not None and _agent_event(e)
        ).most_common()
    }

    users = sorted({str(e.get("source_user", "")).strip() for e in events if _agent_event(e)})

    episodes = sorted(
        {_safe_int(e.get("episode"), 0) for e in events if _safe_int(e.get("episode"), 0) > 0}
    )

    return {
        "summary_type": "activity",
        "total_events": len(events),
        "unique_users": users,
        "num_unique_users": len(users),
        "episodes_seen": episodes,
        "num_episodes_seen": len(episodes),
        "action_counts": action_counts,
        "top_labels": labels.most_common(20),
    }


def _summarize_probes(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a summary of probe labels and responses from probe events."""
    probe_rows = [e for e in events if str(e.get("event_type", "")).strip() == "probe"]
    probe_labels = Counter(str(e.get("label", "")) for e in probe_rows)

    responses_present = 0
    for row in probe_rows:
        data = row.get("data")
        if isinstance(data, dict) and "probe_return" in data:
            responses_present += 1

    episodes = sorted(
        {_safe_int(e.get("episode"), 0) for e in probe_rows if _safe_int(e.get("episode"), 0) > 0}
    )

    return {
        "summary_type": "probes",
        "total_probe_events": len(probe_rows),
        "probe_labels": dict(probe_labels),
        "probe_episodes": episodes,
        "responses_present": responses_present,
    }


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Summarize one run directory's action events")
    parser.add_argument("--run-dir", required=True, help="Path to simulation output run directory")
    parser.add_argument("--output", required=True, help="Path to write summary JSON")
    parser.add_argument(
        "--mode",
        default="activity",
        choices=sorted(VALID_MODES),
        help="Summary mode",
    )
    return parser.parse_args()


def main() -> None:
    """Run evaluator and write summary JSON."""
    args = parse_args()
    run_dir = Path(args.run_dir).expanduser().resolve()
    out_path = Path(args.output).expanduser().resolve()

    if args.mode == "probes":
        event_files = [run_dir / "probe_events.jsonl"]
    else:
        # Multi-GM runs isolate action logs under per-GM subdirectories.
        event_files = resolve_action_event_files(run_dir)
    event_files = [path for path in event_files if path.is_file()]
    if not event_files:
        raise FileNotFoundError(f"Missing events file(s) for mode {args.mode!r} in {run_dir}")

    # Tolerant shared reader (skips blank/malformed lines), per the run-artifact contract.
    events: list[dict[str, Any]] = list(iter_jsonl(event_files))
    payload = _summarize_activity(events) if args.mode == "activity" else _summarize_probes(events)

    payload["run_dir"] = str(run_dir)
    payload["source_file"] = ", ".join(str(path) for path in event_files)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
