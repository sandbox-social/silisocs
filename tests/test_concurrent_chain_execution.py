"""Tests for multi-GM ``chain_execution`` modes (concurrent vs sequential).

The existing multi-GM tests list every flow in ``flow_order``, so they exercise the
serial-prefix path. These tests cover the concurrent path (unlisted flows), the
per-agent chain-order guarantee, flow_order precedence, and the sequential fallback.
"""

from __future__ import annotations

import threading

from omegaconf import OmegaConf

from silisocs.runtime.types import ActionOutput, ActionSpec, OutputType
from silisocs.simulation_engines.base_engines import MultiGMRuntimeEngine
from silisocs.simulation_engines.policies.steps import MultiGMStepStrategy


class _Agent:
    def __init__(self, name: str, *, barrier: threading.Barrier | None = None) -> None:
        self.name = name
        self._barrier = barrier
        self.observations: list[str] = []
        self.actions: list[str] = []

    def observe(self, observation: str) -> None:
        self.observations.append(observation)

    def act(self, action_spec: ActionSpec) -> ActionOutput:
        if self._barrier is not None:
            # Released only if a sibling flow runs act() concurrently; otherwise the
            # barrier times out and raises, surfacing as an isolated turn failure.
            self._barrier.wait()
        self.actions.append(action_spec.prompt)
        return ActionOutput.from_text(f"{self.name}:{action_spec.prompt}")


class _BoomAgent(_Agent):
    def act(self, action_spec: ActionSpec) -> ActionOutput:
        raise RuntimeError("boom")


class _GameMaster:
    def __init__(
        self,
        *,
        name: str,
        selected: list[str],
        agent_flow_tags: dict[str, str] | None = None,
        flow_chains: dict[str, list[str]] | None = None,
        log: list[str] | None = None,
    ) -> None:
        self.name = name
        self.selected = selected
        self.agent_flow_tags = agent_flow_tags or {}
        self.flow_chains = flow_chains or {}
        self.resolved: list[str] = []
        self.events: list[str] = []
        self._log = log

    def update(self, *, step: int, agents: list[_Agent], context: object | None = None) -> None:
        del context, agents
        self.events.append(f"update:{step}")

    def acting_agents(self, candidate_agents: list[_Agent]) -> list[str]:
        available = {agent.name for agent in candidate_agents}
        return [name for name in self.selected if name in available]

    def action_prompt(self, agent_name: str) -> ActionSpec:
        return ActionSpec(prompt=f"{self.name}:{agent_name}", output_type=OutputType.TEXT)

    def make_observation(self, agent_name: str) -> str:
        return f"obs:{self.name}:{agent_name}"

    def resolve_action(self, agent_name: str, action: ActionOutput) -> str:
        # The engine serializes resolve_action per GM via its per-GM lock, so the
        # per-GM resolved list needs no extra locking; the shared cross-GM log relies
        # on CPython's atomic list.append.
        self.resolved.append(agent_name)
        if self._log is not None:
            self._log.append(f"{self.name}:{agent_name}")
        return f"resolved:{self.name}:{agent_name}"


def _engine(
    *,
    chain_execution: str | None = None,
    flow_order: list[str] | None = None,
    max_concurrent: int | None = None,
) -> MultiGMRuntimeEngine:
    params: dict[str, object] = {}
    if flow_order is not None:
        params["flow_order"] = flow_order
    if chain_execution is not None:
        params["chain_execution"] = chain_execution
    sim: dict[str, object] = {
        "engine": {
            "turn_policy": {"built_in": "single_action"},
            "step": {"built_in": "multi_gm", "params": params},
        }
    }
    if max_concurrent is not None:
        sim["max_concurrent_actions"] = max_concurrent
    cfg = OmegaConf.create({"sim": sim})
    return MultiGMRuntimeEngine(config=cfg)


def test_chain_execution_defaults_to_concurrent() -> None:
    engine = _engine()
    assert isinstance(engine.step_strategy, MultiGMStepStrategy)
    assert engine.step_strategy.chain_execution == "concurrent"


def test_chain_execution_sequential_threads_from_config() -> None:
    engine = _engine(chain_execution="sequential")
    assert isinstance(engine.step_strategy, MultiGMStepStrategy)
    assert engine.step_strategy.chain_execution == "sequential"


