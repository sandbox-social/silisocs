"""Static HTML report export for declarative analysis views."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

from silisocs.analysis.views import View, build_view, load_view
from silisocs.design.css import css_variables
from silisocs.evaluations.run_artifact import load_run, load_study


def _render_output(output: dict[str, Any], element_id: str) -> str:
    kind = output["type"]
    if kind == "figure":
        payload = html.escape(json.dumps(output["figure"]), quote=True)
        return f'<div class="plot" id="{element_id}" data-figure="{payload}"></div>'
    if kind == "table":
        columns = [
            column if isinstance(column, str) else column.get("name", "")
            for column in output["columns"]
        ]
        head = "".join(
            f"<th>{html.escape(str(column).replace('_', ' ').title())}</th>" for column in columns
        )
        rows = "".join(
            "<tr>"
            + "".join(f"<td>{html.escape(str(row.get(column, '')))}</td>" for column in columns)
            + "</tr>"
            for row in output["rows"]
        )
        return f"<div class=table-wrap><table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div>"
    if kind == "markdown":
        lines = output["text"].splitlines()
        return "".join(f"<p>{html.escape(line).replace('**', '')}</p>" for line in lines)
    if kind == "html":
        return output["html"]
    return (
        '<div class="stat-grid">'
        + "".join(
            _render_output(item, f"{element_id}-{idx}") for idx, item in enumerate(output["items"])
        )
        + "</div>"
    )


def _plotly_script() -> str:
    """Inline the Plotly bundle vendored with Studio."""
    bundle = Path(__file__).parents[1] / "studio" / "static" / "plotly.min.js"
    return f"<script>{bundle.read_text(encoding='utf-8')}</script>"


def _cytoscape_script() -> str:
    """Inline the Cytoscape bundle vendored with Studio."""
    bundle = Path(__file__).parents[1] / "studio" / "static" / "cytoscape.min.js"
    return f"<script>{bundle.read_text(encoding='utf-8')}</script>"


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
        f"{_controls_caption(panel)}</header>{_render_output(panel['output'], f'panel-{idx}')}</section>"
        for idx, panel in enumerate(built["panels"])
    )
    return f"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>{html.escape(built["title"])} | Silisocs Studio</title>{_plotly_script()}{_cytoscape_script()}<style>{_REPORT_CSS}</style></head><body><main><div class=brand><span>SI</span> SILISOCS STUDIO</div><div class=heading><p>ANALYSIS VIEW</p><h1>{html.escape(built["title"])}</h1><small>{html.escape(str(Path(artifact_dir)))}</small></div><div class=grid>{cards}</div></main><script>document.querySelectorAll('.plot').forEach(el=>{{const f=JSON.parse(el.dataset.figure);Plotly.newPlot(el,f.data||[],f.layout||{{}},{{responsive:true,displaylogo:false}})}});document.querySelectorAll('[data-cy-graph]').forEach(el=>{{const g=JSON.parse(el.dataset.cyGraph);cytoscape({{container:el,elements:g.elements||[],style:g.style||[],layout:{{name:'preset'}}}})}})</script></body></html>"""


def _controls_caption(panel: dict[str, Any]) -> str:
    controls = panel.get("controls") or []
    if not controls:
        return ""
    labels = ", ".join(str(control.get("label") or control.get("param")) for control in controls)
    return f'<small class="controls-caption">Default view; interactive controls: {html.escape(labels)}</small>'


_REPORT_CSS = (
    css_variables()
    + """*{box-sizing:border-box;letter-spacing:0}body{margin:0;background:var(--canvas);color:var(--ink);font:14px var(--font)}main{max-width:1280px;margin:auto;padding:32px}.brand{font-size:12px;font-weight:800}.brand span{display:inline-grid;place-items:center;width:30px;height:30px;background:var(--accent);color:var(--on-strong);margin-right:10px}.heading{margin:52px 0 28px}.heading p{font-size:11px;font-weight:800;color:var(--accent-link)}.heading h1{font:600 36px var(--font-display);margin:6px 0}.heading small,.controls-caption{color:var(--muted)}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.panel{background:var(--surface);border:1px solid var(--border);border-radius:6px;min-width:0;overflow:hidden}.panel header{padding:16px 20px;border-bottom:1px solid var(--border)}.panel h2{font-size:14px;margin:0}.controls-caption{display:block;font-size:10px;margin-top:4px}.panel>.plot{height:360px}.cy-network{height:520px}.feed{max-height:600px;overflow:auto}.feed-post{padding:14px 18px;border-bottom:1px solid var(--border)}.feed-replies{border-left:2px solid var(--border);margin:10px 0 0 12px;padding-left:14px}.feed-author{font-weight:700}.feed-meta{color:var(--muted);font-size:10px;margin-left:8px}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:11px 16px;border-bottom:1px solid var(--border)}th{font-size:10px;text-transform:uppercase;color:var(--muted)}.stat-grid{display:grid;grid-template-columns:repeat(5,1fr);padding:20px}.stat-grid p{margin:3px 0}.stat-grid p:first-child{font-size:22px;font-weight:700}@media(max-width:760px){main{padding:20px}.grid{grid-template-columns:1fr}.stat-grid{grid-template-columns:repeat(2,1fr)}}"""
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
