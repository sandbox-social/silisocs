"""Scenario-local postprocessors for election_recsys_engagement studies."""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

import matplotlib
import yaml

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _resolve_probe_events_path(run_dir: Path) -> Path | None:
    """Resolve probe_events.jsonl, tolerating archived outputs under outputs_old."""
    primary = run_dir / "probe_events.jsonl"
    if primary.is_file():
        return primary

    run_str = str(run_dir)
    if "/outputs/" in run_str:
        fallback = Path(run_str.replace("/outputs/", "/outputs_old/", 1)) / "probe_events.jsonl"
        if fallback.is_file():
            return fallback

    return None


def _mean_ci95(values: list[float]) -> tuple[float, float, float] | None:
    if not values:
        return None
    mean = sum(values) / len(values)
    if len(values) < 2:
        return mean, mean, mean
    var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    std = math.sqrt(var)
    delta = 1.96 * (std / math.sqrt(len(values)))
    return mean, mean - delta, mean + delta


def _text_norm(value: str) -> str:
    return " ".join(str(value).strip().lower().replace("_", "-").split())


def _get_vote_choice_options(run_dir: Path) -> list[str]:
    cfg_path = run_dir / "effective_config.yaml"
    if not cfg_path.is_file():
        return []
    try:
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return []

    scenario = cfg.get("scenario", {}) if isinstance(cfg, dict) else {}
    if not isinstance(scenario, dict):
        return []
    probes = scenario.get("probes", {})
    if not isinstance(probes, dict):
        return []
    queries = probes.get("queries", {})
    if not isinstance(queries, dict):
        return []

    vote_entry = queries.get("vote_choice", {})
    if not isinstance(vote_entry, dict):
        return []
    query_data = vote_entry.get("query_data", {})
    if not isinstance(query_data, dict):
        return []
    choices = query_data.get("choices", [])
    if not isinstance(choices, list):
        return []
    return [str(choice).strip() for choice in choices if str(choice).strip()]


def _match_choice_label(response: Any, allowed_labels: list[str]) -> str | None:
    text = str(response or "").strip()
    if not text:
        return None

    for label in allowed_labels:
        if text == label:
            return label

    text_norm = _text_norm(text)
    for label in allowed_labels:
        if text_norm == _text_norm(label):
            return label

    hits = [label for label in allowed_labels if _text_norm(label) in text_norm]
    if len(hits) == 1:
        return hits[0]

    return None


def _is_no_choice_label(label: str) -> bool:
    norm = _text_norm(label)
    return norm in {
        "no-choice",
        "no choice",
        "none",
        "undecided",
        "neither",
        "abstain",
        "abstention",
        "no vote",
    }


