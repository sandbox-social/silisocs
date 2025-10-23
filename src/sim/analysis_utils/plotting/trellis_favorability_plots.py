"""
Trellis Chart Analysis for Election Data

This script creates a trellis (small multiples) chart where each subplot shows:
- Favorability trends for Bill and Bradley over episodes for a single agent
- Organized by party affiliation: Progressive, Conservative, Independent, Candidates
- Forward-fills missing favorability values from previous episodes
"""

import argparse
import json
import math
import os
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import yaml


class TrellisAnalyzer:
    def __init__(self, probe_file_paths: list, separate_runs: bool = False, in_one: bool = False):
        self.probe_file_paths = (
            probe_file_paths if isinstance(probe_file_paths, list) else [probe_file_paths]
        )
        self.separate_runs: bool = separate_runs
        self.in_one: bool = in_one
        self.all_files_data: list[list[dict]] = []  # Store data from all files
        self.favorability_data: list[dict] = []
        self.individual_timeseries: list[
            dict
        ] = []  # Store individual run timeseries when separate_runs=True or in_one=True

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
        categories = {"liberal": [], "conservative": [], "independent": [], "candidate": []}
        for agent in agent_list:
            cat = self.agent_party_map.get(agent, "candidate")
            categories[cat].append(agent)
        return categories

    def load_data(self):
        """Load and parse the probe events data from multiple files"""
        print(f"Loading data from {len(self.probe_file_paths)} files...")

        for file_idx, probe_file_path in enumerate(self.probe_file_paths):
            print(
                f"  Processing file {file_idx + 1}/{len(self.probe_file_paths)}: {probe_file_path}"
            )
            file_data = []

            with open(probe_file_path, encoding="utf-8") as f:
                for line in f:
                    try:
                        event = json.loads(line.strip())
                        file_data.append(event)
                    except json.JSONDecodeError:
                        continue

            self.all_files_data.append(file_data)
            print(f"    Loaded {len(file_data)} events from this file")

        print(f"Total files processed: {len(self.all_files_data)}")
        # Load agent party map for categorization
        self.load_agent_party_map()
        self._process_all_files_data()

    def _process_all_files_data(self):
        """Process the raw data from all files into structured formats"""
        # Process each file separately to maintain forward-fill integrity within files
        all_files_timeseries = []

        for file_idx, file_data in enumerate(self.all_files_data):
            print(f"  Processing favorability data from file {file_idx + 1}...")
            file_favorability_data = []

            for event in file_data:
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
                            # Check for NaN first (covers numpy.nan, float('nan'), etc.)
                            if pd.isna(raw_score):
                                favorability = None
                            elif isinstance(raw_score, (int, float)) and not pd.isna(raw_score):
                                # Valid numeric value
                                favorability = int(raw_score)
                            else:
                                # Handle string values
                                s = str(raw_score).strip()
                                # Check for string representations of nan
                                if s.lower() in ["nan", "null", "none", ""]:
                                    favorability = None
                                elif s.replace(".", "").replace("-", "").isdigit():
                                    # Handle integer or float strings
                                    favorability = int(float(s))
                        except (ValueError, TypeError):
                            favorability = None

                    if candidate:
                        file_favorability_data.append(
                            {
                                "episode": episode,
                                "agent": source_user,
                                "candidate": candidate,
                                "favorability": favorability,
                            }
                        )

            # Convert to DataFrame for this file
            file_favorability_df = pd.DataFrame(file_favorability_data)

            # Get timeseries for this file with forward-filling
            file_timeseries = self._get_file_favorability_timeseries(file_favorability_df)
            all_files_timeseries.append(file_timeseries)

            print(
                f"    Processed {len(file_favorability_data)} favorability ratings from file {file_idx + 1}"
            )

        if self.separate_runs or self.in_one:
            # Store individual timeseries for separate plotting or overlaying
            self.individual_timeseries = all_files_timeseries
            print(f"Individual run timeseries prepared: {len(all_files_timeseries)} runs")

        if not self.separate_runs:
            # Average across all files (used for default mode or in_one mode for layout reference)
            self.averaged_timeseries = self._average_timeseries_across_files(all_files_timeseries)
            print(f"Agents with averaged timeseries: {len(self.averaged_timeseries)}")

        total_ratings = sum(len(file_data) for file_data in self.all_files_data)
        print(f"Total favorability ratings processed across all files: {total_ratings}")

    def get_agent_favorability_timeseries(self):
        """Get favorability time series for each agent with forward-filling"""
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

        agent_timeseries = {}
        episodes_sorted = sorted(self.favorability_df["episode"].unique())

        # For each agent, create time series with forward-filling
        for agent, ep_map in agent_episode_candidate.items():
            agent_timeseries[agent] = {
                "episodes": episodes_sorted,
                "Bill Fredrickson": [],
                "Bradley Carter": [],
            }

            # Track previous values for forward-filling
            prev_vals = {"Bill Fredrickson": None, "Bradley Carter": None}

            for ep in episodes_sorted:
                rec = ep_map.get(ep, {})

                for cand in ["Bill Fredrickson", "Bradley Carter"]:
                    if cand in rec:
                        # explicit value (may be None)
                        if rec[cand] is not None:
                            prev_vals[cand] = rec[cand]
                        # If None, keep previous value (forward-fill)

                    # Append current value (could be None if no previous value)
                    agent_timeseries[agent][cand].append(prev_vals[cand])

        # Only return agents that have at least some data for both candidates
        filtered_timeseries = {}
        for agent, data in agent_timeseries.items():
            bill_values = [v for v in data["Bill Fredrickson"] if v is not None]
            bradley_values = [v for v in data["Bradley Carter"] if v is not None]
            if bill_values and bradley_values:
                filtered_timeseries[agent] = data

        return filtered_timeseries

    def _get_file_favorability_timeseries(self, favorability_df):
        """Get favorability time series for each agent from a single file with forward-filling"""
        if favorability_df.empty:
            return {}

        # Build agent -> episode -> candidate -> favorability mapping
        agent_episode_candidate = defaultdict(lambda: defaultdict(dict))
        for _, row in favorability_df.iterrows():
            ag = row["agent"]
            ep = row["episode"]
            cand = row["candidate"]
            fav = row["favorability"]
            agent_episode_candidate[ag][ep][cand] = fav

        agent_timeseries = {}
        episodes_sorted = sorted(favorability_df["episode"].unique())
        print(f"    Episodes found: {episodes_sorted}")

        # For each agent, create time series with forward-filling
        for agent, ep_map in agent_episode_candidate.items():
            agent_timeseries[agent] = {
                "episodes": episodes_sorted,
                "Bill Fredrickson": [],
                "Bradley Carter": [],
            }

            # Track previous valid values for forward-filling
            prev_valid_vals = {"Bill Fredrickson": None, "Bradley Carter": None}

            # Debug specific agents
            debug_agent = agent in ["Sophia Johnson", "Alex Carter", "Bill Fredrickson"]
            if debug_agent:
                print(f"    DEBUG: Processing {agent}")

            for ep in episodes_sorted:
                rec = ep_map.get(ep, {})

                for cand in ["Bill Fredrickson", "Bradley Carter"]:
                    current_val = None

                    # Check if we have a value for this episode and candidate
                    if cand in rec and rec[cand] is not None and not pd.isna(rec[cand]):
                        # We have a valid (non-null, non-NaN) value
                        current_val = rec[cand]
                        prev_valid_vals[cand] = current_val
                        if debug_agent:
                            print(f"      Episode {ep}, {cand}: {current_val} (actual)")
                    else:
                        # No value, null value, or NaN value - use the last valid value (forward-fill)
                        current_val = prev_valid_vals[cand]
                        if debug_agent:
                            if cand in rec:
                                if pd.isna(rec[cand]):
                                    print(
                                        f"      Episode {ep}, {cand}: {current_val} (forward-filled from NaN)"
                                    )
                                else:
                                    print(
                                        f"      Episode {ep}, {cand}: {current_val} (forward-filled from null)"
                                    )
                            else:
                                print(
                                    f"      Episode {ep}, {cand}: {current_val} (forward-filled from missing)"
                                )

                    # Append current value (forward-filled if necessary)
                    agent_timeseries[agent][cand].append(current_val)

        # Only return agents that have at least some data for both candidates
        # (i.e., at least one non-None value for each candidate)
        filtered_timeseries = {}
        for agent, data in agent_timeseries.items():
            bill_values = [v for v in data["Bill Fredrickson"] if v is not None]
            bradley_values = [v for v in data["Bradley Carter"] if v is not None]

            debug_agent = agent in ["Sophia Johnson", "Alex Carter", "Bill Fredrickson"]
            if debug_agent:
                print(f"    DEBUG: Filtering {agent}")
                print(
                    f"      Bill non-None values: {len(bill_values)} out of {len(data['Bill Fredrickson'])}"
                )
                print(
                    f"      Bradley non-None values: {len(bradley_values)} out of {len(data['Bradley Carter'])}"
                )
                print(f"      Bill series: {data['Bill Fredrickson'][:5]}...")
                print(f"      Bradley series: {data['Bradley Carter'][:5]}...")

            if bill_values and bradley_values:
                # Ensure we have complete time series (no None values after filtering)
                # If any episode still has None, we need to post-process
                bill_series = data["Bill Fredrickson"][:]
                bradley_series = data["Bradley Carter"][:]

                # Final forward-fill pass to ensure no None values remain
                for i in range(len(bill_series)):
                    if bill_series[i] is None and i > 0:
                        bill_series[i] = bill_series[i - 1]
                        if debug_agent:
                            print(
                                f"      Final forward-fill: Episode {episodes_sorted[i]} Bill = {bill_series[i]}"
                            )
                    if bradley_series[i] is None and i > 0:
                        bradley_series[i] = bradley_series[i - 1]
                        if debug_agent:
                            print(
                                f"      Final forward-fill: Episode {episodes_sorted[i]} Bradley = {bradley_series[i]}"
                            )

                # Only include if we have valid starting values
                if bill_series[0] is not None and bradley_series[0] is not None:
                    filtered_timeseries[agent] = {
                        "episodes": episodes_sorted,
                        "Bill Fredrickson": bill_series,
                        "Bradley Carter": bradley_series,
                    }
                    if debug_agent:
                        print(f"      INCLUDED: {agent} with {len(bill_series)} episodes")
                elif debug_agent:
                    print(
                        f"      EXCLUDED: {agent} - missing starting values (Bill: {bill_series[0]}, Bradley: {bradley_series[0]})"
                    )
            elif debug_agent:
                print(f"      EXCLUDED: {agent} - insufficient data for both candidates")

        return filtered_timeseries

    def _average_timeseries_across_files(self, all_files_timeseries):
        """Average favorability timeseries across multiple files"""
        if not all_files_timeseries:
            return {}

        # Get all unique agents across all files
        all_agents = set()
        for file_timeseries in all_files_timeseries:
            all_agents.update(file_timeseries.keys())

        averaged_timeseries = {}

        # Get common episodes (intersection of all files that have data)
        all_episodes = None
        for file_timeseries in all_files_timeseries:
            if file_timeseries:
                file_episodes = set(next(iter(file_timeseries.values()))["episodes"])
                if all_episodes is None:
                    all_episodes = file_episodes
                else:
                    all_episodes = all_episodes.intersection(file_episodes)

        if all_episodes is None:
            return {}

        episodes_sorted = sorted(list(all_episodes))

        for agent in all_agents:
            # Check if agent appears in at least one file
            agent_data = []
            for file_timeseries in all_files_timeseries:
                if agent in file_timeseries:
                    agent_data.append(file_timeseries[agent])

            if not agent_data:
                continue

            # Initialize averaged data structure
            averaged_timeseries[agent] = {
                "episodes": episodes_sorted,
                "Bill Fredrickson": [],
                "Bradley Carter": [],
            }

            # Average across episodes
            for ep_idx, ep in enumerate(episodes_sorted):
                for candidate in ["Bill Fredrickson", "Bradley Carter"]:
                    # Calculate average if we have values
                    values = []
                    for file_agent_data in agent_data:
                        # Check if this episode exists in this file's data
                        if ep in file_agent_data["episodes"]:
                            file_ep_idx = file_agent_data["episodes"].index(ep)
                            if file_ep_idx < len(file_agent_data[candidate]):
                                val = file_agent_data[candidate][file_ep_idx]
                                # More comprehensive NaN checking
                                if (
                                    val is not None
                                    and not pd.isna(val)
                                    and not (isinstance(val, str) and val.lower() == "nan")
                                ):
                                    values.append(val)

                    # Calculate average if we have valid values
                    if values:
                        avg_value = sum(values) / len(values)
                        averaged_timeseries[agent][candidate].append(avg_value)
                    # If no values available, try to forward-fill from previous episode
                    elif ep_idx > 0 and averaged_timeseries[agent][candidate]:
                        # Use the previous episode's value
                        prev_val = averaged_timeseries[agent][candidate][-1]
                        averaged_timeseries[agent][candidate].append(prev_val)
                    else:
                        # No previous value available - this shouldn't happen with proper forward-fill
                        # but we'll set to None for now
                        averaged_timeseries[agent][candidate].append(None)

        # Final cleanup: ensure no None values remain and filter agents
        filtered_averaged = {}
        for agent, data in averaged_timeseries.items():
            # Check if we have any valid data
            bill_values = [v for v in data["Bill Fredrickson"] if v is not None]
            bradley_values = [v for v in data["Bradley Carter"] if v is not None]

            if bill_values and bradley_values:
                # Clean up any remaining None values with forward-fill
                bill_clean = data["Bill Fredrickson"][:]
                bradley_clean = data["Bradley Carter"][:]

                # Forward-fill any remaining None values
                for i in range(len(bill_clean)):
                    if bill_clean[i] is None and i > 0:
                        bill_clean[i] = bill_clean[i - 1]
                    if bradley_clean[i] is None and i > 0:
                        bradley_clean[i] = bradley_clean[i - 1]

                # Only include agents with complete data starting from episode 0
                if bill_clean[0] is not None and bradley_clean[0] is not None:
                    filtered_averaged[agent] = {
                        "episodes": episodes_sorted,
                        "Bill Fredrickson": bill_clean,
                        "Bradley Carter": bradley_clean,
                    }

        return filtered_averaged

    def plot_trellis_favorability(self, save_path=None):
        """Create trellis chart of favorability trends by agent and party"""
        if self.separate_runs:
            self._plot_separate_runs(save_path)
            return

        if self.in_one:
            self._plot_in_one(save_path)
            return

        # Use averaged time series data
        agent_timeseries = self.averaged_timeseries

        if not agent_timeseries:
            print("No averaged favorability time series data available")
            return

        # Categorize agents
        agents_list = list(agent_timeseries.keys())
        categorized = self.categorize_agents(agents_list)

        # Define the order for plotting: Progressive, Conservative, Independent, Candidates
        category_order = ["liberal", "conservative", "independent", "candidate"]
        category_labels = {
            "liberal": "Progressive",
            "conservative": "Conservative",
            "independent": "Independent",
            "candidate": "Candidates",
        }

        # Organize agents by category with proper row structure
        # First 3 groups get 6 agents per row, Candidates get 2 per row
        organized_rows = []
        for category in category_order:
            if categorized[category]:
                sorted_agents = sorted(categorized[category], key=lambda x: x.split()[0])
                category_name = category_labels[category]

                if category == "candidate":
                    # Candidates: 2 per row
                    agents_per_row = 2
                else:
                    # First 3 groups: 6 per row
                    agents_per_row = 6

                # Split agents into rows
                for i in range(0, len(sorted_agents), agents_per_row):
                    row_agents = sorted_agents[i : i + agents_per_row]
                    organized_rows.append((category_name, row_agents))

        # Calculate total rows and maximum columns
        total_rows = len(organized_rows)
        max_cols = 6  # Maximum possible columns

        # Create the figure
        fig, axes = plt.subplots(total_rows, max_cols, figsize=(3 * max_cols, 3 * total_rows))

        # Handle case where we have only one row
        if total_rows == 1:
            axes = [axes]

        # Plot each row
        for row_idx, (category_name, row_agents) in enumerate(organized_rows):
            n_agents_in_row = len(row_agents)

            # Plot agents in this row
            for col_idx, agent in enumerate(row_agents):
                ax = axes[row_idx][col_idx] if total_rows > 1 else axes[0][col_idx]
                data = agent_timeseries[agent]

                episodes = data["episodes"]
                bill_values = data["Bill Fredrickson"]
                bradley_values = data["Bradley Carter"]

                # Debug plotting data only in averaged mode
                if not hasattr(self, "separate_runs") or not self.separate_runs:
                    print(
                        f"PLOTTING {agent}: Episodes={len(episodes)}, Bill={len(bill_values)}, Bradley={len(bradley_values)}"
                    )
                    print(f"  Bill has None values: {None in bill_values}")
                    print(f"  Bradley has None values: {None in bradley_values}")
                    print(f"  Episodes: {episodes}")
                    print(f"  Bill scores: {bill_values}")
                    print(f"  Bradley scores: {bradley_values}")
                    if None in bill_values:
                        none_indices = [i for i, v in enumerate(bill_values) if v is None]
                        print(f"  Bill None at episodes: {[episodes[i] for i in none_indices]}")
                    if None in bradley_values:
                        none_indices = [i for i, v in enumerate(bradley_values) if v is None]
                        print(f"  Bradley None at episodes: {[episodes[i] for i in none_indices]}")

                # Plot lines (forward-fill ensures no None values)
                if bill_values and episodes:
                    ax.plot(
                        episodes,
                        bill_values,
                        "b-o",
                        linewidth=2,
                        markersize=4,
                        label="Bill Fredrickson",
                        alpha=0.8,
                    )

                if bradley_values and episodes:
                    ax.plot(
                        episodes,
                        bradley_values,
                        "r-s",
                        linewidth=2,
                        markersize=4,
                        label="Bradley Carter",
                        alpha=0.8,
                    )

                # Formatting
                ax.set_title(f"{agent.split()[0]}", fontsize=10, fontweight="bold")
                ax.set_ylim(0, 10)
                ax.grid(True, alpha=0.3)

                # Only show x-axis labels on bottom row
                if row_idx == total_rows - 1:
                    ax.set_xlabel("Episode", fontsize=9)
                else:
                    ax.set_xticklabels([])

                # Only show y-axis labels on leftmost column
                if col_idx == 0:
                    ax.set_ylabel("Favorability", fontsize=9)
                else:
                    ax.set_yticklabels([])

                # Add legend only to first subplot
                if row_idx == 0 and col_idx == 0:
                    ax.legend(fontsize=8, loc="upper right")

            # Hide unused subplots in this row
            for col_idx in range(n_agents_in_row, max_cols):
                ax = axes[row_idx][col_idx] if total_rows > 1 else axes[0][col_idx]
                ax.set_visible(False)

        # Add category labels on the left side, aligned with each row group
        current_row = 0
        for category in category_order:
            if not categorized[category]:
                continue

            category_name = category_labels[category]
            sorted_agents = sorted(categorized[category], key=lambda x: x.split()[0])

            if category == "candidate":
                agents_per_row = 2
            else:
                agents_per_row = 6

            # Calculate number of rows for this category
            category_rows = math.ceil(len(sorted_agents) / agents_per_row)

            # Position label at the middle of this category's rows
            middle_row = current_row + (category_rows - 1) / 2
            y_pos = 1 - (middle_row + 0.5) / total_rows

            fig.text(
                0.02,
                y_pos,
                category_name,
                fontsize=12,
                fontweight="bold",
                rotation=90,
                verticalalignment="center",
                horizontalalignment="center",
                bbox=dict(boxstyle="round,pad=0.3", facecolor="lightblue", alpha=0.7),
            )

            current_row += category_rows

        # Add title
        fig.suptitle(
            "Agent Favorability Trends Over Episodes\n(Organized by Party Affiliation)",
            fontsize=13,
            fontweight="bold",
            y=0.98,
        )

        plt.tight_layout()
        plt.subplots_adjust(left=0.1, top=0.92)

        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches="tight")
            print(f"Saved trellis favorability plot to {save_path}")

        plt.show()

        # Print summary
        print("\nTrellis Chart Summary:")
        print("-" * 40)
        for category in category_order:
            if categorized[category]:
                count = len(categorized[category])
                print(f"{category_labels[category]}: {count} agents")

    def _plot_separate_runs(self, save_path_base=None):
        """Create a single huge figure with each agent's runs organized in rows"""
        if not self.individual_timeseries:
            print("No individual run data available")
            return

        # Filter out empty runs and get consistent agent list across all runs
        valid_runs = []
        all_agents = set()

        for run_idx, run_timeseries in enumerate(self.individual_timeseries):
            if run_timeseries:
                valid_runs.append((run_idx + 1, run_timeseries))
                all_agents.update(run_timeseries.keys())

        if not valid_runs:
            print("No valid runs with data found")
            return

        print(f"Creating combined plot for {len(valid_runs)} runs with {len(all_agents)} agents")

        # Get consistent categorization and sort agents
        categorized = self.categorize_agents(list(all_agents))

        # Define the order for plotting
        category_order = ["liberal", "conservative", "independent", "candidate"]
        category_labels = {
            "liberal": "Progressive",
            "conservative": "Conservative",
            "independent": "Independent",
            "candidate": "Candidates",
        }

        # Create a flat list of agents organized by category
        ordered_agents = []
        category_starts = {}  # Track where each category starts for labeling

        for category in category_order:
            if categorized[category]:
                category_starts[category] = len(ordered_agents)
                sorted_agents = sorted(categorized[category], key=lambda x: x.split()[0])
                ordered_agents.extend(sorted_agents)

        # Layout: Each row is one agent, each column is one run
        total_rows = len(ordered_agents)
        total_cols = len(valid_runs)

        # Create the mega figure with larger subplot sizes
        fig, axes = plt.subplots(total_rows, total_cols, figsize=(6 * total_cols, 4 * total_rows))

        # Handle edge cases for subplot array dimensions
        if total_rows == 1 and total_cols == 1:
            axes = [[axes]]
        elif total_rows == 1:
            axes = [axes]
        elif total_cols == 1:
            axes = [[ax] for ax in axes]

        # Plot each agent across all runs
        for agent_idx, agent in enumerate(ordered_agents):
            for run_idx, (run_number, run_timeseries) in enumerate(valid_runs):
                ax = axes[agent_idx][run_idx]

                # Check if agent exists in this run's data
                if agent in run_timeseries:
                    data = run_timeseries[agent]
                    episodes = data["episodes"]
                    bill_values = data["Bill Fredrickson"]
                    bradley_values = data["Bradley Carter"]

                    # Plot lines with smaller markers and thinner lines for cleaner look
                    if bill_values and episodes:
                        ax.plot(
                            episodes,
                            bill_values,
                            "b-o",
                            linewidth=1.5,
                            markersize=3,
                            label="Bill Fredrickson",
                            alpha=0.8,
                        )

                    if bradley_values and episodes:
                        ax.plot(
                            episodes,
                            bradley_values,
                            "r-s",
                            linewidth=1.5,
                            markersize=3,
                            label="Bradley Carter",
                            alpha=0.8,
                        )
                else:
                    # Agent not in this run - show empty plot with message
                    ax.text(
                        0.5,
                        0.5,
                        "No Data",
                        transform=ax.transAxes,
                        ha="center",
                        va="center",
                        fontsize=10,
                        alpha=0.5,
                    )

                # Formatting
                ax.set_ylim(0, 10)
                ax.grid(True, alpha=0.3)

                # Titles and labels with larger fonts
                if agent_idx == 0:  # Top row gets run numbers
                    ax.set_title(f"Run {run_number}", fontsize=9, fontweight="bold")

                if run_idx == 0:  # Leftmost column gets agent names
                    ax.set_ylabel(f"{agent.split()[0]}", fontsize=6, fontweight="bold")
                else:
                    ax.set_yticklabels([])

                if agent_idx == total_rows - 1:  # Bottom row gets x-axis labels
                    ax.set_xlabel("Episode", fontsize=12)
                else:
                    ax.set_xticklabels([])

                # Increase tick label sizes
                ax.tick_params(axis="both", which="major", labelsize=10)

                # Legend only on first subplot with larger font
                if agent_idx == 0 and run_idx == 0:
                    ax.legend(fontsize=12, loc="upper right")

        # Add category labels on the right side
        for category in category_order:
            if category not in categorized or not categorized[category]:
                continue

            category_name = category_labels[category]
            start_row = category_starts[category]
            category_size = len(categorized[category])

            # Position label at the middle of this category's rows
            middle_row = start_row + (category_size - 1) / 2
            y_pos = 1 - (middle_row + 0.5) / total_rows

            fig.text(
                0.98,
                y_pos,
                category_name,
                fontsize=16,
                fontweight="bold",
                rotation=270,
                verticalalignment="center",
                horizontalalignment="center",
                bbox=dict(boxstyle="round,pad=0.5", facecolor="lightblue", alpha=0.7),
            )

        # Add main title with larger font
        fig.suptitle(
            "Agent Favorability Trends Across All Runs", fontsize=20, fontweight="bold", y=0.98
        )

        plt.tight_layout()
        plt.subplots_adjust(left=0.08, top=0.95, right=0.92, bottom=0.05)

        # Save the combined plot
        if save_path_base:
            if isinstance(save_path_base, str):
                save_path_base = Path(save_path_base)

            # Modify filename for combined plot
            stem = save_path_base.stem
            suffix = save_path_base.suffix
            save_path = save_path_base.parent / f"{stem}_by_agent_combined{suffix}"

            plt.savefig(save_path, dpi=300, bbox_inches="tight")
            print(f"Saved agent-organized trellis favorability plot to {save_path}")

        plt.show()

        # Print summary
        print("\\nAgent-Organized Plot Summary:")
        print("-" * 40)
        print(f"Total runs: {len(valid_runs)}")
        print(f"Runs included: {[run_num for run_num, _ in valid_runs]}")
        print(f"Total agents: {len(ordered_agents)}")
        for category in category_order:
            if categorized[category]:
                count = len(categorized[category])
                print(f"{category_labels[category]}: {count} agents")

    def _create_single_trellis_plot(self, agent_timeseries, run_number, save_path_base=None):
        """Create a single trellis chart for one run"""
        # Categorize agents
        agents_list = list(agent_timeseries.keys())
        categorized = self.categorize_agents(agents_list)

        # Define the order for plotting: Progressive, Conservative, Independent, Candidates
        category_order = ["liberal", "conservative", "independent", "candidate"]
        category_labels = {
            "liberal": "Progressive",
            "conservative": "Conservative",
            "independent": "Independent",
            "candidate": "Candidates",
        }

        # Organize agents by category with proper row structure
        # First 3 groups get 6 agents per row, Candidates get 2 per row
        organized_rows = []
        for category in category_order:
            if categorized[category]:
                sorted_agents = sorted(categorized[category], key=lambda x: x.split()[0])
                category_name = category_labels[category]

                if category == "candidate":
                    # Candidates: 2 per row
                    agents_per_row = 2
                else:
                    # First 3 groups: 6 per row
                    agents_per_row = 6

                # Split agents into rows
                for i in range(0, len(sorted_agents), agents_per_row):
                    row_agents = sorted_agents[i : i + agents_per_row]
                    organized_rows.append((category_name, row_agents))

        # Calculate total rows and maximum columns
        total_rows = len(organized_rows)
        max_cols = 6  # Maximum possible columns

        # Create the figure
        fig, axes = plt.subplots(total_rows, max_cols, figsize=(3 * max_cols, 3 * total_rows))

        # Handle case where we have only one row
        if total_rows == 1:
            axes = [axes]

        # Plot each row
        for row_idx, (category_name, row_agents) in enumerate(organized_rows):
            n_agents_in_row = len(row_agents)

            # Plot agents in this row
            for col_idx, agent in enumerate(row_agents):
                ax = axes[row_idx][col_idx] if total_rows > 1 else axes[0][col_idx]
                data = agent_timeseries[agent]

                episodes = data["episodes"]
                bill_values = data["Bill Fredrickson"]
                bradley_values = data["Bradley Carter"]

                # Plot lines (forward-fill ensures no None values)
                if bill_values and episodes:
                    ax.plot(
                        episodes,
                        bill_values,
                        "b-o",
                        linewidth=2,
                        markersize=4,
                        label="Bill Fredrickson",
                        alpha=0.8,
                    )

                if bradley_values and episodes:
                    ax.plot(
                        episodes,
                        bradley_values,
                        "r-s",
                        linewidth=2,
                        markersize=4,
                        label="Bradley Carter",
                        alpha=0.8,
                    )

                # Formatting
                ax.set_title(f"{agent.split()[0]}", fontsize=10, fontweight="bold")
                ax.set_ylim(0, 10)
                ax.grid(True, alpha=0.3)

                # Only show x-axis labels on bottom row
                if row_idx == total_rows - 1:
                    ax.set_xlabel("Episode", fontsize=9)
                else:
                    ax.set_xticklabels([])

                # Only show y-axis labels on leftmost column
                if col_idx == 0:
                    ax.set_ylabel("", fontsize=9)
                else:
                    ax.set_yticklabels([])

                # Add legend only to first subplot
                if row_idx == 0 and col_idx == 0:
                    ax.legend(fontsize=8, loc="upper right")

            # Hide unused subplots in this row
            for col_idx in range(n_agents_in_row, max_cols):
                ax = axes[row_idx][col_idx] if total_rows > 1 else axes[0][col_idx]
                ax.set_visible(False)

        # Add category labels on the left side, aligned with each row group
        current_row = 0
        for category in category_order:
            if not categorized[category]:
                continue

            category_name = category_labels[category]
            sorted_agents = sorted(categorized[category], key=lambda x: x.split()[0])

            if category == "candidate":
                agents_per_row = 2
            else:
                agents_per_row = 6

            # Calculate number of rows for this category
            category_rows = math.ceil(len(sorted_agents) / agents_per_row)

            # Position label at the middle of this category's rows
            middle_row = current_row + (category_rows - 1) / 2
            y_pos = 1 - (middle_row + 0.5) / total_rows

            fig.text(
                0.02,
                y_pos,
                category_name,
                fontsize=12,
                fontweight="bold",
                rotation=90,
                verticalalignment="center",
                horizontalalignment="center",
                bbox=dict(boxstyle="round,pad=0.3", facecolor="lightblue", alpha=0.7),
            )

            current_row += category_rows

        # Add title with run number
        fig.suptitle(
            f"Agent Favorability Trends Over Episodes - Run {run_number}\n(Organized by Party Affiliation)",
            fontsize=16,
            fontweight="bold",
            y=0.98,
        )

        plt.tight_layout()
        plt.subplots_adjust(left=0.1, top=0.92)

        # Save with run number in filename
        if save_path_base:
            if isinstance(save_path_base, str):
                save_path_base = Path(save_path_base)

            # Insert run number into filename
            stem = save_path_base.stem
            suffix = save_path_base.suffix
            save_path = save_path_base.parent / f"{stem}_run{run_number}{suffix}"

            plt.savefig(save_path, dpi=300, bbox_inches="tight")
            print(f"Saved trellis favorability plot for Run {run_number} to {save_path}")

        plt.show()

        # Print summary for this run
        print(f"\nRun {run_number} Summary:")
        print("-" * 40)
        for category in category_order:
            if categorized[category]:
                count = len(categorized[category])
                print(f"{category_labels[category]}: {count} agents")

    def _plot_in_one(self, save_path=None):
        """Create trellis chart with all individual run lines overlaid on each agent subplot"""
        if not self.individual_timeseries:
            print("No individual run data available")
            return

        # Filter out empty runs and get consistent agent list across all runs
        valid_runs = []
        all_agents = set()

        for run_idx, run_timeseries in enumerate(self.individual_timeseries):
            if run_timeseries:
                valid_runs.append((run_idx + 1, run_timeseries))
                all_agents.update(run_timeseries.keys())

        if not valid_runs:
            print("No valid runs with data found")
            return

        print(f"Creating overlaid plot for {len(valid_runs)} runs with {len(all_agents)} agents")

        # Categorize agents using the same logic as the original plot
        agents_list = list(all_agents)
        categorized = self.categorize_agents(agents_list)

        # Define the order for plotting: Progressive, Conservative, Independent, Candidates
        category_order = ["liberal", "conservative", "independent", "candidate"]
        category_labels = {
            "liberal": "Progressive",
            "conservative": "Conservative",
            "independent": "Independent",
            "candidate": "Candidates",
        }

        # Organize agents by category with proper row structure (same as original)
        # First 3 groups get 6 agents per row, Candidates get 2 per row
        organized_rows = []
        for category in category_order:
            if categorized[category]:
                sorted_agents = sorted(categorized[category], key=lambda x: x.split()[0])
                category_name = category_labels[category]

                if category == "candidate":
                    # Candidates: 2 per row
                    agents_per_row = 2
                else:
                    # First 3 groups: 6 per row
                    agents_per_row = 6

                # Split agents into rows
                for i in range(0, len(sorted_agents), agents_per_row):
                    row_agents = sorted_agents[i : i + agents_per_row]
                    organized_rows.append((category_name, row_agents))

        # Calculate total rows and maximum columns (same as original)
        total_rows = len(organized_rows)
        max_cols = 6  # Maximum possible columns

        # Create the figure (same size as original)
        fig, axes = plt.subplots(total_rows, max_cols, figsize=(3 * max_cols, 3 * total_rows))

        # Handle case where we have only one row
        if total_rows == 1:
            axes = [axes]

        # Plot each row
        for row_idx, (category_name, row_agents) in enumerate(organized_rows):
            n_agents_in_row = len(row_agents)

            # Plot agents in this row
            for col_idx, agent in enumerate(row_agents):
                ax = axes[row_idx][col_idx] if total_rows > 1 else axes[0][col_idx]

                # Plot all runs for this agent
                for run_idx, (run_number, run_timeseries) in enumerate(valid_runs):
                    if agent in run_timeseries:
                        data = run_timeseries[agent]
                        episodes = data["episodes"]
                        bill_values = data["Bill Fredrickson"]
                        bradley_values = data["Bradley Carter"]

                        # Use consistent alpha for all runs
                        alpha = 0.6 if len(valid_runs) > 1 else 0.8

                        # Plot lines for Bill (all runs use same style - no differentiation)
                        if bill_values and episodes:
                            ax.plot(
                                episodes,
                                bill_values,
                                "b-o",
                                color="blue",
                                linewidth=2,
                                markersize=4,
                                alpha=alpha,
                                label="Bill Fredrickson"
                                if col_idx == 0 and row_idx == 0 and run_idx == 0
                                else "",
                            )

                        # Plot lines for Bradley (all runs use same style - no differentiation)
                        if bradley_values and episodes:
                            ax.plot(
                                episodes,
                                bradley_values,
                                "r-s",
                                color="red",
                                linewidth=2,
                                markersize=4,
                                alpha=alpha,
                                label="Bradley Carter"
                                if col_idx == 0 and row_idx == 0 and run_idx == 0
                                else "",
                            )

                # Formatting (same as original)
                ax.set_title(f"{agent.split()[0]}", fontsize=10, fontweight="bold")
                ax.set_ylim(0, 10)
                ax.grid(True, alpha=0.3)

                # Only show x-axis labels on bottom row
                if row_idx == total_rows - 1:
                    ax.set_xlabel("Episode", fontsize=9)
                else:
                    ax.set_xticklabels([])

                # Only show y-axis labels on leftmost column
                if col_idx == 0:
                    ax.set_ylabel("Favorability", fontsize=9)
                else:
                    ax.set_yticklabels([])

                # Add legend only to first subplot (simple candidate legend)
                if row_idx == 0 and col_idx == 0:
                    # Simple legend showing just the candidates (no run differentiation)
                    ax.legend(fontsize=8, loc="upper right")

            # Hide unused subplots in this row
            for col_idx in range(n_agents_in_row, max_cols):
                ax = axes[row_idx][col_idx] if total_rows > 1 else axes[0][col_idx]
                ax.set_visible(False)

        # Add category labels on the left side (same as original)
        current_row = 0
        for category in category_order:
            if not categorized[category]:
                continue

            category_name = category_labels[category]
            sorted_agents = sorted(categorized[category], key=lambda x: x.split()[0])

            if category == "candidate":
                agents_per_row = 2
            else:
                agents_per_row = 6

            # Calculate number of rows for this category
            category_rows = math.ceil(len(sorted_agents) / agents_per_row)

            # Position label at the middle of this category's rows
            middle_row = current_row + (category_rows - 1) / 2
            y_pos = 1 - (middle_row + 0.5) / total_rows

            fig.text(
                0.02,
                y_pos,
                category_name,
                fontsize=12,
                fontweight="bold",
                rotation=90,
                verticalalignment="center",
                horizontalalignment="center",
                bbox=dict(boxstyle="round,pad=0.3", facecolor="lightblue", alpha=0.7),
            )

            current_row += category_rows

        # Add title
        fig.suptitle(
            "Agent Favorability Trends Over Episodes - All Runs Overlaid\n(Organized by Party Affiliation)",
            fontsize=13,
            fontweight="bold",
            y=0.98,
        )

        plt.tight_layout()
        plt.subplots_adjust(left=0.1, top=0.92)

        if save_path:
            if isinstance(save_path, str):
                save_path = Path(save_path)

            # Modify filename for overlaid plot
            stem = save_path.stem
            suffix = save_path.suffix
            save_path = save_path.parent / f"{stem}_all_runs_overlaid{suffix}"

            plt.savefig(save_path, dpi=300, bbox_inches="tight")
            print(f"Saved overlaid trellis favorability plot to {save_path}")

        plt.show()

        # Print summary
        print("\nOverlaid Plot Summary:")
        print("-" * 40)
        print(f"Total runs overlaid: {len(valid_runs)}")
        print(f"Runs included: {[run_num for run_num, _ in valid_runs]}")
        for category in category_order:
            if categorized[category]:
                count = len(categorized[category])
                print(f"{category_labels[category]}: {count} agents")


