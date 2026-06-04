"""Structural dynamics analysis for mastodon-sim runs.

This script extracts cascade/virality/depth-breadth metrics from action_events.jsonl,
then compares groups across runs using configurable difference aggregation:

- overall: unpaired run-level comparison (Mann-Whitney U)
- seed_matched: paired-by-seed comparison (Wilcoxon)
- both: compute both side-by-side

Default cohort parser supports election recsys naming patterns used in this repo.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from statistics import mean, median
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import yaml
from scipy.stats import mannwhitneyu, wilcoxon

SOCIAL_LABELS = ("post", "reply", "like", "repost")
ARMS = ("chronological", "recsys_twitter", "recsys_twhin")
REGIMES = ("like20", "real2", "real_thinking")
FAMILIES = ("4b", "9b")
PAIR_REGIMES = tuple(combinations(REGIMES, 2))


@dataclass(frozen=True)
class RunMeta:
    run_dir: Path
    seed: int
    arm: str
    regime: str
    family: str


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _sig(p: float) -> str:
    if p < 0.001:
        return "***"
    if p < 0.01:
        return "**"
    if p < 0.05:
        return "*"
    return "ns"


def _paired_wilcoxon_safe(a: list[float], b: list[float]) -> tuple[float, float]:
    """Safe paired Wilcoxon that returns neutral stats when vectors are identical.

    SciPy Wilcoxon can raise warnings or errors when all paired differences are 0.
    In that case we return stat=0.0, p=1.0.
    """
    if len(a) != len(b):
        raise ValueError("Paired vectors must have same length")
    if len(a) < 2:
        return (0.0, 1.0)
    diffs = [x - y for x, y in zip(a, b, strict=True)]
    if all(abs(d) < 1e-12 for d in diffs):
        return (0.0, 1.0)
    stat, p = wilcoxon(a, b, zero_method="wilcox", alternative="two-sided")
    return (float(stat), float(p))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
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


def _parse_arm(tag: str) -> str | None:
    if tag.startswith("chronological"):
        return "chronological"
    if tag.startswith("recsys_twitter"):
        return "recsys_twitter"
    if tag.startswith("recsys_twhin"):
        return "recsys_twhin"
    return None


def _parse_family_regime(tag: str) -> tuple[str, str] | None:
    if "like_20_9b" in tag:
        return ("9b", "like20")
    if "real2_9b" in tag:
        return ("9b", "real2")
    if "real_thinking_9b" in tag:
        return ("9b", "real_thinking")

    if "like_20" in tag and "_9b" not in tag:
        return ("4b", "like20")
    if "real2" in tag and "_9b" not in tag:
        return ("4b", "real2")
    if "real_thinking" in tag and "_9b" not in tag:
        return ("4b", "real_thinking")

    return None


def _discover_runs(outputs_dir: Path, seed_start: int, seed_end: int) -> list[RunMeta]:
    pattern = re.compile(r"ElectionRecsys_N50_T10_clean50x10_seed(\d+)_([^/]+)")
    runs: list[RunMeta] = []

    for action_events in outputs_dir.rglob("action_events.jsonl"):
        run_dir = action_events.parent
        match = pattern.search(str(run_dir))
        if not match:
            continue

        seed = int(match.group(1))
        if seed < seed_start or seed > seed_end:
            continue

        tag = match.group(2)
        arm = _parse_arm(tag)
        fam_reg = _parse_family_regime(tag)
        if arm is None or fam_reg is None:
            continue

        family, regime = fam_reg
        if family not in FAMILIES or regime not in REGIMES:
            continue

        runs.append(RunMeta(run_dir=run_dir, seed=seed, arm=arm, regime=regime, family=family))

    runs.sort(key=lambda r: (r.family, r.regime, r.arm, r.seed, str(r.run_dir)))
    return runs


def _gini(values: list[float]) -> float:
    arr = np.array([float(v) for v in values if float(v) >= 0.0], dtype=float)
    if arr.size == 0:
        return 0.0
    if np.allclose(arr, 0.0):
        return 0.0
    arr_sorted = np.sort(arr)
    n = arr_sorted.size
    cum = np.cumsum(arr_sorted)
    g = (n + 1 - 2 * np.sum(cum) / cum[-1]) / n
    return float(g)


def _top_share(values: list[float], frac: float) -> float:
    if not values:
        return 0.0
    arr = sorted([float(v) for v in values], reverse=True)
    k = max(1, int(math.ceil(len(arr) * frac)))
    total = sum(arr)
    if total <= 0:
        return 0.0
    return float(sum(arr[:k]) / total)


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    idx = (len(s) - 1) * q
    lo = int(idx)
    hi = min(lo + 1, len(s) - 1)
    frac = idx - lo
    return float(s[lo] * (1 - frac) + s[hi] * frac)


def _root_of(post_id: str, parent_map: dict[str, str]) -> str:
    current = post_id
    seen: set[str] = set()
    while True:
        if current in seen:
            return current
        seen.add(current)
        parent = parent_map.get(current)
        if not parent or parent == current:
            return current
        if parent not in parent_map:
            return parent
        current = parent


def _compute_structural_metrics(run: RunMeta) -> dict[str, Any]:
    events = _read_jsonl(run.run_dir / "action_events.jsonl")
    effective_config = _load_yaml(run.run_dir / "effective_config.yaml")

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

    if total_steps > 1:
        valid_steps = set(range(1, total_steps))  # exclude final episode
    elif total_steps == 1:
        valid_steps = {1}
    else:
        valid_steps = set()

    post_parent: dict[str, str] = {}
    post_creator: dict[str, str] = {}
    post_created_idx: dict[str, int] = {}
    children: dict[str, list[str]] = defaultdict(list)
    interactions_by_post: Counter[str] = Counter()
    interaction_indices_by_post: dict[str, list[int]] = defaultdict(list)

    active_users_by_step: dict[int, set[str]] = defaultdict(set)
    social_counts: Counter[str] = Counter()

    filtered_events: list[tuple[int, dict[str, Any]]] = []
    for idx, event in enumerate(events):
        episode = _safe_int(event.get("episode"), 0)
        if episode < 1:
            continue
        if valid_steps and episode not in valid_steps:
            continue

        source_user = str(event.get("source_user", ""))
        if source_user not in eval_users:
            continue

        label = str(event.get("label", ""))
        if label not in SOCIAL_LABELS:
            continue

        filtered_events.append((idx, event))
        social_counts[label] += 1
        active_users_by_step[episode].add(source_user)

    for idx, event in filtered_events:
        label = str(event.get("label", ""))
        data = event.get("data", {}) if isinstance(event.get("data"), dict) else {}

        post_id = data.get("post_id")
        if post_id is not None:
            post_id = str(post_id)
        reply_to_id = data.get("reply_to_id")
        if reply_to_id is not None:
            reply_to_id = str(reply_to_id)

        # Creation of new posts (plain post and reply actions typically create post_id).
        if label in {"post", "reply"} and post_id:
            if post_id not in post_parent:
                post_parent[post_id] = reply_to_id or post_id
                post_creator[post_id] = str(event.get("source_user", ""))
                post_created_idx[post_id] = idx
            if reply_to_id:
                children[reply_to_id].append(post_id)

        # Engagement target tracking.
        if label in {"reply", "like", "repost"} and post_id:
            interactions_by_post[post_id] += 1
            interaction_indices_by_post[post_id].append(idx)

    # Compute roots from observed posts.
    observed_posts = set(post_parent.keys())
    root_posts = {_root_of(pid, post_parent) for pid in observed_posts}

    # Build post->root mapping for observed posts only.
    post_root: dict[str, str] = {}
    for pid in observed_posts:
        post_root[pid] = _root_of(pid, post_parent)

    # Group posts by root.
    posts_by_root: dict[str, set[str]] = defaultdict(set)
    for pid, root in post_root.items():
        posts_by_root[root].add(pid)

    # Include root even if missing from observed post set (dangling parents).
    for root in list(posts_by_root.keys()):
        posts_by_root[root].add(root)

    def cascade_depth(root: str) -> int:
        # BFS depth in reply tree using observed children.
        max_depth = 1
        queue = [(root, 1)]
        seen: set[str] = set()
        while queue:
            node, d = queue.pop(0)
            if node in seen:
                continue
            seen.add(node)
            max_depth = max(max_depth, d)
            for child in children.get(node, []):
                queue.append((child, d + 1))
        return max_depth

    def cascade_breadth(root: str) -> int:
        # Maximum branching factor across nodes in cascade.
        max_b = 0
        queue = [root]
        seen: set[str] = set()
        while queue:
            node = queue.pop(0)
            if node in seen:
                continue
            seen.add(node)
            ch = children.get(node, [])
            max_b = max(max_b, len(ch))
            queue.extend(ch)
        return max(1, max_b)

    def time_to_k(root: str, k: int = 5) -> float:
        created = post_created_idx.get(root)
        if created is None:
            return float("nan")
        target_posts = posts_by_root.get(root, {root})
        idxs: list[int] = []
        for pid in target_posts:
            idxs.extend(interaction_indices_by_post.get(pid, []))
        idxs = sorted(i for i in idxs if i >= created)
        if len(idxs) < k:
            return float("nan")
        return float(idxs[k - 1] - created)

    cascade_sizes: list[float] = []
    cascade_depths: list[float] = []
    cascade_breadths: list[float] = []
    depth_breadth_ratios: list[float] = []
    cascade_viralities: list[float] = []
    t5_values: list[float] = []

    for root in sorted(posts_by_root.keys()):
        nodes = posts_by_root[root]
        size = float(len(nodes))
        depth = float(cascade_depth(root))
        breadth = float(cascade_breadth(root))
        virality = float(sum(interactions_by_post.get(pid, 0) for pid in nodes))

        cascade_sizes.append(size)
        cascade_depths.append(depth)
        cascade_breadths.append(breadth)
        depth_breadth_ratios.append(depth / max(1.0, breadth))
        cascade_viralities.append(virality)

        tt5 = time_to_k(root, k=5)
        if not math.isnan(tt5):
            t5_values.append(tt5)

    if not cascade_sizes:
        cascade_sizes = [0.0]
        cascade_depths = [1.0]
        cascade_breadths = [1.0]
        depth_breadth_ratios = [1.0]
        cascade_viralities = [0.0]

    active_agent_episodes = sum(len(v) for v in active_users_by_step.values())
    denom = float(active_agent_episodes) if active_agent_episodes > 0 else 1.0
    post_rate = float(social_counts["post"] / denom)
    reply_rate = float(social_counts["reply"] / denom)
    like_rate = float(social_counts["like"] / denom)
    repost_rate = float(social_counts["repost"] / denom)
    interactions_rate = reply_rate + like_rate + repost_rate
    total_rate = post_rate + interactions_rate

    result = {
        "seed": run.seed,
        "arm": run.arm,
        "regime": run.regime,
        "family": run.family,
        "run_dir": str(run.run_dir),
        "num_cascades": len(cascade_sizes),
        "num_posts_observed": len(observed_posts),
        "num_root_posts": len(posts_by_root),
        "active_agent_episodes": active_agent_episodes,
        "total_actions_per_active_agent_episode": total_rate,
        "posts_per_active_agent_episode": post_rate,
        "interactions_per_active_agent_episode": interactions_rate,
        "replies_per_active_agent_episode": reply_rate,
        "likes_per_active_agent_episode": like_rate,
        "reposts_per_active_agent_episode": repost_rate,
        "cascade_size_mean": mean(cascade_sizes),
        "cascade_size_median": median(cascade_sizes),
        "cascade_size_p90": _quantile(cascade_sizes, 0.9),
        "cascade_size_p99": _quantile(cascade_sizes, 0.99),
        "cascade_size_max": max(cascade_sizes),
        "cascade_depth_mean": mean(cascade_depths),
        "cascade_depth_p90": _quantile(cascade_depths, 0.9),
        "cascade_depth_max": max(cascade_depths),
        "cascade_breadth_mean": mean(cascade_breadths),
        "cascade_breadth_p90": _quantile(cascade_breadths, 0.9),
        "cascade_breadth_max": max(cascade_breadths),
        "depth_breadth_ratio_mean": mean(depth_breadth_ratios),
        "depth_breadth_ratio_p90": _quantile(depth_breadth_ratios, 0.9),
        "virality_mean": mean(cascade_viralities),
        "virality_median": median(cascade_viralities),
        "virality_p90": _quantile(cascade_viralities, 0.9),
        "virality_p99": _quantile(cascade_viralities, 0.99),
        "virality_gini": _gini(cascade_viralities),
        "virality_top1_share": _top_share(cascade_viralities, 0.01),
        "virality_top5_share": _top_share(cascade_viralities, 0.05),
        "time_to_5_interactions_median": median(t5_values) if t5_values else float("nan"),
    }

    return result


def _compare_metric(
    rows: list[dict[str, Any]],
    metric: str,
    group_a: dict[str, str],
    group_b: dict[str, str],
    aggregation_mode: str,
) -> dict[str, Any]:
    vals_a = [
        float(r[metric])
        for r in rows
        if all(str(r[k]) == str(v) for k, v in group_a.items()) and not math.isnan(float(r[metric]))
    ]
    vals_b = [
        float(r[metric])
        for r in rows
        if all(str(r[k]) == str(v) for k, v in group_b.items()) and not math.isnan(float(r[metric]))
    ]

    result: dict[str, Any] = {
        "metric": metric,
        "group_a": json.dumps(group_a, sort_keys=True),
        "group_b": json.dumps(group_b, sort_keys=True),
        "mean_a": mean(vals_a) if vals_a else float("nan"),
        "mean_b": mean(vals_b) if vals_b else float("nan"),
        "delta_a_minus_b": (mean(vals_a) - mean(vals_b)) if vals_a and vals_b else float("nan"),
        "n_a": len(vals_a),
        "n_b": len(vals_b),
    }

    if aggregation_mode in {"overall", "both"}:
        if vals_a and vals_b:
            stat_u, p_u = mannwhitneyu(vals_a, vals_b, alternative="two-sided")
            result["overall_stat"] = float(stat_u)
            result["overall_p"] = float(p_u)
            result["overall_sig"] = _sig(float(p_u))
        else:
            result["overall_stat"] = float("nan")
            result["overall_p"] = float("nan")
            result["overall_sig"] = "ns"

    if aggregation_mode in {"seed_matched", "both"}:
        map_a = {
            int(r["seed"]): float(r[metric])
            for r in rows
            if all(str(r[k]) == str(v) for k, v in group_a.items())
            and not math.isnan(float(r[metric]))
        }
        map_b = {
            int(r["seed"]): float(r[metric])
            for r in rows
            if all(str(r[k]) == str(v) for k, v in group_b.items())
            and not math.isnan(float(r[metric]))
        }
        seeds = sorted(set(map_a) & set(map_b))
        result["seed_matched_n"] = len(seeds)
        if len(seeds) >= 2:
            paired_a = [map_a[s] for s in seeds]
            paired_b = [map_b[s] for s in seeds]
            stat_w, p_w = _paired_wilcoxon_safe(paired_a, paired_b)
            result["seed_matched_stat"] = stat_w
            result["seed_matched_p"] = p_w
            result["seed_matched_sig"] = _sig(p_w)
            result["seed_matched_delta_mean"] = float(
                mean([a - b for a, b in zip(paired_a, paired_b, strict=True)])
            )
        else:
            result["seed_matched_stat"] = float("nan")
            result["seed_matched_p"] = float("nan")
            result["seed_matched_sig"] = "ns"
            result["seed_matched_delta_mean"] = float("nan")

    return result


def _make_comparisons(
    rows: list[dict[str, Any]], metrics: list[str], aggregation_mode: str
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {
        "within_family_regime_pairs_per_arm": [],
        "counterpart_9b_vs_4b_per_arm": [],
        "within_family_regime_pairs_avg_arms": [],
        "counterpart_9b_vs_4b_avg_arms": [],
    }

    # 1) Compare regimes within each family+arm (like20 vs real2, like20 vs thinking, real2 vs thinking)
    for family in FAMILIES:
        for arm in ARMS:
            for reg_a, reg_b in PAIR_REGIMES:
                group_a = {"family": family, "arm": arm, "regime": reg_a}
                group_b = {"family": family, "arm": arm, "regime": reg_b}
                for metric in metrics:
                    row = _compare_metric(rows, metric, group_a, group_b, aggregation_mode)
                    row.update({"family": family, "arm": arm, "regime_a": reg_a, "regime_b": reg_b})
                    out["within_family_regime_pairs_per_arm"].append(row)

    # 2) Counterpart comparisons 9b vs 4b per same arm+regime
    for arm in ARMS:
        for regime in REGIMES:
            group_a = {"family": "9b", "arm": arm, "regime": regime}
            group_b = {"family": "4b", "arm": arm, "regime": regime}
            for metric in metrics:
                row = _compare_metric(rows, metric, group_a, group_b, aggregation_mode)
                row.update({"arm": arm, "regime": regime})
                out["counterpart_9b_vs_4b_per_arm"].append(row)

    # 3) Average across arms for each seed before comparing
    avg_rows: list[dict[str, Any]] = []
    for family in FAMILIES:
        for regime in REGIMES:
            for seed in range(11, 21):
                sub = [
                    r
                    for r in rows
                    if r["family"] == family
                    and r["regime"] == regime
                    and int(r["seed"]) == seed
                    and r["arm"] in ARMS
                ]
                if len(sub) != 3:
                    continue
                rr = {"family": family, "regime": regime, "seed": seed, "arm": "avg_arms"}
                for metric in metrics:
                    vals = [float(x[metric]) for x in sub if not math.isnan(float(x[metric]))]
                    rr[metric] = mean(vals) if vals else float("nan")
                avg_rows.append(rr)

    for family in FAMILIES:
        for reg_a, reg_b in PAIR_REGIMES:
            group_a = {"family": family, "arm": "avg_arms", "regime": reg_a}
            group_b = {"family": family, "arm": "avg_arms", "regime": reg_b}
            for metric in metrics:
                row = _compare_metric(avg_rows, metric, group_a, group_b, aggregation_mode)
                row.update(
                    {"family": family, "arm": "avg_arms", "regime_a": reg_a, "regime_b": reg_b}
                )
                out["within_family_regime_pairs_avg_arms"].append(row)

    for regime in REGIMES:
        group_a = {"family": "9b", "arm": "avg_arms", "regime": regime}
        group_b = {"family": "4b", "arm": "avg_arms", "regime": regime}
        for metric in metrics:
            row = _compare_metric(avg_rows, metric, group_a, group_b, aggregation_mode)
            row.update({"arm": "avg_arms", "regime": regime})
            out["counterpart_9b_vs_4b_avg_arms"].append(row)

    return out


def _plot_structural_dashboard(rows: list[dict[str, Any]], out_path: Path) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(15, 10))
    x = np.arange(len(REGIMES))
    regime_labels = ["like_20", "real2", "real_thinking"]
    colors = {"4b": "#4C78A8", "9b": "#F58518"}

    # 1) Total actions trend avg across arms
    ax = axes[0, 0]
    for fam in FAMILIES:
        means = []
        cis = []
        for reg in REGIMES:
            vals = [
                float(r["total_actions_per_active_agent_episode"])
                for r in rows
                if r["family"] == fam and r["regime"] == reg
            ]
            m = mean(vals)
            ci = 1.96 * (np.std(vals, ddof=1) / np.sqrt(len(vals))) if len(vals) > 1 else 0.0
            means.append(m)
            cis.append(ci)
        ax.errorbar(
            x, means, yerr=cis, marker="o", linewidth=2, capsize=3, color=colors[fam], label=fam
        )
    ax.set_title("Total Actions Across Regimes (avg across arms)")
    ax.set_xticks(x)
    ax.set_xticklabels(regime_labels, rotation=20, ha="right")
    ax.set_ylabel("total actions / active-agent-episode")
    ax.grid(axis="y", alpha=0.3)
    ax.legend(frameon=False)

    # 2) Cascade depth vs breadth scatter
    ax = axes[0, 1]
    markers = {"4b": "o", "9b": "^"}
    for fam in FAMILIES:
        for reg in REGIMES:
            sub = [r for r in rows if r["family"] == fam and r["regime"] == reg]
            xs = [float(r["cascade_breadth_mean"]) for r in sub]
            ys = [float(r["cascade_depth_mean"]) for r in sub]
            ax.scatter(xs, ys, alpha=0.6, marker=markers[fam], label=f"{fam}-{reg}")
    ax.set_title("Depth vs Breadth (run-level)")
    ax.set_xlabel("cascade_breadth_mean")
    ax.set_ylabel("cascade_depth_mean")
    ax.grid(alpha=0.3)

    # 3) Virality concentration shift
    ax = axes[1, 0]
    width = 0.35
    for i, fam in enumerate(FAMILIES):
        vals = [
            mean(
                [
                    float(r["virality_gini"])
                    for r in rows
                    if r["family"] == fam and r["regime"] == reg
                ]
            )
            for reg in REGIMES
        ]
        ax.bar(x + (i - 0.5) * width, vals, width=width, color=colors[fam], alpha=0.85, label=fam)
    ax.set_title("Virality Gini by Regime")
    ax.set_xticks(x)
    ax.set_xticklabels(regime_labels, rotation=20, ha="right")
    ax.set_ylabel("gini")
    ax.grid(axis="y", alpha=0.3)
    ax.legend(frameon=False)

    # 4) Cascade size tail (p90)
    ax = axes[1, 1]
    for fam in FAMILIES:
        means = []
        for reg in REGIMES:
            vals = [
                float(r["cascade_size_p90"])
                for r in rows
                if r["family"] == fam and r["regime"] == reg
            ]
            means.append(mean(vals))
        ax.plot(x, means, marker="o", linewidth=2, color=colors[fam], label=fam)
    ax.set_title("Cascade Size Tail (p90)")
    ax.set_xticks(x)
    ax.set_xticklabels(regime_labels, rotation=20, ha="right")
    ax.set_ylabel("p90 cascade size")
    ax.grid(axis="y", alpha=0.3)
    ax.legend(frameon=False)

    fig.suptitle("Structural Dynamics Dashboard")
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=180)
    plt.close(fig)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--outputs-dir",
        type=Path,
        default=Path("worlds/election_recsys_engagement/outputs"),
    )
    parser.add_argument("--seed-start", type=int, default=11)
    parser.add_argument("--seed-end", type=int, default=20)
    parser.add_argument(
        "--diff-aggregation",
        choices=("overall", "seed_matched", "both"),
        default="both",
        help="How to aggregate differences across runs.",
    )
    parser.add_argument(
        "--output-prefix",
        type=str,
        default="n50_t10_clean50x10_structural_dynamics_9b_vs_4b",
    )
    args = parser.parse_args()

    runs = _discover_runs(args.outputs_dir, args.seed_start, args.seed_end)
    if not runs:
        raise SystemExit("No runs discovered for requested cohort.")

    per_run_rows = [_compute_structural_metrics(run) for run in runs]

    coverage: dict[str, dict[str, dict[str, list[int]]]] = defaultdict(lambda: defaultdict(dict))
    for fam in FAMILIES:
        for reg in REGIMES:
            for arm in ARMS:
                seeds = sorted(
                    {
                        int(r["seed"])
                        for r in per_run_rows
                        if r["family"] == fam and r["regime"] == reg and r["arm"] == arm
                    }
                )
                coverage[fam][reg][arm] = seeds

    metrics = [
        "total_actions_per_active_agent_episode",
        "posts_per_active_agent_episode",
        "interactions_per_active_agent_episode",
        "cascade_size_mean",
        "cascade_size_p90",
        "cascade_depth_mean",
        "cascade_depth_p90",
        "cascade_breadth_mean",
        "depth_breadth_ratio_mean",
        "virality_mean",
        "virality_p90",
        "virality_gini",
        "virality_top1_share",
        "virality_top5_share",
        "time_to_5_interactions_median",
    ]

    comparisons = _make_comparisons(per_run_rows, metrics, aggregation_mode=args.diff_aggregation)

    base = args.outputs_dir / args.output_prefix
    json_path = Path(str(base) + ".json")
    csv_per_run = Path(str(base) + "_per_run_metrics.csv")
    csv_within_arm = Path(str(base) + "_within_family_per_arm.csv")
    csv_counterpart_arm = Path(str(base) + "_counterpart_per_arm.csv")
    csv_within_avg = Path(str(base) + "_within_family_avg_arms.csv")
    csv_counterpart_avg = Path(str(base) + "_counterpart_avg_arms.csv")
    png_dashboard = Path(str(base) + "_dashboard.png")

    _write_csv(csv_per_run, per_run_rows)
    _write_csv(csv_within_arm, comparisons["within_family_regime_pairs_per_arm"])
    _write_csv(csv_counterpart_arm, comparisons["counterpart_9b_vs_4b_per_arm"])
    _write_csv(csv_within_avg, comparisons["within_family_regime_pairs_avg_arms"])
    _write_csv(csv_counterpart_avg, comparisons["counterpart_9b_vs_4b_avg_arms"])

    _plot_structural_dashboard(per_run_rows, png_dashboard)

    payload = {
        "diff_aggregation": args.diff_aggregation,
        "seed_range": [args.seed_start, args.seed_end],
        "coverage": coverage,
        "num_runs": len(runs),
        "num_per_run_rows": len(per_run_rows),
        "metrics_compared": metrics,
        "outputs": {
            "per_run_csv": str(csv_per_run),
            "within_family_per_arm_csv": str(csv_within_arm),
            "counterpart_per_arm_csv": str(csv_counterpart_arm),
            "within_family_avg_arms_csv": str(csv_within_avg),
            "counterpart_avg_arms_csv": str(csv_counterpart_avg),
            "dashboard_png": str(png_dashboard),
        },
        "comparisons": comparisons,
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"Loaded runs: {len(runs)}")
    print(f"Wrote: {json_path}")
    print(f"Wrote: {csv_per_run}")
    print(f"Wrote: {csv_within_arm}")
    print(f"Wrote: {csv_counterpart_arm}")
    print(f"Wrote: {csv_within_avg}")
    print(f"Wrote: {csv_counterpart_avg}")
    print(f"Wrote: {png_dashboard}")


if __name__ == "__main__":
    main()
