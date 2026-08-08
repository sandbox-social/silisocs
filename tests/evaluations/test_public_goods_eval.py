from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

_EVAL_PATH = (
    Path(__file__).resolve().parents[2]
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
                # Mirrors the backend: resolved rows carry the game parameters.
                "data": {
                    "round": rnd,
                    "group_size": group_size,
                    "endowment": endowment,
                    "multiplier": multiplier,
                },
            }
        )
    (tmp_path / "action_events.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    return tmp_path


def _write_manifest(run_dir: Path, *, num_steps: int, turn_failures: int = 0) -> None:
    """Write the minimal run_manifest.json slice the evaluator reads."""
    (run_dir / "run_manifest.json").write_text(
        json.dumps(
            {
                "status": "success",
                "num_steps": num_steps,
                "health": {"agent_turn_failures": turn_failures, "action_parse_failures": 0},
            }
        ),
        encoding="utf-8",
    )


def _write_sim_metrics(run_dir: Path, *, episodes: int) -> None:
    """Write the minimal sim_metrics.json slice the evaluator reads."""
    (run_dir / "sim_metrics.json").write_text(
        json.dumps({"episode_metrics": [{"episode": idx} for idx in range(episodes)]}),
        encoding="utf-8",
    )


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


def test_silent_final_round_counts_as_defection(tmp_path: Path) -> None:
    """Endgame defection must lower the metric, not vanish from the grid.

    The final round has no following ``update`` (no round_resolved row), so a
    last round in which nobody contributed is only visible through the
    manifest's configured episode count.
    """
    agents = ["Alex", "Blair", "Casey", "Devon"]
    contribs = [(a, rnd, 20.0) for rnd in range(2) for a in agents]  # rounds 0-1 full
    run_dir = _write_run(tmp_path, contribs, resolved_rounds=[0, 1])
    _write_manifest(run_dir, num_steps=3)  # round 2 ran but was fully silent
    result = _EVAL.evaluate_run_dir(run_dir)
    assert result["summary"]["rounds"] == 3
    assert result["aggregated"]["avg_contribution_rate"] == pytest.approx(2.0 / 3.0)


def test_early_stopped_run_scores_only_the_rounds_that_ran(tmp_path: Path) -> None:
    """An interactively stopped (or crashed) run must not score un-run rounds.

    The manifest's ``num_steps`` is the configured TARGET; the episodes the
    engine actually executed live in sim_metrics.json. A fully cooperative run
    stopped after 3 of 20 configured rounds is rate 1.0, not 3/20.
    """
    agents = ["Alex", "Blair", "Casey", "Devon"]
    contribs = [(a, rnd, 20.0) for rnd in range(3) for a in agents]  # 3 full rounds
    run_dir = _write_run(tmp_path, contribs, resolved_rounds=[0, 1])
    _write_manifest(run_dir, num_steps=20)  # configured target, never reached
    _write_sim_metrics(run_dir, episodes=3)  # what actually ran
    result = _EVAL.evaluate_run_dir(run_dir)
    assert result["summary"]["rounds"] == 3
    assert result["aggregated"]["avg_contribution_rate"] == pytest.approx(1.0)


def test_silent_final_round_counts_via_sim_metrics_not_only_manifest(tmp_path: Path) -> None:
    """The executed-episode count alone must surface a fully silent final round.

    sim_metrics says 4 episodes ran; contribution/resolved rows only reach
    round 2. The manifest target (20) is a stale ceiling — the denominator must
    come from the episodes that actually ran (4), scoring round 3 as defection.
    """
    agents = ["Alex", "Blair", "Casey", "Devon"]
    contribs = [(a, rnd, 20.0) for rnd in range(3) for a in agents]  # rounds 0-2 full
    run_dir = _write_run(tmp_path, contribs, resolved_rounds=[0, 1, 2])
    _write_manifest(run_dir, num_steps=20)
    _write_sim_metrics(run_dir, episodes=4)  # round 3 ran, fully silent
    result = _EVAL.evaluate_run_dir(run_dir)
    assert result["summary"]["rounds"] == 4
    assert result["aggregated"]["avg_contribution_rate"] == pytest.approx(3.0 / 4.0)


def test_all_defect_healthy_run_scores_zero_not_none(tmp_path: Path) -> None:
    """A healthy run where nobody ever contributed is full defection, not a gap.

    Returning ``None`` would silently drop the replicate from the cross-seed
    mean — biasing the exact statistic the study reports.
    """
    run_dir = _write_run(tmp_path, [], resolved_rounds=[0, 1, 2])
    _write_manifest(run_dir, num_steps=3)
    agg = _EVAL.evaluate_run_dir(run_dir)["aggregated"]
    assert agg["avg_contribution_rate"] == pytest.approx(0.0)
    assert agg["efficiency"] == pytest.approx(0.0)
    assert agg["free_rider_share"] == pytest.approx(1.0)
    assert agg["collective_payoff"] == pytest.approx(20.0 * 4 * 3)  # the all-defect floor
    assert agg["pct_of_optimal"] == pytest.approx(1.0 / 1.6)


def test_all_silent_degraded_run_is_excluded(tmp_path: Path) -> None:
    """Silence caused by failing agent turns is a broken pipeline, not defection."""
    run_dir = _write_run(tmp_path, [], resolved_rounds=[0, 1, 2])
    _write_manifest(run_dir, num_steps=3, turn_failures=8)
    assert _EVAL.evaluate_run_dir(run_dir)["aggregated"]["avg_contribution_rate"] is None


def test_all_silent_run_without_manifest_is_excluded(tmp_path: Path) -> None:
    """With no manifest to vouch for run health, silence cannot be attributed."""
    run_dir = _write_run(tmp_path, [], resolved_rounds=[0, 1, 2])
    assert _EVAL.evaluate_run_dir(run_dir)["aggregated"]["avg_contribution_rate"] is None


def test_duplicate_rows_are_deduplicated(tmp_path: Path) -> None:
    agents = ["Alex", "Blair", "Casey", "Devon"]
    # Simulate a resume: round 0 rows appear twice (last-write-wins).
    contribs = [(a, 0, 20.0) for a in agents] + [(a, 0, 20.0) for a in agents]
    result = _EVAL.evaluate_run_dir(_write_run(tmp_path, contribs))
    assert result["summary"]["contribution_events"] == 4  # deduped, not 8
    assert result["aggregated"]["avg_contribution_rate"] == pytest.approx(1.0)
