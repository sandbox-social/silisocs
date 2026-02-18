import argparse
import colorsys
from pathlib import Path

import dash
import dash_cytoscape as cyto
import networkx as nx
import pandas as pd
import plotly.express as px
import plotly.graph_objs as go
from dash import Input, Output, State, dcc, html

# Load extra layouts for Cytoscape
cyto.load_extra_layouts()

# Theme Configuration
THEME = {
    "background": "#121212",
    "text": "#e0e0e0",
    "accent_primary": "#00ff9d",  # Neon Green/Cyan
    "accent_secondary": "#d600ff",  # Neon Purple
    "node_default": "#00ffff",  # Cyan
    "edge_default": "#555555",
    "plot_bg": "#1e1e1e",
    "paper_bg": "#121212",
    "panel_bg": "rgba(30, 30, 30, 0.95)",
    "glass_style": {
        "backgroundColor": "rgba(30, 30, 30, 0.8)",
        "backdropFilter": "blur(10px)",
        "borderRadius": "12px",
        "border": "1px solid rgba(255, 255, 255, 0.1)",
        "boxShadow": "0 8px 32px 0 rgba(0, 0, 0, 0.37)",
    },
}

# --- Utility Functions ---


def generate_color_map(values: list[str]) -> dict[str, str]:
    """
    Generate a distinct color map for a list of values.
    Uses Golden Ratio to spread hues.
    """
    unique_vals = sorted(list(set(values)))
    color_map = {}

    # Handle specific common values with fixed colors if desired,
    # but for true genericness, we'll generate regular intervals.
    # We can try to match known candidates if they appear.

    n_colors = len(unique_vals)
    if n_colors == 0:
        return {}

    # Use Plotly's qualitative scales if small number
    if n_colors <= 10:
        colors = px.colors.qualitative.Plotly
        for i, val in enumerate(unique_vals):
            color_map[val] = colors[i % len(colors)]
    elif n_colors <= 24:
        colors = px.colors.qualitative.Dark24
        for i, val in enumerate(unique_vals):
            color_map[val] = colors[i % len(colors)]
    else:
        # Programmatic generation for many values
        for i, val in enumerate(unique_vals):
            hue = i / n_colors
            rgb = colorsys.hsv_to_rgb(hue, 0.8, 0.9)
            hex_color = f"#{int(rgb[0] * 255):02x}{int(rgb[1] * 255):02x}{int(rgb[2] * 255):02x}"
            color_map[val] = hex_color

    return color_map


# --- Data Loading ---


