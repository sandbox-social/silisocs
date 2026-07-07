"""Seed-post providers and typed action mapping for simulation initialization."""

from __future__ import annotations

import abc
import csv
import functools
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from silisocs.agents.base_agent import Agent
from silisocs.initialization.context import InitializationContext
from silisocs.runtime.execution import concurrency
from silisocs.runtime.types import ActionOutput, ActionSpec, OutputType, ToolCall


class SeedPostProvider(abc.ABC):
    """Base class for seed-post content providers."""

    @abc.abstractmethod
    def get_seed_posts(
        self,
        *,
        agents: Sequence[Agent],
        context: InitializationContext,
    ) -> dict[str, str]:
        """Return seed text by agent name."""
        ...


class AgentSeedPostProvider(SeedPostProvider):
    """Ask agents to generate their own seed posts through ``act``."""

    def __init__(self, prompt: str = "Write one realistic first-person seed post.") -> None:
        self.prompt = str(prompt or "Write one realistic first-person seed post.")

    def get_seed_posts(
        self,
        *,
        agents: Sequence[Agent],
        context: InitializationContext,
    ) -> dict[str, str]:
        del context
        result: dict[str, str] = {}
        spec = ActionSpec(prompt=self.prompt, output_type=OutputType.TEXT, tag="seed_post")
        pending: dict[str, Callable[[], Any]] = {}
        for agent in agents:
            existing = str(getattr(agent, "seed_post", "") or "").strip()
            if existing:
                result[agent.name] = existing
                continue
            pending[agent.name] = functools.partial(agent.act, spec)
        # One LLM round-trip per agent — run them on the shared bounded pool
        # instead of serially (a serial loop pays O(N) sequential latency at init).
        for name, output in concurrency.run_tasks(pending).items():
            result[name] = str(output).strip()
        return result


class FileSeedPostProvider(SeedPostProvider):
    """Load seed posts from a configured CSV or JSON file."""

    def __init__(self, file_path: str, file_type: str | None = None) -> None:
        self.file_path = str(file_path or "").strip()
        if not self.file_path:
            raise ValueError("Seed-post file provider requires params.file_path.")
        self.file_type = str(file_type or Path(self.file_path).suffix.lstrip(".") or "csv").lower()

    def get_seed_posts(
        self,
        *,
        agents: Sequence[Agent],
        context: InitializationContext,
    ) -> dict[str, str]:
        del context
        path = Path(self.file_path)
        if not path.is_file():
            raise FileNotFoundError(f"Seed-post file not found: {path}")
        if self.file_type == "json":
            return self._load_json(path, agents)
        if self.file_type == "csv":
            return self._load_csv(path, agents)
        raise ValueError(f"Unsupported seed-post file_type: {self.file_type}")

    def _load_json(self, path: Path, agents: Sequence[Agent]) -> dict[str, str]:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(
                f"Seed-post JSON must be an object mapping agent names to text: {path}"
            )
        names = {agent.name for agent in agents}
        return {name: str(data.get(name, "") or "").strip() for name in names}

    def _load_csv(self, path: Path, agents: Sequence[Agent]) -> dict[str, str]:
        names = {agent.name for agent in agents}
        result: dict[str, str] = dict.fromkeys(names, "")
        with path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                raise ValueError(f"Seed-post CSV has no header row: {path}")
            if "previous_tweets" not in reader.fieldnames and "seed_post" not in reader.fieldnames:
                raise ValueError(
                    f"Seed-post CSV must include previous_tweets or seed_post column: {path}"
                )
            for row in reader:
                name = str(row.get("name") or row.get("username") or "").strip()
                if name not in names:
                    continue
                result[name] = _seed_text_from_row(row)
        return result


class FallbackSeedPostProvider(SeedPostProvider):
    """Use file seed posts first, then ask agents for missing seed posts."""

    def __init__(self, file_path: str, file_type: str | None = None, prompt: str = "") -> None:
        self.file_provider = FileSeedPostProvider(file_path, file_type=file_type)
        self.agent_provider = (
            AgentSeedPostProvider(prompt=prompt) if prompt else AgentSeedPostProvider()
        )

    def get_seed_posts(
        self,
        *,
        agents: Sequence[Agent],
        context: InitializationContext,
    ) -> dict[str, str]:
        result = self.file_provider.get_seed_posts(agents=agents, context=context)
        missing = [agent for agent in agents if not result.get(agent.name, "").strip()]
        if missing:
            result.update(self.agent_provider.get_seed_posts(agents=missing, context=context))
        return result


