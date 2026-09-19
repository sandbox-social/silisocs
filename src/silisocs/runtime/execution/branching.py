"""Capability-based planning for independent checkpoint branches.

Branching is deliberately a construction concern: a child is a new run whose
runtime is rebuilt from configuration and whose state is restored from one
explicit checkpoint. This module validates that operation without importing
Studio or naming any backend implementation.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from silisocs.runtime.checkpointing import (
    checkpoint_has_backend_state,
    checkpoint_runtime_metadata,
    load_checkpoint_file,
    resolve_checkpoint_source,
)

_LAUNCHER_OWNED_PATHS = (
    "output_dir",
    "seed",
    "hydra",
    "sim.checkpoint.source_run",
    "sim.checkpoint.source_step",
    "sim.checkpoint.auto_resume",
    "sim.checkpoint.branch",
    "sim.engine.control",
)
_OVERRIDE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\[\]-]*$")


@dataclass(frozen=True)
class BranchPlan:
    """Validated inputs for launching one independent continuation."""

    source_run: Path
    checkpoint_path: Path
    checkpoint_step: int
    mode: str
    continuation_seed: int | None
    overrides: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return the portable plan payload exposed by Studio."""
        return {
            "source_run": str(self.source_run),
            "checkpoint_path": str(self.checkpoint_path),
            "checkpoint_step": self.checkpoint_step,
            "mode": self.mode,
            "continuation_seed": self.continuation_seed,
            "overrides": list(self.overrides),
        }


def override_key(override: str) -> str:
    """Return a normalized dotted key from one Hydra override."""
    text = str(override).strip()
    if not text or "=" not in text or text.startswith("~"):
        raise ValueError(f"Branch override must be a key=value assignment: {override!r}")
    key, value = text.split("=", 1)
    key = key.lstrip("+").strip()
    if not key or not value.strip() or not _OVERRIDE_KEY.fullmatch(key):
        raise ValueError(f"Invalid branch override: {override!r}")
    if any(character in text for character in ("\n", "\r", "\x00")):
        raise ValueError("Branch overrides must be single-line assignments")
    return key


def _owns_path(key: str, path: str) -> bool:
    return key == path or key.startswith(f"{path}.")


def _validate_overrides(overrides: tuple[str, ...]) -> None:
    """Validate override syntax while leaving compatibility to runtime contracts."""
    seen: set[str] = set()
    for override in overrides:
        key = override_key(override)
        if key in seen:
            raise ValueError(f"Branch override {key!r} is assigned more than once")
        seen.add(key)
        if any(_owns_path(key, path) for path in _LAUNCHER_OWNED_PATHS):
            raise ValueError(f"Branch override {key!r} is managed by the branch launcher")


def _validate_backend_isolation(checkpoint: dict[str, Any]) -> None:
    if not checkpoint_has_backend_state(checkpoint):
        raise ValueError(
            "This checkpoint does not contain authoritative state for every game master; "
            "an independent branch cannot be created safely."
        )
    metadata = checkpoint_runtime_metadata(checkpoint)
    game_masters = metadata.get("game_masters")
    if not isinstance(game_masters, list) or not game_masters:
        raise ValueError("Checkpoint is missing game-master branch capability metadata")
    unsupported = [
        str(item.get("name") or "?")
        for item in game_masters
        if not isinstance(item, dict) or not item.get("supports_checkpoint_branching")
    ]
    if unsupported:
        raise ValueError(
            "Independent checkpoint branching is not supported by game master(s): "
            + ", ".join(unsupported)
        )


