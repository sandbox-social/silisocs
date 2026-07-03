"""Tests for branch routing between GMs (``{branch: {router, choices}}`` chain nodes).

Covers the router primitive (weighted-random determinism), config parsing/validation
of branch nodes, the engine-build guards (mode/flow_order/non-pure-router), and the
end-to-end fan-out under all three multi-GM traversals — including that a branch is a
single stage resolved before execution, so the router "acts first" under the staged
barrier and shared pre/post GMs run once on every agent.
"""

from __future__ import annotations

import pytest
from omegaconf import DictConfig, OmegaConf

from silisocs.runtime.construction.engines import build_engine
from silisocs.runtime.construction.game_masters import (
    _parse_branch,
    _resolve_flow_chains,
    collapse_flow_chains,
    flow_chain_gm_names,
)
from silisocs.runtime.types import ActionOutput, ActionSpec, OutputType
from silisocs.simulation_engines.policies.factory import build_router
from silisocs.simulation_engines.policies.routers import (
    BranchSpec,
    RandomChoiceRouter,
    RouteContext,
    Router,
)
from silisocs.simulation_engines.policies.steps import (
    MultiGMSerialStepStrategy,
    MultiGMStagedStepStrategy,
    MultiGMStepStrategy,
)

_BUILT_IN_BY_MODE = {
    "concurrent": MultiGMStepStrategy,
    "serial": MultiGMSerialStepStrategy,
    "staged": MultiGMStagedStepStrategy,
}


# --------------------------------------------------------------------------- routers


def _ctx(agent: str, *, choices: tuple[str, ...], step: int = 0, seed: int = 1) -> RouteContext:
    return RouteContext(
        agent_name=agent, flow_name="social", step_index=step, seed=seed, choices=choices
    )


def test_random_router_is_deterministic_per_agent_step_seed() -> None:
    router = RandomChoiceRouter()
    ctx = _ctx("Alice", choices=("tw", "rd"))
    assert router.route(ctx) == router.route(ctx)  # same inputs -> same pick


def test_random_router_respects_zero_weight() -> None:
    router = RandomChoiceRouter(weights={"tw": 1.0, "rd": 0.0})
    picks = {router.route(_ctx(f"agent{i}", choices=("tw", "rd"))) for i in range(50)}
    assert picks == {"tw"}  # rd has zero weight, never chosen


def test_random_router_returns_a_listed_choice() -> None:
    router = RandomChoiceRouter()
    for i in range(50):
        assert router.route(_ctx(f"a{i}", choices=("tw", "rd", "mast"))) in {"tw", "rd", "mast"}


def test_random_router_all_zero_weights_falls_back_to_uniform() -> None:
    router = RandomChoiceRouter(weights={"tw": 0.0, "rd": 0.0})
    picks = {router.route(_ctx(f"a{i}", choices=("tw", "rd"))) for i in range(50)}
    assert picks == {"tw", "rd"}  # degenerate weights -> uniform, both reachable


def test_build_router_default_and_class_path() -> None:
    assert build_router(None).name == "random"
    assert isinstance(build_router({"built_in": "random"}), RandomChoiceRouter)
    custom = build_router({"class_path": f"{__name__}._ByNameRouter"})
    assert isinstance(custom, _ByNameRouter)


# ------------------------------------------------------------------- config parsing


def _gm_specs(*names_and_sequences: tuple[str, int]) -> list[dict[str, object]]:
    return [{"gm_name": name, "sequence": seq} for name, seq in names_and_sequences]


def _cfg_with_chain(chain: object) -> DictConfig:
    return OmegaConf.create(
        {"env": {"gm_orchestration": {"flow_bindings": {"flow_to_gms": {"social": chain}}}}}
    )


def test_parse_branch_builds_spec() -> None:
    spec = _parse_branch(
        "social",
        {"branch": {"router": {"built_in": "random"}, "choices": ["tw", "rd"]}},
        {"tw", "rd"},
    )
    assert spec.choices == ("tw", "rd")
    assert spec.router_slot == {"built_in": "random"}
    assert spec.router is None  # built later by the engine


