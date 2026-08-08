"""Phase 2 (async slice) tests: the asyncio turn executor and the additive
``act_async`` / ``sample_*_async`` seams.

Covers: LanguageModel default async wrappers, ContextLocal task isolation,
Agent.act_async defaults and overrides, turn-policy sync/async parity, the
engine's asyncio executor end-to-end with a MIXED sync/async roster, per-GM
concurrency caps under asyncio, failure isolation, and the OpenAI provider's
native async client + retry loop.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from silisocs.agents.base_agent import Agent
from silisocs.agents.native import NativeAgent
from silisocs.runtime.language_models.base import (
    ContextLocal,
    LanguageModel,
    NoLanguageModel,
)
from silisocs.runtime.language_models.openai import OpenAILanguageModel
from silisocs.runtime.telemetry import SimMetricsCollector
from silisocs.runtime.types import ActionOutput, ActionSpec
from silisocs.simulation_engines.base_engines import RuntimeEngine
from silisocs.simulation_engines.policies.turns import (
    FixedCountTurnPolicy,
    OpenEndedTurnPolicy,
    SingleActionTurnPolicy,
)
from silisocs.simulation_engines.runtime_base import AgentStepResult

LOOP_THREAD_NAME = "silisocs-async-turns"

# ---------------------------------------------------------------- test doubles


class _GM:
    """Minimal game master satisfying the engine turn/step contract."""

    name = "gm"

    def __init__(self) -> None:
        self.resolved: list[str] = []

    def update(self, *, step: int, agents: list[Any], context: Any | None = None) -> None:
        del step, agents, context

    def acting_agents(self, candidates: list[Any]) -> list[str]:
        return [agent.name for agent in candidates]

    def action_prompt(self, name: str) -> ActionSpec:
        return ActionSpec(prompt=f"act {name}")

    def make_observation(self, name: str) -> str:
        return f"observation for {name}"

    def resolve_action(self, name: str, output: Any) -> str:
        self.resolved.append(name)
        return f"resolved {name}"


class _SyncAgent(Agent):
    """Sync-only agent: relies on the default thread-hopping act_async."""

    def __init__(self, name: str) -> None:
        super().__init__(NoLanguageModel())
        self._name = name
        self.act_thread: str | None = None

    @property
    def name(self) -> str:
        return self._name

    def observe(self, observation: str) -> None:
        del observation

    def act(self, action_spec: Any) -> ActionOutput:
        del action_spec
        self.act_thread = threading.current_thread().name
        return ActionOutput.from_text(f"action by {self._name}")


class _AsyncAgent(Agent):
    """Async-native agent: acts on the event loop; its sync act must NOT run."""

    def __init__(self, name: str) -> None:
        super().__init__(NoLanguageModel())
        self._name = name
        self.act_thread: str | None = None

    @property
    def name(self) -> str:
        return self._name

    def observe(self, observation: str) -> None:
        del observation

    def act(self, action_spec: Any) -> ActionOutput:
        raise AssertionError("sync act must not be called for an async-native agent")

    async def act_async(self, action_spec: Any) -> ActionOutput:
        del action_spec
        self.act_thread = threading.current_thread().name
        await asyncio.sleep(0)
        return ActionOutput.from_text(f"async action by {self._name}")


def _run_one_step(engine: RuntimeEngine, gm: _GM, agents: list[Any]) -> Any:
    try:
        return engine.run_step(step_index=0, game_masters=[gm], agents=agents, verbose=False)
    finally:
        engine._shutdown_loop_runner()


# ------------------------------------------------- LanguageModel async defaults


class _EchoModel(LanguageModel):
    def sample_text(self, prompt: str, **kwargs: Any) -> str:
        del kwargs
        return f"echo:{prompt}"


def test_language_model_async_defaults_delegate_to_sync() -> None:
    model = _EchoModel()
    assert asyncio.run(model.sample_text_async("hi")) == "echo:hi"
    assert asyncio.run(NoLanguageModel().sample_float_async("1.5")) == 0.0


def test_no_language_model_works_through_async_defaults() -> None:
    model = NoLanguageModel()
    assert asyncio.run(model.sample_choice_async("q", ["a", "b"])) == (0, "a", {})
    assert asyncio.run(model.sample_tool_calls_async("q", [{"n": 1}])) == []
    assert asyncio.run(model.sample_structured_async("q", {"type": "object"})) == {}


# --------------------------------------------------------------- ContextLocal


def test_context_local_isolates_concurrent_tasks() -> None:
    local = ContextLocal()

    async def worker(tag: str) -> str:
        local.agent_name = tag
        await asyncio.sleep(0.01)
        return str(local.agent_name)

    async def main() -> list[str]:
        return list(await asyncio.gather(*(worker(f"agent-{i}") for i in range(8))))

    assert asyncio.run(main()) == [f"agent-{i}" for i in range(8)]


def test_context_local_supports_hasattr_and_delattr() -> None:
    local = ContextLocal()
    assert not hasattr(local, "phase")
    local.phase = "action"
    assert local.phase == "action"
    delattr(local, "phase")
    assert not hasattr(local, "phase")


def test_context_local_isolates_threads() -> None:
    local = ContextLocal()
    local.tag = "main"
    seen: list[bool] = []

    def other() -> None:
        seen.append(hasattr(local, "tag"))

    thread = threading.Thread(target=other)
    thread.start()
    thread.join()
    assert seen == [False]
    assert local.tag == "main"


# ------------------------------------------------------------ Agent async seam


def test_default_act_async_runs_sync_act_off_the_loop() -> None:
    agent = _SyncAgent("s")

    async def main() -> ActionOutput:
        return await agent.act_async(ActionSpec(prompt="go"))

    out = asyncio.run(main())
    assert str(out) == "action by s"
    assert agent.act_thread is not None
    assert agent.act_thread != threading.current_thread().name


class _RecordingModel(LanguageModel):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def sample_text(self, prompt: str, **kwargs: Any) -> str:
        del prompt, kwargs
        self.calls.append("sync")
        return "sync text"

    async def sample_text_async(self, prompt: str, **kwargs: Any) -> str:
        del prompt, kwargs
        self.calls.append("async")
        return "async text"


def test_native_agent_act_async_uses_model_async_path() -> None:
    model = _RecordingModel()
    agent = NativeAgent(name="n", model=model)
    out = asyncio.run(agent.act_async(ActionSpec(prompt="go")))
    assert str(out) == "async text"
    assert model.calls == ["async"]
    assert agent.get_last_log()["action_attempt"] == "async text"


def test_native_agent_sync_act_still_uses_sync_path() -> None:
    model = _RecordingModel()
    agent = NativeAgent(name="n", model=model)
    out = agent.act(ActionSpec(prompt="go"))
    assert str(out) == "sync text"
    assert model.calls == ["sync"]


class _SyncOnlyDuckModel:
    """Duck-typed model without any *_async twins (not a LanguageModel)."""

    def sample_text(self, prompt: str, **kwargs: Any) -> str:
        del prompt, kwargs
        return "duck text"


def test_call_model_async_falls_back_to_thread_for_duck_typed_model() -> None:
    agent = NativeAgent(name="n", model=_SyncOnlyDuckModel())  # type: ignore[arg-type]
    out = asyncio.run(agent.act_async(ActionSpec(prompt="go")))
    assert str(out) == "duck text"


# ------------------------------------------------------ turn policy sync/async


class _StubTurnEngine:
    """Records run_agent_step calls; async twin routes through the sync one."""

    def __init__(self, results: list[AgentStepResult]) -> None:
        self._results = list(results)
        self.observe_flags: list[bool] = []

    def run_agent_step(
        self,
        *,
        game_master: Any,
        agent: Any,
        action_spec: Any,
        verbose: bool,
        observe_before_action: bool = True,
    ) -> AgentStepResult:
        del game_master, agent, action_spec, verbose
        self.observe_flags.append(observe_before_action)
        return self._results.pop(0)

    async def run_agent_step_async(self, **kwargs: Any) -> AgentStepResult:
        return self.run_agent_step(**kwargs)


def _step_result(rendered: str, resolved: str = "") -> AgentStepResult:
    return AgentStepResult(
        agent_name="a",
        rendered_action=rendered,
        raw_action=ActionOutput.from_text(rendered),
        resolved_result=resolved,
    )


@pytest.mark.parametrize(
    "policy",
    [
        SingleActionTurnPolicy(),
        FixedCountTurnPolicy(count=2),
        OpenEndedTurnPolicy(max_actions=3),
    ],
)
def test_turn_policy_run_and_run_async_are_equivalent(policy: Any) -> None:
    results = [_step_result("ACTION: POST"), _step_result("ACTION: LIKE"), _step_result("")]
    sync_engine = _StubTurnEngine(list(results))
    async_engine = _StubTurnEngine(list(results))

    sync_out = policy.run(
        engine=sync_engine, game_master=None, agent=None, action_spec=None, verbose=False
    )

    async def main() -> Any:
        return await policy.run_async(
            engine=async_engine, game_master=None, agent=None, action_spec=None, verbose=False
        )

    async_out = asyncio.run(main())
    assert sync_out == async_out
    assert sync_engine.observe_flags == async_engine.observe_flags


def test_open_ended_run_async_stops_on_finished_signal() -> None:
    engine = _StubTurnEngine(
        [
            _step_result("ACTION: POST"),
            _step_result("ACTION: POST", resolved="FINISHED: done"),
            _step_result("ACTION: POST"),
        ]
    )
    policy = OpenEndedTurnPolicy(max_actions=5)

    async def main() -> Any:
        return await policy.run_async(
            engine=engine, game_master=None, agent=None, action_spec=None, verbose=False
        )

    asyncio.run(main())
    # First turn observes, later turns don't (observe_before_act="first"); the
    # FINISHED resolution on turn two ends the episode before turn three.
    assert engine.observe_flags == [True, False]


# --------------------------------------------------- engine asyncio executor


def test_engine_rejects_unknown_executor() -> None:
    with pytest.raises(ValueError, match="sim.engine.executor"):
        RuntimeEngine(executor="bogus")


def test_engine_defaults_to_threads_executor() -> None:
    assert RuntimeEngine()._async_turns is False


def test_asyncio_executor_runs_mixed_sync_async_roster() -> None:
    engine = RuntimeEngine(executor="asyncio")
    gm = _GM()
    async_agents = [_AsyncAgent("a1"), _AsyncAgent("a2")]
    sync_agent = _SyncAgent("s1")
    result = _run_one_step(engine, gm, [*async_agents, sync_agent])

    assert sorted(gm.resolved) == ["a1", "a2", "s1"]
    assert result.action_phase["active_agents"] == 3
    assert result.failed_turns == ()
    # Async-native agents acted on the event loop; the sync agent acted on a
    # helper thread — never on the loop, never blocking it.
    for agent in async_agents:
        assert agent.act_thread == LOOP_THREAD_NAME
    assert sync_agent.act_thread is not None
    assert sync_agent.act_thread != LOOP_THREAD_NAME


def test_asyncio_executor_matches_threads_executor_results() -> None:
    def run(executor: str) -> list[str]:
        gm = _GM()
        agents: list[Any] = (
            [_AsyncAgent("a1"), _SyncAgent("s1")]
            if executor == "asyncio"
            else [
                _SyncAgent("a1"),
                _SyncAgent("s1"),
            ]
        )
        _run_one_step(RuntimeEngine(executor=executor), gm, agents)
        return sorted(gm.resolved)

    assert run("asyncio") == run("threads") == ["a1", "s1"]


class _ConcurrencyProbeAgent(Agent):
    """Records the peak number of concurrently in-flight act_async calls."""

    in_flight = 0
    peak = 0
    _guard = threading.Lock()

    def __init__(self, name: str) -> None:
        super().__init__(NoLanguageModel())
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def observe(self, observation: str) -> None:
        del observation

    def act(self, action_spec: Any) -> ActionOutput:
        raise AssertionError("async path expected")

    async def act_async(self, action_spec: Any) -> ActionOutput:
        del action_spec
        cls = _ConcurrencyProbeAgent
        cls.in_flight += 1  # single loop thread: no race
        cls.peak = max(cls.peak, cls.in_flight)
        await asyncio.sleep(0.02)
        cls.in_flight -= 1
        return ActionOutput.from_text(f"action by {self._name}")


def test_asyncio_executor_overlaps_async_turns() -> None:
    _ConcurrencyProbeAgent.in_flight = 0
    _ConcurrencyProbeAgent.peak = 0
    engine = RuntimeEngine(executor="asyncio")
    gm = _GM()
    _run_one_step(engine, gm, [_ConcurrencyProbeAgent(f"a{i}") for i in range(6)])
    assert len(gm.resolved) == 6
    assert _ConcurrencyProbeAgent.peak > 1


def test_asyncio_executor_honors_per_gm_concurrency_cap() -> None:
    _ConcurrencyProbeAgent.in_flight = 0
    _ConcurrencyProbeAgent.peak = 0
    engine = RuntimeEngine(executor="asyncio", gm_concurrency_caps={"gm": 1})
    gm = _GM()
    _run_one_step(engine, gm, [_ConcurrencyProbeAgent(f"a{i}") for i in range(4)])
    assert len(gm.resolved) == 4
    assert _ConcurrencyProbeAgent.peak == 1


class _FailingAsyncAgent(_AsyncAgent):
    async def act_async(self, action_spec: Any) -> ActionOutput:
        del action_spec
        raise RuntimeError("turn blew up")


def test_asyncio_executor_isolates_failing_turn() -> None:
    SimMetricsCollector.reset()
    engine = RuntimeEngine(executor="asyncio")
    gm = _GM()
    result = _run_one_step(engine, gm, [_AsyncAgent("ok"), _FailingAsyncAgent("bad")])
    assert gm.resolved == ["ok"]
    assert result.failed_turns == ("gm::bad",)
    assert SimMetricsCollector.get().counter("agent_turn_failures") == 1


class _DuckAgent:
    """Duck-typed agent (no Agent ABC, no act_async): still works under asyncio."""

    name = "duck"

    def observe(self, observation: str) -> None:
        del observation

    def act(self, action_spec: Any) -> str:
        del action_spec
        return "duck action"


def test_run_agent_step_async_handles_duck_typed_sync_agent() -> None:
    engine = RuntimeEngine(executor="asyncio")
    gm = _GM()

    async def main() -> Any:
        return await engine.run_agent_step_async(
            game_master=gm, agent=_DuckAgent(), action_spec=ActionSpec(prompt="go"), verbose=False
        )

    result = asyncio.run(main())
    assert result.rendered_action == "duck action"
    assert gm.resolved == ["duck"]


def test_loop_runner_shuts_down_and_restarts() -> None:
    def alive(runner: Any) -> bool:
        return bool(runner.alive)

    engine = RuntimeEngine(executor="asyncio")
    first = engine._ensure_loop_runner()
    assert alive(first)
    engine._shutdown_loop_runner()
    assert not alive(first)
    second = engine._ensure_loop_runner()
    assert second is not first and alive(second)
    engine._shutdown_loop_runner()


def test_ensure_loop_runner_is_thread_safe() -> None:
    """Concurrent drivers (the multi_gm path) must build exactly ONE event loop.

    Without the double-checked guard, N threads racing on a fresh engine each
    construct their own EventLoopThread — the shared _async_gate then binds to
    one loop and turns on the others raise 'bound to a different event loop'.
    """
    engine = RuntimeEngine(executor="asyncio")
    n = 16
    barrier = threading.Barrier(n)
    seen: list[int] = []
    guard = threading.Lock()

    def worker() -> None:
        barrier.wait()
        runner = engine._ensure_loop_runner()
        with guard:
            seen.append(id(runner))

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    try:
        assert len(set(seen)) == 1  # every thread got the same single runner
    finally:
        engine._shutdown_loop_runner()


def test_concurrent_multi_gm_resolves_all_turns_under_asyncio() -> None:
    """The concurrent multi_gm path fans out over driver threads; under asyncio
    every flow's turns must still resolve with no cross-loop failures.
    """
    from omegaconf import OmegaConf

    from silisocs.runtime.construction.engines import build_engine

    cfg = OmegaConf.create(
        {
            "sim": {
                "engine": {
                    "executor": "asyncio",
                    "turn_policy": {"built_in": "single_action"},
                    "step": {"built_in": "multi_gm", "params": {"flow_order": []}},
                }
            }
        }
    )
    engine = build_engine(cfg, flow_chains={"fa": ["gm_a"], "fb": ["gm_b"]})
    alice, bob = _AsyncAgent("Alice"), _AsyncAgent("Bob")
    primary = _MultiGM(name="primary", selected=[], tags={"Alice": "fa", "Bob": "fb"})
    gm_a = _MultiGM(name="gm_a", selected=["Alice"])
    gm_b = _MultiGM(name="gm_b", selected=["Bob"])
    try:
        result = engine.run_step(
            step_index=0, game_masters=[primary, gm_a, gm_b], agents=[alice, bob], verbose=False
        )
    finally:
        engine._shutdown_loop_runner()
    assert result.failed_turns == ()
    assert gm_a.resolved == ["Alice"]
    assert gm_b.resolved == ["Bob"]


class _MultiGM:
    """Multi-GM-shaped game master (name-selected acting) for chain tests."""

    def __init__(
        self, *, name: str, selected: list[str], tags: dict[str, str] | None = None
    ) -> None:
        self.name = name
        self.selected = selected
        self.agent_flow_tags = tags or {}
        self.resolved: list[str] = []

    def update(self, *, step: int, agents: list[Any], context: Any | None = None) -> None:
        del step, agents, context

    def acting_agents(self, candidates: list[Any]) -> list[str]:
        available = {a.name for a in candidates}
        return [n for n in self.selected if n in available]

    def action_prompt(self, name: str) -> ActionSpec:
        return ActionSpec(prompt=f"{self.name}:{name}")

    def make_observation(self, name: str) -> str:
        return f"obs:{self.name}:{name}"

    def resolve_action(self, name: str, output: Any) -> str:
        self.resolved.append(name)
        return f"resolved:{self.name}:{name}"


# ------------------------------------------------------- OpenAI provider async


class _FakeAsyncCompletions:
    def __init__(self, content: str) -> None:
        self._content = content
        self.calls = 0

    async def create(self, **kwargs: Any) -> Any:
        del kwargs
        self.calls += 1
        message = type("Msg", (), {"content": self._content, "tool_calls": None})()
        choice = type("Choice", (), {"message": message})()
        return type("Resp", (), {"choices": [choice]})()


def _openai_model() -> OpenAILanguageModel:
    return OpenAILanguageModel(model_name="test-model", api_key="test-key", debug=False)


def test_openai_async_client_is_lazy() -> None:
    model = _openai_model()
    assert model._async_client is None


def _fake_client(content: str) -> tuple[Any, _FakeAsyncCompletions]:
    completions = _FakeAsyncCompletions(content)
    client = type("Client", (), {"chat": type("Chat", (), {"completions": completions})()})()
    return client, completions


def test_openai_sample_text_async_uses_async_client() -> None:
    model = _openai_model()
    client, completions = _fake_client("hello from async")
    model._get_async_client = lambda: client  # type: ignore[method-assign]
    assert asyncio.run(model.sample_text_async("hi")) == "hello from async"
    assert completions.calls == 1
    assert model.get_retry_counters()["calls_total"] == 1


def test_openai_async_client_rebinds_when_loop_changes() -> None:
    """The cached AsyncOpenAI client is keyed by its event loop: a second run on a
    fresh loop must rebuild it, not reuse a client bound to the closed loop.
    """
    model = _openai_model()
    built: list[Any] = []

    class _FakeAsyncOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            del kwargs
            built.append(self)

    import silisocs.runtime.language_models.openai as openai_mod

    orig = openai_mod.openai.AsyncOpenAI
    openai_mod.openai.AsyncOpenAI = _FakeAsyncOpenAI  # type: ignore[misc,assignment]
    try:

        async def get_client() -> tuple[Any, Any]:
            first = model._get_async_client()
            second = model._get_async_client()  # same loop -> cached, no rebuild
            return first, second

        first, second = asyncio.run(get_client())  # loop #1
        assert first is second and len(built) == 1
        third, _ = asyncio.run(get_client())  # loop #2 (fresh) -> rebuild
        assert third is not first and len(built) == 2
    finally:
        openai_mod.openai.AsyncOpenAI = orig  # type: ignore[misc]


def test_openai_sample_choice_async_escalates_to_match() -> None:
    model = _openai_model()
    samples = iter(["not a choice", "b"])

    async def fake_sample_text_async(prompt: str, **kwargs: Any) -> str:
        del prompt, kwargs
        return next(samples)

    model.sample_text_async = fake_sample_text_async  # type: ignore[method-assign]
    idx, choice, _ = asyncio.run(model.sample_choice_async("pick", ["a", "b"]))
    assert (idx, choice) == (1, "b")


def test_openai_retry_request_async_backs_off_then_succeeds() -> None:
    model = _openai_model()
    model._backoff_base_seconds = 0.0
    model._backoff_max_seconds = 0.0
    attempts: list[int] = []

    async def attempt(i: int) -> str:
        attempts.append(i)
        if i < 2:
            raise RuntimeError("transient")
        model._record_retry_outcome(i, success=True)
        return "ok"

    out = asyncio.run(model._retry_request_async(attempt, label="test", catch_all=True))
    assert out == "ok"
    assert attempts == [0, 1, 2]


def test_openai_retry_request_async_raises_after_exhaustion() -> None:
    model = _openai_model()
    model._max_retries = 2
    model._backoff_base_seconds = 0.0
    model._backoff_max_seconds = 0.0

    async def attempt(i: int) -> str:
        del i
        raise RuntimeError("permanent")

    with pytest.raises(RuntimeError, match="failed after bounded retries"):
        asyncio.run(model._retry_request_async(attempt, label="test", catch_all=True))


def test_openai_runtime_context_isolated_per_task() -> None:
    """set_runtime_context from interleaved tasks must not clobber each other."""
    model = _openai_model()

    async def worker(tag: str) -> str:
        model.set_runtime_context(agent_name=tag)
        await asyncio.sleep(0.01)
        try:
            return str(model._local.agent_name)
        finally:
            model.clear_runtime_context()

    async def main() -> list[str]:
        return list(await asyncio.gather(*(worker(f"agent-{i}") for i in range(6))))

    assert asyncio.run(main()) == [f"agent-{i}" for i in range(6)]
