"""Custom ``class_path`` / registration extension seams, positively exercised.

These cover the "user extends without editing core" contract for two seams the
audit found registered-but-untested on the custom path:

* GM components (``env.gm.components.{role}.class_path``): a custom class that fills
  a slot must subclass that role's ABC, and a wrong class is rejected loudly at build
  time (not as a late AttributeError mid-run).
* Replay mappers (``register_replay_mapper``): a real logged event is driven through a
  *user-registered* mapper for a custom ``backend_type`` end-to-end.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from omegaconf import OmegaConf

from silisocs.environments.gm.components.base import NextActingComponent, ResolveComponent
from silisocs.environments.gm.components.factory import _build_from_slot
from silisocs.runtime.checkpointing import register_replay_mapper
from silisocs.runtime.checkpointing.restore import _map_event_for_gm
from silisocs.runtime.construction.engines import build_engine
from silisocs.runtime.types import ActionOutput, ToolCall
from silisocs.simulation_engines.base_engines import RuntimeEngine


class _CustomNextActing(NextActingComponent):
    """A minimal custom next-acting component reachable by class_path."""

    def __init__(self, only: str = "solo") -> None:
        super().__init__()
        self._only = only

    def acting_agent_names(self) -> list[str]:
        return [self._only]


def test_custom_component_correct_base_builds() -> None:
    component = _build_from_slot(
        {"class_path": f"{__name__}._CustomNextActing", "params": {"only": "alice"}},
        built_ins={},
        default_built_in="all_agents",
        expected_base=NextActingComponent,
    )
    assert isinstance(component, NextActingComponent)
    assert component.acting_agent_names() == ["alice"]


def test_custom_component_wrong_base_rejected_at_build_time() -> None:
    # A ResolveComponent named in the next_acting slot fails loudly with the role,
    # instead of building and dying as an AttributeError when scheduled.
    with pytest.raises(TypeError, match="must subclass NextActingComponent"):
        _build_from_slot(
            {
                "class_path": "silisocs.environments.gm.components.resolve.ParsedActionResolveComponent"
            },
            built_ins={},
            default_built_in="all_agents",
            expected_base=NextActingComponent,
        )


def test_wrong_base_check_names_the_class_path() -> None:
    bad_path = "silisocs.environments.gm.components.next_acting.AllAgentsNextActing"
    with pytest.raises(TypeError, match="must subclass ResolveComponent"):
        _build_from_slot(
            {"class_path": bad_path},
            built_ins={},
            default_built_in="parsed_action",
            expected_base=ResolveComponent,
        )


class _FakeGM:
    """A stand-in game master exposing only the backend_type restore reads."""

    def __init__(self, backend_type: str) -> None:
        self.backend_type = backend_type
        self.name = "custom_gm"


def test_custom_replay_mapper_drives_a_real_event() -> None:
    # A third-party backend registers a mapper for its own backend_type; a logged
    # event is then reconstructed through it with no core edit. (The autouse
    # _isolate_replay_mappers fixture keeps this registration out of other tests.)
    def _my_mapper(label: str, data: Mapping[str, Any]) -> ActionOutput | None:
        if label == "shout":
            return ActionOutput.from_tool_calls(
                [ToolCall("broadcast", {"text": str(data["text"])})]
            )
        return None

    register_replay_mapper("my_custom_backend", _my_mapper)
    gm = _FakeGM("my_custom_backend")

    action = _map_event_for_gm(gm, "shout", {"text": "hello world"})
    assert action is not None
    assert action.tool_calls[0].name == "broadcast"
    assert action.tool_calls[0].arguments == {"text": "hello world"}

    # A label the custom mapper does not recognize maps to None (skip), not a crash.
    assert _map_event_for_gm(gm, "whisper", {"text": "x"}) is None


def test_unregistered_backend_type_has_no_mapper() -> None:
    gm = _FakeGM("never_registered_backend")
    assert _map_event_for_gm(gm, "shout", {"text": "x"}) is None


class _CustomSchedule:
    """A custom probe-schedule policy reachable by class_path (runs on even steps)."""

    name = "even_only"

    def __init__(self, *, stride: int = 2) -> None:
        self._stride = stride

    def should_run_probe_phase(self, *, step: int, orchestrator: object) -> bool:
        del orchestrator
        return step % self._stride == 0


def test_custom_probe_schedule_policy_via_class_path() -> None:
    from silisocs.simulation_engines.policies.factory import build_probe_schedule_policy

    policy = build_probe_schedule_policy(
        {"class_path": f"{__name__}._CustomSchedule", "params": {"stride": 3}}
    )
    assert isinstance(policy, _CustomSchedule)
    assert policy.should_run_probe_phase(step=3, orchestrator=None) is True
    assert policy.should_run_probe_phase(step=4, orchestrator=None) is False


class _MarkerEngine(RuntimeEngine):
    """A custom engine reachable via ``sim.engine.class_path`` (no core edit)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.marker = "custom-engine"


