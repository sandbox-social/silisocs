"""Main application file for Social Sandbox Dashboard."""

import argparse
import base64
import json
from io import StringIO
from pathlib import Path

import dash
import dash_cytoscape as cyto
import pandas as pd
import plotly.graph_objs as go
from dash import Input, Output, State, html

cyto.load_extra_layouts()

# Import from local modules
from data_processing import (
    deserialize_data,
    load_data_from_folder,
    serialize_data,
    stream_filtered_jsonl,
)
from graph_utils import (
    compute_positions,
    create_interactions_figure,
    create_probe_data_figure,
)
from layout import create_app_layout, get_index_string
from styles import build_cytoscape_elements, build_stylesheet
from ui_components import (
    create_display,
    create_display_plan,
    create_interaction_display,
)


def initialize_data(args):
    """Initialize data from command line arguments."""
    serialized_initial_data = None

    if args.output_dir:
        serialized_initial_data = load_from_directory(args.output_dir)
    elif args.output_file:
        print("Legacy single file mode not implemented")

    return serialized_initial_data


def load_from_directory(directory_path):
    """Load data from a directory containing JSONL files."""
    directory_path = Path(directory_path)

    if not directory_path.exists() or not directory_path.is_dir():
        print(f"Error: Invalid directory {directory_path}")
        return None

    folder_contents = {}
    required_files = ["mastodon_action_events.jsonl", "probe_events.jsonl"]
    optional_files = ["prompts_and_responses.jsonl"]

    for file_pattern in required_files + optional_files:
        file_path = directory_path / file_pattern
        if file_path.exists():
            with open(file_path, encoding="utf-8") as f:
                folder_contents[file_pattern] = f.read()
            print(f"Loaded {file_pattern}")
        elif file_pattern in required_files:
            print(f"Error: Required file {file_pattern} not found")
            return None

    try:
        (
            follow_graph,
            interactions_by_episode,
            active_users_by_episode,
            toots,
            probe_data,
            act_data,
        ) = load_data_from_folder(folder_contents)

        serialized_data = serialize_data(
            follow_graph,
            interactions_by_episode,
            active_users_by_episode,
            toots,
            probe_data,
            act_data,
        )

        # Add raw data for heatmap
        raw_data_combined = []
        for filename, content in folder_contents.items():
            if filename.endswith("mastodon_action_events.jsonl") or filename.endswith(
                "probe_events.jsonl"
            ):
                df = pd.read_json(StringIO(content), lines=True)
                raw_data_combined.extend(df.to_dict(orient="records"))

        serialized_data["raw_data"] = raw_data_combined

        # Store prompts content if available
        if "prompts_and_responses.jsonl" in folder_contents:
            serialized_data["prompts_content"] = folder_contents["prompts_and_responses.jsonl"]

        print(f"Successfully loaded data from directory: {directory_path}")
        return serialized_data

    except Exception as e:
        print(f"Error loading data from directory: {e}")
        return None


def process_folder_contents(contents_list, filename_list):
    """Debug version: Prints file discovery details to the terminal."""
    print("\n" + "=" * 50)
    print("DEBUG: process_folder_contents started")

    if not contents_list:
        print("DEBUG ERROR: contents_list is None or Empty")
        return None
    if not filename_list:
        print("DEBUG ERROR: filename_list is None or Empty")
        return None

    print(f"DEBUG: Browser sent {len(contents_list)} total items from the folder.")

    folder_contents = {}
    for i, (content, filename) in enumerate(zip(contents_list, filename_list, strict=False)):
        # Normalize paths for Windows/Mac/Linux
        filename_only = filename.replace("\\", "/").split("/")[-1]

        print(f"  [{i}] Checking file: {filename} -> (Parsed as: {filename_only})")

        if filename_only.endswith(".jsonl"):
            try:
                content_type, content_string = content.split(",")
                decoded = base64.b64decode(content_string)
                content_str = decoded.decode("utf-8")

                folder_contents[filename_only] = content_str
                print(f"      ✅ SUCCESS: {filename_only} (Size: {len(content_str)} chars)")
            except Exception as e:
                print(f"      ❌ DECODE ERROR on {filename_only}: {e}")
        else:
            print(f"      ⏩ SKIPPING: {filename_only} (Not a .jsonl file)")

    print(f"DEBUG: Total valid .jsonl files collected: {len(folder_contents)}")
    print("DEBUG: Files keys found: " + ", ".join(folder_contents.keys()))
    print("=" * 50 + "\n")

    return folder_contents if folder_contents else None


def create_app(serialized_initial_data=None):
    """Create and configure the Dash application."""
    app = dash.Dash(__name__)
    app.index_string = get_index_string()
    app.layout = create_app_layout(serialized_initial_data)

    # Register callbacks
    register_callbacks(app)

    return app


