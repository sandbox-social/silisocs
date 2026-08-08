"""Browser smoke: Studio boots, launches an offline run, and renders its tabs.

The HTTP e2e (``test_studio_e2e_demo.py``) proves the control plane; nothing
proved the *pages* still execute. A dropped ``<script>`` tag, a renamed global,
or a syntax error in one of the extracted ``static/*.js`` modules leaves every
HTTP assertion green while the workspace is dead in a browser.

So this drives a real Chromium through the shortest first-run path — home ->
scenarios -> scenario editor -> preflight -> UI launch -> live page -> the
finished run's Overview/Watch/Analyze tabs — and fails on ANY ``console.error``
or uncaught page error on any page it visits.

Two deliberate choices keep it CI-shaped:

* **No new Python dependency.** The browser is driven by ``demo/smoke_studio.mjs``
  through demo's already-pinned ``playwright-core``; this module owns the server
  and the workspace and shells out.
* **Skip, never fail, without a browser.** The binary is resolved from
  ``SILISOCS_SMOKE_CHROME``, then ``CHROME``, then ``demo/chrome-wrapper.sh``,
  and each candidate must actually answer ``--version``.

The run itself is offline: the scenario is copied into a temp workspace with the
``scripted`` model provider and ``num_steps=2`` (the same override set the HTTP
e2e uses), so the smoke needs no API key and finishes in seconds.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_DIR = REPO_ROOT / "demo"
SMOKE_SCRIPT = DEMO_DIR / "smoke_studio.mjs"
SCENARIO = "misinformation"

pytestmark = [pytest.mark.browser, pytest.mark.subprocess]


def _runnable(command: list[str]) -> bool:
    """Whether a candidate browser answers ``--version`` on this host."""
    try:
        done = subprocess.run(
            [*command, "--version"],
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def _resolve_chrome() -> str | None:
    """First working browser in the documented resolution order."""
    candidates = [
        os.environ.get("SILISOCS_SMOKE_CHROME"),
        os.environ.get("CHROME"),
        str(DEMO_DIR / "chrome-wrapper.sh"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if not (path.is_file() and os.access(path, os.X_OK)):
            continue
        if _runnable([str(path)]):
            return str(path)
    return None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _offline_workspace(root: Path) -> Path:
    """Build a temp repo holding one scenario patched to run offline in 2 steps."""
    source = REPO_ROOT / "scenarios" / SCENARIO
    target = root / "scenarios" / SCENARIO
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)

    world = target / "conf" / "world" / "default.yaml"
    # Line-edited, not YAML round-tripped: the file's `# @package _global_`
    # header is semantic to Hydra and a safe_dump would drop it.
    patched, count = re.subn(
        r"^num_steps:.*$", "num_steps: 2", world.read_text(encoding="utf-8"), flags=re.MULTILINE
    )
    assert count == 1, f"{world} no longer declares num_steps"
    world.write_text(patched, encoding="utf-8")

    sim = target / "conf" / "sim.yaml"
    assert not re.search(r"^llm:", sim.read_text(encoding="utf-8"), flags=re.MULTILINE), (
        f"{sim} already sets llm — the smoke's provider override would be ambiguous"
    )
    # Studio's launch button sends no overrides, so "offline" has to live in the
    # scenario the button launches.
    with sim.open("a", encoding="utf-8") as handle:
        handle.write("\nllm:\n  provider: scripted\n")
    return root


def _wait_until_ready(base_url: str, process: subprocess.Popen, timeout: float) -> None:
    deadline = time.time() + timeout
    last: Exception | str = "no response"
    while time.time() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"studio exited early with code {process.returncode}")
        try:
            with urllib.request.urlopen(f"{base_url}/api/ready", timeout=5) as response:
                if json.load(response).get("ready"):
                    return
                last = "warming"
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last = exc
        time.sleep(0.2)
    raise AssertionError(f"studio never became ready ({last})")


@pytest.fixture(scope="module")
def chrome() -> str:
    binary = _resolve_chrome()
    if binary is None:
        pytest.skip(
            "No usable browser: set SILISOCS_SMOKE_CHROME or CHROME to a Chromium "
            "binary (or configure demo/chrome-wrapper.sh via SILISOCS_DEMO_CHROME)."
        )
    if shutil.which("node") is None:
        pytest.skip("node is not on PATH; the browser smoke is driven by demo/smoke_studio.mjs")
    if not (DEMO_DIR / "node_modules" / "playwright-core").is_dir():
        pytest.skip("playwright-core is not installed; run `npm install` in demo/")
    return binary


@pytest.fixture(scope="module")
def studio(tmp_path_factory):
    """Serve Studio on a random port over a temp offline workspace."""
    tmp_path = tmp_path_factory.mktemp("studio_browser")
    workspace = _offline_workspace(tmp_path / "repo")
    port = _free_port()
    log_path = tmp_path / "studio.log"
    command = [
        sys.executable,
        "-c",
        "import sys; from silisocs.studio.cli import main; sys.exit(main())",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--repo-root",
        str(workspace),
        "--output-root",
        str(tmp_path / "outputs"),
        "--state-dir",
        str(tmp_path / "state"),
    ]
    with log_path.open("w", encoding="utf-8") as handle:
        process = subprocess.Popen(command, cwd=workspace, stdout=handle, stderr=subprocess.STDOUT)
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_until_ready(base_url, process, timeout=90)
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            process.kill()
        print(f"\n--- studio server log ---\n{log_path.read_text(encoding='utf-8')[-4000:]}")


def test_studio_pages_boot_and_a_launched_run_renders(chrome, studio, capfd):
    """Every page a first-run user touches executes without a JS error."""
    done = subprocess.run(
        ["node", str(SMOKE_SCRIPT)],
        cwd=DEMO_DIR,
        env={
            **os.environ,
            "STUDIO_URL": studio,
            "CHROME": chrome,
            "SMOKE_SCENARIO": SCENARIO,
        },
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    with capfd.disabled():
        print(done.stdout)
        if done.stderr:
            print(done.stderr, file=sys.stderr)
    assert done.returncode == 0, f"browser smoke failed\n{done.stdout}\n{done.stderr}"
