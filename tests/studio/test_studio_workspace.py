"""External project discovery and source-aware Studio behavior."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from silisocs.studio.app import create_app
from silisocs.studio.workspace import WorkspaceCatalog


def _external_project(root: Path, package: str = "project_extensions") -> Path:
    project = root / "external"
    conf = project / "scenarios" / "custom_world" / "conf"
    (conf / "world").mkdir(parents=True)
    (conf / "world" / "default.yaml").write_text(
        "# @package _global_\nscenario_name: custom_world\nnum_agents: 1\nnum_steps: 1\nseed: 1\n",
        encoding="utf-8",
    )
    (conf / "agents").mkdir()
    (conf / "agents" / "default.yaml").write_text(
        "# @package agents\npersona_pipeline:\n  classes: {}\n",
        encoding="utf-8",
    )
    (conf / "sim.yaml").write_text("{}\n", encoding="utf-8")
    (conf / "env.yaml").write_text("{}\n", encoding="utf-8")

    source = project / "src" / package
    source.mkdir(parents=True)
    (source / "__init__.py").write_text("", encoding="utf-8")
    (source / "components.py").write_text(
        "from silisocs.analysis.exploration import ExplorationScene\n"
        "from silisocs.analysis.panel import Markdown, Panel\n"
        "from silisocs.environments.gm.components.base import ObservationComponent\n\n"
        "class CustomObservation(ObservationComponent):\n"
        "    def make_observation(self, agent_name: str) -> str:\n"
        "        return agent_name\n\n"
        "class CustomPanel(Panel):\n"
        "    name = 'custom_panel'\n"
        "    title = 'Custom panel'\n"
        "    scope = 'run'\n"
        "    def build(self, artifact, params):\n"
        "        return Markdown('custom')\n\n"
        "class CustomScene(ExplorationScene):\n"
        "    id = 'custom_scene'\n"
        "    title = 'Custom scene'\n"
        "    renderer = '/api/custom-scene'\n",
        encoding="utf-8",
    )
    return project


def test_workspace_discovers_scenarios_and_classes_by_runtime_contract(tmp_path: Path) -> None:
    project = _external_project(tmp_path)
    workspace = WorkspaceCatalog(
        tmp_path / "main",
        tmp_path / "state",
        additional=(project,),
    )

    source = next(item for item in workspace.sources if not item.primary)
    scenarios = workspace.scenarios()
    extensions = workspace.extension_catalog()

    assert scenarios == [
        {
            "name": "custom_world",
            "path": str(project / "scenarios" / "custom_world"),
            "files": [
                "world/default.yaml",
                "agents/default.yaml",
                "sim.yaml",
                "env.yaml",
            ],
            "source": source.id,
            "source_label": "external",
            "source_path": str(project),
            "config_pattern": str(project / "scenarios" / "{scenario}" / "conf"),
        }
    ]
    assert {item["value"] for item in extensions["component.observe"]} == {
        "project_extensions.components.CustomObservation"
    }
    assert {item["value"] for item in extensions["analysis.panels"]} == {
        "project_extensions.components.CustomPanel"
    }
    assert {item["value"] for item in extensions["analysis.scenes"]} == {
        "project_extensions.components.CustomScene"
    }

    components = project / "src" / "project_extensions" / "components.py"
    components.write_text(
        components.read_text(encoding="utf-8")
        + "\nclass SecondObservation(ObservationComponent):\n"
        + "    def make_observation(self, agent_name: str) -> str:\n"
        + "        return agent_name.upper()\n",
        encoding="utf-8",
    )
    workspace.refresh_extensions()

    assert {item["value"] for item in workspace.extension_catalog()["component.observe"]} == {
        "project_extensions.components.CustomObservation",
        "project_extensions.components.SecondObservation",
    }


def test_repository_api_and_external_scenario_page(tmp_path: Path) -> None:
    project = _external_project(tmp_path)
    app = create_app(
        tmp_path / "outputs",
        state_dir=tmp_path / "state",
        repo_root=tmp_path / "main",
    )
    client = TestClient(app)

    added = client.post(
        "/api/repositories",
        json={"path": str(project), "nickname": "Policy lab"},
    )

    assert added.status_code == 200
    source = added.json()["source"]
    assert source["nickname"] == "Policy lab"
    assert (tmp_path / "main" / ".silisocs" / "repositories.yaml").is_file()
    page = client.get(f"/scenarios/custom_world?source={source['id']}")
    assert page.status_code == 200
    assert "project_extensions.components.CustomObservation" not in page.text
    assert 'data-choices-from="component.observe.classes"' in page.text
    assert "Policy lab" in page.text

    choices = client.post(
        "/api/form-choices",
        json={
            "provider": "component.observe.classes",
            "files": {},
            "source": source["id"],
        },
    )
    assert choices.status_code == 200
    assert {
        "value": "project_extensions.components.CustomObservation",
        "label": "Policy lab -> CustomObservation",
    } in choices.json()["items"]


def test_repository_nickname_persists_and_can_be_changed(tmp_path: Path) -> None:
    project = _external_project(tmp_path)
    state = tmp_path / "state"
    workspace = WorkspaceCatalog(tmp_path / "main", state)

    source = workspace.add(project, nickname="Policy lab")
    persisted = yaml.safe_load((state / "repositories.yaml").read_text(encoding="utf-8"))

    assert persisted == {
        "version": 1,
        "repositories": [{"path": str(project), "nickname": "Policy lab"}],
    }
    reopened = WorkspaceCatalog(tmp_path / "main", state)
    assert reopened.source(source.id).label == "Policy lab"

    reopened.rename(source.id, "Behavior group")
    assert WorkspaceCatalog(tmp_path / "main", state).source(source.id).label == "Behavior group"


def test_repository_nicknames_are_unique(tmp_path: Path) -> None:
    first = _external_project(tmp_path / "first", "first_extensions")
    second = _external_project(tmp_path / "second", "second_extensions")
    workspace = WorkspaceCatalog(tmp_path / "main", tmp_path / "state")

    workspace.add(first, nickname="Lab")

    with pytest.raises(ValueError, match="already used"):
        workspace.add(second, nickname="lab")


def test_repository_connection_is_available_from_scenario_and_study_pages(
    tmp_path: Path,
) -> None:
    client = TestClient(
        create_app(
            tmp_path / "outputs",
            state_dir=tmp_path / "state",
            repo_root=tmp_path / "main",
        )
    )

    for route in ("/scenarios", "/studies"):
        page = client.get(route)
        assert page.status_code == 200
        assert "Connect project" in page.text
        assert 'name="nickname"' in page.text
        assert 'name="path"' in page.text


def test_external_launch_uses_project_environment(tmp_path: Path) -> None:
    project = _external_project(tmp_path)
    app = create_app(
        tmp_path / "outputs",
        state_dir=tmp_path / "state",
        repo_root=tmp_path / "main",
        scenario_repositories=(project,),
    )
    workspace = app.state.studio.workspace
    source = next(item for item in workspace.sources if not item.primary)
    submitted = Mock()
    submitted.to_dict.return_value = {"id": "queued"}
    app.state.studio.jobs.submit = Mock(return_value=submitted)
    files = {
        name: item["text"]
        for name, item in workspace.scenario_repository(source.id)
        .load("custom_world")["files"]
        .items()
    }

    response = TestClient(app).post(
        "/api/launch",
        json={
            "name": "custom_world",
            "config_yaml": {"files": files},
            "source": source.id,
        },
    )

    assert response.status_code == 200
    call = app.state.studio.jobs.submit.call_args.kwargs
    assert call["cwd"] == project
    assert str(project / "src") in call["env"]["PYTHONPATH"].split(":")
