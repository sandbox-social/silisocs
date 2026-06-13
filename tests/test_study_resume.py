"""Tests for study-runner resume markers, checkpoint cadence, stats, and preflight."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

import pytest

from silisocs.studies.run_study import (
    RUN_COMPLETE_MARKER,
    EvalSpec,
    RunSpec,
    StudyConfigError,
    _confirm_run_count,
    _expand_runs,
    _partition_completed_runs,
    _planned_run_dir,
    _preflight_summary,
)
from silisocs.studies.study_artifacts import (
    _combine_eval_payloads,
    _metric_stats,
    _t_critical_95,
    build_summary,
)


def _make_spec(output_rootname: str | None, **kwargs: Any) -> RunSpec:
    defaults: dict[str, Any] = {
        "run_id": "h1__c1__default__seed1",
        "study_name": "resume_test",
        "study_id": "resume_test",
        "hypothesis_id": "h1",
        "condition_id": "c1",
        "sub_experiment": "c1",
        "scenario": "default",
        "seed": 1,
        "run_name": "resume_test_h1_c1_seed1",
        "execution_mode": "run",
        "overrides": {},
        "config_path": None,
        "runner_module": "silisocs.runtime.runner",
        "re_evaluate": False,
        "output_rootname": output_rootname,
    }
    defaults.update(kwargs)
    return RunSpec(**defaults)


def _make_eval_spec(eval_id: str = "myeval") -> EvalSpec:
    return EvalSpec(
        eval_id=eval_id,
        enabled=True,
        command=("echo",),
        input_mode="run_dir",
        output_arg="--output",
        run_dir_arg="--run-dir",
        static_args=(),
        explicit_args={},
        include_run_dir_in_explicit=True,
        output_subpath="eval.json",
        manage_io_args=True,
    )


def _write_complete_run_dir(run_dir: Path, run_id: str) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "effective_config.yaml").write_text("num_steps: 1\n", encoding="utf-8")
    (run_dir / RUN_COMPLETE_MARKER).write_text(
        json.dumps(
            {
                "run_id": run_id,
                "finished_at": "2026-06-13T00-00-00Z",
                "effective_config_sha256": "abc123",
                "return_code": 0,
            }
        ),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# 1. Idempotent resume: marker skip behavior
# ---------------------------------------------------------------------------


def test_partition_skips_run_with_complete_marker(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "run1"
    spec = _make_spec(str(run_dir))
    _write_complete_run_dir(run_dir, spec.run_id)

    pending, skipped = _partition_completed_runs(
        [spec], tmp_path, tmp_path / "generated", force=False
    )

    assert pending == []
    assert len(skipped) == 1
    record = skipped[0]
    assert record["status"] == "skipped_complete"
    assert record["run_id"] == spec.run_id
    assert record["run_dir"] == str(run_dir)
    assert record["lock"]["effective_config_sha256"] is not None
    assert record["complete_marker"]["run_id"] == spec.run_id


def test_partition_force_reruns_completed_run(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "run1"
    spec = _make_spec(str(run_dir))
    _write_complete_run_dir(run_dir, spec.run_id)

    pending, skipped = _partition_completed_runs(
        [spec], tmp_path, tmp_path / "generated", force=True
    )

    assert pending == [spec]
    assert skipped == []


def test_partition_keeps_run_without_marker(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "run1"
    run_dir.mkdir(parents=True)
    spec = _make_spec(str(run_dir))

    pending, skipped = _partition_completed_runs(
        [spec], tmp_path, tmp_path / "generated", force=False
    )

    assert pending == [spec]
    assert skipped == []


def test_partition_never_skips_reuse_existing(tmp_path: Path) -> None:
    spec = _make_spec(None, execution_mode="reuse_existing", reused_source="old/run")

    pending, skipped = _partition_completed_runs(
        [spec], tmp_path, tmp_path / "generated", force=False
    )

    assert pending == [spec]
    assert skipped == []


def test_planned_run_dir_resolves_relative_rootname(tmp_path: Path) -> None:
    spec = _make_spec("runs/relative/run")

    planned = _planned_run_dir(spec, tmp_path)

    assert planned == (tmp_path / "runs" / "relative" / "run").resolve()
    assert _planned_run_dir(_make_spec(None), tmp_path) is None


def test_skipped_record_relinks_existing_eval_outputs(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "run1"
    spec = _make_spec(str(run_dir), eval_specs=(_make_eval_spec(),))
    _write_complete_run_dir(run_dir, spec.run_id)

    generated_dir = tmp_path / "generated"
    eval_output = (
        generated_dir / "eval" / "h1" / "c1" / "default" / "seed_1" / "myeval" / "eval.json"
    )
    eval_output.parent.mkdir(parents=True)
    eval_output.write_text("{}", encoding="utf-8")

    _, skipped = _partition_completed_runs([spec], tmp_path, generated_dir, force=False)

    record = skipped[0]
    assert record["eval_paths"] == {"myeval": str(eval_output)}
    assert record["evaluations"][0]["status"] == "reused"


# ---------------------------------------------------------------------------
# 2. Checkpoint cadence injection
# ---------------------------------------------------------------------------


def _study_data(run_defaults_extra: dict[str, Any] | None = None) -> dict[str, Any]:
    run_defaults: dict[str, Any] = {"scenario": "default", "seeds": [1]}
    if run_defaults_extra:
        run_defaults.update(run_defaults_extra)
    return {
        "schema_version": 1,
        "study": {
            "name": "resume_test",
            "study_id": "resume_test",
            "run_defaults": run_defaults,
        },
        "hypotheses": {"h1": {"conditions": {"c1": {"overrides": {}}}}},
    }


def _expanded_overrides(tmp_path: Path, study_data: dict[str, Any]) -> dict[str, Any]:
    run_specs, _, _ = _expand_runs(tmp_path / "study.yaml", study_data)
    assert len(run_specs) == 1
    return run_specs[0].overrides


def test_checkpoint_cadence_defaults_to_one(tmp_path: Path) -> None:
    overrides = _expanded_overrides(tmp_path, _study_data())
    assert overrides["sim.checkpoint.every_n_steps"] == 1


def test_checkpoint_cadence_custom_integer(tmp_path: Path) -> None:
    overrides = _expanded_overrides(tmp_path, _study_data({"checkpoint_every_n_steps": 5}))
    assert overrides["sim.checkpoint.every_n_steps"] == 5


@pytest.mark.parametrize("disabled_value", [None, 0, False])
def test_checkpoint_cadence_disabled(tmp_path: Path, disabled_value: Any) -> None:
    overrides = _expanded_overrides(
        tmp_path, _study_data({"checkpoint_every_n_steps": disabled_value})
    )
    assert "sim.checkpoint.every_n_steps" not in overrides


def test_checkpoint_cadence_rejects_non_integer(tmp_path: Path) -> None:
    with pytest.raises(StudyConfigError, match="checkpoint_every_n_steps"):
        _expanded_overrides(tmp_path, _study_data({"checkpoint_every_n_steps": "often"}))


def test_checkpoint_cadence_explicit_override_still_wins(tmp_path: Path) -> None:
    study_data = _study_data({"overrides": {"sim.checkpoint.every_n_steps": 7}})
    overrides = _expanded_overrides(tmp_path, study_data)
    assert overrides["sim.checkpoint.every_n_steps"] == 7


# ---------------------------------------------------------------------------
# 3. Cross-seed statistics
# ---------------------------------------------------------------------------


def test_metric_stats_known_values() -> None:
    stats = _metric_stats([1.0, 2.0, 3.0])
    half_width = 4.303 * 1.0 / math.sqrt(3)

    assert stats["n"] == 3
    assert stats["mean"] == pytest.approx(2.0)
    assert stats["stdev"] == pytest.approx(1.0)
    assert stats["ci95_low"] == pytest.approx(2.0 - half_width)
    assert stats["ci95_high"] == pytest.approx(2.0 + half_width)
    assert stats["ci95_high"] - stats["mean"] == pytest.approx(2.484, abs=1e-3)


def test_metric_stats_single_value_has_no_ci() -> None:
    stats = _metric_stats([4.5])
    assert stats == {"n": 1, "mean": 4.5, "stdev": None, "ci95_low": None, "ci95_high": None}


def test_t_critical_table_and_normal_fallback() -> None:
    assert _t_critical_95(1) == pytest.approx(12.706)
    assert _t_critical_95(2) == pytest.approx(4.303)
    assert _t_critical_95(30) == pytest.approx(2.042)
    assert _t_critical_95(31) == pytest.approx(1.96)
    assert _t_critical_95(1000) == pytest.approx(1.96)


def test_combine_eval_payloads_adds_stats_and_keeps_means() -> None:
    payloads = [
        {"aggregated": {"m": 1.0}, "summary": {"posts": 2}, "agents": {}},
        {"aggregated": {"m": 3.0}, "summary": {"posts": 4}, "agents": {}},
    ]

    combined = _combine_eval_payloads(payloads)

    # Existing keys are unchanged.
    assert combined["aggregated"]["m"] == pytest.approx(2.0)
    assert combined["summary"]["posts"] == 6

    stats = combined["aggregated_stats"]["m"]
    assert stats["n"] == 2
    assert stats["mean"] == pytest.approx(2.0)
    assert stats["stdev"] == pytest.approx(math.sqrt(2.0))
    assert stats["ci95_low"] == pytest.approx(2.0 - 12.706)
    assert stats["ci95_high"] == pytest.approx(2.0 + 12.706)


def test_combine_eval_payloads_single_payload_backcompat() -> None:
    combined = _combine_eval_payloads([{"aggregated": {"m": 1.0}}])
    assert combined["aggregated"] == {"m": 1.0}
    assert combined["aggregated_stats"] == {}


def test_build_summary_reports_cross_replicate_stats() -> None:
    entries = [
        {
            "_meta": {"hypothesis": "h1", "condition": "c1", "scenario": "s"},
            "aggregated": {"m": value},
            "summary": {},
        }
        for value in (1.0, 2.0, 3.0)
    ]

    summary = build_summary(entries)

    assert summary["metrics_by_condition"]["h1"]["c1"]["m"] == pytest.approx(2.0)
    stats = summary["metrics_stats_by_condition"]["h1"]["c1"]["m"]
    assert stats["n"] == 3
    assert stats["ci95_high"] - stats["mean"] == pytest.approx(2.484, abs=1e-3)


# ---------------------------------------------------------------------------
# 4. Preflight summary and confirmation gate
# ---------------------------------------------------------------------------


def test_preflight_summary_estimates_agent_steps() -> None:
    specs = [
        _make_spec(None, run_id="a", overrides={"num_agents": 5, "num_steps": 10}),
        _make_spec(None, run_id="b", overrides={"num_agents": 2, "num_steps": 3}),
    ]

    lines = _preflight_summary(specs)

    assert lines[0] == "Preflight: 2 run(s) planned"
    assert "  - a: num_agents=5 num_steps=10" in lines
    assert lines[-1] == "Estimated total agent-steps: 56"


def test_preflight_summary_unknown_scale_falls_back() -> None:
    specs = [
        _make_spec(None, run_id="a", overrides={"num_agents": 5, "num_steps": 10}),
        _make_spec(None, run_id="b", overrides={}),
    ]

    lines = _preflight_summary(specs)

    assert "  - b: num_agents=? num_steps=?" in lines
    assert lines[-1] == "Estimated total agent-steps: >= 50 (1 run(s) with unknown scale)"

    all_unknown = _preflight_summary([_make_spec(None, run_id="c", overrides={})])
    assert all_unknown[-1] == "Estimated total agent-steps: ? (1 run(s) with unknown scale)"


class _FakeStdin:
    def __init__(self, tty: bool) -> None:
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def test_confirm_run_count_below_threshold_and_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=False))
    assert _confirm_run_count(50, assume_yes=False) is True
    assert _confirm_run_count(51, assume_yes=True) is True


def test_confirm_run_count_aborts_without_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=False))
    assert _confirm_run_count(51, assume_yes=False) is False


def test_confirm_run_count_prompts_on_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=True))

    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    assert _confirm_run_count(51, assume_yes=False) is True

    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    assert _confirm_run_count(51, assume_yes=False) is False
