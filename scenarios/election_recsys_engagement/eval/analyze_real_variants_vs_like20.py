"""Detailed analysis for real variants vs like_20 baseline.

Includes settings: like20, real, real1, real2
Excludes: real_thinking

Metrics are computed per-active-agent-per-episode:
metric_count / sum_episode(active_agents_in_episode)
with final configured episode excluded.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, stdev
from typing import Any

import yaml
from scipy.stats import friedmanchisquare, wilcoxon

SOCIAL_LABELS = ("post", "reply", "like", "repost")
METRICS = (
    "total_actions_per_active_agent_episode",
    "posts_per_active_agent_episode",
    "replies_per_active_agent_episode",
    "likes_per_active_agent_episode",
    "reposts_per_active_agent_episode",
    "interactions_per_active_agent_episode",
)
ARMS = ("chronological", "recsys_twitter", "recsys_twhin")
SETTINGS = ("like20", "real", "real1", "real2")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


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


def _detect_setting(tag: str) -> str | None:
    if "real_thinking" in tag:
        return None
    if "like_20" in tag:
        return "like20"
    if "real1" in tag:
        return "real1"
    if "real2" in tag:
        return "real2"
    if "real" in tag:
        return "real"
    return None


def _detect_arm(tag: str) -> str | None:
    if tag.startswith("chronological"):
        return "chronological"
    if tag.startswith("recsys_twitter"):
        return "recsys_twitter"
    if tag.startswith("recsys_twhin"):
        return "recsys_twhin"
    return None


def main() -> None:
    outputs_dir = Path("scenarios/election_recsys_engagement/outputs")
    out_json = outputs_dir / "n50_t10_clean50x10_real_variants_vs_like20_seeds11_20_excl_ep10.json"
    out_md = outputs_dir / "n50_t10_clean50x10_real_variants_vs_like20_seeds11_20_excl_ep10.md"

    pattern = re.compile(r"ElectionRecsys_N50_T10_clean50x10_seed(\d+)_([^/]+)")

    per_run_rows: list[dict[str, Any]] = []

    for action_events_path in outputs_dir.rglob("action_events.jsonl"):
        run_dir = action_events_path.parent
        m = pattern.search(str(run_dir))
        if not m:
            continue

        seed = int(m.group(1))
        if seed < 11 or seed > 20:
            continue

        tag = m.group(2)
        setting = _detect_setting(tag)
        arm = _detect_arm(tag)
        if setting not in SETTINGS or arm not in ARMS:
            continue

        events = _read_jsonl(action_events_path)
        effective_config = _load_yaml(run_dir / "effective_config.yaml")

        init_users = {
            str(e.get("source_user", ""))
            for e in events
            if e.get("label") == "init_create_user"
            and isinstance(e.get("source_user"), str)
            and e.get("source_user") not in {"", "system"}
        }
        fixed_users = _extract_fixed_usernames_from_config(effective_config)
        eval_user_set = set(u for u in init_users if u not in fixed_users)

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

            source_user = str(event.get("source_user", ""))
            if source_user not in eval_user_set:
                continue

            label = str(event.get("label", ""))
            if label in SOCIAL_LABELS:
                label_counts[label] += 1
                active_users_by_step[episode].add(source_user)

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

        per_run_rows.append(
            {
                "seed": seed,
                "setting": setting,
                "arm": arm,
                "run_dir": str(run_dir),
                "active_agent_episodes": active_agent_episodes,
                "posts_per_active_agent_episode": post / denom,
                "replies_per_active_agent_episode": reply / denom,
                "likes_per_active_agent_episode": like / denom,
                "reposts_per_active_agent_episode": repost / denom,
                "interactions_per_active_agent_episode": interactions / denom,
                "total_actions_per_active_agent_episode": total_actions / denom,
            }
        )

    # Coverage
    coverage: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for r in per_run_rows:
        coverage[r["setting"]][r["arm"]].append(int(r["seed"]))
    for setting in coverage:
        for arm in coverage[setting]:
            coverage[setting][arm] = sorted(set(coverage[setting][arm]))

    # Summary means/stdev by setting/arm/metric
    summary_by_setting_arm_metric: list[dict[str, Any]] = []
    for setting in SETTINGS:
        for arm in ARMS:
            subset = [r for r in per_run_rows if r["setting"] == setting and r["arm"] == arm]
            if not subset:
                continue
            for metric in METRICS:
                vals = [float(r[metric]) for r in subset]
                summary_by_setting_arm_metric.append(
                    {
                        "setting": setting,
                        "arm": arm,
                        "metric": metric,
                        "n": len(vals),
                        "mean": mean(vals),
                        "std": stdev(vals) if len(vals) > 1 else 0.0,
                    }
                )

    # Average of the three arms (setting-level aggregate of arm means)
    three_arm_averages: list[dict[str, Any]] = []
    for setting in SETTINGS:
        for metric in METRICS:
            arm_means = [
                row["mean"]
                for row in summary_by_setting_arm_metric
                if row["setting"] == setting and row["metric"] == metric and row["arm"] in ARMS
            ]
            if len(arm_means) != 3:
                continue
            three_arm_averages.append(
                {
                    "setting": setting,
                    "metric": metric,
                    "three_arm_mean_of_means": mean(arm_means),
                    "three_arm_std_of_means": stdev(arm_means),
                }
            )

    # Within each setting: omnibus across three arms (Friedman paired by seed)
    within_setting_omnibus: list[dict[str, Any]] = []
    for setting in SETTINGS:
        for metric in METRICS:
            arm_seed_map: dict[str, dict[int, float]] = {arm: {} for arm in ARMS}
            for r in per_run_rows:
                if r["setting"] == setting and r["arm"] in ARMS:
                    arm_seed_map[r["arm"]][int(r["seed"])] = float(r[metric])

            common_seeds = sorted(
                set(arm_seed_map[ARMS[0]]) & set(arm_seed_map[ARMS[1]]) & set(arm_seed_map[ARMS[2]])
            )
            if len(common_seeds) < 2:
                continue

            vals = [[arm_seed_map[arm][s] for s in common_seeds] for arm in ARMS]
            stat, p = friedmanchisquare(*vals)
            within_setting_omnibus.append(
                {
                    "setting": setting,
                    "metric": metric,
                    "n_seeds": len(common_seeds),
                    "friedman_chi2": stat,
                    "friedman_p": p,
                    "sig": _sig(p),
                }
            )

    # Pairwise within each setting among arms
    pairwise_within_setting: list[dict[str, Any]] = []
    arm_pairs = (
        ("recsys_twitter", "chronological"),
        ("recsys_twhin", "chronological"),
        ("recsys_twhin", "recsys_twitter"),
    )
    for setting in SETTINGS:
        for metric in METRICS:
            rows_this_metric_setting: list[dict[str, Any]] = []
            for arm_a, arm_b in arm_pairs:
                map_a = {
                    int(r["seed"]): float(r[metric])
                    for r in per_run_rows
                    if r["setting"] == setting and r["arm"] == arm_a
                }
                map_b = {
                    int(r["seed"]): float(r[metric])
                    for r in per_run_rows
                    if r["setting"] == setting and r["arm"] == arm_b
                }
                seeds = sorted(set(map_a) & set(map_b))
                if len(seeds) < 2:
                    continue
                vals_a = [map_a[s] for s in seeds]
                vals_b = [map_b[s] for s in seeds]
                stat, p = wilcoxon(vals_a, vals_b, zero_method="wilcox", alternative="two-sided")
                rows_this_metric_setting.append(
                    {
                        "setting": setting,
                        "metric": metric,
                        "arm_a": arm_a,
                        "arm_b": arm_b,
                        "n_seeds": len(seeds),
                        "mean_a": mean(vals_a),
                        "mean_b": mean(vals_b),
                        "delta_a_minus_b": mean(vals_a) - mean(vals_b),
                        "wilcoxon_stat": stat,
                        "wilcoxon_p": p,
                    }
                )

            _holm_adjust(rows_this_metric_setting, p_key="wilcoxon_p", out_key="wilcoxon_p_holm")
            for row in rows_this_metric_setting:
                row["sig_raw"] = _sig(float(row["wilcoxon_p"]))
                row["sig_holm"] = _sig(float(row["wilcoxon_p_holm"]))
            pairwise_within_setting.extend(rows_this_metric_setting)

    # Per-arm vs like20 baseline across settings (real, real1, real2)
    per_arm_vs_like20: list[dict[str, Any]] = []
    compare_settings = ("real", "real1", "real2")
    for arm in ARMS:
        for metric in METRICS:
            rows_this_arm_metric: list[dict[str, Any]] = []
            baseline_map = {
                int(r["seed"]): float(r[metric])
                for r in per_run_rows
                if r["setting"] == "like20" and r["arm"] == arm
            }

            for setting in compare_settings:
                target_map = {
                    int(r["seed"]): float(r[metric])
                    for r in per_run_rows
                    if r["setting"] == setting and r["arm"] == arm
                }
                seeds = sorted(set(baseline_map) & set(target_map))
                if len(seeds) < 2:
                    continue
                vals_target = [target_map[s] for s in seeds]
                vals_base = [baseline_map[s] for s in seeds]
                stat, p = wilcoxon(
                    vals_target, vals_base, zero_method="wilcox", alternative="two-sided"
                )
                rows_this_arm_metric.append(
                    {
                        "arm": arm,
                        "metric": metric,
                        "setting": setting,
                        "baseline_setting": "like20",
                        "n_seeds": len(seeds),
                        "mean_setting": mean(vals_target),
                        "mean_like20": mean(vals_base),
                        "delta_setting_minus_like20": mean(vals_target) - mean(vals_base),
                        "wilcoxon_stat": stat,
                        "wilcoxon_p": p,
                    }
                )

            _holm_adjust(rows_this_arm_metric, p_key="wilcoxon_p", out_key="wilcoxon_p_holm")
            for row in rows_this_arm_metric:
                row["sig_raw"] = _sig(float(row["wilcoxon_p"]))
                row["sig_holm"] = _sig(float(row["wilcoxon_p_holm"]))
            per_arm_vs_like20.extend(rows_this_arm_metric)

    # Per-arm across all four settings (like20 + real + real1 + real2): Friedman omnibus
    per_arm_across_settings_omnibus: list[dict[str, Any]] = []
    for arm in ARMS:
        for metric in METRICS:
            setting_seed_map: dict[str, dict[int, float]] = {s: {} for s in SETTINGS}
            for r in per_run_rows:
                if r["arm"] == arm and r["setting"] in SETTINGS:
                    setting_seed_map[r["setting"]][int(r["seed"])] = float(r[metric])

            common_seeds = sorted(set.intersection(*[set(setting_seed_map[s]) for s in SETTINGS]))
            if len(common_seeds) < 2:
                continue

            vals = [[setting_seed_map[s][seed] for seed in common_seeds] for s in SETTINGS]
            stat, p = friedmanchisquare(*vals)
            per_arm_across_settings_omnibus.append(
                {
                    "arm": arm,
                    "metric": metric,
                    "n_seeds": len(common_seeds),
                    "friedman_chi2": stat,
                    "friedman_p": p,
                    "sig": _sig(p),
                }
            )

    payload = {
        "settings_included": list(SETTINGS),
        "settings_excluded": ["real_thinking"],
        "seed_range": [11, 20],
        "exclude_final_episode": True,
        "coverage": coverage,
        "per_run_rows": per_run_rows,
        "summary_by_setting_arm_metric": summary_by_setting_arm_metric,
        "three_arm_averages": three_arm_averages,
        "within_setting_omnibus": within_setting_omnibus,
        "pairwise_within_setting": pairwise_within_setting,
        "per_arm_vs_like20": per_arm_vs_like20,
        "per_arm_across_settings_omnibus": per_arm_across_settings_omnibus,
    }
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # Human-readable markdown report
    lines: list[str] = []
    lines.append("# Real Variants vs like_20 Baseline (Detailed Analysis)")
    lines.append("")
    lines.append("- Included settings: `like20`, `real`, `real1`, `real2`")
    lines.append("- Excluded setting: `real_thinking`")
    lines.append("- Seeds: 11-20, all three arms")
    lines.append("- Episode handling: excluded final configured episode")
    lines.append("")

    lines.append("## Coverage")
    for setting in SETTINGS:
        lines.append(f"- {setting}:")
        for arm in ARMS:
            seeds = coverage.get(setting, {}).get(arm, [])
            lines.append(f"  - {arm}: n={len(seeds)} seeds={seeds}")
    lines.append("")

    lines.append("## Three-Arm Averages (Mean of Arm Means)")
    for metric in (
        "total_actions_per_active_agent_episode",
        "posts_per_active_agent_episode",
        "interactions_per_active_agent_episode",
    ):
        lines.append(f"### {metric}")
        for setting in SETTINGS:
            row = next(
                (
                    r
                    for r in three_arm_averages
                    if r["setting"] == setting and r["metric"] == metric
                ),
                None,
            )
            if row:
                lines.append(
                    f"- {setting}: {row['three_arm_mean_of_means']:.4f} "
                    f"(std across arms={row['three_arm_std_of_means']:.4f})"
                )
        lines.append("")

    lines.append("## Per-Setting Omnibus Across Arms (Friedman)")
    for setting in SETTINGS:
        lines.append(f"### {setting}")
        rows = [r for r in within_setting_omnibus if r["setting"] == setting]
        for r in rows:
            lines.append(f"- {r['metric']}: p={r['friedman_p']:.8f} ({r['sig']})")
        lines.append("")

    lines.append("## Per-Arm vs like20 (Wilcoxon, Holm over real/real1/real2)")
    for arm in ARMS:
        lines.append(f"### {arm}")
        for metric in (
            "total_actions_per_active_agent_episode",
            "posts_per_active_agent_episode",
            "interactions_per_active_agent_episode",
        ):
            lines.append(f"- {metric}:")
            rows = [r for r in per_arm_vs_like20 if r["arm"] == arm and r["metric"] == metric]
            for r in rows:
                lines.append(
                    f"  - {r['setting']} vs like20: "
                    f"delta={r['delta_setting_minus_like20']:+.4f}, "
                    f"p={r['wilcoxon_p']:.8f} ({r['sig_raw']}), "
                    f"holm={r['wilcoxon_p_holm']:.8f} ({r['sig_holm']})"
                )
        lines.append("")

    lines.append("## Per-Arm Omnibus Across Settings (like20/real/real1/real2)")
    for arm in ARMS:
        lines.append(f"### {arm}")
        rows = [r for r in per_arm_across_settings_omnibus if r["arm"] == arm]
        for r in rows:
            lines.append(f"- {r['metric']}: p={r['friedman_p']:.8f} ({r['sig']})")
        lines.append("")

    out_md.write_text("\n".join(lines), encoding="utf-8")

    print(f"Wrote JSON: {out_json}")
    print(f"Wrote MD:   {out_md}")
    print(f"Per-run rows: {len(per_run_rows)}")


if __name__ == "__main__":
    main()
