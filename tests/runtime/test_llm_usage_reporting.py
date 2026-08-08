"""Tests for LLM token-usage / cost reporting (Tier 2a)."""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace
from typing import Any, cast

from silisocs.runtime.language_models import OpenAILanguageModel
from silisocs.runtime.telemetry.engine_metrics import (
    collect_retry_telemetry,
    collect_usage_summary,
)


class _Usage:
    def __init__(self, prompt: int, completion: int, total: int | None = None) -> None:
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.total_tokens = total if total is not None else prompt + completion


def _response(content: str = "ok", usage: _Usage | None = None) -> Any:
    message = SimpleNamespace(content=content, tool_calls=None)
    resp = SimpleNamespace(choices=[SimpleNamespace(message=message)])
    if usage is not None:
        resp.usage = usage
    return resp


class _Completions:
    def __init__(self, response: Any) -> None:
        self._response = response
        self.calls = 0

    def create(self, **kwargs: Any) -> Any:
        self.calls += 1
        return self._response


def _model(response: Any, *, debug: bool = False, log_file: str = "") -> OpenAILanguageModel:
    model = OpenAILanguageModel(
        model_name="test-model", api_key="test-key", debug=debug, log_file=log_file
    )
    cast(Any, model)._client = SimpleNamespace(
        chat=SimpleNamespace(completions=_Completions(response))
    )
    return model


# --------------------------------------------------------------- capture


def test_usage_accumulates_from_response(tmp_path) -> None:
    model = _model(_response(usage=_Usage(30, 12)))
    model.sample_text("hi")
    model.sample_text("hi again")
    totals = model.get_usage_counters("all")
    assert totals["prompt_tokens"] == 60
    assert totals["completion_tokens"] == 24
    assert totals["total_tokens"] == 84
    assert totals["calls_with_usage"] == 2
    assert totals["calls_without_usage"] == 0


def test_missing_usage_counts_without_crashing() -> None:
    model = _model(_response(usage=None))  # provider omitted usage
    assert model.sample_text("hi") == "ok"
    totals = model.get_usage_counters("all")
    assert totals["calls_with_usage"] == 0
    assert totals["calls_without_usage"] == 1
    assert totals["total_tokens"] == 0


def test_usage_attributed_per_phase_including_other() -> None:
    """Regression: the 'other' phase used to be dropped (no counter dict)."""
    model = _model(_response(usage=_Usage(10, 5)))
    for phase, prompt in (("probe", 100), ("action", 200), ("other", 300)):
        model.set_retry_phase(phase)
        model._client.chat.completions._response = _response(usage=_Usage(prompt, 1))  # type: ignore[attr-defined]
        model.sample_text("x")
    assert model.get_usage_counters("probe")["prompt_tokens"] == 100
    assert model.get_usage_counters("action")["prompt_tokens"] == 200
    assert model.get_usage_counters("other")["prompt_tokens"] == 300
    assert model.get_usage_counters("all")["prompt_tokens"] == 600


def test_memory_summarization_usage_attributed_to_active_phase() -> None:
    """Memory summarization model calls land in whatever retry phase is active when
    ``record()`` fires — NOT a stale one.

    Summarization runs on the record/observe side, so it can be triggered outside an
    agent turn (agent initialization seeding, ``broadcast_observation`` interventions),
    where the engine leaves the retry phase at its ``other`` default, or inside the
    engine's ``action`` bracket. Both must be tracked (never untracked), and neither
    must leak into the other's bucket. This locks in the phase model the
    ``SummarizingMemory._summarize`` comment documents.
    """
    from silisocs.agents.memory import SummarizingMemory

    model = _model(_response(usage=_Usage(7, 3)))

    # Outside a turn: the engine has not bracketed a phase, so it is at 'other'.
    model.set_retry_phase("other")
    mem_init = SummarizingMemory(model=cast(Any, model), max_memories=3, chunk_size=2)
    for i in range(4):  # 4 > max 3 -> exactly one summarization model call
        mem_init.record(f"init memory {i}")
    assert model.get_usage_counters("other")["calls_with_usage"] == 1
    assert model.get_usage_counters("other")["prompt_tokens"] == 7
    assert model.get_usage_counters("action")["calls_with_usage"] == 0

    # Inside a turn: the engine brackets the phase to 'action'.
    model.set_retry_phase("action")
    mem_turn = SummarizingMemory(model=cast(Any, model), max_memories=3, chunk_size=2)
    for i in range(4):
        mem_turn.record(f"turn memory {i}")
    assert model.get_usage_counters("action")["calls_with_usage"] == 1
    # The 'other' bucket is unchanged: no cross-phase leakage from the second batch.
    assert model.get_usage_counters("other")["calls_with_usage"] == 1


