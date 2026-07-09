"""Tests for the dashboard console entry points (friendly missing-extra errors)."""

from __future__ import annotations

import sys
import types


def test_dashboard_cli_missing_streamlit_prints_install_hint(monkeypatch, capsys) -> None:
    monkeypatch.setitem(sys.modules, "streamlit", None)  # forces ImportError
    monkeypatch.setitem(sys.modules, "streamlit.web", None)
    from silisocs.dashboard import cli

    assert cli.main() == 1
    assert 'pip install "silisocs[dashboard]"' in capsys.readouterr().err


def test_dashboard_cli_invokes_streamlit_run(monkeypatch) -> None:
    seen_argv: list[str] = []

    def _fake_streamlit_main() -> int:
        seen_argv.extend(sys.argv)
        return 0

    fake_cli = types.ModuleType("streamlit.web.cli")
    fake_cli.main = _fake_streamlit_main  # type: ignore[attr-defined]
    fake_web = types.ModuleType("streamlit.web")
    fake_web.cli = fake_cli  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "streamlit", types.ModuleType("streamlit"))
    monkeypatch.setitem(sys.modules, "streamlit.web", fake_web)
    monkeypatch.setitem(sys.modules, "streamlit.web.cli", fake_cli)
    monkeypatch.setattr(sys, "argv", ["silisocs-dashboard", "--server.port", "8600"])
    from silisocs.dashboard import cli

    assert cli.main() == 0
    assert seen_argv[:2] == ["streamlit", "run"]
    assert seen_argv[2].endswith("launch_app.py")
    assert seen_argv[3:] == ["--server.port", "8600"]  # extra args forwarded


def test_analysis_cli_missing_dash_prints_install_hint(monkeypatch, capsys) -> None:
    monkeypatch.setitem(sys.modules, "dash", None)  # forces ImportError
    from silisocs.evaluations.analysis.dashboard import cli

    assert cli.main() == 1
    assert 'pip install "silisocs[analysis]"' in capsys.readouterr().err


def test_analysis_cli_delegates_to_dashboard_main(monkeypatch) -> None:
    called: list[bool] = []
    fake_main = types.ModuleType("silisocs.evaluations.analysis.dashboard.main")
    fake_main.main = lambda: called.append(True)  # type: ignore[attr-defined]
    for name in ("dash", "dash_cytoscape", "plotly"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "silisocs.evaluations.analysis.dashboard.main", fake_main)
    from silisocs.evaluations.analysis.dashboard import cli

    assert cli.main() == 0
    assert called == [True]
