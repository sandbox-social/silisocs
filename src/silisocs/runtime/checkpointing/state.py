"""Checkpoint state save/load helpers."""

from __future__ import annotations

import base64
import hashlib
import importlib
import json
import logging
import os
import pickle
import random
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from silisocs.agents.base_agent import Agent
from silisocs.environments.gm.base_game_master import BaseGameMaster
from silisocs.exceptions import CheckpointError
from silisocs.runtime.checkpointing.policy import CHECKPOINT_SCHEMA_VERSION
from silisocs.runtime.checkpointing.save import (
    CheckpointSaveStrategy,
    MonolithicJsonSaveStrategy,
    reassemble_sharded_checkpoint,
)
from silisocs.runtime.checkpointing.serialization import json_safe
from silisocs.runtime.construction.assembly import RuntimeObjects, add_agent, add_game_master
from silisocs.runtime.construction.specs import RuntimeRole, RuntimeSpec
from silisocs.runtime.io import flush_jsonl_writers

logger = logging.getLogger(__name__)

# set_state implementations that are the inherited no-ops: an object whose
# set_state resolves to one of these silently discards any checkpointed state.
_NOOP_SET_STATE = frozenset({Agent.set_state, BaseGameMaster.set_state})


def _set_state_is_noop(obj: Any) -> bool:
    """Return whether ``obj`` only inherits a no-op ``set_state``."""
    func = getattr(type(obj), "set_state", None)
    return func in _NOOP_SET_STATE


_SHARED_MEMORIES_REF = "__shared_memories_ref__"


def _dedupe_shared_memories(params: dict[str, Any], table: dict[str, Any]) -> dict[str, Any]:
    """Replace a params ``shared_memories`` value with a reference into ``table``.

    Every agent of a class carries the identical shared_memories block, so
    serializing it per object multiplies the same text N times in every
    checkpoint. It is stored once, keyed by content hash;
    :func:`_resolve_shared_memories_ref` swaps it back on load.
    """
    value = params.get("shared_memories")
    if not value or isinstance(value, Mapping):
        return params
    key = hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    table.setdefault(key, value)
    params["shared_memories"] = {_SHARED_MEMORIES_REF: key}
    return params


def _resolve_shared_memories_ref(
    params: dict[str, Any], table: Mapping[str, Any]
) -> dict[str, Any]:
    """Inverse of :func:`_dedupe_shared_memories`; pass-through for old payloads."""
    value = params.get("shared_memories")
    if isinstance(value, Mapping) and _SHARED_MEMORIES_REF in value:
        key = str(value[_SHARED_MEMORIES_REF])
        if key not in table:
            raise ValueError(
                f"Checkpoint shared_memories reference {key!r} is missing from "
                "shared_memories_table; the checkpoint is corrupt or truncated."
            )
        params["shared_memories"] = table[key]
    return params


def _package_version() -> str:
    from silisocs import __version__

    return __version__


def make_checkpoint_data(runtime: RuntimeObjects, *, step: int | None = None) -> dict[str, Any]:
    """Create a JSON-serializable checkpoint payload."""
    objects: dict[str, Any] = {}
    shared_memories_table: dict[str, Any] = {}
    payload: dict[str, Any] = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        # Which release wrote this checkpoint — read back by the schema
        # mismatch error so "produced by an older silisocs" is diagnosable.
        "silisocs_version": _package_version(),
        "step": runtime.checkpoint_counter if step is None else int(step),
        "objects": objects,
        "checkpoint_counter": runtime.checkpoint_counter,
        "runtime_metadata": _runtime_metadata(runtime),
        "shared_memories_table": shared_memories_table,
    }
    for obj in [*runtime.agents, *runtime.game_masters_by_sequence()]:
        spec = runtime.object_specs.get(obj.name)
        if spec is None:
            raise ValueError(f"Runtime spec not found for object {obj.name}")
        state_getter = getattr(obj, "get_state", None)
        role = spec.role if isinstance(spec.role, RuntimeRole) else RuntimeRole(str(spec.role))
        # json_safe builds fresh containers recursively (it never mutates its
        # input), so no defensive deepcopy is needed before it.
        params = json_safe(spec.params)
        if isinstance(params, dict):
            params = _dedupe_shared_memories(params, shared_memories_table)
        objects[obj.name] = {
            "class_path": spec.class_path,
            "role": role.value,
            "compat": spec.compat,
            "params": params,
            "state": json_safe(state_getter() if callable(state_getter) else {}),
        }
    # Only the auto-numbering path (no explicit step) advances the counter, so an
    # explicit-step call has no side effect on it.
    if step is None:
        runtime.checkpoint_counter += 1
    return payload


