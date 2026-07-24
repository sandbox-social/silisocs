"""Interactive run-control tests: the step gate, controllers, and loop wiring."""

from __future__ import annotations

import threading
import time
from typing import Any

from silisocs.simulation_engines.control import (
    ControlFileController,
    StdinController,
    StepGate,
    _apply_stdin_command,
    build_run_control,
)
from silisocs.simulation_engines.policies.loops import (
    FixedStepsLoopStrategy,
    await_step_permission,
)


def _until(predicate: Any, timeout: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class _Recorder:
    def __init__(self) -> None:
        self.episodes: list[int] = []

    def record_episode(self, *, episode: int, **_: Any) -> None:
        self.episodes.append(episode)


class _StepResult:
    probe_phase: Any = None


class _Engine:
    """Minimal engine exposing only what FixedStepsLoopStrategy consults."""

    probe_runner = None
    interventions = None

    def __init__(self, gate: StepGate | None = None) -> None:
        self.recorder = _Recorder()
        self.step_gate = gate
        self.ran: list[int] = []

    def run_step(self, *, step_index: int, **_: Any) -> _StepResult:
        self.ran.append(step_index)
        return _StepResult()


def _run_loop(engine: _Engine, max_steps: int, start_step: int = 0) -> threading.Thread:
    thread = threading.Thread(
        target=lambda: FixedStepsLoopStrategy().run(
            engine=engine,
            game_masters=[],
            agents=[],
            max_steps=max_steps,
            start_step=start_step,
            verbose=False,
            checkpoint_callback=None,
        ),
        daemon=True,
    )
    thread.start()
    return thread


def test_no_gate_runs_every_episode() -> None:
    """With no gate attached the loop advances every episode uninterrupted."""
    engine = _Engine(gate=None)
    _run_loop(engine, max_steps=3).join(timeout=2)
    assert engine.ran == [0, 1, 2]


def test_gate_holds_then_steps_then_stops() -> None:
    """A paused gate holds the loop, then steps and stops on command."""
    gate = StepGate(target=0)  # start paused before episode 0
    engine = _Engine(gate=gate)
    thread = _run_loop(engine, max_steps=5)

    # Paused: nothing runs until the gate advances.
    assert not _until(lambda: engine.ran, timeout=0.2)

    gate.set_target(2)  # permit episodes 0 and 1, then hold
    assert _until(lambda: engine.ran == [0, 1])
    assert not _until(lambda: engine.ran != [0, 1], timeout=0.2)  # stays held

    gate.set_target(3)  # one more, then hold
    assert _until(lambda: engine.ran == [0, 1, 2])

    gate.stop()  # end the run at the boundary
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert engine.ran == [0, 1, 2]


def test_stop_before_any_episode_runs_nothing() -> None:
    """Stopping a paused gate ends the run without executing an episode."""
    gate = StepGate(target=0)
    engine = _Engine(gate=gate)
    thread = _run_loop(engine, max_steps=5)
    gate.stop()
    thread.join(timeout=2)
    assert engine.ran == []


def test_control_file_drives_the_gate(tmp_path) -> None:
    """A JSON control file advances the loop and stops it via the poller."""
    control = tmp_path / "run.control"
    gate = StepGate(target=0)  # start held (the `start_paused` config path)
    controller = ControlFileController(gate, control, poll_interval=0.02)
    engine = _Engine(gate=gate)
    thread = _run_loop(engine, max_steps=5)
    controller.start()

    control.write_text('{"target": 2}', encoding="utf-8")
    assert _until(lambda: engine.ran == [0, 1])

    control.write_text('{"stopped": true}', encoding="utf-8")
    thread.join(timeout=2)
    controller.close()
    assert engine.ran == [0, 1]


def test_control_file_ignores_invalid_target_then_recovers(tmp_path) -> None:
    """A malformed command must not kill the poller before a valid update."""
    control = tmp_path / "run.control"
    gate = StepGate(target=0)
    controller = ControlFileController(gate, control, poll_interval=0.02)
    engine = _Engine(gate=gate)
    thread = _run_loop(engine, max_steps=2)
    controller.start()

    control.write_text('{"target": "bad"}', encoding="utf-8")
    assert not _until(lambda: engine.ran, timeout=0.1)
    control.write_text('{"target": 1}', encoding="utf-8")
    assert _until(lambda: engine.ran == [0])
    gate.stop()
    thread.join(timeout=2)
    controller.close()


def test_control_file_start_discards_stale_file(tmp_path) -> None:
    """A leftover control file from a prior run must not command a fresh one."""
    control = tmp_path / "run.control"
    control.write_text('{"stopped": true}', encoding="utf-8")  # stale leftover
    gate = StepGate()
    controller = ControlFileController(gate, control, poll_interval=0.02)
    controller.start()

    assert _until(lambda: not control.exists())
    assert gate.snapshot()["stopped"] is False
    controller.close()


def test_control_file_stop_lands_despite_malformed_target(tmp_path) -> None:
    """`stopped` in the same payload as a bad `target` must still stop the run."""
    control = tmp_path / "run.control"
    gate = StepGate(target=0)
    controller = ControlFileController(gate, control, poll_interval=0.02)
    engine = _Engine(gate=gate)
    thread = _run_loop(engine, max_steps=5)
    controller.start()

    control.write_text('{"target": "bad", "stopped": true}', encoding="utf-8")
    thread.join(timeout=2)
    controller.close()
    assert not thread.is_alive()
    assert engine.ran == []


def test_stdin_commands_map_to_gate_transitions() -> None:
    """Terminal commands set the gate target and stop flag as expected."""
    gate = StepGate(target=0)
    assert not _apply_stdin_command(gate, "n\n")
    assert gate.snapshot()["target"] == 1  # advance one from current (0)
    assert not _apply_stdin_command(gate, "next 3")
    assert gate.snapshot()["target"] == 3  # current is still 0 until a turn runs  # noqa: PLR2004
    assert not _apply_stdin_command(gate, "c")
    assert gate.snapshot()["target"] is None  # run freely
    assert _apply_stdin_command(gate, "stop")  # returns True -> stop reading
    assert gate.snapshot()["stopped"] is True


def test_gate_normalizes_targets_at_its_controller_boundary() -> None:
    gate = StepGate(target=-4)
    assert gate.snapshot()["target"] == 0
    gate.set_target(-2)
    assert gate.snapshot()["target"] == 0


def test_build_run_control_modes(tmp_path) -> None:
    """`none` disables control; stdin/control_file build a gate + controller."""
    assert build_run_control({"built_in": "none"}, output_dir=tmp_path) == (None, None)
    assert build_run_control(None, output_dir=tmp_path) == (None, None)

    gate, controller = build_run_control(
        {"built_in": "stdin", "start_paused": True}, output_dir=tmp_path
    )
    assert isinstance(controller, StdinController)
    assert gate is not None
    assert gate.snapshot()["target"] == 0  # start paused

    gate, controller = build_run_control(
        {"built_in": "control_file", "start_paused": False}, output_dir=tmp_path
    )
    assert isinstance(controller, ControlFileController)
    assert gate is not None
    assert gate.snapshot()["target"] is None  # run freely


def test_await_step_permission_is_a_noop_without_a_gate() -> None:
    """A strategy calling the helper costs nothing when control is off."""
    engine = _Engine()
    assert await_step_permission(engine, 0) is True


def test_a_custom_loop_strategy_inherits_control_via_the_helper() -> None:
    """A replacement LoopStrategy gets play/pause/step by calling the helper.

    This is the extension guarantee: FixedStepsLoopStrategy is pluggable, so the
    step gate must be reachable from any custom strategy rather than being welded
    into the built-in loop.
    """
    engine = _Engine()
    gate = StepGate(target=0)  # hold before episode 0
    engine.step_gate = gate
    ran: list[int] = []

    def custom_loop() -> None:
        step = 0
        while step < 5:
            if not await_step_permission(engine, step):
                break
            ran.append(step)
            step += 1

    thread = threading.Thread(target=custom_loop, daemon=True)
    thread.start()

    assert _until(lambda: gate.current == 0)
    assert ran == []  # held before the first episode

    gate.set_target(2)  # allow episodes 0 and 1
    assert _until(lambda: ran == [0, 1])

    gate.stop()  # terminate the custom loop
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert ran == [0, 1]