def load_data_from_folder(
    folder_path: Path,
) -> tuple[nx.DiGraph, dict, dict, dict, list, list]:
    """
    Load and process data from the specified folder.
    """
    interactions_file = folder_path / "action_events.jsonl"
    probes_file = folder_path / "probe_events.jsonl"

    if not interactions_file.exists() or not probes_file.exists():
        raise FileNotFoundError(f"Required files not found in {folder_path}")

    # Load data
    action_df = pd.read_json(interactions_file, lines=True)
    probe_df = pd.read_json(probes_file, lines=True)

    # Combine dataframes for general processing if needed
    full_df = pd.concat([action_df, probe_df], ignore_index=True)

    # --- Process Generic Probes ---
    # Structure: probe_data[probe_label][episode][agent] = value
    probe_data = {}

    # Get all unique probe labels
    # Normalize episode column name
    if "episode" not in probe_df.columns and "episode_idx" in probe_df.columns:
        probe_df.rename(columns={"episode_idx": "episode"}, inplace=True)

    unique_labels = probe_df["label"].unique()

    for label in unique_labels:
        # Filter for this label
        label_df = probe_df[probe_df["label"] == label]

        # Group by episode and source_user
        # We want the 'query_return' or 'response' depending on structure
        # Validating structure from previous `head` command:
        # data object has 'query_return'. If null, use 'response'?
        # The 'response' column in dataframe might be the raw text.
        # Let's try to extract 'query_return' from 'data' column if available, else 'response'

        episode_data = {}
        for episode, group in label_df.groupby("episode"):
            agent_values = {}
            for _, row in group.iterrows():
                val = None
                if isinstance(row["data"], dict):
                    val = row["data"].get("query_return")

                # If query_return is None or empty, maybe parse raw?
                # For now, stick to query_return as it seems structured (e.g. "Bill", "7", "Yes")
                if val is not None:
                    agent_values[row["source_user"]] = str(val)

            if agent_values:
                episode_data[episode] = agent_values

        if episode_data:
            probe_data[label] = episode_data

    # --- Process Network & Actions ---

    # Try using legacy processor if available for edge list
    edge_df = pd.DataFrame(columns=["source_user", "target_user"])
    int_df = action_df.copy()  # Placeholder

    # ... Or build manually since we want to be standalone and robust
    # Construct Graph from 'follow' events
    follow_events = action_df[action_df["label"] == "follow"]
    unfollow_events = action_df[action_df["label"] == "unfollow"]

    # We will build the FINAL state graph for simplicity
    # (or we could make it dynamic, but Cytoscape handles static structure better for layout stability)
    follow_graph = nx.DiGraph()

    # Add all agents found in source_user
    all_agents = set(action_df["source_user"].unique())
    follow_graph.add_nodes_from(all_agents)

    # Replay follows/unfollows in order
    # Assuming chronological order in file
    for _, row in action_df.iterrows():
        if row["label"] == "follow":
            target = row.get("target_user")
            # Sometimes target is in data?
            if not target and isinstance(row["data"], dict):
                target = row["data"].get("target_user")

            if row["source_user"] and target:
                follow_graph.add_edge(row["source_user"], target)

        elif row["label"] == "unfollow":
            target = row.get("target_user")
            if not target and isinstance(row["data"], dict):
                target = row["data"].get("target_user")

            if row["source_user"] and target and follow_graph.has_edge(row["source_user"], target):
                follow_graph.remove_edge(row["source_user"], target)

    # --- Process Interactions ---

    # Build toot dict
    toot_dict = {}

    # Helper to find toot content
    def extract_toot_info(row):
        data = row.get("data", {})
        if not isinstance(data, dict):
            return None

        tid = str(data.get("toot_id", f"gen_{row.name}"))
        content = data.get("post_text", "")
        return tid, content

    for _, row in action_df.iterrows():
        if row["label"] in ["post", "reply"]:
            tid, content = extract_toot_info(row)
            if tid:
                toot_dict[tid] = {
                    "user": row["source_user"],
                    "content": content,
                    "action": row["label"],
                }

    # Map interactions to episodes
    interactions_by_episode = {}

    for episode, group in action_df.groupby("episode"):
        group_interactions = []
        for _, row in group.iterrows():
            label = row["label"]
            data = row.get("data", {})
            if not isinstance(data, dict):
                data = {}

            interaction = {
                "action": label,
                "source": row["source_user"],
                "content": "",
                "target": None,
            }

            # Enrich interaction details
            if label == "get_own_timeline":
                continue

            if label == "post":
                interaction["content"] = data.get("post_text", "")

            elif label == "reply":
                interaction["content"] = data.get("post_text", "")
                # Find target from reply_to
                reply_to = data.get("reply_to", {})
                if reply_to:
                    parent_tid = str(reply_to.get("toot_id"))
                    if parent_tid in toot_dict:
                        interaction["target"] = toot_dict[parent_tid]["user"]
                        interaction["parent_content"] = toot_dict[parent_tid]["content"]

            elif label in ["like_toot", "boost_toot"]:
                target_tid = str(data.get("target_toot_id"))
                if target_tid in toot_dict:
                    interaction["target"] = toot_dict[target_tid]["user"]
                    interaction["content"] = f"Target: {toot_dict[target_tid]['content'][:50]}..."

            group_interactions.append(interaction)

        interactions_by_episode[episode] = group_interactions

    # Debug probe data
    print(f"Loaded probe labels: {list(probe_data.keys())}")
    for label, ep_data in probe_data.items():
        print(f"Probe {label}: {len(ep_data)} episodes with data")
        first_ep = min(ep_data.keys())
        print(f"  Ep {first_ep} sample: {list(ep_data[first_ep].items())[:3]}")
    # Filter for 'action' types where suggested_action exists
    raw_data = action_df.to_dict(orient="records")

    return (
        follow_graph,
        interactions_by_episode,
        probe_data,
        toot_dict,
        raw_data,
        unique_labels.tolist(),
    )


