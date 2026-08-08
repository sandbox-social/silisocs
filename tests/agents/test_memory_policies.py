"""Tests for agent memory policies (sim.memory) — Tier 2b."""

from __future__ import annotations

from typing import Any

import pytest

from silisocs.agents.memory import (
    MemoryPolicy,
    RetrievalMemory,
    SummarizingMemory,
    WindowMemory,
    build_memory_policy,
)
from silisocs.agents.native import NativeAgent
from silisocs.runtime.language_models.base import NoLanguageModel

# --------------------------------------------------------------- window (default parity)


def test_window_default_matches_legacy_render() -> None:
    agent = NativeAgent(name="A", model=NoLanguageModel(), specific_memories=["seed1", "seed2"])
    for i in range(15):
        agent.observe(f"obs {i}")
    all_mem = ["seed1", "seed2"] + [f"obs {i}" for i in range(15)]
    context = agent._context()
    memory_section = context.split("Memory:\n", 1)[1]
    assert memory_section == "\n".join(all_mem[-10:]), "default must render the last 10 memories"


def test_window_respects_memory_history_cap() -> None:
    mem = WindowMemory(memory_history=5)
    for i in range(20):
        mem.record(f"m{i}")
    assert mem.all_memories() == [f"m{i}" for i in range(15, 20)]


def test_window_state_round_trip() -> None:
    mem = WindowMemory(memory_history=100, render_count=3)
    for i in range(5):
        mem.record(f"m{i}")
    restored = WindowMemory(memory_history=100, render_count=3)
    restored.set_state(mem.get_state())
    assert restored.render() == mem.render() == "m2\nm3\nm4"


# --------------------------------------------------------------- retrieval


def test_retrieval_ranks_relevant_above_recent() -> None:
    # window_count=0 exercises legacy pure-retrieval: rank purely by relevance.
    mem = RetrievalMemory(memory_history=100, window_count=0, retrieved_count=2)
    mem.record("alice loves gardening and tomatoes")
    mem.record("bob discusses quantum computing")
    for i in range(10):
        mem.record(f"unrelated chatter {i}")
    rendered = mem.render(query="tell me about gardening tomatoes")
    assert "gardening" in rendered
    # A recent-but-irrelevant memory should not crowd out the relevant one.
    assert rendered.count("\n") == 1  # exactly retrieved_count=2 lines, no labels


def test_retrieval_is_deterministic_across_order() -> None:
    def build() -> RetrievalMemory:
        m = RetrievalMemory(memory_history=100, window_count=0, retrieved_count=3)
        for text in ["cats", "dogs and cats", "fish", "birds", "dogs"]:
            m.record(text)
        return m

    query = "dogs"
    assert build().render(query=query) == build().render(query=query)


def test_retrieval_recency_fallback_without_query() -> None:
    mem = RetrievalMemory(memory_history=100, window_count=0, retrieved_count=2)
    for i in range(5):
        mem.record(f"m{i}")
    assert mem.render(query=None) == "m3\nm4"


def test_retrieval_window_always_present_and_precedes_retrieved() -> None:
    mem = RetrievalMemory(memory_history=100, window_count=3, retrieved_count=2)
    mem.record("gardening tomatoes basil")  # relevant but far outside the window
    for i in range(10):
        mem.record(f"chatter {i}")
    rendered = mem.render(query="gardening tomatoes")
    # Two labeled sections; retrieved first, the recent window last.
    assert rendered.index("Relevant past memories:") < rendered.index("Recent memories:")
    # The relevant old memory surfaces via retrieval despite the recency window.
    assert "gardening tomatoes basil" in rendered
    # The recent window is always the last 3 verbatim, regardless of relevance.
    assert rendered.rstrip().endswith("chatter 7\nchatter 8\nchatter 9")


