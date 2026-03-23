#!/usr/bin/env python3
"""Generate OASIS Reddit herding scenario with full 20-pair data per episode.

Creates multiple seed agents (NewsSource0, NewsSource1, ...) each responsible
for injecting specific posts + comments from OASIS data, matching the original
experiment's distributed authorship model.
"""

import json
from pathlib import Path


def generate_reddit_scenario_config(
    data_file: str,
    output_file: str,
    num_timesteps: int = 3,
    pairs_per_timestep: int = 20,
):
    """Generate complete OASIS Reddit scenario with ALL data."""

    # Load RS-RC pairs
    with open(data_file) as f:
        pairs = json.load(f)

    print(f"Loaded {len(pairs)} total pairs from {data_file}")
    print(f"Generating config for {num_timesteps} timesteps x {pairs_per_timestep} pairs")

    # Create one seed agent per pair (matching multiple "news sources" in practice)
    # This distributes authorship across many agents instead of all from "PostAgent"

    seed_agents = {}  # agent_name -> agent_config

    for timestep in range(num_timesteps):
        for pair_idx in range(pairs_per_timestep):
            pair_num = timestep * pairs_per_timestep + pair_idx
            if pair_num >= len(pairs):
                break

            pair = pairs[pair_num]
            rs = pair.get("RS", {})

            # Create unique agent name for this pair/timestep
            agent_name = f"NewsSource_{timestep}_{pair_idx}"

            # Build actions for this agent
            actions = []

            # 1. Create the post
            post_title = rs.get("title", "")[:80]
            post_content = rs.get("selftext", "")[:150]
            post_text = f"{post_title}\n{post_content}" if post_content else post_title

            actions.append({
                "action_type": "create_reddit_post",
                "target_id": "",
                "content": post_text,
                "reasoning": f"OASIS pair {pair_num}: {post_title[:40]}",
                "episode": timestep + 1,  # Episode 0 is initialization
            })

            # 2. Create comments
            comment_counter = 0
            for rc_key in sorted(pair.keys()):
                if not rc_key.startswith("RC_"):
                    continue

                rc = pair[rc_key]
                comment_counter += 1
                comment_body = rc.get("body", "")[:120]
                group = rc.get("group", "control")

                actions.append({
                    "action_type": "create_comment",
                    "target_id": str(pair_idx + 1),  # Reference to the post created here
                    "content": comment_body,
                    "reasoning": f"RC_{comment_counter} ({group}): {comment_body[:30]}",
                    "episode": timestep + 1,
                })

            # Store agent config
            seed_agents[agent_name] = {
                "count": 1,
                "prefab_module": "mastodon_sim.agents.fixed_entity",
                "params": {
                    "name": agent_name,
                    "context": f"Automated news source {pair_idx} for OASIS data injection",
                    "fixed_action_plan": actions,
                    "action_flow": "fixed_pre",
                },
            }

    # Write YAML-like output for inclusion in scenario config
    with open(output_file, "w") as f:
        f.write("# Auto-generated OASIS Reddit Herding Seed Agents\n")
        f.write("# Include this bajo persona_pipeline.classes in scenario config\n\n")

        for agent_name, config in seed_agents.items():
            f.write(f"{agent_name}:\n")
            f.write(f"  count: {config['count']}\n")
            f.write(f"  prefab_module: {config['prefab_module']}\n")
            f.write(f"  params:\n")
            f.write(f"    name: \"{config['params']['name']}\"\n")
            f.write(f"    context: \"{config['params']['context']}\"\n")
            f.write(f"    action_flow: {config['params']['action_flow']}\n")
            f.write(f"    fixed_action_plan:\n")

            for action in config['params']['fixed_action_plan']:
                f.write(f"      - action_type: {action['action_type']}\n")
                f.write(f"        target_id: \"{action['target_id']}\"\n")
                if action['content']:
                    f.write(f"        content: |\n")
                    for line in action['content'].split('\n'):
                        f.write(f"          {line}\n")
                f.write(f"        reasoning: \"{action['reasoning']}\"\n")
                f.write(f"        episode: {action['episode']}\n")
                f.write(f"\n")
            f.write("\n")

    print(f"\nGenerated {len(seed_agents)} seed agents")
    print(f"Config written to {output_file}")

    # Also write a summary
    return seed_agents


if __name__ == "__main__":
    data_file = "/scratch/ss14247/mastodon-sim/scenarios/oasis_reddit_herding/data/all_topics.json"
    output_file = "/scratch/ss14247/mastodon-sim/scenarios/oasis_reddit_herding/conf/scenario/seed_agents_generated.yaml"

    agents = generate_reddit_scenario_config(
        data_file,
        output_file,
        num_timesteps=3,
        pairs_per_timestep=20,
    )
    print(f"\nTotal agents created: {len(agents)}")
