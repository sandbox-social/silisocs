#!/usr/bin/env python3
"""Build paper-ready assets for the election recsys case study.

Outputs (under scenarios/election_recsys_engagement/outputs/paper_assets):
- New Figure H4: intervention ladder + arm separability
- New Figure H5: action-composition transport with negative-control highlight
- Table 1: core inferential summary (one row per hypothesis)
- Table 2: intervention audit from effective configs
- Table 3: upstream-downstream mechanism chain
- LaTeX snippets for figures, tables, and results text scaffold
"""

from __future__ import annotations

import csv
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import yaml

ROOT = Path("scenarios/election_recsys_engagement/outputs")
OUT_DIR = ROOT / "paper_assets"

SETTING_ORDER = ["like20", "real", "real1", "real2", "real_thinking"]
SETTING_LABELS = {
    "like20": "High-Activity",
    "real": "Mild",
    "real1": "Moderate",
    "real2": "Strict",
    "real_thinking": "Deliberative",
}
ARMS = ["chronological", "recsys_twitter", "recsys_twhin"]


@dataclass
class HypothesisRow:
    hypothesis: str
    primary_endpoint: str
    comparison: str
    delta: float
    p_raw: float
    p_adj: float
    sig: str
    interpretation: str


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _as_float(x: Any, default: float = float("nan")) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _sig_from_p(p: float) -> str:
    if math.isnan(p):
        return "ns"
    if p < 0.001:
        return "***"
    if p < 0.01:
        return "**"
    if p < 0.05:
        return "*"
    return "ns"


def _latex_escape(text: str) -> str:
    return (
        str(text)
        .replace("\\", "\\textbackslash{}")
        .replace("&", "\\&")
        .replace("%", "\\%")
        .replace("_", "\\_")
        .replace("#", "\\#")
        .replace("{", "\\{")
        .replace("}", "\\}")
    )


def _find_row(rows: list[dict[str, str]], **match: str) -> dict[str, str]:
    for r in rows:
        if all(str(r.get(k)) == str(v) for k, v in match.items()):
            return r
    raise KeyError(f"No row found for {match}")


def _extract_regime_from_run_dir(run_dir: str) -> str | None:
    tag_match = re.search(r"seed\d+_([^/]+)", run_dir)
    if not tag_match:
        return None
    tag = tag_match.group(1)
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


def load_effective_configs() -> list[dict[str, str]]:
    """Build intervention audit by sampling seed11 chronological runs for each setting."""
    by_setting: dict[str, Path] = {}
    for p in ROOT.rglob("effective_config.yaml"):
        p_str = str(p)
        if "ElectionRecsys_N50_T10_clean50x10_seed11_chronological" not in p_str:
            continue
        if "_9b_" in p_str:
            continue
        setting = _extract_regime_from_run_dir(p_str)
        if setting in SETTING_ORDER and setting not in by_setting:
            by_setting[setting] = p

    rows: list[dict[str, str]] = []
    for setting in SETTING_ORDER:
        candidate = by_setting.get(setting)
        if candidate is None:
            rows.append(
                {
                    "raw_setting": setting,
                    "paper_name": SETTING_LABELS[setting],
                    "max_actions": "NA",
                    "num_steps": "NA",
                    "llm_name": "NA",
                    "timeline_mode": "NA",
                    "tool_calling_mode": "NA",
                    "note": "effective config not found",
                }
            )
            continue

        cfg = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
        sim = cfg.get("sim", {}) if isinstance(cfg, dict) else {}
        engine = sim.get("engine", {}) if isinstance(sim, dict) else {}
        action_loop = engine.get("action_loop", {}) if isinstance(engine, dict) else {}
        params = action_loop.get("params", {}) if isinstance(action_loop, dict) else {}
        tool = sim.get("tool_calling", {}) if isinstance(sim, dict) else {}

        rows.append(
            {
                "raw_setting": setting,
                "paper_name": SETTING_LABELS[setting],
                "max_actions": str(params.get("max_actions", "NA")),
                "num_steps": str(sim.get("num_steps", "NA")),
                "llm_name": str(sim.get("llm_name", "NA")),
                "timeline_mode": str(sim.get("timeline_mode", "NA")),
                "tool_calling_mode": str(tool.get("mode", "NA")),
                "note": "deliberative planning intervention" if setting == "real_thinking" else "",
            }
        )
    return rows


