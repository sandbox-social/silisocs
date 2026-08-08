#!/usr/bin/env python3
"""Compatibility entry point for the ``silisocs-study`` study runner.

The study runner now lives in three cohesive modules:

- :mod:`silisocs.studies.plan` — pure planning: study loading/validation, run
  matrix expansion, override/command assembly, output layout resolution.
- :mod:`silisocs.studies.execute` — execution: run subprocesses, resume/skip
  markers, evaluator invocation, reproducibility artifact emission.
- :mod:`silisocs.studies.cli` — argparse surface, verb handlers, entry point.

This module stays as the stable invocation path: it backs the
``silisocs-study`` console script (``silisocs.studies.run_study:main``) and
``python -m silisocs.studies.run_study``, which HPC templates, Studio, and the
scenario generator shell out to.
"""

from __future__ import annotations

from silisocs.studies.cli import build_parser, main

__all__ = ["build_parser", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
