#!/usr/bin/env python3
"""Misinformation-spread evaluator for the CTA-framing demo study.

Reads a run's committed action events and probe events and emits per-run
scalars into ``aggregated`` — the channel the study runner averages across
replicate seeds (n/mean/stdev/CI95) into ``generated/organized/summary.json``,
which feeds the Studio condition-comparison panel.

Metrics (all in ``aggregated``):

* ``repost_count`` / ``reply_count`` / ``post_count`` / ``like_count`` — the
  committed action mix (the reactive framing predicts more reposts/likes, the
  deliberate framing more replies).
* ``amplification_share`` — reach-extending actions (repost + like) as a share
  of all user content actions; the study's headline spread measure.
* ``believed_claim_rate`` — fraction of agents answering yes to the
  ``believed_claim`` probe at the last probed step.
* ``shared_misinfo_rate`` — same for the ``shared_misinfo`` probe.
* ``source_trust_mean`` — mean 1-10 ``source_trust`` rating at the last
  probed step.

Deterministic, no LLM calls, stdlib + silisocs loaders only.

Usage (the study-runner contract):
    python eval.py --run-dir <run_dir> --output <eval.json>
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from silisocs.evaluations.action_events import resolve_action_event_files
from silisocs.evaluations.run_artifact import iter_jsonl

CONTENT_LABELS = ("post", "reply", "repost", "like")
AMPLIFY_LABELS = ("repost", "like")
BINARY_PROBES = ("believed_claim", "shared_misinfo")
RATING_PROBE = "source_trust"


def _probe_rows(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "probe_events.jsonl"
    if not path.exists():
        return []
    return [row for row in iter_jsonl([path]) if row.get("event_type") == "probe"]


def _final_step_answers(rows: list[dict[str, Any]], probe: str) -> list[str]:
    """Each agent's answer to ``probe`` at the last episode that probed them."""
    latest: dict[str, tuple[int, str]] = {}
    for row in rows:
        if row.get("label") != probe:
            continue
        agent = str(row.get("source_user") or "")
        episode = int(row.get("episode") or 0)
        answer = str((row.get("data") or {}).get("probe_return") or "")
        if agent and (agent not in latest or episode >= latest[agent][0]):
            latest[agent] = (episode, answer)
    return [answer for _, answer in latest.values()]


def _yes_rate(answers: list[str]) -> float | None:
    votes = [answer.strip().lower().startswith("y") for answer in answers if answer.strip()]
    return (sum(votes) / len(votes)) if votes else None


def _mean_rating(answers: list[str]) -> float | None:
    values = []
    for answer in answers:
        try:
            values.append(float(answer))
        except (TypeError, ValueError):
            continue
    return (sum(values) / len(values)) if values else None


def evaluate_run_dir(run_dir: Path) -> dict[str, Any]:
    """Compute spread/belief scalars for one run directory."""
    action_rows = [
        row
        for row in iter_jsonl(resolve_action_event_files(run_dir))
        if row.get("event_type") == "action"
    ]
    label_counts = Counter(str(row.get("label")) for row in action_rows)
    content_total = sum(label_counts[label] for label in CONTENT_LABELS)
    amplify_total = sum(label_counts[label] for label in AMPLIFY_LABELS)

    per_agent: dict[str, dict[str, float]] = defaultdict(dict)
    for row in action_rows:
        label = str(row.get("label"))
        agent = str(row.get("source_user") or "")
        if agent and label in CONTENT_LABELS:
            per_agent[agent][label] = per_agent[agent].get(label, 0.0) + 1.0

    probe_rows = _probe_rows(run_dir)
    aggregated: dict[str, float | None] = {
        f"{label}_count": float(label_counts[label]) for label in CONTENT_LABELS
    }
    aggregated["amplification_share"] = (amplify_total / content_total) if content_total else None
    aggregated["believed_claim_rate"] = _yes_rate(_final_step_answers(probe_rows, "believed_claim"))
    aggregated["shared_misinfo_rate"] = _yes_rate(_final_step_answers(probe_rows, "shared_misinfo"))
    aggregated["source_trust_mean"] = _mean_rating(_final_step_answers(probe_rows, RATING_PROBE))

    return {
        "source": str(run_dir),
        "aggregated": aggregated,
        "agents": dict(per_agent),
        "summary": {
            "action_events": len(action_rows),
            "content_actions": content_total,
            "probe_events": len(probe_rows),
        },
    }


def main() -> None:
    """Parse ``--run-dir``/``--output`` and write the eval JSON."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = evaluate_run_dir(Path(args.run_dir))
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