def cross_seed_case_ci(
    records_by_type: dict[str, list[dict[str, Any]]],
    out_dir: Path,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Build case-level cross-seed CI plots for vote share and numeric probes."""
    _ = records_by_type
    _ = out_dir
    output_json = Path(str(context.get("output_json", "")))
    if not output_json.is_file():
        return {"generated_files": []}

    seed_dir = output_json.parent.parent
    scenario_dir = seed_dir.parent
    aggregate_dir = scenario_dir / "_aggregated_across_seeds"
    aggregate_dir.mkdir(parents=True, exist_ok=True)

    seed_jsons = sorted(scenario_dir.glob("seed_*/probe_metrics/probe_metrics_detailed.json"))
    if not seed_jsons:
        return {"generated_files": []}

    vote_share_by_seed_ep: dict[int, dict[int, dict[str, float]]] = {}
    numeric_by_label_seed_ep: dict[str, dict[int, dict[int, list[float]]]] = {}
    global_choice_counts: Counter[str] = Counter()
    preferred_choice_labels: list[str] = []

    for seed_json in seed_jsons:
        try:
            seed_num = int(seed_json.parent.parent.name.replace("seed_", ""))
        except ValueError:
            continue

        try:
            payload = _read_json(seed_json)
            run_dir = Path(str(payload.get("run_dir", "")))
            events_path = _resolve_probe_events_path(run_dir)
            if events_path is None:
                continue
            events = _read_jsonl(events_path)
        except Exception:
            continue

        vote_counts_ep: dict[int, Counter[str]] = {}
        numeric_ep: dict[str, dict[int, list[float]]] = {}
        allowed_choice_labels = _get_vote_choice_options(run_dir)
        if not preferred_choice_labels and len(allowed_choice_labels) >= 2:
            preferred_choice_labels = [
                label for label in allowed_choice_labels if not _is_no_choice_label(label)
            ]

        for row in events:
            if str(row.get("event_type", "")).lower() != "probe":
                continue
            label = str(row.get("label", "")).strip()
            episode = int(row.get("episode", 0))
            data = row.get("data", {})
            data = data if isinstance(data, dict) else {}
            response = data.get("query_return")

            if label == "vote_choice":
                if allowed_choice_labels:
                    choice = _match_choice_label(response, allowed_choice_labels)
                else:
                    choice_text = str(response or "").strip()
                    choice = choice_text or None
                if choice is None:
                    continue
                vote_counts_ep.setdefault(episode, Counter())
                vote_counts_ep[episode][choice] += 1
                global_choice_counts[choice] += 1
                continue

            if label.startswith("favorability_") or label == "polarization":
                try:
                    value = float(response)
                except (TypeError, ValueError):
                    continue
                numeric_ep.setdefault(label, {})
                numeric_ep[label].setdefault(episode, [])
                numeric_ep[label][episode].append(value)

        vote_share_by_seed_ep[seed_num] = {}

        candidate_labels = [
            label for label in preferred_choice_labels if not _is_no_choice_label(label)
        ]
        if len(candidate_labels) < 2:
            candidate_labels = [
                label
                for label, _ in global_choice_counts.most_common()
                if not _is_no_choice_label(label)
            ]
        selected_candidates: list[str] = []
        if len(candidate_labels) >= 2:
            selected_candidates = candidate_labels[:2]

        if len(selected_candidates) >= 2:
            for ep, counts in vote_counts_ep.items():
                total = counts.get(selected_candidates[0], 0) + counts.get(
                    selected_candidates[1], 0
                )
                if total <= 0:
                    continue
                candidate_a_share = counts.get(selected_candidates[0], 0) / total
                candidate_b_share = counts.get(selected_candidates[1], 0) / total
                vote_share_by_seed_ep[seed_num][ep] = {
                    selected_candidates[0]: candidate_a_share,
                    selected_candidates[1]: candidate_b_share,
                }

        for label, ep_map in numeric_ep.items():
            numeric_by_label_seed_ep.setdefault(label, {})
            numeric_by_label_seed_ep[label][seed_num] = {}
            for ep, values in ep_map.items():
                if not values:
                    continue
                numeric_by_label_seed_ep[label][seed_num][ep] = values

    generated_files: list[str] = []

    ep_to_values_candidate_a: dict[int, list[float]] = {}
    ep_to_values_candidate_b: dict[int, list[float]] = {}

    for legacy_plot in aggregate_dir.glob("vote_share_*_ci95.png"):
        if legacy_plot.name != "vote_share_candidates_ci95.png":
            legacy_plot.unlink()

    selected_candidates: list[str] = []

    for ep_map in vote_share_by_seed_ep.values():
        if not selected_candidates:
            selected_candidates = list(next(iter(ep_map.values())).keys()) if ep_map else []
        if len(selected_candidates) < 2:
            continue
        for ep, shares in ep_map.items():
            ep_to_values_candidate_a.setdefault(ep, []).append(
                float(shares[selected_candidates[0]])
            )
            ep_to_values_candidate_b.setdefault(ep, []).append(
                float(shares[selected_candidates[1]])
            )

    if ep_to_values_candidate_a and ep_to_values_candidate_b and len(selected_candidates) >= 2:
        episodes = sorted(
            set(ep_to_values_candidate_a.keys()) & set(ep_to_values_candidate_b.keys())
        )

        candidate_a_means: list[float] = []
        candidate_a_lowers: list[float] = []
        candidate_a_uppers: list[float] = []

        candidate_b_means: list[float] = []
        candidate_b_lowers: list[float] = []
        candidate_b_uppers: list[float] = []

        x: list[int] = []
        for ep in episodes:
            candidate_a_stats = _mean_ci95(ep_to_values_candidate_a.get(ep, []))
            candidate_b_stats = _mean_ci95(ep_to_values_candidate_b.get(ep, []))
            if candidate_a_stats is None or candidate_b_stats is None:
                continue
            cm_a, c_lo_a, c_hi_a = candidate_a_stats
            cm_b, c_lo_b, c_hi_b = candidate_b_stats
            x.append(ep)
            candidate_a_means.append(cm_a)
            candidate_a_lowers.append(c_lo_a)
            candidate_a_uppers.append(c_hi_a)
            candidate_b_means.append(cm_b)
            candidate_b_lowers.append(c_lo_b)
            candidate_b_uppers.append(c_hi_b)

        if x:
            plt.figure(figsize=(9, 4))
            plt.plot(x, candidate_a_means, label=f"{selected_candidates[0]} mean", color="#1f77b4")
            plt.fill_between(
                x,
                candidate_a_lowers,
                candidate_a_uppers,
                alpha=0.2,
                color="#1f77b4",
                label=f"{selected_candidates[0]} 95% CI",
            )

            plt.plot(x, candidate_b_means, label=f"{selected_candidates[1]} mean", color="#ff7f0e")
            plt.fill_between(
                x,
                candidate_b_lowers,
                candidate_b_uppers,
                alpha=0.2,
                color="#ff7f0e",
                label=f"{selected_candidates[1]} 95% CI",
            )

            plt.ylim(0.0, 1.0)
            plt.xlabel("Episode")
            plt.ylabel("Vote share")
            plt.title("Vote Share Across Seeds (Mean +/- 95% CI)")
            plt.legend(loc="best")
            plt.tight_layout()
            out_path = aggregate_dir / "vote_share_candidates_ci95.png"
            plt.savefig(out_path, dpi=150)
            plt.close()
            generated_files.append(str(out_path))

    for label, seed_map in sorted(numeric_by_label_seed_ep.items()):
        ep_values: dict[int, list[float]] = {}
        for ep_map in seed_map.values():
            for ep, vals in ep_map.items():
                if not vals:
                    continue
                ep_values.setdefault(ep, []).append(sum(vals) / len(vals))

        if not ep_values:
            continue

        episodes = sorted(ep_values.keys())
        means: list[float] = []
        lowers: list[float] = []
        uppers: list[float] = []
        for ep in episodes:
            stats = _mean_ci95(ep_values[ep])
            if stats is None:
                continue
            m, lo, hi = stats
            means.append(m)
            lowers.append(lo)
            uppers.append(hi)

        if not means:
            continue

        x = episodes[: len(means)]
        plt.figure(figsize=(9, 4))
        plt.plot(x, means, label=f"Mean {label}", color="#d62728")
        plt.fill_between(x, lowers, uppers, alpha=0.2, color="#d62728", label="95% CI")
        plt.xlabel("Episode")
        plt.ylabel("Score")
        plt.title(f"{label} Across Seeds (Mean +/- 95% CI)")
        plt.legend(loc="best")
        plt.tight_layout()
        out_path = aggregate_dir / f"{label}_ci95.png"
        plt.savefig(out_path, dpi=150)
        plt.close()
        generated_files.append(str(out_path))

    return {
        "generated_files": generated_files,
        "aggregate_dir": str(aggregate_dir),
        "num_seed_files": len(seed_jsons),
    }
