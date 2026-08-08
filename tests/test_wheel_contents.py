"""The wheel ships the engine + base config and nothing else.

The repo/wheel split (AGENTS.md §3, scenario_library docstring) was previously
enforced only by packaging config nobody asserted; this test builds the real
wheel and checks the invariant, so a packaging regression fails CI instead of
shipping.
"""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.subprocess
def test_wheel_ships_engine_and_config_but_not_repo_content(tmp_path):
    if shutil.which("uv") is None:
        pytest.skip("uv is not available to build the wheel")
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
    )
    wheel = next(tmp_path.glob("silisocs-*.whl"))
    names = zipfile.ZipFile(wheel).namelist()

    # Repository content must never ship: example scenarios, studies, demo
    # pipeline, docs, tests, outputs, env files.
    for forbidden in ("scenarios/", "experiments/", "demo/", "docs/", "tests/", "outputs/"):
        assert not any(name.startswith(forbidden) for name in names), forbidden
    assert not any(name.endswith(".env") for name in names)

    # The engine, packaged base config, and Studio assets must ship.
    assert any(name.endswith("silisocs/conf/experiment.yaml") for name in names)
    assert any(name.endswith("silisocs/conf/sim/base.yaml") for name in names)
    assert any("silisocs/studio/templates/base.html" in name for name in names)
    assert any("silisocs/studio/static/studio.css" in name for name in names)
