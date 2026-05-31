"""Graph layout and visualization utilities for the dashboard."""

import networkx as nx
import plotly.graph_objs as go
from plotly.subplots import make_subplots

from silisocs.evaluations.analysis.dashboard.config import (
    INTERACTION_TYPES,
    LINE_COLORS,
    PAST_TENSE_MAP,
)


def compute_positions(graph):
    """Compute node positions using Kamada-Kawai layout."""
    pos = nx.kamada_kawai_layout(graph, scale=750)
    scaled_pos = {}
    for node, (x, y) in pos.items():
        scaled_pos[node] = {"x": x, "y": y}
    return scaled_pos


def probe_plot_preprocessing(probe_data):
    """Preprocess probe data for plotting response distribution over time."""
    x_data = sorted(probe_data.keys())
    response_labels = sorted(
        {
            str(response)
            for episode_responses in probe_data.values()
            for response in episode_responses.values()
            if response is not None
        }
    )

    graphs_data = []
    for idx, label in enumerate(response_labels):
        values = []
        for ep in x_data:
            ep_responses = probe_data[ep]
            total = len(ep_responses)
            count = sum(1 for response in ep_responses.values() if str(response) == label)
            values.append((count / total) * 100 if total > 0 else 0)
        graphs_data.append(
            {
                "label": label,
                "data": {"x": x_data, "y": values},
                "color": LINE_COLORS[idx % len(LINE_COLORS)],
            }
        )

    missing_values = []
    for ep in x_data:
        ep_responses = probe_data[ep]
        total = len(ep_responses)
        missing = sum(
            1 for response in ep_responses.values() if response is None or str(response) == ""
        )
        missing_values.append((missing / total) * 100 if total > 0 else 0)
    if any(missing_values):
        graphs_data.append(
            {
                "label": "No response",
                "data": {"x": x_data, "y": missing_values},
                "color": "#808080",
            }
        )

    title_label = "Probe Responses Over Time"
    yaxis_label = "Response Percentage"
    return graphs_data, title_label, yaxis_label


def create_probe_data_figure(probe_data):
    """Create Plotly figure for probe data line graph."""
    probe_graphs_data, title_label, yaxis_label = probe_plot_preprocessing(probe_data)

    fig = go.Figure()
    for graph_data in probe_graphs_data:
        fig.add_trace(
            go.Scatter(
                x=graph_data["data"]["x"],
                y=graph_data["data"]["y"],
                mode="lines+markers",
                name=graph_data["label"],
                line=dict(color=graph_data["color"]),
                cliponaxis=False,
            )
        )

    max_episode = max(int(key) for key in probe_data.keys()) if probe_data else 0
    fig.update_layout(
        xaxis={
            "title": {"text": "Episode"},
            "range": [1, max_episode + 1],
            "dtick": 1,
            "showgrid": False,
        },
        font=dict(family="Segoe UI"),
        yaxis={
            "title": {"text": yaxis_label},
            "range": [0, 100],
            "dtick": 20,
            "showgrid": True,
            "gridcolor": "#f0f0f0",
        },
        width=100,
        margin=dict(l=30, r=50, t=30, b=0),
        legend=dict(orientation="h", x=0.5, y=-0.4, xanchor="center", bgcolor="rgba(0,0,0,0)"),
        plot_bgcolor="white",
        paper_bgcolor="white",
        autosize=False,
    )

    return fig


def create_interactions_figure(interactions_by_episode, active_users_by_episode, total_users):
    """Create Plotly figure for interactions over time."""
    int_types = [PAST_TENSE_MAP[ty] for ty in INTERACTION_TYPES]
    interactions_over_time = {interaction: [] for interaction in int_types}
    active_user_fractions = []
    int_episodes = sorted(interactions_by_episode.keys())

    for ep in int_episodes:
        num_active_users = len(active_users_by_episode.get(ep, set()))
        counts = dict.fromkeys(int_types, 0)

        # Count interactions
        for interaction in interactions_by_episode.get(ep, []):
            action = interaction["action"]
            if action in counts:
                counts[action] += 1

        # Append normalized counts
        for interaction in int_types:
            if interaction in counts:
                interactions_over_time[interaction].append(
                    counts[interaction] / num_active_users if num_active_users > 0 else 0
                )

        # Calculate active user fraction
        active_user_fraction = num_active_users / total_users if total_users > 0 else 0
        active_user_fractions.append(active_user_fraction)

    # Create subplot with secondary y-axis
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    # Add interaction traces
    colors = {
        "liked": "#2ca02c",
        "reposted": "#ff7f0e",
        "replied": "#9467bd",
        "posted": "#1f77b4",
    }
    markers = {
        "liked": "circle",
        "reposted": "square",
        "replied": "diamond",
        "posted": "triangle-up",
    }

    for interaction_type in int_types:
        fig.add_trace(
            go.Scatter(
                x=int_episodes,
                y=interactions_over_time[interaction_type],
                mode="lines+markers",
                name=interaction_type.replace("_", " ").title(),
                line=dict(color=colors[interaction_type]),
                marker=dict(symbol=markers[interaction_type], size=6),
                cliponaxis=False,
            ),
            secondary_y=False,
        )

    # Add active users fraction trace
    fig.add_trace(
        go.Scatter(
            x=int_episodes,
            y=active_user_fractions,
            mode="lines",
            name="Active User Fraction",
            line=dict(color="gray"),
            cliponaxis=False,
        ),
        secondary_y=True,
    )

    # Update axes
    fig.update_yaxes(
        title_text="Interactions/ Num. Agents",
        range=[0, 1.5],
        secondary_y=False,
        showgrid=True,
        gridcolor="#f0f0f0",
    )
    fig.update_yaxes(
        title_text="Active User Fraction",
        range=[0, 1],
        secondary_y=True,
        showgrid=False,
    )

    max_ep = max(int_episodes) if int_episodes else 0
    fig.update_layout(
        xaxis={
            "title": {"text": "Episode"},
            "range": [-1, max_ep],
            "dtick": 1,
        },
        font=dict(family="Segoe UI"),
        width=50,
        margin=dict(l=30, r=80, t=80, b=0),
        legend=dict(orientation="h", x=0.5, y=-1, xanchor="center"),
        plot_bgcolor="white",
        paper_bgcolor="white",
        autosize=False,
    )

    return fig
