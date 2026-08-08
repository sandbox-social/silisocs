"""Command-line interface for the ``silisocs-study`` study runner.

Holds the argparse surface and the verb handlers it dispatches to (``plan``,
``generate-bash``, ``run``, ``organize``, ``slurm-array``, ``submitit``,
``summary-append``), plus the interactive preflight confirmation. Each handler
is a thin adapter: it resolves paths from the parsed namespace, calls
:mod:`silisocs.studies.plan` to expand/filter the run matrix and
:mod:`silisocs.studies.execute` to launch and record it, and prints the result.

``silisocs.studies.run_study`` re-exports :func:`main` so the console script and
``python -m silisocs.studies.run_study`` keep working.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from silisocs.runtime.io import append_jsonl_line
from silisocs.runtime.provenance import environment_provenance as _environment_provenance
from silisocs.studies.execute import (
    enrich_study_with_results,
    execute_pending_runs,
    partition_completed_runs,
    resolve_gpu_ids_for_run,
    run_command_with_optional_hooks,
    write_study_index,
)
from silisocs.studies.plan import (
    PLAN_PREVIEW_ROWS,
    build_submitit_job_commands,
    count_array_tasks,
    csv_compact,
    expand_runs,
    filter_run_specs,
    load_yaml,
    plan_rows,
    preflight_summary,
    render_bash_script,
    resolve_summary_paths,
    study_generated_dir,
    submitit_group_filters,
)
from silisocs.studies.study_artifacts import (
    organize_study_outputs,
    resolve_study_definition_path,
    write_json,
    write_yaml,
)
from silisocs.studies.study_types import (
    SCHEMA_VERSION,
    StudyConfigError,
    ensure_mapping,
    now_iso,
    resolve_study_id,
)

PREFLIGHT_CONFIRM_RUN_COUNT = 50


def _confirm_run_count(run_count: int, assume_yes: bool) -> bool:
    """Gate large launches behind --yes or an interactive confirmation."""
    if run_count <= PREFLIGHT_CONFIRM_RUN_COUNT or assume_yes:
        return True
    if not sys.stdin.isatty():
        print(
            f"Aborting: {run_count} runs exceed the preflight threshold "
            f"({PREFLIGHT_CONFIRM_RUN_COUNT}) and stdin is not a TTY. "
            "Re-run with --yes to proceed.",
            file=sys.stderr,
        )
        return False
    answer = input(
        f"About to launch {run_count} runs "
        f"(threshold: {PREFLIGHT_CONFIRM_RUN_COUNT}). Continue? [y/N] "
    )
    return answer.strip().lower() in {"y", "yes"}


def cmd_summary_append(args: argparse.Namespace) -> int:
    """Append a human/LLM study summary entry to JSONL and markdown files."""
    repo_root = Path(args.repo_root).resolve()
    study_path = Path(args.study).resolve()
    study_data = load_yaml(study_path)

    summary_md, summary_log = resolve_summary_paths(repo_root, study_data)
    study = ensure_mapping("study", study_data.get("study"))
    entry = {
        "created_at": now_iso(),
        "study_id": resolve_study_id(study),
        "study_name": study.get("name"),
        "author": str(args.author),
        "hypothesis": args.hypothesis,
        "condition": args.condition,
        "note": str(args.note),
        "evidence_paths": list(args.evidence or []),
    }

    append_jsonl_line(summary_log, entry)

    summary_md.parent.mkdir(parents=True, exist_ok=True)
    if not summary_md.exists():
        summary_md.write_text(
            f"# Study Summary\n\nStudy: {study.get('name')}\n\n", encoding="utf-8"
        )
    block = [
        "",
        f"## {entry['created_at']} | {entry['author']}",
        f"Hypothesis: {entry['hypothesis'] or 'n/a'}",
        f"Condition: {entry['condition'] or 'n/a'}",
        "",
        entry["note"],
    ]
    if entry["evidence_paths"]:
        block.append("")
        block.append("Evidence:")
        block.extend([f"- {item}" for item in entry["evidence_paths"]])

    with summary_md.open("a", encoding="utf-8") as f:
        f.write("\n".join(block) + "\n")

    print(f"Appended summary log entry: {summary_log}")
    print(f"Updated markdown summary: {summary_md}")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    """Validate and expand a study file into a deterministic run plan."""
    study_path = resolve_study_definition_path(Path(args.study).resolve())
    study_data = load_yaml(study_path)
    run_specs, eval_specs, study = expand_runs(study_path, study_data)
    run_specs = filter_run_specs(
        run_specs,
        args.only_hypothesis,
        args.only_condition,
        args.only_sub_experiment,
        args.only_seed,
        getattr(args, "only_run_id", None),
    )

    print(f"Study: {study['name']}")
    print(f"Schema version: {SCHEMA_VERSION}")
    print(f"Global evaluators: {len(eval_specs)}")
    print(f"Total expanded runs: {len(run_specs)}")
    for line in preflight_summary(run_specs):
        print(line)

    rows = plan_rows(run_specs)
    if args.output:
        output = Path(args.output).resolve()
        write_json(output, {"schema_version": SCHEMA_VERSION, "plan": rows})
        print(f"Wrote plan JSON: {output}")

    for row in rows[: min(len(rows), PLAN_PREVIEW_ROWS)]:
        print(
            "- "
            f"{row['run_id']} mode={row['mode']} scenario={row['scenario']} seed={row['seed']} evaluators={row['evaluators']}"
        )
    if len(rows) > PLAN_PREVIEW_ROWS:
        print(f"... and {len(rows) - PLAN_PREVIEW_ROWS} more")
    return 0


def cmd_generate_bash(args: argparse.Namespace) -> int:
    """Render all runnable study commands into a portable bash script."""
    study_path = resolve_study_definition_path(Path(args.study).resolve())
    repo_root = Path(args.repo_root).resolve()
    study_data = load_yaml(study_path)
    run_specs, _, study = expand_runs(study_path, study_data)
    run_specs = filter_run_specs(
        run_specs,
        args.only_hypothesis,
        args.only_condition,
        args.only_sub_experiment,
        args.only_seed,
        getattr(args, "only_run_id", None),
    )

    out = Path(args.output).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    content = render_bash_script(run_specs, repo_root)
    out.write_text(content, encoding="utf-8")
    os.chmod(out, 0o755)

    print(f"Generated bash script for study '{study['name']}': {out}")
    print(f"Commands: {sum(1 for s in run_specs if s.execution_mode == 'run')}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:  # noqa: PLR0915
    """Execute the expanded study plan and write reproducibility artifacts."""
    study_path = resolve_study_definition_path(Path(args.study).resolve())
    repo_root = Path(args.repo_root).resolve()

    study_data = load_yaml(study_path)
    run_specs, eval_specs, study = expand_runs(study_path, study_data)
    run_specs = filter_run_specs(
        run_specs,
        args.only_hypothesis,
        args.only_condition,
        args.only_sub_experiment,
        args.only_seed,
        getattr(args, "only_run_id", None),
    )

    generated_dir = study_generated_dir(repo_root, study)
    generated_dir.mkdir(parents=True, exist_ok=True)

    lock_jsonl = generated_dir / "repro_lock.jsonl"
    lock_json = generated_dir / "repro_lock.json"
    study_index = generated_dir / "study_index.json"
    enriched_yaml = generated_dir / "study_enriched.yaml"

    bash_out = generated_dir / "run_study.sh"
    bash_out.write_text(render_bash_script(run_specs, repo_root), encoding="utf-8")
    os.chmod(bash_out, 0o755)

    max_concurrent = int(args.max_concurrent)
    timeout_seconds = int(args.timeout_seconds) if args.timeout_seconds > 0 else None
    gpu_ids = resolve_gpu_ids_for_run()

    pending_specs, skipped_records = partition_completed_runs(
        run_specs, repo_root, generated_dir, force=bool(args.force)
    )

    gpu_bindings: dict[str, str] = {}
    if gpu_ids and max_concurrent > 1:
        shuffled = list(pending_specs)
        random.shuffle(shuffled)
        for idx, spec in enumerate(shuffled):
            gpu_bindings[spec.run_id] = gpu_ids[idx % len(gpu_ids)]

    provenance = _environment_provenance(repo_root)
    print(f"Study: {study['name']}")
    print(f"Schema version: {SCHEMA_VERSION}")
    print(f"Git commit: {provenance.get('git_commit') or 'unknown'}")
    if provenance.get("git_dirty"):
        print("⚠ Working tree has uncommitted changes; repro lock records git_dirty=true")
    print(f"Expanded runs: {len(run_specs)}")
    if skipped_records:
        print(f"Already complete (will skip): {len(skipped_records)}")
    for line in preflight_summary(pending_specs):
        print(line)
    print(f"Global evaluators: {[e.eval_id for e in eval_specs]}")
    print(f"Max concurrency: {max_concurrent}")
    if gpu_bindings:
        print(
            "GPU distribution: enabled (randomized round-robin) "
            f"across CUDA_VISIBLE_DEVICES={','.join(gpu_ids)}"
        )
    elif gpu_ids:
        print(f"GPU distribution: single-worker mode; CUDA_VISIBLE_DEVICES={','.join(gpu_ids)}")
    else:
        print("GPU distribution: disabled (no RUN_STUDY_GPU_IDS/CUDA_VISIBLE_DEVICES set)")
    print(f"Generated artifacts directory: {generated_dir}")

    if args.dry_run:
        rows = plan_rows(run_specs)
        write_json(generated_dir / "plan.json", {"schema_version": SCHEMA_VERSION, "plan": rows})
        print("Dry-run only. Wrote plan and bash script.")
        return 0

    if not _confirm_run_count(len(pending_specs), bool(args.yes)):
        return 2

    records = execute_pending_runs(
        pending_specs,
        skipped_records,
        repo_root,
        generated_dir,
        timeout_seconds,
        max_concurrent,
        gpu_bindings,
        lock_jsonl,
    )

    records.sort(key=lambda r: str(r.get("run_id", "")))
    write_json(
        lock_json,
        {
            "schema_version": SCHEMA_VERSION,
            "environment": provenance,
            "records": records,
        },
    )
    write_study_index(study_index, study_data, records)
    write_yaml(enriched_yaml, enrich_study_with_results(study_data, records))

    success = sum(
        1 for r in records if r.get("status") in {"success", "reused", "skipped_complete"}
    )
    failed = sum(1 for r in records if r.get("status") in {"failed", "timeout"})
    print("Run complete")
    print(f"Success/reused: {success}")
    print(f"Failed/timeout: {failed}")
    print(f"Skipped {len(skipped_records)} already-complete runs (use --force to re-run)")
    print(f"Repro lock JSONL: {lock_jsonl}")
    print(f"Repro lock JSON: {lock_json}")
    print(f"Study index JSON: {study_index}")
    print(f"Enriched study YAML: {enriched_yaml}")

    organized_dir = organize_study_outputs(
        repo_root,
        study_data,
        records,
        dry_run=args.dry_run,
    )
    print(f"Organized study tree: {organized_dir}")

    return 1 if failed else 0


def cmd_organize(args: argparse.Namespace) -> int:
    """Rebuild organized study artifacts from existing reproducibility records."""
    study_path = resolve_study_definition_path(Path(args.study).resolve())
    repo_root = Path(args.repo_root).resolve()

    study_data = load_yaml(study_path)
    generated_dir = study_generated_dir(repo_root, ensure_mapping("study", study_data.get("study")))
    lock_json = generated_dir / "repro_lock.json"

    if not lock_json.is_file():
        raise StudyConfigError(f"Missing repro_lock.json: {lock_json}")

    with lock_json.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    records = payload.get("records", [])
    if not isinstance(records, list):
        raise StudyConfigError(f"Invalid repro_lock.json records payload: {lock_json}")

    organized_dir = organize_study_outputs(
        repo_root,
        study_data,
        records,
        dry_run=args.dry_run,
        clean=not args.keep_existing,
    )
    print(f"Organized study tree: {organized_dir}")
    return 0


def cmd_slurm_array(args: argparse.Namespace) -> int:
    """Generate (and optionally submit) a Slurm sbatch command from filtered study runs."""
    study_path = resolve_study_definition_path(Path(args.study).resolve())
    repo_root = Path(args.repo_root).resolve()
    base_script = Path(args.base_script).resolve()

    if not base_script.is_file():
        raise StudyConfigError(f"Base script not found: {base_script}")

    study_data = load_yaml(study_path)
    run_specs, _, _ = expand_runs(study_path, study_data)
    run_specs = filter_run_specs(
        run_specs,
        args.only_hypothesis,
        args.only_condition,
        args.only_sub_experiment,
        args.only_seed,
        getattr(args, "only_run_id", None),
    )

    if not run_specs:
        print("No runs matched filters; nothing to submit.")
        return 0

    array_mode = str(args.array_mode).strip().lower()
    total_tasks = count_array_tasks(run_specs, array_mode)
    array_spec = f"0-{total_tasks - 1}"

    study_rel = os.path.relpath(study_path, repo_root)
    plan_json = (
        repo_root / "logs" / f"study_plan_{Path(study_rel).stem}_{array_mode}_{now_iso()}.json"
    )
    plan_json.parent.mkdir(parents=True, exist_ok=True)
    write_json(plan_json, {"schema_version": SCHEMA_VERSION, "plan": plan_rows(run_specs)})

    export_parts = {
        "REPO_ROOT": str(repo_root),
        "STUDY_FILE": study_rel,
        "PLAN_JSON": str(plan_json),
        "ARRAY_MODE": array_mode,
        "HYPOTHESIS_IDS": csv_compact(args.only_hypothesis),
        "CONDITION_IDS": csv_compact(args.only_condition),
        "SUB_EXPERIMENT_IDS": csv_compact(args.only_sub_experiment),
        "SEED_IDS": csv_compact(args.only_seed),
        "RUN_IDS": csv_compact(args.only_run_id),
        "MAX_CONCURRENT": str(int(args.max_concurrent)),
        "RUNNER_PYTHON": str(args.runner_python),
        "SILISOCS_HPC_SETUP_COMMAND": str(args.setup_command or ""),
        "SILISOCS_HPC_SERVER_COMMAND": str(args.server_command or ""),
        "SILISOCS_HPC_SERVER_READY_URL": str(args.server_ready_url or ""),
        "SILISOCS_HPC_SERVER_TIMEOUT_SECONDS": str(int(args.server_timeout_seconds)),
    }
    export_arg = ",".join(f"{k}={v}" for k, v in export_parts.items())

    sbatch_cmd = [
        "sbatch",
        f"--array={array_spec}",
        f"--export={export_arg}",
        str(base_script),
    ]

    print(f"Array mode: {array_mode}")
    print(f"Matched expanded runs: {len(run_specs)}")
    print(f"Array tasks: {total_tasks}")
    print(f"Array spec: {array_spec}")
    print(f"Plan JSON: {plan_json}")
    print("Prepared sbatch command:")
    print(" ".join(shlex.quote(part) for part in sbatch_cmd))

    if not args.submit:
        print("Dry mode: add --submit to dispatch the job.")
        return 0

    result = subprocess.run(
        sbatch_cmd,
        cwd=str(repo_root),
        check=False,
        text=True,
        capture_output=True,
    )
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.returncode != 0:
        if result.stderr.strip():
            print(result.stderr.strip(), file=sys.stderr)
    return int(result.returncode)


def _submitit_parameters(args: argparse.Namespace) -> dict[str, Any]:
    params: dict[str, Any] = {
        "name": str(args.job_name),
        "timeout_min": int(args.timeout_minutes),
        "cpus_per_task": int(args.cpus_per_task),
    }
    if args.nodes:
        params["nodes"] = int(args.nodes)
    if args.tasks_per_node:
        params["tasks_per_node"] = int(args.tasks_per_node)
    if args.gpus_per_node:
        params["gpus_per_node"] = int(args.gpus_per_node)
    if args.mem_gb:
        params["mem_gb"] = int(args.mem_gb)
    if args.partition:
        params["slurm_partition"] = str(args.partition)
    if args.account:
        params["slurm_account"] = str(args.account)
    if args.constraint:
        params["slurm_constraint"] = str(args.constraint)
    if args.slurm_comment:
        params["slurm_comment"] = str(args.slurm_comment)
    return params


def cmd_submitit(args: argparse.Namespace) -> int:
    """Submit study run groups with Submitit while preserving study-runner semantics."""
    study_path = resolve_study_definition_path(Path(args.study).resolve())
    repo_root = Path(args.repo_root).resolve()
    study_data = load_yaml(study_path)
    run_specs, _, study = expand_runs(study_path, study_data)
    run_specs = filter_run_specs(
        run_specs,
        args.only_hypothesis,
        args.only_condition,
        args.only_sub_experiment,
        args.only_seed,
        args.only_run_id,
    )
    if not run_specs:
        print("No runs matched filters; nothing to submit.")
        return 0

    array_mode = str(args.array_mode).strip().lower()
    groups = submitit_group_filters(run_specs, array_mode)
    commands = build_submitit_job_commands(
        study_path=study_path,
        repo_root=repo_root,
        groups=groups,
        max_concurrent=int(args.max_concurrent),
        timeout_seconds=int(args.timeout_seconds),
    )

    print(f"Study: {study['name']}")
    print(f"Matched expanded runs: {len(run_specs)}")
    print(f"Submitit groups ({array_mode}): {len(commands)}")
    for command in commands[: min(len(commands), PLAN_PREVIEW_ROWS)]:
        print(" ".join(shlex.quote(part) for part in command))
    if len(commands) > PLAN_PREVIEW_ROWS:
        print(f"... and {len(commands) - PLAN_PREVIEW_ROWS} more")
    if args.dry_run:
        print("Dry mode: no Submitit jobs were submitted.")
        return 0

    try:
        import submitit
    except ImportError as e:  # pragma: no cover - exercised when optional extra missing
        raise StudyConfigError(
            "Submitit support requires the optional hpc dependencies. "
            "Install with `uv sync --extra hpc` or `pip install silisocs[hpc]`."
        ) from e

    folder = Path(args.folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    executor = submitit.AutoExecutor(folder=str(folder))
    executor.update_parameters(**_submitit_parameters(args))
    jobs = [
        executor.submit(
            run_command_with_optional_hooks,
            command,
            str(repo_root),
            args.setup_command,
            args.server_command,
            args.server_ready_url,
            int(args.server_timeout_seconds),
        )
        for command in commands
    ]
    print(f"Submitted {len(jobs)} Submitit jobs")
    for job in jobs:
        print(f"- {job.job_id}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build parser for planning, generation, and execution commands."""
    parser = argparse.ArgumentParser(description="Structured study runner for silisocs")
    parser.add_argument(
        "--study",
        required=True,
        help="Path to a study directory or study.yaml file",
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root used as execution cwd (default: .)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_plan = sub.add_parser("plan", help="Validate and expand study into concrete runs")
    p_plan.add_argument("--output", help="Optional output JSON path for expanded plan")
    p_plan.add_argument(
        "--only-hypothesis",
        default=None,
        help="Optional comma-separated hypothesis IDs to include",
    )
    p_plan.add_argument(
        "--only-condition",
        default=None,
        help="Optional comma-separated condition IDs to include",
    )
    p_plan.add_argument(
        "--only-sub-experiment",
        default=None,
        help="Optional comma-separated sub_experiment labels to include",
    )
    p_plan.add_argument(
        "--only-seed",
        default=None,
        help="Optional comma-separated seed values to include",
    )
    p_plan.add_argument(
        "--only-run-id",
        default=None,
        help="Optional comma-separated expanded run IDs to include",
    )
    p_plan.set_defaults(func=cmd_plan)

    p_bash = sub.add_parser("generate-bash", help="Generate runnable bash script")
    p_bash.add_argument("--output", required=True, help="Output path for generated bash script")
    p_bash.add_argument(
        "--only-hypothesis",
        default=None,
        help="Optional comma-separated hypothesis IDs to include",
    )
    p_bash.add_argument(
        "--only-condition",
        default=None,
        help="Optional comma-separated condition IDs to include",
    )
    p_bash.add_argument(
        "--only-sub-experiment",
        default=None,
        help="Optional comma-separated sub_experiment labels to include",
    )
    p_bash.add_argument(
        "--only-seed",
        default=None,
        help="Optional comma-separated seed values to include",
    )
    p_bash.add_argument(
        "--only-run-id",
        default=None,
        help="Optional comma-separated expanded run IDs to include",
    )
    p_bash.set_defaults(func=cmd_generate_bash)

    p_run = sub.add_parser("run", help="Execute study with optional eval hooks")
    p_run.add_argument("--max-concurrent", type=int, default=1, help="Maximum concurrent runs")
    p_run.add_argument(
        "--timeout-seconds",
        type=int,
        default=0,
        help="Per subprocess timeout (0 disables timeout)",
    )
    p_run.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not execute, only write generated plan and bash script",
    )
    p_run.add_argument(
        "--force",
        action="store_true",
        help="Re-run all runs even when RUN_COMPLETE.json markers exist",
    )
    p_run.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Skip the preflight confirmation when more than "
            f"{PREFLIGHT_CONFIRM_RUN_COUNT} runs would launch"
        ),
    )
    p_run.add_argument(
        "--only-hypothesis",
        default=None,
        help="Optional comma-separated hypothesis IDs to include",
    )
    p_run.add_argument(
        "--only-condition",
        default=None,
        help="Optional comma-separated condition IDs to include",
    )
    p_run.add_argument(
        "--only-sub-experiment",
        default=None,
        help="Optional comma-separated sub_experiment labels to include",
    )
    p_run.add_argument(
        "--only-seed",
        default=None,
        help="Optional comma-separated seed values to include",
    )
    p_run.add_argument(
        "--only-run-id",
        default=None,
        help="Optional comma-separated expanded run IDs to include",
    )
    p_run.set_defaults(func=cmd_run)

    p_org = sub.add_parser("organize", help="Rebuild organized study artifacts")
    p_org.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write files, only validate and resolve paths",
    )
    p_org.add_argument(
        "--keep-existing",
        action="store_true",
        help="Do not clean the organized output directory before rebuilding",
    )
    p_org.set_defaults(func=cmd_organize)

    p_slurm = sub.add_parser(
        "slurm-array",
        help="Build Slurm sbatch command from study filters (optionally submit)",
    )
    p_slurm.add_argument(
        "--base-script",
        required=True,
        help="Base Slurm script path (required)",
    )
    p_slurm.add_argument(
        "--array-mode",
        choices=["case", "seed", "hypothesis", "run"],
        default="case",
        help="Array granularity: case(default), seed, hypothesis, or run",
    )
    p_slurm.add_argument(
        "--max-concurrent",
        type=int,
        default=8,
        help="run_study --max-concurrent passed to each array task",
    )
    p_slurm.add_argument(
        "--runner-python",
        default=sys.executable,
        help="Python executable used by the generic array template",
    )
    p_slurm.add_argument(
        "--only-hypothesis",
        default=None,
        help="Optional comma-separated hypothesis IDs to include",
    )
    p_slurm.add_argument(
        "--only-condition",
        default=None,
        help="Optional comma-separated condition IDs to include",
    )
    p_slurm.add_argument(
        "--only-sub-experiment",
        default=None,
        help="Optional comma-separated sub_experiment labels to include",
    )
    p_slurm.add_argument(
        "--only-seed",
        default=None,
        help="Optional comma-separated seed values to include",
    )
    p_slurm.add_argument(
        "--only-run-id",
        default=None,
        help="Optional comma-separated expanded run IDs to include",
    )
    p_slurm.add_argument(
        "--submit",
        action="store_true",
        help="Submit to Slurm via sbatch. Otherwise prints command only.",
    )
    p_slurm.add_argument(
        "--setup-command",
        default=None,
        help="Optional user-owned shell command run before each array task",
    )
    p_slurm.add_argument(
        "--server-command",
        default=None,
        help="Optional user-owned long-running server command started before each array task",
    )
    p_slurm.add_argument(
        "--server-ready-url",
        default=None,
        help="Optional URL polled before running each array task",
    )
    p_slurm.add_argument("--server-timeout-seconds", type=int, default=600)
    p_slurm.set_defaults(func=cmd_slurm_array)

    p_submitit = sub.add_parser(
        "submitit",
        help="Submit study run groups with optional hpc dependencies",
    )
    p_submitit.add_argument(
        "--array-mode",
        choices=["case", "seed", "hypothesis", "run"],
        default="case",
        help="Submission granularity: case(default), seed, hypothesis, or run",
    )
    p_submitit.add_argument(
        "--folder",
        default="logs/submitit",
        help="Submitit log/checkpoint folder",
    )
    p_submitit.add_argument(
        "--max-concurrent",
        type=int,
        default=1,
        help="run_study --max-concurrent passed inside each submitted group",
    )
    p_submitit.add_argument(
        "--timeout-seconds",
        type=int,
        default=0,
        help="Per subprocess timeout passed inside each submitted group",
    )
    p_submitit.add_argument("--job-name", default="silisocs-study")
    p_submitit.add_argument("--timeout-minutes", type=int, default=240)
    p_submitit.add_argument("--cpus-per-task", type=int, default=4)
    p_submitit.add_argument("--nodes", type=int, default=1)
    p_submitit.add_argument("--tasks-per-node", type=int, default=1)
    p_submitit.add_argument("--gpus-per-node", type=int, default=0)
    p_submitit.add_argument("--mem-gb", type=int, default=0)
    p_submitit.add_argument("--partition", default=None)
    p_submitit.add_argument("--account", default=None)
    p_submitit.add_argument("--constraint", default=None)
    p_submitit.add_argument("--slurm-comment", default=None)
    p_submitit.add_argument(
        "--setup-command",
        default=None,
        help="Optional user-owned shell command run before each submitted group",
    )
    p_submitit.add_argument(
        "--server-command",
        default=None,
        help="Optional user-owned long-running server command started for each submitted group",
    )
    p_submitit.add_argument(
        "--server-ready-url",
        default=None,
        help="Optional URL polled before running the submitted group",
    )
    p_submitit.add_argument("--server-timeout-seconds", type=int, default=600)
    p_submitit.add_argument("--only-hypothesis", default=None)
    p_submitit.add_argument("--only-condition", default=None)
    p_submitit.add_argument("--only-sub-experiment", default=None)
    p_submitit.add_argument("--only-seed", default=None)
    p_submitit.add_argument("--only-run-id", default=None)
    p_submitit.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned Submitit commands without submitting jobs",
    )
    p_submitit.set_defaults(func=cmd_submitit)

    p_summary = sub.add_parser("summary-append", help="Append a study summary entry")
    p_summary.add_argument("--author", required=True, help="Author label for this summary entry")
    p_summary.add_argument("--note", required=True, help="Summary note text")
    p_summary.add_argument("--hypothesis", default=None, help="Optional hypothesis ID")
    p_summary.add_argument("--condition", default=None, help="Optional condition ID")
    p_summary.add_argument(
        "--evidence",
        action="append",
        default=[],
        help="Evidence path; can be provided multiple times",
    )
    p_summary.set_defaults(func=cmd_summary_append)

    return parser


def main() -> int:
    """Entry point for the study orchestration CLI."""
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except StudyConfigError as e:
        print(f"Study configuration error: {e}", file=sys.stderr)
        return 2
