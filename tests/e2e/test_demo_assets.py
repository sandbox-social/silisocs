"""The checked-in product tours stay documented and reproducible."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VIDEO_NAMES = {
    "silisocs-cli-quickstart.mp4",
    "silisocs-studio-tour.mp4",
    "silisocs-create-scenario.mp4",
    "silisocs-design-study.mp4",
}
MIN_VIDEO_BYTES = 100_000


def test_product_tour_videos_are_real_and_documented() -> None:
    """Every published tour is a nontrivial MP4 referenced by docs and tooling."""
    docs = (PROJECT_ROOT / "docs" / "tutorials" / "studio_demo.md").read_text()
    demo_readme = (PROJECT_ROOT / "demo" / "README.md").read_text()
    pipeline = (PROJECT_ROOT / "demo" / "run_all.sh").read_text()

    for name in VIDEO_NAMES:
        video = PROJECT_ROOT / "docs" / "assets" / "videos" / name
        assert video.stat().st_size > MIN_VIDEO_BYTES
        assert b"ftyp" in video.read_bytes()[:32]
        assert name in docs
        assert name in demo_readme
        assert name in pipeline


def test_every_product_tour_has_reproducible_source() -> None:
    """Each product tour keeps its recorder, configuration, and example source."""
    expected_sources = {
        "demo/tapes/cli.yaml",
        "demo/tapes/study.yaml",
        "demo/record_cli.mjs",
        "demo/record_studio.mjs",
        "demo/record_create_scenario.mjs",
        "scenarios/campus_rumor/conf/world/default.yaml",
        "experiments/studies/rumor_pressure_demo/study.yaml",
        "experiments/studies/rumor_pressure_demo/eval.py",
    }

    assert not [path for path in expected_sources if not (PROJECT_ROOT / path).is_file()]
