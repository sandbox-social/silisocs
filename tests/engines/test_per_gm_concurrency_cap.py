"""Tests for per-GM concurrency caps (sim.engine.step.params.gm_concurrency_caps).

A per-GM cap installs a BoundedSemaphore that limits how many of THAT GM's agent
turns run concurrently. The global ``sim.max_concurrent_actions`` stays the overall
ceiling and the default for every GM; the effective per-GM permit count is
``min(cap, worker_limit)``. An empty map is a byte-for-byte no-op.
"""

from __future__ import annotations

import threading
import time

import pytest
from omegaconf import DictConfig, OmegaConf

from silisocs.runtime.construction.engines import build_engine
from silisocs.runtime.types import ActionOutput, ActionSpec, OutputType
from silisocs.simulation_engines.base_engines import RuntimeEngine
from silisocs.simulation_engines.policies.factory import build_gm_concurrency_caps


class _ConcurrencyTracker:
    """Records, per GM name, the peak number of turns in-flight at once."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.in_flight: dict[str, int] = {}
        self.peak: dict[str, int] = {}

    def enter(self, gm_name: str) -> None:
        with self._lock:
            current = self.in_flight.get(gm_name, 0) + 1
            self.in_flight[gm_name] = current
            if current > self.peak.get(gm_name, 0):
                self.peak[gm_name] = current

    def leave(self, gm_name: str) -> None:
        with self._lock:
            self.in_flight[gm_name] = self.in_flight.get(gm_name, 0) - 1


class _Agent:
    def __init__(self, name: str, tracker: _ConcurrencyTracker, sleep_s: float = 0.05) -> None:
        self.name = name
        self._tracker = tracker
        self._sleep_s = sleep_s
        self.actions: list[str] = []

    def observe(self, observation: str) -> None:
        del observation

    def act(self, action_spec: ActionSpec) -> ActionOutput:
        # The GM stamps its name into the prompt ("<gm>:<agent>"), so the agent can
        # report which GM's turn is currently executing.
        gm_name = action_spec.prompt.split(":", 1)[0]
        self.actions.append(action_spec.prompt)
        self._tracker.enter(gm_name)
        try:
            time.sleep(self._sleep_s)
        finally:
            self._tracker.leave(gm_name)
        return ActionOutput.from_text(self.name)


class _GameMaster:
    def __init__(
        self,
        *,
        name: str,
        selected: list[str],
        agent_flow_tags: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self.selected = selected
        self.agent_flow_tags = agent_flow_tags or {}
        self.resolved: list[str] = []

    def update(self, *, step: int, agents: list[_Agent], context: object | None = None) -> None:
        del step, agents, context

    def acting_agents(self, candidate_agents: list[_Agent]) -> list[str]:
        available = {agent.name for agent in candidate_agents}
        return [name for name in self.selected if name in available]

    def action_prompt(self, agent_name: str) -> ActionSpec:
        return ActionSpec(prompt=f"{self.name}:{agent_name}", output_type=OutputType.TEXT)

    def make_observation(self, agent_name: str) -> str:
        del agent_name
        return ""

    def resolve_action(self, agent_name: str, action: ActionOutput) -> str:
        del action
        self.resolved.append(agent_name)
        return ""


_TWO_FLOW_CHAINS = {"tw_flow": ["tw_gm"], "rd_flow": ["rd_gm"]}


def _multi_engine(
    *,
    gm_concurrency_caps: dict[str, object] | None = None,
    max_concurrent_actions: int | None = None,
    flow_chains: dict[str, list[str]] | None = None,
) -> RuntimeEngine:
    params: dict[str, object] = {}
    if gm_concurrency_caps is not None:
        params["gm_concurrency_caps"] = gm_concurrency_caps
    sim: dict[str, object] = {
        "engine": {
            "turn_policy": {"built_in": "single_action"},
            "step": {"built_in": "multi_gm", "params": params},
        }
    }
    if max_concurrent_actions is not None:
        sim["max_concurrent_actions"] = max_concurrent_actions
    cfg = OmegaConf.create({"sim": sim})
    return build_engine(cfg, flow_chains=flow_chains)


def _two_flow_world(
    tracker: _ConcurrencyTracker, *, n_each: int = 4, sleep_s: float = 0.05
) -> tuple[list[_Agent], list[_GameMaster]]:
    """Two non-overlapping flows: tw_flow -> tw_gm, rd_flow -> rd_gm.

    Neither flow is in the default flow_order, so both run as concurrent chains.
    """
    tw_agents = [_Agent(f"tw{i}", tracker, sleep_s) for i in range(n_each)]
    rd_agents = [_Agent(f"rd{i}", tracker, sleep_s) for i in range(n_each)]
    agents = tw_agents + rd_agents
    flow_tags = {a.name: "tw_flow" for a in tw_agents}
    flow_tags.update({a.name: "rd_flow" for a in rd_agents})
    primary = _GameMaster(
        name="primary",
        selected=[],
        agent_flow_tags=flow_tags,
    )
    tw_gm = _GameMaster(name="tw_gm", selected=[a.name for a in tw_agents])
    rd_gm = _GameMaster(name="rd_gm", selected=[a.name for a in rd_agents])
    return agents, [primary, tw_gm, rd_gm]


def test_capped_gm_serializes_uncapped_gm_runs_parallel() -> None:
    # tw_gm is capped to 1 concurrent turn; rd_gm is uncapped. With a large global
    # ceiling and several agents per GM, tw_gm's peak must be 1 while rd_gm's > 1.
    tracker = _ConcurrencyTracker()
    engine = _multi_engine(
        gm_concurrency_caps={"tw_gm": 1},
        max_concurrent_actions=1000,
        flow_chains=_TWO_FLOW_CHAINS,
    )
    agents, game_masters = _two_flow_world(tracker, n_each=4)

    engine.run_step(step_index=0, game_masters=game_masters, agents=agents, verbose=False)

    assert tracker.peak["tw_gm"] == 1
    assert tracker.peak["rd_gm"] > 1


def test_empty_map_is_identity_noop() -> None:
    # Empty cap map: engine stores {}, _wrap_turn returns the thunk unchanged, and a
    # run matches the uncapped baseline (every selected agent resolved exactly once).
    engine = _multi_engine(gm_concurrency_caps={}, flow_chains=_TWO_FLOW_CHAINS)
    assert engine.gm_concurrency_caps == {}

    def thunk() -> str:
        return "x"

    gm = _GameMaster(name="tw_gm", selected=[])
    assert engine._wrap_turn(gm, 16, thunk) is thunk

    tracker = _ConcurrencyTracker()
    agents, game_masters = _two_flow_world(tracker, n_each=3, sleep_s=0.0)
    engine.run_step(step_index=0, game_masters=game_masters, agents=agents, verbose=False)
    _tw, tw_gm, rd_gm = game_masters
    assert sorted(tw_gm.resolved) == ["tw0", "tw1", "tw2"]
    assert sorted(rd_gm.resolved) == ["rd0", "rd1", "rd2"]


def test_cap_clamped_to_worker_limit() -> None:
    # A cap larger than the global worker limit clamps to the global limit: with a
    # tiny max_concurrent_actions and cap=10, the installed permit count (and thus
    # observed peak) never exceeds the global limit.
    tracker = _ConcurrencyTracker()
    engine = _multi_engine(
        gm_concurrency_caps={"tw_gm": 10},
        max_concurrent_actions=2,
        flow_chains=_TWO_FLOW_CHAINS,
    )
    agents, game_masters = _two_flow_world(tracker, n_each=5)

    engine.run_step(step_index=0, game_masters=game_masters, agents=agents, verbose=False)

    # Effective per-GM = min(10, worker_limit<=2) -> peak bounded by the global limit.
    assert tracker.peak["tw_gm"] <= 2


def test_build_gm_concurrency_caps_rejects_bad_values() -> None:
    with pytest.raises(ValueError):
        build_gm_concurrency_caps({"gm": 0})
    with pytest.raises(ValueError):
        build_gm_concurrency_caps({"gm": "x"})
    with pytest.raises(ValueError):
        build_gm_concurrency_caps({"": 2})


def test_gm_concurrency_caps_threads_from_config() -> None:
    engine = _multi_engine(gm_concurrency_caps={"tw_gm": 3})
    assert engine.gm_concurrency_caps == {"tw_gm": 3}


def test_cap_applies_under_base_step_mode() -> None:
    # gm_concurrency_caps is resolved per batch by GM name, so it works under the base
    # (single-GM) step mode too. Cap main to 1 -> peak == 1 across several agents.
    tracker = _ConcurrencyTracker()
    cfg = OmegaConf.create(
        {
            "sim": {
                "max_concurrent_actions": 1000,
                "engine": {
                    "turn_policy": {"built_in": "single_action"},
                    "step": {
                        "built_in": "base",
                        "params": {"gm_concurrency_caps": {"main": 1}},
                    },
                },
            }
        }
    )
    engine = build_engine(cfg)
    agents = [_Agent(f"a{i}", tracker, sleep_s=0.05) for i in range(4)]
    main = _GameMaster(name="main", selected=[a.name for a in agents])

    engine.run_step(step_index=0, game_masters=[main], agents=agents, verbose=False)

    assert tracker.peak["main"] == 1


def _build_engine_cfg(step_built_in: str, params: dict[str, object]) -> DictConfig:
    """Compose a minimal cfg the construction.engines.build_engine factory accepts."""
    return OmegaConf.create(
        {
            "sim": {
                "engine": {
                    "turn_policy": {"built_in": "single_action"},
                    "step": {"built_in": step_built_in, "params": params},
                }
            }
        }
    )


# ---------------------------------------------------------------------------
# Fix #2: build_engine threads gm_concurrency_caps into the `sequential` branch
# (previously dropped/unvalidated for that step mode).
# ---------------------------------------------------------------------------
def test_build_engine_sequential_threads_gm_concurrency_caps() -> None:
    cfg = _build_engine_cfg("sequential", {"gm_concurrency_caps": {"main": 2}})
    engine = build_engine(cfg)

    # Sequential preset is the generic RuntimeEngine (not a Base/Flow wrapper) and
    # must carry the caps verbatim rather than dropping them.
    assert isinstance(engine, RuntimeEngine)
    assert engine.gm_concurrency_caps == {"main": 2}


def test_build_engine_sequential_validates_bad_gm_concurrency_cap() -> None:
    # An invalid cap (< 1) under the sequential step mode is now validated at
    # build_engine time instead of being silently dropped.
    cfg = _build_engine_cfg("sequential", {"gm_concurrency_caps": {"main": 0}})
    with pytest.raises(ValueError):
        build_engine(cfg)


def test_build_engine_sequential_empty_caps_is_noop() -> None:
    # No caps configured -> empty map, byte-for-byte no-op (current behavior).
    cfg = _build_engine_cfg("sequential", {})
    engine = build_engine(cfg)
    assert engine.gm_concurrency_caps == {}
