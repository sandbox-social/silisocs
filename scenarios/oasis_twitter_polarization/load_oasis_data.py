"""Load OASIS Twitter polarization data and generate agent configurations.

This helper loads real Twitter agent profiles from the OASIS group polarization dataset
(conservative and progressive groups) and generates YAML-compatible agent configurations.
"""

import csv
from pathlib import Path
from typing import Any


def load_csv_agents(csv_path: str, group_name: str = "unknown") -> list[dict[str, Any]]:
    """Load agent profiles from OASIS CSV file.

    Args:
        csv_path: Path to CSV file with agent profiles
        group_name: Group name to add to each agent (e.g., "conservative", "progressive")

    Returns
    -------
        List of agent configuration dictionaries
    """
    agents = []
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            agent = {
                "name": row.get("name", f"User_{row.get('user_id', 'unknown')}"),
                "username": row.get("username", row.get("name", "unknown")),
                "description": row.get("description", ""),
                "user_char": row.get("user_char", ""),
                "realname": row.get("name", ""),
                "group": group_name,
            }
            agents.append(agent)
    return agents


def load_both_groups() -> list[dict[str, Any]]:
    """Load both conservative and progressive agent groups.

    Returns
    -------
        Combined list of agents from both groups
    """
    conservative_path = (
        "/scratch/ss14247/oasis/data/twitter_dataset/group_polarization/197_baoshou.csv"
    )
    progressive_path = (
        "/scratch/ss14247/oasis/data/twitter_dataset/group_polarization/197_progressive.csv"
    )

    agents = []
    agents.extend(load_csv_agents(conservative_path, group_name="conservative"))
    agents.extend(load_csv_agents(progressive_path, group_name="progressive"))

    return agents


def generate_yaml_agents(output_path: str | None = None) -> str:
    """Generate YAML agent list from OASIS polarization CSVs.

    Args:
        output_path: Optional path to write YAML file

    Returns
    -------
        YAML string for agents list
    """
    agents = load_both_groups()

    yaml_lines = ["agents:"]
    for agent in agents:
        yaml_lines.append(f'  - name: "{agent["name"]}" # {agent["group"]}')
        yaml_lines.append(f'    realname: "{agent["realname"]}"')
        yaml_lines.append(f'    description: "{agent["description"]}"')
        yaml_lines.append("")

    yaml_str = "\n".join(yaml_lines)

    if output_path:
        Path(output_path).write_text(yaml_str)
        print(f"Wrote {len(agents)} agents to {output_path}")

    return yaml_str


def get_agent_counts() -> dict[str, int]:
    """Get count of agents in each polarization CSV file."""
    conservative_path = (
        "/scratch/ss14247/oasis/data/twitter_dataset/group_polarization/197_baoshou.csv"
    )
    progressive_path = (
        "/scratch/ss14247/oasis/data/twitter_dataset/group_polarization/197_progressive.csv"
    )

    counts = {}
    with open(conservative_path, encoding="utf-8") as f:
        counts["conservative"] = sum(1 for _ in csv.DictReader(f))

    with open(progressive_path, encoding="utf-8") as f:
        counts["progressive"] = sum(1 for _ in csv.DictReader(f))

    return counts


if __name__ == "__main__":
    counts = get_agent_counts()
    total = sum(counts.values())
    print(f"Found {counts['conservative']} conservative agents")
    print(f"Found {counts['progressive']} progressive agents")
    print(f"Total: {total} agents")

    # Generate YAML (can be imported into config)
    yaml = generate_yaml_agents()
    print(f"\nGenerated YAML for {len(yaml.split('- name')) - 1} agents")
    print("Sample YAML:")
    print("\n".join(yaml.split("\n")[:25]))
