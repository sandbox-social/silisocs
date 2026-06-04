"""Aggregate per-persona action behavior across seeds and plot distributions.

This script computes per-agent action rates using ONLY the episodes where that
agent is active (has >=1 social action), then aggregates those rates across seeds
for each arm.

For each run and agent:
    per_agent_rate = action_count / active_episode_count

For each arm and agent aggregated across seeds:
    cross_seed_rate = sum(action_count across seeds) / sum(active_episode_count across seeds)

This honors the active-agent normalization rule both within and across runs.
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

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import yaml

SOCIAL_LABELS = ("post", "reply", "like", "repost")


@dataclass(frozen=True)
class RunRef:
    seed: int
    arm: str
    run_dir: Path


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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
    classes = effective_config.get("agents", {}).get("persona_pipeline", {}).get("classes", {})
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


def _discover_run_refs(
    outputs_dir: Path,
    run_prefix: str,
    seed_start: int,
    seed_end: int,
    setting_filter: str,
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

        run_dir_s = str(run_dir)
        if setting_filter == "like20" and "_like_20_" not in run_dir_s:
            continue
        if setting_filter == "baseline12" and "_like_20_" in run_dir_s:
            continue

        arm_raw = match.group(2)
        refs.append(RunRef(seed=seed, arm=arm_raw_to_norm[arm_raw], run_dir=run_dir))

    refs.sort(key=lambda x: (x.seed, x.arm, str(x.run_dir)))
    return refs


def _iter_agent_episode_counts(
    run_ref: RunRef,
    exclude_final_episode: bool,
) -> list[dict[str, Any]]:
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
    eval_users = set(u for u in init_users if u not in fixed_users)

    configured_steps = _safe_int(effective_config.get("sim", {}).get("num_steps"), 0)
    max_episode_seen = max((_safe_int(e.get("episode"), 0) for e in events), default=0)
    total_steps = configured_steps if configured_steps > 0 else max_episode_seen

    if total_steps <= 0:
        valid_steps: set[int] = set()
    elif exclude_final_episode and total_steps > 1:
        valid_steps = set(range(1, total_steps))
    else:
        valid_steps = set(range(1, total_steps + 1))

    by_agent_episode_labels: dict[str, dict[int, Counter[str]]] = defaultdict(
        lambda: defaultdict(Counter)
    )

    for event in events:
        label = str(event.get("label", ""))
        if label not in SOCIAL_LABELS:
            continue

        episode = _safe_int(event.get("episode"), 0)
        if episode < 1:
            continue
        if valid_steps and episode not in valid_steps:
            continue

        source_user = str(event.get("source_user", ""))
        if source_user not in eval_users:
            continue

        by_agent_episode_labels[source_user][episode][label] += 1

    rows: list[dict[str, Any]] = []
    for agent_name, episode_map in by_agent_episode_labels.items():
        active_episodes = sorted(episode_map.keys())
        active_episode_count = len(active_episodes)
        if active_episode_count <= 0:
            continue

        post_count = sum(episode_map[ep]["post"] for ep in active_episodes)
        reply_count = sum(episode_map[ep]["reply"] for ep in active_episodes)
        like_count = sum(episode_map[ep]["like"] for ep in active_episodes)
        repost_count = sum(episode_map[ep]["repost"] for ep in active_episodes)

        interactions_count = reply_count + like_count + repost_count
        total_actions_count = post_count + interactions_count

        denom = float(active_episode_count)
        rows.append(
            {
                "seed": run_ref.seed,
                "arm": run_ref.arm,
                "run_dir": str(run_ref.run_dir),
                "agent": agent_name,
                "active_episode_count": active_episode_count,
                "post_count": post_count,
                "reply_count": reply_count,
                "like_count": like_count,
                "repost_count": repost_count,
                "interactions_count": interactions_count,
                "total_actions_count": total_actions_count,
                "posts_per_active_episode": post_count / denom,
                "replies_per_active_episode": reply_count / denom,
                "likes_per_active_episode": like_count / denom,
                "reposts_per_active_episode": repost_count / denom,
                "interactions_per_active_episode": interactions_count / denom,
                "total_actions_per_active_episode": total_actions_count / denom,
            }
        )

    return rows


def _quantile(sorted_vals: list[float], q: float) -> float:
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    idx = (n - 1) * q
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def _summarize_distribution(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    s = sorted(values)
    result: dict[str, float] = {
        "n": float(len(values)),
        "mean": float(mean(values)),
        "min": float(s[0]),
        "max": float(s[-1]),
        "p10": float(_quantile(s, 0.10)),
        "p25": float(_quantile(s, 0.25)),
        "p50": float(_quantile(s, 0.50)),
        "p75": float(_quantile(s, 0.75)),
        "p90": float(_quantile(s, 0.90)),
    }
    if len(values) > 1:
        result["std"] = float(stdev(values))
    else:
        result["std"] = 0.0
    return result


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["empty"])
        return

    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _plot_hist_by_arm(
    per_persona_rows: list[dict[str, Any]],
    metric_key: str,
    out_path: Path,
    title: str,
) -> None:
    arm_to_values: dict[str, list[float]] = defaultdict(list)
    for row in per_persona_rows:
        arm_to_values[str(row["arm"])].append(float(row[metric_key]))

    plt.figure(figsize=(10, 6))
    bins = 20
    for arm in ("chronological", "recsys_twitter", "recsys_twhin"):
        vals = arm_to_values.get(arm, [])
        if not vals:
            continue
        plt.hist(vals, bins=bins, alpha=0.35, density=True, label=f"{arm} (n={len(vals)})")

    plt.title(title)
    plt.xlabel(metric_key)
    plt.ylabel("density")
    plt.legend()
    plt.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_path, dpi=180)
    plt.close()


def _plot_box_by_arm(
    per_persona_rows: list[dict[str, Any]],
    metric_key: str,
    out_path: Path,
    title: str,
) -> None:
    labels = ["chronological", "recsys_twitter", "recsys_twhin"]
    data = []
    for arm in labels:
        vals = [float(r[metric_key]) for r in per_persona_rows if str(r["arm"]) == arm]
        data.append(vals)

    plt.figure(figsize=(9, 6))
    plt.boxplot(data, tick_labels=labels, showfliers=True)
    plt.title(title)
    plt.ylabel(metric_key)
    plt.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_path, dpi=180)
    plt.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--outputs-dir",
        type=Path,
        default=Path("worlds/election_recsys_engagement/outputs"),
        help="Directory containing run folders and action_events.jsonl files.",
    )
    parser.add_argument(
        "--run-prefix",
        type=str,
        default="ElectionRecsys_N50_T10_clean50x10_",
        help="Common run directory prefix before seed segment.",
    )
    parser.add_argument("--seed-start", type=int, default=11)
    parser.add_argument("--seed-end", type=int, default=20)
    parser.add_argument(
        "--setting-filter",
        choices=("all", "baseline12", "like20"),
        default="like20",
        help="Filter runs by naming convention: like20 for max_actions=20 runs.",
    )
    parser.add_argument(
        "--exclude-final-episode",
        action="store_true",
        default=True,
        help="Exclude final configured episode (episode 10 in 10-step evaluations).",
    )
    parser.add_argument(
        "--output-prefix",
        type=str,
        default="n50_t10_clean50x10_like20_agent_distribution_seeds11_20_excl_ep10",
        help="Prefix for output files under outputs-dir.",
    )
    args = parser.parse_args()

    refs = _discover_run_refs(
        outputs_dir=args.outputs_dir,
        run_prefix=args.run_prefix,
        seed_start=args.seed_start,
        seed_end=args.seed_end,
        setting_filter=args.setting_filter,
    )

    if not refs:
        raise SystemExit("No matching runs found. Check prefix/filter/seed range.")

    # 1) Per-run, per-agent rows (active-episode normalized)
    per_run_agent_rows: list[dict[str, Any]] = []
    for ref in refs:
        per_run_agent_rows.extend(
            _iter_agent_episode_counts(ref, exclude_final_episode=args.exclude_final_episode)
        )

    # 2) Aggregate across seeds by (arm, agent), weighted by active episodes
    agg: dict[tuple[str, str], dict[str, Any]] = {}
    for row in per_run_agent_rows:
        key = (str(row["arm"]), str(row["agent"]))
        current = agg.get(key)
        if current is None:
            current = {
                "arm": row["arm"],
                "agent": row["agent"],
                "seeds_observed": set(),
                "sum_active_episodes": 0,
                "sum_post_count": 0,
                "sum_reply_count": 0,
                "sum_like_count": 0,
                "sum_repost_count": 0,
                "sum_interactions_count": 0,
                "sum_total_actions_count": 0,
            }
            agg[key] = current

        current["seeds_observed"].add(int(row["seed"]))
        current["sum_active_episodes"] += int(row["active_episode_count"])
        current["sum_post_count"] += int(row["post_count"])
        current["sum_reply_count"] += int(row["reply_count"])
        current["sum_like_count"] += int(row["like_count"])
        current["sum_repost_count"] += int(row["repost_count"])
        current["sum_interactions_count"] += int(row["interactions_count"])
        current["sum_total_actions_count"] += int(row["total_actions_count"])

    per_persona_rows: list[dict[str, Any]] = []
    for row in agg.values():
        denom = float(max(1, int(row["sum_active_episodes"])))
        per_persona_rows.append(
            {
                "arm": row["arm"],
                "agent": row["agent"],
                "num_seeds_observed": len(row["seeds_observed"]),
                "sum_active_episodes": int(row["sum_active_episodes"]),
                "posts_per_active_episode": row["sum_post_count"] / denom,
                "replies_per_active_episode": row["sum_reply_count"] / denom,
                "likes_per_active_episode": row["sum_like_count"] / denom,
                "reposts_per_active_episode": row["sum_repost_count"] / denom,
                "interactions_per_active_episode": row["sum_interactions_count"] / denom,
                "total_actions_per_active_episode": row["sum_total_actions_count"] / denom,
            }
        )

    per_persona_rows.sort(key=lambda r: (str(r["arm"]), str(r["agent"])))

    # 3) Arm-level summaries of per-persona distributions
    metrics = [
        "total_actions_per_active_episode",
        "posts_per_active_episode",
        "replies_per_active_episode",
        "likes_per_active_episode",
        "reposts_per_active_episode",
        "interactions_per_active_episode",
    ]

    summary_rows: list[dict[str, Any]] = []
    for arm in ("chronological", "recsys_twitter", "recsys_twhin"):
        arm_rows = [r for r in per_persona_rows if str(r["arm"]) == arm]
        if not arm_rows:
            continue
        for metric in metrics:
            vals = [float(r[metric]) for r in arm_rows]
            summary = _summarize_distribution(vals)
            summary_rows.append(
                {
                    "arm": arm,
                    "metric": metric,
                    "num_personas": len(vals),
                    **summary,
                }
            )

    # 4) Write outputs
    out_base = args.outputs_dir
    per_run_agent_csv = out_base / f"{args.output_prefix}_per_run_agent.csv"
    per_persona_csv = out_base / f"{args.output_prefix}_per_persona_across_seeds.csv"
    summary_csv = out_base / f"{args.output_prefix}_summary.csv"

    _write_csv(per_run_agent_csv, per_run_agent_rows)
    _write_csv(per_persona_csv, per_persona_rows)
    _write_csv(summary_csv, summary_rows)

    # 5) Plots
    hist_total_png = out_base / f"{args.output_prefix}_hist_total_actions.png"
    box_total_png = out_base / f"{args.output_prefix}_box_total_actions.png"
    hist_inter_png = out_base / f"{args.output_prefix}_hist_interactions.png"

    _plot_hist_by_arm(
        per_persona_rows,
        metric_key="total_actions_per_active_episode",
        out_path=hist_total_png,
        title="Per-persona total actions per active episode (aggregated across seeds)",
    )
    _plot_box_by_arm(
        per_persona_rows,
        metric_key="total_actions_per_active_episode",
        out_path=box_total_png,
        title="Per-persona total actions per active episode by arm (across seeds)",
    )
    _plot_hist_by_arm(
        per_persona_rows,
        metric_key="interactions_per_active_episode",
        out_path=hist_inter_png,
        title="Per-persona interactions per active episode (aggregated across seeds)",
    )

    out_json = out_base / f"{args.output_prefix}.json"
    payload = {
        "num_runs": len(refs),
        "num_per_run_agent_rows": len(per_run_agent_rows),
        "num_per_persona_rows": len(per_persona_rows),
        "seed_start": args.seed_start,
        "seed_end": args.seed_end,
        "setting_filter": args.setting_filter,
        "exclude_final_episode": args.exclude_final_episode,
        "per_run_agent_csv": str(per_run_agent_csv),
        "per_persona_csv": str(per_persona_csv),
        "summary_csv": str(summary_csv),
        "plots": {
            "hist_total_actions": str(hist_total_png),
            "box_total_actions": str(box_total_png),
            "hist_interactions": str(hist_inter_png),
        },
        "summary": summary_rows,
    }
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"Loaded runs: {len(refs)}")
    print(f"Per-run agent rows: {len(per_run_agent_rows)}")
    print(f"Per-persona rows (across seeds): {len(per_persona_rows)}")
    print(f"Wrote: {out_json}")
    print(f"Wrote: {per_run_agent_csv}")
    print(f"Wrote: {per_persona_csv}")
    print(f"Wrote: {summary_csv}")
    print(f"Wrote: {hist_total_png}")
    print(f"Wrote: {box_total_png}")
    print(f"Wrote: {hist_inter_png}")


if __name__ == "__main__":
    main()
