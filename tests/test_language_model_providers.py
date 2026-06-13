from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any, cast

import pytest

from silisocs.runtime.language_models import (
    OpenAICompatibleLanguageModel,
    OpenAILanguageModel,
    ScriptedLanguageModel,
    select_large_language_model,
)
from silisocs.runtime.types import ToolCall


class _Message:
    content = "answer"
    tool_calls = None


class _Choice:
    message = _Message()


class _Response:
    choices = [_Choice()]


class _Completions:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return _Response()


def _attach_fake_client(model: OpenAILanguageModel | OpenAICompatibleLanguageModel) -> _Completions:
    completions = _Completions()
    cast(Any, model)._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    cast(Any, model)._record_retry_outcome = lambda retries, success: None
    return completions


class SocialPostLikeBehavior:
    """Post on the first step, then like visible timeline posts."""

    def sample_text(self, prompt: str, *, model: Any) -> str:
        agent = str(model.meta_data.get("agent_name", "agent") or "agent")
        episode = int(model.meta_data.get("episode_idx", -1) or -1)
        if "seed post" in prompt.lower():
            return f"{agent}: seed post"
        return f"{agent} update at episode {episode}"

    def sample_tool_calls(
        self,
        prompt: str,
        tools: list[dict[str, Any]],
        *,
        model: Any,
    ) -> list[ToolCall]:
        tool_names = {_tool_name(spec) for spec in tools}
        tool_names.discard("")
        episode = int(model.meta_data.get("episode_idx", -1) or -1)
        agent = str(model.meta_data.get("agent_name", "agent") or "agent")
        tweet_ids = re.findall(r"Tweet ID:\s*([0-9]+)", prompt)
        if episode <= 0 and "create_tweet" in tool_names:
            return [ToolCall("create_tweet", {"status": f"{agent} says hello at step {episode}"})]
        if "like_tweet" in tool_names and tweet_ids:
            return [ToolCall("like_tweet", {"post_id": tweet_ids[0]})]
        if "create_tweet" in tool_names:
            return [ToolCall("create_tweet", {"status": f"{agent} update at step {episode}"})]
        return [ToolCall(sorted(tool_names)[0], {})] if tool_names else []


def _tool_name(spec: dict[str, Any]) -> str:
    function = spec.get("function")
    if isinstance(function, dict):
        return str(function.get("name", "") or "").strip()
    return str(spec.get("name", "") or "").strip()


def test_openai_language_model_uses_direct_instruct_messages_without_local_kwargs() -> None:
    model = OpenAILanguageModel.__new__(OpenAILanguageModel)
    model._temperature = 0.1
    model._model_name = "qwen3.5-4b"
    model._max_retries = 0
    model._measurements = None
    model.debug = False
    model._extra_kwargs = {}
    completions = _attach_fake_client(model)

    assert model.sample_text("What should Alice do?") == "answer"

    request = completions.calls[0]
    assert request["messages"] == [
        {"role": "system", "content": "You are a helpful, instruction-following assistant."},
        {"role": "user", "content": "What should Alice do?"},
    ]
    assert "extra_body" not in request


