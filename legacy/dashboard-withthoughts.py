import argparse
import base64
import json
from collections.abc import Generator
from pathlib import Path
from typing import Any

import dash
import dash_cytoscape as cyto
import networkx as nx
import pandas as pd
import plotly.graph_objs as go
from dash import Input, Output, State, dcc, html
from plotly.subplots import make_subplots

cyto.load_extra_layouts()
from io import StringIO

PROJECT_ROOT = Path(__file__).resolve().parents[2]
print("project root: " + str(PROJECT_ROOT))
from sim.analysis_utils.output_proc_utils import post_process_output

# ---------------------Example-specific code
PROBE_LABEL = "VotePref"
custom_names = ["Bill Fredrickson", "Bradley Carter"]
line_colors = {
    "Bill Fredrickson": "#1f77b4",
    "Bradley Carter": "#ff7f0e",
    "did not vote": "#000000",
}
node_colors = {"Bill Fredrickson": "#1f77b4", "Bradley Carter": "#ff7f0e", "Other": "#808080"}


def probe_plot_preprocessing(probe_data):
    candidate1_votes_over_time = []
    candidate2_votes_over_time = []
    non_votes_over_time = []
    x_data = sorted(probe_data.keys())
    for ep in x_data:
        ep_votes = probe_data[ep]
        total_ep_votes = len(ep_votes)
        candidate1_votes = sum(
            1 for vote in ep_votes.values() if vote == custom_names[0].split(" ")[0]
        )
        candidate2_votes = sum(
            1 for vote in ep_votes.values() if vote == custom_names[1].split(" ")[0]
        )
        candidate1_votes_over_time.append(
            (candidate1_votes / total_ep_votes) * 100 if total_ep_votes > 0 else 0
        )
        candidate2_votes_over_time.append(
            (candidate2_votes / total_ep_votes) * 100 if total_ep_votes > 0 else 0
        )
        non_votes_over_time.append(
            ((total_ep_votes - candidate2_votes - candidate1_votes) / total_ep_votes) * 100
            if total_ep_votes > 0
            else 0
        )
    graphs_data = [
        {
            "label": "for " + custom_names[0].split(" ")[0],
            "data": {"x": x_data, "y": candidate1_votes_over_time},
            "color": line_colors[custom_names[0]],
        },
        {
            "label": "for " + custom_names[1].split(" ")[0],
            "data": {"x": x_data, "y": candidate2_votes_over_time},
            "color": line_colors[custom_names[1]],
        },
        {
            "label": "did not vote",
            "data": {"x": x_data, "y": non_votes_over_time},
            "color": line_colors["did not vote"],
        },
    ]

    title_label = "Vote Distribution Over Time"
    yaxis_label = "Vote Percentage"
    return graphs_data, title_label, yaxis_label


# -------------------------------------------------------------


# data loading
def stream_filtered_jsonl(
    content_string: str, selected_name: str, selected_episode: int
) -> Generator[dict[Any, Any], None, None]:
    """
    Stream and filter JSONL content line by line, only yielding matching records.

    Args:
        content_string: Base64 encoded content string
        selected_name: Name to filter by
        selected_episode: Episode index to filter by

    Yields
    ------
        Dict: Parsed JSON objects that match the filter criteria
    """
    # Handle the data URI format if present
    if "," in content_string:
        _, content_string = content_string.split(",", 1)

    if not content_string:
        print("content string is empty")
    # Create a text stream from the decoded content
    decoded = base64.b64decode(content_string).decode("utf-8")
    stream = StringIO(decoded)

    # Process and filter the stream line by line
    for line in stream:
        line = line.strip()
        if not line:  # Skip empty lines
            continue
        try:
            record = json.loads(line)
            # Only yield records that match our filters
            if (selected_name is None or record.get("agent_name") == selected_name) and (
                selected_episode is None or record.get("episode_idx") == selected_episode
            ):
                yield record
        except json.JSONDecodeError as e:
            print(f"Error processing JSONL line: {e!s}")
            continue


def convert_linebreaks(string):
    lst = string.split("\n")
    item = html.Br()
    result = [item] * (len(lst) * 2 - 1)
    result[0::2] = lst
    return result


def create_display(entry):
    first_line = entry["prompt"].split("\n")[0]
    return html.Details(
        [
            html.Summary(f"Prompt: {first_line}..."),
            html.Div(
                [
                    html.Div(
                        [
                            html.Strong("Full Prompt: "),
                            html.Span(convert_linebreaks(entry["prompt"])),
                        ],
                        style={"backgroundColor": "#e8f0fe", "padding": "5px", "margin": "5px"},
                    ),
                    html.Div(
                        [html.Strong("Output: "), html.Span(convert_linebreaks(entry["output"]))],
                        style={"backgroundColor": "#e2f7e1", "padding": "5px", "margin": "5px"},
                    ),
                    *[
                        html.Div([html.Strong(f"{k}: "), html.Span(convert_linebreaks(str(v)))])
                        for k, v in entry.items()
                        if k not in {"prompt", "output"}
                    ],
                ]
            ),
        ],
        style={"backgroundColor": "#f4f4f4", "padding": "10px", "margin": "5px"},
    )


def create_display_plan(entry_plan, entry_acts):
    return html.Details(
        [
            html.Summary(entry_plan),
            html.Div(
                [
                    html.Div(
                        [
                            html.Span(convert_linebreaks("\n- " + "\n- ".join(entry_acts))),
                        ],
                        style={"backgroundColor": "#e8f0fe", "padding": "5px", "margin": "5px"},
                    ),
                ]
            ),
        ],
        style={"backgroundColor": "#f4f4f4", "padding": "10px", "margin": "5px"},
        open=True,
    )


def compute_positions(graph):
    pos = nx.kamada_kawai_layout(graph, scale=750)
    scaled_pos = {}
    for node, (x, y) in pos.items():
        scaled_pos[node] = {"x": x, "y": y}

    return scaled_pos


# Serialization function to convert complex data structures into JSON-serializable format
def serialize_data(
    follow_graph,
    interactions_by_episode,
    active_users_by_episode,
    toots,
    probe_data,
    plan_data,
    act_data,
):
    return {
        "nodes": list(follow_graph.nodes),
        "edges": list(follow_graph.edges),
        "interactions_by_episode": interactions_by_episode,
        "active_users_by_episode": {k: list(v) for k, v in active_users_by_episode.items()},
        "toots": toots,
        "probe_data": probe_data,
        "plan_data": plan_data,
        "act_data": act_data,
    }


# Deserialization function to convert JSON-serializable data back into original structures
def deserialize_data(serialized):
    follow_graph = nx.DiGraph()
    follow_graph.add_nodes_from(serialized["nodes"])
    follow_graph.add_edges_from(serialized["edges"])

    # Convert episode keys back to integers
    interactions_by_episode = {int(k): v for k, v in serialized["interactions_by_episode"].items()}
    active_users_by_episode = {
        int(k): set(v) for k, v in serialized["active_users_by_episode"].items()
    }
    toots = serialized["toots"]
    probe_data = {k: v for k, v in serialized["probe_data"].items()}
    # plan_data = {int(k): v for k, v in serialized["plan_data"].items()}
    plan_data = serialized["plan_data"]
    act_data = serialized["act_data"]
    return (
        follow_graph,
        interactions_by_episode,
        active_users_by_episode,
        toots,
        probe_data,
        plan_data,
        act_data,
    )


def get_target_user(row, toot_owner_dict):
    """
    Get target user from row, using toot_owner_dict to look up users by toot_id.

    Args:
        row: DataFrame row with label, source_user, and data fields
        toot_owner_dict: Dictionary mapping toot_id -> user

    Returns
    -------
        target_user string
    """
    if row.label == "post":
        target_user = row.source_user
    elif row.label == "like_toot" or row.label == "boost_toot":
        target_toot_id = row.data["target_toot_id"]
        target_user = toot_owner_dict.get(target_toot_id)
    elif row.label == "reply":
        target_toot_id = row.data["reply_to"]["toot_id"]
        target_user = toot_owner_dict.get(target_toot_id)
    return target_user


def get_int_dict(int_df, toot_owner_dict):
    """
    Create interaction dictionary from interactions dataframe.

    Args:
        int_df: DataFrame with interaction data
        toot_owner_dict: Dictionary mapping toot_id -> user

    Returns
    -------
        Dictionary mapping episode -> list of interaction data
    """
    past = dict(
        zip(
            ["post", "like_toot", "boost_toot", "reply"],
            ["posted", "liked", "boosted", "replied"],
            strict=False,
        )
    )
    int_df["int_data"] = int_df.apply(
        lambda x: {
            "action": past[x.label],
            "episode": x.episode,
            "source": x.source_user,
            "target": get_target_user(x, toot_owner_dict),
            "toot_id": str(x.data["toot_id"]),
        },
        axis=1,
    )
    int_df.int_data = int_df.apply(
        lambda x: (
            x.int_data | {"parent_toot_id": str(x.data["reply_to"]["toot_id"])}
            if x.label == "reply"
            else x.int_data
        ),
        axis=1,
    )
    return int_df.groupby("episode")["int_data"].apply(list).to_dict()


