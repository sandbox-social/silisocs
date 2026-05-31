"""Environment app for the EchoChamberSim replication."""

from __future__ import annotations

import dataclasses
import json
import random
import threading
from collections.abc import Sequence
from pathlib import Path
from statistics import mean
from typing import Any

from silisocs.environments.backends.twitter_like.app import TwitterLikeApp

BELIEF_VALUES = {-2, -1, 0, 1, 2}
OBSERVATION_START = "ECHO_CHAMBER_OBSERVATION_JSON"
OBSERVATION_END = "END_ECHO_CHAMBER_OBSERVATION_JSON"


def _read_json(path: str | Path) -> Any:
    with Path(path).open(encoding="utf-8") as f:
        return json.load(f)


def _write_jsonl(path: Path, item: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=True) + "\n")


def _safe_pearson(xs: list[float], ys: list[float]) -> float:
    if len(xs) != len(ys) or len(xs) < 2:
        return 0.0
    x_mean = mean(xs)
    y_mean = mean(ys)
    num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys, strict=False))
    x_den = sum((x - x_mean) ** 2 for x in xs)
    y_den = sum((y - y_mean) ** 2 for y in ys)
    if x_den <= 0 or y_den <= 0:
        return 0.0
    return num / ((x_den * y_den) ** 0.5)


