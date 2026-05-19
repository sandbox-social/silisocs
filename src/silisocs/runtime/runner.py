"""Runtime CLI entrypoint."""

from silisocs.runtime.execution.session import cli_main, main

__all__ = ["cli_main", "main"]


if __name__ == "__main__":
    cli_main()
