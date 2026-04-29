"""Tests for normalized runtime projection."""

from __future__ import annotations

import pytest
from omegaconf import OmegaConf

from silisocs.runtime.projection import RuntimeProjection


def _cfg(*, action_mode: str = "custom", tool_mode: str = "single", resolve: str = "tool_calling"):
    return OmegaConf.create(
        {
            "sim": {
                "action_mode": action_mode,
                "tool_calling": {"mode": tool_mode},
                "engine": {"preset": "base"},
                "gm": {
                    "preset": "base",
                    "components": {"resolve": {"built_in": resolve}},
                },
            }
        }
    )


def test_runtime_projection_normalizes_modes() -> None:
    """Projection should normalize string modes to lowercase values."""
    cfg = _cfg(action_mode="Custom", tool_mode="Multi", resolve="tool_calling")
    projection = RuntimeProjection.from_cfg(cfg)
    assert projection.action_mode == "custom"
    assert projection.tool_calling_mode == "multi"
    assert projection.resolve_built_in == "tool_calling"


def test_runtime_projection_rejects_mismatched_resolver() -> None:
    """Tool-calling mode and resolver selection must stay aligned."""
    with pytest.raises(ValueError, match="Tool-calling mode must match"):
        RuntimeProjection.from_cfg(_cfg(tool_mode="single", resolve="parsed_action"))
