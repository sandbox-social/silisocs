# ruff: noqa: D103, PLR2004

"""Tests for default detailed study evaluators."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

pytest.importorskip("matplotlib", reason="default evaluator plotting tests require analysis extra")

from silisocs.evaluations.default_evaluators import (
    _build_payload,
    _build_probe_plots,
    _read_jsonl,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def test_action_metrics_include_per_agent_and_episode(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_jsonl(
        run_dir / "action_events.jsonl",
        [
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
                "label": "reply",
                "data": {},
                "episode": 2,
                "event_type": "action",
                "event_index": 3,
            },
        ],
    )

    events = _read_jsonl(run_dir / "action_events.jsonl")
    payload = _build_payload("action_metrics", events, run_dir)

    assert payload["total_action_events"] == 3
    assert payload["label_counts"]["post"] == 1
    assert payload["per_episode"]["1"]["total_actions"] == 2
    assert payload["per_agent"]["Alice"]["total_actions"] == 2
    assert payload["per_agent_per_episode"]["Alice"]["1"]["label_counts"]["like"] == 1


def test_probe_metrics_use_configured_probe_types(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_jsonl(
        run_dir / "action_events.jsonl",
        [
            {
                "source_user": "Alice",
                "label": "vote_intent",
                "data": {"probe_return": "Yes", "probe_mode": "single_structured_lines"},
                "episode": 1,
                "event_type": "probe",
                "event_index": 1,
            },
            {
                "source_user": "Alice",
                "label": "candidate_score",
                "data": {"probe_return": "7", "probe_mode": "single_structured_lines"},
                "episode": 1,
                "event_type": "probe",
                "event_index": 2,
            },
            {
                "source_user": "Bob",
                "label": "open_comment",
                "data": {
                    "probe_return": "I care about education and local jobs.",
                    "probe_mode": "single_structured_lines",
                },
                "episode": 2,
                "event_type": "probe",
                "event_index": 3,
            },
        ],
    )

    cfg = {
        "scenario": {
            "probes": {
                "probes": {
                    "vote_intent": {
                        "probe_name": "vote_intent",
                        "probe_type": "BinaryProbe",
                        "probe_data": {"name": "VoteIntent"},
                    },
                    "candidate_score": {
                        "probe_name": "candidate_score",
                        "probe_type": "NumericRatingProbe",
                        "probe_data": {"name": "CandidateScore"},
                    },
                    "open_comment": {
                        "probe_name": "open_comment",
                        "probe_type": "FreeTextProbe",
                        "probe_data": {"name": "OpenComment"},
                    },
                }
            }
        }
    }
    (run_dir / "effective_config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")

    events = _read_jsonl(run_dir / "action_events.jsonl")
    payload = _build_payload("probe_metrics", events, run_dir)

    assert payload["total_probe_events"] == 3
    assert payload["per_type"]["BinaryProbe"]["answer_Yes"] == 1
    assert payload["per_label"]["candidate_score"]["numeric_response_stats"]["count"] == 1
    assert "free_text_char_len_stats" in payload["per_label"]["open_comment"]


def test_probe_type_filtered_mode(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_jsonl(
        run_dir / "action_events.jsonl",
        [
            {
                "source_user": "Alice",
                "label": "vote_intent",
                "data": {"probe_return": "Yes"},
                "episode": 1,
                "event_type": "probe",
                "event_index": 1,
            },
            {
                "source_user": "Bob",
                "label": "candidate_score",
                "data": {"probe_return": "8"},
                "episode": 1,
                "event_type": "probe",
                "event_index": 2,
            },
        ],
    )

    cfg = {
        "scenario": {
            "probes": {
                "probes": {
                    "vote_intent": {
                        "probe_name": "vote_intent",
                        "probe_type": "BinaryProbe",
                        "probe_data": {"name": "VoteIntent"},
                    },
                    "candidate_score": {
                        "probe_name": "candidate_score",
                        "probe_type": "NumericRatingProbe",
                        "probe_data": {"name": "CandidateScore"},
                    },
                }
            }
        }
    }
    (run_dir / "effective_config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")

    events = _read_jsonl(run_dir / "action_events.jsonl")
    payload = _build_payload("probe_binary", events, run_dir)

    assert payload["filter_probe_type"] == "BinaryProbe"
    assert payload["filtered_probe_events"] == 1
    assert payload["filtered_out_probe_events"] == 1


def test_probe_postprocessor_accepts_nonexistent_output_json_context(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_jsonl(
        run_dir / "probe_events.jsonl",
        [
            {
                "source_user": "Alice",
                "label": "vote_choice",
                "data": {"probe_return": "Bill Fredrickson"},
                "episode": 1,
                "event_type": "probe",
                "event_index": 1,
            },
            {
                "source_user": "Bob",
                "label": "vote_choice",
                "data": {"probe_return": "Bradley Carter"},
                "episode": 1,
                "event_type": "probe",
                "event_index": 2,
            },
        ],
    )

    cfg = {
        "scenario": {
            "probes": {
                "probes": {
                    "vote_choice": {
                        "probe_name": "vote_choice",
                        "probe_type": "ChoiceProbe",
                        "probe_data": {
                            "name": "VoteChoice",
                            "choices": ["Bill Fredrickson", "Bradley Carter"],
                        },
                    }
                }
            }
        }
    }
    (run_dir / "effective_config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")

    events = _read_jsonl(run_dir / "probe_events.jsonl")
    output_json = tmp_path / "eval" / "probe_metrics" / "probe_metrics_detailed.json"
    output_json.parent.mkdir(parents=True, exist_ok=True)

    # In evaluator flow, output_json does not exist yet when postprocessors execute.
    plots = _build_probe_plots(
        "probe_metrics",
        events,
        run_dir,
        output_json,
        postprocessors=[
            "scenarios/election_recsys_engagement/postprocessors.py:cross_seed_case_ci"
        ],
    )

    ext = plots["extensions"]
    assert len(ext) == 1
    assert ext[0]["status"] == "success"
