#!/usr/bin/env python3
"""Default run evaluators for study orchestration.

This module provides detailed, reproducible summaries over:
- action events (overall, per-episode, per-agent, per-agent-per-episode, transitions)
- probe events (overall, per-probe, per-type, with type-specific metrics)

CLI usage:
  uv run python -m silisocs.evaluations.default_evaluators \
    --run-dir <run_dir> --output <out.json> --mode action_metrics
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from silisocs.design.matplotlib import apply_matplotlib_theme
from silisocs.evaluations.action_events import resolve_action_event_files
from silisocs.evaluations.probes.types import probe_analysis_kind
from silisocs.evaluations.run_artifact import iter_jsonl

# matplotlib is an optional (`analysis` extra) dependency used only by the plot
# writers below. Import it lazily so this module — and its JSONL/stat helpers —
# can be imported without the plotting stack installed.
plt: Any = None


def _ensure_plt() -> Any:
    """Lazily import matplotlib (Agg backend) and cache pyplot on the module."""
    global plt
    if plt is None:
        import matplotlib

        matplotlib.use("Agg")
        apply_matplotlib_theme(matplotlib)
        import matplotlib.pyplot as _plt

        plt = _plt
    return plt


VALID_MODES = {
    "action_metrics",
    "probe_metrics",
    "probe_binary",
    "probe_numeric",
    "probe_choice",
    "probe_freetext",
}

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'-]{2,}")
_CHOICE_WORD_THRESHOLD = 4


def _slug(text: str) -> str:
    """Convert text to a lowercase, underscore-separated slug identifier."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower()).strip("_")
    return slug or "unknown"


def _safe_int(value: Any, default: int = 0) -> int:
    """Coerce a value to int, returning the default if conversion fails."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any) -> float | None:
    """Parse the first numeric token from a value as a float, or None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    m = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if m is None:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _summary_stats(values: list[float]) -> dict[str, Any]:
    """Return count/min/max/mean/median/stdev summary stats for the values."""
    if not values:
        return {
            "count": 0,
            "min": None,
            "max": None,
            "mean": None,
            "median": None,
            "stdev": None,
        }
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "stdev": statistics.stdev(values) if len(values) > 1 else 0.0,
    }


def _normalize_binary(value: Any) -> str | None:
    """Normalize a value to "Yes"/"No", or None if it is not binary."""
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text in {"yes", "y", "true", "1"}:
        return "Yes"
    if text in {"no", "n", "false", "0"}:
        return "No"
    if "yes" in text:
        return "Yes"
    if "no" in text:
        return "No"
    return None


def _load_probe_type_map(run_dir: Path) -> dict[str, str]:  # noqa: C901
    """Load a mapping of probe label to probe type from the run's config."""
    cfg_path = run_dir / "effective_config.yaml"
    if not cfg_path.is_file():
        return {}

    with cfg_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    evaluations = (
        cfg.get("eval", cfg.get("evals", cfg.get("evaluations"))) if isinstance(cfg, dict) else {}
    )
    if not isinstance(evaluations, dict):
        return {}

    probes = evaluations.get("probes", {})
    if not isinstance(probes, dict):
        return {}

    raw_probes = probes.get("probes", {})
    if isinstance(raw_probes, dict):
        query_items = list(raw_probes.values())
    elif isinstance(raw_probes, list):
        query_items = [item for item in raw_probes if isinstance(item, dict)]
    else:
        query_items = []

    out: dict[str, str] = {}
    for item in query_items:
        if not isinstance(item, dict):
            continue
        probe_type = str(item.get("probe_type") or "Unknown")
        probe_data = item.get("probe_data", {})
        probe_data = probe_data if isinstance(probe_data, dict) else {}

        names: list[str] = []
        probe_name = item.get("probe_name")
        query_name = probe_data.get("name")
        if probe_name:
            names.append(str(probe_name))
        if query_name:
            names.append(str(query_name))

        for name in names:
            out[name] = probe_type

    return out


