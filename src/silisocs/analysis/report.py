"""Static HTML report export for declarative analysis views."""

from __future__ import annotations

import argparse
import base64
import html
from functools import lru_cache
from pathlib import Path
from typing import Any

import jinja2

from silisocs.analysis.views import View, build_view, load_view
from silisocs.design.css import css_variables
from silisocs.evaluations.run_artifact import load_run, load_study

_STUDIO_TEMPLATES = Path(__file__).parents[1] / "studio" / "templates"


@lru_cache(maxsize=1)
def _output_macros() -> Any:
    """Return the Studio ``_output.html`` macro module — the one PanelOutput renderer.

    Rendering the export through the same Jinja macro Studio uses keeps the two
    surfaces in lockstep by construction: a new output type or a layout fix (e.g.
    grid-of-markdown tiles) lands on both at once instead of drifting.
    """
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_STUDIO_TEMPLATES)),
        autoescape=True,
    )
    return env.get_template("_output.html").module


def _render_output(output: dict[str, Any]) -> str:
    """Render a PanelOutput dict through the shared ``_output.html`` macro."""
    return str(_output_macros().render_output(output))


def _plotly_script() -> str:
    """Inline the Plotly bundle vendored with Studio."""
    bundle = Path(__file__).parents[1] / "studio" / "static" / "plotly.min.js"
    return f"<script>{bundle.read_text(encoding='utf-8')}</script>"


def _cytoscape_script() -> str:
    """Inline the Cytoscape bundle vendored with Studio."""
    bundle = Path(__file__).parents[1] / "studio" / "static" / "cytoscape.min.js"
    return f"<script>{bundle.read_text(encoding='utf-8')}</script>"


@lru_cache(maxsize=1)
def _font_data_uri() -> str:
    """Return the bundled product font as a self-contained report asset."""
    font = Path(__file__).parents[1] / "design" / "fonts" / "manrope-latin-variable.woff2"
    payload = base64.b64encode(font.read_bytes()).decode("ascii")
    return f"data:font/woff2;base64,{payload}"


def render_report(
    artifact_dir: str | Path,
    view_name: str | Path | View = "overview",
) -> str:
    """Build one report document (fully self-contained when plotly is installed)."""
    view = view_name if isinstance(view_name, View) else load_view(view_name)
    artifact = load_run(artifact_dir) if view.scope == "run" else load_study(artifact_dir)
    built = build_view(view, artifact)
    cards = "".join(
        f'<section class="panel"><header><h2>{html.escape(panel["title"])}</h2>'
        f"{_controls_caption(panel)}</header>{_render_output(panel['output'])}</section>"
        for panel in built["panels"]
    )
    cards += _skipped_caption(built)
    artifact_path = html.escape(str(Path(artifact_dir)))
    title = html.escape(built["title"])
    return f"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>{title} | Silisocs Studio</title>{_plotly_script()}{_cytoscape_script()}<style>{_REPORT_CSS}</style></head><body><main><header class=report-top><div class=brand><span class=brand-mark aria-hidden=true><i></i><i></i><i></i></span><span><b>Silisocs</b><small>Analysis artifact</small></span></div><div class=report-signals aria-hidden=true><i></i><i></i><i></i><i></i></div></header><section class=heading><p>SIMULATION VIEW</p><h1>{title}</h1><small>{artifact_path}</small></section><div class=grid>{cards}</div></main><script>document.querySelectorAll('.plot').forEach(el=>{{const f=JSON.parse(el.dataset.figure);Plotly.newPlot(el,f.data||[],f.layout||{{}},{{responsive:true,displaylogo:false,modeBarButtonsToRemove:['lasso2d','select2d']}})}});const cssVar=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();const resolveCyStyle=s=>(s||[]).map(r=>({{...r,style:Object.fromEntries(Object.entries(r.style||{{}}).map(([k,v])=>[k,typeof v==='string'&&v.startsWith('var(')?(cssVar(v.slice(4,-1))||v):v]))}}));document.querySelectorAll('[data-cy-graph]').forEach(el=>{{const g=JSON.parse(el.dataset.cyGraph);cytoscape({{container:el,elements:g.elements||[],style:resolveCyStyle(g.style||[]),layout:{{name:'preset'}}}})}})</script></body></html>"""


def _skipped_caption(built: dict[str, Any]) -> str:
    """Name the panels this run could not populate, so the report is honest about gaps."""
    skipped = built.get("skipped") or []
    if not skipped:
        return ""
    items = "; ".join(
        f"{html.escape(str(panel['title']))} ({html.escape(str(panel['reason']))})"
        for panel in skipped
    )
    return f'<section class="panel skipped"><small>Not shown: {items}.</small></section>'


def _controls_caption(panel: dict[str, Any]) -> str:
    controls = panel.get("controls") or []
    if not controls:
        return ""
    labels = ", ".join(str(control.get("label") or control.get("param")) for control in controls)
    return f'<small class="controls-caption">Default view; interactive controls: {html.escape(labels)}</small>'


