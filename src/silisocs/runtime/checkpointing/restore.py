"""Checkpoint restore strategies."""

from __future__ import annotations

import inspect
import json
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from omegaconf import OmegaConf

from silisocs.runtime.checkpointing.replay_mappers import get_replay_mapper
from silisocs.runtime.types import ActionOutput


class CheckpointRestoreStrategy:
    """Checkpoint-owned runtime restore hook.

    ``authoritative_gm_names`` lists game masters this call should NOT
    reconstruct because they were already restored elsewhere (from an
    authoritative snapshot, or by a per-GM restore override), so a strategy
    reconstructs only the remaining game masters. ``action_events_files`` is the
    list of per-run action logs (one flat file for single-GM runs, one per-GM
    file for multi-GM runs). ``flow_chains`` is the flow -> GM-sequence routing
    topology resolved from config (engine config, not GM state), used to route
    untagged replay events to their owning GM. Custom strategies should accept
    ``**_`` for forward compatibility.
    """

    def restore(
        self,
        *,
        game_masters: Sequence[Any],
        action_events_files: Sequence[Path],
        checkpoint_step: int,
        authoritative_gm_names: frozenset[str] = frozenset(),
        flow_chains: Mapping[str, Any] | None = None,
    ) -> None:
        del game_masters, action_events_files, checkpoint_step, authoritative_gm_names, flow_chains


class SocialActionEventReplayRestore(CheckpointRestoreStrategy):
    """Replay social backend action events through the native GM resolve surface.

    Each event is routed to its owning game master and mapped to a backend action
    by the replay mapper registered for that GM's ``backend_type`` (see
    ``replay_mappers.py``), so heterogeneous backends each translate their own
    logged vocabulary without carrying a replay-specific method. Game masters
    already restored authoritatively from a snapshot are skipped.
    """

    def restore(
        self,
        *,
        game_masters: Sequence[Any],
        action_events_files: Sequence[Path],
        checkpoint_step: int,
        authoritative_gm_names: frozenset[str] = frozenset(),
        flow_chains: Mapping[str, Any] | None = None,
    ) -> None:
        if not game_masters:
            raise ValueError("Checkpoint restore requires at least one game master.")
        chains = dict(flow_chains or {})
        replay_gms = [
            gm for gm in game_masters if str(getattr(gm, "name", "")) not in authoritative_gm_names
        ]
        if not replay_gms:
            # Every game master was restored from its authoritative snapshot.
            return
        if not action_events_files:
            raise ValueError("Checkpoint replay requires action_events.jsonl.")
        _assert_backends_replayable(replay_gms)
        _begin_backend_replay(replay_gms)
        replay_names = frozenset(str(getattr(gm, "name", "")) for gm in replay_gms)
        # Replay files in order (each per-GM log is chronological); each row is
        # routed back to its resolving GM via the ``gm_name`` tag.
        for events_file in action_events_files:
            for row in _read_jsonl(events_file):
                fields = _replay_event_fields(row, checkpoint_step)
                if fields is None:
                    continue
                label, source_user, data = fields
                owner_name = str(row.get("gm_name", "") or "").strip()
                target_gm, action = _route_and_map_event(
                    source_user,
                    label,
                    data,
                    game_masters,
                    replay_names,
                    owner_name=owner_name,
                    flow_chains=chains,
                )
                if target_gm is None or action is None:
                    continue
                target_gm.resolve_action(source_user, action)


def _begin_backend_replay(game_masters: Sequence[Any]) -> None:
    """Let each replay backend reset its per-replay state before the loop."""
    for game_master in game_masters:
        begin_replay = getattr(getattr(game_master, "backend", None), "begin_action_replay", None)
        if callable(begin_replay):
            begin_replay()


def _replay_event_fields(
    row: Mapping[str, Any], checkpoint_step: int
) -> tuple[str, str, Mapping[str, Any]] | None:
    """Validate one action-log row and return (label, source_user, data), or None to skip."""
    if str(row.get("event_type", "")) != "action":
        return None
    episode = row.get("episode")
    try:
        if episode is None or int(episode) >= int(checkpoint_step):
            return None
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Malformed action event episode: {row}") from exc
    label = str(row.get("label", "") or "").strip()
    if label in _IGNORED_ACTION_LABELS:
        return None
    source_user = str(row.get("source_user", "") or "").strip()
    if not source_user:
        raise ValueError(f"Action event missing source_user: {row}")
    data = row.get("data")
    if not isinstance(data, Mapping):
        raise ValueError(f"Action event data must be a mapping: {row}")
    return label, source_user, data