def _load_probe_hold_last_response(run_dir: Path) -> bool:
    """Return whether probe deployment has hold_last_response enabled in config."""
    cfg_path = run_dir / "effective_config.yaml"
    if not cfg_path.is_file():
        return False

    with cfg_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    evaluations = (
        cfg.get("eval", cfg.get("evals", cfg.get("evaluations"))) if isinstance(cfg, dict) else {}
    )
    if not isinstance(evaluations, dict):
        return False
    probes = evaluations.get("probes", {})
    if not isinstance(probes, dict):
        return False
    deployment = probes.get("deployment", {})
    if not isinstance(deployment, dict):
        return False

    return bool(deployment.get("hold_last_response", False))


def _has_probe_response(row: dict[str, Any]) -> bool:
    """Return whether a probe row carries a non-empty response."""
    data = row.get("data", {})
    data = data if isinstance(data, dict) else {}
    response = data.get("probe_return")
    return response is not None and str(response).strip() != ""


def _apply_carry_forward_probe_rows(probe_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Impute missing-episode probe rows by carrying forward the last response."""
    if not probe_rows:
        return probe_rows

    episodes = sorted({_safe_int(row.get("episode"), 0) for row in probe_rows})
    if not episodes:
        return probe_rows

    by_key_episode: dict[tuple[str, str], dict[int, dict[str, Any]]] = defaultdict(dict)
    for row in probe_rows:
        key = (str(row.get("source_user", "")).strip(), str(row.get("label", "")).strip())
        episode = _safe_int(row.get("episode"), 0)
        by_key_episode[key][episode] = row

    out = list(probe_rows)
    synthetic_index = -1
    for key, episode_map in by_key_episode.items():
        source_user, label = key
        last_response: Any = None
        for episode in episodes:
            existing = episode_map.get(episode)
            if existing is not None:
                if _has_probe_response(existing):
                    data = existing.get("data", {})
                    data = data if isinstance(data, dict) else {}
                    last_response = data.get("probe_return")
                continue

            if last_response is None:
                continue

            out.append(
                {
                    "source_user": source_user,
                    "label": label,
                    "data": {
                        "probe_return": last_response,
                        "probe_mode": "carry_forward_imputed",
                    },
                    "episode": episode,
                    "event_type": "probe",
                    "event_index": synthetic_index,
                }
            )
            synthetic_index -= 1

    out.sort(
        key=lambda row: (_safe_int(row.get("episode"), 0), _safe_int(row.get("event_index"), 0))
    )
    return out


def _extract_probe_records(
    events: list[dict[str, Any]],
    run_dir: Path,
    probe_type_filter: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Extract probe responses grouped into choice, numeric, and freetext records."""
    probe_rows = [row for row in events if str(row.get("event_type", "")).lower() == "probe"]
    hold_last_response = _load_probe_hold_last_response(run_dir)
    if hold_last_response:
        probe_rows = _apply_carry_forward_probe_rows(probe_rows)
    type_map = _load_probe_type_map(run_dir)

    choice_records: list[dict[str, Any]] = []
    numeric_records: list[dict[str, Any]] = []
    freetext_records: list[dict[str, Any]] = []

    for row in probe_rows:
        label = str(row.get("label", "")).strip() or "unknown"
        episode = _safe_int(row.get("episode"), 0)

        data = row.get("data", {})
        data = data if isinstance(data, dict) else {}
        response = data.get("probe_return")
        if response is None or str(response).strip() == "":
            continue

        mapped_type = type_map.get(label)
        probe_type = mapped_type or _infer_probe_type(response)
        if probe_type_filter is not None and probe_type != probe_type_filter:
            continue

        # Scoring is registry-driven (ProbeBase.analysis_kind): any probe class
        # declaring a kind — including custom ones imported via `plugins:` —
        # is bucketed here without touching this module.
        kind = probe_analysis_kind(probe_type)
        if kind in {"binary", "choice"}:
            normalized = _normalize_binary(response)
            choice_records.append(
                {
                    "label": label,
                    "episode": episode,
                    "choice": normalized if normalized is not None else str(response).strip(),
                }
            )
        elif kind == "numeric":
            numeric = _safe_float(response)
            if numeric is not None:
                numeric_records.append(
                    {
                        "label": label,
                        "episode": episode,
                        "value": numeric,
                    }
                )
        elif kind == "freetext":
            text = str(response)
            freetext_records.append(
                {
                    "label": label,
                    "episode": episode,
                    "text": text,
                    "word_count": len(text.split()),
                }
            )

    return {
        "choice": choice_records,
        "numeric": numeric_records,
        "freetext": freetext_records,
    }


def _write_choice_plots(records: list[dict[str, Any]], out_dir: Path) -> list[str]:
    """Write per-label choice-share-over-episodes plots and return their paths."""
    if not records:
        return []

    plt = _ensure_plt()
    grouped: dict[str, dict[int, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    for rec in records:
        grouped[str(rec["label"])][int(rec["episode"])][str(rec["choice"])] += 1

    written: list[str] = []
    for label in sorted(grouped.keys()):
        per_episode = grouped[label]
        episodes = sorted(per_episode.keys())
        choices = sorted({choice for counts in per_episode.values() for choice in counts.keys()})
        if not episodes or not choices:
            continue

        plt.figure(figsize=(9, 4))
        for choice in choices:
            shares: list[float] = []
            for ep in episodes:
                total = sum(per_episode[ep].values())
                shares.append((per_episode[ep].get(choice, 0) / total) if total else 0.0)
            plt.plot(episodes, shares, marker="o", label=choice)

        plt.title(f"Choice Share Over Episodes - {label}")
        plt.xlabel("Episode")
        plt.ylabel("Share")
        plt.ylim(0.0, 1.0)
        plt.legend(loc="best")
        plt.tight_layout()

        out_path = out_dir / f"choice_{_slug(label)}.png"
        plt.savefig(out_path, dpi=150)
        plt.close()
        written.append(str(out_path))

    return written


def _write_numeric_plots(records: list[dict[str, Any]], out_dir: Path) -> list[str]:
    """Write per-label numeric-mean-over-episodes plots and return their paths."""
    if not records:
        return []

    plt = _ensure_plt()
    grouped: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    for rec in records:
        grouped[str(rec["label"])][int(rec["episode"])].append(float(rec["value"]))

    written: list[str] = []
    for label in sorted(grouped.keys()):
        per_episode = grouped[label]
        episodes = sorted(per_episode.keys())
        if not episodes:
            continue
        means = [statistics.fmean(per_episode[ep]) for ep in episodes]

        plt.figure(figsize=(9, 4))
        plt.plot(episodes, means, marker="o")
        plt.title(f"Numeric Mean Over Episodes - {label}")
        plt.xlabel("Episode")
        plt.ylabel("Mean response")
        plt.tight_layout()

        out_path = out_dir / f"numeric_{_slug(label)}.png"
        plt.savefig(out_path, dpi=150)
        plt.close()
        written.append(str(out_path))

    return written


def _write_freetext_plots(records: list[dict[str, Any]], out_dir: Path) -> list[str]:
    """Write free-text word-count and top-token plots and return their paths."""
    if not records:
        return []

    plt = _ensure_plt()
    by_label_episode: dict[str, dict[int, list[int]]] = defaultdict(lambda: defaultdict(list))
    token_counts: Counter[str] = Counter()

    for rec in records:
        label = str(rec["label"])
        episode = int(rec["episode"])
        by_label_episode[label][episode].append(int(rec["word_count"]))
        for token in _TOKEN_RE.findall(str(rec["text"]).lower()):
            token_counts[token] += 1

    written: list[str] = []

    plt.figure(figsize=(10, 4))
    for label in sorted(by_label_episode.keys()):
        episodes = sorted(by_label_episode[label].keys())
        means = [statistics.fmean(by_label_episode[label][ep]) for ep in episodes]
        plt.plot(episodes, means, marker="o", label=label)
    plt.title("Free-Text Mean Word Count Over Episodes")
    plt.xlabel("Episode")
    plt.ylabel("Mean word count")
    if by_label_episode:
        plt.legend(loc="best")
    plt.tight_layout()
    out_path_words = out_dir / "freetext_wordcount_over_time.png"
    plt.savefig(out_path_words, dpi=150)
    plt.close()
    written.append(str(out_path_words))

    top_tokens = token_counts.most_common(20)
    if top_tokens:
        labels = [item[0] for item in top_tokens]
        values = [item[1] for item in top_tokens]

        plt.figure(figsize=(10, 5))
        plt.bar(labels, values)
        plt.title("Free-Text Top Tokens")
        plt.ylabel("Count")
        plt.xticks(rotation=45, ha="right")
        plt.tight_layout()
        out_path_tokens = out_dir / "freetext_top_tokens.png"
        plt.savefig(out_path_tokens, dpi=150)
        plt.close()
        written.append(str(out_path_tokens))

    return written


def _probe_filter_for_mode(mode: str) -> str | None:
    """Map a probe_* mode to its probe type filter, or None for no filter."""
    if mode == "probe_binary":
        return "BinaryProbe"
    if mode == "probe_numeric":
        return "NumericRatingProbe"
    if mode == "probe_choice":
        return "ChoiceProbe"
    if mode == "probe_freetext":
        return "FreeTextProbe"
    return None


def _build_probe_plots(
    mode: str,
    events: list[dict[str, Any]],
    run_dir: Path,
    output_json: Path,
    postprocessors: list[str] | None = None,
) -> dict[str, Any]:
    """Generate probe plots and run postprocessors, returning a result summary."""
    if not mode.startswith("probe_"):
        return {
            "generated_files": [],
            "plot_dir": None,
            "record_counts": {"choice": 0, "numeric": 0, "freetext": 0},
            "extensions": [],
        }

    probe_records = _extract_probe_records(events, run_dir, _probe_filter_for_mode(mode))
    out_dir = output_json.parent / f"{output_json.stem}_plots"
    out_dir.mkdir(parents=True, exist_ok=True)

    generated: list[str] = []
    if mode in {"probe_metrics", "probe_binary", "probe_choice"}:
        generated.extend(_write_choice_plots(probe_records["choice"], out_dir))
    if mode in {"probe_metrics", "probe_numeric"}:
        generated.extend(_write_numeric_plots(probe_records["numeric"], out_dir))
    if mode in {"probe_metrics", "probe_freetext"}:
        generated.extend(_write_freetext_plots(probe_records["freetext"], out_dir))

    extensions: list[dict[str, Any]] = []
    context = {
        "mode": mode,
        "run_dir": str(run_dir),
        "output_json": str(output_json),
        "plot_dir": str(out_dir),
    }
    for ref in postprocessors or []:
        try:
            plugin = _load_postprocessor(ref)
            result = plugin(probe_records, out_dir, context)
            plugin_files = _extract_plugin_files(result)
            generated.extend(plugin_files)
            extensions.append(
                {
                    "postprocessor": ref,
                    "status": "success",
                    "generated_files": plugin_files,
                    "result": _json_safe_plugin_result(result),
                }
            )
        except Exception as exc:  # pragma: no cover
            extensions.append(
                {
                    "postprocessor": ref,
                    "status": "failed",
                    "error": str(exc),
                    "generated_files": [],
                }
            )

    return {
        "plot_dir": str(out_dir),
        "generated_files": generated,
        "record_counts": {
            "choice": len(probe_records["choice"]),
            "numeric": len(probe_records["numeric"]),
            "freetext": len(probe_records["freetext"]),
        },
        "extensions": extensions,
    }


def _load_postprocessor(
    ref: str,
) -> Callable[[dict[str, list[dict[str, Any]]], Path, dict[str, Any]], Any]:
    """Resolve a module:function reference into a callable postprocessor."""
    module_name, sep, attr_name = ref.partition(":")
    module_name = module_name.strip()
    attr_name = attr_name.strip() if sep else "postprocess"
    if not module_name or not attr_name:
        raise ValueError(f"Invalid postprocessor reference: {ref}")

    if module_name.endswith(".py") or "/" in module_name or "\\" in module_name:
        module_path = Path(module_name).expanduser()
        if not module_path.is_absolute():
            module_path = (Path.cwd() / module_path).resolve()
        if not module_path.is_file():
            raise FileNotFoundError(f"Postprocessor module file not found: {module_path}")

        spec_name = f"postprocessor_{_slug(str(module_path))}"
        spec = importlib.util.spec_from_file_location(spec_name, module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load postprocessor module from file: {module_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    else:
        module = importlib.import_module(module_name)

    func = getattr(module, attr_name, None)
    if not callable(func):
        raise TypeError(f"Postprocessor is not callable: {ref}")
    return func


def _extract_plugin_files(result: Any) -> list[str]:
    """Extract the list of generated file paths from a postprocessor result."""
    if isinstance(result, list):
        return [str(item) for item in result]
    if isinstance(result, dict):
        files = result.get("generated_files")
        if isinstance(files, list):
            return [str(item) for item in files]
    return []


def _json_safe_plugin_result(result: Any) -> Any:
    """Return a JSON-serializable form of a postprocessor result."""
    if isinstance(result, (dict, list, str, int, float, bool)) or result is None:
        return result
    return str(result)


def _infer_probe_type(response: Any) -> str:
    """Infer the probe type (binary/numeric/choice/freetext) from a response."""
    if _normalize_binary(response) is not None:
        return "BinaryProbe"
    if _safe_float(response) is not None:
        return "NumericRatingProbe"
    text = str(response or "").strip()
    if not text:
        return "Unknown"
    if len(text.split()) <= _CHOICE_WORD_THRESHOLD:
        return "ChoiceProbe"
    return "FreeTextProbe"


def _build_action_metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Build action metrics aggregated per episode, agent, and transition."""
    action_rows = [row for row in events if str(row.get("event_type", "")).lower() == "action"]
    label_counts = Counter(str(row.get("label", "")) for row in action_rows)

    per_episode_counts: dict[int, Counter[str]] = defaultdict(Counter)
    per_episode_agents: dict[int, set[str]] = defaultdict(set)
    per_agent_counts: dict[str, Counter[str]] = defaultdict(Counter)
    per_agent_episode: dict[str, dict[int, Counter[str]]] = defaultdict(
        lambda: defaultdict(Counter)
    )

    by_agent_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in action_rows:
        episode = _safe_int(row.get("episode"), 0)
        agent = str(row.get("source_user", "")).strip() or "unknown"
        label = str(row.get("label", "")).strip() or "unknown"

        per_episode_counts[episode][label] += 1
        per_episode_agents[episode].add(agent)
        per_agent_counts[agent][label] += 1
        per_agent_episode[agent][episode][label] += 1
        by_agent_rows[agent].append(row)

    transition_counts: Counter[str] = Counter()
    for agent, rows in by_agent_rows.items():
        _ = agent
        rows_sorted = sorted(
            rows,
            key=lambda r: (
                _safe_int(r.get("episode"), -1),
                _safe_int(r.get("event_index"), -1),
            ),
        )
        labels = [str(r.get("label", "")).strip() or "unknown" for r in rows_sorted]
        for idx in range(1, len(labels)):
            transition_counts[f"{labels[idx - 1]} -> {labels[idx]}"] += 1

    per_episode_out: dict[str, Any] = {}
    for episode in sorted(per_episode_counts.keys()):
        total = sum(per_episode_counts[episode].values())
        active_agents = sorted(per_episode_agents[episode])
        per_episode_out[str(episode)] = {
            "total_actions": total,
            "active_agents": active_agents,
            "num_active_agents": len(active_agents),
            "avg_actions_per_active_agent": (total / len(active_agents)) if active_agents else 0.0,
            "label_counts": dict(per_episode_counts[episode]),
        }

    per_agent_out: dict[str, Any] = {}
    per_agent_episode_out: dict[str, Any] = {}
    for agent in sorted(per_agent_counts.keys()):
        rows = by_agent_rows[agent]
        episodes = sorted({_safe_int(r.get("episode"), 0) for r in rows})
        total_actions = sum(per_agent_counts[agent].values())
        per_agent_out[agent] = {
            "total_actions": total_actions,
            "first_episode": episodes[0] if episodes else None,
            "last_episode": episodes[-1] if episodes else None,
            "label_counts": dict(per_agent_counts[agent]),
        }

        nested: dict[str, Any] = {}
        for episode, counts in sorted(per_agent_episode[agent].items()):
            nested[str(episode)] = {
                "total_actions": sum(counts.values()),
                "label_counts": dict(counts),
            }
        per_agent_episode_out[agent] = nested

    return {
        "summary_type": "action_metrics",
        "total_events": len(events),
        "total_action_events": len(action_rows),
        "unique_action_labels": sorted(label_counts.keys()),
        "label_counts": dict(label_counts),
        "per_episode": per_episode_out,
        "per_agent": per_agent_out,
        "per_agent_per_episode": per_agent_episode_out,
        "transition_counts": dict(transition_counts.most_common(200)),
    }


def _accumulate_typed_response(
    probe_type: str,
    label: str,
    response: Any,
    *,
    per_type: dict[str, Counter[str]],
    numeric_values: dict[str, list[float]],
    choice_values: dict[str, Counter[str]],
    free_text_lengths: dict[str, list[float]],
    free_text_word_counts: dict[str, list[float]],
    free_text_tokens: dict[str, Counter[str]],
) -> None:
    """Accumulate one present probe response into the per-type/per-label stats.

    Registry-driven (``ProbeBase.analysis_kind``): custom probe classes join
    these buckets by declaring a kind, with no edit here.
    """
    kind = probe_analysis_kind(probe_type)
    if kind == "binary":
        normalized = _normalize_binary(response)
        key = normalized if normalized is not None else "Other"
        per_type[probe_type][f"answer_{key}"] += 1
    elif kind == "numeric":
        num = _safe_float(response)
        if num is not None:
            numeric_values[label].append(num)
            per_type[probe_type]["numeric_values_parsed"] += 1
        else:
            per_type[probe_type]["numeric_values_unparsed"] += 1
    elif kind == "choice":
        value = str(response).strip() or "<empty>"
        choice_values[label][value] += 1
    elif kind == "freetext":
        text = str(response)
        free_text_lengths[label].append(float(len(text)))
        words = text.split()
        free_text_word_counts[label].append(float(len(words)))
        for token in _TOKEN_RE.findall(text.lower()):
            free_text_tokens[label][token] += 1


def _build_per_label_probe_output(
    per_label_counts: dict[str, Counter[str]],
    per_label_modes: dict[str, Counter[str]],
    type_map: dict[str, str],
    *,
    numeric_values: dict[str, list[float]],
    choice_values: dict[str, Counter[str]],
    free_text_lengths: dict[str, list[float]],
    free_text_word_counts: dict[str, list[float]],
    free_text_tokens: dict[str, Counter[str]],
) -> dict[str, Any]:
    """Build the per-label probe summary (counts, stats, and token frequencies)."""
    per_label_out: dict[str, Any] = {}
    for label, counts in sorted(per_label_counts.items()):
        entry: dict[str, Any] = {
            "probe_type": type_map.get(label) or "inferred",
            "total_events": int(counts.get("total_events", 0)),
            "responses_present": int(counts.get("responses_present", 0)),
            "responses_missing": int(counts.get("responses_missing", 0)),
            "probe_mode_counts": dict(per_label_modes[label]),
        }
        if numeric_values.get(label):
            entry["numeric_response_stats"] = _summary_stats(numeric_values[label])
        if choice_values.get(label):
            entry["choice_value_counts"] = dict(choice_values[label])
        if free_text_lengths.get(label):
            entry["free_text_char_len_stats"] = _summary_stats(free_text_lengths[label])
            entry["free_text_word_count_stats"] = _summary_stats(free_text_word_counts[label])
            entry["free_text_top_tokens"] = dict(free_text_tokens[label].most_common(30))
        per_label_out[label] = entry
    return per_label_out


@dataclass
class _ProbeAccumulation:
    """Counters accumulated over one filtered pass of probe rows."""

    per_type: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    per_label_counts: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    per_label_modes: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    per_episode_counts: dict[int, int] = field(default_factory=lambda: defaultdict(int))
    per_agent_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    per_agent_episode_counts: dict[str, dict[int, int]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(int))
    )
    numeric_values: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    free_text_lengths: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    free_text_word_counts: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    free_text_tokens: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    choice_values: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    kept_rows: int = 0
    dropped_rows: int = 0


def _accumulate_probe_rows(
    probe_rows: list[dict[str, Any]],
    type_map: dict[str, str],
    probe_type_filter: str | None,
) -> _ProbeAccumulation:
    """Fold probe rows into per-type/label/episode/agent counters (one pass)."""
    acc = _ProbeAccumulation()
    for row in probe_rows:
        label = str(row.get("label", "")).strip() or "unknown"
        episode = _safe_int(row.get("episode"), 0)
        agent = str(row.get("source_user", "")).strip() or "unknown"

        data = row.get("data", {})
        data = data if isinstance(data, dict) else {}
        response = data.get("probe_return")
        has_response = response is not None and str(response).strip() != ""

        probe_type = type_map.get(label) or _infer_probe_type(response)

        if probe_type_filter is not None and probe_type != probe_type_filter:
            acc.dropped_rows += 1
            continue

        acc.kept_rows += 1
        acc.per_type[probe_type]["total_events"] += 1
        acc.per_type[probe_type]["responses_present" if has_response else "responses_missing"] += 1

        acc.per_label_counts[label]["total_events"] += 1
        acc.per_label_counts[label][
            "responses_present" if has_response else "responses_missing"
        ] += 1

        probe_mode = str(data.get("probe_mode", "")).strip() or "unknown"
        acc.per_label_modes[label][probe_mode] += 1

        acc.per_episode_counts[episode] += 1
        acc.per_agent_counts[agent] += 1
        acc.per_agent_episode_counts[agent][episode] += 1

        if not has_response:
            continue

        _accumulate_typed_response(
            probe_type,
            label,
            response,
            per_type=acc.per_type,
            numeric_values=acc.numeric_values,
            choice_values=acc.choice_values,
            free_text_lengths=acc.free_text_lengths,
            free_text_word_counts=acc.free_text_word_counts,
            free_text_tokens=acc.free_text_tokens,
        )
    return acc


def _build_probe_metrics_with_context(
    events: list[dict[str, Any]],
    run_dir: Path,
    probe_type_filter: str | None = None,
) -> dict[str, Any]:
    """Build probe metrics per type, label, episode, and agent from run context."""
    probe_rows_raw = [row for row in events if str(row.get("event_type", "")).lower() == "probe"]
    hold_last_response = _load_probe_hold_last_response(run_dir)
    probe_rows = (
        _apply_carry_forward_probe_rows(probe_rows_raw) if hold_last_response else probe_rows_raw
    )
    type_map = _load_probe_type_map(run_dir)

    acc = _accumulate_probe_rows(probe_rows, type_map, probe_type_filter)

    probe_type_by_label = {
        label: (type_map.get(label) or "inferred") for label in sorted(acc.per_label_counts.keys())
    }
    per_label_out = _build_per_label_probe_output(
        acc.per_label_counts,
        acc.per_label_modes,
        type_map,
        numeric_values=acc.numeric_values,
        choice_values=acc.choice_values,
        free_text_lengths=acc.free_text_lengths,
        free_text_word_counts=acc.free_text_word_counts,
        free_text_tokens=acc.free_text_tokens,
    )
    per_type_out = {ptype: dict(counter) for ptype, counter in sorted(acc.per_type.items())}

    return {
        "summary_type": "probe_metrics",
        "filter_probe_type": probe_type_filter,
        "total_events": len(events),
        "total_probe_events": len(probe_rows),
        "total_probe_events_raw": len(probe_rows_raw),
        "carry_forward_enabled": hold_last_response,
        "filtered_probe_events": acc.kept_rows,
        "filtered_out_probe_events": acc.dropped_rows,
        "probe_type_by_label": probe_type_by_label,
        "per_type": per_type_out,
        "per_label": per_label_out,
        "per_episode": {str(k): int(v) for k, v in sorted(acc.per_episode_counts.items())},
        "per_agent": {k: int(v) for k, v in sorted(acc.per_agent_counts.items())},
        "per_agent_per_episode": {
            agent: {str(ep): int(cnt) for ep, cnt in sorted(ep_map.items())}
            for agent, ep_map in sorted(acc.per_agent_episode_counts.items())
        },
    }


def _build_payload(mode: str, events: list[dict[str, Any]], run_dir: Path) -> dict[str, Any]:
    """Dispatch to the evaluator for the given mode and return its payload."""
    if mode == "action_metrics":
        return _build_action_metrics(events)
    if mode == "probe_metrics":
        return _build_probe_metrics_with_context(events, run_dir)
    if mode == "probe_binary":
        return _build_probe_metrics_with_context(events, run_dir, probe_type_filter="BinaryProbe")
    if mode == "probe_numeric":
        return _build_probe_metrics_with_context(
            events, run_dir, probe_type_filter="NumericRatingProbe"
        )
    if mode == "probe_choice":
        return _build_probe_metrics_with_context(events, run_dir, probe_type_filter="ChoiceProbe")
    if mode == "probe_freetext":
        return _build_probe_metrics_with_context(events, run_dir, probe_type_filter="FreeTextProbe")
    raise ValueError(f"Unsupported mode: {mode}")


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for default evaluators."""
    parser = argparse.ArgumentParser(description="Run default detailed evaluators")
    parser.add_argument("--run-dir", required=True, help="Path to simulation output run directory")
    parser.add_argument("--output", required=True, help="Path to write evaluator output JSON")
    parser.add_argument(
        "--mode",
        required=True,
        choices=sorted(VALID_MODES),
        help="Evaluator mode",
    )
    parser.add_argument(
        "--postprocessor",
        action="append",
        default=[],
        help=(
            "Optional probe postprocessor in module:function format. "
            "Called only for probe_* modes with arguments "
            "(probe_records_by_type, plot_dir, context)."
        ),
    )
    return parser.parse_args()


def main() -> None:
    """Run selected evaluator and write JSON payload."""
    args = parse_args()
    run_dir = Path(args.run_dir).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()

    # Probe and action events are persisted in separate files.
    # Use the mode to select the correct source for evaluation.
    if args.mode.startswith("probe_"):
        event_files = [run_dir / "probe_events.jsonl"]
    else:
        # Multi-GM runs isolate action logs under per-GM subdirectories.
        event_files = resolve_action_event_files(run_dir)

    event_files = [path for path in event_files if path.is_file()]
    if not event_files:
        raise FileNotFoundError(f"Missing events file(s) for mode {args.mode!r} in {run_dir}")

    # Tolerant shared reader (skips blank/malformed lines), per the run-artifact contract.
    events: list[dict[str, Any]] = list(iter_jsonl(event_files))
    payload = _build_payload(args.mode, events, run_dir)
    payload["mode"] = args.mode
    payload["run_dir"] = str(run_dir)
    payload["source_file"] = ", ".join(str(path) for path in event_files)
    payload["plots"] = _build_probe_plots(
        args.mode,
        events,
        run_dir,
        output,
        postprocessors=list(args.postprocessor or []),
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