def plan_checkpoint_branch(
    source_run: str | Path,
    *,
    checkpoint_step: int | None = None,
    mode: str = "exact",
    continuation_seed: int | None = None,
    overrides: tuple[str, ...] = (),
) -> BranchPlan:
    """Validate and resolve a branch request without mutating the parent run."""
    branch_mode = str(mode).strip().lower()
    if branch_mode not in {"exact", "resample"}:
        raise ValueError("Branch mode must be 'exact' or 'resample'")
    if branch_mode == "exact" and continuation_seed is not None:
        raise ValueError("An exact branch cannot set continuation_seed")
    if branch_mode == "resample" and continuation_seed is None:
        raise ValueError("A resampled branch requires continuation_seed")
    if continuation_seed is not None and (
        isinstance(continuation_seed, bool) or not isinstance(continuation_seed, int)
    ):
        raise ValueError("continuation_seed must be an integer")

    source = Path(source_run).expanduser().resolve()
    checkpoint_path = resolve_checkpoint_source(source, checkpoint_step)
    checkpoint = load_checkpoint_file(checkpoint_path)
    resolved_step = checkpoint.get("step")
    if isinstance(resolved_step, bool) or not isinstance(resolved_step, int):
        raise ValueError("Checkpoint is missing its integer step")
    _validate_backend_isolation(checkpoint)
    normalized = tuple(str(item).strip() for item in overrides if str(item).strip())
    _validate_overrides(normalized)

    for override in normalized:
        if override_key(override) != "num_steps":
            continue
        value = yaml.safe_load(override.split("=", 1)[1])
        if isinstance(value, bool) or not isinstance(value, int) or value <= resolved_step:
            raise ValueError(
                f"num_steps must be an integer greater than checkpoint step {resolved_step}"
            )

    return BranchPlan(
        source_run=source,
        checkpoint_path=checkpoint_path,
        checkpoint_step=resolved_step,
        mode=branch_mode,
        continuation_seed=continuation_seed,
        overrides=normalized,
    )


def _validate_component_records(name: Any, live_gm: Any, saved: dict[str, Any]) -> None:
    saved_states = saved.get("components")
    saved_classes = saved.get("component_classes", {})
    saved_types = saved.get("component_types")
    if (
        not isinstance(saved_states, Mapping)
        or not isinstance(saved_classes, Mapping)
        or not isinstance(saved_types, Mapping)
    ):
        raise ValueError(f"Checkpoint game master {name!r} has invalid component metadata")
    live_components = getattr(live_gm, "components", {})
    for key, saved_state in saved_states.items():
        component = live_components.get(str(key))
        expected = saved_classes.get(str(key))
        if component is None:
            if saved_state:
                raise ValueError(
                    f"Branch removes stateful component {key!r} from game master {name!r}"
                )
            continue
        actual = f"{type(component).__module__}.{type(component).__qualname__}"
        saved_type = saved_types.get(str(key)) or expected
        if saved_type == actual:
            continue
        if expected:
            raise ValueError(
                f"Branch replaces stateful component {key!r} on game master {name!r}; "
                "only stateless component replacements are supported"
            )
        getter = getattr(component, "get_state", None)
        live_state = getter() if callable(getter) else {}
        if live_state:
            raise ValueError(
                f"Branch replaces stateless component {key!r} on game master {name!r} "
                "with a stateful component; only stateless replacements are supported"
            )
    for key in set(live_components) - set(saved_types):
        component = live_components[key]
        getter = getattr(component, "get_state", None)
        if callable(getter) and getter():
            raise ValueError(
                f"Branch adds stateful component {key!r} to game master {name!r}; "
                "only stateless component additions are supported"
            )