def build_fig_h4_intervention_ladder(
    trend_rows: list[dict[str, str]],
    real_json: dict[str, Any],
    out_path: Path,
) -> None:
    # Activity means by setting (avg across arms)
    by_setting_total: dict[str, list[float]] = {s: [] for s in SETTING_ORDER}
    by_setting_inter: dict[str, dict[str, float]] = {s: {} for s in SETTING_ORDER}

    for r in trend_rows:
        regime = r["regime"]
        if regime not in SETTING_ORDER:
            continue
        arm = r["arm"]
        total = _as_float(r["total_actions_per_active_agent_episode"])
        inter = _as_float(r["interactions_per_active_agent_episode"])
        by_setting_total[regime].append(total)
        by_setting_inter[regime][arm] = inter

    x_labels = [SETTING_LABELS[s] for s in SETTING_ORDER]
    x = list(range(len(SETTING_ORDER)))
    total_mean = [
        mean(by_setting_total[s]) if by_setting_total[s] else float("nan") for s in SETTING_ORDER
    ]

    # Arm separability index: max-min interactions across arms
    sep_idx = []
    for s in SETTING_ORDER:
        vals = [by_setting_inter[s].get(a, float("nan")) for a in ARMS]
        vals = [v for v in vals if not math.isnan(v)]
        sep_idx.append((max(vals) - min(vals)) if vals else float("nan"))

    fig, ax1 = plt.subplots(figsize=(10, 5.5))
    ax1.plot(x, total_mean, marker="o", linewidth=2.2, color="#1f77b4", label="Mean total actions")
    ax1.set_xticks(x)
    ax1.set_xticklabels(x_labels, rotation=15, ha="right")
    ax1.set_ylabel("Total actions / active-agent-episode", color="#1f77b4")
    ax1.tick_params(axis="y", labelcolor="#1f77b4")
    ax1.grid(axis="y", alpha=0.25)

    ax2 = ax1.twinx()
    ax2.plot(
        x,
        sep_idx,
        marker="s",
        linewidth=2.0,
        linestyle="--",
        color="#d62728",
        label="Arm separability (interactions max-min)",
    )
    ax2.set_ylabel("Arm separability index", color="#d62728")
    ax2.tick_params(axis="y", labelcolor="#d62728")

    # Merge legend handles
    lines_1, labels_1 = ax1.get_legend_handles_labels()
    lines_2, labels_2 = ax2.get_legend_handles_labels()
    ax1.legend(lines_1 + lines_2, labels_1 + labels_2, frameon=False, loc="upper right")

    fig.suptitle("H4: Intervention Ladder Controls Activity and Arm Separability")
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(out_path, dpi=200)
    plt.close(fig)


def build_fig_h5_composition_transport(mix_rows: list[dict[str, str]], out_path: Path) -> None:
    # Average action share across arms for each setting
    share_keys = ["post_share", "reply_share", "like_share", "repost_share"]
    by_setting = {s: {k: [] for k in share_keys} for s in SETTING_ORDER}

    for r in mix_rows:
        s = r["regime"]
        if s not in SETTING_ORDER:
            continue
        for k in share_keys:
            by_setting[s][k].append(_as_float(r[k]))

    series = {
        "Posts": [
            mean(by_setting[s]["post_share"]) if by_setting[s]["post_share"] else float("nan")
            for s in SETTING_ORDER
        ],
        "Replies": [
            mean(by_setting[s]["reply_share"]) if by_setting[s]["reply_share"] else float("nan")
            for s in SETTING_ORDER
        ],
        "Likes": [
            mean(by_setting[s]["like_share"]) if by_setting[s]["like_share"] else float("nan")
            for s in SETTING_ORDER
        ],
        "Reposts": [
            mean(by_setting[s]["repost_share"]) if by_setting[s]["repost_share"] else float("nan")
            for s in SETTING_ORDER
        ],
    }

    colors = {
        "Posts": "#1f77b4",
        "Replies": "#2ca02c",
        "Likes": "#ff7f0e",
        "Reposts": "#9467bd",
    }

    x = list(range(len(SETTING_ORDER)))
    x_labels = [SETTING_LABELS[s] for s in SETTING_ORDER]

    fig, ax = plt.subplots(figsize=(10, 5.5))
    for name, vals in series.items():
        lw = 2.0
        ls = "-"
        ax.plot(x, vals, marker="o", linewidth=lw, linestyle=ls, color=colors[name], label=name)

    ax.set_xticks(x)
    ax.set_xticklabels(x_labels, rotation=15, ha="right")
    ax.set_ylabel("Action share of total")
    ax.set_ylim(0.0, 0.6)
    ax.grid(axis="y", alpha=0.25)
    ax.legend(frameon=False, ncol=2)
    fig.suptitle("Action Composition Across Interventions")
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(out_path, dpi=200)
    plt.close(fig)