def get_toot_dict(int_df):
    """
    Create toot dictionary from interactions dataframe.
    Also returns toot_owner_dict for use in get_int_dict.

    Args:
        int_df: DataFrame with interaction data

    Returns
    -------
        tuple: (toot_dict, toot_owner_dict)
    """
    past = dict(
        zip(
            ["post", "like_toot", "boost_toot", "reply"],
            ["posted", "liked", "boosted", "replied"],
            strict=False,
        )
    )
    text_df = int_df.loc[(int_df.label == "post") | (int_df.label == "reply"), :].reset_index(
        drop=True
    )

    # handle Nones as toot_ids by appending an index
    no_toot_id = text_df.data.apply(lambda x: x["toot_id"] is None)
    text_df["no_toot_id_idx"] = -1
    text_df.loc[no_toot_id, "no_toot_id_idx"] = range(no_toot_id.sum())
    text_df.loc[no_toot_id, "data"] = text_df.loc[no_toot_id, :].apply(
        lambda x: x.data | {"toot_id": "None" + str(x.no_toot_id_idx)}, axis=1
    )

    text_df["toot_id"] = text_df.data.apply(lambda x: x["toot_id"])
    text_df = text_df.set_index("toot_id")
    text_df["text_data"] = text_df.apply(
        lambda x: {"user": x.source_user, "action": past[x.label], "content": x.data["post_text"]},
        axis=1,
    )
    text_df.text_data = text_df.apply(
        lambda x: (
            x.text_data | {"parent_toot_id": x.data["reply_to"]["toot_id"]}
            if x.label == "reply"
            else x.text_data
        ),
        axis=1,
    )

    # Create toot_owner_dict mapping toot_id -> user
    toot_owner_dict = text_df.apply(lambda x: x.source_user, axis=1).to_dict()

    return text_df.text_data.to_dict(), toot_owner_dict


# def get_target_user(row):
#     if row.label == "post":
#         target_user = row.source_user
#     elif row.label == "like_toot" or row.label == "boost_toot":
#         target_user = row.data["target_user"]
#     elif row.label == "reply":
#         target_user = row.data["reply_to"]["target_user"]
#     return target_user


# def get_int_dict(int_df):
#     past = dict(
#         zip(
#             ["post", "like_toot", "boost_toot", "reply"],
#             ["posted", "liked", "boosted", "replied"],
#             strict=False,
#         )
#     )
#     int_df["int_data"] = int_df.apply(
#         lambda x: {
#             "action": past[x.label],
#             "episode": x.episode,
#             "source": x.source_user,
#             "target": get_target_user(x),
#             "toot_id": str(x.data["toot_id"]),
#         },
#         axis=1,
#     )
#     int_df.int_data = int_df.apply(
#         lambda x: x.int_data | {"parent_toot_id": str(x.data["reply_to"]["toot_id"])}
#         if x.label == "reply"
#         else x.int_data,
#         axis=1,
#     )
#     return int_df.groupby("episode")["int_data"].apply(list).to_dict()


# def get_toot_dict(int_df):
#     past = dict(
#         zip(
#             ["post", "like_toot", "boost_toot", "reply"],
#             ["posted", "liked", "boosted", "replied"],
#             strict=False,
#         )
#     )
#     text_df = int_df.loc[(int_df.label == "post") | (int_df.label == "reply"), :].reset_index(
#         drop=True
#     )

#     # handle Nones as toot_ids by appending an index
#     no_toot_id = text_df.data.apply(lambda x: x["toot_id"] is None)
#     text_df["no_toot_id_idx"] = -1
#     text_df.loc[no_toot_id, "no_toot_id_idx"] = range(no_toot_id.sum())
#     text_df.loc[no_toot_id, "data"] = text_df.loc[no_toot_id, :].apply(
#         lambda x: x.data | {"toot_id": "None" + str(x.no_toot_id_idx)}, axis=1
#     )

#     text_df["toot_id"] = text_df.data.apply(lambda x: x["toot_id"])
#     text_df = text_df.set_index("toot_id")
#     text_df["text_data"] = text_df.apply(
#         lambda x: {"user": x.source_user, "action": past[x.label], "content": x.data["post_text"]},
#         axis=1,
#     )
#     text_df.text_data = text_df.apply(
#         lambda x: x.text_data | {"parent_toot_id": x.data["reply_to"]["toot_id"]}
#         if x.label == "reply"
#         else x.text_data,
#         axis=1,
#     )

#     return text_df.text_data.to_dict()


# def get_plan_dict(plan_df):
#     data = plan_df.groupby("episode")[["source_user", "data"]].apply(
#         lambda x: sorted(x.to_dict("records"), key=lambda d: d["source_user"])
#     )
#     return data.to_dict()


def get_act_dict(act_df):
    data_dict = act_df.groupby("episode")[["source_user", "data"]].apply(
        lambda x: x.to_dict("records")
    )
    return data_dict.to_dict()


def load_data_from_folder(folder_contents):
    """
    Load data from folder containing separate action_events.jsonl, probe_events.jsonl,
    and prompts_and_responses.jsonl files.

    Args:
        folder_contents: Dictionary with filename as key and file content as value

    Returns
    -------
        Same as load_data function
    """
    # Extract the three required files
    action_content = None
    probe_content = None
    prompts_content = None

    for filename, content in folder_contents.items():
        if filename.endswith("action_events.jsonl"):
            action_content = content
        elif filename.endswith("probe_events.jsonl"):
            probe_content = content
        elif filename.endswith("prompts_and_responses.jsonl"):
            prompts_content = content

    if not action_content:
        raise ValueError("action_events.jsonl file not found in folder")
    if not probe_content:
        raise ValueError("probe_events.jsonl file not found in folder")

    # Load action events
    action_df = pd.read_json(StringIO(action_content), lines=True)

    # Load probe events
    probe_df = pd.read_json(StringIO(probe_content), lines=True)

    # Combine the dataframes (similar to what the original load_data did)
    df = pd.concat([action_df, probe_df], ignore_index=True)

    names = list(df.source_user.unique())
    name_dict = dict(zip([n.split()[0] for n in names], names, strict=False))

    # replace all first name occurence with fullnames in target field
    # def replace_full(data):
    #     if "target_user" in data:
    #         if len(data["target_user"].split()) == 1:
    #             data.update(target_user=name_dict[data["target_user"]])
    #     return data

    # df.loc[:, "data"] = df.loc[:, "data"].apply(lambda x: replace_full(x))

    # make sure all tootids are strings
    def get_toot_id(data):
        if "toot_id" in data:
            data["toot_id"] = str(data["toot_id"])
        return data

    df["data"] = df.data.apply(get_toot_id)

    probe_df_processed, int_df, edge_df, act_df = post_process_output(df)

    # probe_data
    num_entries = len(probe_df_processed.loc[probe_df_processed.label == PROBE_LABEL])
    print(str(num_entries) + " probe entries!")
    probe_data = (
        probe_df_processed.loc[
            probe_df_processed.label == PROBE_LABEL, ["source_user", "response", "episode"]
        ]
        .groupby("episode")
        .apply(lambda x: dict(zip(x.source_user, x.response, strict=False)))
        .to_dict()
    )

    # final follow network
    follow_graph = nx.from_pandas_edgelist(
        edge_df, "source_user", "target_user", create_using=nx.DiGraph()
    )

    # active users with episode keys
    active_users_by_episode = int_df.groupby("episode")["source_user"].apply(set).to_dict()

    # interaction data
    int_dict = get_int_dict(int_df.copy())

    # toot_data (also adds target user to int_dict)
    # toot_dict = get_toot_dict(int_df.copy())
    toot_dict, toot_owner_dict = get_toot_dict(int_df.copy())
    int_dict = get_int_dict(int_df.copy(), toot_owner_dict)
    # plan_data
    # plan_dict = get_plan_dict(plan_df.copy())

    # inner action data
    act_dict = get_act_dict(act_df.copy())

    return (
        follow_graph,
        int_dict,
        active_users_by_episode,
        toot_dict,
        probe_data,
        # plan_dict,
        act_dict,
    )


