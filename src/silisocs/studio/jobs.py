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
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

TERMINAL_STATUSES = frozenset({"finished", "failed", "killed", "orphaned"})
EVENT_STREAMS = ("action", "exposure", "probe", "harness")


def _read_event_growth(
    root: Path,
    stream: str,
    positions: dict[Path, int],
) -> tuple[int, int]:
    """Read only complete JSONL records appended since the previous call."""
    new_count = 0
    latest_step = -1
    for path in root.glob(f"**/{stream}_events.jsonl"):
        try:
            position = positions.get(path, 0)
            if path.stat().st_size < position:
                position = 0
            with path.open("rb") as handle:
                handle.seek(position)
                while raw_line := handle.readline():
                    if not raw_line.endswith(b"\n"):
                        break
                    positions[path] = handle.tell()
                    try:
                        row = json.loads(raw_line)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if not isinstance(row, dict):
                        continue
                    new_count += 1
                    episode = row.get("episode")
                    if isinstance(episode, int):
                        latest_step = max(latest_step, episode)
        except OSError:
            continue
    return new_count, latest_step


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
        data["command"] = json.loads(self.command_json or "[]")
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
                {"argv": command, "cwd": str(Path(cwd).resolve()), "env": dict(env or {})}
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
        status = "finished" if exit_code == 0 else "failed"
        if current.status == "killed":
            status = "killed"
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
            self.store.update(
                job.id,
                status="finished" if complete else "orphaned",
                ended_at=time.time(),
                exit_code=0 if complete else job.exit_code,
            )

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
        manifest = root / "run_manifest.json"
        if not manifest.is_file():
            return False
        try:
            status = str(
                json.loads(manifest.read_text(encoding="utf-8")).get("status") or ""
            ).lower()
            return status in {"complete", "finished", "success", "succeeded"}
        except (OSError, json.JSONDecodeError):
            return False

    def events(self, job_id: str) -> Iterator[dict[str, Any]]:
        """Yield SSE-ready event records until the job reaches a terminal state."""
        job = self.store.get(job_id)
        offset = 0
        previous_status: str | None = None
        previous_output: str | None = None
        artifact_counts: dict[str, int] = {}
        stream_positions: dict[Path, int] = {}
        started_step = -1
        finished_step = -1
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
                latest_step = -1
                for stream in EVENT_STREAMS:
                    growth, stream_step = _read_event_growth(root, stream, stream_positions)
                    if growth:
                        artifact_counts[stream] = artifact_counts.get(stream, 0) + growth
                        yield {
                            "event": "artifact_grown",
                            "data": {
                                "stream": stream,
                                "new_count": artifact_counts[stream],
                            },
                        }
                    latest_step = max(latest_step, stream_step)
                if latest_step > started_step:
                    if started_step >= 0 and finished_step < started_step:
                        finished_step = started_step
                        yield {"event": "step_finished", "data": {"step": finished_step}}
                    started_step = latest_step
                    yield {"event": "step_started", "data": {"step": started_step}}
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
