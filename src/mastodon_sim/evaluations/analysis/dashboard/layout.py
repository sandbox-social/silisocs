"""Layout components and structure for the dashboard."""

import dash_cytoscape as cyto
from dash import dcc, html

from mastodon_sim.evaluations.analysis.dashboard.styles import get_base_stylesheet


def get_index_string():
    """Return the custom index string with CSS and JavaScript."""
    return """
    <!DOCTYPE html>
    <html>
        <head>
            {%metas%}
            <title>{%title%}</title>
            {%favicon%}
            {%css%}
            <style>
                html, body {
                    margin: 0;
                    padding: 0;
                    width: 100%;
                    height: 100%;
                    overflow-x: hidden;
                }
                * {
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    box-sizing: border-box;
                }
                .dashboard-container {
                    max-width: 100%;
                    margin: 0;
                    padding: 10px;
                    width: 100%;
                    background-color: #f9f9f9;
                }
                .dashboard-header {
                    margin-bottom: 20px;
                    padding-bottom: 10px;
                    border-bottom: 1px solid #ddd;
                    width: 100%;
                }
                .plot-container {
                    background-color: white;
                    border: 1px solid #e0e0e0;
                    border-radius: 10px;
                    padding: 20px;
                    margin-bottom: 20px;
                    box-shadow: 0px 2px 5px rgba(0, 0, 0, 0.05);
                    height: 400px;
                    width: 100%;
                    margin: 0 auto;
                }
                .plot-title {
                    font-size: 16px;
                    font-weight: 500;
                    color: #333;
                    margin-bottom: 10px;
                    height: 20px;
                }
                .plot-row {
                    display: flex;
                    flex-wrap: wrap;
                    gap: 20px;
                    width: 100%;
                }
                .plot-col-third {
                    flex: 1 1 calc(33.333% - 20px);
                    min-width: 250px;
                }
                .js-plotly-plot, .plot-container .js-plotly-plot, #graph-div {
                    width: 100%;
                }
                .dash-graph {
                    height: 300px !important;
                    width: 100%;
                }
                .main-svg, .svg-container {
                    width: 100%;
                }
                .cyto-div-row {
                    display: flex;
                    width: 100%;
                    gap: 20px;
                    margin-bottom: 20px;
                    align-items: stretch;
                }

                .cyto-container {
                    /* Use calc to account for the gap so the total doesn't exceed 100% */
                    flex: 0 0 calc(80% - 20px);
                    min-height: 500px;
                    background-color: white;
                    border: 1px solid #e0e0e0;
                    border-radius: 5px;
                    position: relative; /* Anchors the dropdown and episode text */
                    overflow: hidden;
                }

                .detail-container {
                    flex: 0 0 20%;
                    background-color: white;
                    border: 1px solid #e0e0e0;
                    border-radius: 5px;
                    padding: 15px;
                    height: 500px; /* Match the graph height exactly */
                    overflow-y: auto;
                }
                .detail-container.hidden {
                    display: none;
                }
            </style>
        </head>
        <body>
            {%app_entry%}
            <footer>
                {%config%}
                {%scripts%}
                {%renderer%}
            </footer>
        </body>
    </html>
    """


def create_upload_section(show=False):
    """Simplified upload section without complex folder logic."""
    return html.Div(
        id="upload-screen",
        className="dashboard-container",
        style={
            "display": "none" if show else "flex",
            "flexDirection": "column",
            "alignItems": "center",
        },
        children=[
            html.H3("Upload Data Files"),
            html.P("Select all .jsonl files (Action, Probe, and Prompts) together:"),
            dcc.Upload(
                id="upload-app-logger",
                children=html.Div(
                    [
                        "Drag and Drop or ",
                        html.A(
                            "Select Multiple Files",
                            style={"color": "#1a73e8", "fontWeight": "bold"},
                        ),
                    ]
                ),
                style={
                    "width": "400px",
                    "height": "100px",
                    "lineHeight": "100px",
                    "borderWidth": "2px",
                    "borderStyle": "dashed",
                    "borderRadius": "10px",
                    "textAlign": "center",
                    "margin": "20px",
                },
                multiple=True,  # This allows multi-file selection without webkitdirectory
            ),
            html.Button(
                "Launch Dashboard",
                id="submit-button",
                n_clicks=0,
                style={
                    "width": "200px",
                    "height": "50px",
                    "backgroundColor": "#4CAF50",
                    "color": "white",
                    "border": "none",
                    "borderRadius": "8px",
                },
            ),
            html.Div(id="upload-error-message", style={"color": "red", "marginTop": "20px"}),
        ],
    )


