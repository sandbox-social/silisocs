"""Studio orchestration for checkpoint branches.

The runtime planner owns compatibility. Studio only reconstructs the parent
launch command, assigns child output/control paths, and submits ordinary run
jobs to the existing queue.
"""

from __future__ import annotations

import json
import re
import secrets
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from silisocs.runtime.execution.branching import BranchPlan, override_key, plan_checkpoint_branch
from silisocs.studio.jobs import Job, JobManager

_MANAGED_KEYS = frozenset(
    {
        "output_dir",
        "seed",
        "sim.checkpoint.source_run",
        "sim.checkpoint.source_step",
        "sim.checkpoint.auto_resume",
        "sim.engine.control.built_in",
        "sim.engine.control.control_file",
        "sim.engine.control.start_paused",
        "sim.checkpoint.branch.id",
        "sim.checkpoint.branch.group_id",
        "sim.checkpoint.branch.parent_run_id",
        "sim.checkpoint.branch.checkpoint_step",
        "sim.checkpoint.branch.mode",
        "sim.checkpoint.branch.continuation_seed",
    }
)


@dataclass(frozen=True)
class PreparedBranch:
    """A planned branch plus the subprocess inputs Studio will queue."""

    id: str
    name: str
    plan: BranchPlan
    command: list[str]
    output_dir: Path
    control_path: Path | None
    snapshot: dict[str, Any]


def _scalar(value: Any) -> str:
    """Render a scalar as Hydra-safe JSON/YAML syntax."""
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def _argument_key(argument: str) -> str | None:
    if "=" not in argument or argument.startswith("--"):
        return None
    try:
        return override_key(argument)
    except ValueError:
        return None


def _slug(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip()).strip("-")
    return text[:40] or "branch"


def _parent_launch(job: Job) -> tuple[list[str], str, dict[str, str]]:
    payload = json.loads(job.command_json or "{}")
    argv = payload.get("argv") if isinstance(payload, dict) else None
    cwd = payload.get("cwd") if isinstance(payload, dict) else None
    env = payload.get("env") if isinstance(payload, dict) else None
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
        raise ValueError("The parent run has no reusable Studio launch command")
    if not isinstance(cwd, str) or not cwd:
        raise ValueError("The parent run has no reusable working directory")
    return list(argv), cwd, dict(env) if isinstance(env, dict) else {}


def _branch_command(
    parent_argv: list[str],
    *,
    plan: BranchPlan,
    branch_id: str,
    group_id: str,
    parent_run_id: str,
    output_dir: Path,
    interactive: bool,
    start_paused: bool,
) -> tuple[list[str], Path | None]:
    replacement_keys = {override_key(item) for item in plan.overrides}
    managed = _MANAGED_KEYS | replacement_keys
    if plan.mode != "resample":
        managed = managed - {"seed"}
    command = [item for item in parent_argv if _argument_key(item) not in managed]
    command.extend(plan.overrides)
    command.extend(
        [
            f"++output_dir={_scalar(str(output_dir))}",
            f"sim.checkpoint.source_run={_scalar(str(plan.source_run))}",
            f"sim.checkpoint.source_step={plan.checkpoint_step}",
            "sim.checkpoint.auto_resume=false",
            f"sim.checkpoint.branch.id={_scalar(branch_id)}",
            f"sim.checkpoint.branch.group_id={_scalar(group_id)}",
            f"sim.checkpoint.branch.parent_run_id={_scalar(parent_run_id)}",
            f"sim.checkpoint.branch.checkpoint_step={plan.checkpoint_step}",
            f"sim.checkpoint.branch.mode={plan.mode}",
            (
                "sim.checkpoint.branch.continuation_seed=null"
                if plan.continuation_seed is None
                else f"sim.checkpoint.branch.continuation_seed={plan.continuation_seed}"
            ),
        ]
    )
    if plan.continuation_seed is not None:
        command.append(f"seed={plan.continuation_seed}")

    control_path = None
    if interactive:
        control_path = output_dir / "run.control"
        command.extend(
            [
                "sim.engine.control.built_in=control_file",
                f"sim.engine.control.control_file={_scalar(str(control_path))}",
                f"sim.engine.control.start_paused={'true' if start_paused else 'false'}",
            ]
        )
        if not any(_argument_key(item) == "sim.checkpoint.every_n_steps" for item in command):
            command.append("sim.checkpoint.every_n_steps=1")
    return command, control_path


def find_parent_job(jobs: JobManager, run_path: Path) -> Job:
    """Return the Studio job that produced ``run_path``."""
    resolved = run_path.resolve()
    for job in jobs.store.list():
        if job.kind == "run" and job.output_dir and Path(job.output_dir).resolve() == resolved:
            return job
    raise ValueError(
        "Branching currently requires Studio launch provenance; this run was not launched "
        "by this Studio state directory."
    )


def prepare_branches(
    *,
    parent_job: Job,
    parent_run_id: str,
    parent_run_path: Path,
    output_root: Path,
    requests: list[Mapping[str, Any]],
    checkpoint_step: int | None,
    interactive: bool,
    start_paused: bool,
) -> tuple[str, list[PreparedBranch], str, dict[str, str]]:
    """Plan a complete batch before any child job is submitted."""
    if not requests or len(requests) > 32:
        raise ValueError("branches must contain between 1 and 32 requests")
    parent_argv, cwd, env = _parent_launch(parent_job)
    group_id = uuid.uuid4().hex[:12]
    prepared: list[PreparedBranch] = []
    for index, request in enumerate(requests, start=1):
        name = str(request.get("name") or f"branch-{index}").strip()
        raw_overrides = request.get("overrides") or []
        if not isinstance(raw_overrides, list) or not all(
            isinstance(item, str) for item in raw_overrides
        ):
            raise ValueError("Each branch's overrides must be a list of strings")
        mode = str(request.get("mode") or "exact")
        seed = request.get("continuation_seed")
        if mode.strip().lower() == "resample" and seed is None:
            seed = secrets.randbelow(2**31 - 1) + 1
        plan = plan_checkpoint_branch(
            parent_run_path,
            checkpoint_step=checkpoint_step,
            mode=mode,
            continuation_seed=seed,
            overrides=tuple(raw_overrides),
        )
        branch_id = f"{_slug(name)}-{uuid.uuid4().hex[:8]}"
        output_dir = (output_root / "branches" / group_id / branch_id).resolve()
        command, control_path = _branch_command(
            parent_argv,
            plan=plan,
            branch_id=branch_id,
            group_id=group_id,
            parent_run_id=parent_run_id,
            output_dir=output_dir,
            interactive=interactive,
            start_paused=start_paused,
        )
        prepared.append(
            PreparedBranch(
                id=branch_id,
                name=name,
                plan=plan,
                command=command,
                output_dir=output_dir,
                control_path=control_path,
                snapshot={
                    "scenario": parent_job.scenario,
                    "branch": {
                        "id": branch_id,
                        "group_id": group_id,
                        "parent_run_id": parent_run_id,
                        **plan.to_dict(),
                    },
                    "command": command,
                },
            )
        )
    return group_id, prepared, cwd, env


def submit_prepared_branches(
    jobs: JobManager,
    prepared: list[PreparedBranch],
    *,
    cwd: str,
    env: dict[str, str],
    scenario: str | None,
) -> list[Job]:
    """Queue a planned batch through the normal run job manager."""
    return [
        jobs.submit(
            kind="run",
            command=item.command,
            cwd=cwd,
            scenario=scenario,
            snapshot=item.snapshot,
            output_dir=item.output_dir,
            env=env,
            control_path=item.control_path,
        )
        for item in prepared
    ]
