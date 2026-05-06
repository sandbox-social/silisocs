"""
2D Scatter Plot Analysis for Election Data

This script creates scatter plots showing:
1. Favorability of Bill vs Bradley by agent category and episode
2. Vote preference transitions by agent category and episode

Color scheme:
- Conservative: Blue
- Independent: Gray
- Progressive (Liberal): Red
- Candidates: Yellow

Marker scheme:
- Episode 0 (first): Open markers
- Episode 17 (final): Filled markers
"""

import json
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml


class ScatterAnalyzer:
    """ScatterAnalyzer.

    Constructor parameters:

    __init__.

    :param str probe_file_path:
    :type probe_file_path: str
    """

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
        categories = {"conservative": [], "liberal": [], "independent": [], "candidate": []}
        for agent in agent_list:
            cat = self.agent_party_map.get(agent, "candidate")
            categories[cat].append(agent)
        return categories

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
                raw_score = data.get("query_return")
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
                preference = data.get("query_return", "")

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

    def get_favorability_data_for_episodes(self, target_episodes):
        """Get favorability data with forward-filling for specific episodes"""
        if self.favorability_df.empty:
            return {}

        # Build agent -> episode -> candidate -> favorability mapping
        agent_episode_candidate = defaultdict(lambda: defaultdict(dict))
        for _, row in self.favorability_df.iterrows():
            ag = row["agent"]
            ep = row["episode"]
            cand = row["candidate"]
            fav = row["favorability"]
            agent_episode_candidate[ag][ep][cand] = fav

        episode_data = {}

        # For each target episode
        for target_ep in target_episodes:
            episode_data[target_ep] = {}

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

                    if ep == target_ep:
                        # At the target episode, include agent only if we have values for both
                        b = prev_vals["Bill Fredrickson"]
                        r = prev_vals["Bradley Carter"]
                        if b is not None and r is not None:
                            episode_data[target_ep][agent] = {
                                "Bill Fredrickson": b,
                                "Bradley Carter": r,
                            }
                        break

        return episode_data

    def plot_favorability_scatter(self, save_path=None):
        """Create 2D scatter plot of Bill vs Bradley favorability"""
        if self.favorability_df.empty:
            print("No favorability data to plot")
            return

        # Get first and last episodes
        episodes = sorted(self.favorability_df["episode"].unique())
        if len(episodes) < 2:
            print("Need at least 2 episodes for comparison")
            return

        first_episode = episodes[0]
        final_episode = episodes[-1]

        # Get favorability data for both episodes
        episode_data = self.get_favorability_data_for_episodes([first_episode, final_episode])

        # Get agents that appear in both episodes
        first_agents = set(episode_data[first_episode].keys())
        final_agents = set(episode_data[final_episode].keys())
        common_agents = first_agents & final_agents

        if not common_agents:
            print("No agents found with favorability ratings for both candidates in both episodes")
            return

        # Categorize agents
        categorized = self.categorize_agents(list(common_agents))

        # Color mapping
        color_map = {
            "conservative": "blue",
            "independent": "gray",
            "liberal": "red",
            "candidate": "gold",
        }

        # Marker mapping for different groups
        marker_map = {
            "conservative": "o",  # circle
            "independent": "s",  # square
            "liberal": "^",  # triangle up
            "candidate": "D",  # diamond
        }

        # Create the plot
        fig, ax = plt.subplots(figsize=(12, 10))

        # Plot each category
        for category, agents in categorized.items():
            if not agents:
                continue

            # First episode data (open markers)
            first_bill = [
                episode_data[first_episode][agent]["Bill Fredrickson"] for agent in agents
            ]
            first_bradley = [
                episode_data[first_episode][agent]["Bradley Carter"] for agent in agents
            ]

            # Final episode data (filled markers)
            final_bill = [
                episode_data[final_episode][agent]["Bill Fredrickson"] for agent in agents
            ]
            final_bradley = [
                episode_data[final_episode][agent]["Bradley Carter"] for agent in agents
            ]

            color = color_map[category]
            marker = marker_map[category]
            label_name = "Progressive" if category == "liberal" else category.capitalize()

            # Plot first episode (open markers)
            ax.scatter(
                first_bill,
                first_bradley,
                c="none",
                edgecolors=color,
                s=80,
                linewidth=2,
                marker=marker,
                label=f"{label_name} (Episode {first_episode})",
                alpha=0.7,
            )

            # Plot final episode (filled markers)
            ax.scatter(
                final_bill,
                final_bradley,
                c=color,
                s=80,
                marker=marker,
                label=f"{label_name} (Episode {final_episode})",
                alpha=0.8,
            )

            # Draw arrows showing transitions
            for i, agent in enumerate(agents):
                ax.annotate(
                    "",
                    xy=(final_bill[i], final_bradley[i]),
                    xytext=(first_bill[i], first_bradley[i]),
                    arrowprops=dict(arrowstyle="->", color=color, alpha=0.3, lw=1),
                )

        # Formatting
        ax.set_xlabel("Favorability: Bill Fredrickson", fontsize=14)
        ax.set_ylabel("Favorability: Bradley Carter", fontsize=14)
        ax.set_title(
            f"Agent Favorability: Bill vs Bradley\n(Episode {first_episode} → Episode {final_episode})",
            fontsize=16,
            fontweight="bold",
        )

        # Add diagonal line for reference (equal favorability)
        min_val = min(ax.get_xlim()[0], ax.get_ylim()[0])
        max_val = max(ax.get_xlim()[1], ax.get_ylim()[1])
        ax.plot(
            [min_val, max_val], [min_val, max_val], "k--", alpha=0.3, label="Equal Favorability"
        )

        ax.legend(bbox_to_anchor=(1.05, 1), loc="upper left")
        ax.grid(True, alpha=0.3)

        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches="tight")
            print(f"Saved favorability scatter plot to {save_path}")

        plt.show()

    def plot_vote_preference_scatter(self, save_path=None):
        """Create 2D scatter plot showing vote preference transitions"""
        if self.vote_pref_df.empty:
            print("No vote preference data to plot")
            return

        # Get first and last episodes
        episodes = sorted(self.vote_pref_df["episode"].unique())
        if len(episodes) < 2:
            print("Need at least 2 episodes for comparison")
            return

        first_episode = episodes[0]
        final_episode = episodes[-1]

        # Get vote preferences for both episodes
        first_votes = {}
        final_votes = {}

        first_ep_data = self.vote_pref_df[self.vote_pref_df["episode"] == first_episode]
        final_ep_data = self.vote_pref_df[self.vote_pref_df["episode"] == final_episode]

        for _, row in first_ep_data.iterrows():
            first_votes[row["agent"]] = row["preferred_candidate"]

        for _, row in final_ep_data.iterrows():
            final_votes[row["agent"]] = row["preferred_candidate"]

        # Get common agents
        common_agents = set(first_votes.keys()) & set(final_votes.keys())

        if not common_agents:
            print("No agents found with vote preferences in both episodes")
            return

        # Categorize agents
        categorized = self.categorize_agents(list(common_agents))

        # Color mapping
        color_map = {
            "conservative": "blue",
            "independent": "gray",
            "liberal": "red",
            "candidate": "gold",
        }

        # Marker mapping for different groups
        marker_map = {
            "conservative": "o",  # circle
            "independent": "s",  # square
            "liberal": "^",  # triangle up
            "candidate": "D",  # diamond
        }

        # Create the plot - using discrete positions for vote choices
        fig, ax = plt.subplots(figsize=(10, 8))

        # Define positions for candidates
        candidate_positions = {"Bill Fredrickson": 0, "Bradley Carter": 1}

        # Plot each category
        for category, agents in categorized.items():
            if not agents:
                continue

            color = color_map[category]
            marker = marker_map[category]
            label_name = "Progressive" if category == "liberal" else category.capitalize()

            # Get positions for this category's agents
            first_positions = [candidate_positions[first_votes[agent]] for agent in agents]
            final_positions = [candidate_positions[final_votes[agent]] for agent in agents]

            # Add some jitter to avoid overlapping points
            jitter_strength = 0.1
            first_jitter = np.random.normal(0, jitter_strength, len(agents))
            final_jitter = np.random.normal(0, jitter_strength, len(agents))

            first_x = np.array(first_positions) + first_jitter
            final_x = np.array(final_positions) + final_jitter

            # Plot first episode (open markers)
            ax.scatter(
                first_x,
                [0.3] * len(agents),
                c="none",
                edgecolors=color,
                s=100,
                linewidth=2,
                marker=marker,
                label=f"{label_name} (Episode {first_episode})",
                alpha=0.7,
            )

            # Plot final episode (filled markers)
            ax.scatter(
                final_x,
                [0.7] * len(agents),
                c=color,
                s=100,
                marker=marker,
                label=f"{label_name} (Episode {final_episode})",
                alpha=0.8,
            )

            # Draw arrows showing transitions
            for i, agent in enumerate(agents):
                ax.annotate(
                    "",
                    xy=(final_x[i], 0.7),
                    xytext=(first_x[i], 0.3),
                    arrowprops=dict(arrowstyle="->", color=color, alpha=0.5, lw=1.5),
                )

        # Formatting
        ax.set_xlim(-0.5, 1.5)
        ax.set_ylim(0, 1)
        ax.set_xticks([0, 1])
        ax.set_xticklabels(["Bill Fredrickson", "Bradley Carter"])
        ax.set_yticks([0.3, 0.7])
        ax.set_yticklabels([f"Episode {first_episode}", f"Episode {final_episode}"])

        ax.set_xlabel("Vote Preference", fontsize=14)
        ax.set_ylabel("Episode", fontsize=14)
        ax.set_title(
            f"Vote Preference Transitions\n(Episode {first_episode} → Episode {final_episode})",
            fontsize=16,
            fontweight="bold",
        )

        ax.legend(bbox_to_anchor=(1.05, 1), loc="upper left")
        ax.grid(True, alpha=0.3, axis="x")

        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches="tight")
            print(f"Saved vote preference scatter plot to {save_path}")

        plt.show()


def main():
    """Main function to run the analysis"""
    import argparse

    parser = argparse.ArgumentParser(description="Generate scatter plots for election data")
    parser.add_argument("--input", "-i", required=True, help="Path to probe_events.jsonl file")
    parser.add_argument(
        "--output-dir",
        "-o",
        default="scatter_plots",
        help="Directory to save generated plots",
    )

    args = parser.parse_args()

    probe_file = args.input
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Initialize analyzer
    analyzer = ScatterAnalyzer(probe_file)
    analyzer.load_data()

    # Generate the scatter plots
    print("\n" + "=" * 60)
    print("GENERATING FAVORABILITY SCATTER PLOT")
    print("=" * 60)
    analyzer.plot_favorability_scatter(output_dir / "favorability_scatter.png")

    print("\n" + "=" * 60)
    print("GENERATING VOTE PREFERENCE SCATTER PLOT")
    print("=" * 60)
    analyzer.plot_vote_preference_scatter(output_dir / "vote_preference_scatter.png")

    print(f"\nAnalysis complete! Plots saved to {output_dir}")


if __name__ == "__main__":
    main()
