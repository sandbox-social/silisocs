from __future__ import annotations

import json

from silisocs.evaluations.analysis.dashboard.data_processing import (
    deserialize_data,
    load_data_from_directory,
    load_data_from_folder,
    serialize_data,
)


def _jsonl(rows: list[dict]) -> str:
    return "\n".join(json.dumps(row) for row in rows) + "\n"


def test_dashboard_parser_accepts_current_social_action_logs_without_probes() -> None:
    rows = [
        {
            "episode": 0,
            "event_type": "action",
            "source_user": "Alice",
            "label": "post",
            "data": {"post_id": "1", "post_text": "hello"},
        },
        {
            "episode": 1,
            "event_type": "action",
            "source_user": "Bob",
            "label": "like",
            "data": {"post_id": "1"},
        },
    ]

    graph, interactions, active, posts, probes, actions = load_data_from_folder(
        {
            "action_events.jsonl": _jsonl(rows),
            "prompts_and_responses.jsonl": _jsonl(
                [{"episode_idx": 1, "agent_name": "Bob", "prompt": "Tweet ID: 1"}]
            ),
        }
    )

    assert list(graph.nodes) == []
    assert interactions[1][0]["action"] == "liked"
    assert interactions[1][0]["target"] == "Alice"
    assert active[0] == {"Alice"}
    assert posts["1"]["content"] == "hello"
    assert probes == {}
    assert "prompts" in actions

    serialized = serialize_data(graph, interactions, active, posts, probes, actions)
    round_tripped = deserialize_data(serialized)
    assert round_tripped[3]["1"]["user"] == "Alice"


def test_dashboard_parser_keeps_probe_responses_generic() -> None:
    graph, interactions, active, posts, probes, actions = load_data_from_folder(
        {
            "action_events.jsonl": _jsonl(
                [
                    {
                        "episode": 0,
                        "event_type": "action",
                        "source_user": "Alice",
                        "label": "inspect_market",
                        "data": {"result": "market state"},
                    }
                ]
            ),
            "probe_events.jsonl": _jsonl(
                [
                    {
                        "episode": 0,
                        "event_type": "probe",
                        "source_user": "Alice",
                        "label": "MoodProbe",
                        "data": {"probe_return": "curious"},
                    },
                    {
                        "episode": 0,
                        "event_type": "probe",
                        "source_user": "Bob",
                        "label": "AnyProbeName",
                        "data": {"probe_return": "focused"},
                    },
                ]
            ),
        }
    )

    assert list(graph.nodes) == []
    assert interactions == {}
    assert active == {}
    assert posts == {}
    assert probes == {0: {"Alice": "curious", "Bob": "focused"}}
    assert actions[0][0]["data"]["result"] == "market state"


def test_analysis_dashboard_loads_directory_without_probe_log(tmp_path) -> None:
    (tmp_path / "action_events.jsonl").write_text(
        _jsonl(
            [
                {
                    "episode": 0,
                    "event_type": "action",
                    "source_user": "Alice",
                    "label": "post",
                    "data": {"post_id": "1", "post_text": "hello"},
                }
            ]
        ),
        encoding="utf-8",
    )

    serialized = load_data_from_directory(tmp_path)

    assert serialized is not None
    assert serialized["posts"]["1"]["content"] == "hello"
    assert serialized["probe_data"] == {}


def test_analysis_dashboard_loads_multi_gm_directory_via_run_artifact(tmp_path) -> None:
    """Per-GM event logs (multi-GM layout) are merged through the artifact module."""
    for gm_name, post_id in (("social_gm", "1"), ("world_gm", "2")):
        gm_dir = tmp_path / gm_name
        gm_dir.mkdir()
        (gm_dir / "action_events.jsonl").write_text(
            _jsonl(
                [
                    {
                        "episode": 0,
                        "event_type": "action",
                        "source_user": "Alice",
                        "label": "post",
                        "data": {"post_id": post_id, "post_text": f"hello from {gm_name}"},
                    }
                ]
            ),
            encoding="utf-8",
        )
    (tmp_path / "probe_events.jsonl").write_text(
        _jsonl(
            [
                {
                    "episode": 0,
                    "event_type": "probe",
                    "source_user": "Alice",
                    "label": "MoodProbe",
                    "data": {"probe_return": "curious"},
                }
            ]
        ),
        encoding="utf-8",
    )

    serialized = load_data_from_directory(tmp_path)

    assert serialized is not None
    assert set(serialized["posts"]) == {"1", "2"}
    assert serialized["probe_data"]