def save_checkpoint(
    runtime: RuntimeObjects,
    *,
    step: int,
    checkpoint_path: str | None,
    state_callback: Callable[[dict[str, Any]], None] | None = None,
    save_strategy: CheckpointSaveStrategy | None = None,
) -> None:
    """Save a checkpoint and optionally notify a callback.

    The payload always comes from :func:`make_checkpoint_data`; the on-disk
    layout is delegated to ``save_strategy`` (``sim.checkpoint.save``), which
    defaults to the monolithic single-JSON format.
    """
    flush_jsonl_writers(timeout_s=5.0)
    checkpoint_data = make_checkpoint_data(runtime, step=step)
    if state_callback is not None:
        state_callback(checkpoint_data)
    if not checkpoint_path:
        return
    strategy = save_strategy or MonolithicJsonSaveStrategy()
    checkpoint_file = strategy.save(checkpoint_data, step=step, checkpoint_path=checkpoint_path)
    print(f"Step {step}: Saved checkpoint to {checkpoint_file}")


def _stage_checkpoint_objects(
    runtime: RuntimeObjects,
    checkpoint: Mapping[str, Any],
    *,
    models: dict[str, Any],
    object_to_model: dict[str, str],
) -> list[tuple[str, RuntimeSpec, Mapping[str, Any], Any]]:
    """Validate every checkpoint object before any runtime mutation.

    Returns staged ``(name, spec, state, existing_object_or_None)`` entries,
    ordered agents-first so game masters can reference restored agents.
    Raises without touching the runtime if any entry is invalid.
    """
    objects = checkpoint.get("objects", {})
    if not isinstance(objects, Mapping):
        raise ValueError("Checkpoint field `objects` must be a mapping.")
    shared_table = checkpoint.get("shared_memories_table") or {}
    if not isinstance(shared_table, Mapping):
        raise ValueError("Checkpoint field `shared_memories_table` must be a mapping.")
    by_name = {obj.name: obj for obj in [*runtime.agents, *runtime.game_masters]}
    staged: list[tuple[str, RuntimeSpec, Mapping[str, Any], Any]] = []
    for name, raw in objects.items():
        if not isinstance(raw, Mapping):
            raise ValueError(f"Checkpoint object state for {name} must be a mapping.")
        spec = RuntimeSpec(
            class_path=str(raw.get("class_path") or ""),
            role=RuntimeRole(str(raw.get("role") or "")),
            compat=raw.get("compat"),
            params=_resolve_shared_memories_ref(dict(raw.get("params") or {}), shared_table),
        )
        if spec.role not in (RuntimeRole.AGENT, RuntimeRole.GAME_MASTER):
            raise ValueError(f"Unsupported checkpoint role for {name}: {spec.role}")
        raw_state = raw.get("state")
        state: Mapping[str, Any] = raw_state if isinstance(raw_state, Mapping) else {}
        existing = by_name.get(str(name))
        if existing is not None:
            if not callable(getattr(existing, "set_state", None)):
                raise ValueError(f"Runtime object {name} does not support set_state().")
            if state and _set_state_is_noop(existing):
                raise ValueError(
                    f"Runtime object {name!r} ({type(existing).__name__}) has checkpointed "
                    "state but only inherits the no-op set_state(); restoring it would "
                    "silently drop that state. Implement set_state() to restore this object "
                    "(see AGENTS.md §6), or make its get_state() return {} if it is stateless."
                )
            current_spec = runtime.object_specs.get(str(name))
            if current_spec is not None:
                checkpoint_compat = spec.compat or None
                current_compat = current_spec.compat or None
                if (
                    str(current_spec.class_path) != str(spec.class_path)
                    or current_compat != checkpoint_compat
                ):
                    raise ValueError(
                        f"Checkpoint object {name!r} was saved as class {spec.class_path!r} "
                        f"(compat={checkpoint_compat!r}) but the current runtime defines it as "
                        f"{current_spec.class_path!r} (compat={current_compat!r}). Refusing to "
                        "restore mismatched object identity; rebuild the runtime from the "
                        "original configuration."
                    )
        else:
            model_key = object_to_model.get(str(name))
            if model_key is None or model_key not in models:
                raise ValueError(
                    f"Checkpoint object {name} has no language-model assignment in the "
                    "current configuration; cannot restore it into this runtime."
                )
            if not spec.compat:
                module_path, _, attr = spec.class_path.rpartition(".")
                if not module_path:
                    raise ValueError(f"Checkpoint object {name} has invalid class_path.")
                try:
                    getattr(importlib.import_module(module_path), attr)
                except (ImportError, AttributeError) as exc:
                    raise ValueError(
                        f"Checkpoint object {name} references unimportable class "
                        f"'{spec.class_path}': {exc}"
                    ) from exc
        staged.append((str(name), spec, state, existing))
    checkpoint_names = {str(name) for name in objects}
    orphaned = [name for name in by_name if name not in checkpoint_names]
    if orphaned:
        logger.warning(
            "Runtime objects present in the current configuration but absent from the "
            "checkpoint will keep their freshly-initialized state (not restored): %s",
            ", ".join(sorted(orphaned)),
        )
    staged.sort(key=_stage_apply_order)
    return staged


