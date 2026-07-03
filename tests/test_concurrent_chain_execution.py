"""Tests for the multi-GM traversal step strategies.

Each traversal is its own step strategy (``multi_gm`` concurrent default,
``multi_gm_serial`` legacy row-major, ``multi_gm_staged`` global per-stage barrier),
replacing the retired ``chain_execution`` knob. These cover the concurrent path
(unlisted flows), the per-agent chain-order guarantee, flow_order precedence, the
serial fallback, and the staged barrier + empty-slot behavior.
"""

from __future__ import annotations

import threading

import pytest
from omegaconf import OmegaConf

from silisocs.runtime.construction.engines import build_engine
from silisocs.runtime.types import ActionOutput, ActionSpec, OutputType
from silisocs.simulation_engines.policies.participation import ParticipationPolicy
from silisocs.simulation_engines.policies.steps import (
    MultiGMSerialStepStrategy,
    MultiGMStagedStepStrategy,
    MultiGMStepStrategy,
)


class _KeepOnly(ParticipationPolicy):
    """Participation policy that keeps only a fixed set of agents (test double)."""

    name = "keep_only"

    def __init__(self, keep: list[str] | None = None) -> None:
        self._keep = set(keep or [])

    def participating_agents(
        self, *, agent_names: list[str], step_index: int, seed: int
    ) -> list[str]:
        del step_index, seed
        return [name for name in agent_names if name in self._keep]


_BUILT_IN_BY_MODE = {
    "concurrent": "multi_gm",
    "serial": "multi_gm_serial",
    "staged": "multi_gm_staged",
}


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
        log: list[str] | None = None,
    ) -> None:
        self.name = name
        self.selected = selected
        self.agent_flow_tags = agent_flow_tags or {}
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
    mode: str = "concurrent",
    flow_order: list[str] | None = None,
    max_concurrent: int | None = None,
    flow_chains: dict[str, list[str | None]] | None = None,
):
    params: dict[str, object] = {}
    if flow_order is not None:
        params["flow_order"] = flow_order
    sim: dict[str, object] = {
        "engine": {
            "turn_policy": {"built_in": "single_action"},
            "step": {"built_in": _BUILT_IN_BY_MODE[mode], "params": params},
        }
    }
    if max_concurrent is not None:
        sim["max_concurrent_actions"] = max_concurrent
    cfg = OmegaConf.create({"sim": sim})
    return build_engine(cfg, flow_chains=flow_chains)


def test_multi_gm_default_is_the_concurrent_strategy() -> None:
    engine = _engine()
    assert type(engine.step_strategy) is MultiGMStepStrategy
    assert engine.step_strategy.name == "multi_gm"


def test_multi_gm_serial_selects_serial_strategy() -> None:
    engine = _engine(mode="serial")
    assert isinstance(engine.step_strategy, MultiGMSerialStepStrategy)
    assert engine.step_strategy.name == "multi_gm_serial"


def test_multi_gm_staged_selects_staged_strategy() -> None:
    engine = _engine(mode="staged")
    assert isinstance(engine.step_strategy, MultiGMStagedStepStrategy)
    assert engine.step_strategy.name == "multi_gm_staged"


def test_retired_chain_execution_param_raises_with_migration_hint() -> None:
    # The knob was replaced by dedicated step strategies; a stale config must fail
    # loudly rather than be silently ignored (which would change scheduling).
    cfg = OmegaConf.create(
        {
            "sim": {
                "engine": {
                    "step": {"built_in": "multi_gm", "params": {"chain_execution": "sequential"}}
                }
            }
        }
    )
    with pytest.raises(ValueError, match="chain_execution has been removed"):
        build_engine(cfg)


