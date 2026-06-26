"""Tests for the shared ``_retry_request`` core in the OpenAI language model.

The three sampling methods (``sample_text``, ``sample_tool_calls``,
``sample_structured``) previously duplicated their retry/backoff loops. They now
share ``_retry_request``; these tests pin the behaviour that must be preserved:
bounded retries that raise on exhaustion, recovery after transient failures, and
``sample_structured``'s BadRequestError → json_object fallback.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx
import openai
import pytest

from silisocs.runtime.language_models import OpenAICompatibleLanguageModel


def _model(max_retries: int = 2) -> OpenAICompatibleLanguageModel:
    model = OpenAICompatibleLanguageModel(
        model_name="x",
        api_key="k",
        api_base="http://localhost:8000/v1",
        debug=False,
    )
    model._max_retries = max_retries
    model._backoff_base_seconds = 0.0
    model._backoff_max_seconds = 0.0
    return model


def _api_error() -> openai.APIError:
    return openai.APIError("boom", httpx.Request("POST", "http://x"), body=None)


def _text_response(content: str) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    return resp


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("silisocs.runtime.language_models.openai.time.sleep", lambda *_: None)


def test_sample_text_raises_after_bounded_retries(monkeypatch) -> None:
    model = _model(max_retries=2)
    create = MagicMock(side_effect=_api_error())
    model._client = MagicMock()
    model._client.chat.completions.create = create

    with pytest.raises(RuntimeError):
        model.sample_text("hello")

    assert create.call_count == model._max_retries + 1  # one attempt per retry budget
    assert model.get_retry_counters(phase="all")["failed_calls_total"] >= 1


def test_sample_text_recovers_after_transient_errors(monkeypatch) -> None:
    model = _model(max_retries=5)
    create = MagicMock(side_effect=[_api_error(), _api_error(), _text_response("the answer")])
    model._client = MagicMock()
    model._client.chat.completions.create = create

    assert model.sample_text("hi") == "the answer"
    assert create.call_count == 3
    assert model.get_retry_counters(phase="all")["calls_total"] >= 1


def test_sample_structured_falls_back_to_json_object_on_bad_request(monkeypatch) -> None:
    model = _model(max_retries=3)
    bad = openai.BadRequestError(
        "schema unsupported",
        response=httpx.Response(400, request=httpx.Request("POST", "http://x")),
        body=None,
    )
    # First call (json_schema mode) raises BadRequestError; the fallback json_object
    # call then returns a valid object.
    create = MagicMock(side_effect=[bad, _text_response(json.dumps({"k": 1}))])
    model._client = MagicMock()
    model._client.chat.completions.create = create

    out = model.sample_structured("prompt", {"title": "t", "type": "object"})
    assert out == {"k": 1}
    assert create.call_count == 2  # schema attempt + fallback attempt, no extra retries