def load_data(input_var):
    if len(input_var) < 500:
        df = pd.read_json(input_var, lines=True)
    else:
        df = pd.read_json(StringIO(input_var), lines=True)

    names = list(df.source_user.unique())
    name_dict = dict(zip([n.split()[0] for n in names], names, strict=False))

    # replace all first name occurence with fullnames in target field
    def replace_full(data):
        if "target_user" in data:
            if len(data["target_user"].split()) == 1:
                data.update(target_user=name_dict[data["target_user"]])
        return data

    df.loc[:, "data"] = df.loc[:, "data"].apply(lambda x: replace_full(x))

    # make sure all tootids are strings
    def get_toot_id(data):
        if "toot_id" in data:
            data["toot_id"] = str(data["toot_id"])
        return data

    df["data"] = df.data.apply(get_toot_id)

    probe_df, int_df, edge_df, plan_df, act_df = post_process_output(df)

    # probe_data
    num_entries = len(probe_df.loc[probe_df.label == PROBE_LABEL])
    print(str(num_entries) + " probe entries!")
    probe_data = (
        probe_df.loc[probe_df.label == PROBE_LABEL, ["source_user", "response", "episode"]]
        .groupby("episode")
        .apply(lambda x: dict(zip(x.source_user, x.response, strict=False)))
        .to_dict()
    )

    # final follow network
    follow_graph = nx.from_pandas_edgelist(
        edge_df, "source_user", "target_user", create_using=nx.DiGraph()
    )  # invalid in presence of unfollows (in which case use episodewise_graphbuild)

    # active users with episode keys
    active_users_by_episode = int_df.groupby("episode")["source_user"].apply(set).to_dict()

    # interaction data
    int_dict = get_int_dict(int_df.copy())

    # toot_data
    toot_dict = get_toot_dict(int_df.copy())

    # plan_data
    plan_dict = get_plan_dict(plan_df.copy())

    # inner action data
    # act_df['episode'] = act_df['episode'].apply(lambda x: str(x))
    act_dict = get_act_dict(act_df.copy())

    return (
        follow_graph,
        int_dict,
        active_users_by_episode,
        toot_dict,
        probe_data,
        plan_dict,
        act_dict,
    )


