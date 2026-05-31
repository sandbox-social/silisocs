"""Compare 9b runs vs 4b counterparts for like_20 / real2 / real_thinking.

Outputs:
1) Within-family comparisons (4b and 9b), per arm and averaged across arms.
2) Counterpart comparisons (9b vs 4b), per arm and averaged across arms.
3) Variation-of-variation: compare pairwise regime gaps in 9b vs 4b.
4) Visualizations for trends and pairwise difference structure.
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from statistics import mean, stdev
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import yaml
from scipy.stats import friedmanchisquare, wilcoxon

SOCIAL_LABELS = ("post", "reply", "like", "repost")
ARMS = ("chronological", "recsys_twitter", "recsys_twhin")
REGIMES = ("like20", "real2", "real_thinking")
FAMILIES = ("4b", "9b")
PAIR_REGIMES = tuple(combinations(REGIMES, 2))

METRICS = (
    "total_actions_per_active_agent_episode",
    "posts_per_active_agent_episode",
    "interactions_per_active_agent_episode",
    "replies_per_active_agent_episode",
    "likes_per_active_agent_episode",
    "reposts_per_active_agent_episode",
)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
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


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
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


def _arm_from_tag(tag: str) -> str | None:
    if tag.startswith("chronological"):
        return "chronological"
    if tag.startswith("recsys_twitter"):
        return "recsys_twitter"
    if tag.startswith("recsys_twhin"):
        return "recsys_twhin"
    return None


def _family_and_regime_from_tag(tag: str) -> tuple[str, str] | None:
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


def load_per_run_rows(outputs_dir: Path) -> list[dict[str, Any]]:
    pattern = re.compile(r"ElectionRecsys_N50_T10_clean50x10_seed(\d+)_([^/]+)")
    rows: list[dict[str, Any]] = []

    for action_events in outputs_dir.rglob("action_events.jsonl"):
        run_dir = action_events.parent
        match = pattern.search(str(run_dir))
        if not match:
            continue

        seed = int(match.group(1))
        if seed < 11 or seed > 20:
            continue

        tag = match.group(2)
        arm = _arm_from_tag(tag)
        family_regime = _family_and_regime_from_tag(tag)
        if arm not in ARMS or family_regime is None:
            continue

        family, regime = family_regime
        if family not in FAMILIES or regime not in REGIMES:
            continue

        events = _read_jsonl(action_events)
        effective_config = _load_yaml(run_dir / "effective_config.yaml")

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
            step_range = list(range(1, total_steps))
        elif total_steps == 1:
            step_range = [1]
        else:
            step_range = []

        label_counts: Counter[str] = Counter()
        active_users_by_step: dict[int, set[str]] = defaultdict(set)

        for event in events:
            episode = _safe_int(event.get("episode"), 0)
            if episode < 1:
                continue
            if step_range and episode not in step_range:
                continue

            user = str(event.get("source_user", ""))
            if user not in eval_users:
                continue

            label = str(event.get("label", ""))
            if label in SOCIAL_LABELS:
                label_counts[label] += 1
                active_users_by_step[episode].add(user)

        if not step_range:
            step_range = sorted(active_users_by_step.keys())

        active_agent_episodes = sum(
            len(active_users_by_step.get(step, set())) for step in step_range
        )
        denom = float(active_agent_episodes) if active_agent_episodes > 0 else 1.0

        post = label_counts["post"]
        reply = label_counts["reply"]
        like = label_counts["like"]
        repost = label_counts["repost"]
        interactions = reply + like + repost
        total_actions = post + interactions

        rows.append(
            {
                "seed": seed,
                "arm": arm,
                "family": family,
                "regime": regime,
                "run_dir": str(run_dir),
                "active_agent_episodes": active_agent_episodes,
                "total_actions_per_active_agent_episode": total_actions / denom,
                "posts_per_active_agent_episode": post / denom,
                "replies_per_active_agent_episode": reply / denom,
                "likes_per_active_agent_episode": like / denom,
                "reposts_per_active_agent_episode": repost / denom,
                "interactions_per_active_agent_episode": interactions / denom,
            }
        )

    return rows


def compute_coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    coverage: dict[str, dict[str, dict[str, list[int]]]] = defaultdict(lambda: defaultdict(dict))
    for fam in FAMILIES:
        for reg in REGIMES:
            for arm in ARMS:
                seeds = sorted(
                    {
                        int(r["seed"])
                        for r in rows
                        if r["family"] == fam and r["regime"] == reg and r["arm"] == arm
                    }
                )
                coverage[fam][reg][arm] = seeds
    return coverage


def summarize_means(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for fam in FAMILIES:
        for reg in REGIMES:
            for arm in ARMS:
                sub = [
                    r for r in rows if r["family"] == fam and r["regime"] == reg and r["arm"] == arm
                ]
                if not sub:
                    continue
                for metric in METRICS:
                    vals = [float(r[metric]) for r in sub]
                    out.append(
                        {
                            "family": fam,
                            "regime": reg,
                            "arm": arm,
                            "metric": metric,
                            "n": len(vals),
                            "mean": mean(vals),
                            "std": stdev(vals) if len(vals) > 1 else 0.0,
                        }
                    )
    return out


def compute_within_family_per_arm(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    omnibus_rows: list[dict[str, Any]] = []
    pairwise_rows: list[dict[str, Any]] = []

    for fam in FAMILIES:
        for arm in ARMS:
            for metric in METRICS:
                reg_seed_map = {
                    reg: {
                        int(r["seed"]): float(r[metric])
                        for r in rows
                        if r["family"] == fam and r["arm"] == arm and r["regime"] == reg
                    }
                    for reg in REGIMES
                }
                common_seeds = sorted(
                    set.intersection(*[set(reg_seed_map[reg]) for reg in REGIMES])
                )
                if len(common_seeds) < 2:
                    continue

                vals = [[reg_seed_map[reg][s] for s in common_seeds] for reg in REGIMES]
                stat, p = friedmanchisquare(*vals)
                omnibus_rows.append(
                    {
                        "family": fam,
                        "arm": arm,
                        "metric": metric,
                        "n_seeds": len(common_seeds),
                        "friedman_chi2": float(stat),
                        "friedman_p": float(p),
                        "sig": _sig(float(p)),
                    }
                )

                block: list[dict[str, Any]] = []
                for reg_a, reg_b in PAIR_REGIMES:
                    vals_a = [reg_seed_map[reg_a][s] for s in common_seeds]
                    vals_b = [reg_seed_map[reg_b][s] for s in common_seeds]
                    stat_pw, p_pw = wilcoxon(
                        vals_a, vals_b, zero_method="wilcox", alternative="two-sided"
                    )
                    block.append(
                        {
                            "family": fam,
                            "arm": arm,
                            "metric": metric,
                            "regime_a": reg_a,
                            "regime_b": reg_b,
                            "n_seeds": len(common_seeds),
                            "mean_a": mean(vals_a),
                            "mean_b": mean(vals_b),
                            "delta_a_minus_b": mean(vals_a) - mean(vals_b),
                            "wilcoxon_stat": float(stat_pw),
                            "wilcoxon_p": float(p_pw),
                        }
                    )

                _holm_adjust(block, p_key="wilcoxon_p", out_key="wilcoxon_p_holm")
                for row in block:
                    row["sig_raw"] = _sig(float(row["wilcoxon_p"]))
                    row["sig_holm"] = _sig(float(row["wilcoxon_p_holm"]))
                pairwise_rows.extend(block)

    return omnibus_rows, pairwise_rows


def compute_average_across_arms(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Average metric across arms for each (family, regime, seed)."""
    out: list[dict[str, Any]] = []
    for fam in FAMILIES:
        for reg in REGIMES:
            for seed in range(11, 21):
                sub = [
                    r
                    for r in rows
                    if r["family"] == fam
                    and r["regime"] == reg
                    and int(r["seed"]) == seed
                    and r["arm"] in ARMS
                ]
                if len(sub) != 3:
                    continue
                row = {"family": fam, "regime": reg, "seed": seed}
                for metric in METRICS:
                    row[metric] = mean([float(x[metric]) for x in sub])
                out.append(row)
    return out


