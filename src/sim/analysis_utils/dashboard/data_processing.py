"""Data processing utilities for the Social Sandbox Dashboard."""

import base64
import json
from collections.abc import Generator
from io import StringIO
from typing import Any

import networkx as nx
import pandas as pd
from config import PAST_TENSE_MAP, PROBE_LABEL


def post_process_output(df):
    """Extract and process different event types from the main dataframe."""
    probe_df = df.loc[
        df.event_type == "probe", ["episode", "source_user", "label", "data"]
    ].reset_index(drop=True)
    probe_df["response"] = probe_df.data.apply(lambda x: x["query_return"])
    probe_df = probe_df.drop("data", axis=1)

    edge_df = df.loc[
        df.label.isin(["follow", "unfollow"]), ["episode", "source_user", "data", "label"]
    ].reset_index(drop=True)
    edge_df["target_user"] = edge_df.data.apply(lambda d: d["target_user"])
    edge_df = edge_df.drop("data", axis=1)

    interaction_types = ["post", "like_toot", "boost_toot", "reply"]
    int_df = df.loc[df.label.isin(interaction_types), :].reset_index(drop=True)

    act_df = df.loc[df.label == "inner_actions", :].reset_index(drop=True)

    return probe_df, int_df, edge_df, act_df


def get_target_user(row, toot_owner_dict):
    """Get target user from row, using toot_owner_dict to look up users by toot_id."""
    if row.label == "post":
        target_user = row.source_user
    elif row.label in ["like_toot", "boost_toot"]:
        target_toot_id = row.data["target_toot_id"]
        target_user = toot_owner_dict.get(target_toot_id)
    elif row.label == "reply":
        target_toot_id = row.data["reply_to"]["toot_id"]
        target_user = toot_owner_dict.get(target_toot_id)
    else:
        target_user = None
    return target_user


def get_toot_dict(int_df):
    """Create toot dictionary from interactions dataframe.
    Also returns toot_owner_dict for use in get_int_dict.
    """
    text_df = int_df.loc[(int_df.label == "post") | (int_df.label == "reply"), :].reset_index(
        drop=True
    )

    # Handle Nones as toot_ids by appending an index
    no_toot_id = text_df.data.apply(lambda x: x["toot_id"] is None)
    text_df["no_toot_id_idx"] = -1
    text_df.loc[no_toot_id, "no_toot_id_idx"] = range(no_toot_id.sum())
    text_df.loc[no_toot_id, "data"] = text_df.loc[no_toot_id, :].apply(
        lambda x: x.data | {"toot_id": "None" + str(x.no_toot_id_idx)}, axis=1
    )

    text_df["toot_id"] = text_df.data.apply(lambda x: x["toot_id"])
    text_df = text_df.set_index("toot_id")
    text_df["text_data"] = text_df.apply(
        lambda x: {
            "user": x.source_user,
            "action": PAST_TENSE_MAP[x.label],
            "content": x.data["post_text"],
        },
        axis=1,
    )
    text_df.text_data = text_df.apply(
        lambda x: x.text_data | {"parent_toot_id": x.data["reply_to"]["toot_id"]}
        if x.label == "reply"
        else x.text_data,
        axis=1,
    )

    # Create toot_owner_dict mapping toot_id -> user
    toot_owner_dict = text_df.apply(lambda x: x.source_user, axis=1).to_dict()

    return text_df.text_data.to_dict(), toot_owner_dict


def get_int_dict(int_df, toot_owner_dict):
    """Create interaction dictionary from interactions dataframe."""
    int_df["int_data"] = int_df.apply(
        lambda x: {
            "action": PAST_TENSE_MAP[x.label],
            "episode": x.episode,
            "source": x.source_user,
            "target": get_target_user(x, toot_owner_dict),
            "toot_id": str(x.data["toot_id"]),
        },
        axis=1,
    )
    int_df.int_data = int_df.apply(
        lambda x: x.int_data | {"parent_toot_id": str(x.data["reply_to"]["toot_id"])}
        if x.label == "reply"
        else x.int_data,
        axis=1,
    )
    return int_df.groupby("episode")["int_data"].apply(list).to_dict()


def get_act_dict(act_df):
    """Extract action data grouped by episode."""
    data_dict = act_df.groupby("episode")[["source_user", "data"]].apply(
        lambda x: x.to_dict("records")
    )
    return data_dict.to_dict()


