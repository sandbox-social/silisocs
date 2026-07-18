"""Declarative mid-run interventions.

An optional top-level ``interventions`` schedule fires actions at step
boundaries (after probes, before the step runs), so a config can express a
controlled experiment — swap the participation policy or recommender, ban
agents, inject a post or broadcast an observation — without a manual
checkpoint / edit / resume cycle.

Two action classes, distinguished by ``persistent``:

* **Persistent state-setters** (``set_participation`` / ``ban_agents`` /
  ``unban_agents`` / ``set_component_params`` / ``set_recsys`` /
  ``set_turn_policy`` / ``set_router`` / ``swap_component``) mutate live
  engine/component state that checkpoints do NOT capture, so on resume they are
  REPLAYED for every ``at_step < start_step``.
* **One-shot events** (``inject_action`` / ``inject_post`` /
  ``broadcast_observation``) land in checkpointed backend/agent state, so they
  fire only when ``step == at_step`` during the live loop and are never
  replayed.

Fired-ness is therefore a pure function of ``(schedule, step)`` — no per-run
"fired set" to persist and no checkpoint schema change. Custom kinds
(``kind: custom``, ``class_path`` to an :class:`InterventionHandler` subclass)
declare their own ``persistent`` flag and follow the same rules.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, ClassVar

from omegaconf import DictConfig, ListConfig, OmegaConf

from silisocs.initialization.simulation.seed_posts import build_seed_post_action
from silisocs.runtime.class_loading import load_class
from silisocs.runtime.types import ActionOutput, OutputType, ToolCall
from silisocs.simulation_engines.policies.factory import (
    build_participation_policy,
    build_router,
    build_turn_policy,
)
from silisocs.simulation_engines.policies.participation import ParticipationPolicy
from silisocs.simulation_engines.policies.routers import BranchSpec
from silisocs.simulation_engines.runtime_base import set_gm_episode_index

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Participation ban wrapper
# --------------------------------------------------------------------------- #


class BanFilterParticipation(ParticipationPolicy):
    """Wraps a participation policy, additionally excluding a fixed ban set.

    Composing (rather than replacing) the underlying policy is what lets
    ``set_participation`` and ``ban_agents`` interventions coexist: a ban keeps
    whatever activity model is active and just removes the banned names from its
    output. An emptied ban set is unwrapped back to the inner policy.
    """

    name = "ban_filter"

    def __init__(self, inner: Any, banned: Collection[str] = ()) -> None:
        self.inner = inner
        self.banned: set[str] = {str(n) for n in banned}

    def participating_agents(
        self, *, agent_names: Sequence[str], step_index: int, seed: int
    ) -> list[str]:
        base = (
            self.inner.participating_agents(
                agent_names=agent_names, step_index=step_index, seed=seed
            )
            if self.inner is not None
            else list(agent_names)
        )
        if not self.banned:
            return base
        return [name for name in base if name not in self.banned]


# --------------------------------------------------------------------------- #
# Handler interface + helpers
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class InterventionContext:
    """Live runtime handed to a handler at fire time (single-threaded boundary)."""

    engine: Any
    game_masters: Sequence[Any]
    agents: Sequence[Any]
    sim_roles: Mapping[str, str]
    step: int

    def stamp_episode(self, game_master: Any) -> None:
        """Stamp this intervention's step on the GM backend's event loggers.

        Interventions fire BEFORE ``run_step`` stamps the episode index, so a
        handler that logs backend events (directly or via ``resolve_action``)
        must stamp first — otherwise its rows land under the previous step's
        index (or step 0 on resume). Covers action, exposure, and harness
        loggers, same as the engine's per-step stamping.
        """
        set_gm_episode_index(game_master, self.step)

    def resolve_action(self, game_master: Any, agent_name: str, output: Any) -> None:
        """Resolve an injected action through the GM's resolve pipeline at this step.

        The resolve component is the generic action seam agents themselves speak
        (catalog validation + runtime actor injection), so injections reuse it
        rather than calling backend methods; the episode index is stamped first.
        """
        self.stamp_episode(game_master)
        game_master.resolve_action(agent_name, output)


class InterventionHandler(ABC):
    """One action kind. Stateless: the action config is passed to each method."""

    kind: ClassVar[str]
    persistent: ClassVar[bool]

    def validate(
        self,
        action: Mapping[str, Any],
        *,
        gm_names: Collection[str],
        flow_names: Collection[str] = (),
    ) -> None:
        """Raise ``ValueError`` on a malformed action config. Default: no-op."""

    @abstractmethod
    def apply(self, action: Mapping[str, Any], ctx: InterventionContext) -> None:
        """Apply the action to live runtime state."""


def _resolve_gm(action: Mapping[str, Any], ctx: InterventionContext) -> Any:
    name = action.get("gm")
    gms = list(ctx.game_masters)
    if name is None:
        if len(gms) != 1:
            raise ValueError(
                f"intervention needs an explicit 'gm' name ({len(gms)} game masters present)."
            )
        return gms[0]
    for gm in gms:
        if str(getattr(gm, "name", "")) == str(name):
            return gm
    raise ValueError(f"intervention references unknown game master '{name}'.")


def _validate_gm_target(action: Mapping[str, Any], gm_names: Collection[str]) -> None:
    if not gm_names:
        return  # GM names not declared in config; deferred to fire-time resolution.
    name = action.get("gm")
    if name is None:
        if len(gm_names) != 1:
            raise ValueError(
                f"this intervention needs an explicit 'gm' (declared: {sorted(gm_names)})."
            )
    elif str(name) not in gm_names:
        raise ValueError(
            f"intervention references unknown game master '{name}' (declared: {sorted(gm_names)})."
        )


def _validate_flow_target(action: Mapping[str, Any], flow_names: Collection[str]) -> None:
    if not flow_names:
        return  # Flow names not declared in config; deferred to fire-time resolution.
    flow = action.get("flow")
    if flow is not None and str(flow).strip() not in flow_names:
        raise ValueError(
            f"intervention references unknown flow '{flow}' (declared: {sorted(flow_names)})."
        )


def _require_name_list(action: Mapping[str, Any], key: str, kind: str) -> None:
    value = action.get(key)
    if not isinstance(value, (list, tuple)) or not value or not all(str(v).strip() for v in value):
        raise ValueError(f"{kind} requires a non-empty '{key}' list of agent names.")


def _gm_components(gm: Any) -> list[Any]:
    """Deduplicated component instances registered on a game master."""
    registry = getattr(gm, "components", {}) or {}
    seen: dict[int, Any] = {}
    for component in registry.values():
        seen.setdefault(id(component), component)
    return list(seen.values())


def _apply_component_params(gm: Any, params: Mapping[str, Any]) -> set[str]:
    """Dispatch runtime parameters across a GM's components; return applied names.

    The contract is ``component.set_params(params) -> applied names`` —
    ``BaseComponent`` implements it over the component's declared
    ``runtime_tunable`` set. A plain (non-``BaseComponent``) component
    participates by exposing ``set_<name>`` setters directly.
    """
    applied: set[str] = set()
    for component in _gm_components(gm):
        set_params = getattr(component, "set_params", None)
        if callable(set_params):
            applied.update(set_params(params))
            continue
        for name, value in params.items():
            setter = getattr(component, f"set_{name}", None)
            if callable(setter):
                setter(value)
                applied.add(name)
    return applied


def _ensure_ban_wrapper(engine: Any) -> BanFilterParticipation:
    current = engine.participation
    if isinstance(current, BanFilterParticipation):
        return current
    wrapper = BanFilterParticipation(current)
    engine.participation = wrapper
    return wrapper


# --------------------------------------------------------------------------- #
# Built-in handlers
# --------------------------------------------------------------------------- #


class _SetParticipation(InterventionHandler):
    kind = "set_participation"
    persistent = True

    def validate(
        self,
        action: Mapping[str, Any],
        *,
        gm_names: Collection[str],
        flow_names: Collection[str] = (),
    ) -> None:
        if not isinstance(action.get("slot"), Mapping):
            raise ValueError(
                "set_participation requires a 'slot' mapping ({built_in|class_path, params})."
            )

    def apply(self, action: Mapping[str, Any], ctx: InterventionContext) -> None:
        policy = build_participation_policy(dict(action["slot"]), sim_roles=ctx.sim_roles)
        current = ctx.engine.participation
        if isinstance(current, BanFilterParticipation):
            current.inner = policy  # keep the ban wrapper on top of the new policy
        else:
            ctx.engine.participation = policy


class _BanAgents(InterventionHandler):
    kind = "ban_agents"
    persistent = True

    def validate(
        self,
        action: Mapping[str, Any],
        *,
        gm_names: Collection[str],
        flow_names: Collection[str] = (),
    ) -> None:
        _require_name_list(action, "agents", "ban_agents")

    def apply(self, action: Mapping[str, Any], ctx: InterventionContext) -> None:
        _ensure_ban_wrapper(ctx.engine).banned.update(str(n) for n in action["agents"])


class _UnbanAgents(InterventionHandler):
    kind = "unban_agents"
    persistent = True

    def validate(
        self,
        action: Mapping[str, Any],
        *,
        gm_names: Collection[str],
        flow_names: Collection[str] = (),
    ) -> None:
        _require_name_list(action, "agents", "unban_agents")

    def apply(self, action: Mapping[str, Any], ctx: InterventionContext) -> None:
        current = ctx.engine.participation
        if isinstance(current, BanFilterParticipation):
            current.banned.difference_update(str(n) for n in action["agents"])
            if not current.banned:
                ctx.engine.participation = current.inner  # unwrap when empty


class _SetComponentParams(InterventionHandler):
    """Generic mid-run component retuning: any parameter a component declares.

    ``set_recsys`` is sugar over this same dispatch; new tunables need only a
    ``runtime_tunable`` declaration on the component — no new intervention kind.
    """

    kind = "set_component_params"
    persistent = True

    def validate(
        self,
        action: Mapping[str, Any],
        *,
        gm_names: Collection[str],
        flow_names: Collection[str] = (),
    ) -> None:
        params = action.get("params")
        if not isinstance(params, Mapping) or not params:
            raise ValueError("set_component_params requires a non-empty 'params' mapping.")
        _validate_gm_target(action, gm_names)

    def apply(self, action: Mapping[str, Any], ctx: InterventionContext) -> None:
        gm = _resolve_gm(action, ctx)
        params = dict(action["params"])
        missing = sorted(set(params) - _apply_component_params(gm, params))
        if missing:
            raise ValueError(
                f"set_component_params: no component of game master "
                f"'{getattr(gm, 'name', '')}' accepts parameter(s) {missing}; components "
                "declare mid-run tunables via 'runtime_tunable' (or a set_<name> setter)."
            )


class _SetRecsys(InterventionHandler):
    """Sugar for ``set_component_params`` with ``params: {recsys_type: ...}``."""

    kind = "set_recsys"
    persistent = True

    def validate(
        self,
        action: Mapping[str, Any],
        *,
        gm_names: Collection[str],
        flow_names: Collection[str] = (),
    ) -> None:
        if not str(action.get("recsys_type") or "").strip():
            raise ValueError("set_recsys requires a non-empty 'recsys_type'.")
        _validate_gm_target(action, gm_names)

    def apply(self, action: Mapping[str, Any], ctx: InterventionContext) -> None:
        gm = _resolve_gm(action, ctx)
        recsys_type = str(action["recsys_type"]).strip()
        if not _apply_component_params(gm, {"recsys_type": recsys_type}):
            raise ValueError(
                f"set_recsys: game master '{getattr(gm, 'name', '')}' has no recsys-aware "
                "components (none accepts a 'recsys_type' runtime parameter)."
            )


class _SetTurnPolicy(InterventionHandler):
    """Swap a turn policy mid-run: global, per-flow, or per-GM.

    Turn policies are config-only objects (no episodic state, no checkpoint
    footprint), so rebuilding one from its ``slot`` and re-pointing the map the
    scheduler reads per batch is replay-safe — the same move ``set_participation``
    makes. Scope is at most one of ``flow`` or ``gm``; with neither, the global
    default is swapped. Batch-time precedence is unchanged: per-flow > per-GM >
    global.
    """

    kind = "set_turn_policy"
    persistent = True

    def validate(
        self,
        action: Mapping[str, Any],
        *,
        gm_names: Collection[str],
        flow_names: Collection[str] = (),
    ) -> None:
        if not isinstance(action.get("slot"), Mapping):
            raise ValueError(
                "set_turn_policy requires a 'slot' mapping ({built_in|class_path, params})."
            )
        if action.get("flow") is not None and action.get("gm") is not None:
            raise ValueError("set_turn_policy accepts at most one of 'flow' or 'gm'.")
        if action.get("gm") is not None:
            _validate_gm_target(action, gm_names)
        _validate_flow_target(action, flow_names)

    def apply(self, action: Mapping[str, Any], ctx: InterventionContext) -> None:
        policy = build_turn_policy(dict(action["slot"]))
        flow = action.get("flow")
        if flow is not None:
            strategy = getattr(ctx.engine, "step_strategy", None)
            policies = getattr(strategy, "flow_turn_policies", None)
            if policies is None:
                raise ValueError(
                    "set_turn_policy 'flow' scope needs a flow-aware step strategy "
                    "(sim.engine.step.built_in of flow/multi_gm*); the active strategy "
                    f"'{getattr(strategy, 'name', '?')}' has no per-flow turn policies."
                )
            policies[str(flow).strip()] = policy
        elif action.get("gm") is not None:
            ctx.engine.gm_turn_policies[str(_resolve_gm(action, ctx).name)] = policy
        else:
            ctx.engine.turn_policy = policy


class _SetRouter(InterventionHandler):
    """Swap the router at a flow's branch node mid-run.

    A branch router is a stateless plain callable built from a ``{built_in|
    class_path, params}`` slot (see ``policies/routers.py``), so rebuilding it and
    re-pointing the frozen ``BranchSpec`` in the step strategy's ``flow_chains`` is
    replay-safe — the same move ``set_turn_policy`` makes for turn policies. Only
    meaningful under a ``multi_gm*`` step strategy whose chain for ``flow`` contains
    a branch (there is at most one, so ``flow`` identifies it unambiguously). The
    swap re-reads at the next step's branch stage (``_branch_hop``).
    """

    kind = "set_router"
    persistent = True

    def validate(
        self,
        action: Mapping[str, Any],
        *,
        gm_names: Collection[str],
        flow_names: Collection[str] = (),
    ) -> None:
        if not isinstance(action.get("slot"), Mapping):
            raise ValueError(
                "set_router requires a 'slot' mapping ({built_in|class_path, params})."
            )
        if not str(action.get("flow") or "").strip():
            raise ValueError(
                "set_router requires a non-empty 'flow' (the branch flow to re-point)."
            )
        _validate_flow_target(action, flow_names)

    def apply(self, action: Mapping[str, Any], ctx: InterventionContext) -> None:
        flow = str(action["flow"]).strip()
        strategy = getattr(ctx.engine, "step_strategy", None)
        chains = getattr(strategy, "flow_chains", None)
        if not isinstance(chains, Mapping) or flow not in chains:
            raise ValueError(
                f"set_router: no branch chain for flow '{flow}' on the active step strategy "
                f"'{getattr(strategy, 'name', '?')}' (branches need a multi_gm* strategy)."
            )
        chain = chains[flow]  # the resolved chain: a mutable list built by build_engine
        branch_index = next(
            (i for i, entry in enumerate(chain) if isinstance(entry, BranchSpec)), None
        )
        if branch_index is None:
            raise ValueError(f"set_router: flow '{flow}' has no branch node to re-point.")
        router = build_router(dict(action["slot"]))
        chain[branch_index] = replace(chain[branch_index], router=router)


# Roles this intervention may hot-swap. The GM's ``rebuild_component`` seam is the
# single source of truth (``_HOT_SWAP_ROLES`` in environments/gm/game_master.py) and
# re-validates the role at apply time; this list is only for the pre-fire config check
# here. Not imported from game_master to keep this module free of a GM import cycle —
# keep the two in sync if the swappable set changes.
_SWAPPABLE_COMPONENT_ROLES = ("observe", "next_acting", "update")


class _SwapComponent(InterventionHandler):
    """Hot-swap a STATELESS GM component (``observe`` | ``next_acting`` | ``update``).

    Delegates to the game master's ``rebuild_component`` seam, which rebuilds the
    component from its ``slot`` with the GM's live wiring and refuses the swap when
    either the outgoing or the freshly built incoming component carries non-empty
    ``get_state()`` — a stateful component (e.g. the recsys updater) is retuned via
    ``set_component_params`` instead, never replaced. ``resolve`` / ``action_prompt``
    are excluded (coupled to per-GM tool-calling compat); ``initialize`` is
    meaningless mid-run.
    """

    kind = "swap_component"
    persistent = True

    def validate(
        self,
        action: Mapping[str, Any],
        *,
        gm_names: Collection[str],
        flow_names: Collection[str] = (),
    ) -> None:
        role = str(action.get("role") or "").strip()
        if role not in _SWAPPABLE_COMPONENT_ROLES:
            raise ValueError(
                f"swap_component 'role' must be one of {list(_SWAPPABLE_COMPONENT_ROLES)} "
                f"(got {role!r}); resolve/action_prompt/initialize are not hot-swappable."
            )
        slot = action.get("slot")
        if not isinstance(slot, Mapping):
            raise ValueError(
                "swap_component requires a 'slot' mapping ({built_in|class_path, params})."
            )
        # Lazy import: configuration.validation imports THIS module for its own
        # intervention checks, so a top-level import here would be circular.
        from silisocs.runtime.configuration.validation import validate_component_slot_shape

        validate_component_slot_shape(slot, path="interventions.swap_component.slot")
        _validate_gm_target(action, gm_names)

    def apply(self, action: Mapping[str, Any], ctx: InterventionContext) -> None:
        gm = _resolve_gm(action, ctx)
        rebuild = getattr(gm, "rebuild_component", None)
        if not callable(rebuild):
            raise ValueError(
                f"swap_component: game master '{getattr(gm, 'name', '')}' does not support "
                "component hot-swap (no rebuild_component method)."
            )
        rebuild(str(action["role"]).strip(), dict(action["slot"]))


class _InjectAction(InterventionHandler):
    """Generic one-shot injection: any backend catalog action, invoked by name.

    The payload is a typed ``ToolCall`` — the same platform-neutral action
    language agents emit — resolved through the GM's resolve component.
    ``inject_post`` is sugar over this path for the common "post text" case.
    """

    kind = "inject_action"
    persistent = False

    def validate(
        self,
        action: Mapping[str, Any],
        *,
        gm_names: Collection[str],
        flow_names: Collection[str] = (),
    ) -> None:
        if not str(action.get("agent") or "").strip():
            raise ValueError("inject_action requires a non-empty 'agent'.")
        if not str(action.get("action") or "").strip():
            raise ValueError("inject_action requires a non-empty 'action' (backend action name).")
        args = action.get("args")
        if args is not None and not isinstance(args, Mapping):
            raise ValueError("inject_action 'args' must be a mapping of action arguments.")
        _validate_gm_target(action, gm_names)

    def apply(self, action: Mapping[str, Any], ctx: InterventionContext) -> None:
        gm = _resolve_gm(action, ctx)
        output = ActionOutput.from_tool_calls(
            [ToolCall(str(action["action"]).strip(), dict(action.get("args") or {}))]
        )
        ctx.resolve_action(gm, str(action["agent"]).strip(), output)


class _InjectPost(InterventionHandler):
    """Sugar for ``inject_action``: text -> the backend's canonical post action.

    The text-to-action mapping is the same declarative per-backend table the
    seed-post initializer uses; ``action_mapping`` extends it for custom
    backends (same schema as the initializer's ``action_mappings`` param).
    """

    kind = "inject_post"
    persistent = False

    def validate(
        self,
        action: Mapping[str, Any],
        *,
        gm_names: Collection[str],
        flow_names: Collection[str] = (),
    ) -> None:
        if not str(action.get("author") or "").strip():
            raise ValueError("inject_post requires a non-empty 'author'.")
        if not str(action.get("text") or "").strip():
            raise ValueError("inject_post requires non-empty 'text'.")
        mapping = action.get("action_mapping")
        if mapping is not None and not isinstance(mapping, Mapping):
            raise ValueError(
                "inject_post 'action_mapping' must map backend_type -> {tool_name, arguments}."
            )
        _validate_gm_target(action, gm_names)

    def apply(self, action: Mapping[str, Any], ctx: InterventionContext) -> None:
        gm = _resolve_gm(action, ctx)
        author = str(action["author"]).strip()
        backend_type = str(getattr(gm, "backend_type", "") or "").strip()
        if not backend_type:
            raise ValueError(
                f"inject_post: game master '{getattr(gm, 'name', '')}' has no backend_type."
            )
        output = build_seed_post_action(
            backend_type=backend_type,
            agent_name=author,
            post_text=str(action["text"]),
            context=getattr(ctx.engine, "_initialization_context", None),
            mapping_overrides=action.get("action_mapping"),
            default_subreddit=str(action.get("subreddit") or "general"),
        )
        if output.output_type == OutputType.SKIP:
            return
        ctx.resolve_action(gm, author, output)


class _BroadcastObservation(InterventionHandler):
    kind = "broadcast_observation"
    persistent = False

    def validate(
        self,
        action: Mapping[str, Any],
        *,
        gm_names: Collection[str],
        flow_names: Collection[str] = (),
    ) -> None:
        if not str(action.get("text") or "").strip():
            raise ValueError("broadcast_observation requires non-empty 'text'.")
        targets = action.get("agents")
        if targets is not None and not isinstance(targets, (list, tuple)):
            raise ValueError("broadcast_observation 'agents' must be a list (empty = all).")

    def apply(self, action: Mapping[str, Any], ctx: InterventionContext) -> None:
        text = str(action["text"])
        targets = {str(n) for n in (action.get("agents") or [])}
        for agent in ctx.agents:
            if not targets or agent.name in targets:
                agent.observe(text)


_BUILTIN_HANDLERS: dict[str, InterventionHandler] = {
    handler.kind: handler
    for handler in (
        _SetParticipation(),
        _BanAgents(),
        _UnbanAgents(),
        _SetComponentParams(),
        _SetRecsys(),
        _SetTurnPolicy(),
        _SetRouter(),
        _SwapComponent(),
        _InjectAction(),
        _InjectPost(),
        _BroadcastObservation(),
    )
}


def _resolve_handler(action: Mapping[str, Any]) -> InterventionHandler:
    kind = str(action.get("kind") or "").strip()
    if kind == "custom":
        class_path = str(action.get("class_path") or "").strip()
        if not class_path:
            raise ValueError("intervention kind 'custom' requires a 'class_path'.")
        cls = load_class(class_path)
        if not (isinstance(cls, type) and issubclass(cls, InterventionHandler)):
            raise ValueError(
                f"custom intervention '{class_path}' must subclass InterventionHandler."
            )
        return cls(**dict(action.get("params") or {}))
    if kind not in _BUILTIN_HANDLERS:
        raise ValueError(
            f"unknown intervention kind '{kind}'; available: "
            f"{sorted(_BUILTIN_HANDLERS)} or 'custom' with a class_path. "
            "Arbitrary config overrides are not hot-swappable."
        )
    return _BUILTIN_HANDLERS[kind]


# --------------------------------------------------------------------------- #
# Schedule
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Action:
    handler: InterventionHandler
    config: Mapping[str, Any]


@dataclass(frozen=True)
class _Intervention:
    at_step: int
    actions: list[_Action]


def _to_plain(node: Any) -> Any:
    if isinstance(node, (DictConfig, ListConfig)):
        return OmegaConf.to_container(node, resolve=True)
    return node


def _parse_interventions(raw: Any) -> list[_Intervention]:
    """Structurally validate + resolve handlers (raises on malformed config)."""
    raw = _to_plain(raw)
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raise ValueError("'interventions' must be a list of {at_step, actions} entries.")
    parsed: list[_Intervention] = []
    for raw_entry in raw:
        entry = _to_plain(raw_entry)
        if not isinstance(entry, Mapping):
            raise ValueError("each intervention must be a mapping with 'at_step' and 'actions'.")
        try:
            at_step = int(entry["at_step"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("intervention 'at_step' must be an integer.") from exc
        if at_step < 0:
            raise ValueError(f"intervention 'at_step' must be >= 0 (got {at_step}).")
        actions_raw = _to_plain(entry.get("actions"))
        if not isinstance(actions_raw, (list, tuple)) or not actions_raw:
            raise ValueError(f"intervention at step {at_step} needs a non-empty 'actions' list.")
        actions = [
            _Action(handler=_resolve_handler(_to_plain(a)), config=dict(_to_plain(a)))
            for a in actions_raw
        ]
        parsed.append(_Intervention(at_step=at_step, actions=actions))
    parsed.sort(key=lambda i: i.at_step)  # stable: same-step entries keep declaration order
    return parsed


class InterventionSchedule:
    """A parsed, replay-stable schedule of step-indexed interventions."""

    def __init__(
        self, interventions: list[_Intervention], sim_roles: Mapping[str, str] | None = None
    ) -> None:
        self._interventions = interventions
        self._sim_roles = dict(sim_roles or {})
        self._log: list[dict[str, Any]] = []

    def __bool__(self) -> bool:
        return bool(self._interventions)

    @classmethod
    def parse(cls, cfg: Any, *, sim_roles: Mapping[str, str] | None = None) -> InterventionSchedule:
        raw = OmegaConf.select(cfg, "interventions") if isinstance(cfg, DictConfig) else None
        if raw is None and isinstance(cfg, Mapping):
            raw = cfg.get("interventions")
        return cls(_parse_interventions(raw), sim_roles)

    def apply_due(
        self, *, step: int, engine: Any, game_masters: Sequence[Any], agents: Sequence[Any]
    ) -> None:
        """Fire every action whose ``at_step == step`` (persistent and one-shot)."""
        self._dispatch(
            engine=engine,
            game_masters=game_masters,
            agents=agents,
            predicate=lambda at: at == step,
            persistent_only=False,
            replayed=False,
        )

    def replay_persistent(
        self, *, start_step: int, engine: Any, game_masters: Sequence[Any], agents: Sequence[Any]
    ) -> None:
        """Re-apply persistent actions with ``at_step < start_step`` on resume.

        One-shot events with ``at_step < start_step`` already ran before the
        checkpoint (their effect is in restored backend/agent state), so they are
        NOT replayed. The live loop fires ``at_step == start_step`` itself.
        """
        if start_step <= 0:
            return
        self._dispatch(
            engine=engine,
            game_masters=game_masters,
            agents=agents,
            predicate=lambda at: at < start_step,
            persistent_only=True,
            replayed=True,
        )

    def _dispatch(
        self,
        *,
        engine: Any,
        game_masters: Sequence[Any],
        agents: Sequence[Any],
        predicate: Any,
        persistent_only: bool,
        replayed: bool,
    ) -> None:
        gms = list(game_masters)
        roster = list(agents)
        for intervention in self._interventions:
            if not predicate(intervention.at_step):
                continue
            ctx = InterventionContext(
                engine=engine,
                game_masters=gms,
                agents=roster,
                sim_roles=self._sim_roles,
                step=intervention.at_step,
            )
            for action in intervention.actions:
                if persistent_only and not action.handler.persistent:
                    continue
                action.handler.apply(action.config, ctx)
                self._record(step=intervention.at_step, kind=action.handler.kind, replayed=replayed)

    def _record(self, *, step: int, kind: str, replayed: bool) -> None:
        self._log.append({"step": step, "kind": kind, "replayed": replayed})
        logger.info("intervention fired: step=%s kind=%s replayed=%s", step, kind, replayed)
        try:
            from silisocs.runtime.telemetry import SimMetricsCollector

            collector = SimMetricsCollector.get()
            collector.increment_counter("interventions_fired")
            collector.set_meta("interventions", list(self._log))
        except Exception:  # pragma: no cover - telemetry must never break a run
            logger.debug("failed to record intervention telemetry", exc_info=True)


def validate_interventions(
    cfg: Any, *, gm_names: Collection[str], flow_names: Collection[str] = ()
) -> None:
    """Structurally + semantically validate the ``interventions`` config (if present).

    ``flow_names`` are the flows statically declared in config; empty means flows
    are not statically known (e.g. a custom step strategy) and flow targets are
    deferred to fire-time resolution — the same convention as ``gm_names``.
    """
    raw = OmegaConf.select(cfg, "interventions") if isinstance(cfg, DictConfig) else None
    if raw is None and isinstance(cfg, Mapping):
        raw = cfg.get("interventions")
    for intervention in _parse_interventions(raw):
        for action in intervention.actions:
            action.handler.validate(
                action.config, gm_names=set(gm_names), flow_names=set(flow_names)
            )
