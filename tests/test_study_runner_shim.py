"""Back-compat: the legacy experiments.run_study entry points still resolve."""

from __future__ import annotations


def test_experiments_shim_reexports_canonical_symbols() -> None:
    import experiments.run_study as shim

    import silisocs.studies.run_study as canonical

    # Public API used by SLURM templates / external scripts.
    for name in ("main", "RUN_COMPLETE_MARKER", "RunSpec", "_expand_runs"):
        assert getattr(shim, name) is getattr(canonical, name)


def test_artifacts_shim_reexports_canonical_symbols() -> None:
    import experiments._internal.study_artifacts as shim

    import silisocs.studies.study_artifacts as canonical

    for name in ("organize_study_outputs", "load_study_definition", "build_summary"):
        assert getattr(shim, name) is getattr(canonical, name)


def test_study_template_is_locatable() -> None:
    from silisocs.studies.templates import study_template_path

    template = study_template_path()
    assert template.is_dir()
    assert (template / "study.yaml").is_file()
