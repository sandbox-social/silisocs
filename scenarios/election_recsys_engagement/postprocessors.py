"""Scenario-local postprocessors for election_recsys_engagement studies."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _resolve_probe_events_path(run_dir: Path) -> Path | None:
    primary = run_dir / "probe_events.jsonl"
    if primary.is_file():
        return primary

    run_str = str(run_dir)
    if "/outputs/" in run_str:
        fallback = Path(run_str.replace("/outputs/", "/outputs_old/", 1)) / "probe_events.jsonl"
        if fallback.is_file():
            return fallback

    return None


def cross_seed_case_ci(
    records_by_type: dict[str, list[dict[str, Any]]],
    out_dir: Path,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Build lightweight cross-seed summary artifacts for probe metrics.

    This keeps study postprocessor wiring stable and produces deterministic artifacts
    from available seed outputs without depending on legacy scenario directories.
    """
    _ = records_by_type
    _ = out_dir

    output_json_raw = str(context.get("output_json", "")).strip()
    if not output_json_raw:
        return {"generated_files": []}

    output_json = Path(output_json_raw)
    seed_dir = output_json.parent.parent
    scenario_dir = seed_dir.parent
    aggregate_dir = scenario_dir / "_aggregated_across_seeds"
    aggregate_dir.mkdir(parents=True, exist_ok=True)

    seed_jsons = sorted(scenario_dir.glob("seed_*/probe_metrics/probe_metrics_detailed.json"))
    if not seed_jsons:
        return {"generated_files": []}

    vote_counts_by_seed: dict[int, Counter[str]] = {}
    numeric_values_by_label: dict[str, list[float]] = defaultdict(list)

    for seed_json in seed_jsons:
        try:
            seed_num = int(seed_json.parent.parent.name.replace("seed_", ""))
            payload = _read_json(seed_json)
            run_dir = Path(str(payload.get("run_dir", "")))
            events_path = _resolve_probe_events_path(run_dir)
            if events_path is None:
                continue
            events = _read_jsonl(events_path)
        except Exception:
            continue

        vote_counter: Counter[str] = Counter()
        for row in events:
            if str(row.get("event_type", "")).lower() != "probe":
                continue
            label = str(row.get("label", "")).strip()
            data = row.get("data", {})
            if not isinstance(data, dict):
                continue
            response = data.get("query_return")

            if label == "vote_choice":
                choice = str(response or "").strip()
                if choice:
                    vote_counter[choice] += 1
                continue

            if label.startswith("favorability_") or label == "polarization":
                try:
                    numeric_values_by_label[label].append(float(response))
                except (TypeError, ValueError):
                    pass

        vote_counts_by_seed[seed_num] = vote_counter

    summary = {
        "seed_count": len(vote_counts_by_seed),
        "vote_choice_counts_by_seed": {str(k): dict(v) for k, v in vote_counts_by_seed.items()},
        "numeric_probe_means": {
            label: (sum(values) / len(values) if values else None)
            for label, values in numeric_values_by_label.items()
        },
    }

    summary_path = aggregate_dir / "cross_seed_case_ci_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    return {"generated_files": [str(summary_path)]}
