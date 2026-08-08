"""Interactive episode-boundary run control (play / pause / step).

The engine loop advances one episode at a time. A :class:`StepGate` lets an
external controller *hold* the loop between episodes without touching per-turn
scheduling: the loop calls :meth:`StepGate.await_turn` before each episode and
blocks there while the gate withholds permission. The default engine has **no**
gate attached, so the loop never blocks and pays nothing.

A gate is attached only when ``sim.engine.control`` selects a controller:

- ``stdin`` — a REPL thread reads single-letter commands from the terminal
  (``n``/next, ``c``/continue, ``p``/pause, ``s``/stop) between episodes.
- ``control_file`` — a poller thread obeys a JSON file another process (Studio)
  writes: ``{"target": <int|null>, "stopped": <bool>}``.

Both controllers only ever call the gate's thread-safe primitives
(:meth:`set_target`, :meth:`stop`), so the gate is input-agnostic and the loop
stays a plain ``while``. ``target`` is the index of the first episode to hold
before, i.e. episodes ``0 .. target-1`` run and the loop then blocks; ``None``
means "run freely" (bounded only by ``num_steps``).
"""

from __future__ import annotations

import json
import sys
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import psutil


class StepGate:
    """Thread-safe permission gate consulted at each episode boundary.

    ``target`` is the first episode index to hold before (``None`` = unbounded).
    ``current`` tracks the episode the loop is gating on, so a controller can
    express commands relative to "here" (e.g. step-one = ``current + 1``).
    """

    def __init__(self, *, target: int | None = None) -> None:
        self._cond = threading.Condition()
        self._target = None if target is None else max(0, int(target))
        self._stopped = False
        self._current = 0

    def await_turn(self, step: int) -> bool:
        """Block until episode ``step`` may run. Return ``False`` to stop the loop."""
        with self._cond:
            self._current = step
            while not self._stopped and self._target is not None and step >= self._target:
                self._cond.wait()
            return not self._stopped

    def set_target(self, target: int | None) -> None:
        """Permit episodes below ``target`` (``None`` = run freely)."""
        with self._cond:
            self._target = None if target is None else max(0, int(target))
            self._cond.notify_all()

    def stop(self) -> None:
        """Release the loop and end it at the next boundary."""
        with self._cond:
            self._stopped = True
            self._cond.notify_all()

    @property
    def current(self) -> int:
        """The episode index the loop is currently gating on."""
        with self._cond:
            return self._current

    def snapshot(self) -> dict[str, Any]:
        """Return a read-only view of the gate's state (for status/telemetry)."""
        with self._cond:
            paused = (
                not self._stopped and self._target is not None and self._current >= self._target
            )
            return {
                "current": self._current,
                "target": self._target,
                "stopped": self._stopped,
                "paused": paused,
            }


@runtime_checkable
class RunController(Protocol):
    """Drives a :class:`StepGate` from some input source (stdin, a file, ...)."""

    def start(self) -> None:
        """Begin translating the input source into gate transitions."""
        ...

    def close(self) -> None:
        """Stop the controller (the loop and gate are unaffected)."""
        ...


_STDIN_HELP = (
    "[run control] n/next [k] = advance k episodes | c/continue = run freely | "
    "p/pause = hold | s/stop = end run | h = help"
)


