"""Unit tests for checkpoint resume planning (runtime/execution/resume.py).

Fast, filesystem-light tests of the ``plan_checkpoint_resume`` decision that
``session.main`` delegates to — the fresh-start pass-through and the
``source_run`` guard, without launching a full run.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from silisocs.initialization.context import InitializationContext
from silisocs.runtime.execution.resume import ResumePlan, plan_checkpoint_resume

_LOGGER = logging.getLogger(__name__)


def _context() -> InitializationContext:
    return InitializationContext(shared_memories=["seed"])


def test_fresh_start_returns_defaults_and_passes_context_through(tmp_path):
    """No checkpoint config and an empty output dir → a fresh-start plan."""
    context = _context()
    plan = plan_checkpoint_resume(
        checkpoint_cfg=None,
        output_dir=str(tmp_path),
        initializer_context=context,
        metrics=SimpleNamespace(),  # .phase is never touched on the fresh path
        logger=_LOGGER,
    )
    assert isinstance(plan, ResumePlan)
    assert plan.start_step == 0
    assert plan.checkpoint_data is None
    assert plan.checkpoint_restore is None
    assert plan.action_event_files == []
    assert plan.authoritative_gm_names == frozenset()
    assert plan.checkpoint_meta == {}
    # The context is passed through unchanged (not rebuilt) on a fresh start.
    assert plan.initializer_context is context


def test_auto_resume_off_with_empty_dir_is_fresh_start(tmp_path):
    """auto_resume disabled and no source_run → fresh start even if dir exists."""
    plan = plan_checkpoint_resume(
        checkpoint_cfg=SimpleNamespace(source_run=None, auto_resume=False, restore=None),
        output_dir=str(tmp_path),
        initializer_context=_context(),
        metrics=SimpleNamespace(),
        logger=_LOGGER,
    )
    assert plan.checkpoint_data is None
    assert plan.start_step == 0


def test_source_run_requires_restore_strategy(tmp_path):
    """An explicit source_run without a restore strategy fails loudly."""
    with pytest.raises(ValueError, match="source_run requires sim.checkpoint.restore"):
        plan_checkpoint_resume(
            checkpoint_cfg=SimpleNamespace(
                source_run=str(tmp_path), auto_resume=True, restore=None
            ),
            output_dir=str(tmp_path),
            initializer_context=_context(),
            metrics=SimpleNamespace(),
            logger=_LOGGER,
        )