def _stage_apply_order(entry: tuple[str, RuntimeSpec, Mapping[str, Any], Any]) -> tuple[int, int]:
    """Order agents before game masters, and game masters by ascending sequence."""
    spec = entry[1]
    if spec.role == RuntimeRole.AGENT:
        return (0, 0)
    try:
        sequence = int(spec.params.get("sequence", 0))
    except (TypeError, ValueError):
        sequence = 0
    return (1, sequence)


def load_checkpoint_into_runtime(
    runtime: RuntimeObjects,
    checkpoint: Mapping[str, Any],
    *,
    models: dict[str, Any],
    object_to_model: dict[str, str],
) -> None:
    """Load checkpointed object state into an existing runtime object set.

    Validation happens for every object before any state is applied, so an
    invalid checkpoint leaves the runtime untouched. If applying state fails
    partway despite validation, a :class:`CheckpointError` is raised because
    the runtime can no longer be trusted and must be rebuilt.
    """
    if checkpoint.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
        writer = checkpoint.get("silisocs_version") or "an older silisocs release"
        raise ValueError(
            f"Unsupported checkpoint schema version {checkpoint.get('schema_version')!r} "
            f"(this runtime writes v{CHECKPOINT_SCHEMA_VERSION}; the checkpoint was "
            f"written by silisocs {writer}). Checkpoints do not migrate across "
            "schema versions — re-run the simulation, or restore with the "
            "silisocs release that wrote it (schema bumps are listed in "
            "docs/upgrading.md)."
        )
    staged = _stage_checkpoint_objects(
        runtime, checkpoint, models=models, object_to_model=object_to_model
    )
    applied: list[str] = []
    try:
        for name, spec, state, existing in staged:
            if existing is not None:
                existing.set_state(state)
            elif spec.role == RuntimeRole.AGENT:
                add_agent(
                    runtime=runtime,
                    spec=spec,
                    models=models,
                    object_to_model=object_to_model,
                    state=dict(state),
                )
            else:
                add_game_master(
                    runtime=runtime,
                    spec=spec,
                    models=models,
                    object_to_model=object_to_model,
                    state=dict(state),
                )
            applied.append(name)
    except Exception as exc:
        raise CheckpointError(
            f"Checkpoint restore failed while applying state for object "
            f"'{staged[len(applied)][0]}' (after {len(applied)}/{len(staged)} objects). "
            "The runtime is partially restored and must be rebuilt before use."
        ) from exc
    runtime.checkpoint_counter = int(checkpoint.get("checkpoint_counter", 0))