class StdinController:
    """Read terminal commands and translate them into gate transitions."""

    def __init__(self, gate: StepGate) -> None:
        self._gate = gate
        self._closed = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Print the command help and start the stdin reader thread."""
        print(_STDIN_HELP, flush=True)
        self._thread = threading.Thread(target=self._run, daemon=True, name="run-control-stdin")
        self._thread.start()

    def _run(self) -> None:
        for raw in sys.stdin:
            if self._closed.is_set():
                return
            if _apply_stdin_command(self._gate, raw):
                return
        # EOF (piped/closed stdin): release so a non-interactive run never hangs.
        self._gate.set_target(None)

    def close(self) -> None:
        """Signal the reader thread to stop after its current line."""
        self._closed.set()


def _apply_stdin_command(gate: StepGate, line: str) -> bool:
    """Apply one terminal command to ``gate``. Return ``True`` to stop reading."""
    parts = line.strip().split()
    command = parts[0].lower() if parts else "n"
    if command in {"n", "next", "step"}:
        count = _positive_int(parts[1]) if len(parts) > 1 else 1
        gate.set_target(gate.current + count)
    elif command in {"c", "continue", "play", "run"}:
        gate.set_target(None)
    elif command in {"p", "pause"}:
        gate.set_target(gate.current)
    elif command in {"s", "stop", "q", "quit"}:
        gate.stop()
        return True
    elif command in {"h", "help", "?"}:
        print(_STDIN_HELP, flush=True)
    return False


class ControlFileController:
    """Poll a JSON control file and apply ``{"target", "stopped"}`` to the gate."""

    def __init__(self, gate: StepGate, path: str | Path, *, poll_interval: float = 0.3) -> None:
        self._gate = gate
        self._path = Path(path)
        self._poll = max(0.05, float(poll_interval))
        self._closed = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Discard any stale control file, then start the poller thread.

        The file is a live command channel owned by this run: a leftover from a
        previous process reusing the same path (e.g. a stopped interactive run
        relaunched into the same output dir) must not leak into a fresh launch —
        a stale ``{"stopped": true}`` would otherwise end the new run before
        episode 0. Pre-seed a held start via ``start_paused`` config instead.

        Staleness is judged by mtime against this process's start: a command
        written AFTER the process spawned is a launcher (e.g. Studio) pressing
        play/pause while engine startup is still importing — that command is for
        this run and must survive, not be silently discarded.
        """
        try:
            if self._path.exists():
                try:
                    started = psutil.Process().create_time()
                    stale = self._path.stat().st_mtime < started - 1.0
                except psutil.Error:
                    stale = True  # masked /proc: cannot date the file, treat as stale
                # A stop command is never preserved across startup regardless of
                # age: losing a raced end-run press is benign (press again), while
                # a leftover ``{"stopped": true}`` surviving clock skew would end
                # the new run before episode 0.
                if stale or bool((self._read() or {}).get("stopped")):
                    self._path.unlink(missing_ok=True)
        except OSError:
            pass  # unwritable path: the poller below will simply read nothing
        self._thread = threading.Thread(target=self._run, daemon=True, name="run-control-file")
        self._thread.start()

    def _run(self) -> None:
        while not self._closed.wait(self._poll):
            if self._apply(self._read()):
                return  # stop requested; the loop will end at its next boundary

    def _read(self) -> Mapping[str, Any] | None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, Mapping) else None

    def _apply(self, data: Mapping[str, Any] | None) -> bool:
        """Apply ``target`` and ``stopped`` independently: a malformed field is
        skipped without blocking the other, so a stop request always lands.
        """
        if data is None:
            return False
        if "target" in data:
            target = data["target"]
            if target is None or (isinstance(target, int) and not isinstance(target, bool)):
                self._gate.set_target(target)
        if data.get("stopped"):
            self._gate.stop()
            return True
        return False

    def close(self) -> None:
        """Signal the poller thread to stop at its next tick."""
        self._closed.set()


def _positive_int(text: str) -> int:
    try:
        return max(1, int(text))
    except (TypeError, ValueError):
        return 1


def build_run_control(
    control_cfg: Mapping[str, Any] | None,
    *,
    output_dir: str | Path,
) -> tuple[StepGate | None, RunController | None]:
    """Build the run-control gate and controller from ``sim.engine.control``.

    Returns ``(None, None)`` for the default ``none`` mode, so the engine gets no
    gate and the loop runs uninterrupted. ``stdin`` and ``control_file`` each
    return a gate plus its driving controller; the caller attaches the gate to
    the engine, calls ``controller.start()`` before the loop, and
    ``controller.close()`` in its ``finally``.
    """
    if not isinstance(control_cfg, Mapping):
        return None, None
    mode = str(control_cfg.get("built_in") or "none").strip()
    class_path = str(control_cfg.get("class_path") or "").strip()
    if not class_path and mode == "none":
        return None, None
    start_paused = bool(control_cfg.get("start_paused"))
    gate = StepGate(target=0 if start_paused else None)
    if class_path:
        # Custom controller (any transport — HTTP, message queue, ...): a class
        # constructed with the shared gate plus its params, driving the run
        # through the gate's primitives exactly like the built-ins. Must expose
        # start()/close(), checked here rather than as an AttributeError mid-run.
        from silisocs.runtime.class_loading import (
            instantiate_with_supported_kwargs,
            load_class,
        )

        controller = instantiate_with_supported_kwargs(
            load_class(class_path, what="run controller"),
            dict(control_cfg.get("params") or {}),
            args=(gate,),
            config_path="sim.engine.control.params",
        )
        for required in ("start", "close"):
            if not callable(getattr(controller, required, None)):
                raise TypeError(
                    f"sim.engine.control.class_path {class_path!r} must define "
                    f"a callable {required}() (the engine starts it before the "
                    "loop and closes it in the run's finally)."
                )
        return gate, controller
    if mode == "stdin":
        return gate, StdinController(gate)
    if mode == "control_file":
        path = str(control_cfg.get("control_file") or "").strip() or str(
            Path(output_dir) / "run.control"
        )
        poll = float(control_cfg.get("poll_interval") or 0.3)
        return gate, ControlFileController(gate, path, poll_interval=poll)
    raise ValueError(
        f"Unknown sim.engine.control.built_in={mode!r}; expected none | stdin | control_file."
    )
