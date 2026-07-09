"""Console entry point for the Streamlit scenario/launch dashboard.

Installed as ``silisocs-dashboard``; extra args are forwarded to
``streamlit run`` (e.g. ``silisocs-dashboard --server.port 8600``).
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    """Launch the Streamlit dashboard, or explain how to install it."""
    try:
        from streamlit.web import cli as stcli
    except ImportError:
        print(
            "silisocs-dashboard needs the 'dashboard' extra (Streamlit).\n"
            'Install it with: pip install "silisocs[dashboard]"',
            file=sys.stderr,
        )
        return 1
    app_path = Path(__file__).resolve().parent / "launch_app.py"
    sys.argv = ["streamlit", "run", str(app_path), *sys.argv[1:]]
    return int(stcli.main() or 0)


if __name__ == "__main__":
    sys.exit(main())
