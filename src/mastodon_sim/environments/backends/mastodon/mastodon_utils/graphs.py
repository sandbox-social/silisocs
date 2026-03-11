"""Module to create and display a graph of user relationships."""

import html
import os

import networkx as nx
from IPython.display import HTML, display
from pyvis.network import Network

from mastodon_sim.environments.backends.mastodon.logging_config import logger
from mastodon_sim.environments.backends.mastodon.mastodon_ops.get_client import get_client
from mastodon_sim.environments.backends.mastodon.mastodon_ops.login import login
from mastodon_sim.environments.backends.mastodon.mastodon_utils.get_users_from_env import (
    get_users_from_env,
)


def get_network_html_code(nt: Network) -> str:
    """Get the HTML code for the network graph."""
    fname = "temp_file.html"
    nt.save_graph(fname)
    with open(fname) as f:
        html_content = f.read()
    os.remove(fname)
    return html_content


def create_user_graph(users: list[str] | None = None) -> None:
    """
    Create and display a graph of user relationships.

    Tested in VSCode Jupyter Notebooks.

    Args:
        users (List[str]): List of usernames to include in the graph.

    Returns
    -------
        None (displays the graph inline in a Jupyter notebook)
    """
    if users is None:
        users = get_users_from_env()
        logger.info(f"Found {len(users)} users from environment variables.")

    try:
        # Create a directed graph
        G = nx.DiGraph()  # noqa: N806

        # Add nodes (users) to the graph
        G.add_nodes_from(users)

        # Dictionary to store follower counts
        follower_counts = dict.fromkeys(users, 0)

        # Add edges (follow relationships) to the graph
        for user in users:
            try:
                # Login and get Mastodon client
                access_token = login(user)
                mastodon = get_client()
                mastodon.access_token = access_token

                # Get user's followers
                followers = mastodon.account_followers(mastodon.account_lookup(user)["id"])
                for follower in followers:
                    if follower["acct"] in users:
                        G.add_edge(follower["acct"], user)
                        follower_counts[user] += 1
            except Exception as e:
                logger.warning(f"Error fetching followers for {user}: {e}")

        # Create a pyvis network
        net = Network(
            notebook=True,
            directed=True,
            height="600px",
            width="100%",
            bgcolor="#222222",
            font_color="white",
            cdn_resources="remote",
        )

        # Add nodes and edges to the pyvis network
        for node in G.nodes():
            net.add_node(
                node,
                label=node,
                title=node,
                size=follower_counts[node] * 10 + 10,
                color="dodgerblue",
            )

        for edge in G.edges():
            net.add_edge(edge[0], edge[1], color="gray")

        # Set options for better visualization
        net.set_options("""
        var options = {
          "nodes": {
            "shape": "dot",
            "font": {
              "size": 16,
              "color": "#ffffff",
              "face": "Arial"
            }
          },
          "edges": {
            "arrows": {
              "to": {
                "enabled": true
              }
            },
            "color": {
              "color": "gray",
              "highlight": "red"
            }
          },
          "physics": {
            "enabled": true,
            "barnesHut": {
              "gravitationalConstant": -8000,
              "springLength": 250
            }
          }
        }
        """)

        # Generate HTML content
        html_content = get_network_html_code(net)

        # Create iframe with responsive height
        iframe = f"""
        <iframe srcdoc="{html.escape(html_content)}" width="100%" height="600px" style="border:none;">
        </iframe>
        """

        # Add script to make iframe height responsive
        responsive_iframe = f"""
        <div id="graph-container" style="width:100%; height:600px;">
            {iframe}
        </div>
        <script>
            function resizeIframe() {{
                var container = document.getElementById('graph-container');
                var iframe = container.getElementsByTagName('iframe')[0];
                iframe.style.height = container.offsetHeight + 'px';
            }}
            window.addEventListener('resize', resizeIframe);
            resizeIframe();
        </script>
        """

        # Display the graph
        display(HTML(responsive_iframe))

        logger.info("Graph created and displayed successfully.")

    except Exception as e:
        logger.error(f"An error occurred while creating the graph: {e}")
        raise
