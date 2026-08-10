"""Static evaluator presets shared by the study runner and visual composer.

Preset commands start with the :data:`PYTHON_TOKEN` placeholder rather than a
hard-coded launcher: :func:`silisocs.studies.plan._resolve_eval_spec` swaps it
for the interpreter the study itself resolves (``RUN_STUDY_PYTHON`` or
``sys.executable``), so evaluators run under the SAME interpreter as the runs
they evaluate — including pip-installed, non-``uv`` environments. Custom study
commands may use the same ``{python}`` token.
"""

from __future__ import annotations

from typing import Any

#: Placeholder for the study's resolved Python interpreter.
PYTHON_TOKEN = "{python}"

BUILTIN_EVAL_PRESETS: dict[str, dict[str, Any]] = {
    "builtin.activity_summary": {
        "command": [PYTHON_TOKEN, "-m", "silisocs.evaluations.activity_summary"],
        "input_mode": "run_dir",
        "run_dir_arg": "--run-dir",
        "output_arg": "--output",
        "output_subpath": "activity_summary.json",
    },
    "builtin.probe_summary": {
        "command": [
            PYTHON_TOKEN,
            "-m",
            "silisocs.evaluations.activity_summary",
            "--mode",
            "probes",
        ],
        "input_mode": "run_dir",
        "run_dir_arg": "--run-dir",
        "output_arg": "--output",
        "output_subpath": "probe_summary.json",
    },
    "builtin.action_metrics_detailed": {
        "command": [
            PYTHON_TOKEN,
            "-m",
            "silisocs.evaluations.default_evaluators",
            "--mode",
            "action_metrics",
        ],
        "input_mode": "run_dir",
        "run_dir_arg": "--run-dir",
        "output_arg": "--output",
        "output_subpath": "action_metrics_detailed.json",
    },
    "builtin.probe_metrics_detailed": {
        "command": [
            PYTHON_TOKEN,
            "-m",
            "silisocs.evaluations.default_evaluators",
            "--mode",
            "probe_metrics",
        ],
        "input_mode": "run_dir",
        "run_dir_arg": "--run-dir",
        "output_arg": "--output",
        "output_subpath": "probe_metrics_detailed.json",
    },
    "builtin.probe_binary_detailed": {
        "command": [
            PYTHON_TOKEN,
            "-m",
            "silisocs.evaluations.default_evaluators",
            "--mode",
            "probe_binary",
        ],
        "input_mode": "run_dir",
        "run_dir_arg": "--run-dir",
        "output_arg": "--output",
        "output_subpath": "probe_binary_detailed.json",
    },
    "builtin.probe_numeric_detailed": {
        "command": [
            PYTHON_TOKEN,
            "-m",
            "silisocs.evaluations.default_evaluators",
            "--mode",
            "probe_numeric",
        ],
        "input_mode": "run_dir",
        "run_dir_arg": "--run-dir",
        "output_arg": "--output",
        "output_subpath": "probe_numeric_detailed.json",
    },
    "builtin.probe_choice_detailed": {
        "command": [
            PYTHON_TOKEN,
            "-m",
            "silisocs.evaluations.default_evaluators",
            "--mode",
            "probe_choice",
        ],
        "input_mode": "run_dir",
        "run_dir_arg": "--run-dir",
        "output_arg": "--output",
        "output_subpath": "probe_choice_detailed.json",
    },
    "builtin.probe_freetext_detailed": {
        "command": [
            PYTHON_TOKEN,
            "-m",
            "silisocs.evaluations.default_evaluators",
            "--mode",
            "probe_freetext",
        ],
        "input_mode": "run_dir",
        "run_dir_arg": "--run-dir",
        "output_arg": "--output",
        "output_subpath": "probe_freetext_detailed.json",
    },
    "builtin.study_eval": {
        "command": [PYTHON_TOKEN, "./eval.py"],
        "input_mode": "run_dir",
        "run_dir_arg": "--run-dir",
        "output_arg": "--output",
        "output_subpath": "eval.json",
    },
}
