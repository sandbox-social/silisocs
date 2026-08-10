"""``silisocs-config-dry-run`` must be usable on ONE scenario.

It is the highest-signal tool in the authoring loop — it builds the real runtime
rather than only composing YAML — but it could only sweep a whole checkout via
``--project-root``, so the person writing a single scenario had no way to point
it at their work, and an empty directory failed with a message that named
neither flag.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from silisocs.runtime.config_dry_run import _resolve_config_path, main, run_dry_runs

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_config_path_accepts_the_conf_dir_and_the_scenario_root(tmp_path: Path) -> None:
    conf = tmp_path / "my_world" / "conf"
    (conf / "world").mkdir(parents=True)

    assert _resolve_config_path(str(conf)) == conf.resolve()
    # Users have the scenario root in hand as often as the conf dir.
    assert _resolve_config_path(str(conf.parent)) == conf.resolve()


def test_no_targets_message_names_both_modes(tmp_path: Path, capsys) -> None:
    exit_code = main(["--project-root", str(tmp_path)])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "--config-path" in out
    assert "--project-root" in out


def test_config_path_without_world_files_reports_the_requirement(tmp_path: Path, capsys) -> None:
    empty = tmp_path / "conf"
    empty.mkdir()

    exit_code = main(["--config-path", str(empty)])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "world/*.yaml" in out


def test_config_path_that_is_not_a_directory_fails_actionably(tmp_path: Path, capsys) -> None:
    exit_code = main(["--config-path", str(tmp_path / "nope")])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "is not a directory" in out


@pytest.mark.subprocess
def test_config_path_validates_exactly_one_bundled_scenario() -> None:
    """One scenario, the same checks the whole-repo sweep runs on it."""
    conf = REPO_ROOT / "scenarios" / "misinformation" / "conf"

    results = run_dry_runs(REPO_ROOT, config_path=conf)

    assert results, "no targets discovered for a bundled scenario"
    assert all(r.target.config_path == conf for r in results)
    assert all(r.target.label.startswith("misinformation/") for r in results)
    failures = [
        f"{r.target.label}: {(r.stderr or r.stdout).strip()[-2000:]}" for r in results if not r.ok
    ]
    assert not failures, "\n".join(failures)


@pytest.mark.subprocess
def test_config_path_cli_exits_zero_on_a_bundled_scenario(capsys) -> None:
    exit_code = main(["--config-path", str(REPO_ROOT / "scenarios" / "misinformation" / "conf")])

    out = capsys.readouterr().out
    assert exit_code == 0, out
    assert "0 failed" in out
