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

REPO_ROOT = Path(__file__).resolve().parents[2]

# Every JS file the wheel is expected to carry, asserted explicitly so a
# packaging change (a new asset, a dropped one, a vendored bundle moved out of
# the base wheel) has to be made deliberately rather than noticed in production.
#
# `plotly.min.js`/`cytoscape.min.js` are vendored third-party bundles and are
# ~1.7 MB of the wheel, but they stay in the BASE wheel on purpose: package-data
# cannot be conditioned on an extra, and `analysis/report.py` inlines both into
# the self-contained static report, which is not behind the `analysis` extra.
EXPECTED_STUDIO_JS = {
    "boot.js",
    "composer.js",
    "cytoscape.min.js",
    "explore.js",
    "panels.js",
    "plotly.min.js",
    "runs.js",
    "scenario.js",
    "studio.js",
    "study.js",
}


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

    prefix = "silisocs/studio/static/"
    shipped_js = {
        name.rsplit("/", 1)[1] for name in names if prefix in name and name.endswith(".js")
    }
    assert shipped_js == EXPECTED_STUDIO_JS, (
        "packaged Studio JS changed; update EXPECTED_STUDIO_JS if intended. "
        f"extra={sorted(shipped_js - EXPECTED_STUDIO_JS)} "
        f"missing={sorted(EXPECTED_STUDIO_JS - shipped_js)}"
    )