def test_concurrent_mode_keeps_single_agent_chain_serial() -> None:
    # 'browse' is NOT in flow_order, so it runs through the concurrent path; a single
    # agent's chain hops must still execute in order (each hop observes the prior).
    engine = _engine(flow_order=["fixed_pre", "default"])
    alice = _Agent("Alice")
    primary = _GameMaster(
        name="primary",
        selected=[],
        agent_flow_tags={"Alice": "browse"},
        flow_chains={"browse": ["tw_gm", "rd_gm"]},
    )
    tw_gm = _GameMaster(name="tw_gm", selected=["Alice"])
    rd_gm = _GameMaster(name="rd_gm", selected=["Alice"])

    result = engine.run_step(
        step_index=0,
        game_masters=[primary, tw_gm, rd_gm],
        agents=[alice],
        verbose=False,
    )

    assert result.active_agent_names == ("Alice",)
    assert alice.actions == ["tw_gm:Alice", "rd_gm:Alice"]
    assert tw_gm.resolved == ["Alice"]
    assert rd_gm.resolved == ["Alice"]
    assert alice.observations == [
        "obs:tw_gm:Alice",
        "resolved:tw_gm:Alice",
        "obs:rd_gm:Alice",
        "resolved:rd_gm:Alice",
    ]


def test_concurrent_mode_runs_independent_flows_in_parallel() -> None:
    # Two unlisted flows on different GMs. Each agent's act() waits on a 2-party
    # barrier; it only releases if both turns run concurrently. If the scheduler
    # serialized them, the barrier would time out and the turns would fail.
    barrier = threading.Barrier(2, timeout=10)
    engine = _engine(flow_order=["fixed_pre", "default"])
    alice = _Agent("Alice", barrier=barrier)
    bob = _Agent("Bob", barrier=barrier)
    primary = _GameMaster(
        name="primary",
        selected=[],
        agent_flow_tags={"Alice": "flow_a", "Bob": "flow_b"},
        flow_chains={"flow_a": ["gm_a"], "flow_b": ["gm_b"]},
    )
    gm_a = _GameMaster(name="gm_a", selected=["Alice"])
    gm_b = _GameMaster(name="gm_b", selected=["Bob"])

    result = engine.run_step(
        step_index=0,
        game_masters=[primary, gm_a, gm_b],
        agents=[alice, bob],
        verbose=False,
    )

    # No failed turns proves both flows reached the barrier simultaneously.
    assert result.failed_turns == ()
    assert result.active_agent_names == ("Alice", "Bob")
    assert gm_a.resolved == ["Alice"]
    assert gm_b.resolved == ["Bob"]


def test_concurrent_mode_runs_flow_order_prefix_before_concurrent_flows() -> None:
    # 'seed' is listed in flow_order (serial prefix); 'main' is not (concurrent). Both
    # act in the same shared GM, so flow_order precedence (seed-then-act) must hold.
    engine = _engine(flow_order=["seed"])
    seed = _Agent("Seed")
    main = _Agent("Main")
    primary = _GameMaster(
        name="primary",
        selected=[],
        agent_flow_tags={"Seed": "seed", "Main": "main"},
        flow_chains={"seed": ["board"], "main": ["board"]},
    )
    board = _GameMaster(name="board", selected=["Seed", "Main"])

    engine.run_step(
        step_index=0,
        game_masters=[primary, board],
        agents=[seed, main],
        verbose=False,
    )

    assert board.resolved == ["Seed", "Main"]


def test_sequential_mode_runs_flows_serially_in_flow_major_order() -> None:
    # Legacy row-major: each flow runs its full chain before the next flow starts.
    log: list[str] = []
    engine = _engine(chain_execution="sequential", flow_order=["flow_a", "flow_b"])
    alice = _Agent("Alice")
    bob = _Agent("Bob")
    primary = _GameMaster(
        name="primary",
        selected=[],
        agent_flow_tags={"Alice": "flow_a", "Bob": "flow_b"},
        flow_chains={"flow_a": ["g1", "g2"], "flow_b": ["g1", "g2"]},
    )
    g1 = _GameMaster(name="g1", selected=["Alice", "Bob"], log=log)
    g2 = _GameMaster(name="g2", selected=["Alice", "Bob"], log=log)

    engine.run_step(
        step_index=0,
        game_masters=[primary, g1, g2],
        agents=[alice, bob],
        verbose=False,
    )

    assert log == ["g1:Alice", "g2:Alice", "g1:Bob", "g2:Bob"]


