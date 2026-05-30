"""Cytoscape stylesheet generation for the dashboard."""


def get_base_stylesheet():
    """Get the base stylesheet for Cytoscape."""
    return [
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
            "selector": ".interaction_edge:hover",
            "style": {
                "label": "data(label)",
                "font-size": "18px",
                "color": "#000000",
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
            "selector": ".highlighted",
            "style": {
                "background-color": "#98FF98",
                "border-color": "#FF69B4",
                "border-width": 4,
            },
        },
    ]


def build_stylesheet(
    follow_graph, selected_episode, interactions_by_episode, probe_data, selected_name=None
):
    """Build complete stylesheet based on current state."""
    stylesheet = get_base_stylesheet()

    # Show interaction edges for the selected episode
    for episode in interactions_by_episode.keys():
        visibility = "visible" if episode == selected_episode else "hidden"
        stylesheet.append(
            {
                "selector": f".episode_{episode}",
                "style": {"visibility": visibility},
            }
        )

    # Mark nodes with probe responses in the selected episode.
    episode_probe_data = probe_data.get(selected_episode, {})
    for node in follow_graph.nodes:
        if node in episode_probe_data:
            stylesheet.append(
                {
                    "selector": f'[id="{node}"]',
                    "style": {"border-color": "#1f77b4"},
                }
            )

    # Highlight selected node and its followees
    if selected_name:
        follows = list(follow_graph.successors(selected_name))
        if follows:
            highlight_selector = f'[id="{selected_name}"], ' + ", ".join(
                [f'[id="{follow}"]' for follow in follows]
            )
        else:
            highlight_selector = f'[id="{selected_name}"]'

        stylesheet.append(
            {
                "selector": highlight_selector,
                "style": {
                    "background-color": "#98FF98",
                    "border-color": "#FF69B4",
                    "border-width": 4,
                },
            }
        )

    return stylesheet


def build_cytoscape_elements(follow_graph, interactions_by_episode):
    """Build Cytoscape elements (nodes and edges)."""
    # Create nodes
    elements = [
        {
            "data": {"id": node, "label": node.split(" ")[0]},
            "classes": "default_node",
        }
        for node in follow_graph.nodes
    ]

    # Add follow edges
    elements.extend(
        [
            {
                "data": {"source": src, "target": tgt},
                "classes": "follow_edge",
            }
            for src, tgt in follow_graph.edges
        ]
    )

    # Add interaction edges
    for episode, interactions in interactions_by_episode.items():
        for interaction in interactions:
            source = interaction["source"]
            target = interaction["target"]

            if source in follow_graph.nodes and target in follow_graph.nodes:
                elements.append(
                    {
                        "data": {
                            "source": source,
                            "target": target,
                            "label": f"{interaction['action']}",
                        },
                        "classes": f"interaction_edge episode_{episode}",
                    }
                )

    return elements