def test_openai_compatible_language_model_requires_api_base_and_merges_extra_kwargs() -> None:
    with pytest.raises(ValueError, match="api_base"):
        OpenAICompatibleLanguageModel(model_name="qwen3.5-4b", api_base="", api_key="test-key")

    model = OpenAICompatibleLanguageModel.__new__(OpenAICompatibleLanguageModel)
    model._temperature = 0.1
    model._model_name = "openai-compatible-model"
    model._max_retries = 0
    model._measurements = None
    model.debug = False
    model._extra_kwargs = {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
    completions = _attach_fake_client(model)

    assert model.sample_text("Hello") == "answer"

    request = completions.calls[0]
    assert request["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_select_language_model_requires_explicit_provider() -> None:
    with pytest.raises(ValueError, match="provider"):
        select_large_language_model(
            "gpt-4o-mini",
            "prompts.jsonl",
            False,
            api_key="test",
            provider=None,
        )


def test_select_language_model_rejects_removed_local_provider_alias() -> None:
    with pytest.raises(ValueError, match="Unknown sim.llm.provider"):
        select_large_language_model(
            "openai-compatible-model",
            "prompts.jsonl",
            False,
            api_base="http://localhost:8000/v1",
            provider="local",
        )


def test_scripted_language_model_returns_typed_method_defaults() -> None:
    model = ScriptedLanguageModel(
        text_response="hello",
        choice_response="B",
        float_response=0.75,
        tool_calls=[
            {"name": "post", "arguments": {"content": "one"}},
            {"name": "like", "arguments": {"post_id": "p1"}},
        ],
        structured_response={"ok": True},
        debug=False,
    )

    assert model.sample_text("prompt") == "hello"
    assert model.sample_choice("prompt", ["A", "B"]) == (1, "B", {})
    assert model.sample_float("prompt") == 0.75
    assert model.sample_tool_calls("prompt", [], mode="single") == [
        ToolCall("post", {"content": "one"})
    ]
    assert model.sample_tool_calls("prompt", [], mode="multi") == [
        ToolCall("post", {"content": "one"}),
        ToolCall("like", {"post_id": "p1"}),
    ]
    assert model.sample_structured("prompt", {"type": "object"}) == {"ok": True}


def test_select_language_model_builds_public_scripted_provider() -> None:
    model = select_large_language_model(
        "scripted",
        "prompts.jsonl",
        False,
        provider="scripted",
        extra_kwargs={"text_response": "dry run"},
    )

    assert isinstance(model, ScriptedLanguageModel)
    assert model.sample_text("prompt") == "dry run"


def test_scripted_language_model_behavior_hook_emits_post_then_like() -> None:
    model = ScriptedLanguageModel(
        behavior_class_path="tests.test_language_model_providers.SocialPostLikeBehavior",
        debug=False,
    )
    model.set_runtime_context(agent_name="Alice", episode_idx=0, phase="action", action_tag="turn")
    tools = [
        {"type": "function", "function": {"name": "create_tweet"}},
        {"type": "function", "function": {"name": "like_tweet"}},
    ]
    first = model.sample_tool_calls("Prompt without timeline", tools, mode="single")
    assert first[0].name == "create_tweet"

    model.set_runtime_context(agent_name="Alice", episode_idx=1, phase="action", action_tag="turn")
    second = model.sample_tool_calls("Timeline\nTweet ID: 42", tools, mode="single")
    assert second[0].name in {"create_tweet", "like_tweet"}


def test_no_sample_tool_call_legacy_method_on_providers() -> None:
    assert not hasattr(OpenAILanguageModel, "sample_tool_call")
    assert hasattr(OpenAILanguageModel, "sample_tool_calls")


def test_sample_tool_calls_returns_typed_tool_calls() -> None:
    class _Function:
        name = "toot"
        arguments = '{"status": "hello"}'

    class _ToolCall:
        function = _Function()

    class _ToolMessage:
        content = None
        tool_calls = [_ToolCall()]

    class _ToolChoice:
        message = _ToolMessage()

    class _ToolResponse:
        choices = [_ToolChoice()]

    class _ToolCompletions(_Completions):
        def create(self, **kwargs: Any) -> _ToolResponse:
            self.calls.append(kwargs)
            return _ToolResponse()

    model = OpenAILanguageModel.__new__(OpenAILanguageModel)
    model._temperature = 0.1
    model._model_name = "gpt-4o-mini"
    model._max_retries = 0
    model._measurements = None
    model.debug = False
    model._extra_kwargs = {}
    completions = _ToolCompletions()
    cast(Any, model)._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    cast(Any, model)._record_retry_outcome = lambda retries, success: None

    result = model.sample_tool_calls("prompt", [{"type": "function"}], mode="multi")

    assert result == [ToolCall("toot", {"status": "hello"})]


class _ToyModel:
    """Minimal custom provider for registry tests."""

    def __init__(self, *, model_name: str = "", temperature: float = 0.0) -> None:
        self.model_name = model_name
        self.temperature = temperature


def test_registered_custom_provider_is_selectable() -> None:
    from silisocs.runtime.language_models.base import LanguageModel
    from silisocs.runtime.language_models.factory import select_large_language_model
    from silisocs.runtime.language_models.registry import (
        _PROVIDERS,
        register_llm_provider,
    )

    @register_llm_provider("toy_test_provider")
    class ToyLanguageModel(LanguageModel):
        def __init__(self, *, model_name: str = "", temperature: float = 0.0) -> None:
            self.model_name = model_name
            self.temperature = temperature

        def sample_text(self, prompt: str, **kwargs):  # type: ignore[override]
            return "toy"

    try:
        model = select_large_language_model(
            model_name="toy-1",
            log_file="",
            debug_mode=False,
            provider="toy_test_provider",
            temperature=0.9,
        )
        assert isinstance(model, ToyLanguageModel)
        assert model.model_name == "toy-1"
        assert model.temperature == 0.9
    finally:
        _PROVIDERS.pop("toy_test_provider", None)


def test_class_path_provider_must_be_language_model() -> None:
    import pytest

    from silisocs.runtime.language_models.factory import select_large_language_model

    with pytest.raises(TypeError, match="not a silisocs LanguageModel"):
        select_large_language_model(
            model_name="x",
            log_file="",
            debug_mode=False,
            provider=f"{_ToyModel.__module__}._ToyModel",
        )


def test_unknown_provider_error_mentions_extension_paths() -> None:
    import pytest

    from silisocs.runtime.language_models.factory import select_large_language_model

    with pytest.raises(ValueError, match="register_llm_provider"):
        select_large_language_model(model_name="x", log_file="", debug_mode=False, provider="nope")
