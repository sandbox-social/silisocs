"""Persistent local control plane for Studio subprocess jobs."""

from __future__ import annotations

import json
import os
import signal
import socket
import sqlite3
import subprocess
import threading
import time
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from silisocs.evaluations.action_events import resolve_event_files
from silisocs.evaluations.run_artifact import load_run
from silisocs.runtime.execution.run_events import RUN_EVENTS_FILENAME

TERMINAL_STATUSES = frozenset({"finished", "failed", "killed", "orphaned"})
EVENT_STREAMS = ("action", "exposure", "probe", "harness")


def _tail_jsonl(path: Path, position: int) -> tuple[list[dict[str, Any]], int]:
    """Read the complete JSONL records appended to ``path`` past byte ``position``.

    The one incremental-tail implementation the SSE loop uses for every live log
    (per-stream event files and the runner's ``run_events.jsonl``): resume from
    the recorded byte offset, stop at the first partial line so a half-written
    record is re-read whole next tick, and restart from zero when the file
    shrank. Returns the parsed rows plus the offset to resume from; an
    unreadable file yields no rows and the offset unchanged.
    """
    rows: list[dict[str, Any]] = []
    try:
        if path.stat().st_size < position:
            position = 0
        with path.open("rb") as handle:
            handle.seek(position)
            while raw_line := handle.readline():
                if not raw_line.endswith(b"\n"):
                    break
                position = handle.tell()
                try:
                    row = json.loads(raw_line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        pass
    return rows, position


def _read_event_growth(
    paths: list[Path],
    positions: dict[Path, int],
    root: Path,
) -> tuple[dict[str, int], int]:
    """Read only complete JSONL records appended to ``paths`` since the previous call.

    Returns growth keyed by source — the per-GM subdirectory name for multi-GM
    layouts, "" for a flat run-root log — plus the latest episode index seen.
    """
    growth: dict[str, int] = {}
    latest_step = -1
    for path in paths:
        source = "" if path.parent == root else path.parent.name
        rows, positions[path] = _tail_jsonl(path, positions.get(path, 0))
        for row in rows:
            growth[source] = growth.get(source, 0) + 1
            episode = row.get("episode")
            if isinstance(episode, int):
                latest_step = max(latest_step, episode)
    return growth, latest_step


# How often the SSE loop re-discovers event-log files. Resolving all four streams
# stats the filesystem far more than the incremental byte reads do, so it runs on
# this cadence (and whenever a stream has not appeared yet) instead of every 0.4s
# poll tick.
_EVENT_DISCOVERY_INTERVAL_SECONDS = 3.0


def _event_stream_files(root: Path, stream: str) -> list[Path]:
    """Discover a run's ``<stream>_events.jsonl`` logs through the canonical resolver."""
    return resolve_event_files(root, f"{stream}_events.jsonl")


def _checkpoint_finished_step(root: Path) -> int:
    """Highest episode index proven complete by a saved per-step checkpoint.

    Legacy fallback for runs predating ``run_events.jsonl``: a saved
    ``step_N_checkpoint.json`` proves episode ``N-1`` finished, which the event
    streams alone cannot show for a run HOLDING at an episode boundary
    (interactive Step/Pause). Returns ``-1`` when no checkpoint exists.
    Delegates the layout knowledge to the canonical runtime resolver.
    """
    from silisocs.runtime.checkpointing import latest_checkpoint_step

    latest = latest_checkpoint_step(root)
    return latest - 1 if latest >= 0 else -1


def _read_run_events(path: Path, state: dict[str, int]) -> list[dict[str, Any]]:
    """Complete rows appended to ``run_events.jsonl`` since the previous call.

    The runner's purpose-built live feed (``runtime/execution/run_events.py``):
    when present it replaces episode-boundary inference from action rows and
    checkpoint filenames.
    """
    rows, state["pos"] = _tail_jsonl(path, state.get("pos", 0))
    return rows


def _refresh_event_files(
    root: Path,
    stream_files: dict[str, list[Path]],
    last_discovery: float,
    now: float,
) -> tuple[dict[str, list[Path]], float]:
    """Re-discover the per-stream event logs on a cadence, else keep the memo.

    Discovery is far pricier than the per-tick byte reads, so it runs only every
    few seconds — or while a stream's log has not appeared yet.
    """
    if now - last_discovery >= _EVENT_DISCOVERY_INTERVAL_SECONDS or any(
        not stream_files.get(stream) for stream in EVENT_STREAMS
    ):
        return {stream: _event_stream_files(root, stream) for stream in EVENT_STREAMS}, now
    return stream_files, last_discovery


@dataclass(frozen=True)
class Job:
    id: str
    kind: str
    status: str
    pid: int | None
    created_at: float
    started_at: float | None
    ended_at: float | None
    exit_code: int | None
    scenario: str | None
    config_snapshot_path: str | None
    output_dir: str | None
    log_path: str
    parent_study: str | None
    port: int | None
    command_json: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        payload = json.loads(self.command_json or "{}")
        data["command"] = payload
        control_path = payload.get("control_path") if isinstance(payload, dict) else None
        data["control_path"] = control_path
        data["interactive"] = bool(control_path)
        del data["command_json"]
        return data


class JobStore:
    """Thread-safe SQLite persistence for job lifecycle records."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs(
                  id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
                  pid INTEGER, created_at REAL NOT NULL, started_at REAL, ended_at REAL,
                  exit_code INTEGER, scenario TEXT, config_snapshot_path TEXT,
                  output_dir TEXT, log_path TEXT NOT NULL, parent_study TEXT,
                  port INTEGER, command_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS jobs_status_created ON jobs(status, created_at);
                CREATE INDEX IF NOT EXISTS jobs_parent_study ON jobs(parent_study);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def insert(self, job: Job) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                tuple(asdict(job).values()),
            )

    def update(self, job_id: str, **values: Any) -> None:
        if not values:
            return
        columns = ", ".join(f"{name}=?" for name in values)
        with self._lock, self._connect() as connection:
            connection.execute(
                f"UPDATE jobs SET {columns} WHERE id=?",
                (*values.values(), job_id),
            )

    def get(self, job_id: str) -> Job:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return Job(**dict(row))

    def list(self, *, status: str | None = None) -> list[Job]:
        query = "SELECT * FROM jobs"
        args: tuple[Any, ...] = ()
        if status:
            query += " WHERE status=?"
            args = (status,)
        query += " ORDER BY created_at DESC"
        with self._lock, self._connect() as connection:
            return [Job(**dict(row)) for row in connection.execute(query, args)]


class JobManager:
    """FIFO run queue plus unlimited viewer-process management."""

    def __init__(
        self,
        state_dir: str | Path,
        *,
        output_root: str | Path,
        max_concurrent_runs: int = 1,
    ) -> None:
        self.state_dir = Path(state_dir).resolve()
        self.output_root = Path(output_root).resolve()
        self.snapshots = self.state_dir / "snapshots"
        self.logs = self.state_dir / "logs"
        self.snapshots.mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(parents=True, exist_ok=True)
        self.store = JobStore(self.state_dir / "studio.db")
        self.max_concurrent_runs = max(1, int(max_concurrent_runs))
        self._processes: dict[str, subprocess.Popen[Any]] = {}
        self._monitored_external: set[str] = set()
        self._condition = threading.Condition()
        self._closing = False
        self.reconcile()
        self._worker = threading.Thread(target=self._queue_loop, daemon=True, name="studio-jobs")
        self._worker.start()

    def close(self) -> None:
        """Stop Studio's queue thread without terminating detached jobs."""
        self._closing = True
        with self._condition:
            self._condition.notify_all()

    def submit(
        self,
        *,
        kind: str,
        command: list[str],
        cwd: str | Path,
        scenario: str | None = None,
        snapshot: dict[str, Any] | None = None,
        output_dir: str | Path | None = None,
        parent_study: str | None = None,
        port: int | None = None,
        env: dict[str, str] | None = None,
        control_path: str | Path | None = None,
    ) -> Job:
        job_id = uuid.uuid4().hex[:16]
        snapshot_path = self.snapshots / f"{job_id}.yaml"
        snapshot_path.write_text(
            yaml.safe_dump(snapshot or {}, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        job = Job(
            id=job_id,
            kind=kind,
            status="queued",
            pid=None,
            created_at=time.time(),
            started_at=None,
            ended_at=None,
            exit_code=None,
            scenario=scenario,
            config_snapshot_path=str(snapshot_path),
            output_dir=str(Path(output_dir).resolve()) if output_dir else None,
            log_path=str(self.logs / f"{job_id}.log"),
            parent_study=parent_study,
            port=port,
            command_json=json.dumps(
                {
                    "argv": command,
                    "cwd": str(Path(cwd).resolve()),
                    "env": dict(env or {}),
                    "control_path": str(Path(control_path).resolve()) if control_path else None,
                }
            ),
        )
        self.store.insert(job)
        if kind == "viewer":
            self._start(job)
        else:
            with self._condition:
                self._condition.notify_all()
        return self.store.get(job_id)

    def _queue_loop(self) -> None:
        while not self._closing:
            with self._condition:
                running = len(
                    [
                        job
                        for job in self.store.list()
                        if job.kind != "viewer" and job.status == "running"
                    ]
                )
                queued = [
                    job
                    for job in reversed(self.store.list())
                    if job.kind != "viewer" and job.status == "queued"
                ]
                available = self.max_concurrent_runs - running
                if not queued or available <= 0:
                    self._condition.wait(timeout=0.5)
                    continue
            for job in queued[:available]:
                self._start(job)

    def _start(self, job: Job) -> None:
        payload = json.loads(job.command_json)
        log_handle = Path(job.log_path).open("a", encoding="utf-8")  # noqa: SIM115
        try:
            process = subprocess.Popen(
                payload["argv"],
                cwd=payload["cwd"],
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
                env={**os.environ, **payload.get("env", {})},
            )
        except Exception as exc:
            log_handle.write(f"Studio failed to start job: {exc}\n")
            log_handle.close()
            self.store.update(job.id, status="failed", ended_at=time.time(), exit_code=127)
            return
        self._processes[job.id] = process
        self.store.update(job.id, status="running", pid=process.pid, started_at=time.time())
        threading.Thread(
            target=self._monitor,
            args=(job.id, process, log_handle),
            daemon=True,
            name=f"studio-job-{job.id}",
        ).start()

    def _monitor(self, job_id: str, process: subprocess.Popen[Any], log_handle: Any) -> None:
        while process.poll() is None:
            current = self.store.get(job_id)
            self._discover_run_output(current)
            time.sleep(0.4)
        exit_code = int(process.returncode or 0)
        log_handle.close()
        current = self.store.get(job_id)
        # A killed run may still read "running" here (stop() flips the status
        # only after wait()); discovery may also still be pending. Re-attempt
        # discovery once so the manifest patch has a directory to fix.
        self._discover_run_output(current)
        current = self.store.get(job_id)
        status = "finished" if exit_code == 0 else "failed"
        if current.status == "killed":
            status = "killed"
        if status != "finished":
            if status == "killed" or exit_code < 0:
                # Negative return code = terminated by a signal; whether the
                # stop() bookkeeping won the race or not, "exited with code
                # -15" would misattribute a deliberate stop as a crash.
                reason = f"stopped (terminated by signal {abs(exit_code)})"
            else:
                reason = f"process exited with code {exit_code} without finalizing the run"
            self._finalize_run_manifest(current.output_dir, reason)
        self.store.update(job_id, status=status, ended_at=time.time(), exit_code=exit_code)
        self._processes.pop(job_id, None)
        with self._condition:
            self._condition.notify_all()

    def _monitor_external(self, job_id: str) -> None:
        """Monitor a process inherited from an earlier Studio instance.

        A restarted process cannot recreate ``Popen``'s exit status, but it can
        retain truthful liveness and reconcile terminal state from the generic
        run completion marker. Non-run commands become orphaned when their PID
        disappears because their exit code is no longer observable.
        """
        try:
            while True:
                job = self.store.get(job_id)
                self._discover_run_output(job)
                if job.status != "running" or not self._pid_alive(job.pid):
                    break
                time.sleep(0.5)
            job = self.store.get(job_id)
            if job.status != "running":
                return
            complete = self._output_complete(job.output_dir)
            if not complete:
                self._finalize_run_manifest(
                    job.output_dir, "process disappeared before the run finalized"
                )
            self.store.update(
                job.id,
                status="finished" if complete else "orphaned",
                ended_at=time.time(),
                exit_code=0 if complete else None,
            )
        finally:
            self._monitored_external.discard(job_id)
            with self._condition:
                self._condition.notify_all()

    def _discover_run_output(self, job: Job) -> None:
        if job.kind != "run" or job.output_dir or not job.scenario:
            return
        from silisocs.studio.viewers import discover_run_dir

        payload = json.loads(job.command_json)
        discovered = discover_run_dir(
            job.scenario,
            job.started_at or job.created_at,
            root=payload["cwd"],
        )
        if discovered is not None:
            self.store.update(job.id, output_dir=str(discovered.resolve()))

    def stop(self, job_id: str, *, grace_seconds: float = 3.0) -> Job:
        job = self.store.get(job_id)
        if job.status in TERMINAL_STATUSES:
            return job
        if job.status == "queued":
            self.store.update(
                job_id, status="killed", ended_at=time.time(), exit_code=-signal.SIGTERM
            )
            return self.store.get(job_id)
        process = self._processes.get(job_id)
        try:
            if process is not None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=grace_seconds)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
            elif job.pid:
                os.killpg(job.pid, signal.SIGTERM)
                deadline = time.monotonic() + grace_seconds
                while self._pid_alive(job.pid) and time.monotonic() < deadline:
                    time.sleep(0.05)
                if self._pid_alive(job.pid):
                    os.killpg(job.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.store.update(job_id, status="killed", ended_at=time.time())
        return self.store.get(job_id)

    def control(self, job_id: str, command: Mapping[str, Any]) -> dict[str, Any]:
        """Write an interactive run's control file (play/pause/step/stop).

        ``command`` carries ``{"stopped": true}`` to end the run at its next
        episode boundary, or ``{"target": <int|null>}`` to permit episodes below
        ``target`` (``null`` = run freely). The runner's ``control_file``
        controller polls this file; the write fully replaces it, so only the
        acted-on key is present each time.
        """
        job = self.store.get(job_id)  # raises KeyError -> 404 at the route
        payload = json.loads(job.command_json or "{}")
        control_path = payload.get("control_path") if isinstance(payload, dict) else None
        if not control_path:
            raise ValueError("Job is not interactive")
        if job.status not in ("queued", "running"):
            # Writing a terminal job's control file would return 200 while doing
            # nothing; refuse loudly instead.
            raise ValueError(f"Job is {job.status}; run controls only apply to a live job")
        if command.get("stopped"):
            state: dict[str, Any] = {"stopped": True}
        elif "target" in command:
            target = command["target"]
            if target is not None and (isinstance(target, bool) or not isinstance(target, int)):
                raise ValueError("target must be an integer or null")
            state = {"target": target}
        else:
            raise ValueError("control requires 'target' or 'stopped'")
        path = Path(control_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        staged = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        staged.write_text(json.dumps(state), encoding="utf-8")
        staged.replace(path)
        return {"job": job_id, **state}

    def reconcile(self) -> None:
        """Heal persisted running jobs after a Studio restart."""
        for job in self.store.list(status="running"):
            if self._pid_alive(job.pid):
                self._monitored_external.add(job.id)
                threading.Thread(
                    target=self._monitor_external,
                    args=(job.id,),
                    daemon=True,
                    name=f"studio-inherited-{job.id}",
                ).start()
                continue
            complete = self._output_complete(job.output_dir)
            if not complete:
                self._finalize_run_manifest(
                    job.output_dir, "process disappeared before the run finalized"
                )
            self.store.update(
                job.id,
                status="finished" if complete else "orphaned",
                ended_at=time.time(),
                exit_code=0 if complete else job.exit_code,
            )

    @staticmethod
    def _finalize_run_manifest(output_dir: str | None, reason: str) -> None:
        """Mark a dead job's still-``running`` manifest as failed, with the reason.

        The manifest is the archive's source of truth: without this, a run whose
        process died (crash, kill, host reboot) lists as running forever in every
        consumer. Best-effort — a manifest that is missing, unreadable, or already
        terminal is left alone.
        """
        if not output_dir:
            return
        try:
            artifact = load_run(output_dir)
            if artifact.status != "running":
                return
            manifest = {**artifact.manifest, "status": "failed", "error": reason}
            path = Path(output_dir) / "run_manifest.json"
            staged = path.with_name(f".{path.name}.tmp")
            staged.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            staged.replace(path)
        except (OSError, ValueError):
            return

    @staticmethod
    def _pid_alive(pid: int | None) -> bool:
        if not pid:
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    @staticmethod
    def _output_complete(output_dir: str | None) -> bool:
        if not output_dir:
            return False
        root = Path(output_dir)
        if (root / "RUN_COMPLETE.json").is_file():
            return True
        try:
            status = str(load_run(root).status or "").lower()
        except (OSError, ValueError):
            return False
        return status in {"complete", "finished", "success", "succeeded"}

    def events(self, job_id: str) -> Iterator[dict[str, Any]]:
        """Yield SSE-ready event records until the job reaches a terminal state."""
        job = self.store.get(job_id)
        offset = 0
        previous_status: str | None = None
        previous_output: str | None = None
        artifact_counts: dict[str, int] = {}
        artifact_sources: dict[str, dict[str, int]] = {}
        stream_positions: dict[Path, int] = {}
        stream_files: dict[str, list[Path]] = {}
        last_discovery = 0.0
        started_step = -1
        finished_step = -1
        run_event_state: dict[str, int] = {}
        has_runner_events = False
        while True:
            job = self.store.get(job_id)
            if job.status != previous_status:
                previous_status = job.status
                yield {"event": "status_changed", "data": {"status": job.status}}
            if job.output_dir and job.output_dir != previous_output:
                previous_output = job.output_dir
                yield {"event": "output_discovered", "data": {"output_dir": job.output_dir}}
            path = Path(job.log_path)
            if path.is_file():
                with path.open(encoding="utf-8", errors="replace") as handle:
                    handle.seek(offset)
                    for line in handle:
                        yield {"event": "log_line", "data": {"line": line.rstrip("\n")}}
                    offset = handle.tell()
            if job.output_dir:
                root = Path(job.output_dir)
                stream_files, last_discovery = _refresh_event_files(
                    root, stream_files, last_discovery, time.monotonic()
                )
                latest_step = -1
                for stream in EVENT_STREAMS:
                    growth, stream_step = _read_event_growth(
                        stream_files.get(stream, []), stream_positions, root
                    )
                    if growth:
                        by_source = artifact_sources.setdefault(stream, {})
                        for source, count in growth.items():
                            by_source[source] = by_source.get(source, 0) + count
                        artifact_counts[stream] = sum(by_source.values())
                        yield {
                            "event": "artifact_grown",
                            "data": {
                                "stream": stream,
                                "new_count": artifact_counts[stream],
                                # Per-GM breakdown ("" = flat run-root log);
                                # the watch ribbon shows it for multi-GM runs.
                                "sources": dict(by_source),
                            },
                        }
                    latest_step = max(latest_step, stream_step)
                # Prefer the runner's own live feed for step boundaries; the
                # inference below stays only for runs predating it.
                runner_rows = _read_run_events(root / RUN_EVENTS_FILENAME, run_event_state)
                # Gate on STEP rows, not any row: a custom LoopStrategy that
                # never emits step boundaries still gets status rows from the
                # session, and must keep the legacy inference below working.
                has_runner_events = has_runner_events or any(
                    row.get("kind") in ("step_started", "step_finished") for row in runner_rows
                )
                for row in runner_rows:
                    step = row.get("step")
                    if not isinstance(step, int):
                        continue
                    if row.get("kind") == "step_started" and step > started_step:
                        started_step = step
                        yield {"event": "step_started", "data": {"step": step}}
                    elif row.get("kind") == "step_finished" and step > finished_step:
                        finished_step = step
                        started_step = max(started_step, step)
                        yield {"event": "step_finished", "data": {"step": step}}
                if not has_runner_events:
                    if latest_step > started_step:
                        if started_step >= 0 and finished_step < started_step:
                            finished_step = started_step
                            yield {"event": "step_finished", "data": {"step": finished_step}}
                        started_step = latest_step
                        yield {"event": "step_started", "data": {"step": started_step}}
                    # A saved per-step checkpoint completes an episode even when
                    # no later episode has started — the case for an interactive
                    # run holding at the boundary, whose control bar must flip
                    # from "running episode N" to "paused · N+1 done" on hold.
                    completed = _checkpoint_finished_step(root)
                    if completed > finished_step:
                        finished_step = completed
                        started_step = max(started_step, completed)
                        yield {"event": "step_finished", "data": {"step": finished_step}}
            if job.status in TERMINAL_STATUSES:
                if started_step >= 0 and finished_step < started_step:
                    yield {"event": "step_finished", "data": {"step": started_step}}
                yield {"event": "done", "data": {"status": job.status, "exit_code": job.exit_code}}
                return
            time.sleep(0.4)


def allocate_port() -> int:
    """Ask the OS for an available local TCP port."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
