"""Tests for declarative mid-run interventions (simulation_engines/interventions.py)."""

from __future__ import annotations

from typing import Any

import pytest
from omegaconf import OmegaConf

from silisocs.environments.gm.components.base import BaseComponent
from silisocs.runtime.types import ActionOutput, ToolCall
from silisocs.simulation_engines.interventions import (
    BanFilterParticipation,
    InterventionContext,
    InterventionHandler,
    InterventionSchedule,
    validate_interventions,
)
from silisocs.simulation_engines.policies.participation import (
    ActivityProbabilityParticipation,
    AllParticipation,
)


class _Agent:
    def __init__(self, name: str) -> None:
        self.name = name
        self.observations: list[str] = []

    def observe(self, text: str) -> None:
        self.observations.append(text)


class _RecsysComponent:
    def __init__(self) -> None:
        self.recsys_type: str | None = None

    def set_recsys_type(self, recsys_type: str) -> None:
        self.recsys_type = recsys_type


class _Logger:
    def __init__(self) -> None:
        self.episode_idx = 0


class _Backend:
    def __init__(self) -> None:
        self.action_logger = _Logger()
        self.exposure_logger = _Logger()
        self.harness_logger = _Logger()


class _GM:
    def __init__(self, name: str = "social", backend_type: str = "twitter_like") -> None:
        self.name = name
        self.backend_type = backend_type
        self.backend = _Backend()
        self.observe_c: Any = _RecsysComponent()
        self.update_c: Any = _RecsysComponent()
        self.resolved: list[tuple[str, Any, int]] = []

    @property
    def components(self) -> dict[str, Any]:
        return {"observe": self.observe_c, "update": self.update_c}

    def resolve_action(self, agent_name: str, action: Any) -> str:
        # Capture the episode the injected action would be logged under.
        self.resolved.append((agent_name, action, self.backend.action_logger.episode_idx))
        return "ok"


class _Engine:
    def __init__(self, participation: Any = None) -> None:
        self.participation = participation
        self._initialization_context = None


def _ctx(
    engine: _Engine, gms: list[_GM], agents: list[_Agent], step: int = 0
) -> InterventionContext:
    return InterventionContext(
        engine=engine, game_masters=gms, agents=agents, sim_roles={}, step=step
    )


# --------------------------------------------------------------- parsing / validation


def test_parse_sorts_and_reports_bool() -> None:
    cfg = OmegaConf.create(
        {
            "interventions": [
                {"at_step": 5, "actions": [{"kind": "ban_agents", "agents": ["A"]}]},
                {"at_step": 2, "actions": [{"kind": "unban_agents", "agents": ["A"]}]},
            ]
        }
    )
    sched = InterventionSchedule.parse(cfg, sim_roles={})
    assert bool(sched)
    assert [i.at_step for i in sched._interventions] == [2, 5]


def test_parse_none_is_empty() -> None:
    assert not InterventionSchedule.parse(OmegaConf.create({}))
    assert not InterventionSchedule.parse(OmegaConf.create({"interventions": None}))


