"""Create trend-first regime comparison visualizations.

Focus:
- Show magnitude and direction of differences across regimes.
- Include significance as a secondary validity overlay.
- Highlight total-actions changes per arm and average across arms.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import yaml
from scipy.stats import wilcoxon

SOCIAL_LABELS = ("post", "reply", "like", "repost")
ARMS = ("chronological", "recsys_twitter", "recsys_twhin")
REGIMES = ("like20", "real", "real1", "real2", "real_thinking")
REGIME_LABELS = {
    "like20": "like_20",
    "real": "real",
    "real1": "real1",
    "real2": "real2",
    "real_thinking": "real_thinking",
}


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _read_jsonl(path: Path):
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def _load_yaml(path: Path):
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _extract_fixed_usernames_from_config(effective_config):
    fixed_usernames = set()
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


def _arm_from_tag(tag: str):
    if tag.startswith("chronological"):
        return "chronological"
    if tag.startswith("recsys_twitter"):
        return "recsys_twitter"
    if tag.startswith("recsys_twhin"):
        return "recsys_twhin"
    return None


def _regime_from_tag(tag: str):
    if "_9b" in tag:
        return None
    if "real_thinking" in tag:
        return "real_thinking"
    if "like_20" in tag:
        return "like20"
    if "real1" in tag:
        return "real1"
    if "real2" in tag:
        return "real2"
    if "real" in tag:
        return "real"
    return None


def _holm_adjust(rows, p_key="p", out_key="p_holm"):
    order = sorted(range(len(rows)), key=lambda i: rows[i][p_key])
    m = len(rows)
    prev = 0.0
    adjusted = [1.0] * m
    for rank, i in enumerate(order, start=1):
        value = min(1.0, (m - rank + 1) * rows[i][p_key])
        value = max(value, prev)
        prev = value
        adjusted[i] = value
    for i, row in enumerate(rows):
        row[out_key] = adjusted[i]


def _sig_star(p: float) -> str:
    if p < 0.001:
        return "***"
    if p < 0.01:
        return "**"
    if p < 0.05:
        return "*"
    return ""


def _mean_and_ci(values: list[float]):
    arr = np.array(values, dtype=float)
    m = float(np.mean(arr))
    if len(arr) <= 1:
        return m, 0.0
    sem = float(np.std(arr, ddof=1) / np.sqrt(len(arr)))
    return m, 1.96 * sem


def load_rows(outputs_dir: Path):
    pattern = re.compile(r"ElectionRecsys_N50_T10_clean50x10_seed(\d+)_([^/]+)")
    rows = []
    for action_path in outputs_dir.rglob("action_events.jsonl"):
        run_dir = action_path.parent
        match = pattern.search(str(run_dir))
        if not match:
            continue

        seed = int(match.group(1))
        if seed < 11 or seed > 20:
            continue

        tag = match.group(2)
        arm = _arm_from_tag(tag)
        regime = _regime_from_tag(tag)
        if arm not in ARMS or regime not in REGIMES:
            continue

        events = _read_jsonl(action_path)
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

        label_counts = Counter()
        active_users_by_step = defaultdict(set)

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

        active_agent_eps = sum(len(active_users_by_step.get(ep, set())) for ep in step_range)
        denom = float(active_agent_eps) if active_agent_eps > 0 else 1.0

        post = label_counts["post"]
        reply = label_counts["reply"]
        like = label_counts["like"]
        repost = label_counts["repost"]
        interactions = reply + like + repost
        total = post + interactions

        rows.append(
            {
                "seed": seed,
                "regime": regime,
                "arm": arm,
                "total_actions_per_active_agent_episode": total / denom,
                "posts_per_active_agent_episode": post / denom,
                "interactions_per_active_agent_episode": interactions / denom,
                "post_share": post / total if total > 0 else 0.0,
                "reply_share": reply / total if total > 0 else 0.0,
                "like_share": like / total if total > 0 else 0.0,
                "repost_share": repost / total if total > 0 else 0.0,
            }
        )

    return rows


def compute_pairwise_vs_like20(rows):
    """Pairwise by seed: each regime vs like20 for total actions, per arm."""
    out = []
    for arm in ARMS:
        metric = "total_actions_per_active_agent_episode"
        baseline = {
            r["seed"]: r[metric] for r in rows if r["arm"] == arm and r["regime"] == "like20"
        }
        block = []
        for regime in REGIMES:
            if regime == "like20":
                continue
            target = {
                r["seed"]: r[metric] for r in rows if r["arm"] == arm and r["regime"] == regime
            }
            seeds = sorted(set(target) & set(baseline))
            vals_t = [target[s] for s in seeds]
            vals_b = [baseline[s] for s in seeds]
            stat, p = wilcoxon(vals_t, vals_b, zero_method="wilcox", alternative="two-sided")
            block.append(
                {
                    "arm": arm,
                    "regime": regime,
                    "mean_delta": mean(vals_t) - mean(vals_b),
                    "p": float(p),
                    "stat": float(stat),
                }
            )

        _holm_adjust(block, "p", "p_holm")
        out.extend(block)
    return out


def save_tables(rows, outputs_dir: Path):
    import csv

    summary_path = outputs_dir / "n50_t10_clean50x10_regime_trend_summary_seeds11_20_excl_ep10.csv"
    mix_path = outputs_dir / "n50_t10_clean50x10_regime_action_mix_summary_seeds11_20_excl_ep10.csv"

    summary_rows = []
    for regime in REGIMES:
        for arm in ARMS:
            sub = [r for r in rows if r["regime"] == regime and r["arm"] == arm]
            if not sub:
                continue
            summary_rows.append(
                {
                    "regime": regime,
                    "arm": arm,
                    "n_runs": len(sub),
                    "total_actions_per_active_agent_episode": mean(
                        [x["total_actions_per_active_agent_episode"] for x in sub]
                    ),
                    "posts_per_active_agent_episode": mean(
                        [x["posts_per_active_agent_episode"] for x in sub]
                    ),
                    "interactions_per_active_agent_episode": mean(
                        [x["interactions_per_active_agent_episode"] for x in sub]
                    ),
                }
            )

    mix_rows = []
    for regime in REGIMES:
        for arm in ARMS:
            sub = [r for r in rows if r["regime"] == regime and r["arm"] == arm]
            if not sub:
                continue
            mix_rows.append(
                {
                    "regime": regime,
                    "arm": arm,
                    "n_runs": len(sub),
                    "post_share": mean([x["post_share"] for x in sub]),
                    "reply_share": mean([x["reply_share"] for x in sub]),
                    "like_share": mean([x["like_share"] for x in sub]),
                    "repost_share": mean([x["repost_share"] for x in sub]),
                }
            )

    with summary_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
        writer.writeheader()
        writer.writerows(summary_rows)

    with mix_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(mix_rows[0].keys()))
        writer.writeheader()
        writer.writerows(mix_rows)

    return summary_path, mix_path


def plot_dashboard(rows, pairwise_like20, outputs_dir: Path):
    palette = {
        "chronological": "#4C78A8",
        "recsys_twitter": "#F58518",
        "recsys_twhin": "#54A24B",
        "avg": "#1f1f1f",
    }

    x = np.arange(len(REGIMES))
    fig, axes = plt.subplots(2, 2, figsize=(16, 10))
    ax1, ax2, ax3, ax4 = axes.flatten()

    # Panel 1: total actions trends by arm + average
    for arm in ARMS:
        means = []
        cis = []
        for regime in REGIMES:
            vals = [
                r["total_actions_per_active_agent_episode"]
                for r in rows
                if r["regime"] == regime and r["arm"] == arm
            ]
            m, ci = _mean_and_ci(vals)
            means.append(m)
            cis.append(ci)
        ax1.errorbar(
            x,
            means,
            yerr=cis,
            marker="o",
            linewidth=2,
            capsize=3,
            color=palette[arm],
            label=arm,
        )

    avg_means = []
    avg_cis = []
    for regime in REGIMES:
        vals = [r["total_actions_per_active_agent_episode"] for r in rows if r["regime"] == regime]
        m, ci = _mean_and_ci(vals)
        avg_means.append(m)
        avg_cis.append(ci)

    ax1.errorbar(
        x,
        avg_means,
        yerr=avg_cis,
        marker="s",
        linewidth=3,
        capsize=4,
        color=palette["avg"],
        label="avg across arms",
    )

    ax1.set_title("Total Actions Trend Across Regimes")
    ax1.set_xticks(x)
    ax1.set_xticklabels([REGIME_LABELS[r] for r in REGIMES], rotation=20, ha="right")
    ax1.set_ylabel("total actions / active-agent-episode")
    ax1.grid(axis="y", alpha=0.3)
    ax1.legend(frameon=False, fontsize=9)

    # Panel 2: delta vs like20 per arm with significance star overlay
    width = 0.22
    offsets = {"chronological": -width, "recsys_twitter": 0.0, "recsys_twhin": width}
    comp_regimes = [r for r in REGIMES if r != "like20"]
    xx = np.arange(len(comp_regimes))

    pair_lookup = {(r["arm"], r["regime"]): r for r in pairwise_like20}

    for arm in ARMS:
        vals = [pair_lookup[(arm, rg)]["mean_delta"] for rg in comp_regimes]
        bars = ax2.bar(
            xx + offsets[arm],
            vals,
            width=width,
            color=palette[arm],
            alpha=0.85,
            label=arm,
        )
        for b, rg in zip(bars, comp_regimes):
            row = pair_lookup[(arm, rg)]
            star = _sig_star(row["p_holm"])
            if star:
                y = b.get_height()
                ax2.text(
                    b.get_x() + b.get_width() / 2,
                    y + (0.08 if y >= 0 else -0.12),
                    star,
                    ha="center",
                    va="bottom" if y >= 0 else "top",
                    fontsize=10,
                    color="#222222",
                )

    ax2.axhline(0.0, color="#333333", linewidth=1)
    ax2.set_xticks(xx)
    ax2.set_xticklabels([REGIME_LABELS[r] for r in comp_regimes], rotation=20, ha="right")
    ax2.set_ylabel("delta vs like_20")
    ax2.set_title("Total Actions Change vs like_20 (stars = Holm-significant)")
    ax2.grid(axis="y", alpha=0.3)
    ax2.legend(frameon=False, fontsize=9)

    # Panel 3: average across arms regime shifts for key levels
    for metric, color, label in [
        ("total_actions_per_active_agent_episode", "#1f1f1f", "total"),
        ("posts_per_active_agent_episode", "#C44E52", "posts"),
        ("interactions_per_active_agent_episode", "#2A9D8F", "interactions"),
    ]:
        means = []
        cis = []
        for regime in REGIMES:
            vals = [r[metric] for r in rows if r["regime"] == regime]
            m, ci = _mean_and_ci(vals)
            means.append(m)
            cis.append(ci)
        ax3.errorbar(
            x,
            means,
            yerr=cis,
            marker="o",
            linewidth=2,
            capsize=3,
            color=color,
            label=label,
        )

    ax3.set_title("Average Across Arms: Regime Shift in Key Levels")
    ax3.set_xticks(x)
    ax3.set_xticklabels([REGIME_LABELS[r] for r in REGIMES], rotation=20, ha="right")
    ax3.set_ylabel("per active-agent-episode")
    ax3.grid(axis="y", alpha=0.3)
    ax3.legend(frameon=False, fontsize=9)

    # Panel 4: stacked action mix shares averaged across arms
    share_means = {k: [] for k in ("post_share", "reply_share", "like_share", "repost_share")}
    for regime in REGIMES:
        arm_means = {}
        for share in share_means:
            vals_by_arm = []
            for arm in ARMS:
                sub = [r[share] for r in rows if r["regime"] == regime and r["arm"] == arm]
                vals_by_arm.append(mean(sub))
            arm_means[share] = mean(vals_by_arm)
        for share in share_means:
            share_means[share].append(arm_means[share])

    bottoms = np.zeros(len(REGIMES))
    colors = {
        "post_share": "#457B9D",
        "reply_share": "#E76F51",
        "like_share": "#2A9D8F",
        "repost_share": "#E9C46A",
    }
    labels = {
        "post_share": "post",
        "reply_share": "reply",
        "like_share": "like",
        "repost_share": "repost",
    }
    for share in ("post_share", "reply_share", "like_share", "repost_share"):
        vals = np.array(share_means[share])
        ax4.bar(x, vals, bottom=bottoms, color=colors[share], label=labels[share], width=0.72)
        bottoms += vals

    ax4.set_title("Action Mix Shift Across Regimes (avg across arms)")
    ax4.set_xticks(x)
    ax4.set_xticklabels([REGIME_LABELS[r] for r in REGIMES], rotation=20, ha="right")
    ax4.set_ylim(0, 1)
    ax4.set_ylabel("share of total actions")
    ax4.grid(axis="y", alpha=0.3)
    ax4.legend(frameon=False, fontsize=9, ncol=2)

    fig.suptitle(
        "Regime Comparison Dashboard: Difference/Trend First, Significance as Validation",
        fontsize=14,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.97))

    out_png = (
        outputs_dir / "n50_t10_clean50x10_regime_differences_dashboard_seeds11_20_excl_ep10.png"
    )
    fig.savefig(out_png, dpi=180)
    plt.close(fig)
    return out_png


def main():
    outputs_dir = Path("worlds/election_recsys_engagement/outputs")
    rows = load_rows(outputs_dir)
    pairwise_like20 = compute_pairwise_vs_like20(rows)

    summary_csv, mix_csv = save_tables(rows, outputs_dir)
    dashboard_png = plot_dashboard(rows, pairwise_like20, outputs_dir)

    out_json = (
        outputs_dir / "n50_t10_clean50x10_regime_differences_dashboard_seeds11_20_excl_ep10.json"
    )
    payload = {
        "summary_csv": str(summary_csv),
        "mix_csv": str(mix_csv),
        "dashboard_png": str(dashboard_png),
        "pairwise_like20": pairwise_like20,
        "n_rows": len(rows),
    }
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"Loaded rows: {len(rows)}")
    print(f"Wrote: {summary_csv}")
    print(f"Wrote: {mix_csv}")
    print(f"Wrote: {dashboard_png}")
    print(f"Wrote: {out_json}")


if __name__ == "__main__":
    main()