def checkpoint_has_backend_state(checkpoint: Mapping[str, Any]) -> bool:
    """Return whether every checkpointed game master carries backend state."""
    objects = checkpoint.get("objects", {})
    if not isinstance(objects, Mapping):
        return False
    game_master_states: list[Mapping[str, Any]] = []
    for raw in objects.values():
        if not isinstance(raw, Mapping):
            continue
        if raw.get("role") != RuntimeRole.GAME_MASTER.value:
            continue
        state = raw.get("state")
        if isinstance(state, Mapping):
            game_master_states.append(state)
    return bool(game_master_states) and all("backend" in state for state in game_master_states)


def checkpoint_authoritative_gm_names(checkpoint: Mapping[str, Any]) -> frozenset[str]:
    """Return the names of game masters that carry an authoritative backend snapshot.

    These GMs are restored directly from their snapshot via ``set_state``; the
    remaining (non-authoritative) GMs are handed to the configured restore
    strategy. This is the per-GM replacement for the all-or-nothing
    :func:`checkpoint_has_backend_state` gate, so a twitter GM can restore from a
    snapshot while a mastodon GM in the same run replays through a strategy.
    """
    objects = checkpoint.get("objects", {})
    if not isinstance(objects, Mapping):
        return frozenset()
    authoritative: set[str] = set()
    for name, raw in objects.items():
        if not isinstance(raw, Mapping):
            continue
        if raw.get("role") != RuntimeRole.GAME_MASTER.value:
            continue
        state = raw.get("state")
        if isinstance(state, Mapping) and "backend" in state:
            authoritative.add(str(name))
    return frozenset(authoritative)


def checkpoint_runtime_metadata(checkpoint: Mapping[str, Any]) -> dict[str, Any]:
    """Return normalized runtime metadata from a checkpoint payload."""
    raw = checkpoint.get("runtime_metadata", {})
    return dict(raw) if isinstance(raw, Mapping) else {}


def load_checkpoint_file(path: str | Path) -> dict[str, Any]:
    """Load and validate a checkpoint JSON file.

    Handles both save layouts: a monolithic checkpoint is returned as-is; a
    sharded manifest (``format: sharded``) is reassembled — object shards read
    back in, sidecar DB files re-inlined — into the same payload shape, so
    callers never see the difference.
    """
    checkpoint_path = Path(path).expanduser()
    try:
        with checkpoint_path.open(encoding="utf-8") as f:
            parsed = json.load(f)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed checkpoint JSON: {checkpoint_path}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"Checkpoint JSON must be an object: {checkpoint_path}")
    if parsed.get("format") == "sharded":
        return reassemble_sharded_checkpoint(parsed, checkpoint_path.parent)
    return parsed


def resolve_checkpoint_source(source_run: str | Path) -> Path:
    """Return the latest checkpoint file path for a previous run.

    Action logs are resolved separately via
    :func:`silisocs.evaluations.action_events.resolve_action_event_files`, which
    handles both the flat single-GM log and per-GM multi-GM subdirectory logs.
    """
    root = Path(source_run).expanduser()
    if not root.is_dir():
        raise FileNotFoundError(f"Checkpoint source_run directory not found: {root}")
    checkpoints_dir = root / "checkpoints"
    if not checkpoints_dir.is_dir():
        raise FileNotFoundError(f"Checkpoint directory not found in source_run: {checkpoints_dir}")
    candidates = sorted(
        checkpoints_dir.glob("step_*_checkpoint.json"),
        key=lambda path: _checkpoint_step_from_name(path.name),
    )
    if not candidates:
        raise FileNotFoundError(f"No checkpoint files found in {checkpoints_dir}")
    return candidates[-1]


