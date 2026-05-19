"""
Specific Election Analysis Plots

This script creates two specific visualizations:
1. Voting evolution - total number of votes per candidate per episode
2. Favorability difference histogram - Bill vs Bradley favorability difference by agent
"""

import json
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml


class VotingFavorabilityAnalyzer:
    """VotingFavorabilityAnalyzer.

    Constructor parameters:

    __init__.

    :param str probe_file_path:
    :type probe_file_path: str
    """

    def load_agent_party_map(
        self, yaml_path: str = "conf/agents/election_agents_with_partisan.yaml"
    ):
        """Load agent party affiliations from the YAML config."""
        with open(yaml_path, encoding="utf-8") as f:
            agent_yaml = yaml.safe_load(f)
        party_map = {}
        for entry in agent_yaml.get("directory", []):
            name = entry.get("name")
            party = entry.get("party", "").strip().lower()
            if name:
                if party in ["conservative", "liberal", "independent"]:
                    party_map[name] = party
                else:
                    party_map[name] = "candidate"  # Bill, Bradley, or others with no party
        # Add Bill and Bradley explicitly if not present
        for candidate in ["Bill Fredrickson", "Bradley Carter"]:
            party_map.setdefault(candidate, "candidate")
        self.agent_party_map = party_map

    def categorize_agents(self, agent_list):
        """Categorize agents into four types for plotting."""
        # Keep internal keys as party names from the YAML. We'll relabel 'liberal' -> 'Progress' for display.
        categories = {"conservative": [], "liberal": [], "independent": [], "candidate": []}
        for agent in agent_list:
            cat = self.agent_party_map.get(agent, "candidate")
            categories[cat].append(agent)
        return categories

    """Analyzes voting and favorability differences"""

    def __init__(self, probe_file_path: str):
        """__init__.

        :param str probe_file_path:
        :type probe_file_path: str
        """
        self.probe_file_path = probe_file_path
        # Typed instance attributes
        self.data: list[dict] = []
        self.favorability_data: list[dict] = []
        self.vote_pref_data: list[dict] = []

    def load_data(self):
        """Load and parse the probe events data"""
        print(f"Loading data from {self.probe_file_path}")

        with open(self.probe_file_path, encoding="utf-8") as f:
            for line in f:
                try:
                    event = json.loads(line.strip())
                    self.data.append(event)
                except json.JSONDecodeError:
                    continue

        print(f"Loaded {len(self.data)} events")
        # Load agent party map for categorization
        self.load_agent_party_map()
        self._process_data()

    def _process_data(self):
        """Process the raw data into structured formats"""
        for event in self.data:
            label = event.get("label")
            episode = event.get("episode", 0)
            source_user = event.get("source_user")
            data = event.get("data", {})

            if label == "Favorability":
                candidate = data.get("interaction_premise_template", {}).get("candidate")
                # Accept null/non-numeric favorability values (store as None) so we can forward-fill later
                raw_score = data.get("probe_return")
                favorability = None
                if raw_score is not None:
                    try:
                        # Some values may already be integers
                        if isinstance(raw_score, int):
                            favorability = int(raw_score)
                        else:
                            # Strip and check digits (handles strings like ' 75 ')
                            s = str(raw_score).strip()
                            if s.isdigit():
                                favorability = int(s)
                    except Exception:
                        favorability = None

                if candidate:
                    self.favorability_data.append(
                        {
                            "episode": episode,
                            "agent": source_user,
                            "candidate": candidate,
                            "favorability": favorability,
                        }
                    )

            elif label == "VotePref":
                preference = data.get("probe_return", "")

                # Determine preferred candidate based on response
                preferred_candidate = None
                if "Bill" in preference or "bill" in preference.lower():
                    preferred_candidate = "Bill Fredrickson"
                elif "Bradley" in preference or "bradley" in preference.lower():
                    preferred_candidate = "Bradley Carter"

                # Only include votes for Bill or Bradley
                if preferred_candidate:
                    self.vote_pref_data.append(
                        {
                            "episode": episode,
                            "agent": source_user,
                            "preferred_candidate": preferred_candidate,
                        }
                    )

        # Convert to DataFrames
        self.favorability_df = pd.DataFrame(self.favorability_data)
        self.vote_pref_df = pd.DataFrame(self.vote_pref_data)

        print(f"Processed {len(self.favorability_data)} favorability ratings")
        print(f"Processed {len(self.vote_pref_data)} vote preferences (Bill/Bradley only)")

    def plot_voting_evolution(self, save_path=None):
        """Plot total number of votes per candidate per episode"""
        if self.vote_pref_df.empty:
            print("No vote preference data to plot")
            return

        # Count votes by episode and candidate
        vote_counts = (
            self.vote_pref_df.groupby(["episode", "preferred_candidate"])
            .size()
            .unstack(fill_value=0)
        )

        # Create the plot
        fig, ax = plt.subplots(figsize=(12, 8))

        # Plot lines for each candidate
        candidates = ["Bill Fredrickson", "Bradley Carter"]
        colors = ["#1f77b4", "#ff7f0e"]  # Blue and orange

        for i, candidate in enumerate(candidates):
            if candidate in vote_counts.columns:
                ax.plot(
                    vote_counts.index,
                    vote_counts[candidate],
                    marker="o",
                    linewidth=3,
                    markersize=8,
                    label=candidate,
                    color=colors[i],
                )

        ax.set_xlabel("Episode", fontsize=12)
        ax.set_ylabel("Total Number of Votes", fontsize=12)
        ax.set_title(
            "Voting Evolution: Total Votes per Candidate per Episode",
            fontsize=14,
            fontweight="bold",
        )
        ax.legend(fontsize=12)
        ax.grid(True, alpha=0.3)

        # Set y-axis to start from 0
        ax.set_ylim(bottom=0)

        # Add value labels on points
        for i, candidate in enumerate(candidates):
            if candidate in vote_counts.columns:
                for episode, count in vote_counts[candidate].items():
                    if count > 0:
                        ax.annotate(
                            f"{count}",
                            (episode, count),
                            textcoords="offset points",
                            xytext=(0, 10),
                            ha="center",
                            fontsize=10,
                            color=colors[i],
                        )

        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches="tight")
            print(f"Saved voting evolution plot to {save_path}")

        plt.show()

        # Print summary statistics
        print("\nVoting Evolution Summary:")
        print("-" * 40)
        for candidate in candidates:
            if candidate in vote_counts.columns:
                total_votes = vote_counts[candidate].sum()
                final_votes = vote_counts[candidate].iloc[-1] if len(vote_counts) > 0 else 0
                print(f"{candidate}:")
                print(f"  Total votes across all episodes: {total_votes}")
                print(f"  Final episode votes: {final_votes}")

    def plot_favorability_difference_histogram(self, save_path=None):
        """Plot favorability difference (Bill - Bradley) histogram for first and final episodes"""
        if self.favorability_df.empty:
            print("No favorability data to plot")
            return

        # Get first and last episodes
        episodes = sorted(self.favorability_df["episode"].unique())
        if len(episodes) < 1:
            print("Not enough episode data")
            return

        first_episode = episodes[0]
        final_episode = episodes[-1]

        # Calculate favorability differences for each agent in first and final episodes
        # We'll forward-fill missing candidate ratings from previous episodes per agent.
        def get_favorability_differences(episode_num):
            # Build agent -> episode -> candidate -> favorability mapping
            """get_favorability_differences.

            :param episode_num:
            """
            agent_episode_candidate = defaultdict(lambda: defaultdict(dict))
            for _, row in self.favorability_df.iterrows():
                ag = row["agent"]
                ep = row["episode"]
                cand = row["candidate"]
                fav = row["favorability"]
                agent_episode_candidate[ag][ep][cand] = fav

            differences = {}

            # For each agent, walk episodes in order and forward-fill candidate scores
            episodes_sorted = sorted(self.favorability_df["episode"].unique())
            for agent, ep_map in agent_episode_candidate.items():
                prev_vals = {"Bill Fredrickson": None, "Bradley Carter": None}
                for ep in episodes_sorted:
                    # if there is a record for this agent at this episode, use it (even if None)
                    rec = ep_map.get(ep, {})
                    for cand in ["Bill Fredrickson", "Bradley Carter"]:
                        if cand in rec:
                            # explicit value (may be None)
                            if rec[cand] is not None:
                                prev_vals[cand] = rec[cand]
                            else:
                                # None => keep previous value (no change)
                                pass
                        else:
                            # no record for this episode & candidate -> keep prev
                            pass

                    if ep == episode_num:
                        # At the target episode, include agent only if we have values for both
                        b = prev_vals["Bill Fredrickson"]
                        r = prev_vals["Bradley Carter"]
                        if b is not None and r is not None:
                            differences[agent] = b - r
                        break

            return differences

        first_differences = get_favorability_differences(first_episode)
        final_differences = get_favorability_differences(final_episode)

        # Get agents that appear in both episodes
        common_agents = set(first_differences.keys()) & set(final_differences.keys())

        if not common_agents:
            print("No agents found with favorability ratings for both candidates in both episodes")
            return

        # Prepare data for plotting
        # Categorize and order agents by party, then alphabetical within category
        categorized = self.categorize_agents(sorted(common_agents))
        ordered_agents = []
        # User requested sequence: conservative, independent, liberal, candidates
        category_sequence = ["conservative", "independent", "liberal", "candidate"]
        for cat in category_sequence:
            ordered_agents.extend(sorted(categorized.get(cat, []), key=lambda x: x.split()[0]))
        agents = ordered_agents
        first_diffs = [first_differences[agent] for agent in agents]
        final_diffs = [final_differences[agent] for agent in agents]

        # Create the plot
        fig, ax = plt.subplots(figsize=(15, 8))

        x = np.arange(len(agents))
        width = 0.35

        bars1 = ax.bar(
            x - width / 2,
            first_diffs,
            width,
            label=f"Episode {first_episode}",
            alpha=0.8,
            color="lightblue",
        )
        bars2 = ax.bar(
            x + width / 2,
            final_diffs,
            width,
            label=f"Episode {final_episode}",
            alpha=0.8,
            color="darkblue",
        )

        ax.set_xlabel("Agents", fontsize=12)
        ax.set_ylabel("Favorability Difference (Bill - Bradley)", fontsize=12)
        ax.set_title(
            "Agent Favorability Differences: Bill Fredrickson - Bradley Carter",
            fontsize=14,
            fontweight="bold",
        )
        ax.set_xticks(x)
        ax.set_xticklabels(agents, rotation=45, ha="right")
        ax.legend(fontsize=12)
        ax.grid(True, alpha=0.3, axis="y")

        # Add horizontal line at y=0
        ax.axhline(y=0, color="black", linestyle="-", alpha=0.5)

        # Add value labels on bars
        def add_value_labels(bars):
            """add_value_labels.

            :param bars:
            """
            for bar in bars:
                height = bar.get_height()
                ax.annotate(
                    f"{height:+.1f}",
                    xy=(bar.get_x() + bar.get_width() / 2, height),
                    xytext=(0, 3 if height >= 0 else -15),
                    textcoords="offset points",
                    ha="center",
                    va="bottom" if height >= 0 else "top",
                    fontsize=8,
                    rotation=90,
                )

        add_value_labels(bars1)
        add_value_labels(bars2)

        # Add group separators and labels beneath the x-axis
        group_labels = []
        group_positions = []
        boundaries = []
        last_idx = 0
        # Use the same category sequence here
        for cat in category_sequence:
            group_agents = sorted(categorized.get(cat, []), key=lambda x: x.split()[0])
            if group_agents:
                start = last_idx
                end = last_idx + len(group_agents) - 1
                mid = (start + end) / 2
                # Display label: relabel 'liberal' -> 'Progress'
                display_label = "Progressive" if cat == "liberal" else cat.capitalize()
                group_labels.append(display_label)
                group_positions.append(mid)
                # boundary after this group (don't add if it's the last position)
                boundaries.append(end + 0.5)
                last_idx = end + 1

        # Draw vertical separators (skip the final boundary which is beyond last tick)
        n = len(agents)
        for b in boundaries:
            if b < n - 0.4:  # avoid drawing past the final bar
                ax.axvline(b, color="gray", linestyle="--", linewidth=0.8, alpha=0.7)

        # Place group labels centered below the x-axis tick labels using axis transform
        for label, pos in zip(group_labels, group_positions, strict=False):
            # push labels further down (negative axis transform y) to avoid overlapping the agent names
            ax.text(
                pos,
                -0.22,
                label,
                transform=ax.get_xaxis_transform(),
                ha="center",
                va="top",
                fontsize=11,
                fontweight="bold",
            )

        # Add some extra bottom margin so group labels don't overlap
        plt.subplots_adjust(bottom=0.32)

        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches="tight")
            print(f"Saved favorability difference histogram to {save_path}")

        plt.show()

        # Print summary statistics
        print("\nFavorability Difference Summary:")
        print("-" * 50)
        print(f"Episode {first_episode} (First):")
        print(f"  Mean difference: {np.mean(first_diffs):.2f}")
        print(f"  Std deviation: {np.std(first_diffs):.2f}")
        print(f"  Agents favoring Bill: {sum(1 for d in first_diffs if d > 0)}")
        print(f"  Agents favoring Bradley: {sum(1 for d in first_diffs if d < 0)}")
        print(f"  Neutral agents: {sum(1 for d in first_diffs if d == 0)}")

        print(f"\nEpisode {final_episode} (Final):")
        print(f"  Mean difference: {np.mean(final_diffs):.2f}")
        print(f"  Std deviation: {np.std(final_diffs):.2f}")
        print(f"  Agents favoring Bill: {sum(1 for d in final_diffs if d > 0)}")
        print(f"  Agents favoring Bradley: {sum(1 for d in final_diffs if d < 0)}")
        print(f"  Neutral agents: {sum(1 for d in final_diffs if d == 0)}")

        # Calculate change in differences
        changes = [final_diffs[i] - first_diffs[i] for i in range(len(agents))]
        print(f"\nChange from Episode {first_episode} to {final_episode}:")
        print(f"  Mean change: {np.mean(changes):.2f}")
        print(f"  Agents with increased Bill preference: {sum(1 for c in changes if c > 0)}")
        print(f"  Agents with increased Bradley preference: {sum(1 for c in changes if c < 0)}")
        print(f"  Agents with no change: {sum(1 for c in changes if c == 0)}")


def main():
    """Main function to run the analysis"""
    import argparse

    parser = argparse.ArgumentParser(description="Generate voting and favorability plots")
    parser.add_argument(
        "--input",
        "-i",
        required=True,
        help="Path to the probe_events.jsonl file to analyze",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        default="specific_plots",
        help="Directory to save generated plots",
    )

    args = parser.parse_args()

    probe_file = args.input
    # Create output directory
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Initialize analyzer
    analyzer = VotingFavorabilityAnalyzer(probe_file)
    analyzer.load_data()

    # Generate the specific plots
    print("\n" + "=" * 60)
    print("GENERATING VOTING EVOLUTION PLOT")
    print("=" * 60)
    analyzer.plot_voting_evolution(output_dir / "voting_evolution.png")

    print("\n" + "=" * 60)
    print("GENERATING FAVORABILITY DIFFERENCE HISTOGRAM")
    print("=" * 60)
    analyzer.plot_favorability_difference_histogram(
        output_dir / "favorability_difference_histogram.png"
    )

    print(f"\nAnalysis complete! Plots saved to {output_dir}")


if __name__ == "__main__":
    main()
