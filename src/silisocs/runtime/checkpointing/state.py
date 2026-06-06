"""Checkpoint state save/load helpers."""

from __future__ import annotations

import base64
import copy
import json
import os
import pickle
import random
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from silisocs.runtime.checkpointing.policy import CHECKPOINT_SCHEMA_VERSION
from silisocs.runtime.checkpointing.serialization import json_safe
from silisocs.runtime.construction.assembly import RuntimeObjects, add_agent, add_game_master
from silisocs.runtime.construction.specs import RuntimeRole, RuntimeSpec
from silisocs.runtime.io import flush_jsonl_writers


def make_checkpoint_data(runtime: RuntimeObjects, *, step: int | None = None) -> dict[str, Any]:
    """Create a JSON-serializable checkpoint payload."""
    objects: dict[str, Any] = {}
    payload: dict[str, Any] = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "step": runtime.checkpoint_counter if step is None else int(step),
        "objects": objects,
        "checkpoint_counter": runtime.checkpoint_counter,
        "runtime_metadata": _runtime_metadata(runtime),
    }
    for obj in [*runtime.agents, *runtime.game_masters]:
        spec = runtime.object_specs.get(obj.name)
        if spec is None:
            raise ValueError(f"Runtime spec not found for object {obj.name}")
        state_getter = getattr(obj, "get_state", None)
        role = spec.role if isinstance(spec.role, RuntimeRole) else RuntimeRole(str(spec.role))
        objects[obj.name] = {
            "class_path": spec.class_path,
            "role": role.value,
            "compat": spec.compat,
            "params": json_safe(copy.deepcopy(spec.params)),
            "state": json_safe(state_getter() if callable(state_getter) else {}),
        }
    runtime.checkpoint_counter += 1
    return payload


def save_checkpoint(
    runtime: RuntimeObjects,
    *,
    step: int,
    checkpoint_path: str | None,
    state_callback: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    """Save a checkpoint file and optionally notify a callback."""
    flush_jsonl_writers(timeout_s=5.0)
    checkpoint_data = make_checkpoint_data(runtime, step=step)
    if state_callback is not None:
        state_callback(checkpoint_data)
    if not checkpoint_path:
        return
    os.makedirs(checkpoint_path, exist_ok=True)
    checkpoint_file = os.path.join(checkpoint_path, f"step_{step}_checkpoint.json")
    with open(checkpoint_file, "w", encoding="utf-8") as f:
        json.dump(json_safe(checkpoint_data), f, indent=2)
    print(f"Step {step}: Saved checkpoint to {checkpoint_file}")


def load_checkpoint_into_runtime(
    runtime: RuntimeObjects,
    checkpoint: Mapping[str, Any],
    *,
    models: dict[str, Any],
    object_to_model: dict[str, str],
) -> None:
    """Load checkpointed object state into an existing runtime object set."""
    if checkpoint.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported checkpoint schema version: {checkpoint.get('schema_version')!r}"
        )
    objects = checkpoint.get("objects", {})
    if not isinstance(objects, Mapping):
        raise ValueError("Checkpoint field `objects` must be a mapping.")
    for name, raw in objects.items():
        if not isinstance(raw, Mapping):
            raise ValueError(f"Checkpoint object state for {name} must be a mapping.")
        spec = RuntimeSpec(
            class_path=str(raw.get("class_path") or ""),
            role=RuntimeRole(str(raw.get("role") or "")),
            compat=raw.get("compat"),
            params=dict(raw.get("params") or {}),
        )
        raw_state = raw.get("state")
        state: Mapping[str, Any] = raw_state if isinstance(raw_state, Mapping) else {}
        existing = next(
            (obj for obj in [*runtime.agents, *runtime.game_masters] if obj.name == str(name)),
            None,
        )
        if existing is not None:
            setter = getattr(existing, "set_state", None)
            if not callable(setter):
                raise ValueError(f"Runtime object {name} does not support set_state().")
            setter(state)
            continue
        if spec.role == RuntimeRole.AGENT:
            add_agent(
                runtime=runtime,
                spec=spec,
                models=models,
                object_to_model=object_to_model,
                state=dict(state),
            )
        elif spec.role == RuntimeRole.GAME_MASTER:
            add_game_master(
                runtime=runtime,
                spec=spec,
                models=models,
                object_to_model=object_to_model,
                state=dict(state),
            )
        else:
            raise ValueError(f"Unsupported checkpoint role for {name}: {spec.role}")
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


def checkpoint_runtime_metadata(checkpoint: Mapping[str, Any]) -> dict[str, Any]:
    """Return normalized runtime metadata from a checkpoint payload."""
    raw = checkpoint.get("runtime_metadata", {})
    return dict(raw) if isinstance(raw, Mapping) else {}


def load_checkpoint_file(path: str | Path) -> dict[str, Any]:
    """Load and validate a checkpoint JSON file."""
    checkpoint_path = Path(path).expanduser()
    try:
        with checkpoint_path.open(encoding="utf-8") as f:
            parsed = json.load(f)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed checkpoint JSON: {checkpoint_path}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"Checkpoint JSON must be an object: {checkpoint_path}")
    return parsed


def resolve_checkpoint_source(source_run: str | Path) -> tuple[Path, Path]:
    """Return the latest checkpoint path and action log for a previous output dir."""
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
    action_events = root / "action_events.jsonl"
    if not action_events.is_file():
        raise FileNotFoundError(
            f"Action events log not found for checkpoint replay: {action_events}"
        )
    return candidates[-1], action_events


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
    action_events_file = ""
    output_rootname = ""
    if runtime.game_masters:
        backend = getattr(runtime.game_masters[0], "backend", None)
        action_logger = getattr(backend, "action_logger", None)
        action_events_file = str(getattr(action_logger, "output_filename", "") or "")
        if action_events_file:
            output_rootname = str(os.path.dirname(action_events_file))

    rng_state_bytes = pickle.dumps(random.getstate(), protocol=pickle.HIGHEST_PROTOCOL)
    return {
        "action_events_file": action_events_file,
        "output_rootname": output_rootname,
        "rng_state_b64": base64.b64encode(rng_state_bytes).decode("ascii"),
    }


def _checkpoint_step_from_name(name: str) -> int:
    try:
        return int(name.split("step_", 1)[1].split("_checkpoint", 1)[0])
    except (IndexError, ValueError) as exc:
        raise ValueError(f"Malformed checkpoint filename: {name}") from exc
