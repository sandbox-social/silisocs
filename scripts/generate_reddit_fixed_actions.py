#!/usr/bin/env python3
"""
Generate fixed_action_plan from OASIS Reddit data.

Loads RS-RC pairs from all_topics.json and generates PostAgent/RateAgent actions
matching OASIS align-with-human scenario exactly:
- 20 pairs per timestep
- PostAgent: create posts + comments sequentially
- RateAgent: rate comments based on voting group (up/down/control)
"""

import json
import sys
from pathlib import Path


def generate_reddit_fixed_actions(
    data_file: str,
    output_file: str,
    num_timesteps: int = 3,
    pairs_per_timestep: int = 2,
):
    """Generate high-fidelity fixed action plan from Reddit data."""

    # Load data
    with open(data_file) as f:
        pairs = json.load(f)

    print(f"Loaded {len(pairs)} pairs from {data_file}")

    post_agent_actions = []
    rate_agent_actions = []

    comment_counter = 0  # Track comment IDs across all pairs
    post_counter = 0     # Track post IDs

    for timestep in range(num_timesteps):
        print(f"\nTimestep {timestep}:")
        timestep_posts = []
        timestep_comments = []

        for pair_idx in range(pairs_per_timestep):
            pair_num = timestep * pairs_per_timestep + pair_idx
            if pair_num >= len(pairs):
                print(f"  Only {pair_num} pairs available, stopping")
                break

            pair = pairs[pair_num]
            rs = pair.get("RS", {})
            post_counter += 1

            # PostAgent creates the submission
            post_title = rs.get("title", "")[:100]  # Truncate for readability
            post_content = rs.get("selftext", "")[:200]
            post_text = f"{post_title}\n{post_content}" if post_content else post_title

            post_action = {
                "action_type": "create_reddit_post",
                "target_id": "",
                "content": post_text,
                "reasoning": f"OASIS data pair {pair_num}: Reddit submission from align-with-human",
                "episode": timestep,
            }
            post_agent_actions.append(post_action)
            timestep_posts.append(post_counter)

            # PostAgent creates comments from RC entries
            comment_counts = []
            for rc_key in sorted(pair.keys()):
                if not rc_key.startswith("RC_"):
                    continue

                rc = pair[rc_key]
                comment_counter += 1
                comment_body = rc.get("body", "")[:150]  # Truncate for readability
                group = rc.get("group", "control")

                comment_action = {
                    "action_type": "create_comment",
                    "target_id": str(post_counter),
                    "content": comment_body,
                    "reasoning": f"Comment from RC pair {pair_num}, voting group: {group}",
                    "episode": timestep,
                }
                post_agent_actions.append(comment_action)
                comment_counts.append((comment_counter, group))
                timestep_comments.append((comment_counter, group))

            # RateAgent rates comments based on their voting group (same episode)
            for comment_id, group in comment_counts:
                if group == "up":
                    rate_action = {
                        "action_type": "upvote_comment",
                        "target_id": str(comment_id),
                        "reasoning": f"OASIS voting: upvote for 'up' group",
                        "episode": timestep,
                    }
                    rate_agent_actions.append(rate_action)
                elif group == "down":
                    rate_action = {
                        "action_type": "downvote_comment",
                        "target_id": str(comment_id),
                        "reasoning": f"OASIS voting: downvote for 'down' group",
                        "episode": timestep,
                    }
                    rate_agent_actions.append(rate_action)
                # Skip "control" group ratings

        print(f"  Posts: {len(timestep_posts)}, Comments: {len(timestep_comments)}")

    print(f"\nGenerated {len(post_agent_actions)} PostAgent actions")
    print(f"Generated {len(rate_agent_actions)} RateAgent actions")

    # Write YAML compatible format
    with open(output_file, "w") as f:
        f.write("# Generated OASIS Reddit fixed action plan\n")
        f.write("# Auto-generated from all_topics.json\n\n")

        f.write("post_agent_fixed_action_plan:\n")
        for i, action in enumerate(post_agent_actions):
            f.write(f"  - action_type: {action['action_type']}\n")
            f.write(f"    target_id: \"{action['target_id']}\"\n")
            f.write(f"    content: |\n")
            for line in action['content'].split('\n'):
                f.write(f"      {line}\n")
            f.write(f"    reasoning: \"{action['reasoning']}\"\n")
            f.write(f"    episode: {action['episode']}\n\n")

        f.write("\n\nrate_agent_fixed_action_plan:\n")
        for action in rate_agent_actions:
            f.write(f"  - action_type: {action['action_type']}\n")
            f.write(f"    target_id: \"{action['target_id']}\"\n")
            f.write(f"    reasoning: \"{action['reasoning']}\"\n")
            f.write(f"    episode: {action['episode']}\n\n")

    print(f"\nConfig written to {output_file}")


if __name__ == "__main__":
    data_file = "/scratch/ss14247/mastodon-sim/scenarios/oasis_reddit_herding/data/all_topics.json"
    output_file = "/scratch/ss14247/mastodon-sim/scripts/reddit_fixed_actions.yaml"

    generate_reddit_fixed_actions(data_file, output_file, num_timesteps=3, pairs_per_timestep=2)
