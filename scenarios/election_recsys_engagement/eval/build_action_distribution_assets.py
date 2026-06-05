#!/usr/bin/env python3
"""Build action-distribution (percentage) tables and figures for the case study.

Outputs under scenarios/election_recsys_engagement/outputs/paper_assets:
- action_distribution_run_level.csv
- table_action_distribution_by_timeline_algorithm.csv
- table_action_distribution_by_enforcement.csv
- table_action_distribution_by_model.csv
- fig_action_distribution_timeline_algorithms.png
- fig_action_distribution_enforcement_ladder_4b.png
- fig_action_distribution_model_comparison.png
- fig_action_distribution_share_stacked_4b.png
- table_action_distribution_enforcement_detailed.tex
- table_action_distribution_model_detailed.tex
- table_action_distribution_timeline_detailed.tex
"""

from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, stdev
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import yaml

SOCIAL = ("post", "reply", "like", "repost")
ARMS = ("chronological", "recsys_twitter", "recsys_twhin")
ACTIONS = ("post", "reply", "like", "repost")

REGIME_ORDER_4B = ["like20", "real", "real1", "real2", "real_thinking"]
REGIME_LABELS = {
    "like20": "Relaxed Baseline",
    "real": "Mild",
    "real1": "Moderate",
    "real2": "Strict",
    "real_thinking": "Thinking",
}