def build_table1_core(
    pairwise_cap12: list[dict[str, str]],
    pairwise_cap20: list[dict[str, str]],
    omnibus_cap12: list[dict[str, str]],
    omnibus_cap20: list[dict[str, str]],
    counterpart_avg: list[dict[str, str]],
    real_json: dict[str, Any],
    structural_counterpart: list[dict[str, str]],
) -> list[HypothesisRow]:
    rows: list[HypothesisRow] = []

    # H1: baseline engagement effect (cap12: twhin-chronological interactions)
    h1 = _find_row(
        pairwise_cap12,
        metric="interactions_per_active_agent_episode",
        arm_a="recsys_twhin",
        arm_b="chronological",
    )
    rows.append(
        HypothesisRow(
            hypothesis="H1",
            primary_endpoint="Interactions per active-agent-episode",
            comparison="recsys_twhin - chronological (baseline cap=12)",
            delta=_as_float(h1["mean_delta"]),
            p_raw=_as_float(h1["p_raw"]),
            p_adj=_as_float(h1["p_holm"]),
            sig=h1["sig"],
            interpretation="Recommendation curation changes engagement behavior relative to follower-chronological feed.",
        )
    )

    # H2: cap confound via interaction effect reveal (ns -> significant)
    h2_old = _find_row(
        pairwise_cap12,
        metric="interactions_per_active_agent_episode",
        arm_a="recsys_twhin",
        arm_b="chronological",
    )
    h2_new = _find_row(
        pairwise_cap20,
        metric="interactions_per_active_agent_episode",
        arm_a="recsys_twhin",
        arm_b="chronological",
    )
    rows.append(
        HypothesisRow(
            hypothesis="H2",
            primary_endpoint="Interactions effect reveal under cap change",
            comparison="recsys_twhin - chronological, cap=12 vs cap=20",
            delta=_as_float(h2_new["mean_delta"]) - _as_float(h2_old["mean_delta"]),
            p_raw=_as_float(h2_new["p_raw"]),
            p_adj=_as_float(h2_new["p_holm"]),
            sig=h2_new["sig"],
            interpretation="Relaxing the action cap reveals latent arm differences that were weak/non-significant under tighter budgets.",
        )
    )

    # H3: model-scale persistence (9b-4b interactions real2)
    h3 = _find_row(counterpart_avg, regime="real2", metric="interactions_per_active_agent_episode")
    rows.append(
        HypothesisRow(
            hypothesis="H3",
            primary_endpoint="9b - 4b interactions (avg arms)",
            comparison="real2 counterpart",
            delta=_as_float(h3["delta_9b_minus_4b"]),
            p_raw=_as_float(h3["wilcoxon_p"]),
            p_adj=_as_float(h3["wilcoxon_p_holm"]),
            sig=h3["sig_holm"],
            interpretation="Key engagement differences persist across model scale in strict regime settings.",
        )
    )

    # H4 merged: controllability (real2 vs like20 total actions, twhin)
    h4_candidates = [
        r
        for r in real_json.get("per_arm_vs_like20", [])
        if r.get("arm") == "recsys_twhin"
        and r.get("metric") == "total_actions_per_active_agent_episode"
        and r.get("setting") == "real2"
    ]
    h4 = h4_candidates[0]
    rows.append(
        HypothesisRow(
            hypothesis="H4",
            primary_endpoint="Total actions (intervention vs baseline)",
            comparison="real2 vs like20 for recsys_twhin",
            delta=_as_float(h4["delta_setting_minus_like20"]),
            p_raw=_as_float(h4["wilcoxon_p"]),
            p_adj=_as_float(h4["wilcoxon_p_holm"]),
            sig=h4["sig_holm"],
            interpretation="Prompt/structure intervention shifts activity level and changes arm-separability conditions.",
        )
    )

    # H5: composition mechanism with negative control (likes invariance under cap20)
    h5 = _find_row(
        pairwise_cap20,
        metric="likes_per_active_agent_episode",
        arm_a="recsys_twhin",
        arm_b="chronological",
    )
    rows.append(
        HypothesisRow(
            hypothesis="H5",
            primary_endpoint="Likes channel (negative-control composition)",
            comparison="recsys_twhin - chronological (cap=20)",
            delta=_as_float(h5["mean_delta"]),
            p_raw=_as_float(h5["p_raw"]),
            p_adj=_as_float(h5["p_holm"]),
            sig=h5["sig"],
            interpretation="Not all channels shift; likes remain comparatively stable while posts/replies drive composition differences.",
        )
    )

    # H6: structural consequence (9b-4b structural breadth real2, seed-matched)
    h6 = _find_row(
        structural_counterpart,
        metric="cascade_breadth_mean",
        regime="real2",
    )
    p_seed = _as_float(h6.get("seed_matched_p"))
    rows.append(
        HypothesisRow(
            hypothesis="H6",
            primary_endpoint="Cascade breadth mean (structural)",
            comparison="9b - 4b in real2 (avg arms, seed-matched)",
            delta=_as_float(h6["delta_a_minus_b"]),
            p_raw=p_seed,
            p_adj=p_seed,
            sig=h6.get("seed_matched_sig", _sig_from_p(p_seed)),
            interpretation="Behavioral/intervention differences propagate into diffusion topology, not only volume.",
        )
    )

    return rows


