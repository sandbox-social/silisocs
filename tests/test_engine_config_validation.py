"""Validation of ``sim.engine`` sub-slots and per-GM maps.

These guard the loud-failure behavior for the newest, most actively-configured
engine knobs: a typo in a sub-slot key, a bogus ``step.params`` key, or an
unknown GM name in the per-GM maps must raise rather than silently no-op.
"""

from __future__ import annotations

import pytest
from omegaconf import OmegaConf

from silisocs.runtime.configuration.validation import (
    _validate_engine_slots,
    validate_runtime_structure,
)


def _engine(cfg: dict) -> None:
    _validate_engine_slots(OmegaConf.create(cfg))


def test_typo_in_engine_sub_slot_key_raises() -> None:
    with pytest.raises(ValueError, match="sim.engine.step"):
        _engine(
            {"env": {"gm": {"name": "gm"}}, "sim": {"engine": {"step": {"buit_in": "multi_gm"}}}}
        )


def test_bogus_step_params_key_raises() -> None:
    with pytest.raises(ValueError, match="sim.engine.step.params"):
        _engine(
            {
                "env": {"gm": {"name": "gm"}},
                "sim": {"engine": {"step": {"built_in": "flow", "params": {"agent_to_flows": {}}}}},
            }
        )


def test_retired_chain_execution_param_gets_migration_hint() -> None:
    with pytest.raises(ValueError, match="chain_execution has been removed"):
        _engine(
            {
                "env": {"gm": {"name": "gm"}},
                "sim": {
                    "engine": {"step": {"built_in": "multi_gm", "params": {"chain_execution": "x"}}}
                },
            }
        )


def test_unknown_gm_name_in_gm_scoped_map_raises() -> None:
    for key in ("gm_turn_policies", "gm_concurrency_caps"):
        with pytest.raises(ValueError, match="unknown game master"):
            _engine(
                {
                    "env": {"gm": {"name": "real_gm"}},
                    "sim": {"engine": {"step": {"params": {key: {"typo_gm": {}}}}}},
                }
            )


def test_custom_class_path_step_leaves_params_open() -> None:
    # A custom step strategy may carry arbitrary params; only built-ins are strict.
    _engine(
        {
            "env": {"gm": {"name": "g"}},
            "sim": {"engine": {"step": {"class_path": "my.Step", "params": {"anything": 1}}}},
        }
    )


def test_known_gm_names_and_valid_params_pass() -> None:
    _engine(
        {
            "env": {"gm": {"name": "real_gm"}},
            "sim": {
                "engine": {
                    "step": {
                        "built_in": "flow",
                        "params": {
                            "flow_order": ["a"],
                            "gm_turn_policies": {"real_gm": {}},
                            "gm_concurrency_caps": {"real_gm": 2},
                        },
                    }
                }
            },
        }
    )


def test_backend_action_aliases_valid_on_single_gm() -> None:
    # action_aliases was accepted for multi-GM backends but rejected for single-GM.
    validate_runtime_structure(
        OmegaConf.create(
            {
                "env": {
                    "gm": {
                        "name": "g",
                        "backend": {
                            "type": "twitter_like",
                            "action_aliases": {"create_tweet": ["post"]},
                        },
                    }
                },
                "sim": {},
            }
        )
    )
