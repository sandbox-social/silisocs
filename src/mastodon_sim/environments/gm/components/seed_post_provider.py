"""Configurable seed post providers for agent initialization.

Supports multiple strategies:
- LLM-generated: Use language model to generate initial posts for each agent
- CSV/JSON-based: Load real seed posts from a CSV or JSON file (like OASIS does)
- Disabled: No seed posts (agents grow organically from zero)
"""

from __future__ import annotations

import abc
import csv
import json
import logging
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from concordia.agents import entity_agent_with_logging

from mastodon_sim.evaluations.probes.agent_speech import write_seed_toot

_LOGGER = logging.getLogger(__name__)


class SeedPostProvider(abc.ABC):
    """Base class for seed post generation strategies."""

    @abc.abstractmethod
    def get_seed_posts(
        self,
        entities: Sequence[entity_agent_with_logging.EntityAgentWithLogging],
    ) -> dict[str, str]:
        """Get seed posts for all entities.

        Args:
            entities: List of agent entities to generate/load seed posts for.

        Returns
        -------
            Dict mapping agent name -> seed post text.
        """
        ...


class LLMSeedPostProvider(SeedPostProvider):
    """Generate seed posts using LLM calls (default Mastodon-Sim behavior)."""

    def __init__(self, max_workers: int = 64):
        """Initialize LLM-based seed post generator.

        Args:
            max_workers: Maximum concurrent LLM threads.
        """
        self.max_workers = max_workers

    def get_seed_posts(
        self,
        entities: Sequence[entity_agent_with_logging.EntityAgentWithLogging],
    ) -> dict[str, str]:
        """Generate seed posts for agents that don't have them.

        Agents with existing seed_post attributes are kept as-is.
        Missing posts are generated via LLM calls in parallel.
        """
        seed_posts: dict[str, str] = {}
        llm_seed_agents = []

        # Collect existing seed posts
        for agent in entities:
            name = agent._agent_name
            if hasattr(agent, "seed_post") and agent.seed_post:
                seed_posts[name] = agent.seed_post
            else:
                llm_seed_agents.append(agent)

        # Generate missing ones via LLM
        if llm_seed_agents:
            workers = min(len(llm_seed_agents), self.max_workers)
            with ThreadPoolExecutor(max_workers=workers) as executor:
                future_to_name = {
                    executor.submit(write_seed_toot, a): a._agent_name for a in llm_seed_agents
                }
                for future in as_completed(future_to_name):
                    name = future_to_name[future]
                    try:
                        seed_posts[name] = future.result()
                    except Exception:
                        _LOGGER.exception("LLM seed post generation failed for %s", name)
                        seed_posts[name] = ""

        return seed_posts


