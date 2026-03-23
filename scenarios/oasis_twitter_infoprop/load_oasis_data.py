#!/usr/bin/env python3
"""Load OASIS Twitter data and generate agent configurations.

This helper loads real Twitter agent profiles from the OASIS dataset
and generates YAML-compatible agent configurations for the simulation.
"""

import csv
import json
from pathlib import Path
from typing import Any


def load_csv_agents(csv_path: str) -> list[dict[str, Any]]:
    """Load agent profiles from OASIS CSV file.

    Args:
        csv_path: Path to CSV file with agent profiles

    Returns:
        List of agent configuration dictionaries
    """
    agents = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            agent = {
                'name': row.get('name', f"User_{row.get('user_id', 'unknown')}"),
                'username': row.get('username', row.get('name', 'unknown')),
                'description': row.get('description', ''),
                'user_char': row.get('user_char', ''),
                'realname': row.get('name', ''),
            }
            agents.append(agent)
    return agents


def generate_yaml_agents(csv_path: str, output_path: str | None = None) -> str:
    """Generate YAML agent list from OASIS CSV.

    Args:
        csv_path: Path to CSV file
        output_path: Optional path to write YAML file

    Returns:
        YAML string for agents list
    """
    agents = load_csv_agents(csv_path)

    yaml_lines = ['agents:']
    for agent in agents:
        yaml_lines.append(f'  - name: "{agent["name"]}"')
        yaml_lines.append(f'    realname: "{agent["realname"]}"')
        yaml_lines.append(f'    description: "{agent["description"]}"')
        yaml_lines.append('')

    yaml_str = '\n'.join(yaml_lines)

    if output_path:
        Path(output_path).write_text(yaml_str)
        print(f"Wrote {len(agents)} agents to {output_path}")

    return yaml_str


def get_agent_count(csv_path: str) -> int:
    """Get count of agents in CSV file."""
    with open(csv_path, 'r', encoding='utf-8') as f:
        return sum(1 for _ in csv.DictReader(f))


if __name__ == '__main__':
    # For Twitter InfoProp
    csv_path = '/scratch/ss14247/oasis/data/twitter_dataset/anonymous_topic_200_1h/False_Business_0.csv'
    count = get_agent_count(csv_path)
    print(f"Found {count} agents in {csv_path}")

    # Generate YAML (can be imported into config)
    yaml = generate_yaml_agents(csv_path)
    print(f"\nGenerated YAML for {len(yaml.split('- name'))-1} agents")
    print("Sample YAML:")
    print('\n'.join(yaml.split('\n')[:20]))
