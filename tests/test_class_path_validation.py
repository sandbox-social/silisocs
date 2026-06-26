"""Validation-time class_path importability checks."""

from __future__ import annotations

import pytest
from omegaconf import OmegaConf

from silisocs.runtime.configuration.validation import validate_class_paths


def test_valid_and_null_class_paths_pass() -> None:
    cfg = OmegaConf.create(
        {
            "agents": {"builder": {"class_path": "silisocs.agents.fixed.FixedAgent"}},
            "env": {"gm": {"components": {"resolve": {"class_path": None}}}},
        }
    )
    validate_class_paths(cfg)


def test_unimportable_class_path_fails_with_location() -> None:
    cfg = OmegaConf.create({"agents": {"builder": {"class_path": "no.such.module.Klass"}}})
    with pytest.raises(ValueError, match="agents.builder"):
        validate_class_paths(cfg)


def test_missing_attribute_fails() -> None:
    cfg = OmegaConf.create({"x": {"class_path": "silisocs.agents.fixed.DoesNotExist"}})
    with pytest.raises(ValueError, match="DoesNotExist"):
        validate_class_paths(cfg)


def test_bare_name_fails_with_guidance() -> None:
    cfg = OmegaConf.create({"x": {"class_path": "FixedAgent"}})
    with pytest.raises(ValueError, match="fully qualified"):
        validate_class_paths(cfg)


def test_interpolated_class_path_is_skipped() -> None:
    cfg = OmegaConf.create({"x": {"class_path": "${some.other.key}"}})
    validate_class_paths(cfg)
