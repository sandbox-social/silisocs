from __future__ import annotations

import json

from silisocs.evaluations.analysis.dashboard.data_processing import load_data_from_folder


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
