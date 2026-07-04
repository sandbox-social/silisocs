"""Checkpoint resume planning for the runtime entrypoint.

Turns the ``sim.checkpoint`` config into a concrete plan for a resume: which
checkpoint file (if any) to restore from, the start step, the restore strategy and
its per-run action-event logs, which game masters carry an authoritative snapshot,
and the initializer context enriched with the checkpoint metadata. Keeping this off
``session.main`` leaves the entrypoint a thin orchestrator and makes the resume
decision unit-testable in isolation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from silisocs.evaluations.action_events import resolve_action_event_files
from silisocs.initialization.context import InitializationContext
from silisocs.runtime.checkpointing import (
    CheckpointRestoreStrategy,
    build_checkpoint_restore,
    checkpoint_authoritative_gm_names,
    checkpoint_runtime_metadata,
    load_checkpoint_file,
    resolve_checkpoint_source,
    select_resume_source,
)


@dataclass
class ResumePlan:
    """The resolved resume decision handed back to ``session.main``.

    On a fresh start ``checkpoint_data``/``checkpoint_restore`` are ``None`` and the
    remaining fields hold their fresh-start defaults, so the caller's downstream
    ``if checkpoint_data is not None`` / ``if checkpoint_restore is not None`` guards
    short-circuit exactly as before this extraction. ``initializer_context`` is the
    context to use going forward — passed through unchanged on a fresh start, or
    rebuilt with the checkpoint metadata attached on a resume.
    """

    initializer_context: InitializationContext
    start_step: int = 0
    checkpoint_data: dict[str, Any] | None = None
    checkpoint_restore: CheckpointRestoreStrategy | None = None
    action_event_files: list[Path] = field(default_factory=list)
    authoritative_gm_names: frozenset[str] = frozenset()
    checkpoint_meta: dict[str, Any] = field(default_factory=dict)


def plan_checkpoint_resume(
    *,
    checkpoint_cfg: Any,
    output_dir: str,
    initializer_context: InitializationContext,
    metrics: Any,
    logger: logging.Logger,
) -> ResumePlan:
    """Resolve the resume source and build the restore plan (see module docstring)."""
    source_run = None
    auto_resume = True
    restore_cfg = None
    if checkpoint_cfg is not None:
        source_run = getattr(checkpoint_cfg, "source_run", None)
        auto_resume = bool(getattr(checkpoint_cfg, "auto_resume", True))
        restore_cfg = getattr(checkpoint_cfg, "restore", None)

    # An explicit ``source_run`` resumes from that prior run and requires a
    # ``restore`` strategy; otherwise auto-resume (default on) picks up this
    # run's own output dir only if it already holds checkpoints.
    if source_run and restore_cfg is None:
        raise ValueError("sim.checkpoint.source_run requires sim.checkpoint.restore.")
    source_path = select_resume_source(
        source_run,
        auto_resume=auto_resume,
        restore_present=restore_cfg is not None,
        output_dir=output_dir,
    )
    if source_path is None:
        return ResumePlan(initializer_context=initializer_context)

    is_auto_resume = not source_run
    resume_path = resolve_checkpoint_source(source_path)
    action_event_files = resolve_action_event_files(source_path)
    checkpoint_restore = build_checkpoint_restore(restore_cfg)

    with metrics.phase("checkpoint_load"):
        checkpoint_data = load_checkpoint_file(resume_path)
        authoritative = checkpoint_authoritative_gm_names(checkpoint_data)
        checkpoint_meta = checkpoint_runtime_metadata(checkpoint_data)

    default_step = checkpoint_data.get("step")
    if default_step is None:
        raise ValueError("Checkpoint is missing required `step` value.")
    start_step = int(default_step)

    if is_auto_resume:
        logger.info(
            "Auto-resuming from latest checkpoint in %s at step %d", resume_path, start_step
        )
    else:
        logger.info("Restoring from checkpoint %s at step %d", resume_path, start_step)
    checkpoint_meta["auto_resumed"] = is_auto_resume
    checkpoint_meta["checkpoint_step"] = start_step
    checkpoint_meta["checkpoint_file"] = str(resume_path)
    checkpoint_meta["source_run"] = str(source_path)
    checkpoint_meta["action_events_files"] = [str(p) for p in action_event_files]
    # Only ``checkpoint`` changes; ``replace`` copies every other field verbatim so a
    # new field on InitializationContext can never be silently dropped here.
    enriched_context = replace(initializer_context, checkpoint=checkpoint_meta)
    return ResumePlan(
        initializer_context=enriched_context,
        start_step=start_step,
        checkpoint_data=checkpoint_data,
        checkpoint_restore=checkpoint_restore,
        action_event_files=action_event_files,
        authoritative_gm_names=authoritative,
        checkpoint_meta=checkpoint_meta,
    )