@dataclasses.dataclass
class EchoChamberWorld:
    """Runtime world state for one echo-chamber replication run."""

    output_dir: Path
    agent_records_path: Path
    network_path: Path
    belief_keywords_path: Path
    opinions_path: Path
    mitigation_perspectives_path: Path | None = None
    topic_key: str = "euthanasia"
    seed: int = 50
    leaders: tuple[int, ...] = (10, 30)
    similarity_threshold: int = 2
    max_interactions: int = -1
    recommendation: str = "similarity"
    mitigation_step: int = 1000
    mitigation_perspectives_only: bool = False
    with_long_memory: bool = True
    expected_updates_per_agent_per_episode: int = 1

    lock: threading.RLock = dataclasses.field(default_factory=threading.RLock)
    rng: random.Random = dataclasses.field(init=False)
    records: list[dict[str, Any]] = dataclasses.field(init=False)
    graph_edges: list[tuple[int, int]] = dataclasses.field(init=False)
    neighbors: dict[int, list[int]] = dataclasses.field(init=False)
    node_by_name: dict[str, int] = dataclasses.field(init=False)
    name_by_node: dict[int, str] = dataclasses.field(init=False)
    belief_keywords: dict[str, list[str]] = dataclasses.field(init=False)
    topic: str = dataclasses.field(init=False)
    mitigation_perspectives: list[str] = dataclasses.field(init=False, default_factory=list)
    current_beliefs: dict[str, int] = dataclasses.field(init=False)
    current_opinions: dict[str, str] = dataclasses.field(init=False)
    current_reasonings: dict[str, str] = dataclasses.field(init=False)
    long_memory: dict[str, str] = dataclasses.field(init=False)
    short_memory_full: dict[str, list[str]] = dataclasses.field(init=False)
    long_memory_full: dict[str, list[str]] = dataclasses.field(init=False)
    beliefs_history: dict[str, list[int]] = dataclasses.field(init=False)
    opinions_history: dict[str, list[str]] = dataclasses.field(init=False)
    reasonings_history: dict[str, list[str]] = dataclasses.field(init=False)
    contact_ids_history: dict[str, list[list[int]]] = dataclasses.field(init=False)
    active_episode: int | None = None
    snapshot_beliefs: dict[str, int] = dataclasses.field(default_factory=dict)
    snapshot_opinions: dict[str, str] = dataclasses.field(default_factory=dict)
    interactions_by_episode: dict[int, dict[str, dict[str, Any]]] = dataclasses.field(
        default_factory=dict
    )
    staged_by_episode: dict[int, dict[str, dict[str, Any]]] = dataclasses.field(
        default_factory=dict
    )
    update_counts_by_episode: dict[int, dict[str, int]] = dataclasses.field(default_factory=dict)
    committed_episodes: set[int] = dataclasses.field(default_factory=set)

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)
        self.records = list(_read_json(self.agent_records_path))
        network = _read_json(self.network_path)
        self.graph_edges = [(int(u), int(v)) for u, v in network.get("edges", [])]
        nodes = [int(n) for n in network.get("nodes", range(len(self.records)))]
        self.neighbors = {node: [] for node in nodes}
        for left, right in self.graph_edges:
            self.neighbors.setdefault(left, []).append(right)
            self.neighbors.setdefault(right, []).append(left)
        for values in self.neighbors.values():
            values.sort()

        self.belief_keywords = dict(_read_json(self.belief_keywords_path))
        opinions = _read_json(self.opinions_path)
        self.topic = str(opinions.get(self.topic_key, self.topic_key))
        if self.mitigation_perspectives_path and self.mitigation_perspectives_path.exists():
            payload = _read_json(self.mitigation_perspectives_path)
            self.mitigation_perspectives = [str(v) for v in payload.get("perspectives", [])]

        self.node_by_name = {}
        self.name_by_node = {}
        self.current_beliefs = {}
        self.current_opinions = {}
        self.current_reasonings = {}
        self.long_memory = {}
        self.short_memory_full = {}
        self.long_memory_full = {}
        self.beliefs_history = {}
        self.opinions_history = {}
        self.reasonings_history = {}
        self.contact_ids_history = {}

        for record in self.records:
            node = int(record["agent_id"])
            name = str(record["name"])
            belief = int(record.get("initial_belief", 0))
            if belief not in BELIEF_VALUES:
                belief = max(-2, min(2, belief))
            opinion = str(record.get("initial_opinion", ""))
            reasoning = str(record.get("initial_reasoning", ""))
            self.node_by_name[name] = node
            self.name_by_node[node] = name
            self.current_beliefs[name] = belief
            self.current_opinions[name] = opinion
            self.current_reasonings[name] = reasoning
            self.long_memory[name] = ""
            self.short_memory_full[name] = []
            self.long_memory_full[name] = [""]
            self.beliefs_history[name] = [belief]
            self.opinions_history[name] = [opinion]
            self.reasonings_history[name] = [reasoning]
            self.contact_ids_history[name] = []

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._write_metrics(step=0, episode=-1)
        self._write_agents_data()
        self._write_network_copy()

    @property
    def agent_names(self) -> list[str]:
        return [str(record["name"]) for record in self.records]

    def _write_network_copy(self) -> None:
        payload = {
            "nodes": sorted(self.name_by_node),
            "node_ids": sorted(self.neighbors),
            "edges": self.graph_edges,
            "name_by_node": self.name_by_node,
        }
        (self.output_dir / "echo_network.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=True),
            encoding="utf-8",
        )

    def _write_agents_data(self) -> None:
        data: dict[str, Any] = {}
        for name in self.agent_names:
            node = self.node_by_name[name]
            data[str(node)] = {
                "name": name,
                "opinions": self.opinions_history[name],
                "beliefs": self.beliefs_history[name],
                "reasonings": self.reasonings_history[name],
                "short-memory": self.short_memory_full[name],
                "long_memory": self.long_memory_full[name],
                "contact_ids": self.contact_ids_history[name],
            }
        (self.output_dir / "echo_agents_data.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=True),
            encoding="utf-8",
        )

    def _metric_payload(self) -> dict[str, float]:
        by_node = {self.node_by_name[name]: belief for name, belief in self.current_beliefs.items()}
        values = [float(by_node[node]) for node in sorted(by_node)]
        avg = mean(values) if values else 0.0
        belief_variance = mean([(value - avg) ** 2 for value in values]) if values else 0.0

        xs: list[float] = []
        ys: list[float] = []
        directed_disagreement_sum = 0.0
        degree_normalized_disagreement_sum = 0.0
        same_belief_neighbor_count = 0
        for node in sorted(by_node):
            node_neighbors = self.neighbors.get(node, [])
            xs.append(float(by_node[node]))
            if node_neighbors:
                neighbor_values = [float(by_node[neighbor]) for neighbor in node_neighbors]
                ys.append(mean(neighbor_values))
                local = sum((float(by_node[node]) - value) ** 2 for value in neighbor_values)
                directed_disagreement_sum += local
                degree_normalized_disagreement_sum += local / len(node_neighbors)
                same_belief_neighbor_count += sum(
                    1 for neighbor in node_neighbors if by_node[node] == by_node[neighbor]
                )
            else:
                ys.append(float(by_node[node]))
                degree_normalized_disagreement_sum += 0.0
        paper_nci = _safe_pearson(xs, ys)
        paper_global_disagreement = 0.5 * degree_normalized_disagreement_sum / max(1, len(by_node))
        return {
            "polarization": float(belief_variance),
            "neighbor_correlation_index": float(paper_nci),
            "global_disagreement": float(paper_global_disagreement),
            "mean_belief": float(avg),
            "belief_variance": float(belief_variance),
            "pearson_neighbor_correlation": float(paper_nci),
            "global_disagreement_degree_normalized": float(paper_global_disagreement),
            "upstream_model_polarization_mean_belief": float(avg),
            "upstream_model_nci_same_belief_per_agent": float(
                same_belief_neighbor_count / max(1, len(by_node))
            ),
            "global_disagreement_unnormalized": float(0.5 * directed_disagreement_sum),
        }

    def _write_metrics(self, *, step: int, episode: int) -> None:
        payload = {"step": int(step), "episode": int(episode)} | self._metric_payload()
        _write_jsonl(self.output_dir / "echo_metrics.jsonl", payload)

    def begin_episode(self, episode: int) -> None:
        with self.lock:
            if self.active_episode == episode:
                return
            self.active_episode = episode
            self.snapshot_beliefs = dict(self.current_beliefs)
            self.snapshot_opinions = dict(self.current_opinions)
            self.interactions_by_episode[episode] = self._build_interactions_locked(episode)

    def _select_neighbors(self, *, name: str) -> list[int]:
        node = self.node_by_name[name]
        candidates = list(self.neighbors.get(node, []))
        belief = self.snapshot_beliefs[name]

        if self.recommendation == "random":
            self.rng.shuffle(candidates)
        elif self.recommendation == "similarity":
            candidates = [
                other
                for other in candidates
                if abs(belief - self.snapshot_beliefs[self.name_by_node[other]])
                <= self.similarity_threshold
            ]
        else:
            candidates = [
                other
                for other in candidates
                if abs(belief - self.snapshot_beliefs[self.name_by_node[other]])
                >= self.similarity_threshold
            ]

        if self.max_interactions is not None and self.max_interactions >= 0:
            candidates = candidates[: self.max_interactions]
        return candidates

    def _build_interactions_locked(self, episode: int) -> dict[str, dict[str, Any]]:
        interactions: dict[str, dict[str, Any]] = {}
        for name in self.agent_names:
            selected_nodes = self._select_neighbors(name=name)
            opinion_items = [
                {
                    "agent_id": node,
                    "name": self.name_by_node[node],
                    "belief": self.snapshot_beliefs[self.name_by_node[node]],
                    "opinion": self.snapshot_opinions[self.name_by_node[node]],
                    "source": "neighbor",
                }
                for node in selected_nodes
            ]

            mitigation_items: list[dict[str, Any]] = []
            passive_perspectives: list[str] = []
            own_node = self.node_by_name[name]
            own_belief = self.snapshot_beliefs[name]
            if (
                episode >= self.mitigation_step
                and own_belief in {-2, 2}
                and own_node not in self.leaders
            ):
                if self.mitigation_perspectives:
                    count = len(opinion_items) // 2 + 1
                    passive_perspectives = [
                        self.rng.choice(self.mitigation_perspectives) for _ in range(count)
                    ]
                if not self.mitigation_perspectives_only:
                    for leader_node in self.leaders:
                        leader_name = self.name_by_node.get(leader_node)
                        if leader_name and self.snapshot_beliefs[leader_name] != own_belief:
                            mitigation_items.append(
                                {
                                    "agent_id": leader_node,
                                    "name": leader_name,
                                    "belief": self.snapshot_beliefs[leader_name],
                                    "opinion": self.snapshot_opinions[leader_name],
                                    "source": "active_nudge_leader",
                                }
                            )

            all_items = opinion_items + mitigation_items
            interactions[name] = {
                "contact_ids": [int(item["agent_id"]) for item in all_items],
                "opinions": all_items,
                "passive_perspectives": passive_perspectives,
            }
        return interactions

    def observation_for(self, name: str, episode: int) -> dict[str, Any]:
        self.begin_episode(episode)
        with self.lock:
            interaction = self.interactions_by_episode[episode][name]
            return {
                "episode": episode,
                "agent_name": name,
                "agent_id": self.node_by_name[name],
                "topic": self.topic,
                "belief": self.snapshot_beliefs[name],
                "opinion": self.snapshot_opinions[name],
                "long_memory": self.long_memory[name],
                "belief_keywords": self.belief_keywords,
                "with_long_memory": self.with_long_memory,
                "opinions_heard": interaction["opinions"],
                "passive_perspectives": interaction["passive_perspectives"],
                "contact_ids": interaction["contact_ids"],
            }

    def stage_update(self, *, name: str, episode: int, update: dict[str, Any]) -> None:
        with self.lock:
            belief = update.get("belief", self.current_beliefs[name])
            try:
                belief = int(belief)
            except (TypeError, ValueError):
                belief = self.current_beliefs[name]
            belief = max(-2, min(2, belief))
            staged = {
                "episode": int(episode),
                "agent_name": name,
                "agent_id": self.node_by_name[name],
                "belief": belief,
                "opinion": str(update.get("opinion", self.current_opinions[name]) or ""),
                "reasoning": str(update.get("reasoning", "") or ""),
                "short_term_memory": str(update.get("short_term_memory", "") or ""),
                "long_term_memory": str(
                    update.get("long_term_memory", self.long_memory[name]) or ""
                ),
                "contact_ids": list(update.get("contact_ids", []) or []),
            }
            self.staged_by_episode.setdefault(episode, {})[name] = staged
            counts = self.update_counts_by_episode.setdefault(episode, {})
            counts[name] = int(counts.get(name, 0)) + 1
            _write_jsonl(self.output_dir / "echo_belief_events.jsonl", staged)
            expected_updates = max(1, int(self.expected_updates_per_agent_per_episode))
            enough_updates = all(
                int(counts.get(agent_name, 0)) >= expected_updates
                for agent_name in self.agent_names
            )
            if len(self.staged_by_episode[episode]) >= len(self.agent_names) and enough_updates:
                self._commit_episode_locked(episode)

    def _commit_episode_locked(self, episode: int) -> None:
        if episode in self.committed_episodes:
            return
        staged = self.staged_by_episode.get(episode, {})
        if len(staged) < len(self.agent_names):
            return
        for name in self.agent_names:
            item = staged[name]
            self.current_beliefs[name] = int(item["belief"])
            self.current_opinions[name] = str(item["opinion"])
            self.current_reasonings[name] = str(item["reasoning"])
            self.long_memory[name] = str(item["long_term_memory"])
            self.beliefs_history[name].append(self.current_beliefs[name])
            self.opinions_history[name].append(self.current_opinions[name])
            self.reasonings_history[name].append(self.current_reasonings[name])
            self.short_memory_full[name].append(str(item["short_term_memory"]))
            self.long_memory_full[name].append(self.long_memory[name])
            self.contact_ids_history[name].append([int(v) for v in item["contact_ids"]])
        self.committed_episodes.add(episode)
        self._write_metrics(step=episode + 1, episode=episode)
        self._write_agents_data()


