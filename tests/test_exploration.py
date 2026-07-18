"""Contracts for the backend-neutral exploration model."""
# ruff: noqa: PLC0415, PLR2004

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlencode

import pytest

from silisocs.analysis.exploration import (
    ExplorationQuery,
    ExplorationState,
    Scene,
    list_scenes,
    query_entities,
    query_events,
    query_evidence,
    query_relationships,
    query_series,
    register_scene,
    run_capability_document,
    run_story,
    study_capability_document,
)
from silisocs.evaluations.run_artifact import load_run, load_study


def _artifact(tmp_path, *, count: int = 5):
    tmp_path.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "source_user": f"agent-{index % 2}",
            "label": "custom_action" if index % 2 else "inspect",
            "episode": index // 2,
            "backend_type": "custom_world",
            "tags": ["observe"] if index % 2 == 0 else ["change"],
            "data": {"value": index},
        }
        for index in range(count)
    ]
    (tmp_path / "action_events.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    (tmp_path / "run_manifest.json").write_text(
        json.dumps(
            {
                "status": "success",
                "scenario": "custom",
                "num_agents": 2,
                "num_steps": 3,
                "game_masters": [{"name": "world", "backend_type": "custom_world"}],
                "artifacts": {"action_events": ["action_events.jsonl"]},
            }
        ),
        encoding="utf-8",
    )
    return load_run(tmp_path)


