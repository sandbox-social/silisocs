"""The run manifest must record WHICH provider answered, not only the model name.

``llm_name`` is the config's ``sim.llm.name``, which a scenario keeps declaring
(``gpt-4o-mini``) even when the run was executed offline with
``provider=scripted`` — so a manifest of a scripted run read as if a real model
had been called.
"""

from __future__ import annotations

import pytest
from omegaconf import OmegaConf

from silisocs.runtime.execution.manifest import build_run_manifest
from silisocs.runtime.execution.session import _resolved_llm_provider


@pytest.mark.parametrize(
    ("llm", "expected"),
    [
        ({"provider": "openai", "name": "gpt-4o-mini", "disabled": False}, "openai"),
        ({"provider": "scripted", "name": "gpt-4o-mini", "disabled": False}, "scripted"),
        # `disabled` outranks `provider` in the model factory; mirror that here.
        ({"provider": "openai", "name": "gpt-4o-mini", "disabled": True}, "disabled"),
        ({"provider": "disabled", "name": "disabled", "disabled": False}, "disabled"),
        ({"provider": "  openai  ", "name": "m", "disabled": False}, "openai"),
    ],
    ids=["openai", "scripted", "disabled_flag", "disabled_provider", "whitespace"],
)
def test_resolved_llm_provider_mirrors_the_model_factory(llm: dict, expected: str) -> None:
    cfg = OmegaConf.create({"sim": {"llm": llm}})

    assert _resolved_llm_provider(cfg) == expected


def test_manifest_carries_llm_provider_alongside_llm_name(tmp_path) -> None:
    manifest = build_run_manifest(
        output_dir=tmp_path,
        status="success",
        meta={"llm_name": "gpt-4o-mini", "llm_provider": "scripted", "scenario": "demo"},
        counters={},
    )

    assert manifest["llm_name"] == "gpt-4o-mini"
    assert manifest["llm_provider"] == "scripted"


def test_manifest_llm_provider_is_none_when_unrecorded(tmp_path) -> None:
    """Older runs have no `llm_provider` meta; the field is present and null."""
    manifest = build_run_manifest(
        output_dir=tmp_path,
        status="success",
        meta={"llm_name": "gpt-4o-mini"},
        counters={},
    )

    assert "llm_provider" in manifest
    assert manifest["llm_provider"] is None