@pytest.mark.parametrize(
    "branch, match",
    [
        ({"branch": {"choices": ["tw"]}}, "at least 2 choices"),
        ({"branch": {"choices": ["tw", "tw"]}}, "duplicate choices"),
        ({"branch": {"choices": ["tw", "ghost"]}}, "unknown GM"),
        ({"not_branch": {}}, "must be a GM name"),
    ],
)
def test_parse_branch_rejects_bad_shapes(branch: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        _parse_branch("social", branch, {"tw", "rd"})


def test_resolve_chain_with_branch() -> None:
    cfg = _cfg_with_chain(
        ["seed", {"branch": {"router": {"built_in": "random"}, "choices": ["tw", "rd"]}}, "wrap"]
    )
    chains = _resolve_flow_chains(
        cfg, _gm_specs(("seed", 0), ("tw", 1), ("rd", 1), ("wrap", 2)), set()
    )
    chain = chains["social"]
    assert chain[0] == "seed"
    assert isinstance(chain[1], BranchSpec) and chain[1].choices == ("tw", "rd")
    assert chain[2] == "wrap"


def test_resolve_rejects_two_branches() -> None:
    cfg = _cfg_with_chain(
        [
            {"branch": {"choices": ["tw", "rd"]}},
            {"branch": {"choices": ["tw", "rd"]}},
        ]
    )
    with pytest.raises(ValueError, match="at most one branch"):
        _resolve_flow_chains(cfg, _gm_specs(("tw", 1), ("rd", 1)), set())


def test_resolve_rejects_branch_choice_sequence_outside_band() -> None:
    # 'wrap' (seq 5) sits BEFORE the branch whose choices are seq 3 -> not strictly
    # increasing band-to-band.
    cfg = _cfg_with_chain(["wrap", {"branch": {"choices": ["tw", "rd"]}}])
    with pytest.raises(ValueError, match="strictly serial by sequence"):
        _resolve_flow_chains(cfg, _gm_specs(("tw", 3), ("rd", 3), ("wrap", 5)), set())


def test_resolve_rejects_duplicate_gm_across_branch_and_chain() -> None:
    cfg = _cfg_with_chain(["tw", {"branch": {"choices": ["tw", "rd"]}}])
    with pytest.raises(ValueError, match="duplicate GMs"):
        _resolve_flow_chains(cfg, _gm_specs(("tw", 1), ("rd", 2), ("foo", 3)), set())


def test_flow_chain_gm_names_includes_branch_choices() -> None:
    chain = ["seed", BranchSpec(choices=("tw", "rd")), None, "wrap"]
    assert flow_chain_gm_names(chain) == ["seed", "tw", "rd", "wrap"]


def test_collapse_flow_chains_spreads_branches() -> None:
    chains = {"social": ["seed", BranchSpec(choices=("tw", "rd")), "wrap"]}
    assert collapse_flow_chains(chains) == {"social": ["seed", "tw", "rd", "wrap"]}


# ----------------------------------------------------------------- engine-build guards


def _cfg(mode: str, *, flow_order: list[str] | None = None, seed: int = 0) -> DictConfig:
    params: dict[str, object] = {}
    if flow_order is not None:
        params["flow_order"] = flow_order
    return OmegaConf.create(
        {
            "seed": seed,
            "sim": {"engine": {"step": {"built_in": mode, "params": params}}},
        }
    )


def test_branch_requires_multi_gm_mode() -> None:
    chains = {"social": [BranchSpec(choices=("tw", "rd"), router_slot={"built_in": "random"})]}
    with pytest.raises(ValueError, match="requires a multi_gm"):
        build_engine(_cfg("base"), flow_chains=chains)


def test_branch_rejected_in_flow_order_flow() -> None:
    chains = {"social": [BranchSpec(choices=("tw", "rd"), router_slot={"built_in": "random"})]}
    with pytest.raises(ValueError, match="flow_order and cannot contain a branch"):
        build_engine(_cfg("multi_gm", flow_order=["social"]), flow_chains=chains)


def test_non_pure_router_is_rejected() -> None:
    chains = {
        "social": [
            BranchSpec(choices=("tw", "rd"), router_slot={"class_path": f"{__name__}._LiveRouter"})
        ]
    }
    with pytest.raises(NotImplementedError, match="execution-time routing is not yet supported"):
        build_engine(_cfg("multi_gm"), flow_chains=chains)


def test_build_engine_builds_branch_router() -> None:
    chains = {"social": [BranchSpec(choices=("tw", "rd"), router_slot={"built_in": "random"})]}
    engine = build_engine(_cfg("multi_gm"), flow_chains=chains)
    branch = engine.step_strategy.flow_chains["social"][0]
    assert isinstance(branch.router, RandomChoiceRouter)
    assert engine.step_strategy.seed == 0


# ------------------------------------------------------------------ end-to-end fan-out


class _Agent:
    def __init__(self, name: str) -> None:
        self.name = name
        self.observations: list[str] = []
        self.actions: list[str] = []

    def observe(self, observation: str) -> None:
        self.observations.append(observation)

    def act(self, action_spec: ActionSpec) -> ActionOutput:
        self.actions.append(action_spec.prompt)
        return ActionOutput.from_text(f"{self.name}:{action_spec.prompt}")


class _GameMaster:
    def __init__(
        self,
        *,
        name: str,
        agent_flow_tags: dict[str, str] | None = None,
        log: list[str] | None = None,
    ) -> None:
        self.name = name
        self.agent_flow_tags = agent_flow_tags or {}
        self.resolved: list[str] = []
        self._log = log

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
        if self._log is not None:
            self._log.append(f"{self.name}:{agent_name}")
        return f"resolved:{self.name}:{agent_name}"


class _ByNameRouter(Router):
    """Deterministic test router: agents before 'M' take the first choice, else the second."""

    name = "by_name"

    def route(self, ctx: RouteContext) -> str:
        return ctx.choices[0] if ctx.agent_name < "M" else ctx.choices[1]


class _LiveRouter(Router):
    """Non-pure router used to exercise the v1 rejection seam."""

    reads_live_state = True

    def route(self, ctx: RouteContext) -> str:
        return ctx.choices[0]


def _branch_engine(mode: str, *, log: list[str] | None = None, seed: int = 0):
    """Build an engine whose multi-GM strategy routes 'social' through a branch.

    Chain: ``seed_gm -> branch{tw_gm, rd_gm} -> wrap_gm``. The router is the
    deterministic by-name router, so Alice routes to ``tw_gm`` and Zed to ``rd_gm``.
    """
    branch = BranchSpec(choices=("tw_gm", "rd_gm"), router=_ByNameRouter())
    flow_chains = {"social": ["seed_gm", branch, "wrap_gm"]}
    # The traversal is supplied by overwriting step_strategy below, so the engine is
    # built neutrally; the pre-built router survives (no _prepare_branch_chains pass).
    engine = build_engine(_cfg("base"))
    engine.step_strategy = _BUILT_IN_BY_MODE[mode](
        flow_order=("fixed_pre", "default"), flow_chains=flow_chains, seed=seed
    )
    flow_tags = {"Alice": "social", "Zed": "social"}
    primary = _GameMaster(name="primary", agent_flow_tags=flow_tags)
    gms = [primary] + [
        _GameMaster(name=n, log=log) for n in ("seed_gm", "tw_gm", "rd_gm", "wrap_gm")
    ]
    by_name = {gm.name: gm for gm in gms}
    return engine, gms, by_name


def test_branch_concurrent_fans_per_agent_and_shares_tail() -> None:
    engine, gms, by_name = _branch_engine("concurrent")
    engine.run_step(
        step_index=0, game_masters=gms, agents=[_Agent("Alice"), _Agent("Zed")], verbose=False
    )
    # Shared seed/wrap GMs run ONCE on every agent; the branch fans per agent.
    assert set(by_name["seed_gm"].resolved) == {"Alice", "Zed"}
    assert by_name["tw_gm"].resolved == ["Alice"]
    assert by_name["rd_gm"].resolved == ["Zed"]
    assert set(by_name["wrap_gm"].resolved) == {"Alice", "Zed"}


def test_branch_serial_flattens_into_one_chain() -> None:
    log: list[str] = []
    engine, gms, by_name = _branch_engine("serial", log=log)
    engine.run_step(
        step_index=0, game_masters=gms, agents=[_Agent("Alice"), _Agent("Zed")], verbose=False
    )
    # seed (both) -> branch (tw:Alice, rd:Zed) -> wrap (both), batch by batch.
    assert set(log[0:2]) == {"seed_gm:Alice", "seed_gm:Zed"}
    assert set(log[2:4]) == {"tw_gm:Alice", "rd_gm:Zed"}
    assert set(log[4:6]) == {"wrap_gm:Alice", "wrap_gm:Zed"}


def test_branch_staged_router_acts_first_with_aligned_stages() -> None:
    # The branch is a single stage resolved at materialization, so under the staged
    # barrier every seed turn precedes the branch turns, which precede every wrap turn.
    log: list[str] = []
    engine, gms, by_name = _branch_engine("staged", log=log)
    engine.run_step(
        step_index=0, game_masters=gms, agents=[_Agent("Alice"), _Agent("Zed")], verbose=False
    )
    assert set(log[0:2]) == {"seed_gm:Alice", "seed_gm:Zed"}
    assert set(log[2:4]) == {"tw_gm:Alice", "rd_gm:Zed"}
    assert set(log[4:6]) == {"wrap_gm:Alice", "wrap_gm:Zed"}


def test_random_router_fan_out_is_replay_stable() -> None:
    # Real weighted router, fresh engines/GMs twice: the per-GM assignment must be
    # identical (replay-stable) and both GMs reached (genuine fan-out).
    def _run() -> dict[str, list[str]]:
        branch = BranchSpec(choices=("tw_gm", "rd_gm"), router=RandomChoiceRouter())
        engine = build_engine(_cfg("multi_gm", seed=7))
        engine.step_strategy = MultiGMStepStrategy(flow_chains={"social": [branch]}, seed=7)
        names = [f"agent{i}" for i in range(12)]
        primary = _GameMaster(name="primary", agent_flow_tags=dict.fromkeys(names, "social"))
        tw = _GameMaster(name="tw_gm")
        rd = _GameMaster(name="rd_gm")
        engine.run_step(
            step_index=0,
            game_masters=[primary, tw, rd],
            agents=[_Agent(n) for n in names],
            verbose=False,
        )
        return {"tw_gm": sorted(tw.resolved), "rd_gm": sorted(rd.resolved)}

    first, second = _run(), _run()
    assert first == second
    assert first["tw_gm"] and first["rd_gm"]  # both branches actually used