def observation_to_text(payload: dict[str, Any]) -> str:
    return f"{OBSERVATION_START}\n{json.dumps(payload, ensure_ascii=True)}\n{OBSERVATION_END}"


def extract_observation(text: str) -> dict[str, Any] | None:
    if OBSERVATION_START not in text or OBSERVATION_END not in text:
        return None
    body = text.split(OBSERVATION_START, 1)[1].split(OBSERVATION_END, 1)[0].strip()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


@dataclasses.dataclass
class EchoChamberApp(TwitterLikeApp):
    """Twitter-like app shell that owns EchoChamberSim environment state."""

    app_description: str = "EchoChamberApp"
    agent_records_path: str = "replications/echo_chambers/input/agent_records.json"
    network_path: str = (
        "replications/echo_chambers/input/networks/scale_free_network_num_agents_50_seed_50.json"
    )
    belief_keywords_path: str = "replications/echo_chambers/input/belief_keywords.json"
    opinions_path: str = "replications/echo_chambers/input/opinions.json"
    mitigation_perspectives_path: str | None = None
    topic_key: str = "euthanasia"
    seed: int = 50
    leaders: tuple[int, ...] = (10, 30)
    similarity_threshold: int = 2
    max_interactions: int = -1
    recommendation: str = "similarity"
    mitigation_step: int = 1000
    mitigation_perspectives_only: bool = False
    with_long_memory: bool = True
    llm_response_mode: str = "json_object"
    prompt_variant: str = "compat"
    expected_updates_per_agent_per_episode: int = 1
    echo_state: EchoChamberWorld | None = dataclasses.field(default=None, init=False, repr=False)

    def name(self) -> str:
        return "EchoChamberApp"

    def _build_world(self) -> EchoChamberWorld:
        return EchoChamberWorld(
            output_dir=Path(self.db_path).resolve().parent,
            agent_records_path=Path(self.agent_records_path),
            network_path=Path(self.network_path),
            belief_keywords_path=Path(self.belief_keywords_path),
            opinions_path=Path(self.opinions_path),
            mitigation_perspectives_path=(
                Path(self.mitigation_perspectives_path)
                if self.mitigation_perspectives_path
                else None
            ),
            topic_key=self.topic_key,
            seed=int(self.seed),
            leaders=tuple(int(v) for v in self.leaders),
            similarity_threshold=int(self.similarity_threshold),
            max_interactions=int(self.max_interactions),
            recommendation=str(self.recommendation),
            mitigation_step=int(self.mitigation_step),
            mitigation_perspectives_only=bool(self.mitigation_perspectives_only),
            with_long_memory=bool(self.with_long_memory),
            expected_updates_per_agent_per_episode=max(
                1, int(self.expected_updates_per_agent_per_episode)
            ),
        )

    def initialize(self, agent_names: Sequence[str], **kwargs: Any) -> None:
        super().initialize(list(agent_names), **kwargs)
        self.echo_state = self._build_world()
        expected = set(self.echo_state.agent_names)
        actual = {str(name) for name in agent_names}
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        if missing or extra:
            raise ValueError(
                "EchoChamber agent mismatch. "
                f"missing_from_runtime={missing[:5]} extra_runtime={extra[:5]}"
            )
        self._log_action_event(
            "system",
            "echo_chamber_state_init",
            {
                "num_agents": len(self.echo_state.agent_names),
                "num_edges": len(self.echo_state.graph_edges),
                "recommendation": self.echo_state.recommendation,
                "state_owner": "EchoChamberApp",
            },
        )

    def setup_social_state(
        self,
        *,
        agent_names: list[str],
        sim_roles: dict[str, str] | None = None,
        graph_config: dict[str, Any] | None = None,
        following_graph: dict[str, list[str]] | None = None,
        agent_bios: dict[str, str] | None = None,
    ) -> None:
        super().setup_social_state(
            agent_names=agent_names,
            sim_roles=sim_roles,
            graph_config=graph_config,
            following_graph=following_graph,
            agent_bios=agent_bios,
        )
        self.echo_state = self._build_world()
        expected = set(self.echo_state.agent_names)
        actual = {str(name) for name in agent_names}
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        if missing or extra:
            raise ValueError(
                "EchoChamber agent mismatch. "
                f"missing_from_runtime={missing[:5]} extra_runtime={extra[:5]}"
            )
        self._log_action_event(
            "system",
            "echo_chamber_state_init",
            {
                "num_agents": len(self.echo_state.agent_names),
                "num_edges": len(self.echo_state.graph_edges),
                "recommendation": self.echo_state.recommendation,
                "state_owner": self.__class__.__name__,
            },
        )

    def echo_observation_for(self, name: str, episode: int) -> dict[str, Any]:
        if self.echo_state is None:
            raise RuntimeError("EchoChamberApp used before initialize(); echo_state missing.")
        return self.echo_state.observation_for(name, episode)

    def echo_stage_update(self, *, name: str, episode: int, update: dict[str, Any]) -> None:
        if self.echo_state is None:
            raise RuntimeError("EchoChamberApp used before initialize(); echo_state missing.")
        self.echo_state.stage_update(name=name, episode=episode, update=update)


