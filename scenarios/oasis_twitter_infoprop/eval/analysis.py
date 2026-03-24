"""
Evaluation script for Twitter Information Propagation scenario.

Analyzes cascade metrics: scale, depth, breadth, and structural virality.
Compares against real-world cascade data (if available).
"""

import json
import sqlite3
from collections import deque
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


def build_cascade_graph(db_path: str) -> dict[int, list[int]]:
    """
    Build repost cascade graph.

    Each post can be reposted (creating new posts with original_post_id = parent).
    We trace the full cascade tree.
    """
    conn = sqlite3.connect(db_path)

    # Get all reposts (posts with non-null quote_of_id or reply_to_id)
    cursor = conn.execute("""
        SELECT p.id, p.quote_of_id, p.created_at
        FROM posts p
        WHERE p.quote_of_id IS NOT NULL
        ORDER BY p.created_at
    """)

    graph = {}  # original_id -> list of repost_ids
    for post_id, quote_of_id, created_at in cursor.fetchall():
        if quote_of_id not in graph:
            graph[quote_of_id] = []
        graph[quote_of_id].append(post_id)

    conn.close()
    return graph


def compute_cascade_metrics(graph: dict[int, list[int]]) -> dict[str, list[float]]:
    """Compute cascade metrics for all cascades."""
    metrics = {
        "scale": [],  # Number of reposts (cascade size)
        "depth": [],  # Maximum depth of cascade tree
        "max_breadth": [],  # Maximum width at any level
        "structural_virality": [],  # Average shortest path
    }

    for root_post_id, children in graph.items():
        scale = compute_cascade_size(root_post_id, graph)
        depth = compute_cascade_depth(root_post_id, graph)
        breadth = compute_cascade_breadth(root_post_id, graph)
        virality = compute_structural_virality(root_post_id, graph)

        metrics["scale"].append(scale)
        metrics["depth"].append(depth)
        metrics["max_breadth"].append(breadth)
        metrics["structural_virality"].append(virality)

    return metrics


def compute_cascade_size(post_id: int, graph: dict) -> int:
    """Count total nodes in cascade tree (BFS)."""
    visited = set()
    queue = deque([post_id])
    count = 0

    while queue:
        node = queue.popleft()
        if node in visited:
            continue
        visited.add(node)
        count += 1

        if node in graph:
            for child in graph[node]:
                if child not in visited:
                    queue.append(child)

    return count


def compute_cascade_depth(post_id: int, graph: dict) -> int:
    """Compute maximum depth of cascade tree (BFS)."""
    if post_id not in graph or not graph[post_id]:
        return 1

    max_depth = 1
    queue = deque([(post_id, 1)])
    visited = set()

    while queue:
        node, depth = queue.popleft()
        max_depth = max(max_depth, depth)

        if node in graph:
            for child in graph[node]:
                if child not in visited:
                    visited.add(child)
                    queue.append((child, depth + 1))

    return max_depth


def compute_cascade_breadth(post_id: int, graph: dict) -> int:
    """Compute maximum breadth (nodes at any single level)."""
    if post_id not in graph:
        return 1

    max_width = 1
    queue = deque([(post_id, 0)])
    level_counts = {}

    while queue:
        node, level = queue.popleft()
        level_counts[level] = level_counts.get(level, 0) + 1

        if node in graph:
            for child in graph[node]:
                queue.append((child, level + 1))

    return max(level_counts.values()) if level_counts else 1


def compute_structural_virality(post_id: int, graph: dict) -> float:
    """Approximate structural virality as 1 - (depth / scale)."""
    scale = compute_cascade_size(post_id, graph)
    depth = compute_cascade_depth(post_id, graph)

    if depth == 0:
        return 0.0
    return 1.0 - (depth / max(scale, 1))


