"""Error-path test for the study runner's subprocess timeout handling.

``_run_subprocess`` must kill a run that overruns its timeout and report it with
``PROCESS_TIMEOUT_RC`` (124) and a TIMEOUT marker in the captured tail, rather than
hanging or crashing the study.
"""

from __future__ import annotations

import sys

from silisocs.studies.execute import PROCESS_TIMEOUT_RC, _run_subprocess


def test_run_subprocess_times_out_and_reports_rc_124(tmp_path) -> None:
    # A genuine no-output hang: the child keeps stdout OPEN and emits nothing, so
    # the blocking stdout read never reaches EOF. The timeout must still fire
    # (enforced via proc.wait on the main thread while a reader thread drains).
    script = "import time; time.sleep(30)"
    rc, tail, parsed_dir = _run_subprocess(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        log_path=tmp_path / "run.log",
        timeout_seconds=1,
    )

    assert rc == PROCESS_TIMEOUT_RC == 124
    assert any("TIMEOUT" in line for line in tail)
    assert parsed_dir is None


def test_run_subprocess_returns_zero_on_success(tmp_path) -> None:
    rc, tail, _ = _run_subprocess(
        [sys.executable, "-c", "print('hello from run')"],
        cwd=tmp_path,
        log_path=tmp_path / "ok.log",
        timeout_seconds=30,
    )
    assert rc == 0
    assert any("hello from run" in line for line in tail)