def test_concurrent_mode_keeps_single_agent_chain_serial() -> None:
    # 'browse' is NOT in flow_order, so it runs through the concurrent path; a single
    # agent's chain hops must still execute in order (each hop observes the prior).
    engine = _engine(
        flow_order=["fixed_pre", "default"], flow_chains={"browse": ["tw_gm", "rd_gm"]}
    )
    alice = _Agent("Alice")
    primary = _GameMaster(
        name="primary",
        selected=[],
        agent_flow_tags={"Alice": "browse"},
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
    engine = _engine(
        flow_order=["fixed_pre", "default"],
        flow_chains={"flow_a": ["gm_a"], "flow_b": ["gm_b"]},
    )
    alice = _Agent("Alice", barrier=barrier)
    bob = _Agent("Bob", barrier=barrier)
    primary = _GameMaster(
        name="primary",
        selected=[],
        agent_flow_tags={"Alice": "flow_a", "Bob": "flow_b"},
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
    engine = _engine(flow_order=["seed"], flow_chains={"seed": ["board"], "main": ["board"]})
    seed = _Agent("Seed")
    main = _Agent("Main")
    primary = _GameMaster(
        name="primary",
        selected=[],
        agent_flow_tags={"Seed": "seed", "Main": "main"},
    )
    board = _GameMaster(name="board", selected=["Seed", "Main"])

    engine.run_step(
        step_index=0,
        game_masters=[primary, board],
        agents=[seed, main],
        verbose=False,
    )

    assert board.resolved == ["Seed", "Main"]


def test_serial_mode_runs_flows_serially_in_flow_major_order() -> None:
    # Legacy row-major: each flow runs its full chain before the next flow starts.
    log: list[str] = []
    engine = _engine(
        mode="serial",
        flow_order=["flow_a", "flow_b"],
        flow_chains={"flow_a": ["g1", "g2"], "flow_b": ["g1", "g2"]},
    )
    alice = _Agent("Alice")
    bob = _Agent("Bob")
    primary = _GameMaster(
        name="primary",
        selected=[],
        agent_flow_tags={"Alice": "flow_a", "Bob": "flow_b"},
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
    engine = _engine(flow_order=[" seed"], flow_chains={"seed": ["board"], "main": ["board"]})
    seed = _Agent("Seed")
    main = _Agent("Main")
    primary = _GameMaster(
        name="primary",
        selected=[],
        agent_flow_tags={"Seed": "seed", "Main": "main"},
    )
    board = _GameMaster(name="board", selected=["Seed", "Main"])

    engine.run_step(
        step_index=0,
        game_masters=[primary, board],
        agents=[seed, main],
        verbose=False,
    )

    assert board.resolved == ["Seed", "Main"]


def _all_inactive_step(mode: str):
    # A flow that HAS an agent but whose GM selects nobody this step: batches exist
    # with zero turns. Returns the StepResult for the given mode.
    engine = _engine(
        mode=mode, flow_order=["fixed_pre", "default"], flow_chains={"browse": ["tw_gm"]}
    )
    alice = _Agent("Alice")
    primary = _GameMaster(
        name="primary",
        selected=[],
        agent_flow_tags={"Alice": "browse"},
    )
    tw_gm = _GameMaster(name="tw_gm", selected=[])  # selects nobody this step
    return engine.run_step(
        step_index=0,
        game_masters=[primary, tw_gm],
        agents=[alice],
        verbose=False,
    )


def test_all_traversals_agree_on_all_inactive_step() -> None:
    # Telemetry parity: a step with batches but zero selected turns must produce the
    # same envelope shape in every mode (not an empty StepResult for concurrent/staged).
    results = {mode: _all_inactive_step(mode) for mode in ("concurrent", "serial", "staged")}

    for result in results.values():
        assert result.skipped is True
        assert result.primary_game_master == "tw_gm"
        assert result.requested_workers == 1
        assert result.worker_limit >= 1
        assert "failed_turns" in result.action_phase

    assert {r.primary_game_master for r in results.values()} == {"tw_gm"}
    assert {r.requested_workers for r in results.values()} == {1}


def test_concurrent_mode_runs_more_flows_than_drivers() -> None:
    # 12 distinct unlisted flows but a worker cap of 2 -> the driver pool is bounded
    # to 2 yet every flow must still complete its turn.
    n = 12
    engine = _engine(
        flow_order=["fixed_pre", "default"],
        max_concurrent=2,
        flow_chains={f"flow{i}": [f"gm{i}"] for i in range(n)},
    )
    agents = [_Agent(f"A{i}") for i in range(n)]
    primary = _GameMaster(
        name="primary",
        selected=[],
        agent_flow_tags={f"A{i}": f"flow{i}" for i in range(n)},
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
    engine = _engine(
        flow_order=["fixed_pre", "default"],
        flow_chains={"flow_boom": ["boom_gm"], "flow_ok": ["ok_gm"]},
    )
    boom = _BoomAgent("Boom")
    ok = _Agent("Ok")
    primary = _GameMaster(
        name="primary",
        selected=[],
        agent_flow_tags={"Boom": "flow_boom", "Ok": "flow_ok"},
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


def test_staged_mode_barriers_between_stages() -> None:
    # Two unlisted flows, each with chain [g1, g2]. The staged barrier means EVERY
    # flow's stage-0 hop (on g1) finishes before ANY flow's stage-1 hop (on g2)
    # starts -- so both g1 resolves precede both g2 resolves in the shared log.
    log: list[str] = []
    engine = _engine(
        mode="staged",
        flow_order=["fixed_pre", "default"],
        flow_chains={"flow_a": ["g1", "g2"], "flow_b": ["g1", "g2"]},
    )
    alice = _Agent("Alice")
    bob = _Agent("Bob")
    primary = _GameMaster(
        name="primary",
        selected=[],
        agent_flow_tags={"Alice": "flow_a", "Bob": "flow_b"},
    )
    g1 = _GameMaster(name="g1", selected=["Alice", "Bob"], log=log)
    g2 = _GameMaster(name="g2", selected=["Alice", "Bob"], log=log)

    engine.run_step(
        step_index=0,
        game_masters=[primary, g1, g2],
        agents=[alice, bob],
        verbose=False,
    )

    # Within a stage the order is nondeterministic, but the barrier fixes the split.
    assert set(log[:2]) == {"g1:Alice", "g1:Bob"}
    assert set(log[2:]) == {"g2:Alice", "g2:Bob"}


def test_staged_mode_respects_participation_filter() -> None:
    # Participation removes Bob from the roster BEFORE staged scheduling, so only
    # Alice runs her chain [g1, g2]; the staged barrier still splits g1 before g2.
    log: list[str] = []
    cfg = OmegaConf.create(
        {
            "sim": {
                "engine": {
                    "turn_policy": {"built_in": "single_action"},
                    "step": {
                        "built_in": "multi_gm_staged",
                        "params": {"flow_order": ["fixed_pre", "default"]},
                    },
                    "participation": {
                        "class_path": f"{__name__}._KeepOnly",
                        "params": {"keep": ["Alice"]},
                    },
                }
            }
        }
    )
    engine = build_engine(cfg, flow_chains={"flow_a": ["g1", "g2"], "flow_b": ["g1", "g2"]})
    alice = _Agent("Alice")
    bob = _Agent("Bob")
    primary = _GameMaster(
        name="primary",
        selected=[],
        agent_flow_tags={"Alice": "flow_a", "Bob": "flow_b"},
    )
    g1 = _GameMaster(name="g1", selected=["Alice", "Bob"], log=log)
    g2 = _GameMaster(name="g2", selected=["Alice", "Bob"], log=log)

    result = engine.run_step(
        step_index=0,
        game_masters=[primary, g1, g2],
        agents=[alice, bob],
        verbose=False,
    )

    # Bob was filtered out entirely; Alice traverses g1 then g2 (barrier holds).
    assert log == ["g1:Alice", "g2:Alice"]
    assert result.failed_turns == ()
    assert bob.actions == []


def test_staged_mode_runs_stage_hops_concurrently() -> None:
    # Within one stage, hops on different GMs run concurrently: each agent's act()
    # waits on a 2-party barrier that only releases if both turns run at once. A
    # serialized stage would time out the barrier and fail the turns.
    barrier = threading.Barrier(2, timeout=10)
    engine = _engine(
        mode="staged",
        flow_order=["fixed_pre", "default"],
        flow_chains={"flow_a": ["gm_a"], "flow_b": ["gm_b"]},
    )
    alice = _Agent("Alice", barrier=barrier)
    bob = _Agent("Bob", barrier=barrier)
    primary = _GameMaster(
        name="primary",
        selected=[],
        agent_flow_tags={"Alice": "flow_a", "Bob": "flow_b"},
    )
    gm_a = _GameMaster(name="gm_a", selected=["Alice"])
    gm_b = _GameMaster(name="gm_b", selected=["Bob"])

    result = engine.run_step(
        step_index=0,
        game_masters=[primary, gm_a, gm_b],
        agents=[alice, bob],
        verbose=False,
    )

    assert result.failed_turns == ()
    assert gm_a.resolved == ["Alice"]
    assert gm_b.resolved == ["Bob"]


def test_staged_mode_empty_slot_skips_a_stage() -> None:
    # flow_b's chain has an empty slot at stage 0 (None), so Bob idles stage 0 and
    # only acts at stage 1 on g2 -- alongside Alice, who reaches g2 via [g1, g2].
    log: list[str] = []
    engine = _engine(
        mode="staged",
        flow_order=["fixed_pre", "default"],
        flow_chains={"flow_a": ["g1", "g2"], "flow_b": [None, "g2"]},
    )
    alice = _Agent("Alice")
    bob = _Agent("Bob")
    primary = _GameMaster(
        name="primary",
        selected=[],
        agent_flow_tags={"Alice": "flow_a", "Bob": "flow_b"},
    )
    g1 = _GameMaster(name="g1", selected=["Alice", "Bob"], log=log)
    g2 = _GameMaster(name="g2", selected=["Alice", "Bob"], log=log)

    engine.run_step(
        step_index=0,
        game_masters=[primary, g1, g2],
        agents=[alice, bob],
        verbose=False,
    )

    # Bob idled stage 0, so g1 only saw Alice; stage 1 (g2) saw both.
    assert bob.actions == ["g2:Bob"]
    assert g1.resolved == ["Alice"]
    assert log[0] == "g1:Alice"
    assert set(log[1:]) == {"g2:Alice", "g2:Bob"}


def test_staged_and_concurrent_agree_on_primary_gm_with_leading_empty_slot() -> None:
    # A flow that idles stage 0 (leading None) must not change which GM is reported as
    # primary_game_master between concurrent and staged: both prep batches in flow
    # order, so the first flow's first real hop ('ga2') wins in both.
    def _primary(mode: str) -> str:
        engine = _engine(
            mode=mode,
            flow_order=["fixed_pre", "default"],
            flow_chains={"flow_a": [None, "ga2"], "flow_b": ["gb1"]},
        )
        alice = _Agent("Alice")
        bob = _Agent("Bob")
        primary = _GameMaster(
            name="primary",
            selected=[],
            agent_flow_tags={"Alice": "flow_a", "Bob": "flow_b"},
        )
        ga2 = _GameMaster(name="ga2", selected=["Alice"])
        gb1 = _GameMaster(name="gb1", selected=["Bob"])
        return engine.run_step(
            step_index=0,
            game_masters=[primary, ga2, gb1],
            agents=[alice, bob],
            verbose=False,
        ).primary_game_master

    assert _primary("concurrent") == _primary("staged") == "ga2"


def test_staged_mode_runs_flow_order_prefix_before_staged_flows() -> None:
    # 'seed' is listed in flow_order (serial prefix); 'main' is not (staged). Both act
    # on the same shared GM, so the seed-then-act precedence must still hold.
    engine = _engine(
        mode="staged",
        flow_order=["seed"],
        flow_chains={"seed": ["board"], "main": ["board"]},
    )
    seed = _Agent("Seed")
    main = _Agent("Main")
    primary = _GameMaster(
        name="primary",
        selected=[],
        agent_flow_tags={"Seed": "seed", "Main": "main"},
    )
    board = _GameMaster(name="board", selected=["Seed", "Main"])

    engine.run_step(
        step_index=0,
        game_masters=[primary, board],
        agents=[seed, main],
        verbose=False,
    )

    assert board.resolved == ["Seed", "Main"]
