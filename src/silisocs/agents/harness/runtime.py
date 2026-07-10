"""Wire the Model Proxy into a run: start it, bind harness agents, expose usage.

The session calls :func:`setup_harness_proxy` once after models are built and agents are
constructed. If any agent is a :class:`HarnessAgent`, it starts one loopback proxy,
derives each agent's upstream from its built model (so a harness agent reuses the same
per-class model config native agents do), registers a per-agent routing token, and binds
the agent to the proxy. The returned proxy's :meth:`usage_models` are added to the run's
``models`` collection so harness token usage rolls into the unified ``llm_usage``. The
session stops the proxy in its teardown ``finally`` before writing metrics.

Runs with no harness agents get ``None`` and no proxy thread ever starts.
"""

from __future__ import annotations

from typing import Any

from silisocs.agents.harness.base import HarnessAgent
from silisocs.agents.harness.proxy import HarnessModelProxy

# The placeholder key OpenAICompatibleLanguageModel synthesizes for keyless endpoints.
_PLACEHOLDER_API_KEY = "openai-compatible-api-key"
_DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"


def _upstream_from_model(model: Any) -> dict[str, Any]:
    """Derive proxy upstream kwargs (kind/base_url/api_key/model_label) from a model.

    A model with an HTTP endpoint (OpenAI or OpenAI-compatible) forwards there; a
    scripted/disabled/no-model has no endpoint and gets a deterministic scripted
    upstream (used by no-key integration tests).
    """
    model_label = str(getattr(model, "_model_name", "") or "") or type(model).__name__
    api_base = str(getattr(model, "_api_base", "") or "")
    api_key = str(getattr(model, "_api_key", "") or "")
    real_key = api_key and api_key != _PLACEHOLDER_API_KEY
    if not api_base and not real_key:
        return {"kind": "scripted", "model_label": model_label}
    return {
        "kind": "http",
        "base_url": api_base or _DEFAULT_OPENAI_BASE_URL,
        "api_key": api_key,
        "model_label": model_label,
    }


def setup_harness_proxy(
    agents: list[Any],
    *,
    prompt_logger: Any = None,
) -> HarnessModelProxy | None:
    """Start and wire the Model Proxy for a run's harness agents (``None`` if none)."""
    harness_agents = [agent for agent in agents if isinstance(agent, HarnessAgent)]
    if not harness_agents:
        return None
    proxy = HarnessModelProxy(prompt_logger=prompt_logger)
    proxy.start()
    for agent in harness_agents:
        upstream = _upstream_from_model(agent.model)
        token = proxy.register_agent(agent.name, phase="action", **upstream)
        agent.bind_model_proxy(proxy.base_url, token)
    return proxy


def harness_usage_models(proxy: HarnessModelProxy | None) -> list[Any]:
    """The usage accumulators to fold into the run's ``models`` summary collection."""
    return proxy.usage_models() if proxy is not None else []


class PromptProxyLogger:
    """Writes proxy prompt/response rows to the run's ``prompts_and_responses.jsonl``.

    The same async JSONL writer native models log through, so harness model calls and
    native model calls land in one prompt log.
    """

    def __init__(self, path: str) -> None:
        self._path = str(path)

    def log(self, row: dict[str, Any]) -> None:
        from silisocs.runtime.io import write_jsonl_item

        write_jsonl_item(dict(row), self._path)