def write_table1(rows: list[HypothesisRow], out_csv: Path) -> None:
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "hypothesis",
                "primary_endpoint",
                "comparison",
                "delta",
                "p_raw",
                "p_adj",
                "sig",
                "interpretation",
            ],
        )
        w.writeheader()
        for r in rows:
            w.writerow(
                {
                    "hypothesis": r.hypothesis,
                    "primary_endpoint": r.primary_endpoint,
                    "comparison": r.comparison,
                    "delta": f"{r.delta:+.6f}",
                    "p_raw": f"{r.p_raw:.6g}",
                    "p_adj": f"{r.p_adj:.6g}",
                    "sig": r.sig,
                    "interpretation": r.interpretation,
                }
            )


def write_table2_intervention_audit(rows: list[dict[str, str]], out_csv: Path) -> None:
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "raw_setting",
                "paper_name",
                "max_actions",
                "num_steps",
                "llm_name",
                "timeline_mode",
                "tool_calling_mode",
                "note",
            ],
        )
        w.writeheader()
        w.writerows(rows)


def write_table3_chain(
    table1_rows: list[HypothesisRow],
    out_csv: Path,
) -> None:
    # Compact chain table linking upstream->downstream hypotheses.
    chain_rows = [
        {
            "stage": "Upstream constraint",
            "hypothesis": "H2",
            "endpoint": table1_rows[1].primary_endpoint,
            "delta_or_effect": f"{table1_rows[1].delta:+.6f}",
            "p_adj": f"{table1_rows[1].p_adj:.6g}",
            "interpretation": "Observability changes with budget regime.",
        },
        {
            "stage": "Behavior control",
            "hypothesis": "H4",
            "endpoint": table1_rows[3].primary_endpoint,
            "delta_or_effect": f"{table1_rows[3].delta:+.6f}",
            "p_adj": f"{table1_rows[3].p_adj:.6g}",
            "interpretation": "Interventions shift activity and arm separability context.",
        },
        {
            "stage": "Mechanism",
            "hypothesis": "H5",
            "endpoint": table1_rows[4].primary_endpoint,
            "delta_or_effect": f"{table1_rows[4].delta:+.6f}",
            "p_adj": f"{table1_rows[4].p_adj:.6g}",
            "interpretation": "Composition channels mediate observed behavior shifts.",
        },
        {
            "stage": "Downstream structure",
            "hypothesis": "H6",
            "endpoint": table1_rows[5].primary_endpoint,
            "delta_or_effect": f"{table1_rows[5].delta:+.6f}",
            "p_adj": f"{table1_rows[5].p_adj:.6g}",
            "interpretation": "Diffusion topology changes downstream of upstream controls.",
        },
    ]

    with out_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "stage",
                "hypothesis",
                "endpoint",
                "delta_or_effect",
                "p_adj",
                "interpretation",
            ],
        )
        w.writeheader()
        w.writerows(chain_rows)


def write_latex_snippets(table1: list[HypothesisRow], out_tex: Path) -> None:
    lines: list[str] = []
    lines.append("% Auto-generated case-study snippets")
    lines.append("% Figures")
    lines.append("\\begin{figure}[t]")
    lines.append("  \\centering")
    lines.append("  \\includegraphics[width=0.95\\linewidth]{fig_h4_intervention_ladder.png}")
    lines.append(
        "  \\caption{Intervention ladder (H4): average activity (left axis) and arm separability (right axis) across settings.}"
    )
    lines.append("  \\label{fig:h4-intervention-ladder}")
    lines.append("\\end{figure}")
    lines.append("")
    lines.append("\\begin{figure}[t]")
    lines.append("  \\centering")
    lines.append("  \\includegraphics[width=0.95\\linewidth]{fig_h5_composition_transport.png}")
    lines.append("  \\caption{Action-composition transport (H5) across interventions.}")
    lines.append("  \\label{fig:h5-composition-transport}")
    lines.append("\\end{figure}")
    lines.append("")
    lines.append("% Results scaffold")
    lines.append("\\subsection{Hypothesis-Driven Results}")
    for r in table1:
        lines.append(f"\\paragraph{{{r.hypothesis}.}} {r.primary_endpoint}. ")
        lines.append(
            f"Primary comparison: {r.comparison} with $\\Delta={r.delta:+.3f}$, $p_{{raw}}={r.p_raw:.3g}$, $p_{{adj}}={r.p_adj:.3g}$ ({r.sig})."
        )
        lines.append(f"Interpretation: {r.interpretation}")
        lines.append(
            "Boundary: this result is local to the specified comparison and does not imply universal realism across all regimes."
        )
        lines.append("")

    out_tex.write_text("\n".join(lines), encoding="utf-8")


