"""The persisted effective config must never carry live credentials.

``effective_config.yaml`` is a run artifact — rendered verbatim by Studio's
Effective YAML panel, archived, and shared — and nothing reads model
credentials back out of it, so every non-empty ``api_key`` is masked before
the file is written.
"""

from __future__ import annotations

from typing import Any

from silisocs.runtime.execution.session import _REDACTED_SECRET, _redact_secrets


def test_redact_secrets_masks_every_api_key() -> None:
    cfg = {
        "sim": {"llm": {"name": "gpt-4o-mini", "api_key": "sk-live-global"}},
        "agents": {
            "persona_pipeline": {
                "classes": {
                    "influencer": {"model": {"name": "gpt-4o", "api_key": "sk-live-per-class"}}
                }
            }
        },
        "nested_list": [{"api_key": "sk-live-in-list"}, "plain"],
    }

    redacted = _redact_secrets(cfg)

    assert redacted["sim"]["llm"]["api_key"] == _REDACTED_SECRET
    classes = redacted["agents"]["persona_pipeline"]["classes"]
    assert classes["influencer"]["model"]["api_key"] == _REDACTED_SECRET
    assert redacted["nested_list"][0]["api_key"] == _REDACTED_SECRET
    assert "sk-live" not in str(redacted)


def test_redact_secrets_preserves_everything_else() -> None:
    cfg: dict[str, Any] = {
        "sim": {"llm": {"name": "gpt-4o-mini", "api_key": None, "temperature": 0.5}},
        "num_steps": 5,
    }

    redacted = _redact_secrets(cfg)

    # An unset key stays legibly unset, non-secret values pass through, and the
    # input container is copied, never mutated.
    assert redacted["sim"]["llm"]["api_key"] is None
    assert redacted["sim"]["llm"]["name"] == "gpt-4o-mini"
    assert redacted["num_steps"] == 5
    assert redacted is not cfg
    cfg["sim"]["llm"]["api_key"] = "sk-set-after"
    assert _redact_secrets(cfg)["sim"]["llm"]["api_key"] == _REDACTED_SECRET
