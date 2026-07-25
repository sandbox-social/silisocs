"""Study-scope panels built on the organized study summary."""
# ruff: noqa: D101, D102

from __future__ import annotations

import html
from typing import Any

from silisocs.analysis.panel import (
    Control,
    Figure,
    Grid,
    Html,
    Panel,
    PanelOutput,
    Table,
    register_panel,
)
from silisocs.evaluations.run_artifact import RunArtifact, StudyArtifact


def _condition_metrics(
    summary: dict[str, Any], key: str, hypothesis: str, condition: str
) -> dict[str, Any]:
    per_hypothesis = summary.get(key)
    per_condition = per_hypothesis.get(hypothesis) if isinstance(per_hypothesis, dict) else None
    values = per_condition.get(condition) if isinstance(per_condition, dict) else None
    return values if isinstance(values, dict) else {}


def _baseline_axis_title(base_title: str, baseline: str, known_conditions: set[str]) -> str:
    """Name the baseline on the axis — including when it matched nothing.

    A typo'd baseline must not silently render the same figure as no baseline
    (a control that visibly does nothing erodes trust in the ones that work).
    """
    if not baseline:
        return base_title
    if baseline in known_conditions:
        return f"{base_title} (baseline: {baseline})"
    return f"{base_title} (baseline {baseline!r} matches no condition)"


@register_panel
class ConditionComparisonPanel(Panel):
    name = "condition_comparison"
    title = "Condition comparison"
    scope = "study"
    controls = (
        Control(kind="select", param="compare", label="Compare", choices=("condition", "seed")),
        Control(kind="text", param="baseline", label="Baseline"),
        Control(kind="text", param="hypothesis", label="Hypothesis"),
    )

    def build(self, artifact: RunArtifact | StudyArtifact, params: dict[str, Any]) -> Figure:
        assert isinstance(artifact, StudyArtifact)
        wanted = str(params.get("hypothesis") or "") or None
        baseline = str(params.get("baseline") or "").strip()
        if str(params.get("compare") or "condition") == "seed":
            return self._seed_figure(artifact, wanted, baseline)
        return self._condition_figure(artifact, wanted, baseline)

    def _condition_figure(
        self, artifact: StudyArtifact, wanted: str | None, baseline: str
    ) -> Figure:
        summary = artifact.summary if isinstance(artifact.summary, dict) else {}
        # Both summary sections nest condition values under hypothesis ids
        # ({hypothesis: {condition: ...}}, docs/study_schema.md); the stats
        # section carries per-metric replicate CIs when seeds were replicated.
        means = summary.get("metrics_by_condition")
        means = means if isinstance(means, dict) else {}
        columns = [
            (str(hypothesis), str(condition))
            for hypothesis, conditions in means.items()
            if isinstance(conditions, dict) and (not wanted or hypothesis == wanted)
            for condition in conditions
        ]
        if baseline:
            # Stable partition: the baseline condition leads, order otherwise kept.
            columns.sort(key=lambda pair: pair[1] != baseline)
        multi = len({hypothesis for hypothesis, _ in columns}) > 1
        labels = [
            (f"{hypothesis} · {condition}" if multi else condition)
            + (" (baseline)" if baseline and condition == baseline else "")
            for hypothesis, condition in columns
        ]
        known_conditions = {condition for _, condition in columns}
        metric_names = sorted(
            {
                metric
                for hypothesis, condition in columns
                for metric, value in _condition_metrics(
                    summary, "metrics_by_condition", hypothesis, condition
                ).items()
                if isinstance(value, (int, float))
            }
        )
        traces = []
        for metric in metric_names:
            values: list[float | None] = []
            err_plus, err_minus = [], []
            has_ci = False
            for hypothesis, condition in columns:
                value = _condition_metrics(
                    summary, "metrics_by_condition", hypothesis, condition
                ).get(metric)
                value = value if isinstance(value, (int, float)) else None
                values.append(value)
                stat = _condition_metrics(
                    summary, "metrics_stats_by_condition", hypothesis, condition
                ).get(metric)
                high = stat.get("ci95_high") if isinstance(stat, dict) else None
                low = stat.get("ci95_low") if isinstance(stat, dict) else None
                if (
                    value is not None
                    and isinstance(high, (int, float))
                    and isinstance(low, (int, float))
                ):
                    err_plus.append(high - value)
                    err_minus.append(value - low)
                    has_ci = True
                else:
                    err_plus.append(0)
                    err_minus.append(0)
            trace: dict[str, Any] = {"type": "bar", "name": metric, "x": labels, "y": values}
            if has_ci:
                trace["error_y"] = {
                    "type": "data",
                    "array": err_plus,
                    "arrayminus": err_minus,
                    "visible": True,
                }
            traces.append(trace)
        return Figure(
            {
                "data": traces,
                "layout": {
                    "barmode": "group",
                    "xaxis": {
                        "title": _baseline_axis_title("Condition", baseline, known_conditions)
                    },
                    "yaxis": {"title": "Metric"},
                },
            }
        )

    def _seed_figure(self, artifact: StudyArtifact, wanted: str | None, baseline: str) -> Figure:
        """One column per replicate: the per-run values behind the condition means."""
        records = [
            record
            for record in artifact.run_records
            if isinstance(record.get("aggregated"), dict)
            and record["aggregated"]
            and (not wanted or str(record.get("hypothesis")) == wanted)
        ]
        if baseline:
            records.sort(key=lambda record: str(record.get("condition")) != baseline)
        multi = len({str(record.get("hypothesis")) for record in records}) > 1
        labels = []
        for record in records:
            condition = str(record.get("condition") or "")
            label = f"{record.get('hypothesis')} · {condition}" if multi else condition
            if baseline and condition == baseline:
                label += " (baseline)"
            labels.append(f"{label} · seed {record.get('seed')}")
        known_conditions = {str(record.get("condition") or "") for record in records}
        metric_names = sorted(
            {
                metric
                for record in records
                for metric, value in record["aggregated"].items()
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            }
        )
        traces = []
        for metric in metric_names:
            values: list[float | None] = []
            for record in records:
                value = record["aggregated"].get(metric)
                usable = isinstance(value, (int, float)) and not isinstance(value, bool)
                values.append(float(value) if usable else None)
            traces.append({"type": "bar", "name": metric, "x": labels, "y": values})
        return Figure(
            {
                "data": traces,
                "layout": {
                    "barmode": "group",
                    "xaxis": {
                        "title": _baseline_axis_title("Replicate", baseline, known_conditions)
                    },
                    "yaxis": {"title": "Metric"},
                },
            }
        )