def compute_within_family_average(
    avg_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    omnibus_rows: list[dict[str, Any]] = []
    pairwise_rows: list[dict[str, Any]] = []

    for fam in FAMILIES:
        for metric in METRICS:
            reg_seed_map = {
                reg: {
                    int(r["seed"]): float(r[metric])
                    for r in avg_rows
                    if r["family"] == fam and r["regime"] == reg
                }
                for reg in REGIMES
            }
            common_seeds = sorted(set.intersection(*[set(reg_seed_map[reg]) for reg in REGIMES]))
            if len(common_seeds) < 2:
                continue

            vals = [[reg_seed_map[reg][s] for s in common_seeds] for reg in REGIMES]
            stat, p = friedmanchisquare(*vals)
            omnibus_rows.append(
                {
                    "family": fam,
                    "metric": metric,
                    "n_seeds": len(common_seeds),
                    "friedman_chi2": float(stat),
                    "friedman_p": float(p),
                    "sig": _sig(float(p)),
                }
            )

            block = []
            for reg_a, reg_b in PAIR_REGIMES:
                vals_a = [reg_seed_map[reg_a][s] for s in common_seeds]
                vals_b = [reg_seed_map[reg_b][s] for s in common_seeds]
                stat_pw, p_pw = wilcoxon(
                    vals_a, vals_b, zero_method="wilcox", alternative="two-sided"
                )
                block.append(
                    {
                        "family": fam,
                        "metric": metric,
                        "regime_a": reg_a,
                        "regime_b": reg_b,
                        "n_seeds": len(common_seeds),
                        "mean_a": mean(vals_a),
                        "mean_b": mean(vals_b),
                        "delta_a_minus_b": mean(vals_a) - mean(vals_b),
                        "wilcoxon_stat": float(stat_pw),
                        "wilcoxon_p": float(p_pw),
                    }
                )

            _holm_adjust(block, p_key="wilcoxon_p", out_key="wilcoxon_p_holm")
            for row in block:
                row["sig_raw"] = _sig(float(row["wilcoxon_p"]))
                row["sig_holm"] = _sig(float(row["wilcoxon_p_holm"]))
            pairwise_rows.extend(block)

    return omnibus_rows, pairwise_rows


def compute_counterpart_comparisons(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for arm in ARMS:
        for reg in REGIMES:
            for metric in METRICS:
                map_9b = {
                    int(r["seed"]): float(r[metric])
                    for r in rows
                    if r["family"] == "9b" and r["arm"] == arm and r["regime"] == reg
                }
                map_4b = {
                    int(r["seed"]): float(r[metric])
                    for r in rows
                    if r["family"] == "4b" and r["arm"] == arm and r["regime"] == reg
                }
                seeds = sorted(set(map_9b) & set(map_4b))
                if len(seeds) < 2:
                    continue
                vals_9b = [map_9b[s] for s in seeds]
                vals_4b = [map_4b[s] for s in seeds]
                stat, p = wilcoxon(vals_9b, vals_4b, zero_method="wilcox", alternative="two-sided")
                out.append(
                    {
                        "arm": arm,
                        "regime": reg,
                        "metric": metric,
                        "n_seeds": len(seeds),
                        "mean_9b": mean(vals_9b),
                        "mean_4b": mean(vals_4b),
                        "delta_9b_minus_4b": mean(vals_9b) - mean(vals_4b),
                        "wilcoxon_stat": float(stat),
                        "wilcoxon_p": float(p),
                    }
                )

    # Holm per (arm, metric) across the three regimes
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in out:
        grouped[(row["arm"], row["metric"])].append(row)
    for group_rows in grouped.values():
        _holm_adjust(group_rows, p_key="wilcoxon_p", out_key="wilcoxon_p_holm")
        for row in group_rows:
            row["sig_raw"] = _sig(float(row["wilcoxon_p"]))
            row["sig_holm"] = _sig(float(row["wilcoxon_p_holm"]))

    return out


def compute_counterpart_average(avg_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for reg in REGIMES:
        for metric in METRICS:
            map_9b = {
                int(r["seed"]): float(r[metric])
                for r in avg_rows
                if r["family"] == "9b" and r["regime"] == reg
            }
            map_4b = {
                int(r["seed"]): float(r[metric])
                for r in avg_rows
                if r["family"] == "4b" and r["regime"] == reg
            }
            seeds = sorted(set(map_9b) & set(map_4b))
            if len(seeds) < 2:
                continue
            vals_9b = [map_9b[s] for s in seeds]
            vals_4b = [map_4b[s] for s in seeds]
            stat, p = wilcoxon(vals_9b, vals_4b, zero_method="wilcox", alternative="two-sided")
            out.append(
                {
                    "regime": reg,
                    "metric": metric,
                    "n_seeds": len(seeds),
                    "mean_9b": mean(vals_9b),
                    "mean_4b": mean(vals_4b),
                    "delta_9b_minus_4b": mean(vals_9b) - mean(vals_4b),
                    "wilcoxon_stat": float(stat),
                    "wilcoxon_p": float(p),
                }
            )

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in out:
        grouped[row["metric"]].append(row)
    for group_rows in grouped.values():
        _holm_adjust(group_rows, p_key="wilcoxon_p", out_key="wilcoxon_p_holm")
        for row in group_rows:
            row["sig_raw"] = _sig(float(row["wilcoxon_p"]))
            row["sig_holm"] = _sig(float(row["wilcoxon_p_holm"]))

    return out


def compute_variation_of_variation(
    rows: list[dict[str, Any]], avg_rows: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Compare regime gaps in 9b vs regime gaps in 4b.

    For each pair (A,B), compare:
        (A-B)_9b - (A-B)_4b
    using paired Wilcoxon by seed.
    """
    per_arm_rows: list[dict[str, Any]] = []
    avg_rows_out: list[dict[str, Any]] = []

    for arm in ARMS:
        for metric in METRICS:
            block: list[dict[str, Any]] = []
            for reg_a, reg_b in PAIR_REGIMES:
                map_9b_a = {
                    int(r["seed"]): float(r[metric])
                    for r in rows
                    if r["family"] == "9b" and r["arm"] == arm and r["regime"] == reg_a
                }
                map_9b_b = {
                    int(r["seed"]): float(r[metric])
                    for r in rows
                    if r["family"] == "9b" and r["arm"] == arm and r["regime"] == reg_b
                }
                map_4b_a = {
                    int(r["seed"]): float(r[metric])
                    for r in rows
                    if r["family"] == "4b" and r["arm"] == arm and r["regime"] == reg_a
                }
                map_4b_b = {
                    int(r["seed"]): float(r[metric])
                    for r in rows
                    if r["family"] == "4b" and r["arm"] == arm and r["regime"] == reg_b
                }

                seeds = sorted(set(map_9b_a) & set(map_9b_b) & set(map_4b_a) & set(map_4b_b))
                if len(seeds) < 2:
                    continue

                gap_9b = [map_9b_a[s] - map_9b_b[s] for s in seeds]
                gap_4b = [map_4b_a[s] - map_4b_b[s] for s in seeds]
                diff_gap = [g9 - g4 for g9, g4 in zip(gap_9b, gap_4b, strict=True)]

                stat, p = wilcoxon(diff_gap, zero_method="wilcox", alternative="two-sided")
                block.append(
                    {
                        "arm": arm,
                        "metric": metric,
                        "regime_a": reg_a,
                        "regime_b": reg_b,
                        "n_seeds": len(seeds),
                        "gap_9b_mean": mean(gap_9b),
                        "gap_4b_mean": mean(gap_4b),
                        "delta_gap_mean": mean(diff_gap),
                        "wilcoxon_stat": float(stat),
                        "wilcoxon_p": float(p),
                    }
                )

            _holm_adjust(block, p_key="wilcoxon_p", out_key="wilcoxon_p_holm")
            for row in block:
                row["sig_raw"] = _sig(float(row["wilcoxon_p"]))
                row["sig_holm"] = _sig(float(row["wilcoxon_p_holm"]))
            per_arm_rows.extend(block)

    # average across arms
    for metric in METRICS:
        block = []
        for reg_a, reg_b in PAIR_REGIMES:
            map_9b_a = {
                int(r["seed"]): float(r[metric])
                for r in avg_rows
                if r["family"] == "9b" and r["regime"] == reg_a
            }
            map_9b_b = {
                int(r["seed"]): float(r[metric])
                for r in avg_rows
                if r["family"] == "9b" and r["regime"] == reg_b
            }
            map_4b_a = {
                int(r["seed"]): float(r[metric])
                for r in avg_rows
                if r["family"] == "4b" and r["regime"] == reg_a
            }
            map_4b_b = {
                int(r["seed"]): float(r[metric])
                for r in avg_rows
                if r["family"] == "4b" and r["regime"] == reg_b
            }

            seeds = sorted(set(map_9b_a) & set(map_9b_b) & set(map_4b_a) & set(map_4b_b))
            if len(seeds) < 2:
                continue

            gap_9b = [map_9b_a[s] - map_9b_b[s] for s in seeds]
            gap_4b = [map_4b_a[s] - map_4b_b[s] for s in seeds]
            diff_gap = [g9 - g4 for g9, g4 in zip(gap_9b, gap_4b, strict=True)]

            stat, p = wilcoxon(diff_gap, zero_method="wilcox", alternative="two-sided")
            block.append(
                {
                    "metric": metric,
                    "regime_a": reg_a,
                    "regime_b": reg_b,
                    "n_seeds": len(seeds),
                    "gap_9b_mean": mean(gap_9b),
                    "gap_4b_mean": mean(gap_4b),
                    "delta_gap_mean": mean(diff_gap),
                    "wilcoxon_stat": float(stat),
                    "wilcoxon_p": float(p),
                }
            )

        _holm_adjust(block, p_key="wilcoxon_p", out_key="wilcoxon_p_holm")
        for row in block:
            row["sig_raw"] = _sig(float(row["wilcoxon_p"]))
            row["sig_holm"] = _sig(float(row["wilcoxon_p_holm"]))
        avg_rows_out.extend(block)

    return per_arm_rows, avg_rows_out


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def plot_total_actions_trend(rows: list[dict[str, Any]], out_path: Path) -> None:
    x = np.arange(len(REGIMES))
    fig, axes = plt.subplots(2, 2, figsize=(14, 9), sharex=True)
    panels = ["chronological", "recsys_twitter", "recsys_twhin", "avg"]
    colors = {"4b": "#4C78A8", "9b": "#F58518"}

    for ax, panel in zip(axes.flatten(), panels, strict=True):
        for fam in FAMILIES:
            means = []
            cis = []
            for reg in REGIMES:
                if panel == "avg":
                    vals = [
                        float(r["total_actions_per_active_agent_episode"])
                        for r in rows
                        if r["family"] == fam and r["regime"] == reg
                    ]
                else:
                    vals = [
                        float(r["total_actions_per_active_agent_episode"])
                        for r in rows
                        if r["family"] == fam and r["regime"] == reg and r["arm"] == panel
                    ]
                m = mean(vals)
                if len(vals) > 1:
                    ci = 1.96 * (np.std(vals, ddof=1) / np.sqrt(len(vals)))
                else:
                    ci = 0.0
                means.append(m)
                cis.append(ci)

            ax.errorbar(
                x,
                means,
                yerr=cis,
                marker="o",
                linewidth=2,
                capsize=3,
                color=colors[fam],
                label=fam,
            )

        title = panel if panel != "avg" else "avg across arms"
        ax.set_title(title)
        ax.grid(axis="y", alpha=0.3)
        if panel in ("chronological", "recsys_twitter"):
            ax.set_ylabel("total actions / active-agent-episode")

    for ax in axes[1]:
        ax.set_xticks(x)
        ax.set_xticklabels(["like_20", "real2", "real_thinking"], rotation=20, ha="right")

    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=2, frameon=False)
    fig.suptitle("Total Actions Trend: 4b vs 9b")
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=180)
    plt.close(fig)


def plot_level_metric_trend(
    rows: list[dict[str, Any]], metric: str, title: str, out_path: Path
) -> None:
    x = np.arange(len(REGIMES))
    fig, axes = plt.subplots(2, 2, figsize=(14, 9), sharex=True)
    panels = ["chronological", "recsys_twitter", "recsys_twhin", "avg"]
    colors = {"4b": "#4C78A8", "9b": "#F58518"}

    for ax, panel in zip(axes.flatten(), panels, strict=True):
        for fam in FAMILIES:
            means = []
            cis = []
            for reg in REGIMES:
                if panel == "avg":
                    vals = [
                        float(r[metric]) for r in rows if r["family"] == fam and r["regime"] == reg
                    ]
                else:
                    vals = [
                        float(r[metric])
                        for r in rows
                        if r["family"] == fam and r["regime"] == reg and r["arm"] == panel
                    ]
                m = mean(vals)
                if len(vals) > 1:
                    ci = 1.96 * (np.std(vals, ddof=1) / np.sqrt(len(vals)))
                else:
                    ci = 0.0
                means.append(m)
                cis.append(ci)

            ax.errorbar(
                x,
                means,
                yerr=cis,
                marker="o",
                linewidth=2,
                capsize=3,
                color=colors[fam],
                label=fam,
            )

        title_panel = panel if panel != "avg" else "avg across arms"
        ax.set_title(title_panel)
        ax.grid(axis="y", alpha=0.3)

    for ax in axes[1]:
        ax.set_xticks(x)
        ax.set_xticklabels(["like_20", "real2", "real_thinking"], rotation=20, ha="right")

    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=2, frameon=False)
    fig.suptitle(title)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=180)
    plt.close(fig)


def _pair_matrix_from_rows(rows: list[dict[str, Any]], metric: str, family: str) -> np.ndarray:
    reg_idx = {r: i for i, r in enumerate(REGIMES)}
    mat = np.zeros((len(REGIMES), len(REGIMES)), dtype=float)

    # mean(reg_i - reg_j) averaged across arms
    for i, reg_i in enumerate(REGIMES):
        for j, reg_j in enumerate(REGIMES):
            if i == j:
                mat[i, j] = 0.0
                continue
            deltas = []
            for arm in ARMS:
                map_i = {
                    int(r["seed"]): float(r[metric])
                    for r in rows
                    if r["family"] == family and r["regime"] == reg_i and r["arm"] == arm
                }
                map_j = {
                    int(r["seed"]): float(r[metric])
                    for r in rows
                    if r["family"] == family and r["regime"] == reg_j and r["arm"] == arm
                }
                seeds = sorted(set(map_i) & set(map_j))
                if not seeds:
                    continue
                deltas.append(mean([map_i[s] - map_j[s] for s in seeds]))
            mat[i, j] = mean(deltas) if deltas else np.nan

    return mat


def plot_pair_matrix_comparison(rows: list[dict[str, Any]], out_path: Path) -> None:
    metrics = (
        "total_actions_per_active_agent_episode",
        "posts_per_active_agent_episode",
        "interactions_per_active_agent_episode",
    )
    titles = ("total", "posts", "interactions")

    fig, axes = plt.subplots(3, 3, figsize=(13, 12))
    cmap = "RdBu_r"

    for row_i, (metric, mt) in enumerate(zip(metrics, titles, strict=True)):
        m4 = _pair_matrix_from_rows(rows, metric, "4b")
        m9 = _pair_matrix_from_rows(rows, metric, "9b")
        md = m9 - m4
        mats = (m4, m9, md)
        cmax = np.nanmax(np.abs(np.stack(mats)))

        for col_i, (mat, col_title) in enumerate(
            zip(mats, ("4b pair deltas", "9b pair deltas", "(9b-4b) pair deltas"), strict=True)
        ):
            ax = axes[row_i, col_i]
            im = ax.imshow(mat, cmap=cmap, vmin=-cmax, vmax=cmax)
            ax.set_xticks(range(len(REGIMES)))
            ax.set_yticks(range(len(REGIMES)))
            ax.set_xticklabels(["like20", "real2", "thinking"], rotation=20, ha="right")
            ax.set_yticklabels(["like20", "real2", "thinking"])
            if row_i == 0:
                ax.set_title(col_title)
            if col_i == 0:
                ax.set_ylabel(mt)
            for i in range(len(REGIMES)):
                for j in range(len(REGIMES)):
                    ax.text(j, i, f"{mat[i, j]:+.2f}", ha="center", va="center", fontsize=8)
            fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)

    fig.suptitle("Pairwise Regime Delta Structure: 4b vs 9b")
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=180)
    plt.close(fig)


def write_markdown_report(
    out_md: Path,
    means_rows: list[dict[str, Any]],
    within_arm_omnibus: list[dict[str, Any]],
    within_arm_pairwise: list[dict[str, Any]],
    counterpart_arm: list[dict[str, Any]],
    counterpart_avg: list[dict[str, Any]],
    vov_arm: list[dict[str, Any]],
    vov_avg: list[dict[str, Any]],
) -> None:
    lines: list[str] = []
    lines.append("# 9b vs 4b Regime Comparison")
    lines.append("")
    lines.append("- Regimes: like_20, real2, real_thinking")
    lines.append("- Families: 4b (default) vs 9b")
    lines.append("- Seeds: 11-20")
    lines.append("- Denominator: active-agent-episodes (final episode excluded)")
    lines.append("")

    lines.append("## Mean Levels by Family / Regime / Arm")
    focus = (
        "total_actions_per_active_agent_episode",
        "posts_per_active_agent_episode",
        "interactions_per_active_agent_episode",
    )
    for fam in FAMILIES:
        lines.append(f"### {fam}")
        for reg in REGIMES:
            lines.append(f"- {reg}:")
            for arm in ARMS:
                vals = {
                    r["metric"]: r["mean"]
                    for r in means_rows
                    if r["family"] == fam
                    and r["regime"] == reg
                    and r["arm"] == arm
                    and r["metric"] in focus
                }
                if vals:
                    lines.append(
                        f"  - {arm}: total={vals['total_actions_per_active_agent_episode']:.4f}, "
                        f"posts={vals['posts_per_active_agent_episode']:.4f}, "
                        f"interactions={vals['interactions_per_active_agent_episode']:.4f}"
                    )
        lines.append("")

    lines.append("## Within-Family Arm Differences (Omnibus)")
    for fam in FAMILIES:
        lines.append(f"### {fam}")
        for arm in ARMS:
            lines.append(f"- {arm}:")
            for metric in focus:
                r = next(
                    (
                        x
                        for x in within_arm_omnibus
                        if x["family"] == fam and x["arm"] == arm and x["metric"] == metric
                    ),
                    None,
                )
                if r:
                    lines.append(f"  - {metric}: p={r['friedman_p']:.8f} ({r['sig']})")
        lines.append("")

    lines.append("## Counterpart Comparisons (9b vs 4b)")
    lines.append("### Per Arm")
    for arm in ARMS:
        lines.append(f"- {arm}:")
        for metric in focus:
            lines.append(f"  - {metric}:")
            rows = [r for r in counterpart_arm if r["arm"] == arm and r["metric"] == metric]
            for reg in REGIMES:
                rr = next((x for x in rows if x["regime"] == reg), None)
                if rr:
                    lines.append(
                        f"    - {reg}: delta(9b-4b)={rr['delta_9b_minus_4b']:+.4f}, "
                        f"p={rr['wilcoxon_p']:.8f} ({rr['sig_raw']}), "
                        f"holm={rr['wilcoxon_p_holm']:.8f} ({rr['sig_holm']})"
                    )
    lines.append("")

    lines.append("### Average Across Arms")
    for metric in focus:
        lines.append(f"- {metric}:")
        rows = [r for r in counterpart_avg if r["metric"] == metric]
        for reg in REGIMES:
            rr = next((x for x in rows if x["regime"] == reg), None)
            if rr:
                lines.append(
                    f"  - {reg}: delta(9b-4b)={rr['delta_9b_minus_4b']:+.4f}, "
                    f"p={rr['wilcoxon_p']:.8f} ({rr['sig_raw']}), "
                    f"holm={rr['wilcoxon_p_holm']:.8f} ({rr['sig_holm']})"
                )
    lines.append("")

    lines.append("## Variation-of-Variation: How Pairwise Regime Gaps Change (9b vs 4b)")
    lines.append("### Per Arm")
    for arm in ARMS:
        lines.append(f"- {arm}:")
        for metric in focus:
            lines.append(f"  - {metric}:")
            rows = [r for r in vov_arm if r["arm"] == arm and r["metric"] == metric]
            for reg_a, reg_b in PAIR_REGIMES:
                rr = next(
                    (x for x in rows if x["regime_a"] == reg_a and x["regime_b"] == reg_b), None
                )
                if rr:
                    lines.append(
                        f"    - ({reg_a} - {reg_b}): gap9b={rr['gap_9b_mean']:+.4f}, gap4b={rr['gap_4b_mean']:+.4f}, "
                        f"delta_gap={rr['delta_gap_mean']:+.4f}, p={rr['wilcoxon_p']:.8f} ({rr['sig_raw']}), "
                        f"holm={rr['wilcoxon_p_holm']:.8f} ({rr['sig_holm']})"
                    )
    lines.append("")

    lines.append("### Average Across Arms")
    for metric in focus:
        lines.append(f"- {metric}:")
        rows = [r for r in vov_avg if r["metric"] == metric]
        for reg_a, reg_b in PAIR_REGIMES:
            rr = next((x for x in rows if x["regime_a"] == reg_a and x["regime_b"] == reg_b), None)
            if rr:
                lines.append(
                    f"  - ({reg_a} - {reg_b}): gap9b={rr['gap_9b_mean']:+.4f}, gap4b={rr['gap_4b_mean']:+.4f}, "
                    f"delta_gap={rr['delta_gap_mean']:+.4f}, p={rr['wilcoxon_p']:.8f} ({rr['sig_raw']}), "
                    f"holm={rr['wilcoxon_p_holm']:.8f} ({rr['sig_holm']})"
                )

    out_md.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    outputs_dir = Path("scenarios/election_recsys_engagement/outputs")

    per_run_rows = load_per_run_rows(outputs_dir)
    coverage = compute_coverage(per_run_rows)
    means_rows = summarize_means(per_run_rows)

    within_arm_omnibus, within_arm_pairwise = compute_within_family_per_arm(per_run_rows)

    avg_rows = compute_average_across_arms(per_run_rows)
    within_avg_omnibus, within_avg_pairwise = compute_within_family_average(avg_rows)

    counterpart_arm = compute_counterpart_comparisons(per_run_rows)
    counterpart_avg = compute_counterpart_average(avg_rows)

    vov_arm, vov_avg = compute_variation_of_variation(per_run_rows, avg_rows)

    # Save tables/json
    base = outputs_dir / "n50_t10_clean50x10_9b_vs_4b"
    _write_csv(Path(str(base) + "_means.csv"), means_rows)
    _write_csv(Path(str(base) + "_within_arm_omnibus.csv"), within_arm_omnibus)
    _write_csv(Path(str(base) + "_within_arm_pairwise.csv"), within_arm_pairwise)
    _write_csv(Path(str(base) + "_within_avg_omnibus.csv"), within_avg_omnibus)
    _write_csv(Path(str(base) + "_within_avg_pairwise.csv"), within_avg_pairwise)
    _write_csv(Path(str(base) + "_counterpart_per_arm.csv"), counterpart_arm)
    _write_csv(Path(str(base) + "_counterpart_avg.csv"), counterpart_avg)
    _write_csv(Path(str(base) + "_variation_of_variation_per_arm.csv"), vov_arm)
    _write_csv(Path(str(base) + "_variation_of_variation_avg.csv"), vov_avg)

    payload = {
        "coverage": coverage,
        "per_run_rows": per_run_rows,
        "means_rows": means_rows,
        "within_arm_omnibus": within_arm_omnibus,
        "within_arm_pairwise": within_arm_pairwise,
        "within_avg_omnibus": within_avg_omnibus,
        "within_avg_pairwise": within_avg_pairwise,
        "counterpart_per_arm": counterpart_arm,
        "counterpart_avg": counterpart_avg,
        "variation_of_variation_per_arm": vov_arm,
        "variation_of_variation_avg": vov_avg,
    }
    json_path = Path(str(base) + ".json")
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # Plots
    plot_total_actions_trend(per_run_rows, Path(str(base) + "_total_actions_trend.png"))
    plot_level_metric_trend(
        per_run_rows,
        metric="posts_per_active_agent_episode",
        title="Posts Trend: 4b vs 9b",
        out_path=Path(str(base) + "_posts_trend.png"),
    )
    plot_level_metric_trend(
        per_run_rows,
        metric="interactions_per_active_agent_episode",
        title="Interactions Trend: 4b vs 9b",
        out_path=Path(str(base) + "_interactions_trend.png"),
    )
    plot_pair_matrix_comparison(per_run_rows, Path(str(base) + "_pair_delta_structure.png"))

    # Markdown report
    md_path = Path(str(base) + "_report.md")
    write_markdown_report(
        md_path,
        means_rows=means_rows,
        within_arm_omnibus=within_arm_omnibus,
        within_arm_pairwise=within_arm_pairwise,
        counterpart_arm=counterpart_arm,
        counterpart_avg=counterpart_avg,
        vov_arm=vov_arm,
        vov_avg=vov_avg,
    )

    print(f"Loaded runs: {len(per_run_rows)}")
    print(f"Wrote: {json_path}")
    print(f"Wrote: {md_path}")
    print(f"Wrote figure: {Path(str(base) + '_total_actions_trend.png')}")
    print(f"Wrote figure: {Path(str(base) + '_posts_trend.png')}")
    print(f"Wrote figure: {Path(str(base) + '_interactions_trend.png')}")
    print(f"Wrote figure: {Path(str(base) + '_pair_delta_structure.png')}")


if __name__ == "__main__":
    main()