def test_custom_engine_via_engine_class_path() -> None:
    cfg = OmegaConf.create(
        {
            "seed": 3,
            "sim": {
                "engine": {
                    "class_path": f"{__name__}._MarkerEngine",
                    "loop": {"built_in": "fixed_steps", "class_path": None, "params": {}},
                    "step": {"built_in": "base", "class_path": None, "params": {}},
                    "turn_policy": {"built_in": "single_action", "class_path": None, "params": {}},
                    "participation": {"built_in": "all", "class_path": None, "params": {}},
                }
            },
        }
    )
    engine = build_engine(cfg)
    assert isinstance(engine, _MarkerEngine)
    assert engine.marker == "custom-engine"
    assert engine.seed == 3


# ---------------------------------------------------------------------------
# Custom Agent via the persona pipeline (the most advertised seam)
# ---------------------------------------------------------------------------


def _marker_agent_class():
    from silisocs.agents.base_agent import Agent
    from silisocs.runtime.types import ActionOutput

    class _MarkerAgent(Agent):
        def __init__(self, *, model, name: str, marker: str, context: str = "") -> None:
            super().__init__(model)
            self._name = name
            self._marker = marker
            self._seen: list[str] = []

        @property
        def name(self) -> str:
            return self._name

        def observe(self, observation: str) -> None:
            self._seen.append(observation)

        def act(self, action_spec):
            from pathlib import Path

            Path(self._marker).write_text(f"acted:{self._name}:{len(self._seen)}")
            return ActionOutput(action="FINISHED")

    return _MarkerAgent


def marker_agent_factory(*, model, name: str, marker: str, context: str = "", **persona):
    """Module-level factory so a class_path can reach the lazily-defined class.

    ``**persona`` absorbs the pipeline's default persona params (bio, goal,
    style, memories, ...) that a minimal custom agent does not model.
    """
    return _marker_agent_class()(model=model, name=name, marker=marker, context=context)


def test_custom_agent_class_path_runs_end_to_end(tmp_path):
    """persona_pipeline.classes.*.class_path builds and runs a user Agent with
    strict params — in-process, via the programmatic API.
    """
    from omegaconf import OmegaConf

    from silisocs import compose_config, load_run, run_simulation

    marker = tmp_path / "acted.txt"
    # env=messaging: the graph-free backend, so one agent needs no follow network.
    cfg = compose_config(
        overrides=["env=messaging", "num_agents=1", "num_steps=1", "sim.llm.provider=scripted"]
    )
    OmegaConf.set_struct(cfg, False)
    cfg.agents.persona_pipeline.classes = {
        "custom": {
            "count": 1,
            "class_path": f"{__name__}.marker_agent_factory",
            "data": {"source": "inline", "records": [{"name": "Echo", "context": "a tester"}]},
            "params": {"name": "Echo", "marker": str(marker), "context": "a tester"},
        }
    }
    OmegaConf.set_struct(cfg, True)

    run_dir = run_simulation(cfg, output_dir=tmp_path / "run")

    assert marker.read_text().startswith("acted:Echo:")
    assert load_run(run_dir).status == "success"


# ---------------------------------------------------------------------------
# Checkpoint SAVE strategy class_path (restore's mirror, previously untested)
# ---------------------------------------------------------------------------


def test_checkpoint_save_class_path_builds_custom_strategy(tmp_path):
    from silisocs.runtime.checkpointing import CheckpointSaveStrategy
    from silisocs.runtime.checkpointing.save import build_checkpoint_save_strategy

    strategy = build_checkpoint_save_strategy(
        {"class_path": f"{__name__}.TinySaveStrategy", "params": {"suffix": "tiny"}}
    )
    assert isinstance(strategy, CheckpointSaveStrategy)
    written = strategy.save({"objects": []}, step=3, checkpoint_path=str(tmp_path))
    assert written.endswith("step_3_checkpoint.json")
    assert (tmp_path / "step_3_checkpoint.json.tiny").exists()

    with pytest.raises(TypeError, match="must subclass CheckpointSaveStrategy"):
        build_checkpoint_save_strategy({"class_path": f"{__name__}._CustomSchedule"})


# ---------------------------------------------------------------------------
# probe_lib_module: bare-name probe resolution from a user module
# ---------------------------------------------------------------------------


def test_probe_lib_module_resolves_custom_probe_class():
    from silisocs.evaluations.probes.agent_speech import _resolve_probe_class
    from silisocs.evaluations.probes.types import FreeTextProbe

    cls = _resolve_probe_class("CustomLibProbe", __name__)
    assert cls is CustomLibProbe
    # Built-ins keep precedence over same-named user classes.
    assert _resolve_probe_class("FreeTextProbe", __name__) is FreeTextProbe
    with pytest.raises(ImportError, match="Unknown probe type"):
        _resolve_probe_class("NoSuchProbe", None)


from silisocs.runtime.checkpointing.save import CheckpointSaveStrategy as _SaveBase


class TinySaveStrategy(_SaveBase):
    """Out-of-tree checkpoint save layout reachable by class_path."""

    def __init__(self, suffix: str = "x") -> None:
        self._suffix = suffix

    def save(self, checkpoint_data, *, step, checkpoint_path):
        import json
        from pathlib import Path

        path = Path(checkpoint_path) / f"step_{step}_checkpoint.json"
        path.write_text(json.dumps(checkpoint_data))
        path.with_name(path.name + f".{self._suffix}").write_text("")
        return str(path)


class CustomLibProbe:
    """A user probe class resolvable through eval.probes.probe_lib_module."""
