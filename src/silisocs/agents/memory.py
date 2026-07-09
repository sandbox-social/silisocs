"""Agent memory policies (``sim.memory``).

A ``MemoryPolicy`` owns an agent's accumulated memory: how observations are
RECORDED and how the "Memory" section of the agent's prompt is RENDERED. It is
held inside the agent, so its state rides in the agent's ``get_state`` /
``set_state`` and is checkpointed for free.

Built-ins:

* ``window`` (default) — the long-standing behavior: keep the last
  ``memory_history`` memories, render the last ``render_count`` (10). Byte
  identical to the pre-slot NativeAgent.
* ``retrieval`` — a recency window PLUS relevance recall: always render the last
  ``window_count`` memories verbatim, and prepend the ``retrieved_count``
  memories from the OLDER remainder most relevant to the current query (last
  observation) by deterministic lexical overlap, recency as tiebreak.
  Replay-stable, no embedding API. Defaults lean generous (``window_count`` 40,
  ``retrieved_count`` 10); ``window_count: 0`` recovers pure retrieval.
* ``summarizing`` — three tiers rendered in order: all rolling summaries, then
  the ``retrieved_count`` most relevant older verbatim memories, then the recent
  ``render_count`` window. When memory exceeds ``max_memories`` the oldest
  ``chunk_size`` are compressed into one summary via a model call. Only as
  reproducible as the model it calls (like the ``agent_choice`` router).

``sim.memory`` and ``sim.initialization.agents`` are orthogonal: the initializer
SEEDS memories at step 0; this slot governs runtime memory behavior. Applies to
NativeAgent (and subclasses that opt in); Concordia-compat agents manage memory
through their own components.
"""

from __future__ import annotations

import inspect
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from typing import Any

from silisocs.runtime.class_loading import load_class

logger = logging.getLogger(__name__)


def _build_filtered(cls: type[Any], kwargs: Mapping[str, Any]) -> Any:
    """Instantiate ``cls``, passing only the kwargs its constructor accepts.

    Framework kwargs (``model``, ``memory_history``) are offered to every policy;
    those a policy doesn't declare are dropped rather than raising (e.g. the
    window policy ignores ``model``).
    """
    try:
        params = inspect.signature(cls).parameters
    except (TypeError, ValueError):
        return cls(**dict(kwargs))
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values()):
        return cls(**dict(kwargs))
    return cls(**{key: value for key, value in kwargs.items() if key in params})


def _reject_unknown_params(cls: type[Any], params: Mapping[str, Any]) -> None:
    """Fail loudly when user-supplied ``sim.memory.params`` name unknown kwargs.

    The silent filtering in :func:`_build_filtered` exists for the FRAMEWORK
    kwargs (``model``, ``memory_history``), which are offered to every policy;
    a user param a policy doesn't declare is a config typo, not an option.
    """
    try:
        signature = inspect.signature(cls).parameters
    except (TypeError, ValueError):
        return
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.values()):
        return
    unknown = sorted(set(params) - set(signature))
    if unknown:
        raise ValueError(
            f"sim.memory.params {unknown} are not accepted by {cls.__name__}; "
            f"accepted params: {sorted(signature)}."
        )


def _normalize(items: Any) -> list[str]:
    if isinstance(items, str):
        text = items.strip()
        return [text] if text else []
    if not items:
        return []
    return [str(item).strip() for item in items if str(item).strip()]


def _rank_by_overlap(
    indexed_pool: list[tuple[int, str]], query: str, count: int
) -> list[tuple[int, str]]:
    """Return the ``count`` pool items most relevant to ``query``, chronologically.

    Relevance is deterministic lexical overlap — the size of the shared token set,
    with the original index as a recency tiebreak — so retrieval is replay- and
    resume-stable without an embedding service. The winners are re-sorted by index
    so the rendered block still reads oldest→newest. Shared by ``retrieval`` and
    ``summarizing``.
    """
    if count <= 0 or not indexed_pool:
        return []
    query_tokens = set(query.lower().split())
    ranked = sorted(
        indexed_pool,
        key=lambda pair: (len(query_tokens & set(pair[1].lower().split())), pair[0]),
        reverse=True,
    )
    return sorted(ranked[:count], key=lambda pair: pair[0])


class MemoryPolicy(ABC):
    """Records observations and renders the agent's memory prompt section."""

    @abstractmethod
    def record(self, text: str) -> None:
        """Store one memory item (an observation or seeded memory)."""

    @abstractmethod
    def render(self, *, query: str | None = None) -> str:
        """Return the Memory prompt section, optionally conditioned on ``query``."""

    @abstractmethod
    def all_memories(self) -> list[str]:
        """Return every stored memory (for logs/tests)."""

    def get_state(self) -> dict[str, Any]:
        return {"memories": self.all_memories()}

    def set_state(self, state: Mapping[str, Any]) -> None:
        raise NotImplementedError


