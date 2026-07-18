"""Regression tests for the analysis-surface review fixes.

Covers: top-level ``estimated_cost_usd`` pricing in TokenUsagePanel, the
``serialize_controls`` helper shared by views/app, ``build_view`` skipped-panel
enrichment (requires/missing) that lets the Watch shell revive a panel live, and
the static report rendering panel output through the shared ``_output.html`` macro
(grid-of-markdown -> stat tiles).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from silisocs.analysis.panel import (
    Control,
    Grid,
    Markdown,
    Panel,
    register_panel,
    serialize_controls,
)
from silisocs.analysis.panels.metrics import TokenUsagePanel
from silisocs.analysis.report import render_report
from silisocs.analysis.views import build_view, parse_view
from silisocs.evaluations.run_artifact import load_run


def _write_run(run_dir: Path, *, llm_usage: dict[str, Any] | None = None) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "action_events.jsonl").write_text(
        json.dumps(
            {
                "source_user": "Alex",
                "label": "post",
                "episode": 0,
                "backend_type": "twitter_like",
                "data": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    manifest: dict[str, Any] = {
        "status": "success",
        "scenario": "demo",
        "game_masters": [{"name": "gm", "backend_type": "twitter_like"}],
        "artifacts": {"action_events": ["action_events.jsonl"]},
    }
    if llm_usage is not None:
        manifest["llm_usage"] = llm_usage
    (run_dir / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return run_dir


def test_top_level_estimated_cost_renders_cost_tile(tmp_path: Path) -> None:
    """A priced run writes ``estimated_cost_usd`` at the llm_usage TOP level."""
    usage = {
        "totals": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60},
        "estimated_cost_usd": 1.23,
        "pricing_applied": True,
    }
    run = _write_run(tmp_path / "run", llm_usage=usage)
    output = TokenUsagePanel().build(load_run(run), {})

    assert isinstance(output, Grid)
    text = " ".join(item.text for item in output.items if isinstance(item, Markdown))
    assert "Estimated cost" in text
    assert "$1.23" in text


def test_top_level_estimated_cost_annotates_per_model_figure(tmp_path: Path) -> None:
    usage = {
        "per_model": [
            {"model": "gpt-4o-mini", "prompt_tokens": 100, "completion_tokens": 20},
        ],
        "totals": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        "estimated_cost_usd": 4.5,
        "pricing_applied": True,
    }
    run = _write_run(tmp_path / "run", llm_usage=usage)
    output = TokenUsagePanel().build(load_run(run), {})

    from silisocs.analysis.panel import Figure

    assert isinstance(output, Figure)
    (annotation,) = output.figure["layout"]["annotations"]
    assert "Estimated cost" in annotation["text"]
    assert "4.50" in annotation["text"]


def test_serialize_controls_shape() -> None:
    @register_panel
    class _ControlledPanel(Panel):
        name = "test_controlled"
        title = "Controlled"
        scope = "run"
        controls = (Control(kind="select", param="mode", choices=("a", "b")),)

        def build(self, artifact, params):
            return Markdown("ok")

    serialized = serialize_controls(_ControlledPanel, {"mode": "b"})
    assert serialized == [
        {
            "kind": "select",
            "param": "mode",
            "label": "Mode",  # falls back to param.title() when label is unset
            "choices": ["a", "b"],
            "value": "b",
        }
    ]


def test_build_view_drops_dead_missing_key_on_built_panels(tmp_path: Path) -> None:
    view = parse_view(
        {"name": "v", "scope": "run", "layout": "rows", "panels": [{"built_in": "action_trends"}]}
    )
    result = build_view(view, load_run(_write_run(tmp_path / "run")))
    (built,) = result["panels"]
    assert "missing" not in built


def test_build_view_enriches_skipped_for_live_refresh(tmp_path: Path) -> None:
    """A panel skipped for a missing event stream carries requires+missing so the
    Watch shell can render a placeholder and refresh it once the stream grows.
    """
    view = parse_view(
        {"name": "p", "scope": "run", "layout": "rows", "panels": [{"built_in": "probe_trends"}]}
    )
    result = build_view(view, load_run(_write_run(tmp_path / "run")))
    (skipped,) = result["skipped"]
    assert skipped["name"] == "probe_trends"
    assert "probe_events" in skipped["requires"]
    assert skipped["missing"] == ["probe_events"]


def test_report_renders_grid_markdown_as_stat_tiles(tmp_path: Path) -> None:
    """The export renders through _output.html, so grid-of-markdown emits stat tiles
    (not bare <p>), keeping every tile's value emphasized in the layout.
    """
    _write_run(
        tmp_path / "run",
        llm_usage={"totals": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60}},
    )
    document = render_report(tmp_path / "run", "cost")
    assert "stat-tile" in document
    assert "Total tokens" in document
