"""Language-model contracts used by agents and runtime components."""

import asyncio
import contextvars
from abc import ABC, abstractmethod
from collections.abc import Collection, Sequence
from typing import Any

from silisocs.runtime.types import ToolCall

DEFAULT_MAX_TOKENS = 1200
DEFAULT_TERMINATORS: Collection[str] | None = ()
DEFAULT_TIMEOUT_SECONDS = 60.0


class InvalidResponseError(RuntimeError):
    """Raised when a model cannot produce a valid constrained response."""


class LanguageModel(ABC):
    """Abstract base for language models used by Silisocs agents and components.

    Subclasses must implement :meth:`sample_text`. All other sampling methods
    have default implementations that delegate to ``sample_text`` or raise
    ``NotImplementedError`` for capabilities the provider may not support.
    """

    @abstractmethod
    def sample_text(
        self,
        prompt: str,
        *,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        terminators: Collection[str] | None = DEFAULT_TERMINATORS,
        temperature: float | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        media: Sequence[str] | None = None,
        seed: int | None = 0,
    ) -> str:
        """Generate a text completion for the given prompt."""

    def sample_choice(
        self,
        prompt: str,
        responses: Sequence[str],
        *,
        seed: int | None = None,
        **kwargs: Any,
    ) -> tuple[int, str, dict[str, float]]:
        del seed, kwargs
        if not responses:
            raise InvalidResponseError("No responses provided.")
        sample = self.sample_text(prompt)
        answer = extract_choice_response(sample)
        try:
            idx = list(responses).index(answer)
        except ValueError as exc:
            raise InvalidResponseError(
                f"Model response was not one of the allowed choices: {answer!r}"
            ) from exc
        return idx, responses[idx], {}

    def sample_float(self, prompt: str, **kwargs: Any) -> float:
        raw = self.sample_text(prompt, **kwargs)
        try:
            return float(str(raw).strip())
        except ValueError as exc:
            raise InvalidResponseError(f"Model response is not a float: {raw!r}") from exc

    def sample_tool_calls(
        self,
        prompt: str,
        tools: list[dict[str, Any]],
        *,
        mode: str = "single",
        **kwargs: Any,
    ) -> list[ToolCall]:
        del prompt, tools, mode, kwargs
        raise NotImplementedError("This language model does not support tool calls.")

    def sample_structured(
        self,
        prompt: str,
        schema: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        del prompt, schema, kwargs
        raise NotImplementedError("This language model does not support structured output.")

    # --- Async counterparts (additive) -------------------------------------
    # Every sampling method has an ``*_async`` twin whose default runs the sync
    # method on a helper thread, so every provider — including custom ones that
    # predate the async path — works under the asyncio turn executor unchanged.
    # Providers with a native async client override these to keep thousands of
    # requests in flight on the event loop (see OpenAILanguageModel).

    async def sample_text_async(
        self,
        prompt: str,
        *,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        terminators: Collection[str] | None = DEFAULT_TERMINATORS,
        temperature: float | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        media: Sequence[str] | None = None,
        seed: int | None = 0,
    ) -> str:
        """Async twin of :meth:`sample_text`; default runs it on a helper thread.

        Mirrors :meth:`sample_text`'s explicit, ``**kwargs``-free contract (the
        other sampling methods carry ``**kwargs`` in the ABC and so do their
        twins). A custom model that widens ``sample_text`` with extra keyword
        arguments should override this twin (or ``act_async``) to forward them.
        """
        return await asyncio.to_thread(
            self.sample_text,
            prompt,
            max_tokens=max_tokens,
            terminators=terminators,
            temperature=temperature,
            timeout=timeout,
            media=media,
            seed=seed,
        )

    async def sample_choice_async(
        self,
        prompt: str,
        responses: Sequence[str],
        **kwargs: Any,
    ) -> tuple[int, str, dict[str, float]]:
        """Async twin of :meth:`sample_choice`; default runs it on a helper thread."""
        return await asyncio.to_thread(self.sample_choice, prompt, responses, **kwargs)

    async def sample_float_async(self, prompt: str, **kwargs: Any) -> float:
        """Async twin of :meth:`sample_float`; default runs it on a helper thread."""
        return await asyncio.to_thread(self.sample_float, prompt, **kwargs)

    async def sample_tool_calls_async(
        self,
        prompt: str,
        tools: list[dict[str, Any]],
        **kwargs: Any,
    ) -> list[ToolCall]:
        """Async twin of :meth:`sample_tool_calls`; default runs it on a helper thread."""
        return await asyncio.to_thread(self.sample_tool_calls, prompt, tools, **kwargs)

    async def sample_structured_async(
        self,
        prompt: str,
        schema: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Async twin of :meth:`sample_structured`; default runs it on a helper thread."""
        return await asyncio.to_thread(self.sample_structured, prompt, schema, **kwargs)

    def set_runtime_context(
        self,
        *,
        agent_name: str | None = None,
        episode_idx: int | None = None,
        phase: str | None = None,
        action_tag: str | None = None,
    ) -> None:
        del agent_name, episode_idx, phase, action_tag

    def clear_runtime_context(self) -> None:
        return


class NoLanguageModel(LanguageModel):
    """Deterministic no-op model for tests and dry runs."""

    def sample_text(self, prompt: str, **kwargs: Any) -> str:
        del prompt, kwargs
        return ""

    def sample_choice(
        self,
        prompt: str,
        responses: Sequence[str],
        *,
        seed: int | None = None,
        **kwargs: Any,
    ) -> tuple[int, str, dict[str, float]]:
        del prompt, seed, kwargs
        if not responses:
            raise InvalidResponseError("No responses provided.")
        return 0, responses[0], {}

    def sample_tool_calls(
        self,
        prompt: str,
        tools: list[dict[str, Any]],
        *,
        mode: str = "single",
        **kwargs: Any,
    ) -> list[ToolCall]:
        del prompt, tools, mode, kwargs
        return []

    def sample_structured(
        self,
        prompt: str,
        schema: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        del prompt, schema, kwargs
        return {}

    def sample_float(self, prompt: str, **kwargs: Any) -> float:
        del prompt, kwargs
        return 0.0


class ContextLocal:
    """``threading.local`` drop-in backed by a :class:`contextvars.ContextVar`.

    Providers stash per-call runtime context (agent name, episode, phase) on a
    "local" object so concurrent turns don't clobber each other. A plain
    ``threading.local`` isolates per OS thread, which breaks under the asyncio
    turn executor where many turns interleave on one loop thread. ContextVars
    isolate per thread AND per asyncio task (``to_thread`` propagates them into
    its worker), so the same attribute-style call sites work in both modes.
    """

    def __init__(self) -> None:
        object.__setattr__(self, "_var", contextvars.ContextVar[dict[str, Any]]("context_local"))

    def _mapping(self) -> dict[str, Any]:
        return self._var.get({})

    def __getattr__(self, name: str) -> Any:
        mapping = object.__getattribute__(self, "_var").get({})
        if name in mapping:
            return mapping[name]
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        self._var.set({**self._mapping(), name: value})

    def __delattr__(self, name: str) -> None:
        mapping = dict(self._mapping())
        if name in mapping:
            del mapping[name]
            self._var.set(mapping)


def extract_choice_response(sample: str) -> str:
    return str(sample or "").strip().splitlines()[0].strip()
