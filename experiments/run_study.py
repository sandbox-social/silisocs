#!/usr/bin/env python3
"""Back-compat shim: the study runner now lives in :mod:`silisocs.studies.run_study`.

Keeps ``python -m experiments.run_study`` and ``python experiments/run_study.py``
working for existing scripts and SLURM templates. New code should use the
``silisocs-study`` console command or import :mod:`silisocs.studies.run_study`.
"""

from __future__ import annotations

import sys

import silisocs.studies.run_study as _impl

globals().update({name: value for name, value in vars(_impl).items() if not name.startswith("__")})

if __name__ == "__main__":
    sys.exit(_impl.main())
