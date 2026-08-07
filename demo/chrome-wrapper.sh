#!/bin/sh
# Chromium launch shim for hosts whose system libraries cannot run the
# Playwright-bundled browser directly (e.g. HPC nodes with an old glibc).
#
# Set SILISOCS_DEMO_CHROME to the Chromium binary. Optionally set
# SILISOCS_DEMO_LOADER to a dynamic loader and SILISOCS_DEMO_LIBPATH to its
# library path (for Compute Canada / CVMFS Gentoo prefixes:
#   SILISOCS_DEMO_LOADER=$GP/lib64/ld-linux-x86-64.so.2
#   SILISOCS_DEMO_LIBPATH=$GP/lib64:$GP/usr/lib64
# ). On an ordinary desktop, leave the loader unset: the shim execs the
# browser directly.
set -eu
CHROME="${SILISOCS_DEMO_CHROME:?set SILISOCS_DEMO_CHROME to the chromium binary}"
if [ -n "${SILISOCS_DEMO_LOADER:-}" ]; then
  exec "$SILISOCS_DEMO_LOADER" --library-path "${SILISOCS_DEMO_LIBPATH:-}" "$CHROME" "$@"
fi
exec "$CHROME" "$@"
