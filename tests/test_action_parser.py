from silisocs.environments.gm.components.resolve import find_and_parse_action_data


def test_parse_post_with_empty_target_id() -> None:
    raw = """FINAL DECISION:
ACTION TYPE: POST
TARGET ID:
CONTENT: Hello world
REASONING: New post should not require target id.
"""
    parsed = find_and_parse_action_data(raw)
    assert parsed is not None
    assert parsed["action_type"] == "POST"
    assert parsed["target_id"] == ""
    assert parsed["content"] == "Hello world"


def test_parse_post_with_na_target_id() -> None:
    raw = """FINAL DECISION:
ACTION TYPE: POST
TARGET ID: N/A
CONTENT: Another standalone post.
REASONING: Posting does not reference an existing item.
"""
    parsed = find_and_parse_action_data(raw)
    assert parsed is not None
    assert parsed["action_type"] == "POST"
    assert parsed["target_id"] == ""


def test_parse_reply_extracts_numeric_target_id() -> None:
    raw = """FINAL DECISION:
ACTION TYPE: REPLY
TARGET ID: Tweet ID: 4650
CONTENT: Reply text
REASONING: Replying to a specific tweet.
"""
    parsed = find_and_parse_action_data(raw)
    assert parsed is not None
    assert parsed["action_type"] == "REPLY"
    assert parsed["target_id"] == "4650"
    assert parsed["content"] == "Reply text"


class _RecordingBackend:
    """Minimal backend stub recording parse_and_resolve_action calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def parse_and_resolve_action(self, user_name: str, action_data: dict) -> str:
        self.calls.append((user_name, action_data))
        return "ok"


def test_unparseable_action_is_counted_and_logged(caplog) -> None:
    import logging

    from silisocs.environments.gm.components.resolve import (
        PARSE_FAILURE_COUNTER,
        ParsedActionResolveComponent,
    )
    from silisocs.runtime.telemetry.collector import SimMetricsCollector

    SimMetricsCollector.reset()
    backend = _RecordingBackend()
    component = ParsedActionResolveComponent(backend=backend)
    with caplog.at_level(logging.WARNING):
        result = component.resolve(active_agent="Alice", action="I like turtles")
    assert result == ""
    assert backend.calls == []
    assert SimMetricsCollector.get().counter(PARSE_FAILURE_COUNTER) == 1
    assert any("Dropped action" in record.message for record in caplog.records)


def test_finished_signal_is_not_counted_as_parse_failure(caplog) -> None:
    import logging

    from silisocs.environments.gm.components.resolve import (
        PARSE_FAILURE_COUNTER,
        ParsedActionResolveComponent,
    )
    from silisocs.runtime.telemetry.collector import SimMetricsCollector

    SimMetricsCollector.reset()
    component = ParsedActionResolveComponent(backend=_RecordingBackend())
    with caplog.at_level(logging.WARNING):
        result = component.resolve(active_agent="Alice", action="Finished action episode.")
    assert result == ""
    assert SimMetricsCollector.get().counter(PARSE_FAILURE_COUNTER) == 0
    assert not caplog.records


def test_target_required_action_with_non_numeric_target_is_rejected() -> None:
    from silisocs.environments.gm.components.resolve import (
        INVALID_TARGET_COUNTER,
        ParsedActionResolveComponent,
    )
    from silisocs.runtime.telemetry.collector import SimMetricsCollector

    SimMetricsCollector.reset()
    backend = _RecordingBackend()
    component = ParsedActionResolveComponent(backend=backend)
    raw = """ACTION TYPE: REPLY
TARGET ID: @alice
CONTENT: hello
REASONING: replying
"""
    result = component.resolve(active_agent="Bob", action=raw)
    assert "not a valid numeric post id" in result
    assert backend.calls == []
    assert SimMetricsCollector.get().counter(INVALID_TARGET_COUNTER) == 1
