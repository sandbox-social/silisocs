"""Pure launch preparation tests, independent of FastAPI and the simulator."""

from __future__ import annotations

from pathlib import Path

from silisocs.studio.launch import ScenarioNotFoundError, prepare_launch


def test_named_scenario_launch_uses_unique_managed_output(tmp_path: Path) -> None:
    (tmp_path / "scenarios" / "world" / "conf").mkdir(parents=True)

    spec = prepare_launch(
        {"scenario": "world", "overrides": ["num_steps=2"]},
        repository_root=tmp_path,
        output_root=tmp_path / "outputs",
        draft_root=tmp_path / "state" / "drafts",
    )

    assert spec.scenario == "world"
    assert spec.output_dir.parent == tmp_path / "outputs" / "world"
    assert spec.command[-2] == "num_steps=2"
    assert spec.command[-1] == f"++output_rootname={spec.output_dir}"


def test_interactive_launch_injects_control_overrides(tmp_path: Path) -> None:
    (tmp_path / "scenarios" / "world" / "conf").mkdir(parents=True)

    spec = prepare_launch(
        {"scenario": "world", "interactive": True, "start_paused": True},
        repository_root=tmp_path,
        output_root=tmp_path / "outputs",
        draft_root=tmp_path / "drafts",
    )

    assert spec.control_path == (spec.output_dir / "run.control").resolve()
    assert "sim.engine.control.built_in=control_file" in spec.command
    assert f"sim.engine.control.control_file={spec.control_path}" in spec.command
    assert "sim.engine.control.start_paused=true" in spec.command


def test_non_interactive_launch_has_no_control_overrides(tmp_path: Path) -> None:
    (tmp_path / "scenarios" / "world" / "conf").mkdir(parents=True)

    spec = prepare_launch(
        {"scenario": "world"},
        repository_root=tmp_path,
        output_root=tmp_path / "outputs",
        draft_root=tmp_path / "drafts",
    )

    assert spec.control_path is None
    assert not any("engine.control" in arg for arg in spec.command)


def test_custom_backend_config_is_staged_without_backend_branches(tmp_path: Path) -> None:
    files = {
        "world/default.yaml": "scenario_name: custom_world\nnum_agents: 2\nnum_steps: 1\n",
        "env.yaml": "gm:\n  backend:\n    type: custom\n    class_path: lab.backend.World\n",
    }

    spec = prepare_launch(
        {"name": "custom_world", "config_yaml": {"files": files}},
        repository_root=tmp_path,
        output_root=tmp_path / "outputs",
        draft_root=tmp_path / "drafts",
    )

    config_dir = Path(spec.command[spec.command.index("--config-path") + 1])
    assert (
        (config_dir / "env.yaml")
        .read_text(encoding="utf-8")
        .endswith("class_path: lab.backend.World\n")
    )


def test_launch_validation_rejects_unsafe_names_and_output_conflicts(tmp_path: Path) -> None:
    common = {
        "repository_root": tmp_path,
        "output_root": tmp_path / "outputs",
        "draft_root": tmp_path / "drafts",
    }
    for payload, message in (
        ({"name": "../outside", "config_yaml": {"world/default.yaml": "{}\n"}}, "safe"),
        ({"scenario": "missing"}, "not found"),
    ):
        try:
            prepare_launch(payload, **common)
        except (ValueError, ScenarioNotFoundError) as exc:
            assert message in str(exc).lower()
        else:
            raise AssertionError(f"invalid launch was accepted: {payload}")

    (tmp_path / "scenarios" / "world" / "conf").mkdir(parents=True)
    try:
        prepare_launch(
            {
                "scenario": "world",
                "output_dir": "one",
                "overrides": ["output_rootname=two"],
            },
            **common,
        )
    except ValueError as exc:
        assert "not both" in str(exc)
    else:
        raise AssertionError("conflicting output paths were accepted")