@pytest.mark.parametrize(
    "bad,match",
    [
        ({"interventions": [{"actions": []}]}, "at_step"),
        ({"interventions": [{"at_step": -1, "actions": [{"kind": "ban_agents"}]}]}, ">= 0"),
        ({"interventions": [{"at_step": 1, "actions": []}]}, "non-empty 'actions'"),
        (
            {"interventions": [{"at_step": 1, "actions": [{"kind": "nope"}]}]},
            "unknown intervention",
        ),
        ({"interventions": [{"at_step": 1, "actions": [{"kind": "custom"}]}]}, "class_path"),
    ],
)
def test_parse_rejects_malformed(bad: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        InterventionSchedule.parse(OmegaConf.create(bad))


def test_validate_semantic_errors() -> None:
    cfg = OmegaConf.create(
        {"interventions": [{"at_step": 1, "actions": [{"kind": "ban_agents", "agents": []}]}]}
    )
    with pytest.raises(ValueError, match="non-empty 'agents'"):
        validate_interventions(cfg, gm_names={"social"})

    cfg2 = OmegaConf.create(
        {"interventions": [{"at_step": 1, "actions": [{"kind": "set_recsys", "recsys_type": "x"}]}]}
    )
    with pytest.raises(ValueError, match="explicit 'gm'"):
        validate_interventions(cfg2, gm_names={"a", "b"})

    cfg3 = OmegaConf.create(
        {
            "interventions": [
                {
                    "at_step": 1,
                    "actions": [
                        {"kind": "inject_post", "author": "A", "text": "hi", "gm": "ghost"}
                    ],
                }
            ]
        }
    )
    with pytest.raises(ValueError, match="unknown game master"):
        validate_interventions(cfg3, gm_names={"social"})


def _flow_scoped_cfg(kind: str, flow: str) -> Any:
    return OmegaConf.create(
        {
            "interventions": [
                {
                    "at_step": 1,
                    "actions": [{"kind": kind, "flow": flow, "slot": {"built_in": "random"}}],
                }
            ]
        }
    )


@pytest.mark.parametrize("kind", ["set_turn_policy", "set_router"])
def test_validate_flow_targets_preflight(kind: str) -> None:
    """A typo'd flow name fails at config validation, not mid-run at fire time."""
    declared = {"treatment", "default"}
    with pytest.raises(ValueError, match="unknown flow 'treatmnt'"):
        validate_interventions(
            _flow_scoped_cfg(kind, "treatmnt"), gm_names={"social"}, flow_names=declared
        )
    validate_interventions(
        _flow_scoped_cfg(kind, "treatment"), gm_names={"social"}, flow_names=declared
    )
    # No statically-declared flows (e.g. custom step strategy): defer to fire time.
    validate_interventions(_flow_scoped_cfg(kind, "treatmnt"), gm_names={"social"})


def test_declared_flow_names_collects_all_config_sources() -> None:
    from silisocs.runtime.configuration.validation import _declared_flow_names

    cfg = OmegaConf.create(
        {
            "agents": {
                "persona_pipeline": {"classes": {"c1": {"flow_tag": "influencer"}, "c2": {}}}
            },
            "sim": {
                "engine": {
                    "step": {"params": {"flow_order": ["early"], "agent_to_flow": {"A": "vip"}}}
                }
            },
            "env": {"gm_orchestration": {"flow_bindings": {"flow_to_gms": {"chained": ["a"]}}}},
        }
    )
    assert _declared_flow_names(cfg) == {"influencer", "early", "vip", "chained", "default"}
    # Nothing declared -> empty set -> flow checks defer to fire time.
    assert _declared_flow_names(OmegaConf.create({})) == set()


# --------------------------------------------------------------- ban / unban


def test_ban_wrapper_composes_and_unwraps() -> None:
    inner = AllParticipation()
    engine = _Engine(inner)
    agents = [_Agent("A"), _Agent("B"), _Agent("C")]

    ban = InterventionSchedule.parse(
        OmegaConf.create(
            {
                "interventions": [
                    {"at_step": 0, "actions": [{"kind": "ban_agents", "agents": ["B"]}]}
                ]
            }
        )
    )
    ban.apply_due(step=0, engine=engine, game_masters=[], agents=agents)
    assert isinstance(engine.participation, BanFilterParticipation)
    active = engine.participation.participating_agents(
        agent_names=["A", "B", "C"], step_index=0, seed=1
    )
    assert active == ["A", "C"]

    unban = InterventionSchedule.parse(
        OmegaConf.create(
            {
                "interventions": [
                    {"at_step": 0, "actions": [{"kind": "unban_agents", "agents": ["B"]}]}
                ]
            }
        )
    )
    unban.apply_due(step=0, engine=engine, game_masters=[], agents=agents)
    # Empty ban set unwraps back to the inner policy.
    assert engine.participation is inner


def test_ban_preserves_inner_activity_policy() -> None:
    inner = ActivityProbabilityParticipation(active_probability=0.0, min_active_agents=0)
    engine = _Engine(inner)
    wrapper = BanFilterParticipation(inner, banned={"B"})
    engine.participation = wrapper
    # inner returns nobody (p=0); ban still composes without error.
    assert wrapper.participating_agents(agent_names=["A", "B"], step_index=0, seed=1) == []


# --------------------------------------------------------------- set_participation


def test_set_participation_swaps_policy_and_keeps_ban() -> None:
    engine = _Engine(BanFilterParticipation(AllParticipation(), banned={"B"}))
    sched = InterventionSchedule.parse(
        OmegaConf.create(
            {
                "interventions": [
                    {
                        "at_step": 0,
                        "actions": [
                            {
                                "kind": "set_participation",
                                "slot": {"built_in": "all", "params": {}},
                            }
                        ],
                    }
                ]
            }
        ),
        sim_roles={},
    )
    sched.apply_due(step=0, engine=engine, game_masters=[], agents=[])
    # Still wrapped (ban preserved), inner replaced.
    assert isinstance(engine.participation, BanFilterParticipation)
    assert engine.participation.banned == {"B"}


# --------------------------------------------------------------- set_recsys


def test_set_recsys_updates_both_components() -> None:
    gm = _GM()
    engine = _Engine()
    sched = InterventionSchedule.parse(
        OmegaConf.create(
            {
                "interventions": [
                    {"at_step": 0, "actions": [{"kind": "set_recsys", "recsys_type": "twhin"}]}
                ]
            }
        )
    )
    sched.apply_due(step=0, engine=engine, game_masters=[gm], agents=[])
    assert gm.observe_c.recsys_type == "twhin"
    assert gm.update_c.recsys_type == "twhin"


def test_set_recsys_no_components_raises() -> None:
    class _Bare:
        name = "bare"
        components: dict[str, Any] = {}

    handler = InterventionSchedule.parse(
        OmegaConf.create(
            {
                "interventions": [
                    {"at_step": 0, "actions": [{"kind": "set_recsys", "recsys_type": "x"}]}
                ]
            }
        )
    )
    with pytest.raises(ValueError, match="no recsys-aware components"):
        handler.apply_due(step=0, engine=_Engine(), game_masters=[_Bare()], agents=[])


# --------------------------------------------------------------- set_component_params


class _TunableComponent(BaseComponent):
    """Declares tunables resolved via setter convention AND plain attribute."""

    runtime_tunable = frozenset({"recsys_type", "max_posts"})

    def __init__(self) -> None:
        self.recsys_calls: list[str] = []
        self.max_posts = 10

    def set_recsys_type(self, recsys_type: str) -> None:
        self.recsys_calls.append(recsys_type)


def _params_schedule(params: dict[str, Any]) -> InterventionSchedule:
    return InterventionSchedule.parse(
        OmegaConf.create(
            {
                "interventions": [
                    {
                        "at_step": 0,
                        "actions": [{"kind": "set_component_params", "params": params}],
                    }
                ]
            }
        )
    )


def test_set_component_params_setter_and_attribute_paths() -> None:
    gm = _GM()
    component = _TunableComponent()
    gm.observe_c = component  # replaces one duck-typed stub with a BaseComponent
    sched = _params_schedule({"recsys_type": "twhin", "max_posts": 3})
    sched.apply_due(step=0, engine=_Engine(), game_masters=[gm], agents=[])
    assert component.recsys_calls == ["twhin"]  # setter convention (set_recsys_type)
    assert component.max_posts == 3  # plain-attribute fallback
    assert gm.update_c.recsys_type == "twhin"  # duck-typed set_<name> component too


def test_set_component_params_undeclared_param_raises() -> None:
    gm = _GM()
    gm.observe_c = _TunableComponent()
    with pytest.raises(ValueError, match=r"accepts parameter\(s\) \['bogus'\]"):
        _params_schedule({"recsys_type": "x", "bogus": 1}).apply_due(
            step=0, engine=_Engine(), game_masters=[gm], agents=[]
        )


def test_set_component_params_validate_requires_params_mapping() -> None:
    with pytest.raises(ValueError, match="non-empty 'params' mapping"):
        validate_interventions(
            OmegaConf.create(
                {"interventions": [{"at_step": 0, "actions": [{"kind": "set_component_params"}]}]}
            ),
            gm_names=set(),
        )


def test_set_component_params_retunes_timeline_mode_on_real_observe_component() -> None:
    """`timeline_mode` is mid-run tunable on the shipped social-media observe component."""
    from silisocs.environments.gm.components.social_media.observe import (
        TimelineMakeObservation,
    )

    component = TimelineMakeObservation(
        model=None,
        agent_names=["Alice"],
        backend=_Backend(),
        timeline_mode="follower_chronological",
    )
    gm = _GM()
    gm.observe_c = component
    sched = _params_schedule({"timeline_mode": "recommendation"})
    sched.apply_due(step=0, engine=_Engine(), game_masters=[gm], agents=[])
    assert component._timeline_mode == "recommendation"


def test_base_component_set_params_only_applies_declared() -> None:
    component = _TunableComponent()
    assert component.set_params({"max_posts": 7, "unrelated": True}) == {"max_posts"}
    assert component.max_posts == 7
    assert not hasattr(component, "unrelated")


def test_set_recsys_reaches_declared_tunable_without_bespoke_setter() -> None:
    class _AttrOnlyRecsys(BaseComponent):
        runtime_tunable = frozenset({"recsys_type"})

        def __init__(self) -> None:
            self.recsys_type: str | None = None

    gm = _GM()
    gm.observe_c = _AttrOnlyRecsys()
    gm.update_c = _AttrOnlyRecsys()
    sched = InterventionSchedule.parse(
        OmegaConf.create(
            {
                "interventions": [
                    {"at_step": 0, "actions": [{"kind": "set_recsys", "recsys_type": "twhin"}]}
                ]
            }
        )
    )
    sched.apply_due(step=0, engine=_Engine(), game_masters=[gm], agents=[])
    assert gm.observe_c.recsys_type == "twhin"
    assert gm.update_c.recsys_type == "twhin"


# --------------------------------------------------------------- set_turn_policy


_INITIAL_TURN_POLICY = object()


class _TurnPolicyEngine:
    def __init__(self, step_strategy: Any = None) -> None:
        self.participation = None
        self.turn_policy: Any = _INITIAL_TURN_POLICY
        self.gm_turn_policies: dict[str, Any] = {}
        self.step_strategy = step_strategy


class _FlowStrategy:
    name = "flow"

    def __init__(self) -> None:
        self.flow_turn_policies: dict[str, Any] = {}


def _turn_policy_schedule(**extra: Any) -> InterventionSchedule:
    action: dict[str, Any] = {
        "kind": "set_turn_policy",
        "slot": {"built_in": "fixed_count", "params": {"count": 2}},
    }
    action.update(extra)
    return InterventionSchedule.parse(
        OmegaConf.create({"interventions": [{"at_step": 0, "actions": [action]}]})
    )


def test_set_turn_policy_global_swaps_engine_default() -> None:
    engine = _TurnPolicyEngine()
    _turn_policy_schedule().apply_due(step=0, engine=engine, game_masters=[], agents=[])
    assert type(engine.turn_policy).__name__ == "FixedCountTurnPolicy"


def test_set_turn_policy_per_gm_lands_in_gm_map() -> None:
    engine = _TurnPolicyEngine()
    gm = _GM(name="social")
    _turn_policy_schedule(gm="social").apply_due(
        step=0, engine=engine, game_masters=[gm], agents=[]
    )
    assert type(engine.gm_turn_policies["social"]).__name__ == "FixedCountTurnPolicy"
    assert engine.turn_policy is _INITIAL_TURN_POLICY  # global default untouched


def test_set_turn_policy_per_flow_lands_in_strategy_map() -> None:
    strategy = _FlowStrategy()
    engine = _TurnPolicyEngine(step_strategy=strategy)
    _turn_policy_schedule(flow="burst").apply_due(step=0, engine=engine, game_masters=[], agents=[])
    assert type(strategy.flow_turn_policies["burst"]).__name__ == "FixedCountTurnPolicy"


def test_set_turn_policy_flow_scope_requires_flow_aware_strategy() -> None:
    class _NoFlow:
        name = "base"

    engine = _TurnPolicyEngine(step_strategy=_NoFlow())
    with pytest.raises(ValueError, match="flow-aware step strategy"):
        _turn_policy_schedule(flow="burst").apply_due(
            step=0, engine=engine, game_masters=[], agents=[]
        )


def test_set_turn_policy_replays_on_resume() -> None:
    engine = _TurnPolicyEngine()
    _turn_policy_schedule().replay_persistent(
        start_step=3, engine=engine, game_masters=[], agents=[]
    )
    assert type(engine.turn_policy).__name__ == "FixedCountTurnPolicy"


@pytest.mark.parametrize(
    "action,match",
    [
        ({"kind": "set_turn_policy"}, "requires a 'slot'"),
        (
            {
                "kind": "set_turn_policy",
                "slot": {"built_in": "single_action"},
                "flow": "a",
                "gm": "social",
            },
            "at most one of 'flow' or 'gm'",
        ),
        (
            {"kind": "set_turn_policy", "slot": {"built_in": "single_action"}, "gm": "ghost"},
            "unknown game master",
        ),
    ],
)
def test_set_turn_policy_validate_rejects_malformed(action: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        validate_interventions(
            OmegaConf.create({"interventions": [{"at_step": 0, "actions": [action]}]}),
            gm_names={"social"},
        )


# --------------------------------------------------------------- set_router


class _RouterStrategy:
    name = "multi_gm"

    def __init__(self, flow_chains: dict[str, Any]) -> None:
        self.flow_chains = flow_chains


def _router_schedule(**extra: Any) -> InterventionSchedule:
    action: dict[str, Any] = {"kind": "set_router", "slot": {"built_in": "random"}}
    action.update(extra)
    return InterventionSchedule.parse(
        OmegaConf.create({"interventions": [{"at_step": 0, "actions": [action]}]})
    )


def _branch() -> Any:
    from silisocs.simulation_engines.policies.routers import BranchSpec

    return BranchSpec(choices=("gm_a", "gm_b"), router_slot={}, router=lambda *a: {})


def test_set_router_repoints_branch_node() -> None:
    from silisocs.simulation_engines.policies.routers import BranchSpec, RandomChoiceRouter

    branch = _branch()
    strategy = _RouterStrategy({"pick": ["gm_pre", branch]})
    engine = _TurnPolicyEngine(step_strategy=strategy)
    _router_schedule(flow="pick").apply_due(step=0, engine=engine, game_masters=[], agents=[])
    new_branch = strategy.flow_chains["pick"][1]
    assert isinstance(new_branch, BranchSpec)
    assert new_branch.router is not branch.router  # re-pointed
    assert isinstance(new_branch.router, RandomChoiceRouter)
    assert new_branch.choices == ("gm_a", "gm_b")  # choices preserved


def test_set_router_replays_on_resume() -> None:
    from silisocs.simulation_engines.policies.routers import RandomChoiceRouter

    strategy = _RouterStrategy({"pick": [_branch()]})
    engine = _TurnPolicyEngine(step_strategy=strategy)
    _router_schedule(flow="pick").replay_persistent(
        start_step=2, engine=engine, game_masters=[], agents=[]
    )
    assert isinstance(strategy.flow_chains["pick"][0].router, RandomChoiceRouter)


def test_set_router_unknown_flow_raises() -> None:
    strategy = _RouterStrategy({"pick": [_branch()]})
    engine = _TurnPolicyEngine(step_strategy=strategy)
    with pytest.raises(ValueError, match="no branch chain for flow"):
        _router_schedule(flow="ghost").apply_due(step=0, engine=engine, game_masters=[], agents=[])


def test_set_router_no_branch_in_chain_raises() -> None:
    strategy = _RouterStrategy({"pick": ["gm_a", "gm_b"]})  # a plain (non-branch) chain
    engine = _TurnPolicyEngine(step_strategy=strategy)
    with pytest.raises(ValueError, match="no branch node"):
        _router_schedule(flow="pick").apply_due(step=0, engine=engine, game_masters=[], agents=[])


@pytest.mark.parametrize(
    "action,match",
    [
        ({"kind": "set_router", "flow": "pick"}, "requires a 'slot'"),
        ({"kind": "set_router", "slot": {"built_in": "random"}}, "non-empty 'flow'"),
    ],
)
def test_set_router_validate_rejects_malformed(action: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        validate_interventions(
            OmegaConf.create({"interventions": [{"at_step": 0, "actions": [action]}]}),
            gm_names={"social"},
        )


# --------------------------------------------------------------- swap_component


class _SwapGM:
    def __init__(self, name: str = "social") -> None:
        self.name = name
        self.rebuilt: list[tuple[str, dict[str, Any]]] = []

    def rebuild_component(self, role: str, slot: Any) -> None:
        self.rebuilt.append((role, dict(slot)))


def _swap_schedule(**extra: Any) -> InterventionSchedule:
    action: dict[str, Any] = {
        "kind": "swap_component",
        "role": "observe",
        "slot": {"built_in": "episode_only"},
    }
    action.update(extra)
    return InterventionSchedule.parse(
        OmegaConf.create({"interventions": [{"at_step": 0, "actions": [action]}]})
    )


def test_swap_component_dispatches_to_gm_rebuild() -> None:
    gm = _SwapGM()
    _swap_schedule().apply_due(step=0, engine=_Engine(), game_masters=[gm], agents=[])
    assert gm.rebuilt == [("observe", {"built_in": "episode_only"})]


def test_swap_component_replays_on_resume() -> None:
    gm = _SwapGM()
    _swap_schedule().replay_persistent(start_step=2, engine=_Engine(), game_masters=[gm], agents=[])
    assert gm.rebuilt == [("observe", {"built_in": "episode_only"})]


def test_swap_component_requires_rebuild_capable_gm() -> None:
    # A GM stub without rebuild_component (the recsys _GM) is rejected cleanly.
    with pytest.raises(ValueError, match="does not support component hot-swap"):
        _swap_schedule().apply_due(step=0, engine=_Engine(), game_masters=[_GM()], agents=[])


@pytest.mark.parametrize(
    "action,match",
    [
        (
            {"kind": "swap_component", "role": "resolve", "slot": {"built_in": "tool_calling"}},
            "must be one of",
        ),
        ({"kind": "swap_component", "role": "observe"}, "requires a 'slot'"),
        (
            {"kind": "swap_component", "role": "observe", "slot": {"bogus": 1}},
            "Unsupported config key",
        ),
    ],
)
def test_swap_component_validate_rejects_malformed(action: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        validate_interventions(
            OmegaConf.create({"interventions": [{"at_step": 0, "actions": [action]}]}),
            gm_names={"social"},
        )


# --------------------------------------------------------------- inject_post / broadcast


def test_inject_post_resolves_action_on_gm() -> None:
    gm = _GM(backend_type="twitter_like")
    sched = InterventionSchedule.parse(
        OmegaConf.create(
            {
                "interventions": [
                    {
                        "at_step": 7,
                        "actions": [
                            {"kind": "inject_post", "author": "NewsBot", "text": "breaking"}
                        ],
                    }
                ]
            }
        )
    )
    sched.apply_due(step=7, engine=_Engine(), game_masters=[gm], agents=[])
    assert len(gm.resolved) == 1
    author, action, episode = gm.resolved[0]
    assert author == "NewsBot"
    # The typed tool call carries the post text.
    assert "breaking" in str(action.tool_calls[0].arguments)
    # The injected action is stamped with THIS step's episode (interventions fire
    # before run_step sets the episode index).
    assert episode == 7
    assert gm.backend.exposure_logger.episode_idx == 7


def test_inject_action_resolves_arbitrary_tool_call() -> None:
    gm = _GM()
    sched = InterventionSchedule.parse(
        OmegaConf.create(
            {
                "interventions": [
                    {
                        "at_step": 4,
                        "actions": [
                            {
                                "kind": "inject_action",
                                "agent": "Alice",
                                "action": "follow_user",
                                "args": {"target_username": "bob"},
                            }
                        ],
                    }
                ]
            }
        )
    )
    sched.apply_due(step=4, engine=_Engine(), game_masters=[gm], agents=[])
    agent, action, episode = gm.resolved[0]
    assert agent == "Alice"
    call = action.tool_calls[0]
    assert call.name == "follow_user"
    assert call.arguments == {"target_username": "bob"}
    assert episode == 4  # stamped like inject_post


def test_ctx_stamp_episode_covers_all_loggers() -> None:
    """Custom handlers stamp via the public ctx seam — all three loggers, right step.

    Regression: the old private helper stamped only action/exposure loggers, and
    custom handlers had no seam at all (they inherited the previous step's index).
    """
    gm = _GM()
    ctx = _ctx(_Engine(), [gm], [], step=9)
    ctx.stamp_episode(gm)
    assert gm.backend.action_logger.episode_idx == 9
    assert gm.backend.exposure_logger.episode_idx == 9
    assert gm.backend.harness_logger.episode_idx == 9


def test_ctx_resolve_action_stamps_then_resolves() -> None:
    gm = _GM()
    ctx = _ctx(_Engine(), [gm], [], step=3)
    output = ActionOutput.from_tool_calls([ToolCall("do_nothing", {})])
    ctx.resolve_action(gm, "Alice", output)
    agent, action, episode = gm.resolved[0]
    assert agent == "Alice"
    assert action is output
    assert episode == 3  # stamped BEFORE resolve so the logged row carries this step


@pytest.mark.parametrize(
    "action,match",
    [
        ({"kind": "inject_action", "action": "follow_user"}, "non-empty 'agent'"),
        ({"kind": "inject_action", "agent": "Alice"}, "non-empty 'action'"),
        (
            {"kind": "inject_action", "agent": "A", "action": "x", "args": [1]},
            "'args' must be a mapping",
        ),
        (
            {"kind": "inject_post", "author": "A", "text": "hi", "action_mapping": "nope"},
            "'action_mapping' must map",
        ),
    ],
)
def test_inject_validate_rejects_malformed(action: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        validate_interventions(
            OmegaConf.create({"interventions": [{"at_step": 0, "actions": [action]}]}),
            gm_names=set(),
        )


def test_inject_post_action_mapping_unlocks_custom_backend() -> None:
    gm = _GM(backend_type="my_custom")
    sched = InterventionSchedule.parse(
        OmegaConf.create(
            {
                "interventions": [
                    {
                        "at_step": 0,
                        "actions": [
                            {
                                "kind": "inject_post",
                                "author": "NewsBot",
                                "text": "breaking",
                                "action_mapping": {
                                    "my_custom": {
                                        "tool_name": "make_post",
                                        "arguments": {"body": "{post_text}"},
                                    }
                                },
                            }
                        ],
                    }
                ]
            }
        )
    )
    sched.apply_due(step=0, engine=_Engine(), game_masters=[gm], agents=[])
    call = gm.resolved[0][1].tool_calls[0]
    assert call.name == "make_post"
    assert call.arguments == {"body": "breaking"}


def test_broadcast_targets_subset_and_all() -> None:
    agents = [_Agent("A"), _Agent("B"), _Agent("C")]
    subset = InterventionSchedule.parse(
        OmegaConf.create(
            {
                "interventions": [
                    {
                        "at_step": 0,
                        "actions": [
                            {"kind": "broadcast_observation", "text": "psst", "agents": ["B"]}
                        ],
                    }
                ]
            }
        )
    )
    subset.apply_due(step=0, engine=_Engine(), game_masters=[], agents=agents)
    assert agents[0].observations == [] and agents[1].observations == ["psst"]

    every = InterventionSchedule.parse(
        OmegaConf.create(
            {
                "interventions": [
                    {"at_step": 0, "actions": [{"kind": "broadcast_observation", "text": "all"}]}
                ]
            }
        )
    )
    every.apply_due(step=0, engine=_Engine(), game_masters=[], agents=agents)
    assert all("all" in a.observations for a in agents)


# --------------------------------------------------------------- resume replay


def test_replay_persistent_only_before_start_step() -> None:
    """Persistent actions with at_step < start_step re-apply; one-shots do not."""
    gm = _GM()
    agents = [_Agent("A"), _Agent("B")]
    engine = _Engine(AllParticipation())
    sched = InterventionSchedule.parse(
        OmegaConf.create(
            {
                "interventions": [
                    {"at_step": 2, "actions": [{"kind": "ban_agents", "agents": ["B"]}]},
                    {
                        "at_step": 3,
                        "actions": [{"kind": "inject_post", "author": "A", "text": "x"}],
                    },
                    {"at_step": 6, "actions": [{"kind": "ban_agents", "agents": ["A"]}]},
                ]
            }
        )
    )
    # Resume at step 5: steps 2 and 3 already ran (ban B persistent → replay;
    # inject one-shot → NOT replayed); step 6 is in the future.
    sched.replay_persistent(start_step=5, engine=engine, game_masters=[gm], agents=agents)
    assert isinstance(engine.participation, BanFilterParticipation)
    assert engine.participation.banned == {"B"}
    assert gm.resolved == [], "one-shot inject_post must not be replayed on resume"


def test_replay_is_noop_on_fresh_run() -> None:
    engine = _Engine(AllParticipation())
    sched = InterventionSchedule.parse(
        OmegaConf.create(
            {
                "interventions": [
                    {"at_step": 0, "actions": [{"kind": "ban_agents", "agents": ["A"]}]}
                ]
            }
        )
    )
    sched.replay_persistent(start_step=0, engine=engine, game_masters=[], agents=[])
    assert isinstance(engine.participation, AllParticipation)


# --------------------------------------------------------------- custom handler


class _RecordingHandler(InterventionHandler):
    kind = "custom"
    persistent = True
    calls: list[str] = []

    def __init__(self, *, tag: str = "x") -> None:
        self.tag = tag

    def apply(self, action: Any, ctx: InterventionContext) -> None:
        _RecordingHandler.calls.append(self.tag)


def test_custom_handler_loads_and_fires() -> None:
    _RecordingHandler.calls.clear()
    sched = InterventionSchedule.parse(
        OmegaConf.create(
            {
                "interventions": [
                    {
                        "at_step": 0,
                        "actions": [
                            {
                                "kind": "custom",
                                "class_path": f"{__name__}._RecordingHandler",
                                "params": {"tag": "boom"},
                            }
                        ],
                    }
                ]
            }
        )
    )
    sched.apply_due(step=0, engine=_Engine(), game_masters=[], agents=[])
    assert _RecordingHandler.calls == ["boom"]


def test_validate_catches_bad_gm_on_gm_name_orchestration() -> None:
    """Regression: multi-GM validation must resolve names from the 'gm_name' key."""
    from silisocs.runtime.configuration.validation import _declared_gm_names

    cfg = OmegaConf.create(
        {"env": {"gm_orchestration": {"gms": [{"gm_name": "world"}, {"gm_name": "social"}]}}}
    )
    assert _declared_gm_names(cfg) == {"world", "social"}
    with pytest.raises(ValueError, match="unknown game master"):
        validate_interventions(
            OmegaConf.create(
                {
                    "interventions": [
                        {
                            "at_step": 1,
                            "actions": [{"kind": "set_recsys", "recsys_type": "x", "gm": "socail"}],
                        }
                    ]
                }
            ),
            gm_names=_declared_gm_names(cfg),
        )


def test_set_recsys_unlatches_disabled_update_component() -> None:
    """Regression: enabling a recsys mid-run must clear a latched _recsys_disabled."""
    from silisocs.environments.gm.components.social_media.update import (
        SocialRecommendationUpdateComponent,
    )

    component = SocialRecommendationUpdateComponent(backend_type="twitter_like")
    component._recsys_disabled = True  # simulate "started with no recsys configured"
    component.set_recsys_type("twhin")
    assert component.default_recsys_type == "twhin"
    assert component._recsys_disabled is False


def test_custom_handler_must_subclass() -> None:
    with pytest.raises(ValueError, match="must subclass InterventionHandler"):
        InterventionSchedule.parse(
            OmegaConf.create(
                {
                    "interventions": [
                        {
                            "at_step": 0,
                            "actions": [{"kind": "custom", "class_path": "builtins.dict"}],
                        }
                    ]
                }
            )
        )
