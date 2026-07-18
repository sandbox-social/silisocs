from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

_EVAL_PATH = (
    Path(__file__).resolve().parents[1]
    / "experiments"
    / "studies"
    / "public_goods_capability"
    / "eval.py"
)


def _load_evaluator():
    spec = importlib.util.spec_from_file_location("pgg_eval", _EVAL_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_EVAL = _load_evaluator()


def _write_run(
    tmp_path: Path,
    contributions: list[tuple[str, int, float]],
    *,
    endowment: float = 20.0,
    multiplier: float = 1.6,
    group_size: int = 4,
    resolved_rounds: list[int] | None = None,
) -> Path:
    """Write a synthetic action_events.jsonl and return the run dir."""
    rows: list[dict[str, Any]] = []
    for idx, (agent, rnd, amount) in enumerate(contributions):
        rows.append(
            {
                "source_user": agent,
                "label": "contribute",
                "episode": rnd,
                "event_type": "action",
                "event_index": idx,
                "data": {
                    "round": rnd,
                    "contribution": amount,
                    "endowment": endowment,
                    "multiplier": multiplier,
                    "group_size": group_size,
                },
            }
        )
    for rnd in resolved_rounds or []:
        rows.append(
            {
                "source_user": "",
                "label": "round_resolved",
                "episode": rnd + 1,
                "event_type": "action",
                "event_index": len(rows),
                "data": {"round": rnd, "group_size": group_size},
            }
        )
    (tmp_path / "action_events.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    return tmp_path


def test_empty_run_returns_none_metrics(tmp_path: Path) -> None:
    _write_run(tmp_path, [])
    result = _EVAL.evaluate_run_dir(tmp_path)
    assert result["aggregated"]["avg_contribution_rate"] is None
    assert result["summary"]["contribution_events"] == 0


def test_full_participation_identity_holds(tmp_path: Path) -> None:
    agents = ["Alex", "Blair", "Casey", "Devon"]
    contribs = (
        [(a, 0, 20.0) for a in agents]  # round 0: all give 20  -> rate 1.0
        + [(a, 1, 10.0) for a in agents]  # round 1: all give 10  -> rate 0.5
        + [(a, 2, 0.0) for a in agents]  # round 2: all give 0   -> rate 0.0
    )
    result = _EVAL.evaluate_run_dir(_write_run(tmp_path, contribs))
    agg = result["aggregated"]
    assert agg["avg_contribution_rate"] == pytest.approx(0.5)
    # Linear-PGG identity: efficiency must equal the contribution rate exactly.
    assert agg["efficiency"] == pytest.approx(agg["avg_contribution_rate"])
    assert agg["free_rider_share"] == pytest.approx(0.0)
    assert result["summary"]["roster"] == 4
    assert result["summary"]["rounds"] == 3


def test_non_acting_player_counts_as_free_rider(tmp_path: Path) -> None:
    # Devon never contributes; Alex/Blair/Casey give 20 every round for 3 rounds.
    acting = ["Alex", "Blair", "Casey"]
    contribs = [(a, rnd, 20.0) for rnd in range(3) for a in acting]
    result = _EVAL.evaluate_run_dir(_write_run(tmp_path, contribs, group_size=4))
    agg = result["aggregated"]
    # roster=4 -> total endowment = 20*4*3 = 240; contributed = 20*3*3 = 180.
    assert agg["avg_contribution_rate"] == pytest.approx(180.0 / 240.0)  # 0.75
    assert agg["efficiency"] == pytest.approx(agg["avg_contribution_rate"])  # identity holds
    assert agg["free_rider_share"] == pytest.approx(0.25)  # Devon = 1 of 4
    assert result["summary"]["roster"] == 4


def test_silent_middle_round_is_counted(tmp_path: Path) -> None:
    agents = ["Alex", "Blair", "Casey", "Devon"]
    # Rounds 0 and 2 full (20 each); round 1 fully silent but backend-resolved.
    contribs = [(a, 0, 20.0) for a in agents] + [(a, 2, 20.0) for a in agents]
    result = _EVAL.evaluate_run_dir(_write_run(tmp_path, contribs, resolved_rounds=[0, 1]))
    # 3 rounds; contributions only in 2 of them -> rate = (20*4*2)/(20*4*3) = 2/3.
    assert result["summary"]["rounds"] == 3
    assert result["aggregated"]["avg_contribution_rate"] == pytest.approx(2.0 / 3.0)


def test_duplicate_rows_are_deduplicated(tmp_path: Path) -> None:
    agents = ["Alex", "Blair", "Casey", "Devon"]
    # Simulate a resume: round 0 rows appear twice (last-write-wins).
    contribs = [(a, 0, 20.0) for a in agents] + [(a, 0, 20.0) for a in agents]
    result = _EVAL.evaluate_run_dir(_write_run(tmp_path, contribs))
    assert result["summary"]["contribution_events"] == 4  # deduped, not 8
    assert result["aggregated"]["avg_contribution_rate"] == pytest.approx(1.0)