def _assert_backends_replayable(game_masters: Sequence[Any]) -> None:
    """Reject backends whose ``backend_type`` has no registered replay mapper.

    A backend "supports replay" exactly when a mapper is registered for its
    ``backend_type`` (see ``replay_mappers.register_replay_mapper``). Backends
    without one must restore from an authoritative snapshot or a custom strategy.
    """
    for game_master in game_masters:
        if getattr(game_master, "backend", None) is None:
            continue
        backend_type = str(getattr(game_master, "backend_type", "") or "")
        if get_replay_mapper(backend_type) is None:
            gm_name = str(getattr(game_master, "name", "")) or "<unnamed>"
            raise ValueError(
                f"Game master {gm_name!r} uses backend {backend_type or 'unknown'!r}, which "
                "has no registered replay mapper, so the built-in "
                "'social_action_event_replay' strategy cannot reconstruct it. Restore it "
                "from an authoritative snapshot (provides_checkpoint_state=True), register a "
                "replay mapper (replay_mappers.register_replay_mapper), or configure a custom "
                "sim.checkpoint.restore.class_path strategy."
            )


def build_checkpoint_restore(slot_cfg: Any) -> CheckpointRestoreStrategy:
    """Build the configured checkpoint restore strategy.

    A custom ``class_path`` takes precedence over ``built_in`` so backends without
    authoritative snapshot state (e.g. Mastodon) can supply their own restore
    logic. The class must subclass :class:`CheckpointRestoreStrategy`.
    """
    if slot_cfg is None:
        raise ValueError("sim.checkpoint.source_run requires sim.checkpoint.restore.")
    built_in = str(getattr(slot_cfg, "built_in", "") or "").strip()
    class_path = str(getattr(slot_cfg, "class_path", "") or "").strip()
    if class_path:
        return _load_restore_strategy(class_path, getattr(slot_cfg, "params", None))
    if built_in == "social_action_event_replay":
        return SocialActionEventReplayRestore()
    raise ValueError(
        "Unknown sim.checkpoint.restore.built_in "
        f"{built_in!r}. Available: social_action_event_replay. "
        "Set sim.checkpoint.restore.class_path for a custom strategy."
    )


def build_per_gm_checkpoint_restores(cfg: Any) -> dict[str, CheckpointRestoreStrategy]:
    """Build per-GM restore overrides from ``env.gm_orchestration.gms[*].restore``.

    Returns ``{gm_name: strategy}`` only for game masters that declare a
    ``restore`` block (same schema as ``sim.checkpoint.restore``). Game masters
    without one fall back to the global default, so this is empty for single-GM
    runs and for multi-GM runs that do not override any GM.
    """
    gms = OmegaConf.select(cfg, "env.gm_orchestration.gms", default=None)
    overrides: dict[str, CheckpointRestoreStrategy] = {}
    if not gms:
        return overrides
    for spec in gms:
        restore_cfg = OmegaConf.select(spec, "restore", default=None)
        if restore_cfg is None:
            continue
        name = str(
            OmegaConf.select(spec, "gm_name", default=None)
            or OmegaConf.select(spec, "name", default="")
            or ""
        ).strip()
        if name:
            overrides[name] = build_checkpoint_restore(restore_cfg)
    return overrides


def run_checkpoint_restores(
    *,
    game_masters: Sequence[Any],
    default_strategy: CheckpointRestoreStrategy,
    per_gm_strategies: Mapping[str, CheckpointRestoreStrategy],
    action_events_files: Sequence[Path],
    checkpoint_step: int,
    authoritative_gm_names: frozenset[str],
    flow_chains: Mapping[str, Any] | None = None,
) -> None:
    """Restore non-authoritative game masters with per-GM strategy overrides.

    The default strategy reconstructs every non-authoritative GM that has no
    override; each overridden GM is reconstructed by its own strategy. Every
    strategy receives the full game-master set (so flow-chain routing is intact)
    plus ``flow_chains`` (the config-resolved flow -> GM routing topology) and only
    reconstructs the GMs not named in its skip set.
    """
    names = [str(getattr(gm, "name", "")) for gm in game_masters]
    override_names = frozenset(name for name in names if name in per_gm_strategies)
    _call_restore(
        default_strategy,
        game_masters=game_masters,
        action_events_files=action_events_files,
        checkpoint_step=checkpoint_step,
        authoritative_gm_names=authoritative_gm_names | override_names,
        flow_chains=flow_chains,
    )
    for gm in game_masters:
        name = str(getattr(gm, "name", ""))
        strategy = per_gm_strategies.get(name)
        if strategy is None or name in authoritative_gm_names:
            continue
        _call_restore(
            strategy,
            game_masters=game_masters,
            action_events_files=action_events_files,
            checkpoint_step=checkpoint_step,
            authoritative_gm_names=frozenset(other for other in names if other != name),
            flow_chains=flow_chains,
        )


