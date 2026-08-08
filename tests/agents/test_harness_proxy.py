# mypy: disable-error-code="arg-type, union-attr"
# ^ Relaxed for this experimental suite: minimal fake models (not LanguageModel
#   subclasses) and attribute access on the proxy's Optional upstream in assertions.
"""Direct tests for the harness Model Proxy (usage unification + HTTP forwarding)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from silisocs.agents.harness.fake import FakeHarnessAgent
from silisocs.agents.harness.proxy import HarnessModelProxy, UsageAccumulator
from silisocs.agents.harness.runtime import _upstream_from_model, setup_harness_proxy
from silisocs.runtime.telemetry.engine_metrics import collect_usage_summary


class _ScriptedModel:
    """A model with no HTTP endpoint (like ScriptedLanguageModel)."""


class _HttpModel:
    _model_name = "gpt-4o-mini"
    _api_base = "https://api.example.com/v1"
    _api_key = "sk-real-key"


class _CaptureLogger:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def log(self, row: dict[str, Any]) -> None:
        self.rows.append(dict(row))


def _post(base_url: str, token: str, content: str = "hello") -> httpx.Response:
    return httpx.post(
        base_url + "/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": content}]},
        timeout=10.0,
    )


def test_scripted_upstream_returns_response_and_records_usage() -> None:
    logger = _CaptureLogger()
    with HarnessModelProxy(prompt_logger=logger) as proxy:
        token = proxy.register_agent(
            "Alice",
            kind="scripted",
            model_label="m1",
            scripted_content="scripted hi",
            phase="action",
        )
        resp = _post(proxy.base_url, token, content="what's up")
        assert resp.status_code == 200
        data = resp.json()
        assert data["choices"][0]["message"]["content"] == "scripted hi"
        assert data["usage"]["total_tokens"] == 12

    acc = proxy.usage_models()[0]
    counters = acc.get_usage_counters("action")
    assert counters["prompt_tokens"] == 8
    assert counters["completion_tokens"] == 4
    assert counters["calls_with_usage"] == 1
    # Prompt logged with attribution.
    assert logger.rows and logger.rows[0]["agent_name"] == "Alice"
    assert logger.rows[0]["source"] == "harness_proxy"
    assert logger.rows[0]["prompt"] == "what's up"


def test_unknown_token_is_rejected() -> None:
    with HarnessModelProxy() as proxy:
        proxy.register_agent("Alice")
        resp = _post(proxy.base_url, "bogus-token")
        assert resp.status_code == 401


def test_forwarding_records_upstream_usage() -> None:
    with HarnessModelProxy() as upstream, HarnessModelProxy() as proxy:
        up_token = upstream.register_agent(
            "provider", kind="scripted", scripted_content="ok", model_label="up"
        )
        token = proxy.register_agent(
            "Alice", kind="http", base_url=upstream.base_url, api_key=up_token, model_label="m1"
        )
        resp = _post(proxy.base_url, token)
        assert resp.status_code == 200
        assert resp.json()["choices"][0]["message"]["content"] == "ok"
    # The forwarding proxy recorded the upstream's reported usage.
    acc = proxy.usage_models()[0]
    assert acc.get_usage_counters("all")["total_tokens"] == 12


def test_shared_accumulator_dedups_by_model_label() -> None:
    proxy = HarnessModelProxy()
    t1 = proxy.register_agent("Alice", model_label="shared")
    t2 = proxy.register_agent("Bob", model_label="shared")
    assert proxy.upstream_for_token(t1).accumulator is proxy.upstream_for_token(t2).accumulator
    assert len(proxy.usage_models()) == 1


def test_usage_summary_includes_harness_accumulators() -> None:
    acc = UsageAccumulator("harness-model")
    acc.record_usage(100, 50, phase="action")
    acc.record_usage(10, 5, phase="probe")
    summary = collect_usage_summary([acc])
    assert summary["totals"]["prompt_tokens"] == 110
    assert summary["totals"]["completion_tokens"] == 55
    assert summary["by_phase"]["action"]["prompt_tokens"] == 100
    assert summary["by_phase"]["probe"]["prompt_tokens"] == 10
    assert summary["per_model"][0]["model"] == "harness-model"


def test_pricing_applies_to_harness_usage() -> None:
    acc = UsageAccumulator("harness-model")
    acc.record_usage(1_000_000, 1_000_000, phase="action")
    summary = collect_usage_summary([acc], pricing={"input_per_1m": 2.0, "output_per_1m": 6.0})
    assert summary["pricing_applied"] is True
    assert summary["estimated_cost_usd"] == pytest.approx(8.0)


def test_upstream_from_scripted_model_is_scripted() -> None:
    upstream = _upstream_from_model(_ScriptedModel())
    assert upstream["kind"] == "scripted"


def test_upstream_from_http_model_forwards() -> None:
    upstream = _upstream_from_model(_HttpModel())
    assert upstream["kind"] == "http"
    assert upstream["base_url"] == "https://api.example.com/v1"
    assert upstream["api_key"] == "sk-real-key"
    assert upstream["model_label"] == "gpt-4o-mini"


def test_setup_harness_proxy_binds_agents_and_exposes_usage() -> None:
    agents = [
        FakeHarnessAgent(_ScriptedModel(), name="Alice"),
        FakeHarnessAgent(_ScriptedModel(), name="Bob"),
        object(),  # a non-harness object must be ignored
    ]
    proxy = setup_harness_proxy(agents)
    assert proxy is not None
    try:
        assert proxy.base_url.startswith("http://127.0.0.1")
        # Both agents registered; scripted models share one accumulator label.
        assert proxy.usage_models()
    finally:
        proxy.stop()


def test_setup_harness_proxy_none_without_harness_agents() -> None:
    assert setup_harness_proxy([object(), object()]) is None