class WindowMemory(MemoryPolicy):
    """Keep the last ``memory_history`` memories; render the last ``render_count``."""

    def __init__(self, *, memory_history: int = 1000, render_count: int = 10) -> None:
        self._limit = max(1, int(memory_history))
        self._render_count = max(0, int(render_count))
        self._memories: list[str] = []

    def record(self, text: str) -> None:
        self._memories.append(text)
        self._memories = self._memories[-self._limit :]

    def render(self, *, query: str | None = None) -> str:
        del query
        return "\n".join(self._memories[-self._render_count :])

    def all_memories(self) -> list[str]:
        return list(self._memories)

    def set_state(self, state: Mapping[str, Any]) -> None:
        self._memories = _normalize(state.get("memories", ()))[-self._limit :]


class RetrievalMemory(MemoryPolicy):
    """A recency window plus relevance recall (deterministic lexical overlap).

    Always renders the last ``window_count`` memories verbatim (the recency
    floor), and prepends the ``retrieved_count`` memories from the OLDER remainder
    most relevant to the query. Scoring is deterministic (token overlap, recency
    tiebreak), so retrieval is replay- and resume-stable without an embedding
    service. ``window_count: 0`` recovers pure retrieval; ``retrieved_count: 0``
    is a plain recency window.
    """

    def __init__(
        self,
        *,
        memory_history: int = 1000,
        window_count: int = 40,
        retrieved_count: int = 10,
    ) -> None:
        self._limit = max(1, int(memory_history))
        self._window_count = max(0, int(window_count))
        self._retrieved_count = max(0, int(retrieved_count))
        self._memories: list[str] = []

    def record(self, text: str) -> None:
        self._memories.append(text)
        self._memories = self._memories[-self._limit :]

    def render(self, *, query: str | None = None) -> str:
        window = self._memories[-self._window_count :] if self._window_count else []
        # The retrieval pool is everything OLDER than the window, so the two
        # sections never duplicate a memory (structural dedup, no content compare).
        pool = self._memories[: -self._window_count] if self._window_count else list(self._memories)
        if not query or not pool or self._retrieved_count <= 0:
            # No query / nothing older than the window / retrieval off: render the
            # most recent (window + retrieved) memories verbatim, keeping the
            # rendered volume consistent with the two-section path.
            fallback = self._window_count + self._retrieved_count
            return "\n".join(self._memories[-fallback:]) if fallback else ""
        retrieved = _rank_by_overlap(list(enumerate(pool)), query, self._retrieved_count)
        retrieved_text = "\n".join(text for _, text in retrieved)
        if not window:
            return retrieved_text  # window_count == 0: legacy pure retrieval, no labels
        # Retrieved first, window LAST — the freshest context sits nearest the
        # action prompt.
        return (
            "Relevant past memories:\n"
            + retrieved_text
            + "\n\nRecent memories:\n"
            + "\n".join(window)
        )

    def all_memories(self) -> list[str]:
        return list(self._memories)

    def set_state(self, state: Mapping[str, Any]) -> None:
        self._memories = _normalize(state.get("memories", ()))[-self._limit :]


_DEFAULT_SUMMARY_PROMPT = (
    "Concisely summarize the following events into a few sentences, preserving "
    "names, decisions, and outcomes:\n\n{memories}\n\nSummary:"
)