def _call_restore(
    strategy: CheckpointRestoreStrategy, *, flow_chains: Mapping[str, Any] | None, **kwargs: Any
) -> None:
    """Invoke a restore strategy, passing ``flow_chains`` only if it accepts it.

    ``flow_chains`` was added to the restore contract after custom strategies could
    already exist. Strategies that declare it (the shipped ones) or follow the
    documented ``**_`` forward-compat convention receive it; an older custom
    strategy that declares neither is called without it instead of raising.
    """
    params = inspect.signature(strategy.restore).parameters
    accepts = "flow_chains" in params or any(
        param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values()
    )
    if accepts:
        strategy.restore(flow_chains=flow_chains, **kwargs)
    else:
        strategy.restore(**kwargs)


def _load_restore_strategy(class_path: str, params: Any) -> CheckpointRestoreStrategy:
    """Import and instantiate a custom CheckpointRestoreStrategy from a class path."""
    import importlib

    module_path, _, attr = class_path.rpartition(".")
    if not module_path:
        raise ValueError(f"Invalid checkpoint restore class_path: {class_path!r}")
    try:
        cls = getattr(importlib.import_module(module_path), attr)
    except (ImportError, AttributeError) as exc:
        raise ValueError(
            f"Could not import checkpoint restore class_path {class_path!r}: {exc}"
        ) from exc
    kwargs: dict[str, Any] = {}
    if isinstance(params, Mapping):
        kwargs = {str(key): value for key, value in params.items()}
    try:
        strategy = cls(**kwargs)
    except TypeError as exc:
        raise ValueError(
            f"Could not instantiate checkpoint restore {class_path!r} with params "
            f"{sorted(kwargs)}: {exc}"
        ) from exc
    if not isinstance(strategy, CheckpointRestoreStrategy):
        raise TypeError(
            f"Checkpoint restore class {class_path!r} must subclass CheckpointRestoreStrategy."
        )
    return strategy


_IGNORED_ACTION_LABELS = {
    "finish_action_episode",
    "initialize",
    "init_create_user",
    "init_follow",
    "read_profile",
    "get_own_timeline",
    "get_home_feed",
    "timeline_retrieval",
    "timeline_retrieval_error",
    "recsys_init",
    "recsys_update",
    "get_trending",
    "get_post_comments",
    "do_nothing",
}