def compute_positions(graph):
    # Use Kamada-Kawai for organic layout
    if len(graph.nodes) == 0:
        return {}
    pos = nx.kamada_kawai_layout(graph, scale=500)
    return pos


# --- App Definition ---


def create_dash_app(data_tuple):
    (follow_graph, interactions_by_episode, probe_data, toot_dict, raw_data, probe_labels) = (
        data_tuple
    )

    app = dash.Dash(__name__, title="Sim Dashboard")

    initial_pos = compute_positions(follow_graph)
    min_ep = min(interactions_by_episode.keys()) if interactions_by_episode else 0
    max_ep = max(interactions_by_episode.keys()) if interactions_by_episode else 0

    # --- Layout ---
    app.index_string = f"""
    <!DOCTYPE html>
    <html>
        <head>
            {{%metas%}}
            <title>{{%title%}}</title>
            {{%favicon%}}
            {{%css%}}
            <style>
                body {{
                    margin: 0;
                    background-color: {THEME["background"]};
                    color: {THEME["text"]};
                    font-family: 'Segoe UI', sans-serif;
                }}
                .glass-panel {{
                    background-color: {THEME["glass_style"]["backgroundColor"]};
                    backdrop-filter: {THEME["glass_style"]["backdropFilter"]};
                    border-radius: {THEME["glass_style"]["borderRadius"]};
                    border: {THEME["glass_style"]["border"]};
                    box-shadow: {THEME["glass_style"]["boxShadow"]};
                    padding: 20px;
                    margin-bottom: 20px;
                }}
                .control-label {{
                    font-weight: 600;
                    color: {THEME["accent_primary"]};
                    margin-bottom: 8px;
                    display: block;
                }}
                /* Scrollbar styling */
                ::-webkit-scrollbar {{
                    width: 8px;
                }}
                ::-webkit-scrollbar-track {{
                    background: {THEME["paper_bg"]};
                }}
                ::-webkit-scrollbar-thumb {{
                    background: {THEME["edge_default"]};
                    border-radius: 4px;
                }}
                ::-webkit-scrollbar-thumb:hover {{
                    background: {THEME["accent_primary"]};
                }}
            </style>
        </head>
        <body>
            {{%app_entry%}}
            <footer>
                {{%config%}}
                {{%scripts%}}
                {{%renderer%}}
            </footer>
        </body>
    </html>
    """

    app.layout = html.Div(
        [
            # --- Top Bar ---
            html.Div(
                className="glass-panel",
                style={
                    "margin": "20px",
                    "display": "flex",
                    "alignItems": "center",
                    "justifyContent": "space-between",
                },
                children=[
                    html.H2(
                        "Simulation Analysis", style={"margin": 0, "color": THEME["accent_primary"]}
                    ),
                    # Controls Container
                    html.Div(
                        style={
                            "display": "flex",
                            "gap": "30px",
                            "alignItems": "center",
                            "flex": 1,
                            "marginLeft": "40px",
                        },
                        children=[
                            # Probe Selector
                            html.Div(
                                style={"width": "200px"},
                                children=[
                                    html.Label("Color By Metric", className="control-label"),
                                    dcc.Dropdown(
                                        id="probe-selector",
                                        options=[
                                            {"label": l, "value": l} for l in sorted(probe_labels)
                                        ],
                                        value=probe_labels[0] if probe_labels else None,
                                        clearable=False,
                                        style={
                                            "color": "#000"
                                        },  # Dropdown text color needs to be dark for contrast
                                    ),
                                ],
                            ),
                            # Episode Slider
                            html.Div(
                                style={"flex": 1},
                                children=[
                                    html.Label(
                                        id="episode-label",
                                        children=f"Episode: {min_ep}",
                                        className="control-label",
                                    ),
                                    dcc.Slider(
                                        id="episode-slider",
                                        min=min_ep,
                                        max=max_ep,
                                        value=min_ep,
                                        marks={
                                            str(i): str(i) for i in range(min_ep, max_ep + 1, 5)
                                        },
                                        step=1,
                                        updatemode="drag",
                                    ),
                                ],
                            ),
                            # Play/Pause
                            html.Button(
                                "▶ Play",
                                id="play-button",
                                n_clicks=0,
                                style={
                                    "backgroundColor": THEME["accent_secondary"],
                                    "color": "white",
                                    "border": "none",
                                    "padding": "10px 25px",
                                    "borderRadius": "8px",
                                    "cursor": "pointer",
                                    "fontWeight": "bold",
                                },
                            ),
                        ],
                    ),
                ],
            ),
            # --- Main Grid ---
            html.Div(
                style={
                    "display": "flex",
                    "height": "calc(100vh - 140px)",
                    "padding": "0 20px 20px 20px",
                    "gap": "20px",
                },
                children=[
                    # Left: Graph
                    html.Div(
                        className="glass-panel",
                        style={
                            "flex": 2,
                            "display": "flex",
                            "flexDirection": "column",
                            "overflow": "hidden",
                        },
                        children=[
                            cyto.Cytoscape(
                                id="cytoscape-graph",
                                layout={"name": "preset"},
                                style={"width": "100%", "height": "100%"},
                                elements=[],
                                stylesheet=[
                                    {
                                        "selector": "node",
                                        "style": {
                                            "content": "data(label)",
                                            "color": "#fff",
                                            "background-color": THEME["node_default"],
                                            "width": 45,
                                            "height": 45,
                                            "font-size": "12px",
                                            "text-valign": "center",
                                            "text-halign": "center",
                                            "text-outline-width": 2,
                                            "text-outline-color": "#000",
                                        },
                                    },
                                    {
                                        "selector": "edge",
                                        "style": {
                                            "width": 2,
                                            "line-color": THEME["edge_default"],
                                            "target-arrow-shape": "triangle",
                                            "target-arrow-color": THEME["edge_default"],
                                            "curve-style": "bezier",
                                            "opacity": 0.4,
                                        },
                                    },
                                    {
                                        "selector": ".highlighted_edge",
                                        "style": {
                                            "line-color": THEME["accent_primary"],
                                            "target-arrow-color": THEME["accent_primary"],
                                            "width": 4,
                                            "opacity": 1.0,
                                            "z-index": 999,
                                        },
                                    },
                                ],
                            )
                        ],
                    ),
                    # Right: Details & Stats
                    html.Div(
                        style={
                            "flex": 1,
                            "display": "flex",
                            "flexDirection": "column",
                            "gap": "20px",
                        },
                        children=[
                            # Top Right: Distribution Chart
                            html.Div(
                                className="glass-panel",
                                style={"flex": 1, "minHeight": "200px"},
                                children=[
                                    dcc.Graph(
                                        id="metric-dist-graph",
                                        config={"displayModeBar": False},
                                        style={"height": "100%"},
                                    )
                                ],
                            ),
                            # Bottom Right: Interaction Feed
                            html.Div(
                                className="glass-panel",
                                style={
                                    "flex": 1.5,
                                    "display": "flex",
                                    "flexDirection": "column",
                                    "overflow": "hidden",
                                },
                                children=[
                                    html.H4(
                                        "Interaction Log",
                                        style={
                                            "marginTop": 0,
                                            "color": THEME["accent_primary"],
                                            "borderBottom": "1px solid #444",
                                            "paddingBottom": "10px",
                                        },
                                    ),
                                    html.Div(
                                        id="interaction-feed",
                                        style={
                                            "overflowY": "auto",
                                            "flex": 1,
                                            "paddingRight": "10px",
                                        },
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            # Internals
            dcc.Interval(id="animation-interval", interval=1500, disabled=True),
            dcc.Store(id="node-positions-store", data=initial_pos),  # Store computed layout
        ]
    )

    # --- Callbacks ---

    @app.callback(
        Output("animation-interval", "disabled"),
        Output("play-button", "children"),
        Input("play-button", "n_clicks"),
        State("animation-interval", "disabled"),
    )
    def toggle_play(n, disabled):
        if n:
            return not disabled, "❚❚ Pause" if disabled else "▶ Play"
        return True, "▶ Play"

    @app.callback(
        Output("episode-slider", "value"),
        Input("animation-interval", "n_intervals"),
        State("episode-slider", "value"),
        State("episode-slider", "max"),
    )
    def advance_time(n, val, max_val):
        if val < max_val:
            return val + 1
        return val  # Loop? or Stop. Stop is better.

    @app.callback(
        [
            Output("cytoscape-graph", "elements"),
            Output("cytoscape-graph", "stylesheet"),
            Output("metric-dist-graph", "figure"),
            Output("interaction-feed", "children"),
            Output("episode-label", "children"),
        ],
        [
            Input("episode-slider", "value"),
            Input("probe-selector", "value"),
            Input("cytoscape-graph", "tapNodeData"),
        ],
    )
    def update_all(episode, probe_metric, selected_node_data):
        # 1. Colors based on Probe
        node_colors = {}
        metric_values = []

        if probe_metric and probe_metric in probe_data:
            # Gather all values for consistency in coloring
            all_values_for_metric = []
            for ep_data in probe_data[probe_metric].values():
                all_values_for_metric.extend(ep_data.values())

            color_map = generate_color_map(all_values_for_metric)

            if probe_data[probe_metric]:
                # Forward-fill: get latest value for each agent up to current episode
                current_values = {}
                # Ensure episode is int
                target_ep = int(episode)

                # Filter keys that are <= target_ep
                # Keys in probe_data[probe_metric] are integers (from load_data_from_folder)
                sorted_eps = sorted(
                    [ep for ep in probe_data[probe_metric].keys() if ep <= target_ep]
                )

                for ep in sorted_eps:
                    ep_data = probe_data[probe_metric][ep]
                    current_values.update(ep_data)  # Updates with latest

                for agent, val in current_values.items():
                    if val in color_map:
                        node_colors[agent] = color_map[val]
                    metric_values.append(val)

                # DEBUG: Print sample colors
                print(f"--- Update Ep {episode} ({probe_metric}) ---")
                print(f"  Total agents with data: {len(current_values)}")
                sample_agents = list(current_values.keys())[:3]
                for ag in sample_agents:
                    print(f"  Agent {ag}: Val='{current_values[ag]}' Color={node_colors.get(ag)}")

        # 2. Elements Generation
        elements = []

        # Nodes
        for node in follow_graph.nodes():
            # format position
            pos_raw = initial_pos.get(node, [0, 0])
            position = {"x": float(pos_raw[0]), "y": float(pos_raw[1])}

            elements.append({"data": {"id": node, "label": node.split()[0]}, "position": position})

        # Edges (Base)
        for u, v in follow_graph.edges():
            elements.append({"data": {"source": u, "target": v}})

        # Highlight Actions (Edges)
        ep_interactions = interactions_by_episode.get(episode, [])
        for interaction in ep_interactions:
            src = interaction["source"]
            tgt = interaction["target"]
            if src and tgt and src in follow_graph.nodes and tgt in follow_graph.nodes:
                elements.append(
                    {"data": {"source": src, "target": tgt}, "classes": "highlighted_edge"}
                )

        # 3. Stats Chart
        if metric_values:
            try:
                counts = pd.Series(metric_values).value_counts().reset_index()
                counts.columns = ["Value", "Count"]

                # Ensure color_map is available
                cmap = color_map if "color_map" in locals() else {}

                fig = px.bar(counts, x="Value", y="Count", color="Value", color_discrete_map=cmap)
                fig.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font={"color": THEME["text"]},
                    showlegend=False,
                    margin={"l": 0, "r": 0, "t": 30, "b": 0},
                    title=f"{probe_metric} Distribution",
                )
            except Exception as e:
                print(f"Error generating plot: {e}")
                fig = go.Figure()
                fig.update_layout(title=f"Error: {e}")
        else:
            fig = go.Figure()
            fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font={"color": THEME["text"]},
                title="No Data for Metric",
            )

        # 4. Interaction Feed
        # Filter interactions
        selected_agent = selected_node_data["id"] if selected_node_data else None

        feed_items = []
        for interaction in ep_interactions:
            # Filter if node selected
            if (
                selected_agent
                and interaction["source"] != selected_agent
                and interaction["target"] != selected_agent
            ):
                continue

            action_color = THEME["accent_primary"]
            if interaction["action"] == "replied":
                action_color = THEME["accent_secondary"]

            timestamp = f"Ep {episode}"

            item = html.Div(
                style={
                    "marginBottom": "15px",
                    "borderLeft": f"3px solid {action_color}",
                    "paddingLeft": "10px",
                },
                children=[
                    html.Div(
                        style={"fontSize": "0.8rem", "color": "#888"},
                        children=[
                            html.Span(
                                f"{interaction['source']}",
                                style={"color": "#fff", "fontWeight": "bold"},
                            ),
                            html.Span(f" • {interaction['action']} • {timestamp}"),
                        ],
                    ),
                    html.Div(
                        style={"marginTop": "4px", "fontSize": "0.9rem"},
                        children=interaction["content"],
                    ),
                ],
            )
            feed_items.append(item)

        if not feed_items:
            feed_items = [
                html.Div(
                    "No interactions for this episode.",
                    style={"color": "#666", "fontStyle": "italic"},
                )
            ]

        # 5. Stylesheet dynamic updates
        stylesheet = [
            {
                "selector": "node",
                "style": {
                    "content": "data(label)",
                    "color": "#fff",
                    "background-color": THEME["node_default"],  # Default
                    "width": 45,
                    "height": 45,
                    "font-size": "12px",
                    "text-valign": "center",
                    "text-halign": "center",
                    "border-width": 0,
                },
            },
            {
                "selector": "edge",
                "style": {
                    "width": 2,
                    "line-color": THEME["edge_default"],
                    "target-arrow-shape": "triangle",
                    "target-arrow-color": THEME["edge_default"],
                    "curve-style": "bezier",
                    "opacity": 0.4,
                },
            },
            {
                "selector": ".highlighted_edge",
                "style": {
                    "line-color": THEME["accent_primary"],
                    "target-arrow-color": THEME["accent_primary"],
                    "width": 4,
                    "opacity": 1.0,
                    "z-index": 999,
                },
            },
        ]

        # Inject per-node colors into stylesheet
        for node, color in node_colors.items():
            stylesheet.append(
                {"selector": f'node[id = "{node}"]', "style": {"background-color": color}}
            )

        return elements, stylesheet, fig, feed_items, f"Episode: {episode}"

    return app


# --- Main ---


def main():
    parser = argparse.ArgumentParser(description="Generic Sim Visualization")
    parser.add_argument("--output_dir", required=True, help="Path to outputs")
    parser.add_argument("--port", type=int, default=8050)
    args = parser.parse_args()

    data = load_data_from_folder(Path(args.output_dir))
    app = create_dash_app(data)
    app.run_server(debug=True, port=args.port, host="0.0.0.0")


if __name__ == "__main__":
    main()
