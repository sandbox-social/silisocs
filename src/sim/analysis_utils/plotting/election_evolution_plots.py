"""
Election Evolution Analysis and Plotting Script

This script analyzes the probe_events.jsonl file to create comprehensive
visualizations showing the evolution of favorability ratings and voting
preferences for both candidates throughout the simulation episodes.
"""

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
import yaml

# Set the plotting style
plt.style.use("default")
sns.set_palette("husl")


class ElectionAnalyzer:
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

    """Analyzes election probe events and creates visualizations"""

    def __init__(self, probe_file_path: str):
        """
        Initialize the analyzer with probe events file

        Args:
            probe_file_path: Path to the probe_events.jsonl file
        """
        self.probe_file_path = probe_file_path
        # Typed instance attributes
        self.data: list[dict] = []
        self.favorability_data: list[dict] = []
        self.vote_pref_data: list[dict] = []
        self.vote_intent_data: list[dict] = []
        self.candidates = ["Bill Fredrickson", "Bradley Carter"]

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
                score = data.get("query_return")

                if candidate and score and score.isdigit():
                    self.favorability_data.append(
                        {
                            "episode": episode,
                            "agent": source_user,
                            "candidate": candidate,
                            "favorability": int(score),
                        }
                    )

            elif label == "VotePref":
                candidate1 = data.get("interaction_premise_template", {}).get("candidate1")
                candidate2 = data.get("interaction_premise_template", {}).get("candidate2")
                preference = data.get("query_return")

                if candidate1 and candidate2 and preference:
                    # Map the response to the full candidate name
                    if "Bill" in preference or "bill" in preference:
                        preferred_candidate = "Bill Fredrickson"
                    elif "Bradley" in preference or "bradley" in preference:
                        preferred_candidate = "Bradley Carter"
                    else:
                        preferred_candidate = "Unknown"

                    self.vote_pref_data.append(
                        {
                            "episode": episode,
                            "agent": source_user,
                            "preferred_candidate": preferred_candidate,
                        }
                    )

            elif label == "VoteIntent":
                intent = data.get("query_return")
                if intent:
                    self.vote_intent_data.append(
                        {
                            "episode": episode,
                            "agent": source_user,
                            "will_vote": intent.lower() == "yes",
                        }
                    )

        # Convert to DataFrames
        self.favorability_df = pd.DataFrame(self.favorability_data)
        self.vote_pref_df = pd.DataFrame(self.vote_pref_data)
        self.vote_intent_df = pd.DataFrame(self.vote_intent_data)

        print(f"Processed {len(self.favorability_data)} favorability ratings")
        print(f"Processed {len(self.vote_pref_data)} vote preferences")
        print(f"Processed {len(self.vote_intent_data)} vote intents")

    def plot_favorability_evolution(self, save_path: str | None = None):
        """Plot the evolution of favorability ratings over episodes"""
        if self.favorability_df.empty:
            print("No favorability data to plot")
            return

        # Calculate average favorability by episode and candidate
        avg_favorability = (
            self.favorability_df.groupby(["episode", "candidate"])["favorability"]
            .mean()
            .reset_index()
        )

        # Create the plot
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10))

        # Plot 1: Average favorability over time
        for candidate in self.candidates:
            candidate_data = avg_favorability[avg_favorability["candidate"] == candidate]
            if not candidate_data.empty:
                ax1.plot(
                    candidate_data["episode"],
                    candidate_data["favorability"],
                    marker="o",
                    linewidth=2.5,
                    markersize=6,
                    label=candidate,
                )

        ax1.set_xlabel("Episode")
        ax1.set_ylabel("Average Favorability Rating")
        ax1.set_title("Evolution of Candidate Favorability Ratings", fontsize=14, fontweight="bold")
        ax1.legend()
        ax1.grid(True, alpha=0.3)
        ax1.set_ylim(0, 10)

        # Plot 2: Favorability distribution over time (heatmap-style)
        # Create a pivot table for heatmap
        favorability_pivot = (
            self.favorability_df.groupby(["episode", "candidate"])["favorability"]
            .mean()
            .unstack(fill_value=0)
        )

        if not favorability_pivot.empty:
            im = ax2.imshow(favorability_pivot.T, cmap="RdYlBu_r", aspect="auto", vmin=0, vmax=9)
            ax2.set_yticks(range(len(favorability_pivot.columns)))
            ax2.set_yticklabels(favorability_pivot.columns)
            ax2.set_xlabel("Episode")
            ax2.set_ylabel("Candidate")
            ax2.set_title("Favorability Heatmap Over Episodes", fontsize=14, fontweight="bold")

            # Add colorbar
            cbar = plt.colorbar(im, ax=ax2)
            cbar.set_label("Average Favorability Rating")

            # Add text annotations
            for i in range(len(favorability_pivot.columns)):
                for j in range(len(favorability_pivot.index)):
                    value = favorability_pivot.iloc[j, i]
                    if value > 0:
                        ax2.text(
                            j,
                            i,
                            f"{value:.1f}",
                            ha="center",
                            va="center",
                            color="white" if value < 4.5 else "black",
                            fontweight="bold",
                        )

        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches="tight")
            print(f"Saved favorability evolution plot to {save_path}")

        plt.show()

    def plot_vote_preference_evolution(self, save_path: str | None = None):
        """Plot the evolution of voting preferences over episodes"""
        if self.vote_pref_df.empty:
            print("No vote preference data to plot")
            return

        # Calculate vote share by episode
        vote_counts = (
            self.vote_pref_df.groupby(["episode", "preferred_candidate"])
            .size()
            .unstack(fill_value=0)
        )
        vote_percentages = vote_counts.div(vote_counts.sum(axis=1), axis=0) * 100

        # Create the plot
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10))

        # Plot 1: Vote share over time
        for candidate in self.candidates:
            if candidate in vote_percentages.columns:
                ax1.plot(
                    vote_percentages.index,
                    vote_percentages[candidate],
                    marker="o",
                    linewidth=2.5,
                    markersize=6,
                    label=candidate,
                )

        ax1.set_xlabel("Episode")
        ax1.set_ylabel("Vote Share (%)")
        ax1.set_title("Evolution of Vote Preferences", fontsize=14, fontweight="bold")
        ax1.legend()
        ax1.grid(True, alpha=0.3)
        ax1.set_ylim(0, 100)

        # Plot 2: Stacked area chart
        if not vote_percentages.empty:
            # Prepare data for stacked area plot
            episodes = vote_percentages.index
            bottom = np.zeros(len(episodes))

            # Use a colormap safely via get_cmap (some matplotlib backends may not expose Set3 as an attribute)
            cmap = plt.get_cmap("Set3")
            colors = cmap(np.linspace(0, 1, len(self.candidates)))

            for i, candidate in enumerate(self.candidates):
                if candidate in vote_percentages.columns:
                    values = vote_percentages[candidate].values
                    ax2.fill_between(
                        episodes,
                        bottom,
                        bottom + values,
                        alpha=0.7,
                        label=candidate,
                        color=colors[i],
                    )
                    bottom += values

        ax2.set_xlabel("Episode")
        ax2.set_ylabel("Vote Share (%)")
        ax2.set_title("Vote Share Distribution Over Episodes", fontsize=14, fontweight="bold")
        ax2.legend()
        ax2.set_ylim(0, 100)

        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches="tight")
            print(f"Saved vote preference evolution plot to {save_path}")

        plt.show()

    def plot_comprehensive_dashboard(self, save_path: str | None = None):
        """Create a comprehensive dashboard with multiple visualizations"""
        fig = plt.figure(figsize=(16, 12))

        # Create a 3x2 grid
        gs = fig.add_gridspec(3, 2, hspace=0.3, wspace=0.3)

        # 1. Average favorability over time
        ax1 = fig.add_subplot(gs[0, :])
        if not self.favorability_df.empty:
            avg_favorability = (
                self.favorability_df.groupby(["episode", "candidate"])["favorability"]
                .mean()
                .reset_index()
            )

            for candidate in self.candidates:
                candidate_data = avg_favorability[avg_favorability["candidate"] == candidate]
                if not candidate_data.empty:
                    ax1.plot(
                        candidate_data["episode"],
                        candidate_data["favorability"],
                        marker="o",
                        linewidth=2.5,
                        markersize=6,
                        label=candidate,
                    )

            ax1.set_xlabel("Episode")
            ax1.set_ylabel("Average Favorability")
            ax1.set_title("Favorability Evolution", fontsize=14, fontweight="bold")
            ax1.legend()
            ax1.grid(True, alpha=0.3)
            ax1.set_ylim(0, 10)

        # 2. Vote share over time
        ax2 = fig.add_subplot(gs[1, 0])
        if not self.vote_pref_df.empty:
            vote_counts = (
                self.vote_pref_df.groupby(["episode", "preferred_candidate"])
                .size()
                .unstack(fill_value=0)
            )
            vote_percentages = vote_counts.div(vote_counts.sum(axis=1), axis=0) * 100

            for candidate in self.candidates:
                if candidate in vote_percentages.columns:
                    ax2.plot(
                        vote_percentages.index,
                        vote_percentages[candidate],
                        marker="o",
                        linewidth=2,
                        markersize=5,
                        label=candidate,
                    )

            ax2.set_xlabel("Episode")
            ax2.set_ylabel("Vote Share (%)")
            ax2.set_title("Vote Preferences", fontsize=12, fontweight="bold")
            ax2.legend()
            ax2.grid(True, alpha=0.3)
            ax2.set_ylim(0, 100)

        # 3. Voting intent over time
        ax3 = fig.add_subplot(gs[1, 1])
        if not self.vote_intent_df.empty:
            vote_intent_pct = self.vote_intent_df.groupby("episode")["will_vote"].mean() * 100
            ax3.plot(
                vote_intent_pct.index,
                vote_intent_pct.values,
                marker="o",
                linewidth=2,
                markersize=5,
                color="green",
            )
            ax3.set_xlabel("Episode")
            ax3.set_ylabel("Voting Intent (%)")
            ax3.set_title("Intention to Vote", fontsize=12, fontweight="bold")
            ax3.grid(True, alpha=0.3)
            ax3.set_ylim(0, 100)

        # 4. Favorability distribution by candidate
        ax4 = fig.add_subplot(gs[2, 0])
        if not self.favorability_df.empty:
            # Box plot of favorability distributions
            favorability_data = []
            labels = []
            for candidate in self.candidates:
                candidate_ratings = self.favorability_df[
                    self.favorability_df["candidate"] == candidate
                ]["favorability"]
                if not candidate_ratings.empty:
                    favorability_data.append(candidate_ratings)
                    labels.append(candidate)

            if favorability_data:
                # Axes.boxplot may not accept 'labels' in some strict typings; draw boxplot and set xticklabels explicitly
                ax4.boxplot(favorability_data)
                ax4.set_xticks(range(1, len(labels) + 1))
                ax4.set_xticklabels(labels)
                ax4.set_ylabel("Favorability Rating")
                ax4.set_title("Favorability Distribution", fontsize=12, fontweight="bold")
                ax4.grid(True, alpha=0.3)

        # 5. Final episode summary
        ax5 = fig.add_subplot(gs[2, 1])
        if not self.favorability_df.empty and not self.vote_pref_df.empty:
            # Get the latest episode data
            latest_episode = max(
                self.favorability_df["episode"].max(), self.vote_pref_df["episode"].max()
            )

            # Latest favorability
            latest_fav = self.favorability_df[self.favorability_df["episode"] == latest_episode]
            latest_fav_avg = latest_fav.groupby("candidate")["favorability"].mean()

            # Latest vote preferences
            latest_votes = self.vote_pref_df[self.vote_pref_df["episode"] == latest_episode]
            latest_vote_counts = latest_votes["preferred_candidate"].value_counts()
            latest_vote_pct = (latest_vote_counts / latest_vote_counts.sum()) * 100

            # Create bar chart
            x_pos = np.arange(len(self.candidates))
            width = 0.35

            fav_values = [latest_fav_avg.get(candidate, 0) for candidate in self.candidates]
            vote_values = [latest_vote_pct.get(candidate, 0) for candidate in self.candidates]

            bars1 = ax5.bar(
                x_pos - width / 2, fav_values, width, label="Favorability (0-10)", alpha=0.8
            )
            bars2 = ax5.bar(
                x_pos + width / 2,
                [v / 10 for v in vote_values],
                width,
                label="Vote Share (%/10)",
                alpha=0.8,
            )

            ax5.set_xlabel("Candidate")
            ax5.set_ylabel("Rating / Percentage")
            ax5.set_title(f"Final State (Episode {latest_episode})", fontsize=12, fontweight="bold")
            ax5.set_xticks(x_pos)
            ax5.set_xticklabels([c.split()[0] for c in self.candidates])  # Use first names
            ax5.legend()
            ax5.grid(True, alpha=0.3)

        plt.suptitle("Election Simulation Dashboard", fontsize=16, fontweight="bold")

        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches="tight")
            print(f"Saved comprehensive dashboard to {save_path}")

        plt.show()

    def plot_individual_agent_tracking(self, save_path: str | None = None):
        """Plot individual agent favorability changes over time"""
        if self.favorability_df.empty:
            print("No favorability data to plot")
            return

        # Get unique agents
        agents = self.favorability_df["agent"].unique()

        # Create subplot for each candidate
        fig, axes = plt.subplots(1, 2, figsize=(16, 8))

        for i, candidate in enumerate(self.candidates):
            candidate_data = self.favorability_df[self.favorability_df["candidate"] == candidate]

            # Plot each agent's trajectory
            for agent in agents:
                agent_data = candidate_data[candidate_data["agent"] == agent].sort_values("episode")
                if len(agent_data) > 1:  # Only plot if agent has multiple data points
                    axes[i].plot(
                        agent_data["episode"],
                        agent_data["favorability"],
                        marker="o",
                        alpha=0.6,
                        linewidth=1,
                        markersize=3,
                        label=agent,
                    )

            # Calculate and plot average
            avg_data = candidate_data.groupby("episode")["favorability"].mean().reset_index()
            axes[i].plot(
                avg_data["episode"],
                avg_data["favorability"],
                color="black",
                linewidth=3,
                marker="o",
                markersize=6,
                label="Average",
            )

            axes[i].set_xlabel("Episode")
            axes[i].set_ylabel("Favorability Rating")
            axes[i].set_title(f"{candidate} - Individual Agent Trajectories", fontweight="bold")
            axes[i].grid(True, alpha=0.3)
            axes[i].set_ylim(0, 10)

            # Only show legend for first plot to avoid clutter
            if i == 0:
                axes[i].legend(bbox_to_anchor=(1.05, 1), loc="upper left", fontsize=8)

        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches="tight")
            print(f"Saved individual agent tracking plot to {save_path}")

        plt.show()

    def generate_summary_statistics(self):
        """Generate and print summary statistics"""
        print("\n" + "=" * 60)
        print("ELECTION SIMULATION SUMMARY STATISTICS")
        print("=" * 60)

        if not self.favorability_df.empty:
            print("\nFAVORABILITY ANALYSIS:")
            print("-" * 30)

            for candidate in self.candidates:
                candidate_data = self.favorability_df[
                    self.favorability_df["candidate"] == candidate
                ]
                if not candidate_data.empty:
                    mean_fav = candidate_data["favorability"].mean()
                    std_fav = candidate_data["favorability"].std()
                    initial_fav = candidate_data[
                        candidate_data["episode"] == candidate_data["episode"].min()
                    ]["favorability"].mean()
                    final_fav = candidate_data[
                        candidate_data["episode"] == candidate_data["episode"].max()
                    ]["favorability"].mean()

                    print(f"{candidate}:")
                    print(f"  Average favorability: {mean_fav:.2f} ± {std_fav:.2f}")
                    print(f"  Initial favorability: {initial_fav:.2f}")
                    print(f"  Final favorability: {final_fav:.2f}")
                    print(f"  Change: {final_fav - initial_fav:+.2f}")
                    print()

        if not self.vote_pref_df.empty:
            print("VOTING PREFERENCES:")
            print("-" * 30)

            # Overall vote distribution
            total_votes = self.vote_pref_df["preferred_candidate"].value_counts()
            total_pct = (total_votes / total_votes.sum()) * 100

            for candidate in self.candidates:
                if candidate in total_votes:
                    print(
                        f"{candidate}: {total_votes[candidate]} votes ({total_pct[candidate]:.1f}%)"
                    )

            # Final episode vote distribution
            final_episode = self.vote_pref_df["episode"].max()
            final_votes = self.vote_pref_df[self.vote_pref_df["episode"] == final_episode][
                "preferred_candidate"
            ].value_counts()
            final_pct = (final_votes / final_votes.sum()) * 100

            print(f"\nFinal Episode ({final_episode}) Voting:")
            for candidate in self.candidates:
                if candidate in final_votes:
                    print(
                        f"  {candidate}: {final_votes[candidate]} votes ({final_pct[candidate]:.1f}%)"
                    )

        if not self.vote_intent_df.empty:
            print("\nVOTING INTENT:")
            print("-" * 30)
            overall_intent = self.vote_intent_df["will_vote"].mean() * 100
            print(f"Overall voting intent: {overall_intent:.1f}%")

            final_episode = self.vote_intent_df["episode"].max()
            final_intent = (
                self.vote_intent_df[self.vote_intent_df["episode"] == final_episode][
                    "will_vote"
                ].mean()
                * 100
            )
            print(f"Final episode voting intent: {final_intent:.1f}%")