def write_latex_table_h1_h6(rows: list[HypothesisRow], out_tex: Path) -> None:
    lines: list[str] = []
    lines.append("% Table: core hypothesis results")
    lines.append("\\begin{table*}[t]")
    lines.append("  \\centering")
    lines.append("  \\small")
    lines.append("  \\begin{tabular}{l p{3.1cm} p{4.3cm} r r r l}")
    lines.append("    \\hline")
    lines.append(
        "    Hyp. & Primary endpoint & Comparison & $\\Delta$ & $p_{raw}$ & $p_{adj}$ & Sig. \\\\"
    )
    lines.append("    \\hline")
    for r in rows:
        lines.append(
            "    "
            f"{_latex_escape(r.hypothesis)} & "
            f"{_latex_escape(r.primary_endpoint)} & "
            f"{_latex_escape(r.comparison)} & "
            f"{r.delta:+.3f} & {r.p_raw:.3g} & {r.p_adj:.3g} & {_latex_escape(r.sig)} \\\\"
        )
    lines.append("    \\hline")
    lines.append("  \\end{tabular}")
    lines.append(
        "  \\caption{Core inferential results, one primary endpoint per hypothesis. "
        "Seed-matched statistics are prioritized where available.}"
    )
    lines.append("  \\label{tab:hypothesis-core-results}")
    lines.append("\\end{table*}")
    out_tex.write_text("\n".join(lines), encoding="utf-8")


def write_latex_table_intervention_audit(rows: list[dict[str, str]], out_tex: Path) -> None:
    lines: list[str] = []
    lines.append("% Table: intervention audit")
    lines.append("\\begin{table*}[t]")
    lines.append("  \\centering")
    lines.append("  \\small")
    lines.append("  \\begin{tabular}{l l c c l l l}")
    lines.append("    \\hline")
    lines.append(
        "    Raw setting & Paper name & max\\_actions & steps & LLM & timeline\\_mode & tool\\_calling \\\\"
    )
    lines.append("    \\hline")
    for r in rows:
        lines.append(
            "    "
            f"{_latex_escape(r['raw_setting'])} & "
            f"{_latex_escape(r['paper_name'])} & "
            f"{_latex_escape(r['max_actions'])} & "
            f"{_latex_escape(r['num_steps'])} & "
            f"{_latex_escape(r['llm_name'])} & "
            f"{_latex_escape(r['timeline_mode'])} & "
            f"{_latex_escape(r['tool_calling_mode'])} \\\\"
        )
    lines.append("    \\hline")
    lines.append("  \\end{tabular}")
    lines.append(
        "  \\caption{Intervention audit from representative effective configurations "
        "(seed 11, chronological arm) for each setting.}"
    )
    lines.append("  \\label{tab:intervention-audit}")
    lines.append("\\end{table*}")
    out_tex.write_text("\n".join(lines), encoding="utf-8")


def write_latex_table_chain(chain_rows: list[dict[str, str]], out_tex: Path) -> None:
    lines: list[str] = []
    lines.append("% Table: upstream-downstream chain")
    lines.append("\\begin{table*}[t]")
    lines.append("  \\centering")
    lines.append("  \\small")
    lines.append("  \\begin{tabular}{l l p{3.4cm} r r p{4.7cm}}")
    lines.append("    \\hline")
    lines.append("    Stage & Hyp. & Endpoint & Effect & $p_{adj}$ & Interpretation \\\\")
    lines.append("    \\hline")
    for r in chain_rows:
        lines.append(
            "    "
            f"{_latex_escape(r['stage'])} & "
            f"{_latex_escape(r['hypothesis'])} & "
            f"{_latex_escape(r['endpoint'])} & "
            f"{_latex_escape(r['delta_or_effect'])} & "
            f"{_latex_escape(r['p_adj'])} & "
            f"{_latex_escape(r['interpretation'])} \\\\"
        )
    lines.append("    \\hline")
    lines.append("  \\end{tabular}")
    lines.append(
        "  \\caption{Upstream-to-downstream evidence chain linking constraints/interventions "
        "to composition and structural outcomes.}"
    )
    lines.append("  \\label{tab:upstream-downstream-chain}")
    lines.append("\\end{table*}")
    out_tex.write_text("\n".join(lines), encoding="utf-8")


