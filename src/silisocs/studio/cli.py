"""Console entry point for Silisocs Studio."""

from __future__ import annotations

import argparse
import os


def main() -> int:
    """Launch the local Studio server."""
    parser = argparse.ArgumentParser(description="Launch Silisocs Studio")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address (non-localhost requires STUDIO_AUTH_TOKEN)",
    )
    parser.add_argument("--port", type=int, default=8765, help="HTTP port")
    parser.add_argument(
        "--output-root",
        default="outputs",
        help="Directory whose run artifacts Studio indexes and launches into",
    )
    parser.add_argument(
        "--state-dir",
        default=None,
        help="Where Studio keeps its job queue and workspace state (default: <repo-root>/.silisocs)",
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Project checkout holding scenarios/ and experiments/ (default: current directory)",
    )
    parser.add_argument(
        "--scenario-repo",
        action="append",
        default=[],
        help="Trusted repository containing scenarios/ and optional silisocs-studio.yaml",
    )
    parser.add_argument(
        "--max-concurrent-runs",
        type=int,
        default=1,
        help="How many launched runs may execute at once (others queue)",
    )
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"} and not os.environ.get(
        "STUDIO_AUTH_TOKEN"
    ):
        print("Binding Studio beyond localhost requires STUDIO_AUTH_TOKEN.")
        return 2
    try:
        import uvicorn
    except ImportError:
        print('silisocs-studio requires: pip install "silisocs[studio]"')
        return 1
    from silisocs.studio.app import create_app

    uvicorn.run(
        create_app(
            args.output_root,
            state_dir=args.state_dir,
            repo_root=args.repo_root,
            max_concurrent_runs=args.max_concurrent_runs,
            scenario_repositories=tuple(args.scenario_repo),
        ),
        host=args.host,
        port=args.port,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