def plot_cascade_metrics(metrics: dict[str, list[float]], output_dir: Path) -> None:
    """Plot cascade metrics visualization."""
    output_dir.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    # Scale distribution
    ax = axes[0, 0]
    ax.hist(metrics["scale"], bins=20, edgecolor="black", alpha=0.7, color="skyblue")
    ax.set_xlabel("Cascade Scale (# reposts)")
    ax.set_ylabel("Frequency")
    ax.set_title(f"Cascade Scale Distribution (mean: {np.mean(metrics['scale']):.1f})")
    ax.grid(alpha=0.3)

    # Depth distribution
    ax = axes[0, 1]
    ax.hist(metrics["depth"], bins=20, edgecolor="black", alpha=0.7, color="lightcoral")
    ax.set_xlabel("Cascade Depth (# levels)")
    ax.set_ylabel("Frequency")
    ax.set_title(f"Cascade Depth Distribution (mean: {np.mean(metrics['depth']):.1f})")
    ax.grid(alpha=0.3)

    # Max breadth
    ax = axes[1, 0]
    ax.hist(metrics["max_breadth"], bins=20, edgecolor="black", alpha=0.7, color="lightgreen")
    ax.set_xlabel("Max Breadth (# nodes at widest level)")
    ax.set_ylabel("Frequency")
    ax.set_title(f"Max Breadth Distribution (mean: {np.mean(metrics['max_breadth']):.1f})")
    ax.grid(alpha=0.3)

    # Structural virality
    ax = axes[1, 1]
    ax.hist(
        metrics["structural_virality"], bins=20, edgecolor="black", alpha=0.7, color="lightyellow"
    )
    ax.set_xlabel("Structural Virality")
    ax.set_ylabel("Frequency")
    ax.set_title(f"Virality Distribution (mean: {np.mean(metrics['structural_virality']):.2f})")
    ax.grid(alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_dir / "cascade_metrics.png", dpi=150)
    print(f"Plot saved to: {output_dir / 'cascade_metrics.png'}")


def evaluate_info_propagation(db_path: str, output_dir: Path) -> None:
    """Run information propagation evaluation."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Build cascade graph
    graph = build_cascade_graph(db_path)

    if not graph:
        print("No cascades found (no reposts in simulation)")
        metrics = {
            "scale": [1],
            "depth": [1],
            "max_breadth": [1],
            "structural_virality": [0.0],
        }
    else:
        metrics = compute_cascade_metrics(graph)

    print("\n=== Information Propagation Evaluation ===")
    print(f"Number of cascades: {len(metrics['scale'])}")
    print(
        f"Cascade scale (mean ± std): {np.mean(metrics['scale']):.2f} ± {np.std(metrics['scale']):.2f}"
    )
    print(
        f"Cascade depth (mean ± std): {np.mean(metrics['depth']):.2f} ± {np.std(metrics['depth']):.2f}"
    )
    print(
        f"Max breadth (mean ± std): {np.mean(metrics['max_breadth']):.2f} ± {np.std(metrics['max_breadth']):.2f}"
    )
    print(f"Structural virality (mean): {np.mean(metrics['structural_virality']):.3f}")

    # Plot metrics
    plot_cascade_metrics(metrics, output_dir)

    # Save metrics
    metrics_json = {
        "scale_mean": float(np.mean(metrics["scale"])),
        "depth_mean": float(np.mean(metrics["depth"])),
        "breadth_mean": float(np.mean(metrics["max_breadth"])),
        "virality_mean": float(np.mean(metrics["structural_virality"])),
        "num_cascades": len(metrics["scale"]),
    }

    with open(output_dir / "metrics.json", "w") as f:
        json.dump(metrics_json, f, indent=2)
    print(f"Metrics saved to: {output_dir / 'metrics.json'}")


if __name__ == "__main__":
    import glob

    # Find simulation database
    db_files = glob.glob("*/social_media.db")

    if not db_files:
        print("No simulation database found. Run the scenario first.")
        exit(1)

    db_path = db_files[0]
    output_dir = Path("results") / "plots"

    evaluate_info_propagation(db_path, output_dir)