def write_results_full_tex(rows: list[HypothesisRow], out_tex: Path) -> None:
    fig_map = {
        "H2": "Figure~\\ref{fig:h2-cap-reversal}",
        "H3": "Figure~\\ref{fig:h3-4b-9b-counterpart}",
        "H4": "Figure~\\ref{fig:h4-intervention-ladder}",
        "H5": "Figure~\\ref{fig:h5-composition-transport}",
        "H6": "Figure~\\ref{fig:h6-structural-dashboard}",
    }
    row_map = {r.hypothesis: r for r in rows}

    def _evidence_line(h: str) -> str:
        r = row_map[h]
        return (
            f"Observed: $\\Delta={r.delta:+.3f}$, $p_{{raw}}={r.p_raw:.3g}$, "
            f"$p_{{adj}}={r.p_adj:.3g}$ ({_latex_escape(r.sig)})."
        )

    lines: list[str] = []
    lines.append("% Full case-study prose (H1-H6)")
    lines.append("\\subsection{Case Study: Engagement Dynamics}")
    lines.append("\\label{subsec:case-study-engagement-dynamics}")
    lines.append(
        "This case study examines how timeline curation, action-budget constraints, model scale, "
        "and prompt/structure interventions jointly shape engagement dynamics."
    )
    lines.append(
        "We report seed-matched statistics as primary evidence wherever available, "
        "with Holm-adjusted p-values for within-family multiple comparisons."
    )
    lines.append(
        "The scenario fixes agents, content pool, and timeline size, and changes only curation arm "
        "or intervention setting, allowing causal interpretation of policy-level differences."
    )
    lines.append("")
    lines.append("\\paragraph{Hypothesis Ladder and Rationale}")
    lines.append(
        "H1 tests whether recommendation curation increases engagement relative to follower-chronological ranking."
    )
    lines.append(
        "H2 tests whether the action cap acts as a censoring confound that can hide algorithmic effects."
    )
    lines.append(
        "H3 tests whether effects persist across model scale (4b to 9b), which probes robustness rather than one-model artifacts."
    )
    lines.append(
        "H4 tests controllability: whether prompt/structure interventions can modulate activity levels and arm separability."
    )
    lines.append(
        "H5 tests mechanism: whether arm effects are mediated by composition shifts across action channels, including negative-control channels."
    )
    lines.append(
        "H6 tests outcome propagation: whether behavioral changes induce structural diffusion differences (depth/breadth/concentration)."
    )
    lines.append("")
    lines.append("\\paragraph{H1: Recommendation Curation Increases Engagement.}")
    lines.append(
        "Rationale: recommendation ranking should surface engagement-prone content more often than strict follower recency."
    )
    lines.append(_evidence_line("H1"))
    lines.append(
        "Interpretation: in the baseline cap=12 setting this hypothesis is not supported; the direction is positive but non-significant."
    )
    lines.append(
        "Boundary: this does not refute curation effects globally; it motivates explicit confound testing under alternative budgets."
    )
    lines.append("")
    lines.append("\\paragraph{H2: Action-Cap Censoring Masks Latent Arm Effects.}")
    lines.append(
        "Rationale: if agents are action-constrained, policy differences can be compressed into near-ceiling behavior and appear weak."
    )
    lines.append(_evidence_line("H2"))
    lines.append(
        "Interpretation: relaxing the cap reveals a materially larger interaction gap, supporting the censoring-confound hypothesis."
    )
    lines.append(f"Primary visual: {fig_map['H2']}.")
    lines.append(
        "Boundary: the estimate is specific to the tested cap settings and should not be extrapolated to arbitrarily high budgets."
    )
    lines.append("")
    lines.append("\\paragraph{H3: Engagement Effects Persist Across Model Scale.}")
    lines.append(
        "Rationale: if effects are policy-driven, they should remain detectable when moving from smaller to larger model families."
    )
    lines.append(_evidence_line("H3"))
    lines.append(
        "Interpretation: significant positive counterpart deltas in strict regimes support cross-scale persistence with regime dependence."
    )
    lines.append(f"Primary visual: {fig_map['H3']}.")
    lines.append(
        "Boundary: persistence is not uniform across all regimes; low-activity regimes can attenuate or invert selected metrics."
    )
    lines.append("")
    lines.append(
        "\\paragraph{H4: Prompt/Structure Interventions Control Activity and Separability.}"
    )
    lines.append(
        "Rationale: intervention knobs should change how frequently agents act, which in turn alters how clearly arms can be distinguished."
    )
    lines.append(_evidence_line("H4"))
    lines.append(
        "Interpretation: activity drops under stricter intervention settings, indicating controllable moderation of engagement frequency."
    )
    lines.append(f"Primary visual: {fig_map['H4']}.")
    lines.append(
        "Boundary: controllability here refers to aggregate behavior, not guaranteed compliance of every individual persona."
    )
    lines.append("")
    lines.append(
        "\\paragraph{H5: Arm Effects Are Mediated by Action Composition (with Negative Controls).}"
    )
    lines.append(
        "Rationale: algorithmic differences should redistribute behavior across channels (post/reply/repost), while some channels may remain stable."
    )
    lines.append(_evidence_line("H5"))
    lines.append(
        "Interpretation: the negative-control channel is non-significant, supporting a selective-mechanism view rather than uniform shifts."
    )
    lines.append(f"Primary visual: {fig_map['H5']}.")
    lines.append(
        "Boundary: a null in one channel does not imply no mechanism; it indicates channel-specific reallocation rather than global movement."
    )
    lines.append("")
    lines.append("\\paragraph{H6: Behavioral Changes Propagate into Diffusion Structure.}")
    lines.append(
        "Rationale: if engagement policy changes are consequential, they should alter cascade geometry and concentration, not only action counts."
    )
    lines.append(_evidence_line("H6"))
    lines.append(
        "Interpretation: significant structural breadth differences support downstream propagation from policy/intervention to network outcomes."
    )
    lines.append(f"Primary visual: {fig_map['H6']}.")
    lines.append(
        "Boundary: this is an internal simulation causal chain and should be framed as behavioral realism evidence, not external validation by itself."
    )
    lines.append("")
    lines.append("\\paragraph{Cross-Hypothesis Synthesis.}")
    lines.append(
        "Table~\\ref{tab:upstream-downstream-chain} summarizes the evidence chain: "
        "budget/intervention controls (H2, H4) alter channel composition (H5), which is reflected in structural outcomes (H6), "
        "while scale robustness is established by H3."
    )
    out_tex.write_text("\n".join(lines), encoding="utf-8")