def _validate_object_identity(runtime: Any, checkpoint: dict[str, Any]) -> dict[str, Any]:
    objects = checkpoint.get("objects")
    if not isinstance(objects, dict):
        raise ValueError("Checkpoint objects must be a mapping")
    live_specs = getattr(runtime, "object_specs", None)
    if not isinstance(live_specs, dict):
        raise ValueError("Runtime does not expose object_specs for branch validation")
    saved_names = {str(name) for name in objects}
    live_names = {str(name) for name in live_specs}
    if saved_names != live_names:
        added = sorted(live_names - saved_names)
        removed = sorted(saved_names - live_names)
        raise ValueError(
            f"Branch cannot change the agent/game-master roster (added={added}, removed={removed})"
        )
    for name, record in objects.items():
        if not isinstance(record, Mapping):
            raise ValueError(f"Checkpoint object {name!r} must be a mapping")
        spec = live_specs[str(name)]
        role = getattr(spec.role, "value", spec.role)
        saved_identity = (
            str(record.get("role") or ""),
            str(record.get("class_path") or ""),
            record.get("compat") or None,
        )
        live_identity = (
            str(role),
            str(spec.class_path),
            spec.compat or None,
        )
        if saved_identity != live_identity:
            raise ValueError(
                f"Branch cannot change runtime object {name!r} identity: "
                f"checkpoint={saved_identity}, child={live_identity}"
            )
    return objects


def _saved_game_masters(checkpoint: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = checkpoint_runtime_metadata(checkpoint).get("game_masters")
    if not isinstance(raw, list):
        raise ValueError("Checkpoint is missing game-master branch metadata")
    return {
        str(item.get("name") or ""): dict(item)
        for item in raw
        if isinstance(item, Mapping) and item.get("name")
    }


def _validate_game_master(
    name: str, live_gm: Any, spec: Any, record: Mapping[str, Any], metadata: Mapping[str, Any]
) -> None:
    saved = record.get("state")
    if not isinstance(saved, dict):
        raise ValueError(f"Checkpoint game master {name!r} has invalid state")
    backend = saved.get("backend")
    if not isinstance(backend, Mapping):
        raise ValueError(f"Checkpoint game master {name!r} has invalid backend metadata")
    saved_backend = (
        str(backend.get("backend_type") or ""),
        str(backend.get("backend_class") or ""),
    )
    live_backend = getattr(live_gm, "backend", None)
    live_backend_identity = (
        str(getattr(live_gm, "backend_type", "") or ""),
        f"{type(live_backend).__module__}.{type(live_backend).__qualname__}",
    )
    if saved_backend != live_backend_identity:
        raise ValueError(
            f"Branch cannot change game master {name!r} backend identity from "
            f"{saved_backend!r} to {live_backend_identity!r}"
        )
    saved_sequence = int(metadata.get("sequence", 0) or 0)
    live_sequence = int(spec.params.get("sequence", 0) or 0)
    scheduling = saved.get("scheduling")
    if not isinstance(scheduling, Mapping):
        raise ValueError(f"Checkpoint game master {name!r} is missing scheduling metadata")
    saved_routing = (
        dict(scheduling.get("agent_flow_tags") or {}),
        tuple(scheduling.get("owned_flows") or ()),
        saved_sequence,
    )
    live_routing = (
        dict(getattr(live_gm, "agent_flow_tags", {}) or {}),
        tuple(getattr(live_gm, "owned_flows", ()) or ()),
        live_sequence,
    )
    if saved_routing != live_routing:
        raise ValueError(
            f"Branch cannot change game master {name!r} flow ownership, agent-flow "
            "assignment, or sequence"
        )
    _validate_component_records(name, live_gm, saved)


def validate_runtime_branch_compatibility(runtime: Any, checkpoint: dict[str, Any]) -> None:
    """Validate a composed child against checkpoint-owned runtime contracts.

    This runs after the child runtime is constructed but before checkpoint state
    is applied. Config keys are intentionally absent from this check: object
    identity, backend identity, routing identity, and component state determine
    compatibility, so custom implementations participate without registration.
    """
    game_masters = {str(gm.name): gm for gm in runtime.game_masters_by_sequence()}
    objects = _validate_object_identity(runtime, checkpoint)
    metadata = _saved_game_masters(checkpoint)
    if set(metadata) != set(game_masters):
        raise ValueError("Branch cannot change the game-master topology")
    for name, live_gm in game_masters.items():
        record = objects[name]
        spec = runtime.object_specs[name]
        _validate_game_master(name, live_gm, spec, record, metadata[name])
