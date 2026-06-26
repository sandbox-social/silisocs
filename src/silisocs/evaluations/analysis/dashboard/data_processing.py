"""Data processing utilities for the analysis dashboard."""

import base64
import json
from collections.abc import Generator
from io import StringIO
from pathlib import Path
from typing import Any

import networkx as nx
import pandas as pd

from silisocs.evaluations.action_events import resolve_action_event_files
from silisocs.evaluations.analysis.dashboard.config import (
    INTERACTION_TYPES,
    PAST_TENSE_MAP,
)


def _post_id(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    raw = data.get("post_id") or data.get("tweet_id") or data.get("toot_id")
    return None if raw is None else str(raw)


def _reply_target_id(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    raw = data.get("reply_to_id") or data.get("target_id")
    if raw is None and isinstance(data.get("reply_to"), dict):
        raw = data["reply_to"].get("post_id") or data["reply_to"].get("toot_id")
    return None if raw is None else str(raw)


def _normalize_action_data(data: Any) -> dict[str, Any]:
    normalized = dict(data or {}) if isinstance(data, dict) else {}
    post_id = _post_id(normalized)
    if post_id is not None:
        normalized["post_id"] = post_id
    reply_to_id = _reply_target_id(normalized)
    if reply_to_id is not None:
        normalized["reply_to_id"] = reply_to_id
    return normalized


def post_process_output(df):
    """Extract and process different event types from the main dataframe."""
    probe_df = df.loc[
        df.event_type == "probe", ["episode", "source_user", "label", "data"]
    ].reset_index(drop=True)
    if not probe_df.empty:
        probe_df["response"] = probe_df.data.apply(lambda x: x.get("probe_return"))
        probe_df = probe_df.drop("data", axis=1)

    edge_df = df.loc[
        df.label.isin(["follow", "unfollow"]), ["episode", "source_user", "data", "label"]
    ].reset_index(drop=True)
    edge_df["target_user"] = edge_df.data.apply(lambda d: d["target_user"])
    edge_df = edge_df.drop("data", axis=1)

    int_df = df.loc[df.label.isin(INTERACTION_TYPES), :].reset_index(drop=True)

    act_df = df.loc[df.event_type == "action", :].reset_index(drop=True)

    return probe_df, int_df, edge_df, act_df


def get_target_user(row, post_owner_dict):
    """Get target user from row, using post_owner_dict to look up users by post_id."""
    if row.label == "post":
        target_user = row.source_user
    elif row.label in ["like", "repost"]:
        target_post_id = _post_id(row.data)
        target_user = post_owner_dict.get(target_post_id)

    elif row.label == "reply":
        target_post_id = _reply_target_id(row.data)
        target_user = post_owner_dict.get(target_post_id)
    else:
        target_user = None
    return target_user


def get_post_dict(int_df):
    """Create post dictionary and owner lookup from interactions."""
    text_df = int_df.loc[(int_df.label == "post") | (int_df.label == "reply"), :].reset_index(
        drop=True
    )
    if text_df.empty:
        return {}, {}

    missing_post_id = text_df.data.apply(lambda x: _post_id(x) is None)
    text_df["missing_post_idx"] = -1
    text_df.loc[missing_post_id, "missing_post_idx"] = range(missing_post_id.sum())
    text_df.loc[missing_post_id, "data"] = text_df.loc[missing_post_id, :].apply(
        lambda x: x.data | {"post_id": "None" + str(x.missing_post_idx)}, axis=1
    )

    text_df["post_id"] = text_df.data.apply(_post_id)
    text_df = text_df.set_index("post_id")
    text_df["text_data"] = text_df.apply(
        lambda x: {
            "user": x.source_user,
            "action": PAST_TENSE_MAP[x.label],
            "content": x.data.get("post_text") or x.data.get("content") or x.data.get("text", ""),
        },
        axis=1,
    )
    text_df.text_data = text_df.apply(
        lambda x: (
            x.text_data | {"parent_post_id": _reply_target_id(x.data)}
            if x.label == "reply"
            else x.text_data
        ),
        axis=1,
    )

    post_owner_dict = text_df.apply(lambda x: x.source_user, axis=1).to_dict()

    return text_df.text_data.to_dict(), post_owner_dict


def get_int_dict(int_df, post_owner_dict):
    """Create interaction dictionary from interactions dataframe."""
    if int_df.empty:
        return {}
    int_df["int_data"] = int_df.apply(
        lambda x: {
            "action": PAST_TENSE_MAP[x.label],
            "episode": x.episode,
            "source": x.source_user,
            "target": get_target_user(x, post_owner_dict),
            "post_id": str(_post_id(x.data)),
        },
        axis=1,
    )
    int_df.int_data = int_df.apply(
        lambda x: (
            x.int_data | {"parent_post_id": str(_reply_target_id(x.data))}
            if x.label == "reply"
            else x.int_data
        ),
        axis=1,
    )
    return int_df.groupby("episode")["int_data"].apply(list).to_dict()


def get_act_dict(act_df):
    """Extract action data grouped by episode."""
    if act_df.empty:
        return {}
    data_dict = act_df.groupby("episode")[["source_user", "data"]].apply(
        lambda x: x.to_dict("records")
    )
    return data_dict.to_dict()


def get_probe_data(probe_df: pd.DataFrame) -> dict[int, dict[str, str]]:
    """Extract the first probe response per source user and episode."""
    if probe_df.empty or "response" not in probe_df:
        return {}
    probe_df = probe_df.loc[:, ["source_user", "response", "episode"]]
    probe_data: dict[int, dict[str, str]] = {}
    for row in probe_df.itertuples(index=False):
        probe_data.setdefault(int(row.episode), {})[str(row.source_user)] = row.response
    return probe_data


def load_data_from_folder(folder_contents):
    """Load data from folder containing separate JSONL files.

    Args:
        folder_contents: Dictionary with filename as key and file content as value

    Returns
    -------
        Tuple of (follow_graph, int_dict, active_users_by_episode, post_dict,
                  probe_data, act_dict)
    """
    # Extract the required files
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

    # Load dataframes
    action_df = pd.read_json(StringIO(action_content), lines=True)
    probe_df = (
        pd.read_json(StringIO(probe_content), lines=True)
        if probe_content
        else pd.DataFrame(columns=action_df.columns)
    )
    df = pd.concat([action_df, probe_df], ignore_index=True)

    df["data"] = df.data.apply(_normalize_action_data)
    df["episode"] = df.episode.apply(int)
    # Process dataframes
    probe_df_processed, int_df, edge_df, act_df = post_process_output(df)

    probe_data = get_probe_data(probe_df_processed)

    # Build follow network
    follow_graph = nx.from_pandas_edgelist(
        edge_df, "source_user", "target_user", create_using=nx.DiGraph()
    )

    # Get active users by episode
    active_users_by_episode = int_df.groupby("episode")["source_user"].apply(set).to_dict()

    # Get post and interaction data
    post_dict, post_owner_dict = get_post_dict(int_df.copy())
    int_dict = get_int_dict(int_df.copy(), post_owner_dict)

    # Get action data
    act_dict = get_act_dict(act_df.copy())
    return (
        follow_graph,
        int_dict,
        active_users_by_episode,
        post_dict,
        probe_data,
        act_dict | ({"prompts": prompts_content} if prompts_content else {}),
    )


def load_data_from_directory(directory_path: str | Path) -> dict[str, Any] | None:
    """Load serializable dashboard data from a Silisocs output directory."""
    directory = Path(directory_path)
    if not directory.exists() or not directory.is_dir():
        return None

    folder_contents = {}
    # Multi-GM runs isolate action logs under per-GM subdirectories; concatenate
    # them so the dashboard covers every game master, not just a flat root file.
    action_files = resolve_action_event_files(directory)
    if not action_files:
        return None
    folder_contents["action_events.jsonl"] = "\n".join(
        path.read_text(encoding="utf-8").strip() for path in action_files
    )
    for filename in ("probe_events.jsonl", "prompts_and_responses.jsonl"):
        path = directory / filename
        if path.exists():
            folder_contents[filename] = path.read_text(encoding="utf-8")

    (
        follow_graph,
        interactions_by_episode,
        active_users_by_episode,
        posts,
        probe_data,
        act_data,
    ) = load_data_from_folder(folder_contents)

    serialized_data = serialize_data(
        follow_graph,
        interactions_by_episode,
        active_users_by_episode,
        posts,
        probe_data,
        act_data,
    )

    raw_data = []
    for filename, content in folder_contents.items():
        if filename.endswith(("action_events.jsonl", "probe_events.jsonl")):
            raw_data.extend(pd.read_json(StringIO(content), lines=True).to_dict(orient="records"))
    serialized_data["raw_data"] = raw_data
    return serialized_data


def serialize_data(
    follow_graph,
    interactions_by_episode,
    active_users_by_episode,
    posts,
    probe_data,
    act_data,
):
    """Convert data structures into JSON-serializable format."""
    return {
        "nodes": list(follow_graph.nodes),
        "edges": list(follow_graph.edges),
        "interactions_by_episode": interactions_by_episode,
        "active_users_by_episode": {k: list(v) for k, v in active_users_by_episode.items()},
        "posts": posts,
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
    posts = serialized["posts"]
    probe_data = {int(k): v for k, v in serialized["probe_data"].items()}
    act_data = serialized["act_data"]

    return (
        follow_graph,
        interactions_by_episode,
        active_users_by_episode,
        posts,
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