def main():
    """Main function to run the analysis"""
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="Generate trellis charts for election favorability data"
    )
    parser.add_argument(
        "--separate",
        action="store_true",
        help="Generate separate charts for each run instead of averaging",
    )
    parser.add_argument(
        "--in_one",
        action="store_true",
        help="Show all runs overlaid on the same plots (same layout as default but no averaging)",
    )
    parser.add_argument(
        "--inputs",
        "-i",
        nargs="+",
        required=True,
        help="One or more probe_events.jsonl file paths to process (required).",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        default="trellis_plots",
        help="Directory to save generated plots",
    )
    args = parser.parse_args()

    # Validate mutually exclusive flags
    if args.separate and args.in_one:
        print("Error: --separate and --in_one flags are mutually exclusive")
        return

    # Normalize provided input paths. No default list - inputs are required.
    probe_files = [p if os.path.isabs(p) else os.path.join(os.getcwd(), p) for p in args.inputs]

    # Create output directory
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Initialize analyzer with multiple files and flags
    analyzer = TrellisAnalyzer(probe_files, separate_runs=args.separate, in_one=args.in_one)
    analyzer.load_data()

    # Generate the trellis plot(s)
    print("\n" + "=" * 60)
    if args.separate:
        print("GENERATING SEPARATE TRELLIS FAVORABILITY CHARTS FOR EACH RUN")
    elif args.in_one:
        print("GENERATING OVERLAID TRELLIS FAVORABILITY CHART (ALL RUNS ON SAME PLOTS)")
    else:
        print("GENERATING AVERAGED TRELLIS FAVORABILITY CHART")
    print("=" * 60)

    analyzer.plot_trellis_favorability(output_dir / "trellis_favorability.png")

    if args.separate:
        print(f"\nAnalysis complete! Separate plots saved to {output_dir}")
    elif args.in_one:
        print(f"\nAnalysis complete! Overlaid plot saved to {output_dir}")
    else:
        print(f"\nAnalysis complete! Averaged plot saved to {output_dir}")


if __name__ == "__main__":
    main()