def latest_checkpoint_step(output_dir: str | Path) -> int:
    """Highest saved checkpoint step for a run, or -1 when none exist.

    The canonical answer to "how far did this run checkpoint?" — a
    ``step_N_checkpoint.json`` means episodes ``0..N-1`` completed. Never
    raises and skips malformed filenames (observers poll this on a cadence;
    one stray file must not hide every valid checkpoint — restore's
    :func:`resolve_checkpoint_source` stays strict by design).
    """
    steps = []
    try:
        for path in (Path(output_dir) / "checkpoints").glob("step_*_checkpoint.json"):
            try:
                steps.append(_checkpoint_step_from_name(path.name))
            except ValueError:
                continue
    except OSError:
        return -1
    return max(steps, default=-1)


def has_checkpoints(output_dir: str | Path) -> bool:
    """Return whether ``output_dir`` already holds at least one checkpoint file.

    True iff ``<output_dir>/checkpoints`` exists and contains at least one
    ``step_*_checkpoint.json``. Never raises (a missing or unreadable directory
    yields False), so it is safe to call before deciding whether to auto-resume.
    """
    try:
        checkpoints_dir = Path(output_dir) / "checkpoints"
        if not checkpoints_dir.is_dir():
            return False
        return any(checkpoints_dir.glob("step_*_checkpoint.json"))
    except OSError:
        return False


def select_resume_source(
    source_run: str | Path | None,
    *,
    auto_resume: bool,
    restore_present: bool,
    output_dir: str | Path,
) -> Path | None:
    """Decide which directory (if any) a run should resume its checkpoint from.

    Returns the resume source directory, or ``None`` for a fresh start:

    * an explicit ``source_run`` always wins (returned as an expanded/resolved
      absolute path), regardless of ``auto_resume`` or whether it has
      checkpoints — the caller still validates the source and ``restore``;
    * otherwise, when ``auto_resume`` is set, a ``restore`` strategy is present,
      and ``output_dir`` already contains checkpoints, the run resumes from its
      own ``output_dir``;
    * in every other case the run starts fresh (returns ``None``).
    """
    if source_run:
        source_path = Path(str(source_run)).expanduser()
        if not source_path.is_absolute():
            source_path = (Path.cwd() / source_path).resolve()
        return source_path
    if auto_resume and restore_present and has_checkpoints(output_dir):
        return Path(output_dir).resolve()
    return None


def restore_rng_state_from_metadata(metadata: Mapping[str, Any]) -> None:
    """Restore Python's random state from checkpoint metadata if present."""
    raw = metadata.get("rng_state_b64")
    if not raw:
        return
    if not isinstance(raw, str):
        raise ValueError("Checkpoint runtime_metadata.rng_state_b64 must be a string.")
    try:
        state = pickle.loads(base64.b64decode(raw.encode("ascii")))
    except Exception as exc:
        raise ValueError("Failed to decode checkpoint RNG state.") from exc
    random.setstate(state)


def _runtime_metadata(runtime: RuntimeObjects) -> dict[str, Any]:
    game_master_artifacts: list[dict[str, Any]] = []
    for game_master in runtime.game_masters_by_sequence():
        backend = getattr(game_master, "backend", None)
        action_logger = getattr(backend, "action_logger", None)
        action_events_file = str(getattr(action_logger, "output_filename", "") or "")
        output_rootname = str(os.path.dirname(action_events_file)) if action_events_file else ""
        spec = runtime.object_specs.get(str(getattr(game_master, "name", "")))
        sequence = 0
        if spec is not None:
            try:
                sequence = int(spec.params.get("sequence", 0))
            except (TypeError, ValueError):
                sequence = 0
        game_master_artifacts.append(
            {
                "name": str(getattr(game_master, "name", "")),
                "backend_type": str(getattr(game_master, "backend_type", "") or ""),
                "sequence": sequence,
                "action_events_file": action_events_file,
                "output_rootname": output_rootname,
            }
        )

    rng_state_bytes = pickle.dumps(random.getstate(), protocol=pickle.HIGHEST_PROTOCOL)
    return {
        "game_masters": game_master_artifacts,
        "rng_state_b64": base64.b64encode(rng_state_bytes).decode("ascii"),
    }


def _checkpoint_step_from_name(name: str) -> int:
    try:
        return int(name.split("step_", 1)[1].split("_checkpoint", 1)[0])
    except (IndexError, ValueError) as exc:
        raise ValueError(f"Malformed checkpoint filename: {name}") from exc