def test_retrieval_pool_excludes_window_items() -> None:
    mem = RetrievalMemory(memory_history=100, window_count=2, retrieved_count=3)
    for i in range(6):
        mem.record(f"topic {i}")
    relevant, recent = mem.render(query="topic").split("\n\nRecent memories:\n")
    relevant_lines = relevant.removeprefix("Relevant past memories:\n").split("\n")
    recent_lines = recent.split("\n")
    # Window = last 2; retrieved is drawn only from the older remainder (no dup).
    assert recent_lines == ["topic 4", "topic 5"]
    assert set(relevant_lines).isdisjoint(recent_lines)


def test_retrieval_no_query_fallback_volume() -> None:
    mem = RetrievalMemory(memory_history=100, window_count=4, retrieved_count=3)
    for i in range(20):
        mem.record(f"m{i}")
    # No query -> the last (window + retrieved) memories verbatim, unlabeled.
    assert mem.render(query=None) == "\n".join(f"m{i}" for i in range(13, 20))
    assert "Relevant past memories:" not in mem.render(query=None)


# --------------------------------------------------------------- summarizing


class _StubModel(NoLanguageModel):
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.context_set = False

    def set_runtime_context(self, **_: Any) -> None:
        # Summarization must NOT touch the shared per-turn context (it would
        # misattribute the turn's action call); flag it if it ever does.
        self.context_set = True

    def sample_text(self, prompt: str, **kwargs: Any) -> str:
        self.calls.append(prompt)
        return "SUMMARY"


def test_summarizing_compresses_oldest_once_over_threshold() -> None:
    model = _StubModel()
    mem = SummarizingMemory(model=model, max_memories=5, chunk_size=3, render_count=2)
    for i in range(6):  # 6 > max 5 -> one chunk of 3 summarized
        mem.record(f"m{i}")
    assert len(model.calls) == 1
    assert not model.context_set, "summarization must not overwrite the per-turn runtime context"
    assert mem.all_memories() == ["m3", "m4", "m5"]
    rendered = mem.render()
    assert "SUMMARY" in rendered and "m4\nm5" in rendered


def test_summarizing_no_model_uses_deterministic_fallback() -> None:
    mem = SummarizingMemory(model=None, max_memories=3, chunk_size=2)
    for i in range(4):
        mem.record(f"m{i}")
    # No model -> raw-join fallback; no crash, memory still compressed.
    assert mem.all_memories() == ["m2", "m3"]
    assert "m0" in mem.render()


def test_summarizing_state_round_trip_does_not_resummarize() -> None:
    model = _StubModel()
    mem = SummarizingMemory(model=model, max_memories=3, chunk_size=2)
    for i in range(4):
        mem.record(f"m{i}")
    assert len(model.calls) == 1
    restored = SummarizingMemory(model=model, max_memories=3, chunk_size=2)
    restored.set_state(mem.get_state())
    assert restored.all_memories() == mem.all_memories()
    assert restored.render() == mem.render()
    assert len(model.calls) == 1, "restore must not trigger another summarization"


def test_summarizing_retrieved_tier_surfaces_relevant_old_memory() -> None:
    mem = SummarizingMemory(model=None, max_memories=100, render_count=2, retrieved_count=2)
    mem.record("alice mentioned the gardening budget")  # old, relevant
    for i in range(10):
        mem.record(f"chatter {i}")
    rendered = mem.render(query="gardening budget")
    assert "Relevant past memories:" in rendered
    assert "alice mentioned the gardening budget" in rendered
    # The recent window is still the last render_count memories, verbatim.
    assert rendered.rstrip().endswith("chatter 8\nchatter 9")


def test_summarizing_retrieved_tier_absent_without_query_or_count() -> None:
    off = SummarizingMemory(model=None, max_memories=100, render_count=2, retrieved_count=0)
    for i in range(5):
        off.record(f"m{i}")
    assert "Relevant past memories:" not in off.render(query="m0")  # retrieval disabled

    no_query = SummarizingMemory(model=None, max_memories=100, render_count=2, retrieved_count=3)
    for i in range(5):
        no_query.record(f"m{i}")
    assert "Relevant past memories:" not in no_query.render(query=None)  # no query