def test_usage_counters_thread_safe() -> None:
    model = _model(_response(usage=_Usage(1, 1)))

    def call() -> None:
        model.sample_text("x")

    threads = [threading.Thread(target=call) for _ in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    totals = model.get_usage_counters("all")
    assert totals["calls_with_usage"] == 40
    assert totals["prompt_tokens"] == 40 and totals["completion_tokens"] == 40


def test_log_records_per_call_tokens(tmp_path) -> None:
    log_file = str(tmp_path / "prompts.jsonl")
    model = _model(_response(usage=_Usage(7, 3)), debug=True, log_file=log_file)
    model.sample_text("hi")
    from silisocs.runtime.io import flush_jsonl_writers

    flush_jsonl_writers(timeout_s=5.0)
    row = json.loads((tmp_path / "prompts.jsonl").read_text().splitlines()[0])
    assert row["prompt_tokens"] == 7 and row["completion_tokens"] == 3


# --------------------------------------------------------------- aggregation


def test_collect_retry_telemetry_includes_tokens() -> None:
    model = _model(_response(usage=_Usage(50, 20)))
    model.set_retry_phase("action")
    model.sample_text("x")
    telemetry = collect_retry_telemetry([model], requested_workers=4, phase="action")
    assert telemetry["usage"]["prompt_tokens"] == 50
    assert telemetry["per_model"][0]["total_tokens"] == 70
    assert telemetry["per_model"][0]["completion_tokens"] == 20


def test_collect_usage_summary_with_and_without_pricing() -> None:
    model = _model(_response(usage=_Usage(1_000_000, 500_000)))
    model.sample_text("x")

    plain = collect_usage_summary([model])
    assert plain["pricing_applied"] is False
    assert "estimated_cost_usd" not in plain
    assert plain["totals"]["prompt_tokens"] == 1_000_000

    priced = collect_usage_summary([model], {"input_per_1m": 2.5, "output_per_1m": 10.0})
    assert priced["pricing_applied"] is True
    # 1M input * $2.5/M + 0.5M output * $10/M = 2.5 + 5.0
    assert priced["estimated_cost_usd"] == 7.5
    assert priced["per_model"][0]["estimated_cost_usd"] == 7.5


def test_collect_usage_summary_ignores_models_without_usage_api() -> None:
    class _Bare:
        _model_name = "bare"

    summary = collect_usage_summary([_Bare()])
    assert summary["per_model"][0]["total_tokens"] == 0
    assert summary["totals"]["total_tokens"] == 0


def test_collect_usage_summary_reports_by_phase() -> None:
    """The written summary splits probe/action/other so eval spend is separable."""
    model = _model(_response(usage=_Usage(10, 5)))
    for phase, prompt in (("probe", 1_000_000), ("action", 2_000_000)):
        model.set_retry_phase(phase)
        model._client.chat.completions._response = _response(usage=_Usage(prompt, 500_000))  # type: ignore[attr-defined]
        model.sample_text("x")

    summary = collect_usage_summary([model], {"input_per_1m": 1.0, "output_per_1m": 2.0})
    by_phase = summary["by_phase"]
    assert by_phase["probe"]["prompt_tokens"] == 1_000_000
    assert by_phase["action"]["prompt_tokens"] == 2_000_000
    assert by_phase["other"]["prompt_tokens"] == 0
    # 1M * $1/M + 0.5M * $2/M
    assert by_phase["probe"]["estimated_cost_usd"] == 2.0
    assert by_phase["action"]["estimated_cost_usd"] == 3.0
    assert summary["totals"]["prompt_tokens"] == 3_000_000


# --------------------------------------------------------------- probe phase bracketing


def test_probe_execution_attributed_to_probe_phase() -> None:
    """Probe LLM calls land in the 'probe' bucket, not the leftover 'other'.

    Drives the actual loop-strategy probe path (not a manual set_retry_phase),
    mirroring how scheduling brackets the action phase.
    """
    from types import SimpleNamespace

    from silisocs.simulation_engines.policies.loops import FixedStepsLoopStrategy

    model = _model(_response(usage=_Usage(11, 4)))
    phases_seen: list[str] = []

    class _ProbeRunner:
        def maybe_run(self, *, step, agents, worker_limit, agent_flows=None):
            del step, worker_limit, agent_flows
            phases_seen.append(model._retry_phase)
            model.sample_text("probe question")
            return True, len(agents)

    class _Recorder:
        def __init__(self) -> None:
            self.episodes: list[dict[str, Any]] = []

        def record_episode(self, **kwargs: Any) -> None:
            self.episodes.append(kwargs)

    recorder = _Recorder()
    engine = SimpleNamespace(
        probe_runner=_ProbeRunner(),
        interventions=None,
        recorder=recorder,
        run_step=lambda **kwargs: SimpleNamespace(probe_phase=None),
    )
    FixedStepsLoopStrategy().run(
        engine=engine,
        game_masters=[SimpleNamespace(model=model)],
        agents=[SimpleNamespace(model=model)],
        max_steps=1,
        start_step=0,
        verbose=False,
        checkpoint_callback=None,
    )
    assert phases_seen == ["probe"]
    assert model._retry_phase == "other"  # reset after the probe bracket
    assert model.get_usage_counters("probe")["prompt_tokens"] == 11
    assert model.get_usage_counters("action")["prompt_tokens"] == 0
    probe_phase = recorder.episodes[0]["step_result"].probe_phase
    assert probe_phase["deployed"] is True
    assert probe_phase["retry"]["calls"] == 1
    assert probe_phase["duration_s"] >= 0.0