@dataclasses.dataclass
class EchoChamberSocialApp(EchoChamberApp):
    """Twitter-like app with EchoChamberSim belief state used for measurement.

    This variant loosens the exact simulator action assumption.  Agents act on a
    real social-media backend, while EchoChamberWorld remains the app-owned
    measurement state used by observation filtering and post-action belief probes.
    """

    app_description: str = "EchoChamberSocialApp"
    seed_initial_opinion_posts: bool = True
    _seeded_initial_opinion_posts: bool = dataclasses.field(default=False, init=False, repr=False)

    def name(self) -> str:
        return "EchoChamberSocialApp"

    def initialize(self, agent_names: Sequence[str], **kwargs: Any) -> None:
        super().initialize(agent_names, **kwargs)
        self._seed_initial_opinion_posts()

    def setup_social_state(
        self,
        *,
        agent_names: list[str],
        sim_roles: dict[str, str] | None = None,
        graph_config: dict[str, Any] | None = None,
        following_graph: dict[str, list[str]] | None = None,
        agent_bios: dict[str, str] | None = None,
    ) -> None:
        super().setup_social_state(
            agent_names=agent_names,
            sim_roles=sim_roles,
            graph_config=graph_config,
            following_graph=following_graph,
            agent_bios=agent_bios,
        )
        self._seed_initial_opinion_posts()

    def _seed_initial_opinion_posts(self) -> None:
        if (
            not self.seed_initial_opinion_posts
            or self.echo_state is None
            or self._seeded_initial_opinion_posts
        ):
            return
        for name in self.echo_state.agent_names:
            opinion = self.echo_state.current_opinions.get(name, "")
            if opinion:
                try:
                    self.create_tweet(name, opinion)
                except Exception as exc:
                    self._print(
                        f"Initial opinion seed post failed for {name}: {exc}",
                        color="red",
                    )
        self._seeded_initial_opinion_posts = True
        self._log_action_event(
            "system",
            "echo_chamber_social_seed_posts",
            {
                "num_seeded_posts": len(self.echo_state.agent_names),
                "source": "initial_opinions",
            },
        )

    def echo_belief_for_post(self, post: dict[str, Any]) -> int | None:
        """Return current author belief for a backend post, if known."""
        if self.echo_state is None:
            return None
        username = str(post.get("username", "") or "")
        for display_name, mapped_username in self._user_mapping.items():
            if mapped_username == username:
                return int(self.echo_state.current_beliefs.get(display_name, 0))
        return None

    def echo_name_for_post(self, post: dict[str, Any]) -> str | None:
        """Return display name for a backend post author, if known."""
        username = str(post.get("username", "") or "")
        for display_name, mapped_username in self._user_mapping.items():
            if mapped_username == username:
                return display_name
        return None
