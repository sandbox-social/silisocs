"""End-to-end LLM tests for multi-GM architecture.

These tests require a running LLM server with OpenAI-compatible API.

Setup:
    1. Start LLM server: python -m vllm.entrypoints.openai.api_server \
                          --model Qwen/Qwen3.5-4B \
                          --port 30000
    2. Set environment: export LLM_SERVER_URL=http://localhost:30000/v1
    3. Run tests: pytest tests/test_e2e_multi_gm_llm.py -v -s
"""

import os
import json
import urllib.request
import urllib.error
import pytest
import logging

_LOGGER = logging.getLogger(__name__)


class LLMTestHelper:
    """Helper for calling LLM server via OpenAI-compatible API."""

    def __init__(self, base_url: str = None):
        self.base_url = base_url or os.getenv("LLM_SERVER_URL", "http://localhost:30000/v1")

    def call_llm(self, prompt: str, model: str = "qwen3.5-4b", max_tokens: int = 150) -> str:
        """Call LLM server directly via HTTP (OpenAI-compatible API)."""
        url = f"{self.base_url}/chat/completions"

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.7,
        }

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode('utf-8'),
                headers={"Content-Type": "application/json"}
            )

            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.loads(response.read().decode('utf-8'))
                return result['choices'][0]['message']['content']
        except urllib.error.URLError as e:
            pytest.skip(f"LLM server not available: {e}")
        except Exception as e:
            pytest.fail(f"LLM call failed: {e}")


@pytest.mark.skipif(
    not os.getenv("LLM_SERVER_URL"),
    reason="LLM_SERVER_URL not set"
)
class TestEndToEndWithLLM:
    """End-to-end tests using LLM reasoning."""

    def setup_method(self):
        """Setup LLM helper."""
        self.llm = LLMTestHelper()

    def test_llm_can_reason_about_agent_behavior(self):
        """Test LLM can reason about multi-GM agent behavior."""
        prompt = """
In a multi-GM social media simulation:
- Humans use GM_SOCIAL for interaction
- Bots use GM_DETECTION for analysis
- Suspicious bots use both GM_DETECTION and GM_AUDIT

If agent ALICE is human and agent BOT_C is suspicious bot,
which GMs will each agent be processed by?

Format: ALICE → [GM list], BOT_C → [GM list]
"""
        content = self.llm.call_llm(prompt)
        _LOGGER.info(f"LLM response: {content}")

        # Verify LLM understood the routing
        assert ("GM_SOCIAL" in content or "gm_social" in content.lower() or "social" in content.lower())
        assert ("GM_DETECTION" in content or "gm_detection" in content.lower() or "detection" in content.lower())

    def test_llm_can_reason_about_flow_sequencing(self):
        """Test LLM can reason about agent flow sequencing within GMs."""
        prompt = """
In a GM with flow_order: [pre_analysis, respond, post_analysis]
And entity_to_flow: {alice: pre_analysis, bob: respond, charlie: post_analysis}

What is the execution order of agents?
Answer format: Step1/Flow: [agents], Step2/Flow: [agents], Step3/Flow: [agents]
"""
        content = self.llm.call_llm(prompt, max_tokens=200)
        _LOGGER.info(f"LLM response: {content}")

        # Verify LLM understood sequencing
        assert "alice" in content.lower()
        assert "bob" in content.lower()
        assert "charlie" in content.lower()

    def test_llm_can_generate_multi_gm_config(self):
        """Test LLM can generate a valid multi-GM configuration."""
        prompt = """
Generate a YAML configuration for a multi-GM social media simulation.

Requirements:
- 3 agent types: humans, bots, trolls
- 3 GMs: social_interaction, bot_detection, content_moderation
- humans → social_interaction
- bots → bot_detection AND content_moderation (two GMs)
- trolls → content_moderation

Include these keys in the YAML:
gm:
  agent_classes:
  class_to_gms:
  gm_sequence:

Keep it minimal but valid YAML.
"""
        content = self.llm.call_llm(prompt, max_tokens=300)
        _LOGGER.info(f"Generated config:\n{content}")

        # Verify config has required elements
        assert "agent_classes" in content
        assert "class_to_gms" in content
        assert "gm_sequence" in content