def register_callbacks(app):
    """Register all callbacks for the dashboard."""

    @app.callback(
        Output("jsonl-output", "children"),
        [
            Input("data-store", "data"),
            Input("name-selector", "value"),
            Input("episode-slider", "value"),
        ],
        prevent_initial_call=True,
    )
    def process_jsonl_data(data_store, selected_name, selected_episode):
        """Process JSONL data with streaming and filtering."""
        if not data_store or "prompts_content" not in data_store:
            return html.Div(
                [
                    html.P("No prompts_and_responses.jsonl file found."),
                    html.P("This file is needed to display agent thoughts."),
                ]
            )

        prompts_content = data_store["prompts_content"]
        if not prompts_content:
            return html.Div([html.P("Prompts content is empty.")])

        try:
            encoded_content = base64.b64encode(prompts_content.encode("utf-8")).decode("utf-8")
            mock_contents = "data:text/plain;base64," + encoded_content

            return [
                create_display(record)
                for record in stream_filtered_jsonl(mock_contents, selected_name, selected_episode)
            ]
        except Exception as e:
            return html.Div([html.P(f"Error processing prompts data: {e!s}")])

    @app.callback(
        Output("plan-output", "children"),
        [Input("data-store", "data"), Input("episode-slider", "value")],
        prevent_initial_call=True,
    )
    def process_action_data(data, selected_episode):
        """Process action data with filtering."""
        if not data:
            return None

        try:
            act_data = data["act_data"][str(selected_episode)]

            # Group by agent and create displays
            agents = {}
            for entry in act_data:
                agent_name = entry["source_user"]
                if agent_name not in agents:
                    agents[agent_name] = []
                agents[agent_name].append(entry["data"])

            # Create display for each agent
            objs = []
            for agent_name in sorted(agents.keys()):
                objs.append(create_display_plan(agent_name, agents[agent_name]))

            return objs
        except Exception as e:
            print(f"Error processing actions: {e!s}")
            return None

    @app.callback(
        [
            Output("upload-screen", "style"),
            Output("dashboard", "style"),
            Output("dashboard-upload-section", "style"),
            Output("name-selector", "options"),
        ],
        [Input("data-store", "data")],
    )
    def toggle_layout(data_store):
        """Toggle between upload screen and dashboard."""
        if data_store and "nodes" in data_store and len(data_store["nodes"]) > 0:
            return (
                {"display": "none"},
                {"display": "block"},
                {"display": "flex"},
                [{"label": name, "value": name} for name in sorted(data_store["nodes"])],
            )
        return {"display": "flex"}, {"display": "none"}, {"display": "none"}, []

    @app.callback(
        [
            Output("dashboard-showfilename", "children"),
            Output("data-store", "data"),
            Output("upload-error-message", "children"),
            Output("error-message", "children"),
        ],
        [Input("submit-button", "n_clicks")],
        [
            State("upload-app-logger", "contents"),
            State("upload-app-logger", "filename"),
            State("data-store", "data"),
        ],
        prevent_initial_call=True,
    )
    def update_data(n_clicks, contents, filenames, current_data):
        if n_clicks == 0 or contents is None:
            raise dash.exceptions.PreventUpdate

        print(f"DEBUG: Processing {len(filenames)} files...")

        try:
            # Map filenames to contents
            folder_contents = {
                name.split("/")[-1]: content
                for name, content in zip(filenames, contents, strict=False)
            }

            # Decode the contents
            decoded_contents = {}
            for name, content_str in folder_contents.items():
                if name.endswith(".jsonl"):
                    _, content_b64 = content_str.split(",")
                    decoded_contents[name] = base64.b64decode(content_b64).decode("utf-8")

            # Use existing processing logic
            (
                follow_graph,
                interactions_by_episode,
                active_users_by_episode,
                toots,
                probe_data,
                act_data,
            ) = load_data_from_folder(decoded_contents)

            serialized_data = serialize_data(
                follow_graph,
                interactions_by_episode,
                active_users_by_episode,
                toots,
                probe_data,
                act_data,
            )

            # Add raw data for heatmap
            raw_data_combined = []
            for name, content in decoded_contents.items():
                if "mastodon_action_events" in name or "probe_events" in name:
                    df = pd.read_json(StringIO(content), lines=True)
                    raw_data_combined.extend(df.to_dict(orient="records"))

            serialized_data["raw_data"] = raw_data_combined

            if "prompts_and_responses.jsonl" in decoded_contents:
                serialized_data["prompts_content"] = decoded_contents["prompts_and_responses.jsonl"]

            return "Data Loaded Successfully", serialized_data, "", ""

        except Exception as e:
            print(f"DEBUG ERROR: {e}")
            return dash.no_update, dash.no_update, f"Error: {e!s}", ""

    @app.callback(Output("heatmap-graph", "figure"), Input("data-store", "data"))
    def update_heatmap(data_store):
        """Update the action alignment heatmap."""
        if not data_store or "raw_data" not in data_store:
            return go.Figure(data=[], layout=go.Layout(title="No data uploaded"))

        raw_data = data_store["raw_data"]
        df = pd.DataFrame(raw_data)

        # Filter for action events with suggested_action
        dft = df[(df["event_type"] == "action") & (df["label"] != "inner_actions")]

        if not df.empty and isinstance(df.iloc[0]["data"], str):
            dft["data"] = dft["data"].apply(lambda x: json.loads(x) if isinstance(x, str) else x)

        dft = dft[dft["data"].apply(lambda x: x.get("suggested_action") is not None)].copy()

        if dft.empty:
            return go.Figure(
                data=[], layout=go.Layout(title="No action records with suggested_action found")
            )

        # Convert "toot" to "post"
        dft["suggested_action"] = dft["data"].apply(
            lambda x: "post" if x.get("suggested_action") == "toot" else x.get("suggested_action")
        )

        # Create contingency table
        contingency = pd.crosstab(dft["label"], dft["suggested_action"])

        # Create heatmap
        heatmap_fig = go.Figure(
            data=go.Heatmap(
                z=contingency.values,
                x=list(contingency.columns),
                y=list(contingency.index),
                colorscale="YlOrRd",
                colorbar=dict(title="Count"),
            )
        )

        heatmap_fig.update_layout(
            xaxis_title="Suggested Action",
            yaxis_title="Chosen Action",
            margin=dict(l=30, r=100, t=10, b=30),
            font=dict(family="Segoe UI"),
        )

        return heatmap_fig

    @app.callback(
        [
            Output("cytoscape-graph", "elements"),
            Output("cytoscape-graph", "layout"),
            Output("cytoscape-graph", "stylesheet"),
            Output("probe-data-line", "figure"),
            Output("interactions-line-graph", "figure"),
            Output("current-episode", "children"),
            Output("interactions-window", "children"),
            Output("detail-panel", "className"),
            Output("episode-slider", "min"),
            Output("episode-slider", "max"),
            Output("episode-slider", "value"),
            Output("episode-slider", "marks"),
            Output("name-selector", "value"),
        ],
        [
            Input("episode-slider", "value"),
            Input("name-selector", "value"),
            Input("data-store", "data"),
        ],
    )
    def update_graph(selected_episode, selected_name, data_store):
        """Update all graph-related components."""
        if not data_store:
            return (
                [],
                {"name": "preset", "positions": {}},
                [],
                {},
                {},
                "Episode: N/A",
                [],
                "detail-container hidden",
                0,
                0,
                0,
                {},
                None,
            )

        (
            follow_graph,
            interactions_by_episode,
            active_users_by_episode,
            toots,
            probe_data,
            act_data,
        ) = deserialize_data(data_store)

        # Compute positions
        all_positions = compute_positions(follow_graph)
        layout = {"name": "preset", "positions": all_positions}

        # Build Cytoscape elements
        elements = build_cytoscape_elements(follow_graph, interactions_by_episode)

        # Build stylesheet
        stylesheet = build_stylesheet(
            follow_graph, selected_episode, interactions_by_episode, probe_data, selected_name
        )

        # Create probe data figure
        probe_fig = create_probe_data_figure(probe_data)

        # Create interactions figure
        total_users = len(follow_graph.nodes) - 1
        interactions_fig = create_interactions_figure(
            interactions_by_episode, active_users_by_episode, total_users
        )

        # Build interactions window content
        interactions_content = []
        interactions_class = "detail-container" if selected_name else "detail-container hidden"

        if selected_name:
            interactions = [
                interaction
                for interaction in interactions_by_episode.get(selected_episode, [])
                if interaction["source"] == selected_name
            ]

            if interactions:
                for interaction in interactions:
                    interactions_content.append(create_interaction_display(interaction, toots))
            else:
                interactions_content.append(
                    html.P("No interactions found for this agent in the selected episode.")
                )
        else:
            interactions_content.append(html.P("Select an agent to view their interactions."))

        # Set episode slider properties
        int_episodes = sorted(interactions_by_episode.keys())
        slider_min = min(int_episodes) if int_episodes else 0
        slider_max = max(int_episodes) if int_episodes else 0
        slider_value = selected_episode if selected_episode in int_episodes else slider_min
        slider_marks = {str(ep): f"{ep}" for ep in int_episodes}

        return (
            elements,
            layout,
            stylesheet,
            probe_fig,
            interactions_fig,
            f"Episode: {selected_episode}",
            interactions_content,
            interactions_class,
            slider_min,
            slider_max,
            slider_value,
            slider_marks,
            selected_name,
        )


def main():
    """Main entry point for the dashboard."""
    parser = argparse.ArgumentParser(
        description="Run the Dash app with specific data file or directory."
    )
    parser.add_argument(
        "--output_file",
        type=str,
        default=None,
        help="Path to output log file (legacy mode).",
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        default=None,
        help="Path to directory with JSONL files.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode with auto-reload.",
    )

    args = parser.parse_args()

    serialized_initial_data = initialize_data(args)
    app = create_app(serialized_initial_data)

    # Use debug mode for development, disable for production/debugging
    app.run_server(
        debug=args.debug,
        dev_tools_hot_reload=args.debug,
        use_reloader=False,  # Disable reloader to allow IDE breakpoints
    )


if __name__ == "__main__":
    main()