class DisabledSeedPostProvider(SeedPostProvider):
    """Seed-post provider that intentionally returns no posts."""

    def get_seed_posts(
        self,
        *,
        agents: Sequence[Agent],
        context: InitializationContext,
    ) -> dict[str, str]:
        del context
        return {agent.name: "" for agent in agents}


def build_seed_post_provider(slot_cfg: Mapping[str, Any] | None = None) -> SeedPostProvider:
    """Build a seed-post provider from simulation initializer params."""
    cfg = dict(slot_cfg or {})
    provider_type = str(cfg.get("type") or "agent").strip().lower()
    params = dict(cfg.get("params") or {})
    if provider_type == "none":
        return DisabledSeedPostProvider()
    if provider_type == "agent":
        return AgentSeedPostProvider(prompt=str(params.get("prompt") or ""))
    if provider_type in {"csv", "json"}:
        return FileSeedPostProvider(
            str(params.get("file_path") or ""),
            file_type=provider_type,
        )
    if provider_type == "fallback":
        return FallbackSeedPostProvider(
            file_path=str(params.get("file_path") or ""),
            file_type=params.get("file_type"),
            prompt=str(params.get("prompt") or ""),
        )
    raise ValueError(
        f"Unknown seed-post provider type '{provider_type}'. "
        "Available: agent, csv, fallback, json, none"
    )


def build_seed_post_action(
    *,
    backend_type: str,
    agent_name: str,
    post_text: str,
    context: InitializationContext,
    mapping_overrides: Mapping[str, Any] | None = None,
    default_subreddit: str = "general",
) -> ActionOutput:
    """Map seed text to a typed backend action payload."""
    text = str(post_text or "").strip()
    if not text:
        return ActionOutput.skip()
    mapping = _seed_mapping(backend_type, context, mapping_overrides)
    payload = {
        str(key): str(value).format(
            agent_name=agent_name,
            post_text=text,
            title=text[:100],
            default_subreddit=str(default_subreddit or "general"),
        )
        for key, value in dict(mapping.get("arguments", {}) or {}).items()
    }
    return ActionOutput.from_tool_calls([ToolCall(str(mapping["tool_name"]), payload)])


def _seed_mapping(
    backend_type: str,
    context: InitializationContext,
    overrides: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    normalized = str(backend_type or "").strip().lower()
    defaults: dict[str, Mapping[str, Any]] = {
        "twitter_like": {
            "tool_name": "create_tweet",
            "arguments": {"status": "{post_text}"},
        },
        "mastodon": {
            "tool_name": "post_toot",
            "arguments": {"status": "{post_text}"},
        },
        "reddit_like": {
            "tool_name": "create_reddit_post",
            "arguments": {
                "subreddit": "{default_subreddit}",
                "title": "{title}",
                "content": "{post_text}",
            },
        },
    }
    del context
    merged = dict(defaults)
    for key, value in dict(overrides or {}).items():
        if not isinstance(value, Mapping):
            raise ValueError(f"Seed-post action mapping for {key!r} must be a mapping.")
        merged[str(key)] = dict(value)
    if normalized not in merged:
        raise ValueError(
            f"No seed-post action mapping configured for backend_type '{backend_type}'."
        )
    mapping = merged[normalized]
    if not mapping.get("tool_name"):
        raise ValueError(f"Seed-post action mapping for {backend_type!r} is missing tool_name.")
    return mapping


def _seed_text_from_row(row: Mapping[str, str]) -> str:
    direct = str(row.get("seed_post") or "").strip()
    if direct:
        return direct
    previous = str(row.get("previous_tweets") or "").strip()
    if not previous:
        return ""
    if previous.startswith("["):
        import ast

        parsed = ast.literal_eval(previous)
        if isinstance(parsed, list):
            return str(parsed[0]).strip() if parsed else ""
    return previous