class SummarizingMemory(MemoryPolicy):
    """Compress the oldest memories into summaries once memory grows too large.

    Summarization runs on the RECORD side (when memory exceeds ``max_memories``),
    so ``act`` latency stays flat; the model call is bounded (~once per
    ``chunk_size`` records). Summaries persist in state, so a resumed run never
    re-summarizes.
    """

    def __init__(
        self,
        *,
        model: Any = None,
        max_memories: int = 200,
        chunk_size: int = 50,
        max_summaries: int = 20,
        render_count: int = 40,
        retrieved_count: int = 10,
        prompt: str | None = None,
        summary_max_tokens: int = 256,
    ) -> None:
        self._model = model
        self._max_memories = max(1, int(max_memories))
        self._chunk_size = max(1, min(int(chunk_size), self._max_memories))
        self._max_summaries = max(1, int(max_summaries))
        self._render_count = max(0, int(render_count))
        self._retrieved_count = max(0, int(retrieved_count))
        self._prompt = str(prompt or _DEFAULT_SUMMARY_PROMPT)
        self._summary_max_tokens = max(1, int(summary_max_tokens))
        self._memories: list[str] = []
        self._summaries: list[str] = []

    def record(self, text: str) -> None:
        self._memories.append(text)
        while len(self._memories) > self._max_memories:
            chunk, self._memories = (
                self._memories[: self._chunk_size],
                self._memories[self._chunk_size :],
            )
            self._summaries.append(self._summarize(chunk))
            self._summaries = self._summaries[-self._max_summaries :]

    def _summarize(self, chunk: list[str]) -> str:
        joined = "\n".join(chunk)
        if self._model is None:
            return joined[:500]  # deterministic fallback for no-model/test runs
        # NOTE: summarization runs at record/observe time, so it is attributed to
        # whatever phase the caller is in: 'action' for the engine-bracketed turn
        # paths (GM observation, resolve feedback), 'other' for observe calls
        # outside a turn (agent initialization, broadcast_observation
        # interventions). We deliberately do NOT call set_runtime_context here —
        # overwriting the shared per-turn context would misattribute the turn's
        # later action call.
        try:
            return (
                str(
                    self._model.sample_text(
                        self._prompt.format(memories=joined), max_tokens=self._summary_max_tokens
                    )
                ).strip()
                or joined[:500]
            )
        except Exception:  # pragma: no cover - summarization must not break a turn
            logger.debug("memory summarization failed; keeping raw chunk", exc_info=True)
            return joined[:500]

    def render(self, *, query: str | None = None) -> str:
        parts: list[str] = []
        if self._summaries:
            parts.append("Summary of earlier events:\n" + "\n".join(self._summaries))
        # Relevance tier: verbatim memories from OUTSIDE the recent window, ranked
        # by overlap with the query. Summaries stay solely in section 1 (they are
        # always rendered), so the pool is verbatim-minus-window and the three
        # sections are disjoint by construction — no content dedup needed.
        if query and self._retrieved_count > 0:
            pool = self._memories[: -self._render_count] if self._render_count else []
            retrieved = _rank_by_overlap(list(enumerate(pool)), query, self._retrieved_count)
            if retrieved:
                parts.append("Relevant past memories:\n" + "\n".join(t for _, t in retrieved))
        # Guard render_count == 0 (summaries/retrieval only): x[-0:] == x[:] would
        # otherwise dump the WHOLE buffer instead of an empty recent window.
        recent = "\n".join(self._memories[-self._render_count :]) if self._render_count else ""
        if recent:
            parts.append(recent)
        return "\n\n".join(parts)

    def all_memories(self) -> list[str]:
        return list(self._memories)

    def get_state(self) -> dict[str, Any]:
        return {"memories": list(self._memories), "summaries": list(self._summaries)}

    def set_state(self, state: Mapping[str, Any]) -> None:
        self._memories = _normalize(state.get("memories", ()))[-self._max_memories :]
        self._summaries = _normalize(state.get("summaries", ()))[-self._max_summaries :]


_MEMORY_BUILT_INS: dict[str, type[MemoryPolicy]] = {
    "window": WindowMemory,
    "retrieval": RetrievalMemory,
    "summarizing": SummarizingMemory,
}

# A per-agent factory: the agent supplies its ``model`` and ``memory_history``,
# and only the constructor params a policy actually declares are injected.
MemoryPolicyFactory = Callable[..., MemoryPolicy]


def build_memory_policy(slot_cfg: Mapping[str, Any] | None = None) -> MemoryPolicyFactory:
    """Build a per-agent :class:`MemoryPolicy` factory from ``sim.memory`` config.

    Returns a factory called once per agent as ``factory(model=..., memory_history=...)``
    so each agent gets its own policy instance. ``memory_history`` (a per-agent
    param) is the default store cap; ``model`` is used only by ``summarizing``.
    """
    cfg = dict(slot_cfg or {})
    class_path = str(cfg.get("class_path") or "").strip()
    built_in = str(cfg.get("built_in") or "window").strip()
    params = {
        key: value for key, value in dict(cfg.get("params") or {}).items() if value is not None
    }
    if class_path:
        cls = load_class(class_path)
        if not (isinstance(cls, type) and issubclass(cls, MemoryPolicy)):
            raise ValueError(f"sim.memory.class_path {class_path!r} must subclass MemoryPolicy.")
    elif built_in in _MEMORY_BUILT_INS:
        cls = _MEMORY_BUILT_INS[built_in]
    else:
        raise ValueError(
            f"Unknown sim.memory.built_in {built_in!r}. Available: "
            f"{sorted(_MEMORY_BUILT_INS)}, or set sim.memory.class_path."
        )
    _reject_unknown_params(cls, params)

    def factory(*, model: Any = None, memory_history: int = 1000) -> MemoryPolicy:
        return _build_filtered(cls, {"memory_history": memory_history, "model": model, **params})

    return factory
