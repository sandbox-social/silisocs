"""Contracts for Studio's backend-neutral declarative composer."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from silisocs.environments.gm.components.factory import component_built_ins
from silisocs.runtime.construction.engines import engine_strategy_built_ins
from silisocs.simulation_engines.policies.factory import policy_built_ins
from silisocs.studio.forms import (
    PreviewContext,
    ScenarioRepository,
    compose_files,
    materialize_form_schema,
    preflight_payload,
    run_choice_provider,
    run_preview_provider,
)


def _files(*, backend_type: str = "resource_market") -> dict[str, str]:
    return {
        "world/default.yaml": "scenario_name: demo\nnum_agents: 2\nnum_steps: 3\nseed: 1\n",
        "agents/default.yaml": yaml.safe_dump(
            {
                "hand_authored": {"preserved": True},
                "persona_pipeline": {
                    "classes": {
                        "trader": {
                            "count": 1,
                            "class_path": "silisocs.agents.native.NativeAgent",
                            "data": {
                                "source": "inline",
                                "records": [{"name": "A", "inventory": 4}],
                            },
                        }
                    }
                },
            },
            sort_keys=False,
        ),
        "sim.yaml": "llm:\n  provider: disabled\n  name: disabled\n",
        "env.yaml": f"gm:\n  backend:\n    type: {backend_type}\n",
        "eval.yaml": "probes:\n  enabled: false\n",
    }


def test_schema_choices_are_discovered_from_capabilities() -> None:
    schema = materialize_form_schema(_files())
    fields = {item["key"]: item for item in schema["fields"]}

    assert "custom" in fields["env.gm.backend.type"]["choices"]
    assert "disabled" in fields["sim.llm.provider"]["choices"]
    assert fields["env.gm.backend.enabled_actions"]["choices"]
    assert fields["agents.persona_pipeline.classes"]["preview_from"] == "persona.records"


def test_expensive_choices_can_be_deferred_without_losing_dependencies() -> None:
    schema = materialize_form_schema(_files(), defer_expensive=True)
    actions = next(
        item for item in schema["fields"] if item["key"] == "env.gm.backend.enabled_actions"
    )

    assert actions["choices"] == []
    assert actions["choices_deferred"] is True
    assert actions["choices_depend_on"] == (
        "env.gm.backend.type",
        "env.gm.backend.class_path",
    )


def test_authoring_built_ins_are_projected_from_runtime_factories() -> None:
    """Studio must expose every option registered by the runtime factories."""
    expected = {
        **{f"component.{role}": values for role, values in component_built_ins().items()},
        **{
            f"policy.{role}": values
            for role, values in {
                **engine_strategy_built_ins(),
                **policy_built_ins(),
            }.items()
        },
    }

    for provider, choices in expected.items():
        assert run_choice_provider(provider, {}) == list(choices)


def test_compose_preserves_unknown_yaml_keys() -> None:
    files = _files()
    composed = compose_files(files, {"world.num_steps": 7})

    assert yaml.safe_load(composed["world/default.yaml"])["num_steps"] == 7
    assert yaml.safe_load(composed["agents/default.yaml"])["hand_authored"] == {"preserved": True}


def test_preflight_reports_bad_numbers_instead_of_raising() -> None:
    files = _files()
    files["world/default.yaml"] = "num_agents: many\nnum_steps: 3\n"

    result = preflight_payload(files)

    assert result["ok"] is False
    assert any(item["path"] == "world.num_agents" for item in result["findings"])


def test_persona_preview_uses_registered_provider(tmp_path: Path) -> None:
    result = run_preview_provider(
        "persona.records",
        _files(),
        "trader",
        PreviewContext(repository_root=tmp_path),
    )

    assert result == {
        "source": "inline",
        "records": [{"name": "A", "inventory": 4}],
    }


def test_repository_accepts_view_documents_but_rejects_other_paths(tmp_path: Path) -> None:
    repository = ScenarioRepository(tmp_path)
    files = _files()
    files["views/lab.yaml"] = yaml.safe_dump(
        {
            "view": {
                "name": "lab",
                "scope": "run",
                "layout": "rows",
                "panels": [{"built_in": "health_summary"}],
            }
        }
    )

    saved = repository.save("demo", files)

    assert "views/lab.yaml" in saved["files"]
    try:
        repository.save("demo", {"../outside.yaml": json.dumps({})})
    except ValueError as exc:
        assert "Unsupported scenario files" in str(exc)
    else:
        raise AssertionError("unsafe scenario-relative path was accepted")


def test_save_preserves_package_directives_and_comments(tmp_path: Path) -> None:
    """Saving must not strip Hydra directives or author comments (verbatim write)."""
    repository = ScenarioRepository(tmp_path)
    files = _files()
    files["world/default.yaml"] = (
        "# @package _global_\n# tuned for the demo\nscenario_name: demo\nnum_agents: 2\n"
    )
    files["agents/default.yaml"] = "# @package agents\nshared_memories: []\n"

    saved = repository.save("demo", files)

    world_text = saved["files"]["world/default.yaml"]["text"]
    assert world_text.startswith("# @package _global_\n")
    assert "# tuned for the demo" in world_text
    assert saved["files"]["agents/default.yaml"]["text"].startswith("# @package agents\n")


def test_save_restores_missing_package_directives(tmp_path: Path) -> None:
    """A directive-less world/agents document gains the directive Hydra requires."""
    repository = ScenarioRepository(tmp_path)

    saved = repository.save("demo", _files())

    world = (tmp_path / "demo" / "conf" / "world" / "default.yaml").read_text(encoding="utf-8")
    agents = (tmp_path / "demo" / "conf" / "agents" / "default.yaml").read_text(encoding="utf-8")
    assert world.splitlines()[0] == "# @package _global_"
    assert agents.splitlines()[0] == "# @package agents"
    assert saved["files"]["world/default.yaml"]["data"]["num_agents"] == 2


def test_compose_preserves_directives_and_untouched_documents() -> None:
    files = _files()
    files["world/default.yaml"] = (
        "# @package _global_\nscenario_name: demo\nnum_agents: 2\nnum_steps: 3\nseed: 1\n"
    )
    files["agents/default.yaml"] = "# @package agents\n# authored\nshared_memories: []\n"

    composed = compose_files(files, {"world.num_steps": 7})

    world_text = composed["world/default.yaml"]
    assert world_text.startswith("# @package _global_\n")
    assert yaml.safe_load(world_text)["num_steps"] == 7
    # Untouched documents pass through byte-for-byte, comments included.
    assert composed["agents/default.yaml"] == files["agents/default.yaml"]
    assert composed["sim.yaml"] == files["sim.yaml"]


def test_non_type_backend_edit_preserves_authored_components() -> None:
    """Editing a non-type backend field must not touch authored GM components."""
    files = _files(backend_type="twitter_like")
    files["env.yaml"] = yaml.safe_dump(
        {
            "gm": {
                "backend": {"type": "twitter_like"},
                "components": {
                    "observe": {"built_in": "timeline_every_turn", "params": {"max_items": 5}},
                    "action_prompt": {"built_in": "custom", "params": {"template": "authored"}},
                },
            }
        },
        sort_keys=False,
    )

    composed = compose_files(files, {"env.gm.backend.enabled_actions": ["post"]})

    env = yaml.safe_load(composed["env.yaml"])
    components = env["gm"]["components"]
    assert components["observe"]["params"] == {"max_items": 5}
    assert components["action_prompt"]["params"] == {"template": "authored"}
    assert env["gm"]["backend"]["enabled_actions"] == ["post"]


def test_backend_type_change_to_non_social_emits_generic_action() -> None:
    """A non-social backend fills absent slots with generic_action + action_mode generic."""
    files = _files(backend_type="twitter_like")
    files["env.yaml"] = yaml.safe_dump({"gm": {"backend": {"type": "twitter_like"}}})

    composed = compose_files(files, {"env.gm.backend.type": "resource_market"})

    env = yaml.safe_load(composed["env.yaml"])
    components = env["gm"]["components"]
    assert components["resolve"]["built_in"] == "generic_action"
    assert components["observe"]["built_in"] == "app_observation"
    assert yaml.safe_load(composed["sim.yaml"])["action_mode"] == "generic"


def test_backend_type_change_with_null_components_does_not_raise() -> None:
    """A draft carrying ``gm: {components: null}`` must compose, not 500."""
    files = _files(backend_type="twitter_like")
    files["env.yaml"] = yaml.safe_dump(
        {"gm": {"backend": {"type": "twitter_like"}, "components": None}}
    )

    composed = compose_files(files, {"env.gm.backend.type": "resource_market"})

    env = yaml.safe_load(composed["env.yaml"])
    assert env["gm"]["components"]["resolve"]["built_in"] == "generic_action"


def test_preflight_resolves_named_world_group_file() -> None:
    """Scenarios shipping world/<name>.yaml must read real num_agents/num_steps."""
    files = {
        "world/resource_market.yaml": "num_agents: 4\nnum_steps: 6\nseed: 1\n",
        "sim.yaml": "llm:\n  provider: disabled\n",
        "env.yaml": "gm:\n  backend:\n    type: resource_market\n",
    }

    result = preflight_payload(files)

    assert result["estimate"]["agent_steps"] == 24
    assert not any(
        item["path"] in {"world.num_agents", "world.num_steps"} for item in result["findings"]
    )


def test_composed_scenario_hydra_composes_run_params_at_root(tmp_path: Path, monkeypatch) -> None:
    """The full Studio authoring loop must yield a scenario Hydra composes correctly."""
    from hydra import compose, initialize_config_dir
    from hydra.core.global_hydra import GlobalHydra

    from silisocs.runtime.configuration.external import register_search_path_plugin

    repository = ScenarioRepository(tmp_path)
    composed = compose_files(_files(), {"world.num_agents": 5})
    repository.save("demo", composed)

    monkeypatch.setenv("SILISOCS_EXTERNAL_CONFIG_DIRS", str(tmp_path / "demo" / "conf"))
    register_search_path_plugin()
    base_conf = Path(__file__).resolve().parents[1] / "src" / "silisocs" / "conf"
    GlobalHydra.instance().clear()
    try:
        with initialize_config_dir(config_dir=str(base_conf), version_base=None):
            cfg = compose(config_name="experiment", overrides=[])
    finally:
        GlobalHydra.instance().clear()

    assert cfg.num_agents == 5, "run params must land at the config root, not under world."
    assert cfg.num_steps == 3
    assert "trader" in cfg.agents.persona_pipeline.classes
