"""Tests for the built-in typed probe system."""

from __future__ import annotations

from silisocs.evaluations.probes.types import (
    BinaryProbe,
    ChoiceProbe,
    FreeTextProbe,
    NumericRatingProbe,
)


class _FakeAgent:
    def __init__(self, name: str):
        self._agent_name = name
        self.name = name


def test_numeric_rating_probe_parses_in_range() -> None:
    probe = NumericRatingProbe({"name": "Fav", "lo": 1, "hi": 10})
    assert probe.parse_answer("I'd say about 7 out of 10") == "7"
    assert probe.parse_answer("definitely a 10") == "10"
    assert probe.parse_answer("0 stars") is None  # out of range
    assert probe.parse_answer("nothing here") is None


def test_numeric_rating_probe_custom_range() -> None:
    probe = NumericRatingProbe({"name": "Score", "lo": 0, "hi": 100})
    assert probe.parse_answer("I'd rate it 85") == "85"
    assert probe.parse_answer("200 for sure") is None  # out of range


def test_binary_probe_parses_yes_no() -> None:
    probe = BinaryProbe({"name": "Intent"})
    assert probe.parse_answer("Yes, I will vote") == "Yes"
    assert probe.parse_answer("No way") == "No"
    assert probe.parse_answer("maybe") is None


def test_choice_probe_matches_full_name_and_tokens() -> None:
    probe = ChoiceProbe(
        {
            "name": "Vote",
            "choices": ["Bill Fredrickson", "Bradley Carter"],
        }
    )
    assert probe.parse_answer("I vote for Bill") == "Bill Fredrickson"
    assert probe.parse_answer("Bradley Carter is my pick") == "Bradley Carter"
    assert probe.parse_answer("Fredrickson all the way") == "Bill Fredrickson"
    assert probe.parse_answer("nobody") is None


def test_free_text_probe_returns_stripped_text() -> None:
    probe = FreeTextProbe({"name": "Thoughts"})
    assert (
        probe.parse_answer("  I think the election is interesting.  ")
        == "I think the election is interesting."
    )
    assert probe.parse_answer("") is None
    assert probe.parse_answer("   ") is None


def test_probe_form_question_substitutes_agent_name() -> None:
    probe = NumericRatingProbe(
        {
            "name": "Fav",
            "question": "Rate candidate from {lo} to {hi}.",
            "context": "{agentname} is rating the candidate.",
            "lo": 1,
            "hi": 10,
        }
    )
    agent = _FakeAgent("Alice Smith")
    question = probe.form_question_for_agent(agent)
    assert "Alice Smith" in question
    assert "1" in question
    assert "10" in question


def test_probe_submit_with_raw_response() -> None:
    probe = BinaryProbe({"name": "VoteIntent"})
    result = probe.submit_with_raw_response("Yes I will definitely vote")
    assert result["probe_type"] == "VoteIntent"
    assert result["probe_return"] == "Yes"
    assert result["raw_response"] == "Yes I will definitely vote"
