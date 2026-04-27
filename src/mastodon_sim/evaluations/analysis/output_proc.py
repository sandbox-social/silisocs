import json

import networkx as nx
import pandas as pd


def load_data(fileroot):
    with open(fileroot + ".json") as file:
        config_data = json.load(file)
    df = pd.read_json(fileroot + "_output.jsonl", lines=True)
    pd.set_option("display.width", 1000)
    print(df.head())
    print()
    print("Probes:")
    print(df.loc[df.event_type == "probe", "label"].value_counts())
    print()
    print("Actions:")
    print(df.loc[df.event_type == "action", "label"].value_counts())
    print()
    print(df.loc[df.event_type == "action", "data"].apply(lambda x: x.keys()).value_counts())
    return df


def post_process_output(df):
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

    # interaction_types = ["episode_plan"]
    # plan_df = df.loc[df.label.isin(interaction_types), :].reset_index(drop=True)

    interaction_types = ["inner_actions"]
    act_df = df.loc[df.label.isin(interaction_types), :].reset_index(drop=True)
    return probe_df, int_df, edge_df, act_df


def episodewise_graphbuild(edge_df):
    follow_graph = nx.DiGraph()
    for epi_edge_data in edge_df.groupby("episode"):
        for action, operate_on_graph in zip(
            ["follow", "unfollow"],
            [follow_graph.add_edges_from, follow_graph.remove_edges_from],
            strict=False,
        ):
            if (epi_edge_data.label == action).any():
                data = epi_edge_data.loc[
                    epi_edge_data.label == action, ["source_user", "target_user"]
                ]
                operate_on_graph(list(data.itertuples(index=False, name=None)))
    return follow_graph
