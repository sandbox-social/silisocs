"""Tests for single-call structured probe questionnaire flow."""

from __future__ import annotations

from typing import Any, cast

from concordia.typing import entity

from mastodon_sim.evaluations.probes.agent_speech import AgentQuery, deploy_probes_to_agent


class _DummyProbeLogger:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def log(self, log_data):
        if isinstance(log_data, list):
            self.records.extend(log_data)
        else:
            self.records.append(log_data)


class _DummyAgent:
    def __init__(self, name: str, responses: list[object]) -> None:
        self._agent_name = name
        self._responses = list(responses)
        self.call_count = 0

    def act(self, action_spec: entity.ActionSpec) -> str:
        _ = action_spec
        self.call_count += 1
        if self._responses:
            response = self._responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return str(response)
        return ""


class _EchoQuery(AgentQuery):
    def __init__(self, name: str, question: str):
        self.name = name
        self.query_text = {"question_template": {"text": question}}
        super().__init__({})

    def parse_answer(self, agent_says) -> str:
        return (agent_says or "").strip().upper()


def test_structured_questionnaire_uses_single_llm_call() -> None:
    agent = _DummyAgent(
        "Alice",
        ["Q0: yes\nQ1: no"],
    )
    logger = _DummyProbeLogger()
    queries = [_EchoQuery("Q0", "Question 0?"), _EchoQuery("Q1", "Question 1?")]

    deploy_probes_to_agent(agent, queries, logger)

    assert agent.call_count == 1
    assert len(logger.records) == 2
    assert logger.records[0]["data"]["query_mode"] == "single_structured_lines"
    assert logger.records[0]["data"]["query_return"] == "YES"
    assert logger.records[1]["data"]["query_return"] == "NO"


def test_structured_questionnaire_handles_agent_name_prefix() -> None:
    """Concordia prepends agent name to the first line of model output."""
    agent = _DummyAgent(
        "Jailyn Claybon",
        ["Jailyn Claybon Q0: Bill\nQ1: 7\nQ2: 6\nQ3: yes"],
    )
    logger = _DummyProbeLogger()
    queries = [
        _EchoQuery("Q0", "Vote?"),
        _EchoQuery("Q1", "Score1?"),
        _EchoQuery("Q2", "Score2?"),
        _EchoQuery("Q3", "WillVote?"),
    ]

    deploy_probes_to_agent(agent, queries, logger)

    assert agent.call_count == 1, f"Expected 1 call, got {agent.call_count}"
    assert len(logger.records) == 4
    assert logger.records[0]["data"]["query_return"] == "BILL"
    assert logger.records[0]["data"]["query_mode"] == "single_structured_lines"
    assert logger.records[1]["data"]["query_return"] == "7"
    assert logger.records[2]["data"]["query_return"] == "6"
    assert logger.records[3]["data"]["query_return"] == "YES"


def test_structured_questionnaire_error_falls_back_to_legacy_mode() -> None:
    agent = _DummyAgent("Alice", [RuntimeError("probe-act-failed"), "legacy-a", "legacy-b"])
    logger = _DummyProbeLogger()
    queries = [_EchoQuery("Q0", "Question 0?"), _EchoQuery("Q1", "Question 1?")]

    deploy_probes_to_agent(agent, queries, logger)

    # 1 failed structured call + 2 legacy per-query calls
    assert agent.call_count == 3
    assert len(logger.records) == 2
    assert logger.records[0]["data"]["query_mode"] == "legacy_per_query"
    assert logger.records[0]["data"]["query_return"] == "LEGACY-A"
    assert "probe-act-failed" in logger.records[0]["data"]["structured_query_error"]
    assert logger.records[1]["data"]["query_return"] == "LEGACY-B"


def test_structured_questionnaire_normalizes_one_based_indexes() -> None:
    agent = _DummyAgent(
        "Alice",
        ["Q1: yes\nQ2: no"],
    )
    logger = _DummyProbeLogger()
    queries = [_EchoQuery("Q0", "Question 0?"), _EchoQuery("Q1", "Question 1?")]

    deploy_probes_to_agent(agent, queries, logger)

    assert agent.call_count == 1
    assert len(logger.records) == 2
    assert logger.records[0]["data"]["query_mode"] == "single_structured_lines"
    assert logger.records[0]["data"]["query_return"] == "YES"
    assert logger.records[1]["data"]["query_return"] == "NO"


def test_structured_missing_answer_falls_back_only_for_failed_query() -> None:
    agent = _DummyAgent(
        "Alice",
        [
            "Q1: no",  # structured call missing q0
            "legacy-yes",  # fallback only for q0
        ],
    )
    logger = _DummyProbeLogger()
    queries = [_EchoQuery("Q0", "Question 0?"), _EchoQuery("Q1", "Question 1?")]

    deploy_probes_to_agent(agent, queries, logger)

    assert agent.call_count == 2
    assert len(logger.records) == 2

    assert logger.records[0]["label"] == "Q0"
    assert logger.records[0]["data"]["query_mode"] == "legacy_per_query_fallback"
    assert logger.records[0]["data"]["query_return"] == "LEGACY-YES"
    assert (
        logger.records[0]["data"]["structured_fallback_reason"]
        == "missing_or_empty_structured_answer:q0"
    )

    assert logger.records[1]["label"] == "Q1"
    assert logger.records[1]["data"]["query_mode"] == "single_structured_lines"
    assert logger.records[1]["data"]["query_return"] == "NO"


def test_structured_uses_probe_name_as_log_label() -> None:
    agent = _DummyAgent("Alice", ["Q0: yes"])
    logger = _DummyProbeLogger()
    query = _EchoQuery("VoteIntent", "Will you vote?")
    cast(Any, query).probe_name = "vote_intent"

    deploy_probes_to_agent(agent, [query], logger)

    assert len(logger.records) == 1
    assert logger.records[0]["label"] == "vote_intent"
    assert logger.records[0]["data"]["query_type"] == "VoteIntent"
