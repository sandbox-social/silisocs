"""Tests for persona record fitting: truncation, recycling, and suffixing.

When ``count`` (``num_agents``) exceeds the available persona records the
builder recycles the base records with numbered suffixes so any agent count
works out-of-the-box instead of silently capping at the record count. When
``count`` is smaller the list is simply truncated.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml
from hydra import compose, initialize_config_dir
from hydra.core.global_hydra import GlobalHydra
from omegaconf import DictConfig, OmegaConf

from silisocs.runtime.construction.agent_builders import PersonaPipelineAgentBuilder
from silisocs.runtime.construction.agent_configs import build_agent_configs

_RECYCLE_LOGGER = "silisocs.runtime.construction.agent_builders.persona_pipeline"


class _TestBuilder(PersonaPipelineAgentBuilder):
    """Concrete builder for persona-pipeline tests."""


def _world_cfg(count: int, records: list[dict[str, str]]) -> DictConfig:
    return OmegaConf.create(
        {
            "scenario_name": "default",
            "persona_pipeline": {
                "classes": {
                    "user": {
                        "count": count,
                        "class_path": "silisocs.agents.native.NativeAgent",
                        "data": {"source": "inline", "records": records},
                        "field_map": {"name": "name", "context": "persona"},
                    }
                },
            },
        }
    )


_THREE = [
    {"name": "A", "persona": "First."},
    {"name": "B", "persona": "Second."},
    {"name": "C", "persona": "Third."},
]

# The bundled default agents config ships exactly this many distinct personas.
_DEFAULT_PERSONA_COUNT = 100
# A request comfortably past the bundled count, to exercise recycling.
_OVERSUBSCRIBED_COUNT = _DEFAULT_PERSONA_COUNT + 50


def test_count_below_records_truncates_without_suffix() -> None:
    """Count < records yields exactly count agents with original names."""
    agents = _TestBuilder(_world_cfg(2, _THREE)).build_agent_configs()
    assert [a.params["name"] for a in agents] == ["A", "B"]


def test_count_equal_records_keeps_all_unsuffixed() -> None:
    """Count == records yields all base records and no suffix is applied."""
    agents = _TestBuilder(_world_cfg(3, _THREE)).build_agent_configs()
    assert [a.params["name"] for a in agents] == ["A", "B", "C"]


def test_count_above_records_recycles_with_numbered_suffix() -> None:
    """Count > records cycles personas; repeats get a numbered suffix."""
    agents = _TestBuilder(_world_cfg(7, _THREE)).build_agent_configs()
    names = [a.params["name"] for a in agents]
    assert names == ["A", "B", "C", "A 2", "B 2", "C 2", "A 3"]
    # Names stay unique (the unique-name contract still holds).
    assert len(set(names)) == len(names)
    # Recycled agents reuse the base persona context.
    assert agents[3].params["context"] == agents[0].params["context"] == "First."


def test_recycling_emits_warning(caplog: pytest.LogCaptureFixture) -> None:
    """Recycling logs a single warning naming the class and the shortfall."""
    with caplog.at_level(logging.WARNING, logger=_RECYCLE_LOGGER):
        _TestBuilder(_world_cfg(5, _THREE)).build_agent_configs()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "user" in message
    assert "recycling personas" in message


def test_no_recycle_no_warning(caplog: pytest.LogCaptureFixture) -> None:
    """No warning is emitted when count fits within the available records."""
    with caplog.at_level(logging.WARNING, logger=_RECYCLE_LOGGER):
        _TestBuilder(_world_cfg(3, _THREE)).build_agent_configs()
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


def test_zero_count_yields_no_agents() -> None:
    """Count == 0 still produces zero agents (seed/disabled classes)."""
    agents = _TestBuilder(_world_cfg(0, _THREE)).build_agent_configs()
    assert agents == []


def test_default_config_ships_one_hundred_unique_personas() -> None:
    """The shipped default agents config provides 100 distinct starter personas."""
    cfg_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "silisocs"
        / "conf"
        / "agents"
        / "default.yaml"
    )
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    records = data["persona_pipeline"]["classes"]["user"]["data"]["records"]
    names = [r["name"] for r in records]
    assert len(names) == _DEFAULT_PERSONA_COUNT
    assert len(set(names)) == _DEFAULT_PERSONA_COUNT


def test_default_personas_scale_past_one_hundred_via_recycling() -> None:
    """Requesting more than 100 agents from the default personas recycles cleanly."""
    cfg_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "silisocs"
        / "conf"
        / "agents"
        / "default.yaml"
    )
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    records = data["persona_pipeline"]["classes"]["user"]["data"]["records"]
    agents = _TestBuilder(_world_cfg(_OVERSUBSCRIBED_COUNT, records)).build_agent_configs()
    names = [a.params["name"] for a in agents]
    assert len(names) == _OVERSUBSCRIBED_COUNT
    # recycled names disambiguated, unique-name contract upheld
    assert len(set(names)) == _OVERSUBSCRIBED_COUNT


def test_default_experiment_config_scales_past_ten_agents() -> None:
    """End-to-end: the shipped CLI default builds `num_agents` agents, not a capped 10.

    Composes the real `experiment` config (as `silisocs num_agents=...` would) and
    builds agent specs, proving the former silent 10-persona cap is gone and that
    recycling kicks in past the bundled 100 personas.
    """
    base_conf = Path(__file__).resolve().parents[1] / "src" / "silisocs" / "conf"

    GlobalHydra.instance().clear()
    try:
        with initialize_config_dir(config_dir=str(base_conf), version_base=None):
            cfg = compose(
                config_name="experiment",
                overrides=[
                    f"num_agents={_OVERSUBSCRIBED_COUNT}",
                    "num_steps=1",
                    "output_rootname=/tmp/persona_scale_check",
                ],
            )
    finally:
        GlobalHydra.instance().clear()

    agents = build_agent_configs(cfg)
    names = [a.params["name"] for a in agents]
    assert len(names) == _OVERSUBSCRIBED_COUNT  # no silent cap at 10 (or at 100)
    assert len(set(names)) == _OVERSUBSCRIBED_COUNT  # names stay unique
    assert "Alex" in names  # bundled persona present
    assert "Alex 2" in names  # recycled past the bundled 100 with a numbered suffix


# ---------------------------------------------------------------------------
# num_agents (declared total) vs per-class `count` (actual total) reconciliation
# ---------------------------------------------------------------------------

_AGENT_CONFIGS_LOGGER = "silisocs.runtime.construction.agent_configs"


def _full_cfg(*, num_agents: int | None, class_count: int) -> DictConfig:
    """Build a full runtime cfg (root num_agents + agents subtree) for the entrypoint."""
    cfg: dict[str, object] = {
        "scenario_name": "default",
        "agents": {
            "persona_pipeline": {
                "classes": {
                    "user": {
                        "count": class_count,
                        "class_path": "silisocs.agents.native.NativeAgent",
                        "data": {"source": "inline", "records": _THREE},
                        "field_map": {"name": "name", "context": "persona"},
                    }
                },
            },
        },
    }
    if num_agents is not None:
        cfg["num_agents"] = num_agents
    return OmegaConf.create(cfg)


def _count_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == _AGENT_CONFIGS_LOGGER]


def test_class_count_matches_num_agents_no_warning(caplog: pytest.LogCaptureFixture) -> None:
    """When class counts sum to num_agents, no reconciliation warning fires."""
    count = 2
    with caplog.at_level(logging.WARNING, logger=_AGENT_CONFIGS_LOGGER):
        agents = build_agent_configs(_full_cfg(num_agents=count, class_count=count))
    assert len(agents) == count
    assert _count_warnings(caplog) == []


def test_class_counts_undershoot_num_agents_warns(caplog: pytest.LogCaptureFixture) -> None:
    """Fewer agents than the declared num_agents is surfaced (was silent before)."""
    declared, built = 5, 2
    with caplog.at_level(logging.WARNING, logger=_AGENT_CONFIGS_LOGGER):
        agents = build_agent_configs(_full_cfg(num_agents=declared, class_count=built))
    assert len(agents) == built
    assert any(f"num_agents={declared}" in m for m in _count_warnings(caplog))


def test_class_counts_overshoot_num_agents_warns(caplog: pytest.LogCaptureFixture) -> None:
    """More agents than the declared num_agents is surfaced (num_agents is not a cap)."""
    declared, built = 2, 5  # built > 3 records -> recycles; num_agents must not cap it
    with caplog.at_level(logging.WARNING, logger=_AGENT_CONFIGS_LOGGER):
        agents = build_agent_configs(_full_cfg(num_agents=declared, class_count=built))
    assert len(agents) == built
    assert any(
        f"Built {built} agent(s) but num_agents={declared}" in m for m in _count_warnings(caplog)
    )


def test_missing_num_agents_no_warning(caplog: pytest.LogCaptureFixture) -> None:
    """Configs without a declared num_agents are not second-guessed."""
    built = 3
    with caplog.at_level(logging.WARNING, logger=_AGENT_CONFIGS_LOGGER):
        agents = build_agent_configs(_full_cfg(num_agents=None, class_count=built))
    assert len(agents) == built
    assert _count_warnings(caplog) == []


def test_no_classes_defined_raises_actionable_error() -> None:
    """Missing persona_pipeline.classes errors loudly with actionable guidance.

    There is intentionally no implicit single-class fallback; the error must tell
    the user how to define a class or use the bundled default agents config.
    """
    cfg = OmegaConf.create(
        {"scenario_name": "default", "num_agents": 5, "agents": {"persona_pipeline": {}}}
    )
    with pytest.raises(ValueError, match="No agent classes defined") as excinfo:
        build_agent_configs(cfg)
    message = str(excinfo.value)
    assert "persona_pipeline" in message
    assert "agents=default" in message  # points at the bundled ready-made config