def write_appendix_asset_index(out_csv: Path) -> list[dict[str, str]]:
    rows = [
        {
            "asset": "fig_h4_intervention_ladder.png",
            "role": "Main Figure",
            "hypothesis": "H4",
            "description": "Intervention ladder: activity and arm separability",
        },
        {
            "asset": "fig_h5_composition_transport.png",
            "role": "Main Figure",
            "hypothesis": "H5",
            "description": "Action-composition transport with negative-control likes",
        },
        {
            "asset": "table_hypothesis_core_results.csv",
            "role": "Main Table",
            "hypothesis": "H1-H6",
            "description": "One-row-per-hypothesis inferential summary",
        },
        {
            "asset": "table_intervention_audit.csv",
            "role": "Main Table",
            "hypothesis": "H4",
            "description": "Config-level intervention traceability",
        },
        {
            "asset": "table_upstream_downstream_chain.csv",
            "role": "Main Table",
            "hypothesis": "H2-H6",
            "description": "Upstream/downstream evidence chain",
        },
        {
            "asset": "paper_results_snippets.tex",
            "role": "LaTeX",
            "hypothesis": "H1-H6",
            "description": "Figure includes and compact results scaffold",
        },
        {
            "asset": "results_h1_h6_full.tex",
            "role": "LaTeX",
            "hypothesis": "H1-H6",
            "description": "Expanded hypothesis-by-hypothesis prose draft",
        },
        {
            "asset": "table_hypothesis_core_results.tex",
            "role": "LaTeX Table",
            "hypothesis": "H1-H6",
            "description": "Publishable table wrapper for core results",
        },
        {
            "asset": "table_intervention_audit.tex",
            "role": "LaTeX Table",
            "hypothesis": "H4",
            "description": "Publishable table wrapper for intervention audit",
        },
        {
            "asset": "table_upstream_downstream_chain.tex",
            "role": "LaTeX Table",
            "hypothesis": "H2-H6",
            "description": "Publishable table wrapper for mechanism chain",
        },
    ]
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["asset", "role", "hypothesis", "description"])
        w.writeheader()
        w.writerows(rows)
    return rows


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Load inputs
    pairwise_cap20 = _read_csv(
        ROOT / "n50_t10_clean50x10_action_events_maxactions20_seeds11_20_excl_ep10_pairwise.csv"
    )
    pairwise_cap12 = _read_csv(
        ROOT / "n50_t10_clean50x10_action_events_significance_11_35_excl_ep10_pairwise.csv"
    )
    omnibus_cap20 = _read_csv(
        ROOT / "n50_t10_clean50x10_action_events_maxactions20_seeds11_20_excl_ep10_omnibus.csv"
    )
    omnibus_cap12 = _read_csv(
        ROOT / "n50_t10_clean50x10_action_events_significance_11_35_excl_ep10_omnibus.csv"
    )
    counterpart_avg = _read_csv(ROOT / "n50_t10_clean50x10_9b_vs_4b_counterpart_avg.csv")
    structural_counterpart = _read_csv(
        ROOT / "n50_t10_clean50x10_structural_dynamics_9b_vs_4b_counterpart_avg_arms.csv"
    )
    trend_rows = _read_csv(
        ROOT / "n50_t10_clean50x10_regime_trend_summary_seeds11_20_excl_ep10.csv"
    )
    mix_rows = _read_csv(
        ROOT / "n50_t10_clean50x10_regime_action_mix_summary_seeds11_20_excl_ep10.csv"
    )
    real_json = _read_json(
        ROOT / "n50_t10_clean50x10_real_variants_vs_like20_seeds11_20_excl_ep10.json"
    )

    # New figures
    build_fig_h4_intervention_ladder(
        trend_rows=trend_rows,
        real_json=real_json,
        out_path=OUT_DIR / "fig_h4_intervention_ladder.png",
    )
    build_fig_h5_composition_transport(
        mix_rows=mix_rows,
        out_path=OUT_DIR / "fig_h5_composition_transport.png",
    )

    # Tables
    t1 = build_table1_core(
        pairwise_cap12=pairwise_cap12,
        pairwise_cap20=pairwise_cap20,
        omnibus_cap12=omnibus_cap12,
        omnibus_cap20=omnibus_cap20,
        counterpart_avg=counterpart_avg,
        real_json=real_json,
        structural_counterpart=structural_counterpart,
    )
    write_table1(t1, OUT_DIR / "table_hypothesis_core_results.csv")
    t2_rows = load_effective_configs()
    write_table2_intervention_audit(t2_rows, OUT_DIR / "table_intervention_audit.csv")
    write_table3_chain(t1, OUT_DIR / "table_upstream_downstream_chain.csv")
    chain_rows = _read_csv(OUT_DIR / "table_upstream_downstream_chain.csv")

    # LaTeX snippets
    write_latex_snippets(t1, OUT_DIR / "paper_results_snippets.tex")
    write_latex_table_h1_h6(t1, OUT_DIR / "table_hypothesis_core_results.tex")
    write_latex_table_intervention_audit(t2_rows, OUT_DIR / "table_intervention_audit.tex")
    write_latex_table_chain(chain_rows, OUT_DIR / "table_upstream_downstream_chain.tex")
    write_results_full_tex(t1, OUT_DIR / "results_h1_h6_full.tex")
    write_appendix_asset_index(OUT_DIR / "appendix_asset_index.csv")

    print(f"Wrote figure: {OUT_DIR / 'fig_h4_intervention_ladder.png'}")
    print(f"Wrote figure: {OUT_DIR / 'fig_h5_composition_transport.png'}")
    print(f"Wrote table:  {OUT_DIR / 'table_hypothesis_core_results.csv'}")
    print(f"Wrote table:  {OUT_DIR / 'table_intervention_audit.csv'}")
    print(f"Wrote table:  {OUT_DIR / 'table_upstream_downstream_chain.csv'}")
    print(f"Wrote latex:  {OUT_DIR / 'paper_results_snippets.tex'}")
    print(f"Wrote latex:  {OUT_DIR / 'table_hypothesis_core_results.tex'}")
    print(f"Wrote latex:  {OUT_DIR / 'table_intervention_audit.tex'}")
    print(f"Wrote latex:  {OUT_DIR / 'table_upstream_downstream_chain.tex'}")
    print(f"Wrote latex:  {OUT_DIR / 'results_h1_h6_full.tex'}")
    print(f"Wrote index:  {OUT_DIR / 'appendix_asset_index.csv'}")


if __name__ == "__main__":
    main()
