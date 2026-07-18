"""Shared memories reach agents as resolved, individual memories.

They are authored in YAML, so they arrive as OmegaConf containers with
interpolations still to resolve; agents must receive the finished text.
"""
# ruff: noqa: D103

from __future__ import annotations

from omegaconf import OmegaConf

from silisocs.runtime.construction.initialization_context import _normalize_memories


def _select(data, path):
    return OmegaConf.select(OmegaConf.create(data), path)


def test_a_config_list_becomes_one_resolved_memory_per_entry():
    memories = _select(
        {
            "event": {"context": "A market session has begun."},
            "agents": {"shared": ["They are at the market.", "${event.context}"]},
        },
        "agents.shared",
    )
    # A ListConfig is not a list: mishandled, the whole list would be str()-ed
    # into one memory holding its repr, interpolation and all.
    assert _normalize_memories(memories) == [
        "They are at the market.",
        "A market session has begun.",
    ]


def test_a_config_block_string_splits_into_lines():
    memories = _select({"agents": {"shared": "first line\nsecond line\n"}}, "agents.shared")
    assert _normalize_memories(memories) == ["first line", "second line"]


def test_a_config_string_resolves_its_interpolation():
    memories = _select(
        {"event": {"context": "Resolved."}, "agents": {"shared": "${event.context}"}},
        "agents.shared",
    )
    assert _normalize_memories(memories) == ["Resolved."]


def test_plain_python_values_and_blanks_are_unchanged():
    assert _normalize_memories(["a", " b ", "", "  "]) == ["a", "b"]
    assert _normalize_memories("solo") == ["solo"]
    assert _normalize_memories(None) == []
    assert _normalize_memories([]) == []
