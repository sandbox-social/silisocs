"""Validation of ``sim.engine`` sub-slots and per-GM maps.

These guard the loud-failure behavior for the newest, most actively-configured
engine knobs: a typo in a sub-slot key, a bogus ``step.params`` key, or an
unknown GM name in the per-GM maps must raise rather than silently no-op.
"""

from __future__ import annotations

from typing import Any

import pytest
from omegaconf import DictConfig, OmegaConf

from silisocs.runtime.configuration.validation import (
    RunDirInterpolationCheck,
    _validate_control_config,
    _validate_engine_slots,
    validate_runtime_structure,
)
from silisocs.runtime.language_models.catalog import (
    BUILT_IN_PROVIDERS,
    OPENAI_COMPATIBLE_PRESETS,
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


def _rates(entries: object) -> dict:
    return {
        "env": {"gm": {"name": "g"}},
        "sim": {
            "engine": {
                "participation": {
                    "built_in": "activity_markov",
                    "params": {"activity_transition_rates": entries},
                }
            }
        },
    }


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        # The audit case: a positional pair leaves "what are a and b?" unanswerable.
        ({"user": [0.8, 0.1]}, "Positional pairs are not supported"),
        ({"user": 0.8}, "Positional pairs are not supported"),
        ({"user": {"activation": 0.8}}, "declares neither"),
        ({"user": {"inactive_to_active": 1.5}}, "between 0 and 1"),
        ({"user": {"inactive_to_active": "often"}}, "must be a number"),
    ],
)
def test_malformed_activity_rate_entry_raises_at_config_time(entries: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _engine(_rates(entries))


def test_named_activity_rate_entries_pass() -> None:
    _engine(_rates({"user": {"inactive_to_active": 0.8, "active_to_inactive": 0.1}}))
    # Either rate alone is valid: the missing one mirrors the declared one.
    _engine(_rates({"lurker": {"active_to_inactive": 0.6}}))


def test_retired_output_rootname_key_gets_migration_error() -> None:
    with pytest.raises(ValueError, match="'output_rootname' was renamed to 'output_dir'"):
        validate_runtime_structure(
            OmegaConf.create({"output_rootname": "outputs", "env": {}, "sim": {}})
        )


def test_retired_output_rootname_raises_even_when_empty() -> None:
    # An empty/null value is still the retired key: the run would otherwise fall
    # back to Hydra's job dir while the config looks like it set an output path.
    for value in ("", None):
        with pytest.raises(ValueError, match="no longer read by the runtime"):
            validate_runtime_structure(
                OmegaConf.create({"output_rootname": value, "env": {}, "sim": {}})
            )


def test_current_output_dir_key_passes() -> None:
    validate_runtime_structure(OmegaConf.create({"output_dir": "outputs", "env": {}, "sim": {}}))


def test_every_documented_llm_provider_passes_validation() -> None:
    # The factory's tables are the source of truth for provider names; validation
    # must accept everything they accept (a stale hardcoded allowlist once
    # rejected every OpenAI-compatible preset before the factory saw it).
    for provider in (*BUILT_IN_PROVIDERS, *OPENAI_COMPATIBLE_PRESETS):
        validate_runtime_structure(
            OmegaConf.create({"env": {}, "sim": {"llm": {"provider": provider}}})
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


@pytest.mark.parametrize(
    ("control", "message"),
    [
        ({"start_paused": "false"}, "start_paused"),
        ({"poll_interval": 0}, "poll_interval"),
        ({"poll_interval": "fast"}, "poll_interval"),
    ],
)
def test_interactive_control_types_fail_during_preflight(control, message) -> None:
    cfg = OmegaConf.create({"sim": {"engine": {"control": control}}})
    with pytest.raises(ValueError, match=message):
        _validate_control_config(cfg)


# --- RunDirInterpolationCheck: translate the missing-run-param error in BOTH modes


def _cfg_with_unresolvable_run_dir() -> DictConfig:
    # hydra.run.dir / sweep.dir reference ${jobname_format}, which a scenario that
    # dropped the universal run params never defines -> InterpolationResolutionError.
    return OmegaConf.create(
        {
            "hydra": {
                "run": {"dir": "out/${jobname_format}"},
                "sweep": {"dir": "multirun/${jobname_format}"},
                "output_subdir": ".hydra",
                "job": {"name": "job_${jobname_format}"},
            }
        }
    )


def test_run_dir_check_translates_missing_param_on_single_run() -> None:
    cb = RunDirInterpolationCheck()
    with pytest.raises(ValueError, match="jobname_format"):
        cb.on_run_start(_cfg_with_unresolvable_run_dir())


def test_run_dir_check_translates_missing_param_on_multirun() -> None:
    # -m/multirun resolves the sweep dir + job name before the task fn; the callback
    # must translate the same error there instead of dying with the raw one.
    cb = RunDirInterpolationCheck()
    with pytest.raises(ValueError, match="scenario's world/default.yaml"):
        cb.on_multirun_start(_cfg_with_unresolvable_run_dir())


def test_run_dir_check_passes_when_params_present() -> None:
    cb = RunDirInterpolationCheck()
    good = OmegaConf.create(
        {
            "jobname_format": "myrun",
            "hydra": {
                "run": {"dir": "out/${jobname_format}"},
                "sweep": {"dir": "multirun/${jobname_format}"},
                "output_subdir": ".hydra",
                "job": {"name": "job_${jobname_format}"},
            },
        }
    )
    cb.on_run_start(good)
    cb.on_multirun_start(good)


# --- flow_order: absent (default prefix) vs explicit [] (no prefix) -----------


def _step_strategy(step_params: dict | None) -> Any:
    from silisocs.runtime.construction.engines import build_engine

    step: dict = {"built_in": "flow"}
    if step_params is not None:
        step["params"] = step_params
    cfg = OmegaConf.create({"sim": {"engine": {"step": step}}})
    return build_engine(cfg).step_strategy


def test_absent_flow_order_gets_the_default_serial_prefix() -> None:
    assert _step_strategy(None).flow_order == ("fixed_pre", "default")
    assert _step_strategy({}).flow_order == ("fixed_pre", "default")


def test_explicit_empty_flow_order_means_no_serial_prefix() -> None:
    """`flow_order: []` is documented as "no serial prefix" and must survive.

    It used to be collapsed back to the default prefix, silently reintroducing a
    barrier the scenario deliberately removed.
    """
    assert _step_strategy({"flow_order": []}).flow_order == ()


def test_explicit_flow_order_is_preserved() -> None:
    assert _step_strategy({"flow_order": ["a", "b"]}).flow_order == ("a", "b")


def test_non_list_flow_order_raises() -> None:
    with pytest.raises(ValueError, match="flow_order must be a list"):
        _step_strategy({"flow_order": "default"})