ROOT = Path("scenarios/election_recsys_engagement/outputs")
OUT = ROOT / "paper_assets"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _extract_fixed_users(cfg: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    classes = cfg.get("agents", {}).get("persona_pipeline", {}).get("classes", {})
    if not isinstance(classes, dict):
        return out
    for class_cfg in classes.values():
        if not isinstance(class_cfg, dict):
            continue
        if "FixedAgent" not in str(class_cfg.get("class_path", "")):
            continue
        data_cfg = class_cfg.get("data", {})
        if not isinstance(data_cfg, dict):
            continue
        if data_cfg.get("source") != "inline":
            continue
        records = data_cfg.get("records", [])
        if not isinstance(records, list):
            continue
        for rec in records:
            if isinstance(rec, dict) and isinstance(rec.get("name"), str):
                out.add(rec["name"])
    return out


def _arm_from_tag(tag: str) -> str | None:
    if tag.startswith("chronological"):
        return "chronological"
    if tag.startswith("recsys_twitter"):
        return "recsys_twitter"
    if tag.startswith("recsys_twhin"):
        return "recsys_twhin"
    return None


def _regime_family_from_tag(tag: str) -> tuple[str, str] | None:
    is_9b = "_9b" in tag
    family = "9b" if is_9b else "4b"

    if "real_thinking" in tag:
        return family, "real_thinking"
    if "like_20" in tag:
        return family, "like20"
    if "real1" in tag:
        return family, "real1"
    if "real2" in tag:
        return family, "real2"
    if "real" in tag:
        return family, "real"
    if re.search(r"_like(?:_|$)", tag):
        return family, "like"
    return None


def _mean_ci(vals: list[float]) -> tuple[float, float]:
    if not vals:
        return float("nan"), float("nan")
    m = mean(vals)
    if len(vals) == 1:
        return m, 0.0
    return m, 1.96 * (stdev(vals) / math.sqrt(len(vals)))


def collect_run_level() -> list[dict[str, Any]]:
    pattern = re.compile(r"ElectionRecsys_N50_T10_clean50x10_seed(\d+)_([^/]+)")
    rows: list[dict[str, Any]] = []

    for action_path in ROOT.rglob("action_events.jsonl"):
        run_dir = action_path.parent
        m = pattern.search(str(run_dir))
        if not m:
            continue

        seed = int(m.group(1))
        if seed < 11 or seed > 35:
            continue

        tag = m.group(2)
        arm = _arm_from_tag(tag)
        fam_reg = _regime_family_from_tag(tag)
        if arm is None or fam_reg is None:
            continue
        family, regime = fam_reg

        # Never include the original 'like' baseline in distribution outputs.
        if regime == "like":
            continue

        if family == "9b" and regime not in {"like20", "real2", "real_thinking"}:
            continue

        events = _read_jsonl(action_path)
        cfg = _load_yaml(run_dir / "effective_config.yaml")

        init_users = {
            str(e.get("source_user", ""))
            for e in events
            if e.get("label") == "init_create_user"
            and isinstance(e.get("source_user"), str)
            and e.get("source_user") not in {"", "system"}
        }
        eval_users = init_users - _extract_fixed_users(cfg)

        configured_steps = _safe_int(cfg.get("sim", {}).get("num_steps"), 0)
        max_episode_seen = max((_safe_int(e.get("episode"), 0) for e in events), default=0)
        total_steps = configured_steps if configured_steps > 0 else max_episode_seen
        valid_steps = (
            set(range(1, total_steps)) if total_steps > 1 else ({1} if total_steps == 1 else set())
        )

        counts: Counter[str] = Counter()
        for e in events:
            ep = _safe_int(e.get("episode"), 0)
            if ep < 1:
                continue
            if valid_steps and ep not in valid_steps:
                continue
            u = str(e.get("source_user", ""))
            if u not in eval_users:
                continue
            lab = str(e.get("label", ""))
            if lab in SOCIAL:
                counts[lab] += 1

        total = float(sum(counts[a] for a in ACTIONS))
        if total <= 0:
            continue

        rows.append(
            {
                "run_dir": str(run_dir),
                "seed": seed,
                "family": family,
                "regime": regime,
                "arm": arm,
                "post_count": counts["post"],
                "reply_count": counts["reply"],
                "like_count": counts["like"],
                "repost_count": counts["repost"],
                "total_count": total,
                "post_pct": 100.0 * counts["post"] / total,
                "reply_pct": 100.0 * counts["reply"] / total,
                "like_pct": 100.0 * counts["like"] / total,
                "repost_pct": 100.0 * counts["repost"] / total,
            }
        )

    rows.sort(key=lambda r: (r["family"], r["regime"], r["arm"], int(r["seed"])))
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def table_by_timeline(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        grouped[(r["family"], r["regime"], r["arm"])].append(r)

    for (fam, reg, arm), sub in sorted(grouped.items()):
        row = {"family": fam, "regime": reg, "timeline_algorithm": arm, "n_runs": len(sub)}
        for a in ACTIONS:
            vals = [float(x[f"{a}_pct"]) for x in sub]
            m, ci = _mean_ci(vals)
            row[f"{a}_pct_mean"] = m
            row[f"{a}_pct_sd"] = stdev(vals) if len(vals) > 1 else 0.0
            row[f"{a}_pct_ci95"] = ci
        out.append(row)
    return out


def table_by_enforcement(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    order = REGIME_ORDER_4B
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["family"] == "4b" and r["regime"] in order:
            grouped[r["regime"]].append(r)

    for reg in order:
        sub = grouped.get(reg, [])
        if not sub:
            continue
        row = {"regime": reg, "regime_label": REGIME_LABELS[reg], "n_runs": len(sub)}
        for a in ACTIONS:
            vals = [float(x[f"{a}_pct"]) for x in sub]
            m, ci = _mean_ci(vals)
            row[f"{a}_pct_mean"] = m
            row[f"{a}_pct_sd"] = stdev(vals) if len(vals) > 1 else 0.0
            row[f"{a}_pct_ci95"] = ci
        out.append(row)
    return out


def table_by_model(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    shared = ["like20", "real2", "real_thinking"]
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["regime"] in shared and r["family"] in {"4b", "9b"}:
            grouped[(r["family"], r["regime"])].append(r)

    for (fam, reg), sub in sorted(grouped.items()):
        row = {"family": fam, "regime": reg, "n_runs": len(sub)}
        for a in ACTIONS:
            vals = [float(x[f"{a}_pct"]) for x in sub]
            m, ci = _mean_ci(vals)
            row[f"{a}_pct_mean"] = m
            row[f"{a}_pct_sd"] = stdev(vals) if len(vals) > 1 else 0.0
            row[f"{a}_pct_ci95"] = ci
        out.append(row)
    return out


def table_counts_by_timeline(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        grouped[(r["family"], r["regime"], r["arm"])].append(r)

    for (fam, reg, arm), sub in sorted(grouped.items()):
        row = {"family": fam, "regime": reg, "timeline_algorithm": arm, "n_runs": len(sub)}
        for a in ACTIONS:
            vals = [float(x[f"{a}_count"]) for x in sub]
            row[f"{a}_count_mean"] = mean(vals)
            row[f"{a}_count_sd"] = stdev(vals) if len(vals) > 1 else 0.0
        total_vals = [float(x["total_count"]) for x in sub]
        row["total_count_mean"] = mean(total_vals)
        row["total_count_sd"] = stdev(total_vals) if len(total_vals) > 1 else 0.0
        out.append(row)
    return out


def table_counts_by_model(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    shared = ["like20", "real2", "real_thinking"]
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["regime"] in shared and r["family"] in {"4b", "9b"}:
            grouped[(r["family"], r["regime"])].append(r)

    for (fam, reg), sub in sorted(grouped.items()):
        row = {"family": fam, "regime": reg, "n_runs": len(sub)}
        for a in ACTIONS:
            vals = [float(x[f"{a}_count"]) for x in sub]
            row[f"{a}_count_mean"] = mean(vals)
            row[f"{a}_count_sd"] = stdev(vals) if len(vals) > 1 else 0.0
        total_vals = [float(x["total_count"]) for x in sub]
        row["total_count_mean"] = mean(total_vals)
        row["total_count_sd"] = stdev(total_vals) if len(total_vals) > 1 else 0.0
        out.append(row)
    return out


def fig_timeline_algorithms(table_timeline: list[dict[str, Any]], out_path: Path) -> None:
    facets = [
        ("4b", "like20"),
        ("4b", "real2"),
        ("4b", "real_thinking"),
        ("9b", "like20"),
        ("9b", "real2"),
        ("9b", "real_thinking"),
    ]
    action_labels = ["Post", "Reply", "Like", "Repost"]
    colors = {"chronological": "#1f77b4", "recsys_twitter": "#ff7f0e", "recsys_twhin": "#2ca02c"}

    fig, axes = plt.subplots(2, 3, figsize=(15, 8), sharey=True)
    axes_flat = axes.flatten()

    for i, (fam, reg) in enumerate(facets):
        ax = axes_flat[i]
        sub = [r for r in table_timeline if r["family"] == fam and r["regime"] == reg]
        if not sub:
            ax.set_axis_off()
            continue

        x = np.arange(len(action_labels))
        width = 0.25
        for j, arm in enumerate(ARMS):
            rr = next((r for r in sub if r["timeline_algorithm"] == arm), None)
            if rr is None:
                continue
            means = [float(rr[f"{a}_pct_mean"]) for a in ACTIONS]
            errs = [float(rr[f"{a}_pct_ci95"]) for a in ACTIONS]
            ax.bar(
                x + (j - 1) * width,
                means,
                width=width,
                color=colors[arm],
                alpha=0.9,
                label=arm if i == 0 else None,
            )
            ax.errorbar(
                x + (j - 1) * width,
                means,
                yerr=errs,
                fmt="none",
                ecolor="black",
                elinewidth=1,
                capsize=2,
            )

        reg_label = REGIME_LABELS.get(reg, reg)
        ax.set_title(f"{fam} / {reg_label}")
        ax.set_xticks(x)
        ax.set_xticklabels(action_labels, rotation=20)
        ax.grid(axis="y", alpha=0.2)

    axes_flat[0].set_ylabel("Share (%)")
    handles, labels = axes_flat[0].get_legend_handles_labels()
    fig.legend(
        handles, labels, loc="upper center", ncol=3, frameon=False, title="Timeline algorithm"
    )
    fig.suptitle(
        "Action Distribution by Timeline Algorithm, Action-Budget Enforcement, and Model Family"
    )
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    fig.savefig(out_path, dpi=200)
    plt.close(fig)


def fig_enforcement_ladder(table_enforce: list[dict[str, Any]], out_path: Path) -> None:
    order = REGIME_ORDER_4B
    color = {"post": "#1f77b4", "reply": "#2ca02c", "like": "#ff7f0e", "repost": "#9467bd"}

    x = np.arange(len(order))
    fig, ax = plt.subplots(figsize=(11, 5.5))
    for a in ACTIONS:
        y = []
        e = []
        for reg in order:
            rr = next((r for r in table_enforce if r["regime"] == reg), None)
            if rr is None:
                y.append(float("nan"))
                e.append(float("nan"))
            else:
                y.append(float(rr[f"{a}_pct_mean"]))
                e.append(float(rr[f"{a}_pct_ci95"]))
        ax.errorbar(
            x, y, yerr=e, marker="o", linewidth=2.0, capsize=3, label=a.capitalize(), color=color[a]
        )

    ax.set_xticks(x)
    ax.set_xticklabels([REGIME_LABELS[o] for o in order], rotation=20)
    ax.set_ylabel("Share (%)")
    ax.set_title(
        "Action Distribution Across Action-Budget Enforcement (4b, Averaged Across Timeline Algorithms)"
    )
    ax.grid(axis="y", alpha=0.2)
    ax.legend(frameon=False, ncol=4)
    fig.tight_layout()
    fig.savefig(out_path, dpi=200)
    plt.close(fig)


def fig_model_comparison(table_model: list[dict[str, Any]], out_path: Path) -> None:
    regimes = ["like20", "real2", "real_thinking"]
    action_labels = ["Post", "Reply", "Like", "Repost"]

    fig, axes = plt.subplots(1, 3, figsize=(18, 6.8), sharey=True)
    for i, reg in enumerate(regimes):
        ax = axes[i]
        r4 = next((r for r in table_model if r["family"] == "4b" and r["regime"] == reg), None)
        r9 = next((r for r in table_model if r["family"] == "9b" and r["regime"] == reg), None)
        if r4 is None or r9 is None:
            ax.set_axis_off()
            continue

        x = np.arange(len(action_labels))
        width = 0.35
        m4 = [float(r4[f"{a}_pct_mean"]) for a in ACTIONS]
        e4 = [float(r4[f"{a}_pct_ci95"]) for a in ACTIONS]
        m9 = [float(r9[f"{a}_pct_mean"]) for a in ACTIONS]
        e9 = [float(r9[f"{a}_pct_ci95"]) for a in ACTIONS]

        ax.bar(x - width / 2, m4, width=width, color="#4c78a8", label="4b" if i == 0 else None)
        ax.bar(x + width / 2, m9, width=width, color="#f58518", label="9b" if i == 0 else None)
        ax.errorbar(x - width / 2, m4, yerr=e4, fmt="none", ecolor="black", elinewidth=1, capsize=2)
        ax.errorbar(x + width / 2, m9, yerr=e9, fmt="none", ecolor="black", elinewidth=1, capsize=2)

        ax.set_title(REGIME_LABELS.get(reg, reg), fontsize=20, pad=10)
        ax.set_xticks(x)
        ax.set_xticklabels(action_labels, rotation=20, fontsize=16)
        ax.tick_params(axis="y", labelsize=16)
        ax.grid(axis="y", alpha=0.2)

    axes[0].set_ylabel("Share (%)", fontsize=18)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        loc="upper center",
        ncol=2,
        frameon=False,
        title="Model family",
        prop={"size": 16},
        title_fontsize=16,
    )
    # Intentionally no figure-level title per manuscript layout request.
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    fig.savefig(out_path, dpi=260)
    plt.close(fig)


def fig_share_stacked_enforcement(table_enforce: list[dict[str, Any]], out_path: Path) -> None:
    order = REGIME_ORDER_4B
    palette = {"post": "#4c78a8", "reply": "#f58518", "like": "#54a24b", "repost": "#b279a2"}

    x = np.arange(len(order))
    post, reply, like, repost = [], [], [], []
    for reg in order:
        rr = next((r for r in table_enforce if r["regime"] == reg), None)
        if rr is None:
            post.append(0.0)
            reply.append(0.0)
            like.append(0.0)
            repost.append(0.0)
        else:
            post.append(float(rr["post_pct_mean"]))
            reply.append(float(rr["reply_pct_mean"]))
            like.append(float(rr["like_pct_mean"]))
            repost.append(float(rr["repost_pct_mean"]))

    fig, ax = plt.subplots(figsize=(14, 8.2))
    b1 = ax.bar(x, post, color=palette["post"], label="Post")
    b2 = ax.bar(x, reply, bottom=post, color=palette["reply"], label="Reply")
    bottom2 = np.array(post) + np.array(reply)
    b3 = ax.bar(x, like, bottom=bottom2, color=palette["like"], label="Like")
    bottom3 = bottom2 + np.array(like)
    b4 = ax.bar(x, repost, bottom=bottom3, color=palette["repost"], label="Repost")

    for bars, vals in [(b1, post), (b2, reply), (b3, like), (b4, repost)]:
        for rect, v in zip(bars, vals, strict=True):
            if v >= 8.0:
                ax.text(
                    rect.get_x() + rect.get_width() / 2,
                    rect.get_y() + rect.get_height() / 2,
                    f"{v:.1f}%",
                    ha="center",
                    va="center",
                    fontsize=16,
                    color="white",
                )

    ax.set_ylim(0, 100)
    ax.set_ylabel("Share of actions (%)", fontsize=20)
    ax.set_xticks(x)
    ax.set_xticklabels([REGIME_LABELS[o] for o in order], rotation=20, fontsize=18)
    ax.tick_params(axis="y", labelsize=16)
    # Intentionally no axis title per manuscript layout request.
    ax.legend(frameon=False, ncol=4, loc="upper center", bbox_to_anchor=(0.5, 1.10), fontsize=16)
    ax.grid(axis="y", alpha=0.2)
    fig.tight_layout()
    fig.savefig(out_path, dpi=260)
    plt.close(fig)


def _fmt_pm(mean_val: float, sd_val: float) -> str:
    return f"${mean_val:.2f}\\pm{sd_val:.2f}$"


def _fmt_pm_count(mean_val: float, sd_val: float) -> str:
    return f"${mean_val:.1f}\\pm{sd_val:.1f}$"


def write_latex_tables(
    table_timeline: list[dict[str, Any]],
    table_enforce: list[dict[str, Any]],
    table_model: list[dict[str, Any]],
    table_timeline_counts: list[dict[str, Any]],
    table_model_counts: list[dict[str, Any]],
    out_dir: Path,
) -> None:
    lb = "\\\\"

    lines: list[str] = []
    lines.append("% Detailed action-distribution table by action-budget enforcement")
    lines.append("\\begin{table*}[t]")
    lines.append("  \\centering")
    lines.append("  \\small")
    lines.append("  \\begin{tabular}{l c c c c c}")
    lines.append("    \\toprule")
    lines.append(f"    Regime & n & Post share & Reply share & Like share & Repost share {lb}")
    lines.append("    \\midrule")
    for reg in REGIME_ORDER_4B:
        rr = next((r for r in table_enforce if r["regime"] == reg), None)
        if rr is None:
            continue
        lines.append(
            f"    {REGIME_LABELS[reg]} & {int(rr['n_runs'])} & "
            f"{_fmt_pm(float(rr['post_pct_mean']), float(rr['post_pct_sd']))} & "
            f"{_fmt_pm(float(rr['reply_pct_mean']), float(rr['reply_pct_sd']))} & "
            f"{_fmt_pm(float(rr['like_pct_mean']), float(rr['like_pct_sd']))} & "
            f"{_fmt_pm(float(rr['repost_pct_mean']), float(rr['repost_pct_sd']))} {lb}"
        )
    lines.append("    \\bottomrule")
    lines.append("  \\end{tabular}")
    lines.append(
        "  \\caption{Action-distribution shares (\\%) by action-budget enforcement regime in 4b, aggregated across timeline algorithms. Regime naming follows: Relaxed Baseline, Mild, Moderate, Strict, Thinking.}"
    )
    lines.append("  \\label{tab:action-dist-enforcement}")
    lines.append("\\end{table*}")
    (out_dir / "table_action_distribution_action_budget_enforcement_detailed.tex").write_text(
        "\n".join(lines), encoding="utf-8"
    )

    lines = []
    lines.append("% Detailed action-distribution table by model")
    lines.append("\\begin{table*}[t]")
    lines.append("  \\centering")
    lines.append("  \\small")
    lines.append("  \\begin{tabular}{l l c c c c c}")
    lines.append("    \\toprule")
    lines.append(
        f"    Family & Regime & n & Post share & Reply share & Like share & Repost share {lb}"
    )
    lines.append("    \\midrule")
    for rr in table_model:
        reg_label = REGIME_LABELS.get(str(rr["regime"]), str(rr["regime"]))
        lines.append(
            f"    {rr['family']} & {reg_label} & {int(rr['n_runs'])} & "
            f"{_fmt_pm(float(rr['post_pct_mean']), float(rr['post_pct_sd']))} & "
            f"{_fmt_pm(float(rr['reply_pct_mean']), float(rr['reply_pct_sd']))} & "
            f"{_fmt_pm(float(rr['like_pct_mean']), float(rr['like_pct_sd']))} & "
            f"{_fmt_pm(float(rr['repost_pct_mean']), float(rr['repost_pct_sd']))} {lb}"
        )
    lines.append("    \\bottomrule")
    lines.append("  \\end{tabular}")
    lines.append(
        "  \\caption{Action-distribution shares (\\%) by model family for shared regimes.}"
    )
    lines.append("  \\label{tab:action-dist-model}")
    lines.append("\\end{table*}")
    (out_dir / "table_action_distribution_model_family_detailed.tex").write_text(
        "\n".join(lines), encoding="utf-8"
    )

    # longtable is not supported in some venues/toolchains, so emit two regular
    # table* environments split by model family.
    lines = []
    lines.append(
        "% Detailed action-distribution tables by timeline algorithm (appendix, no longtable)"
    )
    for family in ["4b", "9b"]:
        family_rows = [r for r in table_timeline if str(r.get("family")) == family]
        if not family_rows:
            continue

        lines.append("\\begin{table*}[t]")
        lines.append("  \\centering")
        lines.append("  \\scriptsize")
        lines.append("  \\begin{tabular}{l l c c c c c}")
        lines.append("    \\toprule")
        lines.append(
            f"    Regime & Timeline algorithm & n & Post share & Reply share & Like share & Repost share {lb}"
        )
        lines.append("    \\midrule")
        for rr in family_rows:
            reg_label = REGIME_LABELS.get(str(rr["regime"]), str(rr["regime"]))
            lines.append(
                f"    {reg_label} & {rr['timeline_algorithm']} & {int(rr['n_runs'])} & "
                f"{_fmt_pm(float(rr['post_pct_mean']), float(rr['post_pct_sd']))} & "
                f"{_fmt_pm(float(rr['reply_pct_mean']), float(rr['reply_pct_sd']))} & "
                f"{_fmt_pm(float(rr['like_pct_mean']), float(rr['like_pct_sd']))} & "
                f"{_fmt_pm(float(rr['repost_pct_mean']), float(rr['repost_pct_sd']))} {lb}"
            )
        lines.append("    \\bottomrule")
        lines.append("  \\end{tabular}")
        if family == "4b":
            lines.append(
                "  \\caption{Action-distribution shares (\\%) by timeline algorithm and regime for the 4b family. Regime labels: Relaxed Baseline, Mild, Moderate, Strict, Thinking.}"
            )
            lines.append("  \\label{tab:action-dist-timeline-4b}")
        else:
            lines.append(
                "  \\caption{Action-distribution shares (\\%) by timeline algorithm and regime for the 9b family (shared regimes only: Relaxed Baseline, Strict, Thinking).}"
            )
            lines.append("  \\label{tab:action-dist-timeline-9b}")
        lines.append("\\end{table*}")
        lines.append("")

    (out_dir / "table_action_distribution_timeline_algorithms_detailed.tex").write_text(
        "\n".join(lines).rstrip() + "\n", encoding="utf-8"
    )

    lines = []
    lines.append("% Detailed raw action-count table by model family for shared regimes")
    lines.append("\\begin{table*}[t]")
    lines.append("  \\centering")
    lines.append("  \\small")
    lines.append("  \\begin{tabular}{l l c c c c c c}")
    lines.append("    \\toprule")
    lines.append(
        f"    Family & Regime & n & Post count & Reply count & Like count & Repost count & Total actions {lb}"
    )
    lines.append("    \\midrule")
    for rr in table_model_counts:
        reg_label = REGIME_LABELS.get(str(rr["regime"]), str(rr["regime"]))
        lines.append(
            f"    {rr['family']} & {reg_label} & {int(rr['n_runs'])} & "
            f"{_fmt_pm_count(float(rr['post_count_mean']), float(rr['post_count_sd']))} & "
            f"{_fmt_pm_count(float(rr['reply_count_mean']), float(rr['reply_count_sd']))} & "
            f"{_fmt_pm_count(float(rr['like_count_mean']), float(rr['like_count_sd']))} & "
            f"{_fmt_pm_count(float(rr['repost_count_mean']), float(rr['repost_count_sd']))} & "
            f"{_fmt_pm_count(float(rr['total_count_mean']), float(rr['total_count_sd']))} {lb}"
        )
    lines.append("    \\bottomrule")
    lines.append("  \\end{tabular}")
    lines.append(
        "  \\caption{Raw action counts per run (mean\\,$\\pm$\\,SD) by model family for shared regimes. Includes all actions (Post, Reply, Like, Repost) and total actions.}"
    )
    lines.append("  \\label{tab:action-count-model}")
    lines.append("\\end{table*}")
    (out_dir / "table_action_counts_model_family_detailed.tex").write_text(
        "\n".join(lines), encoding="utf-8"
    )

    lines = []
    lines.append(
        "% Detailed raw action-count tables by timeline algorithm (appendix, no longtable)"
    )
    for family in ["4b", "9b"]:
        family_rows = [r for r in table_timeline_counts if str(r.get("family")) == family]
        if not family_rows:
            continue

        lines.append("\\begin{table*}[t]")
        lines.append("  \\centering")
        lines.append("  \\scriptsize")
        lines.append("  \\begin{tabular}{l l c c c c c c}")
        lines.append("    \\toprule")
        lines.append(
            f"    Regime & Timeline algorithm & n & Post count & Reply count & Like count & Repost count & Total actions {lb}"
        )
        lines.append("    \\midrule")
        for rr in family_rows:
            reg_label = REGIME_LABELS.get(str(rr["regime"]), str(rr["regime"]))
            lines.append(
                f"    {reg_label} & {rr['timeline_algorithm']} & {int(rr['n_runs'])} & "
                f"{_fmt_pm_count(float(rr['post_count_mean']), float(rr['post_count_sd']))} & "
                f"{_fmt_pm_count(float(rr['reply_count_mean']), float(rr['reply_count_sd']))} & "
                f"{_fmt_pm_count(float(rr['like_count_mean']), float(rr['like_count_sd']))} & "
                f"{_fmt_pm_count(float(rr['repost_count_mean']), float(rr['repost_count_sd']))} & "
                f"{_fmt_pm_count(float(rr['total_count_mean']), float(rr['total_count_sd']))} {lb}"
            )
        lines.append("    \\bottomrule")
        lines.append("  \\end{tabular}")
        if family == "4b":
            lines.append(
                "  \\caption{Raw action counts per run (mean\\,$\\pm$\\,SD) by timeline algorithm and regime for the 4b family. Regime labels: Relaxed Baseline, Mild, Moderate, Strict, Thinking.}"
            )
            lines.append("  \\label{tab:action-count-timeline-4b}")
        else:
            lines.append(
                "  \\caption{Raw action counts per run (mean\\,$\\pm$\\,SD) by timeline algorithm and regime for the 9b family (shared regimes only: Relaxed Baseline, Strict, Thinking).}"
            )
            lines.append("  \\label{tab:action-count-timeline-9b}")
        lines.append("\\end{table*}")
        lines.append("")

    (out_dir / "table_action_counts_timeline_algorithms_detailed.tex").write_text(
        "\n".join(lines).rstrip() + "\n", encoding="utf-8"
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    run_rows = collect_run_level()
    _write_csv(OUT / "action_distribution_run_level.csv", run_rows)

    t_timeline = table_by_timeline(run_rows)
    t_enforce = table_by_enforcement(run_rows)
    t_model = table_by_model(run_rows)
    t_timeline_counts = table_counts_by_timeline(run_rows)
    t_model_counts = table_counts_by_model(run_rows)

    _write_csv(OUT / "table_action_distribution_by_timeline_algorithms.csv", t_timeline)
    _write_csv(OUT / "table_action_distribution_by_action_budget_enforcement.csv", t_enforce)
    _write_csv(OUT / "table_action_distribution_by_model_family.csv", t_model)
    _write_csv(OUT / "table_action_counts_by_timeline_algorithms.csv", t_timeline_counts)
    _write_csv(OUT / "table_action_counts_by_model_family.csv", t_model_counts)

    fig_timeline_algorithms(t_timeline, OUT / "fig_action_distribution_timeline_algorithms.png")
    fig_enforcement_ladder(t_enforce, OUT / "fig_action_distribution_enforcement_ladder_4b.png")
    fig_model_comparison(t_model, OUT / "fig_action_distribution_model_comparison.png")
    fig_share_stacked_enforcement(t_enforce, OUT / "fig_action_distribution_share_stacked_4b.png")

    write_latex_tables(t_timeline, t_enforce, t_model, t_timeline_counts, t_model_counts, OUT)

    print(f"Wrote: {OUT / 'action_distribution_run_level.csv'}")
    print(f"Wrote: {OUT / 'table_action_distribution_by_timeline_algorithms.csv'}")
    print(f"Wrote: {OUT / 'table_action_distribution_by_action_budget_enforcement.csv'}")
    print(f"Wrote: {OUT / 'table_action_distribution_by_model_family.csv'}")
    print(f"Wrote: {OUT / 'table_action_counts_by_timeline_algorithms.csv'}")
    print(f"Wrote: {OUT / 'table_action_counts_by_model_family.csv'}")
    print(f"Wrote: {OUT / 'fig_action_distribution_timeline_algorithms.png'}")
    print(f"Wrote: {OUT / 'fig_action_distribution_enforcement_ladder_4b.png'}")
    print(f"Wrote: {OUT / 'fig_action_distribution_model_comparison.png'}")
    print(f"Wrote: {OUT / 'fig_action_distribution_share_stacked_4b.png'}")
    print(f"Wrote: {OUT / 'table_action_distribution_action_budget_enforcement_detailed.tex'}")
    print(f"Wrote: {OUT / 'table_action_distribution_model_family_detailed.tex'}")
    print(f"Wrote: {OUT / 'table_action_distribution_timeline_algorithms_detailed.tex'}")
    print(f"Wrote: {OUT / 'table_action_counts_model_family_detailed.tex'}")
    print(f"Wrote: {OUT / 'table_action_counts_timeline_algorithms_detailed.tex'}")


if __name__ == "__main__":
    main()
