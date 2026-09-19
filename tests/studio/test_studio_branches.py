"""Studio branch command reconstruction tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, cast

import pytest

pytest.importorskip("fastapi")

from silisocs.studio.app import create_app
from silisocs.studio.branches import prepare_branches
from silisocs.studio.jobs import Job
from silisocs.studio.routes.jobs import api_create_branches, api_plan_branch
from silisocs.studio.routes.runs import _lineage_graph


def _checkpoint(root: Path) -> None:
    directory = root / "checkpoints"
    directory.mkdir(parents=True)
    (directory / "step_2_checkpoint.json").write_text(
        json.dumps(
            {
                "step": 2,
                "objects": {
                    "gm": {
                        "role": "game_master",
                        "state": {"backend": {"backend_type": "custom", "state": {}}},
                    }
                },
                "runtime_metadata": {
                    "game_masters": [{"name": "gm", "supports_checkpoint_branching": True}]
                },
            }
        ),
        encoding="utf-8",
    )


def _job(tmp_path: Path, parent: Path) -> Job:
    return Job(
        id="parent-job",
        kind="run",
        status="finished",
        pid=None,
        created_at=1,
        started_at=1,
        ended_at=2,
        exit_code=0,
        scenario="world",
        config_snapshot_path=None,
        output_dir=str(parent),
        log_path=str(tmp_path / "parent.log"),
        parent_study=None,
        port=None,
        command_json=json.dumps(
            {
                "argv": [
                    "python",
                    "-m",
                    "silisocs.runtime.runner",
                    "--config-path",
                    str(tmp_path / "conf"),
                    "num_steps=5",
                    f"++output_dir={parent}",
                    "sim.engine.control.built_in=control_file",
                ],
                "cwd": str(tmp_path),
                "env": {"PYTHONPATH": str(tmp_path)},
            }
        ),
    )


def test_prepare_branch_reuses_provenance_and_replaces_managed_overrides(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    _checkpoint(parent)
    group, branches, cwd, env = prepare_branches(
        parent_job=_job(tmp_path, parent),
        parent_run_id="world/parent",
        parent_run_path=parent,
        output_root=tmp_path / "outputs",
        requests=[{"name": "alternate", "mode": "resample", "overrides": ["num_steps=9"]}],
        checkpoint_step=2,
        interactive=True,
        start_paused=True,
    )

    assert len(group) == 12 and cwd == str(tmp_path)
    assert env == {"PYTHONPATH": str(tmp_path)}
    branch = branches[0]
    assert branch.plan.continuation_seed is not None
    assert branch.output_dir.parent.name == group
    assert "num_steps=5" not in branch.command
    assert "num_steps=9" in branch.command
    assert f"sim.checkpoint.source_run={json.dumps(str(parent))}" in branch.command
    assert "sim.checkpoint.source_step=2" in branch.command
    assert branch.command.count("sim.engine.control.built_in=control_file") == 1
    assert branch.control_path == branch.output_dir / "run.control"


def test_studio_branch_plan_create_and_run_page_surface(tmp_path: Path, monkeypatch) -> None:
    outputs = tmp_path / "outputs"
    parent = outputs / "demo" / "parent"
    _checkpoint(parent)
    (parent / "run_manifest.json").write_text(
        json.dumps(
            {
                "status": "success",
                "scenario": "world",
                "num_steps": 5,
                "artifacts": {"action_events": []},
            }
        ),
        encoding="utf-8",
    )
    app = create_app(outputs, state_dir=tmp_path / "state", repo_root=tmp_path)
    parent_job = _job(tmp_path, parent)
    app.state.studio.jobs.store.insert(parent_job)

    class Request:
        def __init__(self, body):
            self.app = app
            self._body = body

        async def json(self):
            return self._body

    plan = asyncio.run(
        api_plan_branch(
            cast(Any, Request({"checkpoint_step": 2, "overrides": ["num_steps=7"]})),
            "demo/parent",
        )
    )
    assert plan["checkpoint_step"] == 2
    assert plan["planned"] is True
    assert plan["runtime_validation"] == "on_launch"
    assert "compatible" not in plan

    child = Job(
        **{
            **parent_job.__dict__,
            "id": "child-job",
            "status": "queued",
            "output_dir": str(outputs / "branches" / "child"),
        }
    )
    monkeypatch.setattr(app.state.studio.jobs, "submit", lambda **kwargs: child)
    created = asyncio.run(
        api_create_branches(
            cast(
                Any,
                Request(
                    {
                        "checkpoint_step": 2,
                        "name": "alternate",
                        "mode": "resample",
                        "overrides": ["num_steps=7"],
                    }
                ),
            ),
            "demo/parent",
        )
    )
    assert created["items"][0]["id"] == "child-job"
    assert created["items"][0]["continuation_seed"] is not None

    template = Path("src/silisocs/studio/templates/run.html").read_text(encoding="utf-8")
    assert 'data-testid="branch-run"' in template
    assert 'id="branch-run-dialog"' in template
    assert "Configuration changes" in template
    assert 'data-testid="run-lineage"' in template


def test_lineage_graph_connects_parent_siblings_and_descendants(tmp_path: Path) -> None:
    """The visual projection preserves the complete connected branch family."""

    def record(run_id: str, parent: str = "", step: int | None = None):
        lineage = (
            {"id": run_id.rsplit("/", 1)[-1], "parent_run_id": parent, "checkpoint_step": step}
            if parent
            else {}
        )
        return cast(
            Any,
            type(
                "Record",
                (),
                {
                    "id": run_id,
                    "path": tmp_path / run_id,
                    "modified": float(step or 0),
                    "artifact": type("Artifact", (), {"lineage": lineage, "status": "success"})(),
                },
            )(),
        )

    parent = record("demo/root")
    first = record("branches/first", "demo/root", 2)
    sibling = record("branches/sibling", "demo/root", 3)
    grandchild = record("branches/grandchild", "branches/first", 4)

    graph = _lineage_graph(first, [grandchild, sibling, parent, first])
    assert [node["id"] for node in graph] == [
        "demo/root",
        "branches/first",
        "branches/sibling",
        "branches/grandchild",
    ]
    assert next(node for node in graph if node["current"])["id"] == "branches/first"
