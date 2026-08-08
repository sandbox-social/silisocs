"""Execution layer for studies: launch runs, evaluate them, emit artifacts.

Everything with a side effect lives here: spawning the runner subprocess for one
expanded :class:`~silisocs.studies.study_types.RunSpec` (with output-dir
sniffing and timeout kill), the resume/skip logic built on ``RUN_COMPLETE.json``
markers, per-run evaluator invocation, the bounded-concurrency pool that drives
the pending specs, and the JSON/JSONL/YAML writers for the reproducibility lock
and study index.

Depends on :mod:`silisocs.studies.plan` for the pure command/path resolution and
carries no argparse dependency; the CLI verbs live in
:mod:`silisocs.studies.cli`.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import threading
import time
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import yaml

from silisocs.runtime.checkpointing import resolve_checkpoint_source
from silisocs.runtime.provenance import hash_file as _hash_file
from silisocs.studies.plan import _build_run_command, _planned_run_dir
from silisocs.studies.study_types import (
    SCHEMA_VERSION,
    EvalSpec,
    RunSpec,
    ensure_mapping,
    format_command_template,
    now_iso,
    resolve_study_id,
)

PROCESS_TIMEOUT_RC = 124
RUN_COMPLETE_MARKER = "RUN_COMPLETE.json"


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=False)


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=False)


def _write_jsonl_line(path: Path, obj: Any, lock: threading.Lock | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(obj, sort_keys=False)
    if lock is None:
        with path.open("a", encoding="utf-8") as f:
            f.write(payload + "\n")
        return
    with lock, path.open("a", encoding="utf-8") as f:
        f.write(payload + "\n")


def _extract_output_dir_from_line(line: str) -> str | None:
    line = line.strip()
    if line.startswith("Output directory:"):
        return line.split("Output directory:", 1)[1].strip()
    marker = "output_dir="
    if marker in line:
        return line.split(marker, 1)[1].strip().split()[0]
    return None


def _find_latest_checkpoint(run_dir: Path) -> Path | None:
    # Defer to the canonical runtime resolver so study eval picks the exact same
    # checkpoint the runtime restore would (highest parsed step, step_*_checkpoint.json
    # only). It raises FileNotFoundError / ValueError for no-usable / unparseable
    # checkpoints; translate both to this function's "None" contract.
    try:
        return resolve_checkpoint_source(run_dir)
    except (FileNotFoundError, ValueError):
        return None


def _run_subprocess(
    command: list[str],
    cwd: Path,
    log_path: Path,
    timeout_seconds: int | None,
    extra_env: dict[str, str] | None = None,
) -> tuple[int, list[str], str | None]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    output_tail: deque[str] = deque(maxlen=40)
    parsed_output_dir: str | None = None

    with log_path.open("w", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            command,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=(dict(os.environ) | dict(extra_env)) if extra_env else None,
        )

        assert proc.stdout is not None

        # Drain stdout on a separate thread so the timeout is enforced via
        # proc.wait(timeout=...) below even when the child produces NO output: a
        # blocking `for line in proc.stdout` only returns at EOF, so a hang that
        # holds stdout open (deadlock, stuck network/LLM call) would otherwise
        # never reach the timeout and run forever.
        def _drain_stdout() -> None:
            nonlocal parsed_output_dir
            assert proc.stdout is not None
            for line in proc.stdout:
                log_file.write(line)
                output_tail.append(line.rstrip("\n"))
                out_dir = _extract_output_dir_from_line(line)
                if out_dir:
                    parsed_output_dir = out_dir

        reader = threading.Thread(target=_drain_stdout, daemon=True)
        reader.start()
        try:
            return_code = proc.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            output_tail.append(f"TIMEOUT after {timeout_seconds}s")
            return_code = PROCESS_TIMEOUT_RC
        # Let the drain thread observe EOF (the pipe closes once the process
        # exits / is killed) and flush remaining output before the log closes.
        reader.join(timeout=5)

    return return_code, list(output_tail), parsed_output_dir


def _enrich_study_with_results(
    study_data: dict[str, Any], records: list[dict[str, Any]]
) -> dict[str, Any]:
    enriched = copy.deepcopy(study_data)
    generated = {
        "generated_at": now_iso(),
        "schema_version": SCHEMA_VERSION,
        "records": records,
    }
    enriched["generated"] = generated
    return enriched


def _write_study_index(
    path: Path, study_data: dict[str, Any], records: list[dict[str, Any]]
) -> None:
    study = ensure_mapping("study", study_data.get("study"))
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "study": {
            "name": study.get("name"),
            "study_id": resolve_study_id(study),
            "study_version": study.get("study_version"),
            "parent_studies": study.get("parent_studies", []),
            "derived_from_runs": study.get("derived_from_runs", []),
        },
        "records": records,
    }
    _write_json(path, payload)


def _resolve_eval_command(
    eval_spec: EvalSpec,
    run_dir: Path,
    eval_output: Path,
    context: dict[str, Any],
) -> list[str]:
    cmd = list(format_command_template(eval_spec.command, context))
    cmd.extend(format_command_template(eval_spec.static_args, context))

    if not eval_spec.manage_io_args:
        return cmd

    if eval_spec.input_mode == "run_dir":
        cmd.extend([eval_spec.run_dir_arg, str(run_dir)])
    else:
        checkpoint = _find_latest_checkpoint(run_dir)
        explicit_map: dict[str, Path | None] = {
            "checkpoint": checkpoint,
            "effective_config": run_dir / "effective_config.yaml",
            "metrics": run_dir / "sim_metrics.json",
            "prompts": run_dir / "prompts_and_responses.jsonl",
            "run_stats": run_dir / "run_stats.log",
        }
        for key, path in explicit_map.items():
            if path is not None and Path(path).is_file():
                cmd.extend([eval_spec.explicit_args[key], str(path)])
        if eval_spec.include_run_dir_in_explicit:
            cmd.extend([eval_spec.run_dir_arg, str(run_dir)])

    cmd.extend([eval_spec.output_arg, str(eval_output)])
    return cmd


def _run_evaluations(
    spec: RunSpec,
    run_dir: Path,
    repo_root: Path,
    generated_dir: Path,
    timeout_seconds: int | None,
    extra_env: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    for eval_spec in spec.eval_specs:
        eval_output = (
            generated_dir
            / "eval"
            / spec.hypothesis_id
            / spec.condition_id
            / spec.scenario
            / f"seed_{spec.seed}"
            / eval_spec.eval_id
            / eval_spec.output_subpath
        )
        eval_log = eval_output.with_suffix(eval_output.suffix + ".log")
        eval_context = {
            "run_id": spec.run_id,
            "study_name": spec.study_name,
            "study_id": spec.study_id,
            "hypothesis_id": spec.hypothesis_id,
            "condition_id": spec.condition_id,
            "scenario": spec.scenario,
            "seed": spec.seed,
            "run_dir": str(run_dir),
            "output_path": str(eval_output),
        }

        if not eval_spec.enabled:
            records.append(
                {
                    "id": eval_spec.eval_id,
                    "status": "disabled",
                    "path": str(eval_output),
                    "command": [],
                    "log_path": str(eval_log),
                    "return_code": None,
                    "tail": [],
                }
            )
            continue

        cmd = _resolve_eval_command(eval_spec, run_dir, eval_output, eval_context)
        rc, tail, _ = _run_subprocess(
            cmd,
            repo_root,
            eval_log,
            timeout_seconds,
            extra_env=extra_env,
        )
        records.append(
            {
                "id": eval_spec.eval_id,
                "status": "success" if rc == 0 else "failed",
                "path": str(eval_output),
                "command": cmd,
                "log_path": str(eval_log),
                "return_code": rc,
                "tail": tail,
            }
        )

    return records


def _build_eval_paths(evaluations: list[dict[str, Any]]) -> dict[str, str]:
    """Map evaluation id -> output path for evaluations that produced one."""
    return {str(item.get("id")): str(item.get("path")) for item in evaluations if item.get("path")}


def _initialize_run_record(spec: RunSpec, assigned_gpu: str | None, started: str) -> dict[str, Any]:
    """Build the base run record shared by the reuse and new-run paths."""
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": spec.run_id,
        "study": spec.study_name,
        "hypothesis": spec.hypothesis_id,
        "condition": spec.condition_id,
        "sub_experiment": spec.sub_experiment,
        "scenario": spec.scenario,
        "seed": spec.seed,
        "execution_mode": spec.execution_mode,
        "started_at": started,
        "status": "pending",
        "resolved_overrides": spec.overrides,
        "study_id": spec.study_id,
        "run_name": spec.run_name,
        "planned_output_rootname": spec.output_rootname,
        "command": None,
        "log_path": None,
        "run_dir": None,
        "simulation_output_path": None,
        "evaluations": [],
        "eval_paths": {},
        "reused": {
            "source": spec.reused_source,
            "eval": spec.reused_eval,
        },
        "lock": {
            "effective_config_sha256": None,
            "effective_config_path": None,
        },
        "gpu_binding": assigned_gpu,
    }


def _run_reused_spec(
    spec: RunSpec,
    record: dict[str, Any],
    repo_root: Path,
    generated_dir: Path,
    timeout_seconds: int | None,
    exec_env: dict[str, str] | None,
) -> dict[str, Any]:
    """Resolve (and optionally re-evaluate) an existing run referenced by the spec."""
    reused_source = Path(spec.reused_source or "")
    if not reused_source.is_absolute():
        reused_source = (repo_root / reused_source).resolve()
    record["run_dir"] = str(reused_source)
    record["simulation_output_path"] = str(reused_source)
    record["status"] = "reused"

    if spec.reused_eval:
        eval_path = Path(spec.reused_eval)
        if not eval_path.is_absolute():
            eval_path = (repo_root / eval_path).resolve()
        record["evaluations"].append(
            {
                "id": "reused_eval",
                "status": "reused",
                "path": str(eval_path),
                "command": [],
                "log_path": None,
                "return_code": None,
                "tail": [],
            }
        )
        record["eval_paths"]["reused_eval"] = str(eval_path)

    if spec.eval_specs and spec.re_evaluate:
        record["evaluations"] = _run_evaluations(
            spec,
            reused_source,
            repo_root,
            generated_dir,
            timeout_seconds,
            extra_env=exec_env,
        )
        record["eval_paths"] = _build_eval_paths(record["evaluations"])

    record["finished_at"] = now_iso()
    return record


def _run_new_spec(
    spec: RunSpec,
    record: dict[str, Any],
    repo_root: Path,
    generated_dir: Path,
    timeout_seconds: int | None,
    exec_env: dict[str, str] | None,
) -> dict[str, Any]:
    """Execute a fresh simulation run, resolve its output dir, and run evaluations."""
    run_log = generated_dir / "logs" / f"{spec.run_id}.log"
    cmd = _build_run_command(spec)
    record["command"] = cmd
    record["log_path"] = str(run_log)

    run_cwd = Path(spec.working_directory).expanduser() if spec.working_directory else repo_root
    if not run_cwd.is_absolute():
        run_cwd = repo_root / run_cwd
    rc, tail, run_dir = _run_subprocess(
        cmd,
        run_cwd.resolve(),
        run_log,
        timeout_seconds,
        extra_env=exec_env,
    )
    record["return_code"] = rc
    record["tail"] = tail

    run_dir_path: Path | None = None
    if run_dir:
        run_dir_path = Path(run_dir)
        if not run_dir_path.is_absolute():
            run_dir_path = (repo_root / run_dir_path).resolve()
    elif spec.output_rootname:
        fallback = Path(spec.output_rootname)
        if not fallback.is_absolute():
            fallback = (repo_root / fallback).resolve()
        if fallback.exists():
            run_dir_path = fallback

    if rc == 0 and run_dir_path is not None:
        record["status"] = "success"
        record["run_dir"] = str(run_dir_path)
        record["simulation_output_path"] = str(run_dir_path)

        effective_cfg = run_dir_path / "effective_config.yaml"
        record["lock"]["effective_config_path"] = str(effective_cfg)
        record["lock"]["effective_config_sha256"] = _hash_file(effective_cfg)

        if spec.eval_specs:
            record["evaluations"] = _run_evaluations(
                spec,
                run_dir_path,
                repo_root,
                generated_dir,
                timeout_seconds,
                extra_env=exec_env,
            )
            record["eval_paths"] = _build_eval_paths(record["evaluations"])
        else:
            record["evaluations"] = []

        # Idempotent-resume marker: a later `run` invocation skips this run
        # unless --force is given.
        _write_json(
            run_dir_path / RUN_COMPLETE_MARKER,
            {
                "run_id": spec.run_id,
                "finished_at": now_iso(),
                "effective_config_sha256": record["lock"]["effective_config_sha256"],
                "return_code": rc,
            },
        )
    else:
        record["status"] = "failed" if rc != PROCESS_TIMEOUT_RC else "timeout"

    record["finished_at"] = now_iso()
    return record


def _run_one_spec(
    spec: RunSpec,
    repo_root: Path,
    generated_dir: Path,
    timeout_seconds: int | None,
    assigned_gpu: str | None = None,
) -> dict[str, Any]:
    record = _initialize_run_record(spec, assigned_gpu, now_iso())
    exec_env = {"CUDA_VISIBLE_DEVICES": assigned_gpu} if assigned_gpu else None
    if spec.execution_mode == "reuse_existing":
        return _run_reused_spec(spec, record, repo_root, generated_dir, timeout_seconds, exec_env)
    return _run_new_spec(spec, record, repo_root, generated_dir, timeout_seconds, exec_env)


def _load_complete_marker(run_dir: Path) -> dict[str, Any] | None:
    marker_path = run_dir / RUN_COMPLETE_MARKER
    if not marker_path.is_file():
        return None
    try:
        with marker_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _skipped_complete_record(spec: RunSpec, run_dir: Path, generated_dir: Path) -> dict[str, Any]:
    """Build a repro record for a run skipped because RUN_COMPLETE.json exists."""
    now = now_iso()
    effective_cfg = run_dir / "effective_config.yaml"
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": spec.run_id,
        "study": spec.study_name,
        "hypothesis": spec.hypothesis_id,
        "condition": spec.condition_id,
        "sub_experiment": spec.sub_experiment,
        "scenario": spec.scenario,
        "seed": spec.seed,
        "execution_mode": spec.execution_mode,
        "started_at": now,
        "finished_at": now,
        "status": "skipped_complete",
        "resolved_overrides": spec.overrides,
        "study_id": spec.study_id,
        "run_name": spec.run_name,
        "planned_output_rootname": spec.output_rootname,
        "command": None,
        "log_path": None,
        "run_dir": str(run_dir),
        "simulation_output_path": str(run_dir),
        "evaluations": [],
        "eval_paths": {},
        "reused": {"source": None, "eval": None},
        "lock": {
            "effective_config_sha256": _hash_file(effective_cfg),
            "effective_config_path": str(effective_cfg),
        },
        "gpu_binding": None,
        "complete_marker": _load_complete_marker(run_dir),
    }

    # Relink prior evaluator outputs (deterministic paths) so the organized
    # tree keeps eval data for skipped runs.
    for eval_spec in spec.eval_specs:
        eval_output = (
            generated_dir
            / "eval"
            / spec.hypothesis_id
            / spec.condition_id
            / spec.scenario
            / f"seed_{spec.seed}"
            / eval_spec.eval_id
            / eval_spec.output_subpath
        )
        if not eval_output.is_file():
            continue
        record["evaluations"].append(
            {
                "id": eval_spec.eval_id,
                "status": "reused",
                "path": str(eval_output),
                "command": [],
                "log_path": None,
                "return_code": None,
                "tail": [],
            }
        )
        record["eval_paths"][eval_spec.eval_id] = str(eval_output)

    return record


def _partition_completed_runs(
    run_specs: list[RunSpec],
    repo_root: Path,
    generated_dir: Path,
    force: bool,
) -> tuple[list[RunSpec], list[dict[str, Any]]]:
    """Split specs into pending runs and records for already-complete runs."""
    if force:
        return list(run_specs), []

    pending: list[RunSpec] = []
    skipped: list[dict[str, Any]] = []
    for spec in run_specs:
        run_dir = _planned_run_dir(spec, repo_root)
        if (
            spec.execution_mode == "run"
            and run_dir is not None
            and (run_dir / RUN_COMPLETE_MARKER).is_file()
        ):
            skipped.append(_skipped_complete_record(spec, run_dir, generated_dir))
        else:
            pending.append(spec)
    return pending, skipped


def _execute_pending_runs(
    pending_specs: list[RunSpec],
    skipped_records: list[dict[str, Any]],
    repo_root: Path,
    generated_dir: Path,
    timeout_seconds: int | None,
    max_concurrent: int,
    gpu_bindings: dict[str, str],
    lock_jsonl: Path,
) -> list[dict[str, Any]]:
    """Run the pending specs concurrently, streaming each record to the lock JSONL.

    Already-complete (skipped) records are emitted first, then each pending spec is
    run via ``_run_one_spec`` in a bounded thread pool; a worker exception is turned
    into a failed record rather than aborting the study.
    """
    records: list[dict[str, Any]] = []
    write_lock = threading.Lock()

    for record in skipped_records:
        records.append(record)
        _write_jsonl_line(lock_jsonl, record, lock=write_lock)
        print(f"[{record.get('status', 'unknown'):>7}] {record.get('run_id')}")

    with ThreadPoolExecutor(max_workers=max_concurrent) as pool:
        future_map = {
            pool.submit(
                _run_one_spec,
                spec,
                repo_root,
                generated_dir,
                timeout_seconds,
                gpu_bindings.get(spec.run_id),
            ): spec
            for spec in pending_specs
        }
        for future in as_completed(future_map):
            spec = future_map[future]
            try:
                record = future.result()
            except Exception as e:  # pragma: no cover
                record = {
                    "schema_version": SCHEMA_VERSION,
                    "run_id": spec.run_id,
                    "study": spec.study_name,
                    "hypothesis": spec.hypothesis_id,
                    "condition": spec.condition_id,
                    "scenario": spec.scenario,
                    "seed": spec.seed,
                    "execution_mode": spec.execution_mode,
                    "status": "failed",
                    "error": str(e),
                    "finished_at": now_iso(),
                }
            records.append(record)
            _write_jsonl_line(lock_jsonl, record, lock=write_lock)
            print(f"[{record.get('status', 'unknown'):>7}] {record.get('run_id')}")

    return records


def _resolve_gpu_ids_for_run() -> list[str]:
    manual = os.environ.get("RUN_STUDY_GPU_IDS", "").strip()
    if manual:
        return [token.strip() for token in manual.split(",") if token.strip()]

    visible = os.environ.get("CUDA_VISIBLE_DEVICES", "").strip()
    if visible:
        return [token.strip() for token in visible.split(",") if token.strip()]

    return []


def _wait_for_ready_url(url: str, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if 200 <= int(response.status) < 500:
                    return
        except Exception as e:
            last_error = e
        time.sleep(2)
    detail = f": {last_error}" if last_error else ""
    raise RuntimeError(f"Server hook did not become ready at {url}{detail}")


def _run_command_with_optional_hooks(
    command: list[str],
    cwd: str,
    setup_command: str | None,
    server_command: str | None,
    server_ready_url: str | None,
    server_timeout_seconds: int,
) -> int:
    """Run one submitted study command with user-owned setup/server hooks."""
    server_proc: subprocess.Popen[str] | None = None
    try:
        if setup_command:
            subprocess.run(setup_command, cwd=cwd, shell=True, check=True, text=True)
        if server_command:
            server_proc = subprocess.Popen(
                server_command,
                cwd=cwd,
                shell=True,
                text=True,
                start_new_session=True,
            )
            if server_ready_url:
                _wait_for_ready_url(server_ready_url, server_timeout_seconds)
        result = subprocess.run(command, cwd=cwd, check=False, text=True)
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, command)
        return int(result.returncode)
    finally:
        if server_proc is not None and server_proc.poll() is None:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                server_proc.kill()
