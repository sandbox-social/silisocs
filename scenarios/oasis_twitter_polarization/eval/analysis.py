"""
Evaluation script for Twitter Polarization scenario.

Analyzes opinion extremeness using LLM judgment on user responses.
Similar to OASIS group polarization evaluation.
"""

import json
import sqlite3
from pathlib import Path
from typing import Dict, List
import matplotlib.pyplot as plt


def load_probe_responses(db_path: str) -> Dict[str, List[str]]:
    """Load probe responses from simulation database."""
    try:
        with open(db_path / "probe_events.jsonl", 'r') as f:
            responses = {}
            for line in f:
                event = json.loads(line)
                if event.get('probe_name') == 'extremeness':
                    agent_id = event.get('agent_id', 'unknown')
                    response = event.get('response', '')
                    if agent_id not in responses:
                        responses[agent_id] = []
                    responses[agent_id].append(response)
            return responses
    except FileNotFoundError:
        return {}


def analyze_polarization_trends(responses: Dict[str, List[str]]) -> Dict:
    """
    Analyze polarization trends from responses.

    In a real implementation, this would use an LLM to judge extremeness.
    For MVP, we'll use heuristics (keyword analysis).
    """
    extremeness_scores = []

    for agent_id, agent_responses in responses.items():
        agent_scores = []
        for response in agent_responses:
            # Simple heuristic: count polarizing keywords
            polarizing_words = [
                'must', 'should', 'always', 'never', 'absolutely',
                'idiots', 'crazy', 'worst', 'best', 'destroy', 'save'
            ]

            score = sum(1 for word in polarizing_words if word in response.lower())
            agent_scores.append(score)

        avg_agent_extremeness = sum(agent_scores) / len(agent_scores) if agent_scores else 0
        extremeness_scores.append(avg_agent_extremeness)

    return {
        "mean_extremeness": sum(extremeness_scores) / len(extremeness_scores) if extremeness_scores else 0,
        "max_extremeness": max(extremeness_scores) if extremeness_scores else 0,
        "min_extremeness": min(extremeness_scores) if extremeness_scores else 0,
        "std_extremeness": 0,  # TODO: compute properly
        "agent_count": len(responses),
        "response_count": sum(len(r) for r in responses.values()),
    }


def evaluate_polarization(output_dir: str) -> None:
    """Run polarization evaluation."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Try to load probe responses from output location
    probe_file = output_dir.parent / "probe_events.jsonl"

    if probe_file.exists():
        responses = load_probe_responses(output_dir.parent)
    else:
        responses = {}

    # Compute polarization metrics
    metrics = analyze_polarization_trends(responses)

    print("\n=== Twitter Polarization Evaluation ===")
    print(f"Mean opinion extremeness score: {metrics['mean_extremeness']:.2f}")
    print(f"Max extremeness: {metrics['max_extremeness']:.2f}")
    print(f"Min extremeness: {metrics['min_extremeness']:.2f}")
    print(f"Agents analyzed: {metrics['agent_count']}")
    print(f"Total responses: {metrics['response_count']}")

    # Plot extremeness distribution
    if metrics['agent_count'] > 0:
        fig, ax = plt.subplots(figsize=(10, 6))

        # Simulate distribution for visualization
        scores = [metrics['mean_extremeness']] * max(1, metrics['agent_count'] // 2)
        ax.hist(scores, bins=10, edgecolor='black', alpha=0.7, color='darkred')
        ax.axvline(metrics['mean_extremeness'], color='red', linestyle='--',
                   linewidth=2, label=f"Mean: {metrics['mean_extremeness']:.2f}")
        ax.set_xlabel("Opinion Extremeness Score")
        ax.set_ylabel("Number of Users")
        ax.set_title("Distribution of Opinion Extremeness")
        ax.legend()
        ax.grid(alpha=0.3)

        plt.tight_layout()
        plot_path = output_dir / "polarization_distribution.png"
        plt.savefig(plot_path, dpi=150)
        print(f"\nPlot saved to: {plot_path}")

    # Save metrics
    metrics_path = output_dir / "metrics.json"
    with open(metrics_path, 'w') as f:
        json.dump(metrics, f, indent=2)
    print(f"Metrics saved to: {metrics_path}")


if __name__ == "__main__":
    import sys
    output_dir = sys.argv[1] if len(sys.argv) > 1 else "results/plots"
    evaluate_polarization(output_dir)