def load_data_from_folder(folder_contents):
    """Load data from folder containing separate JSONL files.

    Args:
        folder_contents: Dictionary with filename as key and file content as value

    Returns
    -------
        Tuple of (follow_graph, int_dict, active_users_by_episode, toot_dict,
                  probe_data, act_dict)
    """
    # Extract the required files
    action_content = None
    probe_content = None

    for filename, content in folder_contents.items():
        if filename.endswith("mastodon_action_events.jsonl"):
            action_content = content
        elif filename.endswith("probe_events.jsonl"):
            probe_content = content

    if not action_content:
        raise ValueError("mastodon action_events.jsonl file not found in folder")
    if not probe_content:
        raise ValueError("probe_events.jsonl file not found in folder")

    # Load dataframes
    action_df = pd.read_json(StringIO(action_content), lines=True)
    probe_df = pd.read_json(StringIO(probe_content), lines=True)
    df = pd.concat([action_df, probe_df], ignore_index=True)

    # Ensure all toot_ids are strings
    def get_toot_id(data):
        if "toot_id" in data:
            data["toot_id"] = str(data["toot_id"])
        return data

    df["data"] = df.data.apply(get_toot_id)

    # Process dataframes
    probe_df_processed, int_df, edge_df, act_df = post_process_output(df)

    # Extract probe data
    num_entries = len(probe_df_processed.loc[probe_df_processed.label == PROBE_LABEL])
    print(f"{num_entries} probe entries!")

    probe_data = (
        probe_df_processed.loc[
            probe_df_processed.label == PROBE_LABEL, ["source_user", "response", "episode"]
        ]
        .groupby("episode")
        .apply(lambda x: dict(zip(x.source_user, x.response, strict=False)))
        .to_dict()
    )

    # Build follow network
    follow_graph = nx.from_pandas_edgelist(
        edge_df, "source_user", "target_user", create_using=nx.DiGraph()
    )

    # Get active users by episode
    active_users_by_episode = int_df.groupby("episode")["source_user"].apply(set).to_dict()

    # Get toot and interaction data
    toot_dict, toot_owner_dict = get_toot_dict(int_df.copy())
    int_dict = get_int_dict(int_df.copy(), toot_owner_dict)

    # Get action data
    act_dict = get_act_dict(act_df.copy())

    return (
        follow_graph,
        int_dict,
        active_users_by_episode,
        toot_dict,
        probe_data,
        act_dict,
    )


def serialize_data(
    follow_graph,
    interactions_by_episode,
    active_users_by_episode,
    toots,
    probe_data,
    act_data,
):
    """Convert data structures into JSON-serializable format."""
    return {
        "nodes": list(follow_graph.nodes),
        "edges": list(follow_graph.edges),
        "interactions_by_episode": interactions_by_episode,
        "active_users_by_episode": {k: list(v) for k, v in active_users_by_episode.items()},
        "toots": toots,
        "probe_data": probe_data,
        "act_data": act_data,
    }


def deserialize_data(serialized):
    """Convert JSON-serializable data back into original structures."""
    follow_graph = nx.DiGraph()
    follow_graph.add_nodes_from(serialized["nodes"])
    follow_graph.add_edges_from(serialized["edges"])

    interactions_by_episode = {int(k): v for k, v in serialized["interactions_by_episode"].items()}
    active_users_by_episode = {
        int(k): set(v) for k, v in serialized["active_users_by_episode"].items()
    }
    toots = serialized["toots"]
    probe_data = {k: v for k, v in serialized["probe_data"].items()}
    act_data = serialized["act_data"]

    return (
        follow_graph,
        interactions_by_episode,
        active_users_by_episode,
        toots,
        probe_data,
        act_data,
    )


def stream_filtered_jsonl(
    content_string: str, selected_name: str, selected_episode: int
) -> Generator[dict[Any, Any], None, None]:
    """Stream and filter JSONL content line by line, only yielding matching records."""
    if "," in content_string:
        _, content_string = content_string.split(",", 1)

    if not content_string:
        print("content string is empty")
        return

    decoded = base64.b64decode(content_string).decode("utf-8")
    stream = StringIO(decoded)

    for line in stream:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
            if (selected_name is None or record.get("agent_name") == selected_name) and (
                selected_episode is None or record.get("episode_idx") == selected_episode
            ):
                yield record
        except json.JSONDecodeError as e:
            print(f"Error processing JSONL line: {e!s}")
            continue
