"""Optional postprocessors for detailed probe evaluators.

Postprocessor signature:
    postprocess(records_by_type, out_dir, context) -> dict | list | None

Where:
- records_by_type: {"choice": [...], "numeric": [...], "freetext": [...]}
- out_dir: directory for plot artifacts
- context: {"mode", "run_dir", "output_json", "plot_dir"}
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def episode_probe_volume(
    records_by_type: dict[str, list[dict[str, Any]]],
    out_dir: Path,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Generate a simple probe-volume-over-episode plot for any probe mode."""
    _ = context
    counts: Counter[int] = Counter()

    for key in ("choice", "numeric", "freetext"):
        for rec in records_by_type.get(key, []):
            try:
                counts[int(rec.get("episode", 0))] += 1
            except (TypeError, ValueError):
                continue

    if not counts:
        return {"generated_files": []}

    episodes = sorted(counts.keys())
    values = [counts[ep] for ep in episodes]

    plt.figure(figsize=(9, 4))
    plt.plot(episodes, values, marker="o")
    plt.title("Probe Response Volume Over Episodes")
    plt.xlabel("Episode")
    plt.ylabel("Response count")
    plt.tight_layout()

    out_path = out_dir / "probe_volume_over_time.png"
    plt.savefig(out_path, dpi=150)
    plt.close()

    return {"generated_files": [str(out_path)]}
