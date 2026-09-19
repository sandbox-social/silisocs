"""Checkpoint-branch planning and compatibility tests."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from silisocs.runtime.checkpointing import list_checkpoint_steps, resolve_checkpoint_source
from silisocs.runtime.execution.branching import (
    plan_checkpoint_branch,
    validate_runtime_branch_compatibility,
)


def _checkpoint(
    root: Path,
    step: int,
    *,
    branchable: bool = True,
    stateful: bool = False,
    component_type: str = "lab.Stateless",
) -> Path:
    directory = root / "checkpoints"
    directory.mkdir(parents=True, exist_ok=True)
    component_state = {"cursor": 1} if stateful else {}
    data = {
        "step": step,
        "objects": {
            "gm": {
                "class_path": "lab.GM",
                "role": "game_master",
                "compat": None,
                "state": {
                    "backend": {
                        "backend_type": "custom",
                        "backend_class": f"{_Backend.__module__}.{_Backend.__qualname__}",
                        "state": {"world": step},
                    },
                    "components": {"observe": component_state},
                    "component_classes": {"observe": component_type} if stateful else {},
                    "component_types": {"observe": component_type},
                    "scheduling": {"agent_flow_tags": {}, "owned_flows": []},
                },
            }
        },
        "runtime_metadata": {
            "game_masters": [
                {
                    "name": "gm",
                    "sequence": 0,
                    "supports_checkpoint_branching": branchable,
                }
            ]
        },
    }
    path = directory / f"step_{step}_checkpoint.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_exact_checkpoint_selection_and_branch_plan(tmp_path: Path) -> None:
    first = _checkpoint(tmp_path, 2)
    _checkpoint(tmp_path, 5)

    assert list_checkpoint_steps(tmp_path) == [2, 5]
    assert resolve_checkpoint_source(tmp_path, 2) == first
    assert resolve_checkpoint_source(tmp_path).name == "step_5_checkpoint.json"
    with pytest.raises(TypeError, match="integer or None"):
        resolve_checkpoint_source(tmp_path, True)

    plan = plan_checkpoint_branch(
        tmp_path,
        checkpoint_step=2,
        overrides=("num_steps=8", "++experiment.variant=alternate"),
    )
    assert plan.checkpoint_step == 2
    assert plan.mode == "exact"
    assert plan.continuation_seed is None


def test_branch_plan_accepts_config_and_reserves_launcher_fields(tmp_path: Path) -> None:
    _checkpoint(tmp_path, 3)
    plan = plan_checkpoint_branch(
        tmp_path,
        overrides=(
            "experiment.temperature=0.3",
            "++custom_extension.mode=alternate",
        ),
    )
    assert len(plan.overrides) == 2
    with pytest.raises(ValueError, match="managed by the branch launcher"):
        plan_checkpoint_branch(tmp_path, overrides=("output_dir=/tmp/other",))
    with pytest.raises(ValueError, match="managed by the branch launcher"):
        plan_checkpoint_branch(tmp_path, overrides=("seed=10",))
    with pytest.raises(ValueError, match="greater than checkpoint step 3"):
        plan_checkpoint_branch(tmp_path, overrides=("num_steps=3",))

    unsupported = tmp_path / "external"
    _checkpoint(unsupported, 1, branchable=False)
    with pytest.raises(ValueError, match="not supported by game master"):
        plan_checkpoint_branch(unsupported)


def test_branch_plan_defers_component_compatibility_to_constructed_runtime(tmp_path: Path) -> None:
    _checkpoint(tmp_path, 4, stateful=True)
    plan = plan_checkpoint_branch(
        tmp_path,
        overrides=("env.gm.components.observe.params.limit=10",),
    )
    assert plan.overrides == ("env.gm.components.observe.params.limit=10",)


def test_resampled_branch_requires_and_records_seed(tmp_path: Path) -> None:
    _checkpoint(tmp_path, 1)
    with pytest.raises(ValueError, match="requires continuation_seed"):
        plan_checkpoint_branch(tmp_path, mode="resample")
    plan = plan_checkpoint_branch(tmp_path, mode="resample", continuation_seed=91)
    assert plan.continuation_seed == 91


class _Stateless:
    def get_state(self):
        return {}


class _Stateful:
    def get_state(self):
        return {"cursor": 0}


class _OtherStateful(_Stateful):
    pass


class _Backend:
    pass


class _OtherBackend:
    pass


def _class_id(value) -> str:
    return f"{type(value).__module__}.{type(value).__qualname__}"


def _runtime(component, *, backend_type: str = "custom", backend=None):
    gm = SimpleNamespace(
        name="gm",
        components={"observe": component},
        backend=backend if backend is not None else _Backend(),
        backend_type=backend_type,
        agent_flow_tags={},
        owned_flows=(),
    )
    spec = SimpleNamespace(
        role="game_master",
        class_path="lab.GM",
        compat=None,
        params={"sequence": 0},
    )
    return SimpleNamespace(
        object_specs={"gm": spec},
        game_masters_by_sequence=lambda: [gm],
    )


def test_runtime_branch_guard_accepts_only_stateless_component_replacements(tmp_path: Path) -> None:
    path = _checkpoint(tmp_path, 1)
    checkpoint = json.loads(path.read_text(encoding="utf-8"))
    validate_runtime_branch_compatibility(_runtime(_Stateless()), checkpoint)
    with pytest.raises(ValueError, match="with a stateful component"):
        validate_runtime_branch_compatibility(_runtime(_Stateful()), checkpoint)


def test_runtime_branch_guard_allows_same_stateful_class_but_rejects_identity_changes(
    tmp_path: Path,
) -> None:
    component = _Stateful()
    path = _checkpoint(
        tmp_path,
        1,
        stateful=True,
        component_type=_class_id(component),
    )
    checkpoint = json.loads(path.read_text(encoding="utf-8"))
    validate_runtime_branch_compatibility(_runtime(component), checkpoint)

    with pytest.raises(ValueError, match="replaces stateful component"):
        validate_runtime_branch_compatibility(_runtime(_OtherStateful()), checkpoint)
    with pytest.raises(ValueError, match="backend identity"):
        validate_runtime_branch_compatibility(
            _runtime(component, backend_type="different"), checkpoint
        )
    with pytest.raises(ValueError, match="backend identity"):
        validate_runtime_branch_compatibility(
            _runtime(component, backend=_OtherBackend()), checkpoint
        )


def test_runtime_branch_guard_rejects_roster_and_routing_changes(tmp_path: Path) -> None:
    path = _checkpoint(tmp_path, 1)
    checkpoint = json.loads(path.read_text(encoding="utf-8"))

    roster_runtime = _runtime(_Stateless())
    roster_runtime.object_specs["Alice"] = SimpleNamespace(
        role="agent",
        class_path="lab.Agent",
        compat=None,
        params={},
    )
    with pytest.raises(ValueError, match="roster"):
        validate_runtime_branch_compatibility(roster_runtime, checkpoint)

    routing_runtime = _runtime(_Stateless())
    routing_runtime.game_masters_by_sequence()[0].agent_flow_tags = {"Alice": "research"}
    with pytest.raises(ValueError, match="flow ownership"):
        validate_runtime_branch_compatibility(routing_runtime, checkpoint)