class CSVSeedPostProvider(SeedPostProvider):
    """Load seed posts from CSV or JSON file.

    Supports both formats for agent seed posts:

    CSV format (with headers):
        username, previous_tweets, ...
        user0,   "['tweet 1', 'tweet 2']", ...
        user1,   "[]", ...

    JSON format (agent_name -> posts):
        {
          "user0": "tweet 1",
          "user1": "tweet 2",
          ...
        }

    File type is auto-detected by extension (.csv or .json).
    """

    def __init__(self, file_path: str):
        """Initialize file-based seed post loader.

        Args:
            file_path: Path to CSV or JSON file containing agent seed posts.
        """
        self.file_path = file_path

    def get_seed_posts(
        self,
        entities: Sequence[entity_agent_with_logging.EntityAgentWithLogging],
    ) -> dict[str, str]:
        """Load seed posts from CSV or JSON file.

        Maps agent names to their seed posts.
        Agents without entries get empty seed posts (organically grow).
        """
        path = Path(self.file_path)

        if path.suffix.lower() == ".json":
            return self._load_json(entities)
        # Default to CSV for .csv or unknown extensions
        return self._load_csv(entities)

    def _load_json(
        self,
        entities: Sequence[entity_agent_with_logging.EntityAgentWithLogging],
    ) -> dict[str, str]:
        """Load seed posts from JSON file.

        JSON should be a dict: {agent_name: post_text, ...}
        """
        seed_posts: dict[str, str] = {}
        agent_names = {agent._agent_name for agent in entities}

        try:
            with open(self.file_path, encoding="utf-8") as f:
                data = json.load(f)

            if not isinstance(data, dict):
                _LOGGER.warning(
                    "JSON %s root must be an object. Agents will have no seed posts.",
                    self.file_path,
                )
                return dict.fromkeys(agent_names, "")

            for agent_name in agent_names:
                post = data.get(agent_name, "")
                seed_posts[agent_name] = str(post).strip() if post else ""

        except FileNotFoundError:
            _LOGGER.error("Seed posts JSON not found: %s", self.file_path)
            seed_posts = dict.fromkeys(agent_names, "")
        except json.JSONDecodeError as e:
            _LOGGER.exception("Error parsing JSON %s: %s", self.file_path, e)
            seed_posts = dict.fromkeys(agent_names, "")
        except Exception as e:
            _LOGGER.exception("Error loading seed posts from JSON %s: %s", self.file_path, e)
            seed_posts = dict.fromkeys(agent_names, "")

        # Ensure all agents have an entry
        for name in agent_names:
            if name not in seed_posts:
                seed_posts[name] = ""

        return seed_posts

    def _load_csv(
        self,
        entities: Sequence[entity_agent_with_logging.EntityAgentWithLogging],
    ) -> dict[str, str]:
        """Load seed posts from CSV file."""
        import ast

        seed_posts: dict[str, str] = {}
        agent_names = {agent._agent_name for agent in entities}

        try:
            with open(self.file_path, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                if reader.fieldnames is None or "previous_tweets" not in reader.fieldnames:
                    _LOGGER.warning(
                        "CSV %s missing 'previous_tweets' column. Agents will have no seed posts.",
                        self.file_path,
                    )
                    return dict.fromkeys(agent_names, "")

                for row in reader:
                    # Match by username or name column to agent names
                    username = row.get("username", "").strip()
                    name_column = row.get("name", "").strip()

                    # Try to match: agent name could be "username" or "name" from CSV
                    agent_name = None
                    for entity in entities:
                        agent_n = entity._agent_name
                        if agent_n == username or agent_n == name_column:
                            agent_name = agent_n
                            break

                    if agent_name is None:
                        # Skip rows that don't match any agent
                        continue

                    # Parse previous_tweets list
                    previous_tweets_str = row.get("previous_tweets", "[]").strip()
                    try:
                        previous_tweets = ast.literal_eval(previous_tweets_str)
                        if isinstance(previous_tweets, list) and previous_tweets:
                            # Join multiple tweets with newlines, use first one if single
                            seed_posts[agent_name] = (
                                previous_tweets[0]
                                if len(previous_tweets) == 1
                                else "\n".join(previous_tweets)
                            )
                        else:
                            seed_posts[agent_name] = ""
                    except (ValueError, SyntaxError):
                        _LOGGER.warning(
                            "Failed to parse previous_tweets for %s: %s",
                            agent_name,
                            previous_tweets_str,
                        )
                        seed_posts[agent_name] = ""
        except FileNotFoundError:
            _LOGGER.error("Seed posts CSV not found: %s", self.file_path)
            seed_posts = dict.fromkeys(agent_names, "")
        except Exception as e:
            _LOGGER.exception("Error loading seed posts from CSV %s: %s", self.file_path, e)
            seed_posts = dict.fromkeys(agent_names, "")

        # Ensure all agents have an entry
        for name in agent_names:
            if name not in seed_posts:
                seed_posts[name] = ""

        return seed_posts


class FallbackSeedPostProvider(SeedPostProvider):
    """Hybrid provider: try CSV/JSON first, fallback to LLM generation.

    Useful for scenarios where some agents have real data (file) but others need
    LLM-generated content.
    """

    def __init__(self, file_path: str | None = None, llm_fallback: bool = True):
        """Initialize hybrid seed post provider.

        Args:
            file_path: Optional path to CSV/JSON with real seed posts.
            llm_fallback: If True, use LLM for agents without file entries.
        """
        self.file_provider = CSVSeedPostProvider(file_path) if file_path else None
        self.llm_provider = LLMSeedPostProvider() if llm_fallback else None

    def get_seed_posts(
        self,
        entities: Sequence[entity_agent_with_logging.EntityAgentWithLogging],
    ) -> dict[str, str]:
        """Load from file first, fallback to LLM for missing entries."""
        seed_posts: dict[str, str] = {}

        # Load from file first
        if self.file_provider:
            seed_posts = self.file_provider.get_seed_posts(entities)

        # Identify agents needing LLM generation
        missing_agents = [
            agent for agent in entities if not seed_posts.get(agent._agent_name, "").strip()
        ]

        # Generate missing ones via LLM
        if missing_agents and self.llm_provider:
            llm_posts = self.llm_provider.get_seed_posts(missing_agents)
            seed_posts.update(llm_posts)

        return seed_posts


class DisabledSeedPostProvider(SeedPostProvider):
    """Disabled provider: no seed posts (organic growth from zero).

    Agents start with empty timelines and grow naturally from interactions.
    """

    def get_seed_posts(
        self,
        entities: Sequence[entity_agent_with_logging.EntityAgentWithLogging],
    ) -> dict[str, str]:
        """Return empty seed posts for all agents."""
        return {agent._agent_name: "" for agent in entities}