def main():
    """Main function to run the analysis"""
    parser = argparse.ArgumentParser(description="Analyze and plot election simulation evolution")
    parser.add_argument(
        "--input",
        "-i",
        help="Path to the probe_events.jsonl file",
    )
    parser.add_argument("--output-dir", "-o", default="plots", help="Directory to save plots")
    parser.add_argument(
        "--plots",
        "-p",
        nargs="+",
        choices=["favorability", "voting", "dashboard", "agents", "all"],
        default=["all"],
        help="Which plots to generate",
    )

    args = parser.parse_args()

    # Create output directory
    output_dir = Path(args.output_dir)
    output_dir.mkdir(exist_ok=True)

    # Initialize analyzer
    analyzer = ElectionAnalyzer(args.input)
    analyzer.load_data()

    # Generate summary statistics
    analyzer.generate_summary_statistics()

    # Generate plots
    plots_to_generate = (
        args.plots if "all" not in args.plots else ["favorability", "voting", "dashboard", "agents"]
    )

    if "favorability" in plots_to_generate:
        analyzer.plot_favorability_evolution(output_dir / "favorability_evolution.png")

    if "voting" in plots_to_generate:
        analyzer.plot_vote_preference_evolution(output_dir / "voting_evolution.png")

    if "dashboard" in plots_to_generate:
        analyzer.plot_comprehensive_dashboard(output_dir / "election_dashboard.png")

    if "agents" in plots_to_generate:
        analyzer.plot_individual_agent_tracking(output_dir / "agent_tracking.png")

    print(f"\nAnalysis complete! Plots saved to {output_dir}")


if __name__ == "__main__":
    main()