def _read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Stream JSONL rows one at a time — replay logs grow with the whole run's
    action history, so materializing them as a list is O(total actions) memory.
    """
    try:
        with path.open(encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                parsed = json.loads(stripped)
                if not isinstance(parsed, dict):
                    raise ValueError(f"JSONL row {line_no} must be an object.")
                yield parsed
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed action_events JSONL: {path}") from exc


def _route_and_map_event(
    agent_name: str,
    label: str,
    data: Mapping[str, Any],
    game_masters: Sequence[Any],
    replay_names: frozenset[str],
    *,
    owner_name: str = "",
    flow_chains: Mapping[str, Any] | None = None,
) -> tuple[Any, ActionOutput | None]:
    """Route an event to its owning GM and map it via that GM's backend_type.

    When the row names its owning GM (``owner_name``, present on every real
    run's logs), the event is routed straight to that GM — correct even when
    several flow-chained GMs persist the same action. Untagged rows (e.g. test
    fixtures) fall back to deriving the owner from the agent's flow chain.

    Returns ``(game_master, action)`` to replay, or ``(None, None)`` to skip the
    event because its owning GM was already restored authoritatively (not in
    ``replay_names``) or no registered mapper maps the label.
    """
    if owner_name:
        owner = next(
            (gm for gm in game_masters if str(getattr(gm, "name", "")) == owner_name), None
        )
        if owner is not None:
            if owner_name not in replay_names:
                return None, None  # owner restored from an authoritative snapshot
            action = _map_event_for_gm(owner, label, data)
            return (owner, action) if action is not None else (None, None)
        # owner_name names no current GM -> fall back to flow-chain routing.
    for game_master in _candidate_game_masters(agent_name, game_masters, flow_chains):
        if str(getattr(game_master, "name", "")) not in replay_names:
            # Owned by an authoritative GM already restored from its snapshot.
            return None, None
        mapper = get_replay_mapper(str(getattr(game_master, "backend_type", "") or ""))
        if mapper is None:
            continue
        action = mapper(label, data)
        if action is None:
            return None, None
        action_name = _first_tool_name(action)
        if action_name and not _gm_backend_exposes_action(game_master, action_name):
            continue
        return game_master, action
    return None, None


def _map_event_for_gm(game_master: Any, label: str, data: Mapping[str, Any]) -> ActionOutput | None:
    """Map one event using the mapper registered for a GM's ``backend_type``."""
    mapper = get_replay_mapper(str(getattr(game_master, "backend_type", "") or ""))
    return mapper(label, data) if mapper is not None else None


def _candidate_game_masters(
    agent_name: str, game_masters: Sequence[Any], flow_chains: Mapping[str, Any] | None = None
) -> list[Any]:
    """Return the GM(s) that may own an agent's action, in routing order."""
    if not game_masters:
        raise ValueError("Checkpoint replay requires at least one game master.")
    if len(game_masters) == 1:
        return [game_masters[0]]

    by_name = {str(getattr(gm, "name", "")): gm for gm in game_masters}
    unnamed = [gm for gm in game_masters if not str(getattr(gm, "name", "")).strip()]
    if unnamed:
        raise ValueError("Checkpoint replay requires every game master to have a name.")
    if len(by_name) != len(game_masters):
        raise ValueError("Checkpoint replay requires unique game master names.")

    agent_flow = _flow_for_agent(agent_name, game_masters)
    chain = _flow_chain_for_flow(agent_flow, flow_chains)
    unknown = [name for name in chain if name not in by_name]
    if unknown:
        raise ValueError(
            f"Checkpoint replay flow '{agent_flow}' references unknown GM(s): {unknown}"
        )
    return [by_name[name] for name in chain]


def _first_tool_name(action: ActionOutput) -> str:
    """Return the first tool-call name in an ActionOutput, or '' if none."""
    tool_calls = getattr(action, "tool_calls", None) or []
    for call in tool_calls:
        name = str(getattr(call, "name", "") or "").strip()
        if name:
            return name
    return ""


def _flow_for_agent(agent_name: str, game_masters: Sequence[Any]) -> str:
    flows: set[str] = set()
    for gm in game_masters:
        flow_tags = getattr(gm, "agent_flow_tags", None)
        if isinstance(flow_tags, Mapping):
            flow = str(flow_tags.get(agent_name, "") or "").strip()
            if flow:
                flows.add(flow)
    if not flows:
        raise ValueError(f"Checkpoint replay requires agent_flow_tags for agent '{agent_name}'.")
    if len(flows) > 1:
        raise ValueError(
            f"Checkpoint replay found conflicting flows for agent '{agent_name}': {sorted(flows)}"
        )
    return next(iter(flows))


def _flow_chain_for_flow(flow: str, flow_chains: Mapping[str, Any] | None) -> list[str]:
    """Resolve a flow's GM chain from the config-resolved routing topology.

    ``flow_chains`` is the single authoritative map (engine config), so there is no
    longer any cross-GM reconciliation to do — the former "conflicting flow chains"
    case cannot arise from one source.
    """
    raw_chain = flow_chains.get(flow) if isinstance(flow_chains, Mapping) else None
    if isinstance(raw_chain, str):
        chain = [raw_chain.strip()] if raw_chain.strip() else []
    elif isinstance(raw_chain, Sequence) and not isinstance(raw_chain, (str, bytes)):
        # Skip ``None`` entries (staged "empty slots"): no GM resolves there.
        chain = [str(name).strip() for name in raw_chain if name is not None and str(name).strip()]
    elif raw_chain is None:
        chain = []
    else:
        raise ValueError(f"Checkpoint replay flow chain for '{flow}' must be a string or list.")
    if not chain:
        raise ValueError(f"Checkpoint replay requires flow_chains metadata for flow '{flow}'.")
    return chain


def _gm_backend_exposes_action(game_master: Any, action_name: str) -> bool:
    backend = getattr(game_master, "backend", None)
    actions = getattr(backend, "actions", None)
    if not callable(actions):
        return False
    for action in actions():
        if action_name in {
            str(getattr(action, "name", "")),
            str(getattr(action, "selectable_name", "")),
        }:
            return True
    return False
