#!/usr/bin/env python3
"""Compute per-active-agent action metrics and significance for clean50x10 runs.

This script reads action_events.jsonl directly (not legacy compare CSV outputs)
for the N50_T10_clean50x10_seedXX runs and computes per-seed metrics for:
- total actions
- posts
- replies
- likes
- reposts
- interactions (reply + like + repost)

All rates are computed as per-active-agent-per-episode:
    metric_count / sum_episode(active_agents_in_episode)

It then runs paired statistics across seeds:
- Omnibus: Friedman test across chronological, twitter, twhin
- Pairwise: Wilcoxon signed-rank tests for the three arm pairs
- Multiple-comparison correction: Holm over the three pairwise tests
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, stdev
from typing import Any

import yaml
from scipy.stats import friedmanchisquare, wilcoxon

SOCIAL_LABELS = ("post", "reply", "like", "repost")
PAIRWISE_COMPARISONS = (
    ("recsys_twitter", "chronological"),
    ("recsys_twhin", "chronological"),
    ("recsys_twhin", "recsys_twitter"),
)


@dataclass(frozen=True)
class RunRef:
    seed: int
    arm: str
    run_dir: Path


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


def _extract_fixed_usernames_from_config(effective_config: dict[str, Any]) -> set[str]:
    fixed_usernames: set[str] = set()
    classes = effective_config.get("scenario", {}).get("persona_pipeline", {}).get("classes", {})
    if not isinstance(classes, dict):
        return fixed_usernames

    for class_cfg in classes.values():
        if not isinstance(class_cfg, dict):
            continue
        class_path = str(class_cfg.get("class_path", ""))
        if "FixedAgent" not in class_path:
            continue

        data_cfg = class_cfg.get("data", {})
        if not isinstance(data_cfg, dict):
            continue

        if data_cfg.get("source") == "inline":
            records = data_cfg.get("records", [])
            if isinstance(records, list):
                for record in records:
                    if isinstance(record, dict) and isinstance(record.get("name"), str):
                        fixed_usernames.add(record["name"])

    return fixed_usernames


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _discover_run_refs(
    outputs_dir: Path,
    run_prefix: str,
    seed_start: int,
    seed_end: int,
) -> list[RunRef]:
    arm_raw_to_norm = {
        "chronological": "chronological",
        "chronological_like_20": "chronological",
        "recsys_twitter_like": "recsys_twitter",
        "recsys_twitter_like_20": "recsys_twitter",
        "recsys_twhin_like": "recsys_twhin",
        "recsys_twhin_like_20": "recsys_twhin",
    }
    pattern = re.compile(
        rf"{re.escape(run_prefix)}seed(\d+)_"
        r"(chronological|chronological_like_20|recsys_twitter_like|recsys_twitter_like_20|recsys_twhin_like|recsys_twhin_like_20)_"
    )

    refs: list[RunRef] = []
    for action_events in outputs_dir.rglob("action_events.jsonl"):
        run_dir = action_events.parent
        match = pattern.search(str(run_dir))
        if not match:
            continue
        seed = int(match.group(1))
        if seed < seed_start or seed > seed_end:
            continue
        arm_raw = match.group(2)
        refs.append(RunRef(seed=seed, arm=arm_raw_to_norm[arm_raw], run_dir=run_dir))

    refs.sort(key=lambda x: (x.seed, x.arm, str(x.run_dir)))
    return refs


def _compute_run_metrics(run_ref: RunRef, exclude_final_episode: bool) -> dict[str, Any]:
    events = _read_jsonl(run_ref.run_dir / "action_events.jsonl")
    effective_config = _load_yaml(run_ref.run_dir / "effective_config.yaml")

    init_users = {
        str(e.get("source_user", ""))
        for e in events
        if e.get("label") == "init_create_user"
        and isinstance(e.get("source_user"), str)
        and e.get("source_user") not in {"", "system"}
    }

    fixed_users = _extract_fixed_usernames_from_config(effective_config)
    eval_users = sorted(u for u in init_users if u not in fixed_users)
    eval_user_set = set(eval_users)

    configured_steps = _safe_int(effective_config.get("sim", {}).get("num_steps"), 0)
    max_episode_seen = max((_safe_int(e.get("episode"), 0) for e in events), default=0)
    total_steps = configured_steps if configured_steps > 0 else max_episode_seen

    if total_steps <= 0:
        step_range: list[int] = []
    elif exclude_final_episode and total_steps > 1:
        step_range = list(range(1, total_steps))
    else:
        step_range = list(range(1, total_steps + 1))

    label_counts: Counter[str] = Counter()
    active_users_by_step: dict[int, set[str]] = defaultdict(set)

    for event in events:
        episode = _safe_int(event.get("episode"), 0)
        if episode < 1:
            continue
        if step_range and episode not in step_range:
            continue

        source_user = str(event.get("source_user", ""))
        if source_user not in eval_user_set:
            continue

        label = str(event.get("label", ""))
        if label in SOCIAL_LABELS:
            label_counts[label] += 1
            active_users_by_step[episode].add(source_user)

    if not step_range:
        step_range = sorted(active_users_by_step.keys())

    active_agent_episodes = sum(len(active_users_by_step.get(step, set())) for step in step_range)
    denom = float(active_agent_episodes) if active_agent_episodes > 0 else 1.0

    post_count = label_counts["post"]
    reply_count = label_counts["reply"]
    like_count = label_counts["like"]
    repost_count = label_counts["repost"]
    interactions_count = reply_count + like_count + repost_count
    total_actions_count = post_count + interactions_count

    return {
        "seed": run_ref.seed,
        "arm": run_ref.arm,
        "run_dir": str(run_ref.run_dir),
        "num_eval_users": len(eval_users),
        "num_fixed_users_excluded": len(fixed_users & init_users),
        "configured_num_steps": total_steps,
        "num_eval_steps": len(step_range),
        "active_agent_episodes": active_agent_episodes,
        "post_count": post_count,
        "reply_count": reply_count,
        "like_count": like_count,
        "repost_count": repost_count,
        "interactions_count": interactions_count,
        "total_actions_count": total_actions_count,
        "posts_per_active_agent_episode": post_count / denom,
        "replies_per_active_agent_episode": reply_count / denom,
        "likes_per_active_agent_episode": like_count / denom,
        "reposts_per_active_agent_episode": repost_count / denom,
        "interactions_per_active_agent_episode": interactions_count / denom,
        "total_actions_per_active_agent_episode": total_actions_count / denom,
    }


def _sig_marker(p: float) -> str:
    if p < 0.001:
        return "***"
    if p < 0.01:
        return "**"
    if p < 0.05:
        return "*"
    return "ns"


def _holm_adjust(rows: list[dict[str, Any]], p_key: str, out_key: str) -> None:
    ordered = sorted(enumerate(rows), key=lambda t: float(t[1][p_key]))
    m = len(rows)
    prev = 0.0
    adjusted = [1.0] * m
    for rank, (idx, row) in enumerate(ordered, start=1):
        p = float(row[p_key])
        adj = min(1.0, (m - rank + 1) * p)
        adj = max(adj, prev)
        prev = adj
        adjusted[idx] = adj
    for i, row in enumerate(rows):
        row[out_key] = adjusted[i]


def _arm_seed_metric_map(
    per_run_rows: list[dict[str, Any]],
    metric: str,
) -> dict[str, dict[int, float]]:
    mapping: dict[str, dict[int, float]] = defaultdict(dict)
    for row in per_run_rows:
        mapping[str(row["arm"])][int(row["seed"])] = float(row[metric])
    return mapping


def _compute_stats(per_run_rows: list[dict[str, Any]], metrics: list[str]) -> dict[str, Any]:
    summary_rows: list[dict[str, Any]] = []
    pairwise_rows: list[dict[str, Any]] = []
    omnibus_rows: list[dict[str, Any]] = []

    arms = ["chronological", "recsys_twitter", "recsys_twhin"]

    for metric in metrics:
        metric_map = _arm_seed_metric_map(per_run_rows, metric)

        for arm in arms:
            vals = [metric_map[arm][s] for s in sorted(metric_map[arm].keys())]
            summary_rows.append(
                {
                    "metric": metric,
                    "arm": arm,
                    "n": len(vals),
                    "mean": mean(vals) if vals else 0.0,
                    "sd": stdev(vals) if len(vals) > 1 else 0.0,
                    "min": min(vals) if vals else 0.0,
                    "max": max(vals) if vals else 0.0,
                }
            )

        common_seeds = sorted(
            set(metric_map["chronological"].keys())
            & set(metric_map["recsys_twitter"].keys())
            & set(metric_map["recsys_twhin"].keys())
        )
        if common_seeds:
            x = [metric_map["chronological"][s] for s in common_seeds]
            y = [metric_map["recsys_twitter"][s] for s in common_seeds]
            z = [metric_map["recsys_twhin"][s] for s in common_seeds]
            stat, p = friedmanchisquare(x, y, z)
            omnibus_rows.append(
                {
                    "metric": metric,
                    "n": len(common_seeds),
                    "friedman_chi2": float(stat),
                    "friedman_p": float(p),
                    "sig": _sig_marker(float(p)),
                    "paired_seeds": common_seeds,
                }
            )
        else:
            omnibus_rows.append(
                {
                    "metric": metric,
                    "n": 0,
                    "friedman_chi2": 0.0,
                    "friedman_p": 1.0,
                    "sig": "ns",
                    "paired_seeds": [],
                }
            )

        metric_pair_rows: list[dict[str, Any]] = []
        for arm_a, arm_b in PAIRWISE_COMPARISONS:
            seeds = sorted(set(metric_map[arm_a].keys()) & set(metric_map[arm_b].keys()))
            vals_a = [metric_map[arm_a][s] for s in seeds]
            vals_b = [metric_map[arm_b][s] for s in seeds]
            deltas = [a - b for a, b in zip(vals_a, vals_b)]

            if seeds and any(abs(d) > 0 for d in deltas):
                stat, p = wilcoxon(vals_a, vals_b, zero_method="wilcox", alternative="two-sided")
                p_value = float(p)
                w_stat = float(stat)
            else:
                p_value = 1.0
                w_stat = 0.0

            metric_pair_rows.append(
                {
                    "metric": metric,
                    "comparison": f"{arm_a}-minus-{arm_b}",
                    "arm_a": arm_a,
                    "arm_b": arm_b,
                    "n": len(seeds),
                    "mean_delta": mean(deltas) if deltas else 0.0,
                    "sd_delta": stdev(deltas) if len(deltas) > 1 else 0.0,
                    "wilcoxon_w": w_stat,
                    "p_raw": p_value,
                    "paired_seeds": seeds,
                }
            )

        _holm_adjust(metric_pair_rows, p_key="p_raw", out_key="p_holm")
        for row in metric_pair_rows:
            row["sig"] = _sig_marker(float(row["p_holm"]))
            pairwise_rows.append(row)

    return {
        "summary": summary_rows,
        "omnibus": omnibus_rows,
        "pairwise": pairwise_rows,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            cooked = dict(row)
            for key, val in list(cooked.items()):
                if isinstance(val, list):
                    cooked[key] = "|".join(str(x) for x in val)
            writer.writerow(cooked)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compute action_events-based metrics and significance for clean50x10 runs"
    )
    parser.add_argument(
        "--outputs-dir",
        default="scenarios/election_recsys_engagement/outputs",
        help="Path to scenario outputs directory",
    )
    parser.add_argument(
        "--run-prefix",
        default="ElectionRecsys_N50_T10_clean50x10_",
        help="Run directory naming prefix",
    )
    parser.add_argument("--seed-start", type=int, default=11)
    parser.add_argument("--seed-end", type=int, default=20)
    parser.add_argument(
        "--exclude-final-episode",
        action="store_true",
        help="If set, drop final configured episode from denominators and counts",
    )
    parser.add_argument(
        "--output-prefix",
        default="n50_t10_clean50x10_action_events_significance",
        help="Output file prefix (without extension) in outputs-dir",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    outputs_dir = Path(args.outputs_dir).expanduser().resolve()

    refs = _discover_run_refs(
        outputs_dir=outputs_dir,
        run_prefix=args.run_prefix,
        seed_start=args.seed_start,
        seed_end=args.seed_end,
    )
    if not refs:
        raise FileNotFoundError("No matching runs found for provided prefix and seed range")

    per_run_rows = [
        _compute_run_metrics(ref, exclude_final_episode=bool(args.exclude_final_episode))
        for ref in refs
    ]

    metrics = [
        "total_actions_per_active_agent_episode",
        "posts_per_active_agent_episode",
        "replies_per_active_agent_episode",
        "likes_per_active_agent_episode",
        "reposts_per_active_agent_episode",
        "interactions_per_active_agent_episode",
    ]
    stats = _compute_stats(per_run_rows, metrics)

    payload = {
        "meta": {
            "outputs_dir": str(outputs_dir),
            "run_prefix": args.run_prefix,
            "seed_start": args.seed_start,
            "seed_end": args.seed_end,
            "exclude_final_episode": bool(args.exclude_final_episode),
            "num_runs": len(per_run_rows),
        },
        "per_run": per_run_rows,
        "summary": stats["summary"],
        "omnibus": stats["omnibus"],
        "pairwise": stats["pairwise"],
    }

    out_json = outputs_dir / f"{args.output_prefix}.json"
    out_summary_csv = outputs_dir / f"{args.output_prefix}_summary.csv"
    out_omnibus_csv = outputs_dir / f"{args.output_prefix}_omnibus.csv"
    out_pairwise_csv = outputs_dir / f"{args.output_prefix}_pairwise.csv"
    out_per_run_csv = outputs_dir / f"{args.output_prefix}_per_run.csv"

    _write_json(out_json, payload)
    _write_csv(
        out_summary_csv,
        stats["summary"],
        ["metric", "arm", "n", "mean", "sd", "min", "max"],
    )
    _write_csv(
        out_omnibus_csv,
        stats["omnibus"],
        ["metric", "n", "friedman_chi2", "friedman_p", "sig", "paired_seeds"],
    )
    _write_csv(
        out_pairwise_csv,
        stats["pairwise"],
        [
            "metric",
            "comparison",
            "arm_a",
            "arm_b",
            "n",
            "mean_delta",
            "sd_delta",
            "wilcoxon_w",
            "p_raw",
            "p_holm",
            "sig",
            "paired_seeds",
        ],
    )
    _write_csv(
        out_per_run_csv,
        per_run_rows,
        [
            "seed",
            "arm",
            "run_dir",
            "num_eval_users",
            "num_fixed_users_excluded",
            "configured_num_steps",
            "num_eval_steps",
            "active_agent_episodes",
            "post_count",
            "reply_count",
            "like_count",
            "repost_count",
            "interactions_count",
            "total_actions_count",
            "posts_per_active_agent_episode",
            "replies_per_active_agent_episode",
            "likes_per_active_agent_episode",
            "reposts_per_active_agent_episode",
            "interactions_per_active_agent_episode",
            "total_actions_per_active_agent_episode",
        ],
    )

    print(f"Wrote JSON: {out_json}")
    print(f"Wrote CSV summary: {out_summary_csv}")
    print(f"Wrote CSV omnibus: {out_omnibus_csv}")
    print(f"Wrote CSV pairwise: {out_pairwise_csv}")
    print(f"Wrote CSV per-run: {out_per_run_csv}")


if __name__ == "__main__":
    main()