def create_dashboard_section(show=False):
    """Create the main dashboard section of the layout."""
    return html.Div(
        id="dashboard",
        style={"display": "block" if show else "none"},
        children=[
            # Three plots in a row
            html.Div(
                className="plot-row",
                children=[
                    create_plot_container("probe-data-line", "Vote Distribution Over Time"),
                    create_plot_container("interactions-line-graph", "Interactions Over Time"),
                    create_plot_container("heatmap-graph", "Action Alignment Histogram"),
                ],
            ),
            html.Br(),
            html.Br(),
            # Network visualization section
            html.Div(html.H2("Interaction Network"), style={"padding": "0px"}),
            html.Div(
                className="cyto-div-row",
                children=[
                    create_cytoscape_container(),
                    create_detail_panel(),
                ],
            ),
            # Episode slider
            html.Label("Select Episode:"),
            dcc.Slider(
                id="episode-slider",
                min=0,
                max=0,
                value=0,
                marks={},
                step=None,
                tooltip={"placement": "bottom", "always_visible": True},
            ),
            html.Hr(style={"margin": "20px 0"}),
            # # Plan viewer
            # html.Div(
            #     id="plan-viewer-section",
            #     style={"padding": "20px"},
            #     children=[
            #         html.H2("Agent Plans For This Episode", style={"textAlign": "center"}),
            #         html.Div(id="plan-output"),
            #     ],
            # ),
            # html.Hr(style={"margin": "20px 0"}),
            # # Thoughts viewer
            # html.Div(
            #     id="jsonl-viewer-section",
            #     style={"padding": "20px"},
            #     children=[
            #         html.H2("Within-agent processing", style={"textAlign": "center"}),
            #         html.P(
            #             "Data loaded from prompts_and_responses.jsonl in the uploaded folder.",
            #             style={"textAlign": "center", "fontStyle": "italic", "color": "#666"},
            #         ),
            #         html.Div(id="jsonl-output"),
            #         dcc.Store(id="jsonl-store"),
            #     ],
            # ),
        ],
    )


def create_plot_container(graph_id, title):
    """Create a plot container with title and graph."""
    return html.Div(
        className="plot-col-third",
        children=[
            html.Div(
                className="plot-container",
                children=[
                    html.Div(className="plot-title", children=title),
                    dcc.Graph(
                        id=graph_id,
                        className="dash-graph",
                        config={"displayModeBar": False},
                        responsive=True,
                    ),
                ],
            )
        ],
    )


def create_cytoscape_container():
    """Create the Cytoscape graph container."""
    return html.Div(
        className="cyto-container",
        children=[
            html.Div(
                [
                    dcc.Dropdown(
                        id="name-selector",
                        options=[],
                        value=None,
                        placeholder="Select Name",
                        clearable=True,
                        style={"padding": "10px", "width": "200px", "z-index": "1000"},
                    ),
                ]
            ),
            html.Div(
                id="current-episode",
                style={
                    "position": "absolute",
                    "top": "10px",
                    "right": "10px",
                    "padding": "10px",
                    "font-size": "20px",
                    "font-weight": "bold",
                    "background-color": "#ffcc99",
                    "z-index": "1000",
                },
                children="",
            ),
            cyto.Cytoscape(
                id="cytoscape-graph",
                elements=[],
                layout={"name": "preset", "positions": {}},
                style={
                    "width": "100%",
                    "height": "500px",
                    "background-color": "#e1e1e1",
                },
                stylesheet=get_base_stylesheet(),
            ),
        ],
    )


def create_detail_panel():
    """Create the detail panel for showing interactions."""
    return html.Div(
        id="detail-panel",
        className="detail-container hidden",
        children=[
            html.H3("Platform Interactions"),
            html.Div(id="interactions-window", style={"overflowY": "auto", "height": "500px"}),
        ],
    )


def create_app_layout(serialized_initial_data=None):
    """Create the complete app layout."""
    show_dashboard = serialized_initial_data is not None

    return html.Div(
        className="dashboard-container",
        children=[
            # Header
            html.Div(
                className="dashboard-header",
                children=[html.H1(id="dashboard-title", children="Social Sandbox Dashboard")],
            ),
            # Upload section in header (for re-uploading)
            html.Div(
                id="dashboard-upload-section",
                style={"display": "flex" if show_dashboard else "none"},
                children=[
                    html.Div(
                        children=[html.H3(id="dashboard-showfilename")],
                        style={"display": "inline-block", "float": "left", "width": "80%"},
                    ),
                    html.Div(
                        children=[
                            html.Label("Upload another output folder: "),
                            dcc.Upload(
                                id="upload-app-logger-dashboard",
                                children=html.Label(html.Span("Select Folder")),
                                multiple=True,
                            ),
                            html.Button("Upload Folder", id="upload-button-dashboard", n_clicks=0),
                        ],
                        style={"display": "inline-block", "float": "right", "width": "20%"},
                    ),
                ],
            ),
            # Data store
            dcc.Store(id="data-store", data=serialized_initial_data),
            # Upload screen
            create_upload_section(show=show_dashboard),
            # Dashboard
            create_dashboard_section(show=show_dashboard),
            # Error messages
            html.Div(id="error-message", style={"color": "red"}),
        ],
    )