def _directed_artifact(tmp_path):
    """A run whose manifest declares a directed-actor role, no backend import needed."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "source_user": "Ada",
            "label": "follow",
            "episode": 0,
            "backend_type": "net",
            "data": {"target": "Bo"},
        },
        {
            "source_user": "Ada",
            "label": "follow",
            "episode": 1,
            "backend_type": "net",
            "data": {"target": "Cy"},
        },
        {
            "source_user": "Bo",
            "label": "follow",
            "episode": 1,
            "backend_type": "net",
            "data": {"target": "Ada"},
        },
        {"source_user": "Cy", "label": "post", "episode": 2, "backend_type": "net", "data": {}},
    ]
    (tmp_path / "action_events.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    (tmp_path / "probe_events.jsonl").write_text(
        json.dumps(
            {
                "source_user": "Ada",
                "label": "mood",
                "episode": 2,
                "anchor": "post_step",
                "data": {"probe_return": "content"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "run_manifest.json").write_text(
        json.dumps(
            {
                "status": "success",
                "scenario": "net",
                "num_agents": 3,
                "num_steps": 3,
                "health": {"agent_turn_failures": 2},
                "game_masters": [
                    {
                        "name": "net",
                        "backend_type": "net",
                        "event_semantics": {
                            "roles": {"network.follow": ["follow"]},
                            "fields": {"network.target_actor": ["target"]},
                        },
                    }
                ],
                "artifacts": {
                    "action_events": ["action_events.jsonl"],
                    "probe_events": ["probe_events.jsonl"],
                },
            }
        ),
        encoding="utf-8",
    )
    return load_run(tmp_path)


def test_relationships_are_declaration_driven(tmp_path) -> None:
    """A backend that maps a label to ``network.follow`` gets a directed graph."""
    artifact = _directed_artifact(tmp_path / "net" / "run")
    document = run_capability_document(artifact, "net/run")
    graph = query_relationships(artifact, ExplorationQuery())

    assert "relationships" in document["features"]
    assert document["evidence"]["has_relationships"] is True
    assert {"source": "Ada", "target": "Bo", "kind": "follow", "weight": 1} in graph["edges"]
    assert {edge["source"] for edge in graph["edges"]} == {"Ada", "Bo"}
    # Cy is a follow target and a poster, so it is a node with no outgoing edge.
    assert "Cy" in {node["id"] for node in graph["nodes"]}


def test_evidence_is_selection_scoped_and_deep_linked(tmp_path) -> None:
    """Evidence for an actor names its probes, neighbours, and shareable refs."""
    artifact = _directed_artifact(tmp_path / "net" / "run")
    evidence = query_evidence(artifact, ExplorationQuery(), entity="Ada")
    kinds = {item["kind"] for item in evidence["items"]}

    assert {"health", "entity", "probe", "relationship"} <= kinds
    probe_item = next(item for item in evidence["items"] if item["kind"] == "probe")
    assert "mood" in probe_item["detail"]
    relationship = next(item for item in evidence["items"] if item["kind"] == "relationship")
    assert relationship["refs"] and relationship["refs"][0]["params"]["actor"]

    # Probe evidence respects the query's time window (the mood probe is episode 2).
    windowed = query_evidence(artifact, ExplorationQuery(end=1), entity="Ada")
    assert "probe" not in {item["kind"] for item in windowed["items"]}


def test_run_story_is_deterministic_and_evidence_linked(tmp_path) -> None:
    """The run story is built from counts and re-derives identically."""
    artifact = _directed_artifact(tmp_path / "net" / "run")
    first = run_story(artifact)
    second = run_story(artifact)
    ids = {beat["id"] for beat in first["beats"]}

    assert first == second
    assert {"behavior", "actor", "network", "health"} <= ids
    for beat in first["beats"]:
        for ref in beat["refs"]:
            assert "params" in ref  # every reference deep-links to its evidence


def test_exploration_state_round_trips_every_url_field() -> None:
    """Repeated filters and selections survive canonical URL serialization."""
    original = ExplorationState(
        "run",
        ("demo/run-1",),
        lens="events",
        scene="custom_scene",
        time_start=2,
        time_end=8,
        entity_ids=("a", "b"),
        event_ids=("action:4",),
        cohort_ids=("treated",),
        labels=("post", "trade"),
        tags=("create",),
        actors=("Ada",),
        backend_types=("custom",),
        comparison_dimension="cohort",
        comparison_baseline="control",
    )
    parsed = parse_qs(urlencode(original.query_items(), doseq=True))

    assert ExplorationState.from_query("run", original.scope_ids, parsed) == original
    assert original.to_dict()["selection"]["entity_ids"] == ["a", "b"]


def test_capabilities_and_scenes_are_declaration_driven(tmp_path) -> None:
    """A custom backend receives generic scenes without a backend-name branch."""
    artifact = _artifact(tmp_path)
    document = run_capability_document(artifact, "custom/run")

    assert {"events", "entities", "pulse"} <= {scene["id"] for scene in document["scenes"]}
    assert document["backend_types"] == ["custom_world"]
    assert document["streams"] == ["action"]
    assert "space" not in {scene["id"] for scene in document["scenes"]}


def test_project_scene_registration_needs_no_studio_edit() -> None:
    """Runtime scene registration is the only extension step."""
    scene = register_scene(
        Scene(
            "test_project_scene",
            "Project scene",
            "run",
            "entities",
            "project",
            requires_all=frozenset({"project.feature"}),
        )
    )

    assert scene in list_scenes("run", {"project.feature"})
    assert scene not in list_scenes("run", {"events"})


def test_event_query_pages_and_filters_with_opaque_cursors(tmp_path) -> None:
    """Cursor pages are stable and filters operate on canonical event fields."""
    artifact = _artifact(tmp_path)
    first = query_events(artifact, ExplorationQuery(limit=2))
    second = query_events(
        artifact,
        ExplorationQuery(limit=2, cursor=first["next_cursor"]),
    )
    filtered = query_events(
        artifact,
        ExplorationQuery(start=1, actors=("agent-1",), limit=20),
    )

    assert [item["id"] for item in first["items"]] == ["action:0", "action:1"]
    assert [item["id"] for item in second["items"]] == ["action:2", "action:3"]
    assert first["next_cursor"]
    assert not first["next_cursor"].isdigit()
    assert all(item["episode"] >= 1 and item["actor"] == "agent-1" for item in filtered["items"])


def test_entity_and_series_queries_share_the_event_filter_language(tmp_path) -> None:
    """Entities and pulse series use the same query instead of bespoke filters."""
    artifact = _artifact(tmp_path)
    query = ExplorationQuery(end=1, limit=10)

    entities = query_entities(artifact, query)
    series = query_series(artifact, query)

    assert entities["total"] == 2
    assert sum(item["event_count"] for item in entities["items"]) == 4
    assert series["episodes"] == [0, 1]
    assert sum(sum(item["values"]) for item in series["series"]) == 4


def test_exploration_shell_is_lazy_and_query_apis_are_paged(tmp_path, monkeypatch) -> None:
    """The shell/capability request does not parse logs; selected scenes do."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from silisocs.evaluations import run_artifact as artifact_module
    from silisocs.studio.app import create_app

    _artifact(tmp_path / "custom" / "run")
    reads = []
    real_iter = artifact_module.iter_jsonl

    def record_iter(files):
        reads.append(tuple(files))
        return real_iter(files)

    monkeypatch.setattr(artifact_module, "iter_jsonl", record_iter)
    client = TestClient(create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path))

    page = client.get("/explore/run/custom/run")
    capabilities = client.get("/api/explore/runs/custom/run/capabilities")

    assert page.status_code == 200
    assert "data-explore-root" in page.text
    assert capabilities.status_code == 200
    assert reads == []

    events = client.get("/api/explore/runs/custom/run/events?limit=2").json()
    assert len(events["items"]) == 2
    assert events["next_cursor"]
    assert reads