_REPORT_CSS = (
    css_variables(font_url=_font_data_uri())
    + """*{box-sizing:border-box;letter-spacing:0}html{background:var(--canvas);color:var(--ink)}body{margin:0;background:var(--canvas);color:var(--ink);font:14px/1.5 var(--font)}main{max-width:1320px;margin:auto;padding:28px 32px 64px}.report-top{display:flex;align-items:center;justify-content:space-between;padding-bottom:20px;border-bottom:1px solid var(--border)}.brand{display:flex;align-items:center;gap:10px}.brand>span:last-child,.brand b,.brand small{display:block}.brand b{font:750 16px var(--font-display)}.brand small{color:var(--muted);font-size:9px;font-weight:700;text-transform:uppercase}.brand-mark{position:relative;display:block;width:38px;height:38px}.brand-mark i{position:absolute;width:25px;height:17px;border:2px solid var(--accent);border-radius:7px;transform:rotate(30deg) skewX(-5deg)}.brand-mark i:nth-child(1){top:1px;left:6px;background:var(--accent-soft)}.brand-mark i:nth-child(2){top:9px;left:6px;border-color:var(--signal-violet)}.brand-mark i:nth-child(3){top:17px;left:6px;border-color:var(--signal-coral)}.report-signals{display:flex;align-items:flex-end;gap:4px;height:26px}.report-signals i{display:block;width:5px;border-radius:var(--radius-pill);background:var(--accent);height:12px}.report-signals i:nth-child(2){height:24px;background:var(--signal-coral)}.report-signals i:nth-child(3){height:17px;background:var(--signal-violet)}.report-signals i:nth-child(4){height:21px;background:var(--signal-sky)}.heading{position:relative;margin:42px 0 30px;padding-left:20px}.heading:before{position:absolute;top:2px;bottom:1px;left:0;width:4px;border-radius:var(--radius-pill);background:var(--accent);content:""}.heading p{margin:0;font-size:var(--type-eyebrow);font-weight:800;color:var(--accent-link)}.heading h1{font:750 var(--type-h1)/1.1 var(--font-display);margin:8px 0 7px}.heading small,.controls-caption{color:var(--muted)}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.panel{min-width:0;overflow:hidden;border:1px solid var(--border);border-radius:var(--radius);background:var(--surface);box-shadow:var(--shadow-sm)}.panel header{padding:16px 20px;border-bottom:1px solid var(--border);background:color-mix(in srgb,var(--surface-2) 48%,var(--surface))}.panel h2{margin:0;font-size:var(--type-h3)}.controls-caption{display:block;margin-top:4px;font-size:var(--type-eyebrow)}.panel.skipped{grid-column:1/-1;padding:14px 20px;color:var(--muted)}.panel>.plot{height:360px}.cy-network{height:520px}.feed{max-height:600px;overflow:auto}.feed-post{padding:14px 18px;border-bottom:1px solid var(--border)}.feed-post:hover{background:var(--surface-2)}.feed-replies{margin:10px 0 0 12px;padding-left:14px;border-left:2px solid var(--border)}.feed-author{font-weight:700}.feed-meta{margin-left:8px;color:var(--muted);font-size:var(--type-eyebrow)}.table-scroll{overflow:auto}.table-scroll table{min-width:520px}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:11px 16px;border-bottom:1px solid var(--border)}th{background:var(--surface-2);color:var(--muted);font-size:var(--type-eyebrow);text-transform:uppercase}.panel-note{padding:20px;color:var(--muted)}.stat-grid{display:grid;grid-template-columns:repeat(5,1fr)}.stat-grid.rich{grid-template-columns:repeat(2,minmax(0,1fr))}.stat-grid.rich>:last-child:nth-child(odd){grid-column:1/-1}.stat-tile{position:relative;padding:22px;border-right:1px solid var(--border)}.stat-tile:before{position:absolute;inset:0 auto 0 0;width:3px;background:var(--accent);content:""}.stat-tile:nth-child(2):before{background:var(--signal-coral)}.stat-tile:nth-child(3):before{background:var(--signal-violet)}.stat-tile:nth-child(4):before{background:var(--signal-sky)}.stat-tile:nth-child(5):before{background:var(--signal-amber)}.stat-tile:last-child{border:0}.stat-tile span,.stat-tile strong{display:block}.stat-tile strong{font-size:24px;font-weight:750}.stat-tile span{margin-top:3px;color:var(--muted);font-size:var(--type-eyebrow);text-transform:uppercase}@media(max-width:760px){main{padding:20px 14px 40px}.heading{margin-top:30px}.heading h1{font-size:28px}.grid{grid-template-columns:1fr}.stat-grid{grid-template-columns:repeat(2,1fr)}.stat-grid.rich{grid-template-columns:1fr}.stat-grid.rich>:last-child:nth-child(odd){grid-column:auto}.panel>.plot{height:320px}}@media(prefers-reduced-motion:reduce){*,:before,:after{scroll-behavior:auto!important;animation-duration:0s!important;transition-duration:0s!important}}"""
)


def main() -> int:
    """Run the report-export command."""
    parser = argparse.ArgumentParser(description="Render a Silisocs artifact analysis view")
    parser.add_argument("artifact_dir")
    parser.add_argument("--view", default="overview")
    parser.add_argument("-o", "--output", default="report.html")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    document = render_report(args.artifact_dir, args.view)
    if not args.check:
        Path(args.output).write_text(document, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
