"""Main application file for the Silisocs analysis dashboard."""

import argparse
import base64
import json
from io import StringIO

import dash
import dash_cytoscape as cyto
import pandas as pd
import plotly.graph_objs as go
from dash import Input, Output, State, html

from silisocs.evaluations.analysis.dashboard.data_processing import (
    deserialize_data,
    load_data_from_directory,
    load_data_from_folder,
    serialize_data,
)
from silisocs.evaluations.analysis.dashboard.graph_utils import (
    compute_positions,
    create_interactions_figure,
    create_probe_data_figure,
)
from silisocs.evaluations.analysis.dashboard.layout import create_app_layout, get_index_string
from silisocs.evaluations.analysis.dashboard.styles import (
    build_cytoscape_elements,
    build_stylesheet,
)
from silisocs.evaluations.analysis.dashboard.ui_components import (
    create_interaction_display,
)

cyto.load_extra_layouts()


def initialize_data(args):
    """Initialize data from command line arguments."""
    serialized_initial_data = None

    if args.output_dir:
        serialized_initial_data = load_from_directory(args.output_dir)

    return serialized_initial_data


def load_from_directory(directory_path):
    """Load data from a directory containing JSONL files."""
    return load_data_from_directory(directory_path)


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
        """update_data.

        :param n_clicks:
        :param contents:
        :param filenames:
        :param current_data:
        """
        if n_clicks == 0 or contents is None:
            raise dash.exceptions.PreventUpdate
        for filename in filenames:
            if "~" in filename:
                return dash.no_update, dash.no_update, f"Unsupported short filename: {filename}", ""
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
                posts,
                probe_data,
                act_data,
            ) = load_data_from_folder(decoded_contents)

            serialized_data = serialize_data(
                follow_graph,
                interactions_by_episode,
                active_users_by_episode,
                posts,
                probe_data,
                act_data,
            )

            # Add raw data for heatmap
            raw_data_combined = []
            for name, content in decoded_contents.items():
                if "action_events" in name or "probe_events" in name:
                    df = pd.read_json(StringIO(content), lines=True)
                    raw_data_combined.extend(df.to_dict(orient="records"))

            serialized_data["raw_data"] = raw_data_combined

            return "Data Loaded Successfully", serialized_data, "", ""

        except Exception as e:
            return dash.no_update, dash.no_update, f"Error: {e!s}", ""

    @app.callback(Output("heatmap-graph", "figure"), Input("data-store", "data"))
    def update_heatmap(data_store):
        """Update the action alignment heatmap."""
        if not data_store or "raw_data" not in data_store:
            return go.Figure(data=[], layout=go.Layout(title="No data uploaded"))

        raw_data = data_store["raw_data"]
        df = pd.DataFrame(raw_data)

        # Filter for action events with suggested_action
        dft = df[(df["event_type"] == "action")]

        if not df.empty and isinstance(df.iloc[0]["data"], str):
            dft["data"] = dft["data"].apply(lambda x: json.loads(x) if isinstance(x, str) else x)

        dft = dft[dft["data"].apply(lambda x: x.get("suggested_action") is not None)].copy()

        if dft.empty:
            return go.Figure(
                data=[], layout=go.Layout(title="No action records with suggested_action found")
            )

        dft["suggested_action"] = dft["data"].apply(lambda x: x.get("suggested_action"))

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
            posts,
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
        total_users = len(follow_graph.nodes)
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
                    interactions_content.append(create_interaction_display(interaction, posts))
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
