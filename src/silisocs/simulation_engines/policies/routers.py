"""Branch routers: decide which GM each of a flow's agents acts on at a branch node.

A flow chain (``env.gm_orchestration.flow_bindings.flow_to_gms``) may contain a
``{branch: {router, choices}}`` node. When the flow's chain reaches that stage, the
engine calls the branch's *router* once with the flow's agents and the candidate GMs,
then runs each agent's turn on the GM the router assigned it.

A router is any callable with this signature — no base class, no registration
(the shape is formalized by the structural :class:`Router` Protocol)::

    def route(agents, gms, ctx):
        '''Assign each agent to one GM.

        agents: the flow's agent objects at this stage; call ``agent.act(...)``
            or ``agent.observe(...)`` freely to involve the agent in the choice.
        gms: {gm name -> game master}, one entry per branch choice in config
            order; read live backend state (``gm.backend``) freely.
        ctx: RouteInfo(flow, step, seed) — the stable inputs for a
            deterministic, replay-friendly decision.
        Returns {agent name -> chosen gm name}, covering every agent.
        '''
        first = next(iter(gms))
        return {agent.name: first for agent in agents}

Select it with ``router: {class_path: mypkg.route}`` — a plain function (``params``,
if given, are bound as keyword arguments) or a class instantiated with ``params``
whose instances are callable. The engine owns everything else: the router runs when
the flow's chain reaches the branch (after its earlier hops have drained, so backend
state reflects them; under ``multi_gm_staged``, after the prior stage's barrier),
turn selection on each chosen GM is serialized against concurrent turns, and the
returned assignment is validated (every agent covered, every GM a real choice).

Built-ins: ``random`` (:class:`RandomChoiceRouter` — weighted, deterministic per
``(seed, flow, step, agent)``) and ``agent_choice`` (:class:`AgentChoiceRouter` —
asks each agent to pick a GM via a CHOICE action spec).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from silisocs.agents.base_agent import Agent
from silisocs.runtime.types import ActionOutput, ActionSpec, OutputType
from silisocs.simulation_engines.policies._rng import stable_rng

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class RouteInfo:
    """The stable, replay-friendly inputs to a routing decision.

    Everything live (agents, GMs) is passed to the router directly; this carries only
    the identifying scalars a router needs to make a decision that reproduces across
    runs: the flow being routed, the step index, and the run seed.
    """

    flow: str
    step: int
    seed: int


class Router(Protocol):
    """The router contract, as a structural :class:`~typing.Protocol`.

    Any callable matching this signature conforms — a plain function, a
    ``functools.partial``, or a class instance with ``__call__``. Nothing inherits
    from it; it exists so the signature is named, importable, and type-checked.
    The parameters are positional-only, so a conforming function may name its
    arguments whatever it likes.
    """

    def __call__(
        self, agents: Sequence[Agent], gms: Mapping[str, Any], ctx: RouteInfo, /
    ) -> Mapping[str, str]:
        """Assign each agent to exactly one GM.

        ``agents`` are the flow's agent objects at this stage (call ``agent.act(...)``
        freely); ``gms`` maps GM name -> game master, one entry per branch choice in
        config order (read ``gm.backend`` freely); ``ctx`` carries the stable scalars
        (flow, step, seed) for a replay-stable decision. Returns
        ``{agent name -> chosen gm name}``, covering every agent.
        """
        ...


def match_choice(answer: str, choices: Sequence[str]) -> str | None:
    """Match a free-text answer to one choice: exact, case-insensitive, then
    contained-exactly-once. Returns ``None`` when the answer names no single choice.

    Used by :class:`AgentChoiceRouter`; importable by custom routers that ask agents
    their own questions and want the same lenient matching.
    """
    text = answer.strip()
    if not text:
        return None
    for choice in choices:
        if text == choice:
            return choice
    lowered = text.lower()
    for choice in choices:
        if lowered == choice.lower():
            return choice
    contained = [c for c in choices if c.lower() in lowered]
    return contained[0] if len(contained) == 1 else None


def _deterministic_choice(
    *, seed: int, flow: str, step: int, agent: str, choices: Sequence[str]
) -> str:
    """A replay-stable pick from ``choices`` (local RNG, never the global one)."""
    # Distinct routing-fallback key salt; shares only the digest→Random mechanic.
    return stable_rng(f"{seed}|{flow}|{step}|{agent}|routing-fallback").choice(list(choices))


@dataclass
class RandomChoiceRouter:
    """Weighted random GM pick (built-in ``random``).

    ``weights`` maps a GM name to a relative weight; any choice absent from the map
    weighs ``1.0``. Each agent's pick is deterministic per ``(seed, flow, step,
    agent)`` — so a run reproduces and replays identically — and uses a local RNG
    (never the global one), so concurrent routing never perturbs other RNG consumers.
    """

    weights: Mapping[str, float] = field(default_factory=dict)
    name: str = "random"

    def __call__(
        self, agents: Sequence[Agent], gms: Mapping[str, Any], ctx: RouteInfo
    ) -> dict[str, str]:
        choices = list(gms)
        weights = [max(0.0, float(self.weights.get(choice, 1.0))) for choice in choices]
        if sum(weights) <= 0.0:
            weights = [1.0] * len(choices)
        route: dict[str, str] = {}
        for agent in agents:
            # RandomChoiceRouter's own (seed, flow, step, agent) key — no shared salt.
            rng = stable_rng(f"{ctx.seed}|{ctx.flow}|{ctx.step}|{agent.name}")
            route[agent.name] = rng.choices(choices, weights=weights, k=1)[0]
        return route


@dataclass
class AgentChoiceRouter:
    """Each agent picks its own GM via a choice probe (built-in ``agent_choice``).

    Asks every agent a CHOICE-type action spec whose options are the branch's GM
    names, matches the answer with :func:`match_choice`, and applies ``on_invalid``
    when the answer names no single choice. This is the reference for agent-driven
    routing — a custom router wanting a different question or interpretation just
    calls ``agent.act(...)`` itself however it likes.

    ``prompt`` placeholders (``str.format``): ``{choices}``, ``{flow}``, ``{step}``,
    ``{agent}``. ``on_invalid`` is ``random`` (replay-stable pick, default),
    ``first``, or ``raise``.
    """

    prompt: str = "You may act on one of: {choices}. Reply with exactly one of those names."
    on_invalid: str = "random"
    name: str = "agent_choice"

    def __post_init__(self) -> None:
        if self.on_invalid not in {"random", "first", "raise"}:
            raise ValueError(
                f"AgentChoiceRouter.on_invalid must be random|first|raise, got '{self.on_invalid}'."
            )

    def __call__(
        self, agents: Sequence[Agent], gms: Mapping[str, Any], ctx: RouteInfo
    ) -> dict[str, str]:
        choices = tuple(gms)
        return {agent.name: self._route_one(agent, choices, ctx) for agent in agents}

    def _render(self, agent_name: str, choices: Sequence[str], ctx: RouteInfo) -> str:
        try:
            return self.prompt.format(
                choices=", ".join(choices), flow=ctx.flow, step=ctx.step, agent=agent_name
            )
        except (KeyError, IndexError) as exc:
            raise ValueError(
                f"AgentChoiceRouter.prompt has an unknown placeholder {exc}; "
                "valid placeholders are {choices}, {flow}, {step}, {agent}."
            ) from exc

    def _route_one(self, agent: Agent, choices: tuple[str, ...], ctx: RouteInfo) -> str:
        spec = ActionSpec(
            prompt=self._render(agent.name, choices, ctx),
            output_type=OutputType.CHOICE,
            options=choices,
            tag="routing",
        )
        # Bracket the ask in the model's "routing" telemetry context when available,
        # so routing calls stay distinguishable from action-phase calls.
        model = getattr(agent, "model", None)
        set_ctx = getattr(model, "set_runtime_context", None)
        clear_ctx = getattr(model, "clear_runtime_context", None)
        if callable(set_ctx):
            set_ctx(
                agent_name=agent.name, episode_idx=ctx.step, phase="routing", action_tag="routing"
            )
        try:
            # A custom agent's act() may return a str or an ActionOutput; treat it as
            # duck-typed (Any) so both cases stay reachable.
            result: Any = agent.act(spec)
        finally:
            if callable(clear_ctx):
                clear_ctx()
        if isinstance(result, ActionOutput):
            answer = str(result.choice or result.text or "")
        else:
            answer = str(result or "")
        matched = match_choice(answer, choices)
        if matched is not None:
            return matched
        _LOGGER.warning(
            "Routing: agent '%s' returned unusable choice %r for flow '%s' "
            "(choices=%s); on_invalid=%s",
            agent.name,
            answer,
            ctx.flow,
            list(choices),
            self.on_invalid,
        )
        if self.on_invalid == "raise":
            raise ValueError(
                f"Agent '{agent.name}' returned invalid routing choice '{answer}' "
                f"(choices: {list(choices)})."
            )
        if self.on_invalid == "first":
            return choices[0]
        return _deterministic_choice(
            seed=ctx.seed, flow=ctx.flow, step=ctx.step, agent=agent.name, choices=choices
        )


@dataclass(frozen=True)
class BranchSpec:
    """A resolved ``{branch: ...}`` chain node: one chain stage where each of a flow's
    agents is routed (by ``router``) to exactly one of ``choices``.

    Config resolution produces it with ``router=None`` (only ``router_slot`` is known
    then); the engine builds the router and fills it in before the strategy runs.
    Consumers that only need the candidate GMs — per-GM owned-flow derivation and the
    restore chain view — read ``choices`` and ignore the router.
    """

    choices: tuple[str, ...]
    router_slot: Mapping[str, Any] = field(default_factory=dict)
    router: Router | None = None
