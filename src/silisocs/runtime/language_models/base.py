"""Language-model contracts used by agents and runtime components."""

from collections.abc import Collection, Sequence
from typing import Any

from silisocs.runtime.types import ToolCall

DEFAULT_MAX_TOKENS = 1200
DEFAULT_TERMINATORS: Collection[str] | None = ()
DEFAULT_TIMEOUT_SECONDS = 60.0


class InvalidResponseError(RuntimeError):
    """Raised when a model cannot produce a valid constrained response."""


class LanguageModel:
    """Minimal model interface used by Silisocs agents and components."""

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
        raise NotImplementedError

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


def extract_choice_response(sample: str) -> str:
    return str(sample or "").strip().splitlines()[0].strip()