# Main entry point
if __name__ == "__main__":
    # Set up argument parsing
    parser = argparse.ArgumentParser(
        description="Run the Dash app with specific data file or directory."
    )
    parser.add_argument(
        "--output_file",
        type=str,
        nargs="?",
        default=None,
        help="The path to the output log file (legacy single file mode).",
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        nargs="?",
        default=None,
        help="The path to the output directory containing action_events.jsonl, probe_events.jsonl, and prompts_and_responses.jsonl.",
    )

    args = parser.parse_args()

    # Initialize variables
    serialized_initial_data = None

    if args.output_dir:
        # Load data from directory containing separate files

        directory_path = Path(args.output_dir)
        if not directory_path.exists():
            print(f"Error: Directory {directory_path} does not exist")
            exit(1)

        if not directory_path.is_dir():
            print(f"Error: {directory_path} is not a directory")
            exit(1)

        # Look for the required files in the directory
        folder_contents = {}
        required_files = ["action_events.jsonl", "probe_events.jsonl"]
        optional_files = ["prompts_and_responses.jsonl"]

        for file_pattern in required_files + optional_files:
            file_path = directory_path / file_pattern
            if file_path.exists():
                with open(file_path, encoding="utf-8") as f:
                    folder_contents[file_pattern] = f.read()
                print(f"Loaded {file_pattern}")
            elif file_pattern in required_files:
                print(f"Error: Required file {file_pattern} not found in {directory_path}")
                exit(1)

        # Load the data using the folder contents
        try:
            (
                follow_graph,
                interactions_by_episode,
                active_users_by_episode,
                toots,
                probe_data,
                plan_data,
                act_data,
            ) = load_data_from_folder(folder_contents)

            # Compute positions
            all_positions = compute_positions(follow_graph)
            layout = {"name": "preset", "positions": all_positions}

            # Serialize the initial data
            serialized_initial_data = serialize_data(
                follow_graph,
                interactions_by_episode,
                active_users_by_episode,
                toots,
                probe_data,
                plan_data,
                act_data,
            )

            # Add raw data and prompts content
            import io

            raw_data_combined = []

            for filename, content in folder_contents.items():
                if filename.endswith("action_events.jsonl") or filename.endswith(
                    "probe_events.jsonl"
                ):
                    df = pd.read_json(io.StringIO(content), lines=True)
                    raw_data_combined.extend(df.to_dict(orient="records"))

            serialized_initial_data["raw_data"] = raw_data_combined

            # Store prompts_and_responses content if available
            if "prompts_and_responses.jsonl" in folder_contents:
                serialized_initial_data["prompts_content"] = folder_contents[
                    "prompts_and_responses.jsonl"
                ]

            print(f"Successfully loaded data from directory: {directory_path}")

        except Exception as e:
            print(f"Error loading data from directory: {e}")
            exit(1)

    elif args.output_file:
        # Legacy mode: Load the data using single file
        (
            follow_graph,
            interactions_by_episode,
            active_users_by_episode,
            toots,
            probe_data,
            plan_data,
            act_data,
        ) = load_data(args.output_file)

        # Compute positions
        all_positions = compute_positions(follow_graph)
        layout = {"name": "preset", "positions": all_positions}

        # Serialize the initial data
        serialized_initial_data = serialize_data(
            follow_graph,
            interactions_by_episode,
            active_users_by_episode,
            toots,
            probe_data,
            plan_data,
            act_data,
        )
        print(f"Successfully loaded data from file: {args.output_file}")

    app = dash.Dash(__name__)

    # Define custom CSS for the entire app
    app.index_string = """
    <!DOCTYPE html>
    <html>
        <head>
            {%metas%}
            <title>{%title%}</title>
            {%favicon%}
            {%css%}
            <style>

                /* Reset browser defaults */
                html, body {
                    margin: 0;
                    padding: 0;
                    width: 100%;
                    height: 100%;
                    overflow-x: hidden;
                }
                /* Set default font for entire dashboard */
                * {
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    box-sizing: border-box;
                }

                /* Container styling for the entire app */
                .dashboard-container {
                    max-width: none;
                    margin: 0 auto;
                    padding: 10px;
                    width: 100vw;
                    background-color: #f9f9f9;
                }

                /* Header styling */
                .dashboard-header {
                    margin-bottom: 20px;
                    padding-bottom: 10px;
                    border-bottom: 1px solid #ddd;
                    width=100%;
                }

                /* Plot container styling */
                .plot-container {
                    background-color: white;
                    border: 1px solid #e0e0e0;
                    border-radius: 10px ; /*!important Reduced from 50px */
                    padding: 20px; /* !important Reduced from 150px */
                    margin-bottom: 20px;
                    box-shadow: 0px 2px 5px rgba(0, 0, 0, 0.05);
                    height: 400px; /* Increased from 350px */
                    width: 100%;
                    margin: 0 auto;
                }

                /* Plot title styling */
                .plot-title {
                    font-size: 16px;
                    font-weight: 500;
                    color: #333;
                    margin-bottom: 10px;
                    height: 20px; /* Fixed height for title */
                }

                /* Row styling for multiple plots */
                .plot-row {
                    display: flex;
                    flex-wrap: wrap;
                    gap: 20px;
                    width: 100%
                }

                /* Column styling for three equal columns */
                .plot-col-third {
                    flex: 1 1 calc(33.333% - 20px);
                    min-width: 250px;
                }

                /* Critical CSS to fix the plot width issues */
                .js-plotly-plot, .plot-container .js-plotly-plot, #graph-div {
                    width: 100%; /* !important;*/
                }

                .dash-graph {
                    height: 300px !important; /* Increased from 250px */
                    width: 100%; /*!important;*/
                }

                /* Fix for SVG elements inside plots */
                .main-svg, .svg-container {
                    width: 100%vw /*!important;*/
                }

                /* For the new cyto-div row */
                .cyto-div-row {
                    display: flex;
                    gap: 20px;
                    margin-bottom: 20px;
                    transition: all 0.3s ease;
                }

                /* Cytoscape container - will adapt based on if detail panel is shown */
                .cyto-container {
                    flex: 1;
                    min-height: 400px;
                    background-color: white;
                    border: 1px solid #e0e0e0;
                    border-radius: 5px;
                    padding: 0px;
                    box-shadow: 0px 2px 5px rgba(0, 0, 0, 0.05);
                }

                /* Detail panel container */
                .detail-container {
                    flex: 0 0 30%;  /* Fixed width when visible */
                    background-color: white;
                    border: 1px solid #e0e0e0;
                    border-radius: 5px;
                    padding: 15px;
                    box-shadow: 0px 2px 5px rgba(0, 0, 0, 0.05);
                    overflow: auto;
                }

            </style>
            <script>
                // Add folder upload functionality after DOM is loaded
                document.addEventListener('DOMContentLoaded', function() {
                    function enableFolderUpload(uploadId) {
                        setTimeout(function() {
                            const uploadElement = document.getElementById(uploadId);
                            if (uploadElement) {
                                const inputElement = uploadElement.querySelector('input[type="file"]');
                                if (inputElement) {
                                    inputElement.setAttribute('webkitdirectory', '');
                                    inputElement.setAttribute('directory', '');
                                    console.log('Folder upload enabled for', uploadId);
                                }
                            }
                        }, 1000); // Wait for Dash to render
                    }

                    enableFolderUpload('upload-app-logger');
                    enableFolderUpload('upload-app-logger-dashboard');

                    // Re-enable after any Dash updates
                    const observer = new MutationObserver(function(mutations) {
                        enableFolderUpload('upload-app-logger');
                        enableFolderUpload('upload-app-logger-dashboard');
                    });

                    observer.observe(document.body, { childList: true, subtree: true });
                });
            </script>
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
    # Create the layout with conditional sections
    app.layout = html.Div(
        className="dashboard-container",
        children=[
            # Title
            html.Div(
                className="dashboard-header",
                children=[html.H1(id="dashboard-title", children="Social Sandbox Dashboard")],
            ),
            html.Div(
                children=[
                    # Filename
                    html.Div(
                        children=[
                            html.H3(id="dashboard-showfilename"),
                        ],
                        style={
                            "display": "inline-block",
                            "float": "left",
                            "width": "80%",
                            "padding": "10px 0",
                            "white-space": "pre-wrap",
                            "word-wrap": "break-word",  # This is the proper way to handle word wrapping
                            "overflow-wrap": "break-word",  # Adding this for better browser compatibility
                        },
                    ),
                    # Upload components and Submit button
                    html.Div(
                        children=[
                            html.Div(
                                [
                                    html.Label(
                                        "Upload another output folder: ",
                                        style={
                                            "display": "inline-block",
                                            "marginRight": "10px",
                                            "verticalAlign": "middle",
                                            "fontWeight": "500",
                                            "white-space": "pre-wrap",
                                            "word-wrap": "break-word",  # This is the proper way to handle word wrapping
                                            "overflow-wrap": "break-word",  # Adding this for better browser compatibility
                                        },
                                    ),
                                    html.Div(
                                        [
                                            dcc.Upload(
                                                id="upload-app-logger-dashboard",
                                                children=html.Label(
                                                    html.Span(
                                                        "Select Folder",
                                                        style={
                                                            "fontWeight": "500",
                                                            "fontSize": "14px",
                                                            "fontFamily": "inherit",
                                                            "color": "#333",
                                                            "textDecoration": "none",
                                                        },
                                                    ),
                                                    style={
                                                        "display": "inline-block",
                                                        "borderWidth": "1px",
                                                        "borderStyle": "solid",
                                                        "borderColor": "#ccc",
                                                        "borderRadius": "4px",
                                                        "backgroundColor": "#f8f9fa",
                                                        "cursor": "pointer",
                                                        "marginRight": "10px",
                                                        "verticalAlign": "middle",
                                                        "boxShadow": "0 1px 2px rgba(0,0,0,0.05)",
                                                        "transition": "background-color 0.3s",
                                                        "color": "#333",
                                                        "padding": "8px 15px",
                                                    },
                                                ),
                                                style={
                                                    "display": "inline-block",
                                                },
                                                multiple=True,
                                            ),
                                        ],
                                    ),
                                    html.Button(
                                        "Upload Folder",
                                        id="upload-button-dashboard",
                                        n_clicks=0,
                                        style={
                                            "display": "inline-block",
                                            "verticalAlign": "middle",
                                            "padding": "8px 15px",
                                            "borderWidth": "1px",
                                            "borderStyle": "solid",
                                            "borderColor": "#ccc",
                                            "borderRadius": "4px",
                                            "backgroundColor": "#f8f9fa",
                                            "cursor": "pointer",
                                            "fontWeight": "500",
                                            "fontSize": "14px",
                                            "fontFamily": "inherit",
                                            "boxShadow": "0 1px 2px rgba(0,0,0,0.05)",
                                            "transition": "background-color 0.3s",
                                            "color": "#333",
                                        },
                                    ),
                                ],
                                style={
                                    "display": "flex",
                                    "alignItems": "center",
                                    "justifyContent": "flex-end",
                                    "flexWrap": "nowrap",
                                    "padding": "10px 0",
                                },
                            )
                        ],
                        style={
                            "display": "inline-block",
                            "float": "right",
                            "width": "20%",
                            "paddingRight": "5px",
                        },
                    ),
                ],
                id="dashboard-upload-section",
                style={
                    "width": "100%",
                    "overflow": "hidden",
                    "borderBottom": "1px solid #eee",
                    "marginBottom": "20px",
                    "paddingBottom": "5px",
                    "display": "flex"
                    if serialized_initial_data
                    else "none",  # Show if data already loaded
                },
            ),
            # Store component to hold serialized data
            dcc.Store(id="data-store", data=serialized_initial_data),
            # upload screen
            html.Div(
                className="dashboard-container",
                id="upload-screen",
                children=[
                    # Upload Output Log
                    html.Div(
                        [
                            html.Label(
                                "Upload an output folder (containing action_events.jsonl, probe_events.jsonl, and prompts_and_responses.jsonl):",
                                style={
                                    "font-size": "18px",
                                    "font-weight": "bold",
                                    "margin-bottom": "10px",
                                    "color": "#555555",
                                    "text-align": "center",
                                },
                            ),
                            html.Div(
                                [
                                    dcc.Upload(
                                        id="upload-app-logger",
                                        children=html.Label(
                                            [
                                                "Drag and Drop or ",
                                                html.A(
                                                    "Select Folder",
                                                    style={
                                                        "color": "#1a73e8",
                                                        "text-decoration": "underline",
                                                    },
                                                ),
                                            ],
                                            style={
                                                "width": "100%",
                                                "max-width": "400px",
                                                "height": "80px",
                                                "lineHeight": "80px",
                                                "borderWidth": "2px",
                                                "borderStyle": "dashed",
                                                "borderRadius": "10px",
                                                "textAlign": "center",
                                                "background-color": "#f9f9f9",
                                                "cursor": "pointer",
                                                "margin": "0 auto",
                                                "transition": "border 0.3s ease-in-out",
                                                "display": "block",
                                            },
                                        ),
                                        style={
                                            "width": "100%",
                                            "max-width": "400px",
                                        },
                                        multiple=True,
                                    ),
                                ],
                            ),
                        ],
                        style={"width": "100%", "max-width": "500px", "margin-bottom": "30px"},
                    ),
                    # Submit Button
                    html.Button(
                        "Submit",
                        id="submit-button",
                        n_clicks=0,
                        style={
                            "width": "200px",
                            "height": "50px",
                            "font-size": "18px",
                            "background-color": "#4CAF50",  # Green background
                            "color": "white",
                            "border": "none",
                            "border-radius": "8px",
                            "cursor": "pointer",
                            "box-shadow": "0 4px 6px rgba(0, 0, 0, 0.1)",
                            "transition": "background-color 0.3s ease, transform 0.2s ease",
                            "margin-bottom": "20px",
                            "align-self": "center",  # Center the button
                        },
                    ),
                    # Error Message
                    html.Div(
                        id="upload-error-message",
                        style={
                            "color": "red",
                            "textAlign": "center",
                            "margin-top": "20px",
                            "font-size": "16px",
                        },
                    ),
                ],
                style={
                    "background-color": "#f0f2f5",  # Light gray background for better contrast
                    "padding": "20px",
                    "display": "none"
                    if serialized_initial_data
                    else "flex",  # Hide if data already loaded
                },
            ),
            # Dashboard
            html.Div(
                id="dashboard",  # Added 'dashboard' id here
                # className='dashboard-container',
                children=[
                    # One row with three equal-width plots
                    html.Div(
                        className="plot-row",
                        children=[
                            # First plot
                            html.Div(
                                className="plot-col-third",
                                children=[
                                    html.Div(
                                        className="plot-container",
                                        children=[
                                            html.Div(
                                                className="plot-title",
                                                children="Vote Distribution Over Time",
                                            ),
                                            # probe data line graph
                                            dcc.Graph(
                                                className="dash-graph",
                                                id="probe-data-line",
                                                config={"displayModeBar": False},
                                                style={
                                                    "display": "inline-block",
                                                },
                                                responsive=True,
                                            ),
                                        ],
                                    )
                                ],
                            ),
                            # Second plot
                            html.Div(
                                className="plot-col-third",
                                children=[
                                    html.Div(
                                        className="plot-container",
                                        children=[
                                            html.Div(
                                                className="plot-title",
                                                children="Interactions Over Time",
                                            ),
                                            # Interactions count line graph
                                            dcc.Graph(
                                                id="interactions-line-graph",
                                                className="dash-graph",
                                                config={"displayModeBar": False},
                                                style={
                                                    "display": "inline-block",
                                                },
                                                responsive=True,
                                            ),
                                        ],
                                    )
                                ],
                            ),  # Third plot
                            html.Div(
                                className="plot-col-third",
                                children=[
                                    html.Div(
                                        className="plot-container",
                                        children=[
                                            html.Div(
                                                className="plot-title",
                                                children="Action Alignment Histogram",
                                            ),
                                            # Heatmap graph
                                            dcc.Graph(
                                                id="heatmap-graph",
                                                className="dash-graph",
                                                config={"displayModeBar": False},
                                                style={
                                                    "display": "inline-block",
                                                },
                                                responsive=True,
                                            ),
                                        ],
                                    )
                                ],
                            ),
                        ],
                    ),
                    html.Br(),
                    html.Br(),
                    # Main content: Cytoscape graph and interactions window
                    html.Div(
                        html.H2("Interaction Network"),
                        className="dashboard-container",
                        style={"padding": "0px"},
                    ),
                    html.Div(
                        className="cyto-div-row",
                        children=[
                            # Flex container for Cytoscape
                            html.Div(
                                className="cyto-container",
                                children=[
                                    html.Div(
                                        [
                                            dcc.Dropdown(
                                                id="name-selector",
                                                options=[],  # To be populated by callback
                                                value=None,
                                                placeholder="Select Name",
                                                clearable=True,
                                                style={
                                                    "padding": "10px",
                                                    "font-size": "16px",
                                                    "font-weight": "bold",
                                                    "width": "200px",
                                                    "z-index": "1000",  # Ensure it stays on top of the Cytoscape graph
                                                },
                                            ),
                                            # dcc.Dropdown(
                                            #     id="mode-dropdown",
                                            #     options=[
                                            #         {"label": "Universal View", "value": "normal"},
                                            #         {"label": "Active View", "value": "focused"},
                                            #     ],
                                            #     value="normal",
                                            #     clearable=False,
                                            #     style={
                                            #         "padding": "10px",
                                            #         "font-size": "16px",
                                            #         "font-weight": "bold",
                                            #         "width": "200px",
                                            #         "z-index": "1000",  # Ensure it stays on top of the Cytoscape graph
                                            #     },
                                            # ),
                                        ],
                                        # style={
                                        #     "position": "absolute",
                                        #     "top": "10px",  # Aligns at the top of the graph
                                        #     "left": "10px",  # Aligns on the left
                                        #     "display": "flex",
                                        #     "gap": "10px",  # Space between the two dropdowns
                                        #     "z-index": "1000",  # Ensure it's above the Cytoscape graph
                                        # },
                                    ),
                                    # Episode number display (top-right)
                                    html.Div(
                                        id="current-episode",
                                        style={
                                            # "float":"right"
                                            "position": "absolute",
                                            "top": "10px",
                                            "right": "10px",
                                            "padding": "10px",
                                            "font-size": "20px",
                                            "font-weight": "bold",
                                            "background-color": "#ffcc99",  # Optional: add a background color
                                            "z-index": "1000",  # Ensure it stays on top of the Cytoscape graph
                                        },
                                        children="",
                                    ),
                                    cyto.Cytoscape(
                                        id="cytoscape-graph",
                                        elements=[],  # To be populated by callback
                                        layout={
                                            "name": "preset",
                                            "positions": {},
                                        },  # To be updated by callback
                                        style={
                                            "width": "100%",  # Initial width set to 100%
                                            "height": "500px",
                                            "background-color": "#e1e1e1",
                                            "transition": "width 0.5s",  # Smooth width transition
                                        },
                                        stylesheet=[
                                            {
                                                "selector": ".default_node",
                                                "style": {
                                                    "background-color": "#fffca0",
                                                    "label": "data(label)",
                                                    "color": "#000000",
                                                    "font-size": "20px",
                                                    "text-halign": "center",
                                                    "text-valign": "center",
                                                    "width": "70px",
                                                    "height": "70px",
                                                    "border-width": 6,
                                                    "border-color": "#000000",
                                                },
                                            },
                                            {
                                                "selector": ".follow_edge",
                                                "style": {
                                                    "curve-style": "bezier",
                                                    "target-arrow-shape": "triangle",
                                                    "opacity": 0.8,
                                                    "width": 2,
                                                    "line-color": "#FFFFFF",
                                                },
                                            },
                                            {
                                                "selector": ".interaction_edge",
                                                "style": {
                                                    "curve-style": "bezier",
                                                    "target-arrow-shape": "triangle",
                                                    "opacity": 0.8,
                                                    "width": 4,
                                                    "line-color": "#000000",
                                                    "visibility": "hidden",
                                                },
                                            },
                                            {
                                                "selector": ".interaction_edge:hover",
                                                "style": {
                                                    "label": "data(label)",
                                                    "font-size": "14px",
                                                    "color": "#000000",
                                                },
                                            },
                                            # Edge labels
                                            {
                                                "selector": "edge",
                                                "style": {
                                                    "label": "data(label)",
                                                    "text-rotation": "autorotate",
                                                    "text-margin-y": "-10px",
                                                    "font-size": "10px",
                                                    "color": "#000000",
                                                    "text-background-color": "#FFFFFF",
                                                    "text-background-opacity": 0.8,
                                                    "text-background-padding": "3px",
                                                },
                                            },
                                            # Specific styles for custom nodes
                                            {
                                                "selector": f'[id="{custom_names[0]}"]',
                                                "style": {
                                                    "background-color": "blue",
                                                    "border-color": "#000000",
                                                },
                                            },
                                            {
                                                "selector": f'[id="{custom_names[1]}"]',
                                                "style": {
                                                    "background-color": "orange",
                                                    "border-color": "#000000",
                                                },
                                            },
                                            # Highlighted Nodes (Added)
                                            {
                                                "selector": ".highlighted",
                                                "style": {
                                                    "background-color": "#98FF98",  # Mint color
                                                    "border-color": "#FF69B4",  # Hot pink border for visibility
                                                    "border-width": 4,
                                                },
                                            },
                                        ],
                                    ),
                                ],
                            ),
                            # Flex container for Interactions Window
                            html.Div(
                                className="detail-container hidden",
                                id="detail-panel",
                                children=[
                                    html.H3("Platform Interactions"),
                                    html.Div(
                                        id="interactions-window",
                                        style={"overflowY": "auto", "height": "500px"},
                                    ),
                                ],
                                # style={
                                #     # "width": "0%",  # Initial width set to 0%
                                #     "height": "600px",
                                #     "padding": "0px",
                                #     "border-left": "1px solid #ccc",
                                #     "background-color": "#f9f9f9",
                                #     "transition": "width 0.5s",  # Smooth width transition
                                #     "overflow": "hidden",
                                #     "display": "none"
                                # },
                                # id="interactions-container",
                            ),
                            # style={
                            #     "display": "flex",
                            #     "flexDirection": "row",
                            #     "height": "600px",
                            #     "transition": "all 0.5s ease",  # Smooth transition for all properties
                            # },
                        ],
                        style={
                            "display": "flex",
                            "flexDirection": "row",
                            "height": "600px",
                            "transition": "all 0.5s ease",  # Smooth transition for all properties
                        },
                    ),
                    # style={
                    #     "position": "relative",
                    #     "height": "600px",
                    #     "margin-top": "10px",
                    #     "margin-bottom": "20px",
                    #     "width": "100%",
                    #     "margin": "auto",
                    # },
                    # ),
                    html.Label("Select Episode:"),
                    # Episode slider
                    dcc.Slider(
                        id="episode-slider",
                        min=0,  # To be updated by callback
                        max=0,  # To be updated by callback
                        value=0,  # To be updated by callback
                        marks={},  # To be updated by callback
                        step=None,
                        tooltip={"placement": "bottom", "always_visible": True},
                    ),
                    html.Hr(style={"margin": "20px 0"}),  # Separator
                    # Plan Viewer Section
                    html.Div(
                        [
                            html.H2(
                                "Agent Plans For This Episode",
                                style={"textAlign": "center"},
                            ),
                            html.Div(id="plan-output"),
                        ],
                        id="plan-viewer-section",
                        style={"padding": "20px"},
                    ),
                    html.Hr(style={"margin": "20px 0"}),  # Separator
                    # Thoughts Viewer Section
                    html.Div(
                        [
                            html.H2(
                                "Within-agent processing for selected agent and episode",
                                style={"textAlign": "center"},
                            ),
                            html.P(
                                "Data loaded from prompts_and_responses.jsonl in the uploaded folder.",
                                style={
                                    "textAlign": "center",
                                    "fontStyle": "italic",
                                    "color": "#666",
                                },
                            ),
                            html.Div(id="jsonl-output"),
                            dcc.Store(id="jsonl-store"),
                        ],
                        id="jsonl-viewer-section",
                        style={"padding": "20px"},
                    ),
                ],
                style={
                    "display": "block" if serialized_initial_data else "none"
                },  # Show if data already loaded
            ),
            # Hidden div for error messages (specific to dashboard uploads)
            html.Div(id="error-message", style={"color": "red"}),
        ],
    )

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
                    html.P("No prompts_and_responses.jsonl file found in the uploaded folder."),
                    html.P("This file is needed to display agent thoughts and processing details."),
                ]
            )

        prompts_content = data_store["prompts_content"]
        if not prompts_content:
            return html.Div([html.P("Prompts content is empty.")])

        print("Processing prompts data from folder")
        try:
            # Convert content to base64 format that stream_filtered_jsonl expects
            import base64

            encoded_content = base64.b64encode(prompts_content.encode("utf-8")).decode("utf-8")
            mock_contents = "data:text/plain;base64," + encoded_content

            # Stream and filter the data, collecting only matching records
            return [
                create_display(record)
                for record in stream_filtered_jsonl(mock_contents, selected_name, selected_episode)
            ]

        except Exception as e:
            print(f"Error processing JSONL: {e!s}")
            return html.Div([html.P(f"Error processing prompts data: {e!s}")])

    @app.callback(
        Output("plan-output", "children"),
        [
            Input("data-store", "data"),
            Input("episode-slider", "value"),
        ],
        prevent_initial_call=True,
    )
    def process_plan_data(data, selected_episode):
        """Process plan data with filtering."""
        if data is None:
            print("plan contents is None")
            return None
        if not data:
            print("PLan Contents is empty")
        print(data["act_data"].keys())
        plan_data = data["plan_data"][str(selected_episode)]
        act_data = data["act_data"][str(selected_episode)]
        try:
            # Stream and filter the data, collecting only matching records
            objs = []
            for entry in plan_data:
                act_agent_data = [
                    entry_act["data"]
                    for entry_act in act_data
                    if entry_act["source_user"] == entry["source_user"]
                ]
                objs.append(
                    create_display_plan(entry["data"], act_agent_data)
                )  # entry_acts[entry['source_user']]))
            return objs

        except Exception as e:
            print(f"Error processing plans: {e!s}")
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
        if data_store and "nodes" in data_store and len(data_store["nodes"]) > 0:
            # Data is available; show dashboard and hide upload screen
            return (
                {"display": "none"},
                {"display": "block"},
                {"display": "flex"},
                [{"label": name, "value": name} for name in sorted(data_store["nodes"])],
            )
        # No data; show upload screen and hide dashboard
        return {"display": "flex"}, {"display": "none"}, {"display": "none"}, []

    # Combined Callback for Initial and Dashboard Uploads
    @app.callback(
        [
            Output("dashboard-showfilename", "children"),
            Output("data-store", "data"),
            Output("upload-error-message", "children"),
            Output("error-message", "children"),
        ],
        [
            Input("submit-button", "n_clicks"),
            Input("upload-button-dashboard", "n_clicks"),
        ],
        [
            State("upload-app-logger", "contents"),
            State("upload-app-logger", "filename"),
            State("upload-app-logger-dashboard", "contents"),
            State("upload-app-logger-dashboard", "filename"),
            State("data-store", "data"),
        ],
    )
    def update_data(
        n_clicks_initial,
        n_clicks_dashboard,
        app_logger_contents_initial,
        app_logger_filename_initial,
        app_logger_contents_dashboard,
        app_logger_filename_dashboard,
        current_data,
    ):
        ctx = dash.callback_context

        if not ctx.triggered:
            raise dash.exceptions.PreventUpdate

        triggered_id = ctx.triggered[0]["prop_id"].split(".")[0]

        def process_folder_contents(contents_list, filename_list):
            """Process folder contents and return a dictionary of filename -> content"""
            if not contents_list or not filename_list:
                return None

            folder_contents = {}
            for content, filename in zip(contents_list, filename_list, strict=False):
                # Extract just the filename without path
                filename_only = filename.split("/")[-1]  # Handle both forward and back slashes
                if filename_only.endswith(".jsonl"):
                    # Decode the file content
                    content_type, content_string = content.split(",")
                    decoded = base64.b64decode(content_string)
                    content_str = decoded.decode("utf-8")
                    folder_contents[filename_only] = content_str

            return folder_contents

        dashboard_title_with_filename = "Folder:\n" + (
            app_logger_filename_initial[0] if app_logger_filename_initial else "Unknown"
        )
        try:
            if triggered_id == "submit-button":
                if app_logger_contents_initial is not None:
                    # Process folder contents
                    folder_contents = process_folder_contents(
                        app_logger_contents_initial, app_logger_filename_initial
                    )

                    if not folder_contents:
                        raise ValueError("No valid .jsonl files found in folder")

                    (
                        follow_graph_new,
                        interactions_by_episode_new,
                        active_users_by_episode_new,
                        toots_new,
                        probe_data_new,
                        plan_data_new,
                        act_data_new,
                    ) = load_data_from_folder(folder_contents)

                    # Serialize the new data
                    serialized_new_data = serialize_data(
                        follow_graph_new,
                        interactions_by_episode_new,
                        active_users_by_episode_new,
                        toots_new,
                        probe_data_new,
                        plan_data_new,
                        act_data_new,
                    )

                    # Combine raw data from action and probe events for heatmap
                    import io

                    raw_data_combined = []

                    for filename, content in folder_contents.items():
                        if filename.endswith("action_events.jsonl") or filename.endswith(
                            "probe_events.jsonl"
                        ):
                            df = pd.read_json(io.StringIO(content), lines=True)
                            raw_data_combined.extend(df.to_dict(orient="records"))

                    serialized_new_data["raw_data"] = raw_data_combined

                    # Store prompts_and_responses content if available
                    for filename, content in folder_contents.items():
                        if filename.endswith("prompts_and_responses.jsonl"):
                            serialized_new_data["prompts_content"] = content
                            break

                    return dashboard_title_with_filename, serialized_new_data, "", ""

                raise ValueError("Output folder required.")

            if triggered_id == "upload-button-dashboard":
                if app_logger_contents_dashboard is not None:
                    # Process folder contents
                    folder_contents = process_folder_contents(
                        app_logger_contents_dashboard, app_logger_filename_dashboard
                    )

                    if not folder_contents:
                        raise ValueError("No valid .jsonl files found in folder")

                    (
                        follow_graph_new,
                        interactions_by_episode_new,
                        active_users_by_episode_new,
                        toots_new,
                        probe_data_new,
                        plan_data_new,
                        act_data_new,
                    ) = load_data_from_folder(folder_contents)

                    # Serialize the new data
                    serialized_new_data = serialize_data(
                        follow_graph_new,
                        interactions_by_episode_new,
                        active_users_by_episode_new,
                        toots_new,
                        probe_data_new,
                        plan_data_new,
                        act_data_new,
                    )

                    # Combine raw data from action and probe events for heatmap
                    import io

                    raw_data_combined = []

                    for filename, content in folder_contents.items():
                        if filename.endswith("action_events.jsonl") or filename.endswith(
                            "probe_events.jsonl"
                        ):
                            df = pd.read_json(io.StringIO(content), lines=True)
                            raw_data_combined.extend(df.to_dict(orient="records"))

                    serialized_new_data["raw_data"] = raw_data_combined

                    # Store prompts_and_responses content if available
                    for filename, content in folder_contents.items():
                        if filename.endswith("prompts_and_responses.jsonl"):
                            serialized_new_data["prompts_content"] = content
                            break

                    dashboard_title_with_filename = (
                        "Social Sandbox Dashboard: " + app_logger_filename_dashboard[0]
                    )
                    return dashboard_title_with_filename, serialized_new_data, "", ""
                raise ValueError("Output folder required for dashboard upload.")

            raise dash.exceptions.PreventUpdate

        except Exception as e:
            if triggered_id == "submit-button":
                return (
                    dashboard_title_with_filename,
                    dash.no_update,
                    f"Error uploading initial data: {e!s}",
                    "",
                )
            if triggered_id == "upload-button-dashboard":
                return dash.no_update, dash.no_update, "", f"Error uploading dashboard data: {e!s}"
            return dashboard_title_with_filename, dash.no_update, "", ""

    @app.callback(Output("heatmap-graph", "figure"), Input("data-store", "data"))
    def update_heatmap(data_store):
        # If no data-store or no raw data is present, return a figure indicating so
        if not data_store or "raw_data" not in data_store:
            return go.Figure(data=[], layout=go.Layout(title="No data uploaded"))

        # Build a DataFrame from the raw uploaded records
        raw_data = data_store["raw_data"]
        df = pd.DataFrame(raw_data)

        # Filter for records with event_type "action" that have a non-null suggested_action.
        dft = df[
            (df["event_type"] == "action")
            & (df["label"] != "episode_plan")
            & (df["label"] != "inner_actions")
        ]
        # If the "data" column is stored as a string, parse it into a dict.
        if not df.empty and isinstance(df.iloc[0]["data"], str):
            dft["data"] = dft["data"].apply(lambda x: json.loads(x) if isinstance(x, str) else x)

        dft = dft[dft["data"].apply(lambda x: x.get("suggested_action") is not None)].copy()

        if dft.empty:
            return go.Figure(
                data=[], layout=go.Layout(title="No action records with a suggested_action found")
            )

        # If the suggested action equals "toot", convert it to "post"
        dft["suggested_action"] = dft["data"].apply(
            lambda x: "post" if x.get("suggested_action") == "toot" else x.get("suggested_action")
        )

        # Build a contingency table:
        # Rows: suggested_action, Columns: actual action taken (from the "label" column)
        contingency = pd.crosstab(dft["label"], dft["suggested_action"])

        # Create the heatmap figure using Plotly
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

    # Callback to update the dashboard based on data-store
    @app.callback(
        [
            Output("cytoscape-graph", "elements"),
            Output("cytoscape-graph", "layout"),
            Output("cytoscape-graph", "stylesheet"),
            Output("probe-data-line", "figure"),
            Output("interactions-line-graph", "figure"),
            Output("current-episode", "children"),
            Output("interactions-window", "children"),  # Added Output
            Output("detail-panel", "className"),
            # Output("interactions-container", "style"),  # Added Output to control width
            # Output("cytoscape-graph", "style"),  # Added Output to control width
            Output("episode-slider", "min"),
            Output("episode-slider", "max"),
            Output("episode-slider", "value"),
            Output("episode-slider", "marks"),
            Output("name-selector", "value"),
        ],
        [
            Input("episode-slider", "value"),
            # Input("mode-dropdown", "value"),
            Input("name-selector", "value"),  # Added Input
            Input("data-store", "data"),  # Added Input to trigger update on data change
        ],
    )
    def update_graph(selected_episode, selected_name, data_store):  # selected_mode,
        if not data_store:
            # If no data is present, return defaults
            return (
                [],  # elements
                {"name": "preset", "positions": {}},  # layout
                [],  # stylesheet
                {},  # probe data line
                {},  # interactions-line-graph
                "Episode: N/A",  # current-episode
                [],  # interactions-window
                "detail-container",  # hidden',
                0,  # slider min
                0,  # slider max
                0,  # slider value
                {},  # slider marks
                None,  # name-selector value
            )

        # Deserialize the data_store.
        (
            follow_graph,
            interactions_by_episode,
            active_users_by_episode,
            toots,
            probe_data,
            plan_data,
            act_data,
        ) = deserialize_data(data_store)

        # Compute positions based on the current follow_graph
        all_positions = compute_positions(follow_graph)
        layout = {"name": "preset", "positions": all_positions}

        # Build Cytoscape elements
        elements = [
            {
                "data": {"id": node, "label": node.split(" ")[0]},
                "classes": "default_node",
            }
            for node in follow_graph.nodes
        ] + [
            {
                "data": {
                    "source": src,
                    "target": tgt,
                },
                "classes": "follow_edge",
            }
            for src, tgt in follow_graph.edges
        ]

        # Add all interaction edges classified by the episode they belong to
        for episode, interactions in interactions_by_episode.items():
            for interaction in interactions:
                source = interaction["source"]
                target = interaction["target"]

                # Check if both source and target exist in the graph before creating the edge
                if source in follow_graph.nodes and target in follow_graph.nodes:
                    elements.append(
                        {
                            "data": {
                                "source": source,
                                "target": target,
                                "label": f"{interaction['action']}",
                            },
                            "classes": f"interaction_edge episode_{episode}",  # Classify edge by episode
                        }
                    )

        # Initialize the stylesheet
        stylesheet = [
            {
                "selector": ".default_node",
                "style": {
                    "background-color": "#fffca0",
                    "label": "data(label)",
                    "color": "#000000",
                    "font-size": "20px",
                    "text-halign": "center",
                    "text-valign": "center",
                    "width": "70px",
                    "height": "70px",
                    "border-width": 6,
                    "border-color": "#000000",
                },
            },
            {
                "selector": ".follow_edge",
                "style": {
                    "curve-style": "bezier",
                    "target-arrow-shape": "triangle",
                    "target-arrow-color": "#FFFFFF",
                    "opacity": 0.8,
                    "width": 2,
                    "line-color": "#FFFFFF",
                },
            },
            {
                "selector": ".interaction_edge",
                "style": {
                    "curve-style": "bezier",
                    "target-arrow-shape": "triangle",
                    "target-arrow-color": "#000000",
                    "opacity": 0.8,
                    "width": 4,
                    "line-color": "#000000",
                    "visibility": "hidden",
                },
            },
            {
                "selector": "edge",
                "style": {
                    "label": "data(label)",
                    "text-rotation": "autorotate",
                    "text-margin-y": "-10px",
                    "font-size": "10px",
                    "color": "#000000",
                    "text-background-color": "#FFFFFF",
                    "text-background-opacity": 0.8,
                    "text-background-padding": "3px",
                },
            },
            {
                "selector": ".interaction_edge:hover",
                "style": {
                    "label": "data(label)",
                    "font-size": "18px",
                    "color": "#000000",
                },
            },
            # Specific styles for custom nodes
            {
                "selector": f'[id="{custom_names[0]}"]',
                "style": {
                    "background-color": "blue",
                    "border-color": "#000000",
                },
            },
            {
                "selector": f'[id="{custom_names[1]}"]',
                "style": {
                    "background-color": "orange",
                    "border-color": "#000000",
                },
            },
            # Highlighted Nodes
            {
                "selector": ".highlighted",
                "style": {
                    "background-color": "#98FF98",  # Mint color
                    "border-color": "#FF69B4",  # Hot pink border for visibility
                    "border-width": 4,
                },
            },
        ]

        # define effects if a agent name is selected
        interactions_content = []

        interactions_window_classname = (
            "detail-container" if selected_name else "detail-container hidden"
        )
        print(interactions_window_classname)
        if selected_name:
            # Pullout and format the content of the interactions window

            # Get interactions where source is selected_name in selected_episode
            interactions = [
                interaction
                for interaction in interactions_by_episode.get(selected_episode, [])
                if interaction["source"] == selected_name
            ]

            if interactions:
                for interaction in interactions:
                    action = interaction["action"]
                    if action in ["liked", "boosted"]:
                        toot_id = interaction["toot_id"]
                        content = toots.get(toot_id, {}).get("content", "No content available.")
                        user = toots.get(toot_id, {}).get("user", "No user available.")
                        interactions_content.append(
                            html.Div(
                                [
                                    html.H4(
                                        f"{action.capitalize()} a toot (ID: {toot_id}) by {user}"
                                    ),
                                    html.P(content),
                                ],
                                style={
                                    "border": "1px solid #ccc",
                                    "padding": "10px",
                                    "margin-bottom": "10px",
                                },
                            )
                        )
                    elif action == "replied":
                        parent_toot_id = interaction.get("parent_toot_id")
                        reply_toot_id = interaction.get("toot_id")
                        parent_content = toots.get(parent_toot_id, {}).get(
                            "content", "No content available."
                        )
                        reply_content = toots.get(reply_toot_id, {}).get(
                            "content", "No content available."
                        )
                        user = toots.get(parent_toot_id, {}).get("user", "No user available.")
                        interactions_content.append(
                            html.Div(
                                [
                                    html.H4(f"Replied to toot (ID: {parent_toot_id}) by {user}"),
                                    html.P(parent_content),
                                    html.H5(f"Reply (ID: {reply_toot_id}):"),
                                    html.P(reply_content),
                                ],
                                style={
                                    "border": "1px solid #ccc",
                                    "padding": "10px",
                                    "margin-bottom": "10px",
                                },
                            )
                        )
                    elif action == "posted":
                        toot_id = interaction["toot_id"]
                        content = toots.get(toot_id, {}).get("content", "No content available.")
                        interactions_content.append(
                            html.Div(
                                [
                                    html.H4(f"Posted a toot (ID: {toot_id})"),
                                    html.P(content),
                                ],
                                style={
                                    "border": "1px solid #ccc",
                                    "padding": "10px",
                                    "margin-bottom": "10px",
                                },
                            )
                        )
            else:
                interactions_content.append(
                    html.P("No interactions found for this agent in the selected episode.")
                )

            # Effects on the graph
            # Highlight selected node and the nodes they follow
            # Find the nodes that the selected node follows (outgoing edges)
            follows = list(follow_graph.successors(selected_name))
            # Define the selector for the selected node and its followees
            if follows:
                highlight_selector = f'[id="{selected_name}"], ' + ", ".join(
                    [f'[id="{follow}"]' for follow in follows]
                )
            else:
                highlight_selector = f'[id="{selected_name}"]'

            # Apply the 'highlighted' class to the selected node and its followees
            stylesheet.append(
                {
                    "selector": highlight_selector,
                    "style": {
                        "background-color": "#98FF98",  # Mint color
                        "border-color": "#FF69B4",  # Hot pink border for visibility
                        "border-width": 4,
                    },
                }
            )
        else:
            interactions_content.append(html.P("Select an agent to view their interactions."))

        # if selected_name:
        #     interactions_style = {
        #         "width": "30%",  # Expanded width
        #         "height": "600px",
        #         "padding": "10px",
        #         "border-left": "1px solid #ccc",
        #         "background-color": "#f9f9f9",
        #         "transition": "width 0.5s",  # Smooth width transition
        #         "overflow": "auto",
        #     }
        #     cytoscape_style = {
        #         "width": "70%",  # Reduced width
        #         "height": "600px",
        #         "background-color": "#e1e1e1",
        #         "transition": "width 0.5s",  # Smooth width transition
        #     }
        # else:
        #     interactions_style = {
        #         "width": "0%",  # Collapsed width
        #         "height": "600px",
        #         "padding": "10px",
        #         "border-left": "1px solid #ccc",
        #         "background-color": "#f9f9f9",
        #         "transition": "width 0.5s",  # Smooth width transition
        #         "overflow": "hidden",
        #     }
        #     cytoscape_style = {
        #         "width": "100%",  # Full width
        #         "height": "600px",
        #         "background-color": "#e1e1e1",
        #         "transition": "width 0.5s",  # Smooth width transition
        #     }

        # Show interaction edges for the selected episode
        for episode in interactions_by_episode.keys():
            visibility = "visible" if episode == selected_episode else "hidden"
            stylesheet.append(
                {
                    "selector": f".episode_{episode}",
                    "style": {"visibility": visibility},
                }
            )

        # Update node border colors based on probe_data
        episode_probe_data = probe_data.get(selected_episode, {})
        total_probe_data = len(episode_probe_data)

        for node in follow_graph.nodes:
            if node in episode_probe_data:
                probe_datum = episode_probe_data[node]
                node_label = node if node in custom_names else "Other"
                stylesheet.append(
                    {
                        "selector": f'[id="{node}"]',
                        "style": {"border-color": node_colors[node_label]},
                    }
                )

        # Create the line graph showing probe_data over time

        probe_graphs_data, title_label, yaxis_label = probe_plot_preprocessing(probe_data)

        probe_data_line_fig = go.Figure()
        for graph_data in probe_graphs_data:
            probe_data_line_fig.add_trace(
                go.Scatter(
                    x=graph_data["data"]["x"],
                    y=graph_data["data"]["y"],
                    mode="lines+markers",
                    name=graph_data["label"],
                    line=dict(color=graph_data["color"]),
                    cliponaxis=False,
                )
            )

        max_episode = max(list(interactions_by_episode.keys()))
        probe_data_line_fig.update_layout(
            xaxis={
                "title": {"text": "Episode"},
                "range": [-1, max_episode + 1],
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

        # Create the line graph showing interactions over time
        interaction_types = ["liked", "boosted", "replied", "posted"]
        interactions_over_time = {interaction: [] for interaction in interaction_types}

        total_users = len(follow_graph.nodes) - 1

        active_user_fractions = []
        # int_episodes = list(active_users_by_episode.keys())
        int_episodes = sorted(interactions_by_episode.keys())
        for ep in int_episodes:
            num_active_users = len(active_users_by_episode[ep]) - 1  # dont count news agent

            counts = dict.fromkeys(interaction_types, 0)

            # Count interactions
            for interaction in interactions_by_episode.get(ep, []):
                action = interaction["action"]
                if action in counts:
                    counts[action] += 1

            # Append counts to the respective lists
            for interaction in interaction_types:
                if interaction == "posted":
                    interactions_over_time[interaction].append(
                        (counts[interaction] - 1) / num_active_users if num_active_users > 0 else 0
                    )
                else:
                    interactions_over_time[interaction].append(
                        (counts[interaction]) / num_active_users if num_active_users > 0 else 0
                    )

            # Calculate active user fraction
            active_user_fraction = num_active_users / total_users if total_users > 0 else 0
            active_user_fractions.append(active_user_fraction)

        interactions_line_fig = make_subplots(specs=[[{"secondary_y": True}]])

        # Add normalized interaction traces to the primary y-axis
        interactions_line_fig.add_trace(
            go.Scatter(
                x=int_episodes,
                y=interactions_over_time["liked"],
                mode="lines+markers",
                name="Likes",
                line=dict(color="#2ca02c"),  # Green
                marker=dict(symbol="circle", size=6),
                cliponaxis=False,
            ),
            secondary_y=False,
        )
        interactions_line_fig.add_trace(
            go.Scatter(
                x=int_episodes,
                y=interactions_over_time["boosted"],
                mode="lines+markers",
                name="Boosts",
                line=dict(color="#ff7f0e"),  # Orange
                marker=dict(symbol="square", size=6),
                cliponaxis=False,
            ),
            secondary_y=False,
        )
        interactions_line_fig.add_trace(
            go.Scatter(
                x=int_episodes,
                y=interactions_over_time["replied"],
                mode="lines+markers",
                name="Replies",
                line=dict(color="#9467bd"),  # Purple
                marker=dict(symbol="diamond", size=6),
                cliponaxis=False,
            ),
            secondary_y=False,
        )
        interactions_line_fig.add_trace(
            go.Scatter(
                x=int_episodes,
                y=interactions_over_time["posted"],
                mode="lines+markers",
                name="Posts",
                line=dict(color="#1f77b4"),  # Blue
                marker=dict(symbol="triangle-up", size=6),
                cliponaxis=False,
            ),
            secondary_y=False,
        )

        # Add active users fraction trace to the secondary y-axis
        interactions_line_fig.add_trace(
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

        # Define the y-axis range
        y_axis_range = [0, 1.5]

        # Update both y-axes
        interactions_line_fig.update_yaxes(
            title_text="Interactions/ Num. Agents",  # "Action Rate of Active Users",
            range=y_axis_range,
            secondary_y=False,
            showgrid=True,
            gridcolor="#f0f0f0",
        )

        interactions_line_fig.update_yaxes(
            title_text="Active User Fraction",
            range=[0, 1],
            secondary_y=True,
            showgrid=False,  # Typically, grid lines are only on the primary y-axis
            gridcolor="#f0f0f0",
        )

        interactions_line_fig.update_layout(
            # title={"text": "Interactions Over Time", "font": {"size": 14}},
            xaxis={
                "title": {"text": "Episode"},  # , "font": {"size": 10}},
                # "tickfont": {"size": 8},
                "range": [-1, max(int_episodes)],
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

        # Update the name-selector dropdown options
        unique_names = sorted(follow_graph.nodes)
        name_options = [{"label": name, "value": name} for name in unique_names]

        # Set episode slider properties
        slider_min = min(int_episodes)
        slider_max = max(int_episodes)
        slider_value = selected_episode if selected_episode in int_episodes else slider_min
        slider_marks = {str(ep): f"{ep}" for ep in sorted(int_episodes)}

        # Return all outputs, including the interactions window content and styles
        return (
            elements,  # Updated elements
            layout,
            stylesheet,
            probe_data_line_fig,
            interactions_line_fig,
            f"Episode: {selected_episode}",  # Updated episode display
            interactions_content,
            interactions_window_classname,
            slider_min,
            slider_max,
            slider_value,
            slider_marks,
            selected_name,
        )

    # Run the Dash app
    app.run_server(debug=False)
