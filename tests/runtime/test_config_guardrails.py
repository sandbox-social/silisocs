"""Build-time guards for two configs that used to fail SILENTLY at runtime.

Both are the same failure shape the repo's failure policy exists to prevent: a
run that completes, reports success, and produced nothing useful.

1. ``sim.llm.disabled=true`` under the packaged ``sim.tool_calling.mode: single``
   builds the no-op model, whose ``sample_tool_calls`` returns ``[]`` — every
   agent turn then fails and the run commits zero actions. ``silisocs doctor``
   used to RECOMMEND the flag.
2. A ``probes:`` block at the config ROOT is read by nothing. It lands there when
   a user puts it in ``conf/world/default.yaml`` (``# @package _global_``); the
   run then succeeds with zero probe events, silently dropping the scenario's
   measurement instrument.
"""

from __future__ import annotations

import pytest
from omegaconf import DictConfig, OmegaConf

from silisocs.runtime.configuration.validation import (
    _reject_no_op_model_under_tool_calling,
    _reject_root_probes_block,
)


def _llm_cfg(llm: dict, tool_calling_mode: str | None) -> DictConfig:
    sim: dict = {"llm": llm}
    if tool_calling_mode is not None:
        sim["tool_calling"] = {"mode": tool_calling_mode}
    return OmegaConf.create({"sim": sim})


# ------------------------------------------------- no-op model + tool calling


@pytest.mark.parametrize("mode", ["single", "multi"])
def test_disabled_llm_with_tool_calling_raises_at_build(mode: str) -> None:
    cfg = _llm_cfg({"provider": "openai", "name": "gpt-4o-mini", "disabled": True}, mode)

    with pytest.raises(ValueError) as excinfo:
        _reject_no_op_model_under_tool_calling(cfg)

    message = str(excinfo.value)
    assert "sim.llm.disabled=true" in message
    assert f"sim.tool_calling.mode={mode!r}" in message
    # The message must name both escapes, not just diagnose.
    assert "sim.llm.provider=scripted" in message
    assert "sim.tool_calling.mode=none" in message
    # Per-GM overrides are out of scope for this check; say so instead of implying
    # a per-GM `mode: none` would exempt the run.
    assert "env.gm.tool_calling.mode" in message


def test_provider_disabled_spelling_is_caught_too() -> None:
    """`provider: disabled` builds the same NoLanguageModel as the flag."""
    cfg = _llm_cfg({"provider": "disabled", "name": "disabled", "disabled": False}, "single")

    with pytest.raises(ValueError, match=r"sim\.llm\.provider=disabled"):
        _reject_no_op_model_under_tool_calling(cfg)


@pytest.mark.parametrize(
    ("llm", "mode"),
    [
        ({"provider": "openai", "disabled": True}, "none"),
        ({"provider": "disabled", "disabled": False}, "none"),
        ({"provider": "scripted", "disabled": False}, "single"),
        ({"provider": "openai", "disabled": False}, "multi"),
        ({"provider": "scripted", "disabled": False}, None),
    ],
    ids=["disabled+none", "provider_disabled+none", "scripted+single", "openai+multi", "no_block"],
)
def test_workable_llm_and_tool_calling_pairs_pass(llm: dict, mode: str | None) -> None:
    _reject_no_op_model_under_tool_calling(_llm_cfg(llm, mode))


# --------------------------------------------------------- root probes block


def test_root_level_probes_block_raises_and_names_the_right_file() -> None:
    cfg = OmegaConf.create(
        {
            "scenario_name": "demo",
            "probes": {"probes": {"mine": {"probe_type": "BinaryProbe"}}},
        }
    )

    with pytest.raises(ValueError) as excinfo:
        _reject_root_probes_block(cfg)

    message = str(excinfo.value)
    assert "root-level `probes:` block" in message
    assert "eval.probes" in message
    assert "conf/eval.yaml" in message
    assert "@package _global_" in message


def test_empty_root_probes_block_still_raises() -> None:
    """An empty block is still the wrong location, and still loses every probe."""
    with pytest.raises(ValueError, match="root-level"):
        _reject_root_probes_block(OmegaConf.create({"probes": {}}))


def test_probes_under_eval_are_accepted() -> None:
    cfg = OmegaConf.create(
        {
            "eval": {
                "probes": {
                    "deployment": {"enabled": True},
                    "probes": {"mine": {"probe_type": "BinaryProbe"}},
                }
            }
        }
    )

    _reject_root_probes_block(cfg)