def test_concurrent_mode_recognizes_whitespace_padded_flow_order() -> None:
    # A whitespace-padded flow_order entry must still be treated as a serial-prefix
    # flow (matching _order_flows' stripping), preserving seed-then-act precedence.
    engine = _engine(flow_order=[" seed"])
    seed = _Agent("Seed")
    main = _Agent("Main")
    primary = _GameMaster(
        name="primary",
        selected=[],
        agent_flow_tags={"Seed": "seed", "Main": "main"},
        flow_chains={"seed": ["board"], "main": ["board"]},
    )
    board = _GameMaster(name="board", selected=["Seed", "Main"])

    engine.run_step(
        step_index=0,
        game_masters=[primary, board],
        agents=[seed, main],
        verbose=False,
    )

    assert board.resolved == ["Seed", "Main"]


def _all_inactive_step(chain_execution: str):
    # A flow that HAS an agent but whose GM selects nobody this step: batches exist
    # with zero turns. Returns the StepResult for the given mode.
    engine = _engine(chain_execution=chain_execution, flow_order=["fixed_pre", "default"])
    alice = _Agent("Alice")
    primary = _GameMaster(
        name="primary",
        selected=[],
        agent_flow_tags={"Alice": "browse"},
        flow_chains={"browse": ["tw_gm"]},
    )
    tw_gm = _GameMaster(name="tw_gm", selected=[])  # selects nobody this step
    return engine.run_step(
        step_index=0,
        game_masters=[primary, tw_gm],
        agents=[alice],
        verbose=False,
    )


def test_concurrent_and_sequential_agree_on_all_inactive_step() -> None:
    # Telemetry parity: a step with batches but zero selected turns must produce the
    # same envelope shape in both modes (not an empty StepResult for concurrent).
    concurrent = _all_inactive_step("concurrent")
    sequential = _all_inactive_step("sequential")

    for result in (concurrent, sequential):
        assert result.skipped is True
        assert result.primary_game_master == "tw_gm"
        assert result.requested_workers == 1
        assert result.worker_limit >= 1
        assert "failed_turns" in result.action_phase

    assert concurrent.primary_game_master == sequential.primary_game_master
    assert concurrent.requested_workers == sequential.requested_workers


def test_concurrent_mode_runs_more_flows_than_drivers() -> None:
    # 12 distinct unlisted flows but a worker cap of 2 -> the driver pool is bounded
    # to 2 yet every flow must still complete its turn.
    n = 12
    engine = _engine(flow_order=["fixed_pre", "default"], max_concurrent=2)
    agents = [_Agent(f"A{i}") for i in range(n)]
    primary = _GameMaster(
        name="primary",
        selected=[],
        agent_flow_tags={f"A{i}": f"flow{i}" for i in range(n)},
        flow_chains={f"flow{i}": [f"gm{i}"] for i in range(n)},
    )
    gms = [_GameMaster(name=f"gm{i}", selected=[f"A{i}"]) for i in range(n)]

    result = engine.run_step(
        step_index=0,
        game_masters=[primary, *gms],
        agents=list(agents),
        verbose=False,
    )

    assert result.failed_turns == ()
    assert result.worker_limit == 2
    assert result.active_agent_names == tuple(sorted(f"A{i}" for i in range(n)))
    for i in range(n):
        assert gms[i].resolved == [f"A{i}"]


def test_concurrent_mode_isolates_a_failing_chain() -> None:
    # A raising turn in one concurrent flow must not abort sibling flows.
    engine = _engine(flow_order=["fixed_pre", "default"])
    boom = _BoomAgent("Boom")
    ok = _Agent("Ok")
    primary = _GameMaster(
        name="primary",
        selected=[],
        agent_flow_tags={"Boom": "flow_boom", "Ok": "flow_ok"},
        flow_chains={"flow_boom": ["boom_gm"], "flow_ok": ["ok_gm"]},
    )
    boom_gm = _GameMaster(name="boom_gm", selected=["Boom"])
    ok_gm = _GameMaster(name="ok_gm", selected=["Ok"])

    result = engine.run_step(
        step_index=0,
        game_masters=[primary, boom_gm, ok_gm],
        agents=[boom, ok],
        verbose=False,
    )

    assert ok_gm.resolved == ["Ok"]
    assert boom_gm.resolved == []
    assert result.failed_turns == ("boom_gm::Boom",)
    assert result.degraded is True
