"""UI component builders for the dashboard."""

from dash import html


def convert_linebreaks(string):
    """Convert newlines to HTML br elements."""
    lst = string.split("\n")
    item = html.Br()
    result = [item] * (len(lst) * 2 - 1)
    result[0::2] = lst
    return result


def create_display(entry):
    """Create HTML display for prompt/output entry."""
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


def create_display_plan(agent_name, entry_acts):
    """Create HTML display for agent actions entry."""
    return html.Details(
        [
            html.Summary(agent_name),
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


def create_interaction_display(interaction, posts):
    """Create HTML display for a single interaction."""
    action = interaction["action"]

    if action in ["liked", "reposted"]:
        post_id = interaction["post_id"]
        content = posts.get(post_id, {}).get("content", "No content available.")
        user = posts.get(post_id, {}).get("user", "No user available.")
        return html.Div(
            [
                html.H4(f"{action.capitalize()} a post (ID: {post_id}) by {user}"),
                html.P(content),
            ],
            style={
                "border": "1px solid #ccc",
                "padding": "10px",
                "margin-bottom": "10px",
            },
        )

    if action == "replied":
        parent_post_id = interaction.get("parent_post_id")
        reply_post_id = interaction.get("post_id")
        parent_content = posts.get(parent_post_id, {}).get("content", "No content available.")
        reply_content = posts.get(reply_post_id, {}).get("content", "No content available.")
        user = posts.get(parent_post_id, {}).get("user", "No user available.")
        return html.Div(
            [
                html.H4(f"Replied to post (ID: {parent_post_id}) by {user}"),
                html.P(parent_content),
                html.H5(f"Reply (ID: {reply_post_id}):"),
                html.P(reply_content),
            ],
            style={
                "border": "1px solid #ccc",
                "padding": "10px",
                "margin-bottom": "10px",
            },
        )

    if action == "posted":
        post_id = interaction["post_id"]
        content = posts.get(post_id, {}).get("content", "No content available.")
        return html.Div(
            [
                html.H4(f"Posted a post (ID: {post_id})"),
                html.P(content),
            ],
            style={
                "border": "1px solid #ccc",
                "padding": "10px",
                "margin-bottom": "10px",
            },
        )

    return html.Div()