@register_panel
class HypothesisBoardPanel(Panel):
    name = "hypothesis_board"
    title = "Hypotheses"
    scope = "study"

    def build(self, artifact: RunArtifact | StudyArtifact, params: dict[str, Any]) -> Grid:
        assert isinstance(artifact, StudyArtifact)
        definition = artifact.definition or {}
        cards: list[PanelOutput] = []
        for hypothesis_id, hypothesis in (definition.get("hypotheses") or {}).items():
            follows = hypothesis.get("follows_from")
            relation = (
                f'<p class="hypothesis-link">Follows {html.escape(str(follows))}</p>'
                if follows
                else ""
            )
            cards.append(
                Html(
                    '<article class="hypothesis-card">'
                    '<span class="badge neutral">'
                    f"{html.escape(str(hypothesis.get('status') or 'planned'))}</span>"
                    f"<h3>{html.escape(str(hypothesis_id))}</h3>"
                    f"<p>{html.escape(str(hypothesis.get('statement') or ''))}</p>"
                    "<dl><dt>Independent variable</dt><dd>"
                    f"{html.escape(str(hypothesis.get('independent_variable') or '—'))}</dd>"
                    "<dt>Prediction</dt><dd>"
                    f"{html.escape(str(hypothesis.get('prediction') or '—'))}</dd></dl>"
                    f"{relation}</article>"
                )
            )
        return Grid(cards)


@register_panel
class PerAgentDistributionsPanel(Panel):
    name = "per_agent_distributions"
    title = "Per-agent distributions"
    scope = "study"
    controls = (Control(kind="text", param="metric", label="Metric"),)

    def build(self, artifact: RunArtifact | StudyArtifact, params: dict[str, Any]) -> Figure:
        assert isinstance(artifact, StudyArtifact)
        metric = str(params.get("metric") or "")
        if not metric:
            metric = next(
                (
                    key
                    for record in artifact.run_records
                    for metrics in (record.get("agents") or {}).values()
                    if isinstance(metrics, dict)
                    for key, value in metrics.items()
                    if isinstance(value, (int, float)) and not isinstance(value, bool)
                ),
                "",
            )
        grouped: dict[tuple[str, str], list[tuple[str, float]]] = {}
        for record in artifact.run_records:
            key = (str(record.get("hypothesis") or ""), str(record.get("condition") or ""))
            for agent, metrics in (record.get("agents") or {}).items():
                value = metrics.get(metric) if isinstance(metrics, dict) else None
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    grouped.setdefault(key, []).append((str(agent), float(value)))
        traces = [
            {
                "type": "scatter",
                "mode": "markers",
                "name": f"{hypothesis} · {condition}",
                "x": [condition] * len(values),
                "y": [value for _, value in values],
                "text": [agent for agent, _ in values],
            }
            for (hypothesis, condition), values in grouped.items()
        ]
        return Figure(
            {
                "data": traces,
                "layout": {
                    "xaxis": {"title": "Condition"},
                    "yaxis": {"title": metric or "Metric"},
                },
            }
        )


@register_panel
class StudyProgressPanel(Panel):
    name = "study_progress"
    title = "Study progress"
    scope = "study"

    def build(self, artifact: RunArtifact | StudyArtifact, params: dict[str, Any]) -> Table:
        assert isinstance(artifact, StudyArtifact)
        # ``run`` cells are run references ({"run_path", "text"}): a shell that
        # can address run pages (Studio) turns them into links; every other
        # renderer (static report, notebook) shows the text. See
        # docs/analysis_panels.md — "Run reference cells".
        rows = [
            {
                **row,
                "run": (
                    {"run_path": str(row.get("run_dir") or ""), "text": "open"}
                    if row.get("run_dir") and row.get("status") in {"complete", "reused"}
                    else ""
                ),
            }
            for row in artifact.progress
        ]
        return Table(
            columns=["hypothesis", "condition", "scenario", "seed", "status", "run"],
            rows=rows,
        )
