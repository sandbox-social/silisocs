"""Branch routing at execution time — a router observes prior hops and may drive agents.

A branch router is any callable ``(agents, gms, ctx) -> {agent name: gm name}``. It runs
when the flow's chain reaches the branch, after the earlier hops have drained, so it can
read live backend state or ask the agents. Covers: live-state visibility under all three
multi-GM traversals; the ``agent_choice`` built-in and its ``on_invalid`` fallbacks;
``match_choice``; custom class/function routers via ``build_router``; a plain router
reading a backend AND asking the agent directly; and the engine-side validation of a
router's returned assignment.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

import pytest

from silisocs.runtime.types import ActionOutput, ActionSpec, OutputType
from silisocs.simulation_engines.base_engines import RuntimeEngine
from silisocs.simulation_engines.policies.factory import build_router
from silisocs.simulation_engines.policies.routers import (
    AgentChoiceRouter,
    BranchSpec,
    RandomChoiceRouter,
    RouteInfo,
    Router,
    match_choice,
)
from silisocs.simulation_engines.policies.steps import (
    MultiGMSerialStepStrategy,
    MultiGMStagedStepStrategy,
    MultiGMStepStrategy,
)
from silisocs.simulation_engines.runtime_base import BranchHop, expand_hop

_MODES = {
    "concurrent": MultiGMStepStrategy,
    "staged": MultiGMStagedStepStrategy,
    "serial": MultiGMSerialStepStrategy,
}


class _Agent:
    def __init__(self, name: str, *, choice: str | None = None) -> None:
        self.name = name
        self._choice = choice  # answer to return for a CHOICE (routing) spec
        self.model = None
        self.observations: list[str] = []
        self.choice_prompts: list[str] = []

    def observe(self, observation: str) -> None:
        self.observations.append(observation)

    def act(self, action_spec: ActionSpec) -> ActionOutput:
        if action_spec.output_type == OutputType.CHOICE:
            self.choice_prompts.append(action_spec.prompt)
            return ActionOutput.choice_response(self._choice if self._choice is not None else "")
        return ActionOutput.from_text(f"{self.name}:{action_spec.prompt}")


class _Backend:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def count(self) -> int:
        return len(self._events)


class _GM:
    def __init__(
        self,
        name: str,
        *,
        events: list[str] | None = None,
        agent_flow_tags: dict[str, str] | None = None,
        backend_type: str = "test",
    ) -> None:
        self.name = name
        self.agent_flow_tags = agent_flow_tags or {}
        self.backend_type = backend_type
        self._events = events if events is not None else []
        self.backend = _Backend(self._events)
        self.resolved: list[str] = []

    def update(self, *, step: int, agents: list[_Agent], context: object | None = None) -> None:
        del step, agents, context

    def acting_agents(self, candidate_agents: list[_Agent]) -> list[str]:
        return [agent.name for agent in candidate_agents]

    def action_prompt(self, agent_name: str) -> ActionSpec:
        return ActionSpec(prompt=f"{self.name}:{agent_name}", output_type=OutputType.TEXT)

    def make_observation(self, agent_name: str) -> str:
        return f"obs:{self.name}:{agent_name}"

    def resolve_action(self, agent_name: str, action: ActionOutput) -> str:
        del action
        self.resolved.append(agent_name)
        self._events.append(f"{self.name}:{agent_name}")
        return f"resolved:{self.name}:{agent_name}"


def _run(strategy_cls: Any, flow_chains: Any, gms: Any, agents: Any, *, seed: int = 0) -> None:
    engine = RuntimeEngine(
        step_strategy=strategy_cls(flow_order=("fixed_pre",), flow_chains=flow_chains, seed=seed)
    )
    engine.run_step(step_index=0, game_masters=gms, agents=agents, verbose=False)


# --------------------------------------------------------- live-state visibility


class _VisibilityRouter:
    """Records the live event count it observes, then routes everyone to the first choice."""

    name = "visibility"

    def __init__(self) -> None:
        self.seen: list[int] = []

    def __call__(self, agents: Any, gms: Any, ctx: RouteInfo) -> dict[str, str]:
        self.seen.append(gms["tw_gm"].backend.count())
        return {agent.name: "tw_gm" for agent in agents}


@pytest.mark.parametrize("mode", ["concurrent", "staged", "serial"])
def test_router_sees_prior_hop_effects(mode: str) -> None:
    events: list[str] = []  # shared backend log across all chain GMs
    router = _VisibilityRouter()
    branch = BranchSpec(choices=("tw_gm", "rd_gm"), router=router)
    flow_chains = {"social": ["seed_gm", branch]}
    primary = _GM("primary", agent_flow_tags={"Alice": "social", "Zed": "social"})
    gms = [
        primary,
        _GM("seed_gm", events=events),
        _GM("tw_gm", events=events),
        _GM("rd_gm", events=events),
    ]
    _run(_MODES[mode], flow_chains, gms, [_Agent("Alice"), _Agent("Zed")])
    # seed_gm resolved both agents BEFORE the branch's router ran, so it observed at
    # least those two events. (This case also proves multi_gm_serial now supports a
    # live-state router — the old design rejected it.)
    assert router.seen, "router never ran"
    assert min(router.seen) >= 2


# ------------------------------------------------------------- agent_choice built-in


def test_agent_choice_routes_by_each_agents_answer() -> None:
    branch = BranchSpec(
        choices=("tw_gm", "rd_gm"), router=AgentChoiceRouter(prompt="pick {choices}")
    )
    flow_chains = {"social": [branch]}
    primary = _GM("primary", agent_flow_tags={"Alice": "social", "Zed": "social"})
    gms = [primary, _GM("tw_gm"), _GM("rd_gm")]
    alice, zed = _Agent("Alice", choice="rd_gm"), _Agent("Zed", choice="tw_gm")
    _run(MultiGMStepStrategy, flow_chains, gms, [alice, zed])
    by_name = {gm.name: gm for gm in gms}
    assert by_name["rd_gm"].resolved == ["Alice"]
    assert by_name["tw_gm"].resolved == ["Zed"]
    assert alice.choice_prompts == ["pick tw_gm, rd_gm"]  # prompt rendered with {choices}


def test_agent_choice_prompt_placeholders_render() -> None:
    router = AgentChoiceRouter(prompt="{agent}@{flow}#{step}: {choices}")
    assert (
        router._render("Alice", ("tw", "rd"), RouteInfo("social", 3, 0)) == "Alice@social#3: tw, rd"
    )


def test_agent_choice_unknown_placeholder_raises() -> None:
    router = AgentChoiceRouter(prompt="pick {bogus}")
    with pytest.raises(ValueError, match="unknown placeholder"):
        router._render("A", ("x", "y"), RouteInfo("f", 0, 0))


def test_agent_choice_invalid_on_invalid_value_rejected() -> None:
    with pytest.raises(ValueError, match="on_invalid must be"):
        AgentChoiceRouter(on_invalid="nonsense")


def _route_one(router: AgentChoiceRouter, choice: str, *, seed: int = 5) -> str:
    """Route a single agent (answering ``choice``) and return its assigned GM."""
    agent = _Agent("Alice", choice=choice)
    out = router(
        [cast(Any, agent)], {"tw_gm": object(), "rd_gm": object()}, RouteInfo("social", 0, seed)
    )
    return out["Alice"]


def test_agent_choice_matches_contained_answer() -> None:
    # A wordy answer that names exactly one choice still resolves.
    assert _route_one(AgentChoiceRouter(), "I would pick rd_gm please") == "rd_gm"


def test_agent_choice_on_invalid_first() -> None:
    assert _route_one(AgentChoiceRouter(on_invalid="first"), "") == "tw_gm"


def test_agent_choice_on_invalid_raise() -> None:
    with pytest.raises(ValueError, match="invalid routing choice"):
        _route_one(AgentChoiceRouter(on_invalid="raise"), "totally-unknown")


def test_agent_choice_random_fallback_is_replay_stable() -> None:
    router = AgentChoiceRouter(on_invalid="random")
    picks = {_route_one(router, "junk") for _ in range(4)}
    assert picks <= {"tw_gm", "rd_gm"}
    assert len(picks) == 1  # deterministic for a fixed (seed, flow, step, agent)


def test_match_choice_variants() -> None:
    assert match_choice("tw_gm", ("tw_gm", "rd_gm")) == "tw_gm"  # exact
    assert match_choice("TW_GM", ("tw_gm", "rd_gm")) == "tw_gm"  # case-insensitive
    assert match_choice("I pick rd_gm", ("tw_gm", "rd_gm")) == "rd_gm"  # contained once
    assert match_choice("", ("tw_gm", "rd_gm")) is None
    assert match_choice("neither", ("tw_gm", "rd_gm")) is None


def _pick_first(
    candidates: Sequence[Any], gm_map: Mapping[str, Any], info: RouteInfo
) -> dict[str, str]:
    first = next(iter(gm_map))
    return {agent.name: first for agent in candidates}


def test_router_protocol_admits_builtins_and_plain_functions() -> None:
    # The typed list is the assertion mypy checks: each entry must structurally
    # satisfy the Router Protocol. Its parameters are positional-only, so
    # _pick_first conforms despite naming its arguments differently.
    routers: list[Router] = [RandomChoiceRouter(), AgentChoiceRouter(), _pick_first]
    ctx = RouteInfo(flow="f", step=0, seed=1)
    for router in routers:
        route = router(cast(Any, [_Agent("ann")]), {"tw_gm": object(), "rd_gm": object()}, ctx)
        assert route["ann"] in {"tw_gm", "rd_gm"}


# ----------------------------------------------------- custom routers (class/function)


class _TargetRouter:
    """Custom class router taking a config param and routing everyone by it."""

    def __init__(self, target: str = "tw_gm") -> None:
        self.target = target

    def __call__(self, agents: Any, gms: Any, ctx: RouteInfo) -> dict[str, str]:
        return {agent.name: self.target for agent in agents}


def test_custom_class_path_router_receives_params() -> None:
    router = build_router(
        {"class_path": f"{__name__}._TargetRouter", "params": {"target": "rd_gm"}}
    )
    assert isinstance(router, _TargetRouter)
    assert router.target == "rd_gm"


class _BackendAwareAskRouter:
    """The freedom demo: read live backend state AND ask each agent its own question.

    No base class, no RouteContext, no engine-provided ask helper — the router just
    reads ``gms[...].backend`` and calls ``agent.act(...)`` with a spec it builds itself.
    """

    def __init__(self) -> None:
        self.observed_count: int | None = None

    def __call__(self, agents: Any, gms: Any, ctx: RouteInfo) -> dict[str, str]:
        self.observed_count = gms["tw_gm"].backend.count()
        route: dict[str, str] = {}
        for agent in agents:
            spec = ActionSpec(
                prompt=f"seen {self.observed_count} events; tw_gm or rd_gm?",
                output_type=OutputType.CHOICE,
                options=("tw_gm", "rd_gm"),
            )
            answer = agent.act(spec)
            text = answer.choice if isinstance(answer, ActionOutput) else str(answer)
            route[agent.name] = "tw_gm" if "tw" in str(text) else "rd_gm"
        return route


def test_custom_router_reads_backend_and_asks_agent_directly() -> None:
    events = ["e1", "e2", "e3"]
    router = _BackendAwareAskRouter()
    branch = BranchSpec(choices=("tw_gm", "rd_gm"), router=router)
    flow_chains = {"social": [branch]}
    primary = _GM("primary", agent_flow_tags={"Alice": "social"})
    gms = [primary, _GM("tw_gm", events=events), _GM("rd_gm", events=events)]
    _run(MultiGMStepStrategy, flow_chains, gms, [_Agent("Alice", choice="tw_gm")])
    assert router.observed_count == 3  # read live backend state itself
    assert {gm.name for gm in gms if gm.resolved} == {"tw_gm"}  # agent's own answer honored


@pytest.mark.parametrize("mode", ["concurrent", "staged", "serial"])
def test_broken_router_fails_the_step_in_every_traversal(mode: str) -> None:
    # A router that leaves an agent unrouted is a config/programming error; it must
    # fail the step loudly under every traversal — including concurrent mode, where
    # branch resolution runs on a chain-driver thread rather than inline.
    branch = BranchSpec(choices=("tw_gm", "rd_gm"), router=lambda agents, gms, ctx: {})
    flow_chains = {"social": [branch]}
    primary = _GM("primary", agent_flow_tags={"Alice": "social"})
    gms = [primary, _GM("tw_gm"), _GM("rd_gm")]
    with pytest.raises(ValueError, match="left agent"):
        _run(_MODES[mode], flow_chains, gms, [_Agent("Alice")])


# -------------------------------------------------------- engine-side validation


def _hop(router: Any, candidates: list[_Agent], gms: dict[str, Any]) -> BranchHop:
    return BranchHop(
        router=router,
        gms=gms,
        candidates=tuple(candidates),
        flow_name="social",
        step_index=0,
        seed=0,
        turn_policy=None,
    )


def _choices() -> dict[str, Any]:
    return {"tw_gm": _GM("tw_gm"), "rd_gm": _GM("rd_gm")}


def test_resolve_branch_rejects_non_mapping_return() -> None:
    engine = RuntimeEngine()
    hop = _hop(lambda agents, gms, ctx: ["tw_gm"], [_Agent("Alice")], _choices())
    with pytest.raises(TypeError, match="must return a mapping"):
        engine._resolve_branch(hop)


def test_resolve_branch_rejects_gm_not_in_choices() -> None:
    engine = RuntimeEngine()
    hop = _hop(lambda agents, gms, ctx: {"Alice": "ghost_gm"}, [_Agent("Alice")], _choices())
    with pytest.raises(ValueError, match="not among its choices"):
        engine._resolve_branch(hop)


def test_resolve_branch_rejects_unrouted_agent() -> None:
    engine = RuntimeEngine()
    hop = _hop(
        lambda agents, gms, ctx: {"Alice": "tw_gm"},
        [_Agent("Alice"), _Agent("Zed")],
        _choices(),
    )
    with pytest.raises(ValueError, match="left agent"):
        engine._resolve_branch(hop)


def test_resolve_branch_rejects_unknown_agent_key() -> None:
    engine = RuntimeEngine()
    hop = _hop(
        lambda agents, gms, ctx: {"Ghost": "tw_gm", "Alice": "tw_gm"},
        [_Agent("Alice")],
        _choices(),
    )
    with pytest.raises(ValueError, match="unknown agent"):
        engine._resolve_branch(hop)


def test_branch_hop_cannot_be_flattened() -> None:
    hop = _hop(_VisibilityRouter(), [_Agent("Alice")], _choices())
    with pytest.raises(RuntimeError, match="resolves at execution time"):
        expand_hop(hop)
