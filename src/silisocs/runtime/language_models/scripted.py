"""Deterministic scripted model provider for tests and dry runs."""

import importlib
import json
import threading
from collections.abc import Mapping, Sequence
from typing import Any

from silisocs.runtime.io import write_jsonl_item
from silisocs.runtime.language_models.base import InvalidResponseError, LanguageModel
from silisocs.runtime.types import ToolCall


class ScriptedLanguageModel(LanguageModel):
    """Deterministic public dry-run model with method-level default responses."""

    def __init__(
        self,
        *,
        text_response: str = (
            "ACTION TYPE: POST\n"
            "TARGET ID: \n"
            "CONTENT: Scripted dry-run post\n"
            "REASONING: Scripted model response."
        ),
        choice_response: str | None = None,
        float_response: float = 0.0,
        tool_calls: Sequence[Mapping[str, Any]] | None = None,
        structured_response: Mapping[str, Any] | None = None,
        behavior_class_path: str | None = None,
        behavior_params: Mapping[str, Any] | None = None,
        log_file: str | None = None,
        debug: bool | None = True,
    ) -> None:
        self.text_response = str(text_response or "")
        self.choice_response = choice_response
        self.float_response = float(float_response)
        self.default_tool_calls = [
            ToolCall(str(item.get("name", "create_tweet")), dict(item.get("arguments", {}) or {}))
            for item in (
                tool_calls
                or ({"name": "create_tweet", "arguments": {"status": "Scripted dry-run post"}},)
            )
        ]
        self.structured_response = dict(structured_response or {"answer": self.text_response})
        self.behavior = _load_behavior(behavior_class_path, behavior_params or {})
        self._log_file = log_file
        self.debug = debug
        self.meta_data = {"episode_idx": -1, "agent_name": "scripted", "phase": "", "tag": ""}
        self.agent_names: list[str] = []
        self._local = threading.local()

    def _log(self, prompt: str, output: str) -> None:
        if not self.debug or not self._log_file:
            return
        entry = {"prompt": prompt, "output": output} | self.meta_data
        write_jsonl_item(entry, self._log_file)

    def set_runtime_context(
        self,
        *,
        agent_name: str | None = None,
        episode_idx: int | None = None,
        phase: str | None = None,
        action_tag: str | None = None,
    ) -> None:
        if agent_name is not None:
            self.meta_data["agent_name"] = str(agent_name)
            self._local.agent_name = str(agent_name)
        if episode_idx is not None:
            self.meta_data["episode_idx"] = int(episode_idx)
            self._local.episode_idx = int(episode_idx)
        if phase is not None:
            self.meta_data["phase"] = str(phase)
            self._local.phase = str(phase)
        if action_tag is not None:
            self.meta_data["tag"] = str(action_tag)
            self._local.tag = str(action_tag)

    def clear_runtime_context(self) -> None:
        self._local = threading.local()

    def sample_text(self, prompt: str, **kwargs: Any) -> str:
        del kwargs
        scripted = self._behavior_call("sample_text", prompt)
        output = scripted if scripted is not None else self.text_response
        self._log(prompt, output)
        return str(output)

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
        response = str(self.choice_response if self.choice_response in responses else responses[0])
        idx = list(responses).index(response)
        self._log(prompt, response)
        return idx, response, {}

    def sample_float(self, prompt: str, **kwargs: Any) -> float:
        del kwargs
        self._log(prompt, str(self.float_response))
        return self.float_response

    def sample_tool_calls(
        self,
        prompt: str,
        tools: list[dict[str, Any]],
        *,
        mode: str = "single",
        **kwargs: Any,
    ) -> list[ToolCall]:
        del kwargs
        calls = self._behavior_call("sample_tool_calls", prompt, tools)
        if calls is None:
            calls = list(self.default_tool_calls)
        calls = [
            call
            if isinstance(call, ToolCall)
            else ToolCall(str(call.get("name", "")), dict(call.get("arguments", {}) or {}))
            for call in calls
            if isinstance(call, (ToolCall, Mapping))
        ]
        if str(mode or "single").strip().lower() == "single":
            calls = calls[:1]
        self._log(prompt, "tool_calls:" + ", ".join(call.name for call in calls))
        return calls

    def sample_structured(
        self,
        prompt: str,
        schema: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        del kwargs
        value = self._behavior_call("sample_structured", prompt, schema)
        response = dict(value) if isinstance(value, Mapping) else dict(self.structured_response)
        self._log(prompt, json.dumps(response, ensure_ascii=True))
        return response

    def _behavior_call(self, method_name: str, *args: Any) -> Any:
        if self.behavior is None:
            return None
        method = getattr(self.behavior, method_name, None)
        if not callable(method):
            return None
        return method(*args, model=self)


def _load_behavior(class_path: str | None, params: Mapping[str, Any]) -> Any | None:
    path = str(class_path or "").strip()
    if not path:
        return None
    module_name, class_name = path.rsplit(".", 1)
    cls = getattr(importlib.import_module(module_name), class_name)
    return cls(**dict(params or {}))