def test_study_exploration_uses_the_same_scene_contract(tmp_path) -> None:
    """Study comparison is capability-gated and served by the shared shell model."""
    study_dir = tmp_path / "experiments" / "studies" / "experiment"
    study_dir.mkdir(parents=True)
    (study_dir / "study.yaml").write_text(
        "study:\n"
        "  name: Experiment\n"
        "  question: What changes?\n"
        "  scenarios: [demo]\n"
        "  run_defaults: {seed: 1}\n"
        "hypotheses:\n"
        "  h1:\n"
        "    conditions:\n"
        "      baseline: {overrides: {}}\n",
        encoding="utf-8",
    )
    document = study_capability_document(load_study(study_dir), "experiment")

    assert [scene["id"] for scene in document["scenes"]] == ["compare"]
    assert document["artifact"]["hypotheses"] == 1

    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from silisocs.studio.app import create_app

    client = TestClient(
        create_app(
            tmp_path / "outputs",
            state_dir=tmp_path / "state",
            repo_root=tmp_path,
        )
    )
    page = client.get("/explore/study/experiment")

    assert page.status_code == 200
    assert "Unified exploration" in page.text
    assert client.get("/api/explore/studies/experiment/capabilities").status_code == 200


def _client(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from silisocs.studio.app import create_app

    return TestClient(create_app(tmp_path, state_dir=tmp_path / "state", repo_root=tmp_path))


def test_explore_page_serves_module_and_selection_regions(tmp_path) -> None:
    """The run explore page is a thin bootstrap over the shared client module."""
    _directed_artifact(tmp_path / "net" / "run")
    client = _client(tmp_path)
    page = client.get("/explore/run/net/run")

    assert page.status_code == 200
    assert 'src="/assets/explore.js"' in page.text and "initExplore(" in page.text
    assert "data-transport" in page.text and "data-evidence" in page.text
    assert 'href="#main-content"' in page.text  # skip link is present
    assert client.get("/assets/explore.js").status_code == 200


def test_explore_query_routes_answer_relationships_evidence_and_story(tmp_path) -> None:
    _directed_artifact(tmp_path / "net" / "run")
    client = _client(tmp_path)

    graph = client.get("/api/explore/runs/net/run/relationships").json()
    evidence = client.get("/api/explore/runs/net/run/evidence?entity=Ada").json()
    story = client.get("/api/explore/runs/net/run/story").json()

    assert any(edge["source"] == "Ada" for edge in graph["edges"])
    assert {"health", "entity"} <= {item["kind"] for item in evidence["items"]}
    assert story["beats"]
    assert story["totals"]["events"] > 0


def test_archive_filters_sorts_and_searches_runs(tmp_path) -> None:
    """The archive list applies search, status, and sort server-side."""
    _run_manifest(tmp_path / "alpha" / "r1", "alpha", steps=3, status="success")
    _run_manifest(tmp_path / "beta" / "r1", "beta", steps=5, status="failed")
    client = _client(tmp_path)

    assert [item["scenario"] for item in client.get("/api/runs?q=alpha").json()["items"]] == [
        "alpha"
    ]
    assert [item["scenario"] for item in client.get("/api/runs?status=failed").json()["items"]] == [
        "beta"
    ]
    assert [item["scenario"] for item in client.get("/api/runs?sort=scenario").json()["items"]] == [
        "alpha",
        "beta",
    ]
    assert "archive-filters" in client.get("/runs").text


def test_direct_run_comparison_aligns_on_one_episode_axis(tmp_path) -> None:
    """Two runs compare on a shared episode axis via the query layer."""
    _run_manifest(tmp_path / "alpha" / "r1", "alpha", steps=3, status="success")
    _run_manifest(tmp_path / "beta" / "r1", "beta", steps=5, status="success")
    client = _client(tmp_path)

    compared = client.get("/api/explore/compare?runs=alpha/r1&runs=beta/r1").json()
    assert compared["episodes"] == [0, 1, 2, 3, 4]
    assert {run["id"] for run in compared["runs"]} == {"alpha/r1", "beta/r1"}
    assert client.get("/api/explore/compare?runs=alpha/r1").status_code == 422
    assert client.get("/explore/compare?runs=alpha/r1,beta/r1").status_code == 200


def _run_manifest(run_dir, scenario, *, steps, status):
    run_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        {"source_user": "A", "label": "post", "episode": index, "backend_type": "net", "data": {}}
        for index in range(steps)
    ]
    (run_dir / "action_events.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    (run_dir / "run_manifest.json").write_text(
        json.dumps(
            {
                "status": status,
                "scenario": scenario,
                "num_agents": 2,
                "num_steps": steps,
                "game_masters": [{"name": "net", "backend_type": "net"}],
                "artifacts": {"action_events": ["action_events.jsonl"]},
            }
        ),
        encoding="utf-8",
    )
