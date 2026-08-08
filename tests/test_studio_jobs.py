"""Control-plane lifecycle tests without FastAPI or simulation dependencies."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from silisocs.studio.jobs import TERMINAL_STATUSES, Job, JobManager, JobStore


def _wait(manager: JobManager, job_id: str, timeout: float = 5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = manager.store.get(job_id)
        if job.status in TERMINAL_STATUSES:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish")


def test_job_manager_runs_captures_and_persists(tmp_path):
    manager = JobManager(tmp_path / "state", output_root=tmp_path / "outputs")
    job = manager.submit(
        kind="run",
        command=[sys.executable, "-c", "print('hello control plane')"],
        cwd=tmp_path,
        scenario="demo",
        snapshot={"scenario": "demo"},
    )

    finished = _wait(manager, job.id)
    assert finished.status == "finished"
    assert finished.exit_code == 0
    assert "hello control plane" in Path(finished.log_path).read_text(encoding="utf-8")
    assert manager.store.get(job.id).to_dict()["command"]["argv"][0] == sys.executable


def test_job_manager_can_kill_running_process(tmp_path):
    manager = JobManager(tmp_path / "state", output_root=tmp_path / "outputs")
    job = manager.submit(
        kind="run",
        command=[sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path,
    )
    deadline = time.time() + 3
    while manager.store.get(job.id).status == "queued" and time.time() < deadline:
        time.sleep(0.05)
    stopped = manager.stop(job.id, grace_seconds=0.2)
    assert stopped.status == "killed"


def test_restart_reconciliation_heals_completed_orphan(tmp_path):
    state = tmp_path / "state"
    run = tmp_path / "outputs" / "run"
    run.mkdir(parents=True)
    (run / "RUN_COMPLETE.json").write_text("{}\n", encoding="utf-8")
    store = JobStore(state / "studio.db")
    store.insert(
        Job(
            id="inherited",
            kind="run",
            status="running",
            pid=999_999_999,
            created_at=time.time(),
            started_at=time.time(),
            ended_at=None,
            exit_code=None,
            scenario="demo",
            config_snapshot_path=None,
            output_dir=str(run),
            log_path=str(state / "inherited.log"),
            parent_study=None,
            port=None,
            command_json=json.dumps({"argv": [], "cwd": str(tmp_path), "env": {}}),
        )
    )

    manager = JobManager(state, output_root=tmp_path / "outputs")
    healed = manager.store.get("inherited")
    assert healed.status == "finished"
    assert healed.exit_code == 0


def test_restart_reconciliation_accepts_success_manifest(tmp_path):
    state = tmp_path / "state"
    run = tmp_path / "outputs" / "run"
    run.mkdir(parents=True)
    (run / "run_manifest.json").write_text(json.dumps({"status": "success"}), encoding="utf-8")
    store = JobStore(state / "studio.db")
    store.insert(
        Job(
            id="manifest-complete",
            kind="run",
            status="running",
            pid=999_999_999,
            created_at=time.time(),
            started_at=time.time(),
            ended_at=None,
            exit_code=None,
            scenario="demo",
            config_snapshot_path=None,
            output_dir=str(run),
            log_path=str(state / "manifest-complete.log"),
            parent_study=None,
            port=None,
            command_json=json.dumps({"argv": [], "cwd": str(tmp_path), "env": {}}),
        )
    )

    manager = JobManager(state, output_root=tmp_path / "outputs")

    assert manager.store.get("manifest-complete").status == "finished"


def test_control_writes_interactive_control_file(tmp_path):
    manager = JobManager(tmp_path / "state", output_root=tmp_path / "outputs")
    control_path = tmp_path / "outputs" / "run" / "run.control"
    job = manager.submit(
        kind="run",
        command=[sys.executable, "-c", "pass"],
        cwd=tmp_path,
        output_dir=tmp_path / "outputs" / "run",
        control_path=control_path,
    )
    assert manager.store.get(job.id).to_dict()["interactive"] is True

    manager.control(job.id, {"target": 2})
    assert json.loads(control_path.read_text(encoding="utf-8")) == {"target": 2}
    manager.control(job.id, {"stopped": True})
    assert json.loads(control_path.read_text(encoding="utf-8")) == {"stopped": True}
    assert not list(control_path.parent.glob(f".{control_path.name}.*.tmp"))


def test_control_rejects_non_interactive_job(tmp_path):
    manager = JobManager(tmp_path / "state", output_root=tmp_path / "outputs")
    job = manager.submit(kind="run", command=[sys.executable, "-c", "pass"], cwd=tmp_path)
    try:
        manager.control(job.id, {"target": 1})
    except ValueError as exc:
        assert "not interactive" in str(exc)
    else:
        raise AssertionError("control accepted a non-interactive job")


def test_terminal_event_stream_reports_growth_and_step(tmp_path):
    manager = JobManager(tmp_path / "state", output_root=tmp_path / "outputs")
    run = tmp_path / "outputs" / "run"
    run.mkdir(parents=True)
    (run / "action_events.jsonl").write_text(
        json.dumps({"episode": 2, "source_user": "A", "label": "move"}) + "\n",
        encoding="utf-8",
    )
    job = manager.submit(
        kind="run",
        command=[sys.executable, "-c", "pass"],
        cwd=tmp_path,
        output_dir=run,
    )
    _wait(manager, job.id)

    events = list(manager.events(job.id))
    assert any(item["event"] == "artifact_grown" for item in events)
    started = {item["data"]["step"] for item in events if item["event"] == "step_started"}
    steps = {item["data"]["step"] for item in events if item["event"] == "step_finished"}
    assert started == {2}
    assert steps == {2}


def test_checkpoint_completes_step_for_a_holding_interactive_run(tmp_path):
    """A saved step checkpoint emits step_finished without a next episode.

    An interactive run holding at the boundary never starts episode N+1, so the
    stream-derived boundary detection alone would report episode N as running
    forever; the per-step checkpoint is the completion signal.
    """
    from silisocs.studio.jobs import _checkpoint_finished_step

    run = tmp_path / "run"
    (run / "checkpoints").mkdir(parents=True)
    assert _checkpoint_finished_step(run) == -1
    (run / "checkpoints" / "step_1_checkpoint.json").write_text("{}", encoding="utf-8")
    assert _checkpoint_finished_step(run) == 0  # step_1 saved => episode 0 done
    (run / "checkpoints" / "step_3_checkpoint.json").write_text("{}", encoding="utf-8")
    assert _checkpoint_finished_step(run) == 2

    manager = JobManager(tmp_path / "state", output_root=tmp_path / "outputs")
    (run / "action_events.jsonl").write_text(
        json.dumps({"episode": 2, "source_user": "A", "label": "move"}) + "\n",
        encoding="utf-8",
    )
    job = manager.submit(
        kind="run",
        command=[sys.executable, "-c", "pass"],
        cwd=tmp_path,
        output_dir=run,
    )
    _wait(manager, job.id)

    events = list(manager.events(job.id))
    finished = [item["data"]["step"] for item in events if item["event"] == "step_finished"]
    # The checkpoint-derived completion (episode 2) arrives from the holding
    # state, not only from the terminal flush.
    assert 2 in finished


def test_event_stream_reads_only_appended_records(tmp_path):
    manager = JobManager(tmp_path / "state", output_root=tmp_path / "outputs")
    run = tmp_path / "outputs" / "run"
    run.mkdir(parents=True)
    events_path = run / "action_events.jsonl"
    events_path.write_text(
        json.dumps({"episode": 0, "source_user": "A", "label": "move"}) + "\n",
        encoding="utf-8",
    )
    job = manager.submit(
        kind="run",
        command=[
            sys.executable,
            "-c",
            (
                "import json,time;time.sleep(.3);"
                f"open({str(events_path)!r},'a').write(json.dumps({{'episode':1}})+'\\n')"
            ),
        ],
        cwd=tmp_path,
        output_dir=run,
    )

    events = list(manager.events(job.id))

    counts = [item["data"]["new_count"] for item in events if item["event"] == "artifact_grown"]
    assert counts == [1, 2]
    assert [item["data"]["step"] for item in events if item["event"] == "step_started"] == [0, 1]
    assert [item["data"]["step"] for item in events if item["event"] == "step_finished"] == [0, 1]


def test_restart_reconciliation_marks_dead_run_manifest_failed(tmp_path):
    """A dead PID with a still-running manifest must not list as running forever."""
    state = tmp_path / "state"
    run = tmp_path / "outputs" / "run"
    run.mkdir(parents=True)
    (run / "run_manifest.json").write_text(
        json.dumps({"status": "running", "scenario": "demo"}), encoding="utf-8"
    )
    store = JobStore(state / "studio.db")
    store.insert(
        Job(
            id="dead-run",
            kind="run",
            status="running",
            pid=999_999_999,
            created_at=time.time(),
            started_at=time.time(),
            ended_at=None,
            exit_code=None,
            scenario="demo",
            config_snapshot_path=None,
            output_dir=str(run),
            log_path=str(state / "dead-run.log"),
            parent_study=None,
            port=None,
            command_json=json.dumps({"argv": [], "cwd": str(tmp_path), "env": {}}),
        )
    )

    manager = JobManager(state, output_root=tmp_path / "outputs")

    assert manager.store.get("dead-run").status == "orphaned"
    manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert "disappeared" in manifest["error"]
    assert manifest["scenario"] == "demo"
