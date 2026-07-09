"""Console entry point for the Dash post-run analysis dashboard.

Installed as ``silisocs-analysis-dashboard``; arguments are handled by the
dashboard's own argparse (``--output_dir``, ``--debug`` — see ``main.py``).
"""

from __future__ import annotations

import sys


def main() -> int:
    """Launch the analysis dashboard, or explain how to install it."""
    try:
        import dash  # noqa: F401
        import dash_cytoscape  # noqa: F401
        import plotly  # noqa: F401
    except ImportError:
        print(
            "silisocs-analysis-dashboard needs the 'analysis' extra (Dash/Plotly).\n"
            'Install it with: pip install "silisocs[analysis]"',
            file=sys.stderr,
        )
        return 1
    from silisocs.evaluations.analysis.dashboard.main import main as run_dashboard

    run_dashboard()
    return 0


if __name__ == "__main__":
    sys.exit(main())
