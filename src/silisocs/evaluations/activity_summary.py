"""Compute lightweight per-run summaries from action_events.jsonl.

Modes:
- activity: action label counts and basic run coverage
- probes: probe label coverage and response counts
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

VALID_MODES = {"activity", "probes"}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """_read_jsonl.

    :param Path path:
    :type path: Path

    :returns: list[dict[str, Any]]
    :rtype: list[dict[str, Any]]
    """
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _safe_int(value: Any, default: int = 0) -> int:
    """_safe_int.

    :param Any value:
    :type value: Any
    :param int default:
    :type default: int

    :returns: int
    :rtype: int
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _summarize_activity(events: list[dict[str, Any]]) -> dict[str, Any]:
    """_summarize_activity.

    :param list[dict[str, Any]] events:
    :type events: list[dict[str, Any]]

    :returns: dict[str, Any]
    :rtype: dict[str, Any]
    """
    labels = Counter(str(e.get("label", "")) for e in events if e.get("label") is not None)
    action_like = {
        "post",
        "reply",
        "like",
        "repost",
        "follow",
        "unfollow",
        "inner_actions",
    }
    action_counts = {k: int(v) for k, v in labels.items() if k in action_like}

    users = sorted(
        {
            str(e.get("source_user", "")).strip()
            for e in events
            if str(e.get("source_user", "")).strip()
            and str(e.get("source_user", "")).strip().lower() != "system"
        }
    )

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
    """_summarize_probes.

    :param list[dict[str, Any]] events:
    :type events: list[dict[str, Any]]

    :returns: dict[str, Any]
    :rtype: dict[str, Any]
    """
    probe_rows = [e for e in events if str(e.get("event_type", "")).strip() == "probe"]
    probe_labels = Counter(str(e.get("label", "")) for e in probe_rows)

    responses_present = 0
    for row in probe_rows:
        data = row.get("data")
        if isinstance(data, dict) and "query_return" in data:
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

    events_path = run_dir / "action_events.jsonl"
    if not events_path.is_file():
        raise FileNotFoundError(f"Missing action events file: {events_path}")

    events = _read_jsonl(events_path)
    payload = _summarize_activity(events) if args.mode == "activity" else _summarize_probes(events)

    payload["run_dir"] = str(run_dir)
    payload["source_file"] = str(events_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
