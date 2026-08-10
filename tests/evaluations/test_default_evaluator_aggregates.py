"""Tests for the flat ``aggregated`` block the builtin evaluators emit.

``aggregated`` is the only key study summaries compare across conditions
(``metrics_by_condition`` / ``metrics_stats_by_condition``), so every builtin
evaluator has to emit one and it has to hold plain numbers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from silisocs.evaluations.default_evaluators import _build_payload
from silisocs.evaluations.run_artifact import iter_jsonl


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _action_events() -> list[dict[str, Any]]:
    return [
        {
            "source_user": "Alice",
            "label": "post",
            "data": {},
            "episode": 1,
            "event_type": "action",
            "event_index": 1,
        },
        {
            "source_user": "Alice",
            "label": "like",
            "data": {},
            "episode": 1,
            "event_type": "action",
            "event_index": 2,
        },
        {
            "source_user": "Bob",
            "label": "post",
            "data": {},
            "episode": 2,
            "event_type": "action",
            "event_index": 3,
        },
    ]


def _assert_flat_numbers(aggregated: dict[str, Any]) -> None:
    """The study layer averages these across seeds: numbers only, no nesting."""
    assert aggregated
    for key, value in aggregated.items():
        assert isinstance(key, str)
        assert isinstance(value, (int, float)) and not isinstance(value, bool), key


def test_action_metrics_emit_aggregated_label_counts(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_jsonl(run_dir / "action_events.jsonl", _action_events())

    payload = _build_payload(
        "action_metrics", list(iter_jsonl([run_dir / "action_events.jsonl"])), run_dir
    )
    aggregated = payload["aggregated"]

    _assert_flat_numbers(aggregated)
    assert aggregated["total_events"] == 3
    assert aggregated["total_action_events"] == 3
    assert aggregated["actions_post"] == 2
    assert aggregated["actions_like"] == 1
    assert aggregated["unique_action_labels"] == 2
    assert aggregated["num_episodes"] == 2
    assert aggregated["num_active_agents"] == 2
    assert aggregated["actions_per_agent"] == 1.5
    assert aggregated["actions_per_episode"] == 1.5


def test_action_metrics_aggregated_on_empty_log(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_jsonl(run_dir / "action_events.jsonl", [])

    payload = _build_payload("action_metrics", [], run_dir)

    assert payload["aggregated"]["total_action_events"] == 0
    assert payload["aggregated"]["actions_per_agent"] == 0.0


def _probe_run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "effective_config.yaml").write_text(
        yaml.safe_dump(
            {
                "eval": {
                    "probes": {
                        "probes": {
                            "worry": {
                                "probe_name": "worry",
                                "probe_type": "BinaryProbe",
                                "probe_data": {"name": "worry"},
                            },
                            "trust": {
                                "probe_name": "trust",
                                "probe_type": "NumericRatingProbe",
                                "probe_data": {"name": "trust"},
                            },
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return run_dir


def test_probe_metrics_emit_aggregated_shares_and_means(tmp_path: Path) -> None:
    run_dir = _probe_run_dir(tmp_path)
    rows = [
        {
            "source_user": "Alice",
            "label": "worry",
            "data": {"probe_return": "Yes"},
            "episode": 1,
            "event_type": "probe",
            "event_index": 1,
        },
        {
            "source_user": "Bob",
            "label": "worry",
            "data": {"probe_return": "No"},
            "episode": 1,
            "event_type": "probe",
            "event_index": 2,
        },
        {
            "source_user": "Alice",
            "label": "trust",
            "data": {"probe_return": "4"},
            "episode": 1,
            "event_type": "probe",
            "event_index": 3,
        },
        {
            "source_user": "Bob",
            "label": "trust",
            "data": {"probe_return": "6"},
            "episode": 1,
            "event_type": "probe",
            "event_index": 4,
        },
        {
            "source_user": "Cara",
            "label": "trust",
            "data": {"probe_return": ""},
            "episode": 1,
            "event_type": "probe",
            "event_index": 5,
        },
    ]
    _write_jsonl(run_dir / "probe_events.jsonl", rows)

    payload = _build_payload(
        "probe_metrics", list(iter_jsonl([run_dir / "probe_events.jsonl"])), run_dir
    )
    aggregated = payload["aggregated"]

    _assert_flat_numbers(aggregated)
    assert aggregated["total_probe_events"] == 5
    assert aggregated["probe_responses_present"] == 4
    assert aggregated["probe_responses_missing"] == 1
    assert aggregated["probe_response_rate"] == 0.8
    assert aggregated["probe_trust_mean"] == 5.0
    assert aggregated["probe_trust_n"] == 2
    assert aggregated["probe_worry_share_yes"] == 0.5
    assert aggregated["probe_worry_share_no"] == 0.5
    # The per_label payload is unchanged by the aggregated block.
    assert "choice_value_counts" not in payload["per_label"]["worry"]


def test_probe_metrics_aggregated_without_responses(tmp_path: Path) -> None:
    run_dir = _probe_run_dir(tmp_path)
    _write_jsonl(run_dir / "probe_events.jsonl", [])

    payload = _build_payload("probe_metrics", [], run_dir)

    assert payload["aggregated"]["total_probe_events"] == 0
    assert payload["aggregated"]["probe_response_rate"] == 0.0
