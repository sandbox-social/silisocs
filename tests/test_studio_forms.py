"""Contracts for Studio's backend-neutral declarative composer."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from silisocs.studio.forms import (
    PreviewContext,
    ScenarioRepository,
    compose_files,
    materialize_form_schema,
    preflight_payload,
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
