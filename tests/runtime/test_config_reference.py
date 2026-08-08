"""Drift check: docs/config_reference.md must match the packaged defaults."""

from __future__ import annotations

from silisocs.runtime.configuration.config_reference import (
    generate_config_reference,
    reference_path,
)


def test_config_reference_doc_matches_packaged_defaults() -> None:
    """Regenerate the reference and compare to the checked-in doc.

    On failure, run ``python -m silisocs.runtime.configuration.config_reference``
    and commit the updated docs/config_reference.md.
    """
    target = reference_path()
    assert target.is_file(), (
        "docs/config_reference.md is missing; regenerate with "
        "python -m silisocs.runtime.configuration.config_reference"
    )
    assert target.read_text(encoding="utf-8") == generate_config_reference(), (
        "docs/config_reference.md is stale relative to the packaged config "
        "defaults; regenerate with "
        "python -m silisocs.runtime.configuration.config_reference and commit it."
    )


def test_config_reference_covers_all_groups() -> None:
    document = generate_config_reference()
    for section in ("## world", "## agents", "## sim", "## env (twitter_like)", "## eval"):
        assert section in document
    assert "`sim.llm.provider`" in document
    assert "`num_agents`" in document
