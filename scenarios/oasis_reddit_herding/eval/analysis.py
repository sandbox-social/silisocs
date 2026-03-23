"""
Evaluation script for Reddit Herding scenario.

Analyzes how user opinions align with community consensus over time.
Computes engagement metrics and consensus formation.
"""

import json
import sqlite3
from pathlib import Path
from typing import Dict, List, Tuple
import numpy as np
import matplotlib.pyplot as plt
from scipy import stats


def load_database(db_path: str) -> sqlite3.Connection:
    """Load simulation database."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def get_post_scores(conn: sqlite3.Connection) -> Dict[int, Tuple[int, int]]:
    """Get post scores (upvotes, downvotes) over time."""
    cursor = conn.execute("""
        SELECT id, likes_count, downvotes
        FROM posts
        ORDER BY created_at
    """)
    return {row[0]: (row[1], row[2]) for row in cursor.fetchall()}


def compute_engagement_statistics(upvotes: List[int], downvotes: List[int]) -> Dict:
    """Compute engagement statistics with 95% confidence intervals."""
    scores = np.array(upvotes) - np.array(downvotes)

    mean_score = np.mean(scores)
    n = len(scores)
    se = stats.sem(scores)
    ci = se * stats.t.ppf((1 + 0.95) / 2, n - 1)

    return {
        "mean": float(mean_score),
        "ci_lower": float(mean_score - ci),
        "ci_upper": float(mean_score + ci),
        "std": float(np.std(scores)),
    }


def analyze_herding(db_path: str, output_dir: Path) -> None:
    """Analyze herding behavior in the simulation."""
    conn = load_database(db_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Get post scores
    post_scores = get_post_scores(conn)

    if not post_scores:
        print("No posts found in database")
        return

    upvotes = [scores[0] for scores in post_scores.values()]
    downvotes = [scores[1] for scores in post_scores.values()]

    # Compute statistics
    stats_dict = compute_engagement_statistics(upvotes, downvotes)

    print("\n=== Reddit Herding Evaluation ===")
    print(f"Mean engagement score: {stats_dict['mean']:.2f}")
    print(f"  95% CI: [{stats_dict['ci_lower']:.2f}, {stats_dict['ci_upper']:.2f}]")
    print(f"Std deviation: {stats_dict['std']:.2f}")
    print(f"Total posts analyzed: {len(post_scores)}")

    # Plot results
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    # Score distribution
    scores = np.array(upvotes) - np.array(downvotes)
    ax1.hist(scores, bins=20, edgecolor='black', alpha=0.7)
    ax1.axvline(stats_dict['mean'], color='red', linestyle='--', linewidth=2, label=f"Mean: {stats_dict['mean']:.2f}")
    ax1.axvline(stats_dict['ci_lower'], color='orange', linestyle=':', alpha=0.7, label="95% CI")
    ax1.axvline(stats_dict['ci_upper'], color='orange', linestyle=':', alpha=0.7)
    ax1.set_xlabel("Engagement Score (Upvotes - Downvotes)")
    ax1.set_ylabel("Frequency")
    ax1.set_title("Distribution of Post Engagement")
    ax1.legend()

    # Mean with CI
    ax2.errorbar(
        [0], [stats_dict['mean']],
        yerr=[[stats_dict['mean'] - stats_dict['ci_lower']],
              [stats_dict['ci_upper'] - stats_dict['mean']]],
        fmt='o', markersize=10, capsize=5, capthick=2, color='blue',
        label="Mean ± 95% CI"
    )
    ax2.set_ylim(stats_dict['ci_lower'] - 2, stats_dict['ci_upper'] + 2)
    ax2.set_ylabel("Engagement Score")
    ax2.set_title("Community Consensus Level")
    ax2.set_xticks([])
    ax2.grid(axis='y', alpha=0.3)
    ax2.legend()

    plt.tight_layout()
    plot_path = output_dir / "herding_analysis.png"
    plt.savefig(plot_path, dpi=150)
    print(f"\nPlot saved to: {plot_path}")

    # Save metrics
    metrics_path = output_dir / "metrics.json"
    with open(metrics_path, 'w') as f:
        json.dump(stats_dict, f, indent=2)
    print(f"Metrics saved to: {metrics_path}")

    conn.close()


if __name__ == "__main__":
    # Find latest simulation database
    import glob
    db_files = glob.glob("*/social_media.db")

    if not db_files:
        print("No simulation database found. Run the scenario first.")
        exit(1)

    db_path = db_files[0]
    output_dir = Path("results")

    analyze_herding(db_path, output_dir / "plots")