def test_summarizing_render_count_zero_omits_recent_window() -> None:
    # render_count=0 means "no recent verbatim window", NOT "dump the whole buffer"
    # (guard against the x[-0:] == x[:] slice quirk).
    mem = SummarizingMemory(model=None, max_memories=100, render_count=0, retrieved_count=0)
    for i in range(5):
        mem.record(f"m{i}")
    assert mem.render() == ""


# --------------------------------------------------------------- factory + wiring


def test_build_memory_policy_selects_built_ins() -> None:
    window = build_memory_policy({"built_in": "window"})(model=None, memory_history=50)
    assert isinstance(window, WindowMemory)
    retrieval = build_memory_policy({"built_in": "retrieval", "params": {"retrieved_count": 4}})(
        model=None, memory_history=50
    )
    assert isinstance(retrieval, RetrievalMemory) and retrieval._retrieved_count == 4
    # model is injected only where the constructor accepts it (summarizing).
    model = NoLanguageModel()
    summarizing = build_memory_policy({"built_in": "summarizing"})(model=model, memory_history=50)
    assert isinstance(summarizing, SummarizingMemory) and summarizing._model is model


def test_build_memory_policy_rejects_bad_config() -> None:
    with pytest.raises(ValueError, match="Unknown sim.memory.built_in"):
        build_memory_policy({"built_in": "bogus"})
    with pytest.raises(ValueError, match="must subclass MemoryPolicy"):
        build_memory_policy({"class_path": "builtins.dict"})


def test_build_memory_policy_rejects_unknown_params() -> None:
    """A typo'd sim.memory.params key must fail loudly, not run with defaults."""
    with pytest.raises(ValueError, match="windw_count"):
        build_memory_policy({"built_in": "retrieval", "params": {"windw_count": 5}})
    # Framework kwargs (model on a model-less policy) are still filtered silently.
    window = build_memory_policy({"built_in": "window", "params": {"render_count": 3}})(
        model=NoLanguageModel(), memory_history=50
    )
    assert isinstance(window, WindowMemory)


class _CustomMemory(MemoryPolicy):
    def __init__(self, *, memory_history: int = 1000, tag: str = "x") -> None:
        self.tag = tag
        self._items: list[str] = []

    def record(self, text: str) -> None:
        self._items.append(text)

    def render(self, *, query: str | None = None) -> str:
        return f"[{self.tag}] " + " | ".join(self._items)

    def all_memories(self) -> list[str]:
        return list(self._items)

    def set_state(self, state: Any) -> None:
        self._items = list(state.get("memories", []))


def test_custom_memory_policy_via_class_path() -> None:
    factory = build_memory_policy(
        {"class_path": f"{__name__}._CustomMemory", "params": {"tag": "boom"}}
    )
    policy = factory(model=None, memory_history=10)
    assert isinstance(policy, _CustomMemory) and policy.tag == "boom"


def test_native_agent_uses_injected_memory_policy() -> None:
    agent = NativeAgent(
        name="A",
        model=NoLanguageModel(),
        memory_policy=build_memory_policy({"class_path": f"{__name__}._CustomMemory"}),
    )
    agent.observe("hello")
    assert isinstance(agent._memory, _CustomMemory)
    assert "hello" in agent._context()


def test_native_agent_legacy_memory_text_state_restores() -> None:
    agent = NativeAgent(name="A", model=NoLanguageModel(), memory_history=5)
    agent.set_state({"memory_text": [f"old {i}" for i in range(100)]})
    memories = agent.get_all_memories_as_text()
    assert len(memories) == 5 and memories[0] == "old 95"


def test_native_agent_new_state_round_trip() -> None:
    agent = NativeAgent(name="A", model=NoLanguageModel())
    agent.observe("x")
    agent.observe("y")
    state = agent.get_state()
    assert "memory" in state
    restored = NativeAgent(name="A", model=NoLanguageModel())
    restored.set_state(state)
    assert restored.get_all_memories_as_text() == ["x", "y"]
