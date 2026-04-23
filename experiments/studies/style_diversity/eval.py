#!/usr/bin/env python3
"""Evaluate style diversity of social media simulation outputs.

Reference-free metrics to diagnose repetitive agent behavior. Computes 10
metrics per agent and aggregated across the simulation, with interpretive
labels and optional JSON export.

Usage:
    uv run python experiments/studies/style_diversity/eval.py checkpoint.json
    uv run python experiments/studies/style_diversity/eval.py ckpt1.json ckpt2.json --compare
    uv run python experiments/studies/style_diversity/eval.py checkpoint.json -o results/
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).parent.parent.parent

# ---------------------------------------------------------------------------
# Post dataclass — mirrors the interface expected by metric functions.
# Populated from mastodon_action_events.jsonl produced by this codebase.
# ---------------------------------------------------------------------------

import dataclasses  # noqa: E402


@dataclasses.dataclass
class Post:
    """Represents a single post in the simulation."""

    id: int
    author: str
    content: str
    step: int
    reply_to: int | None = None  # non-None → this is a reply
    boost_of: int | None = None  # non-None → this is a repost/boost


class _PostStore:
    """Minimal stand-in for SocialMediaApp — only get_all_posts() is needed."""

    def __init__(self, posts: list[Post]) -> None:
        self._posts = posts

    def get_all_posts(self) -> list[Post]:
        return list(self._posts)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALL_ACTION_TYPES = {"post", "reply", "like", "boost", "follow", "skip", "unfollow"}
CONTENT_ACTION_TYPES = {"post", "reply", "boost"}
TEXT_ACTION_TYPES = {"post", "reply"}  # produce original text (boost copies)

MIN_POSTS_FOR_TEXT_METRICS = 2  # agents below this get None for text metrics
NEAR_DUPLICATE_THRESHOLD = 0.9  # Jaccard similarity threshold


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_checkpoint(path: str | Path) -> tuple[_PostStore, list[dict[str, Any]]]:
    """Load a simulation run and return (post_store, raw_log).

    Reads:
      - raw_log from the checkpoint JSON (concordia agent-action log)
      - posts from mastodon_action_events.jsonl in the run output directory
        (located at checkpoint.parent.parent relative to the checkpoint file)

    Action event labels produced by TwitterLikeApp:
      post   → original post   (data: post_id, post_text)
      reply  → reply           (data: post_id, reply_to_id, post_text)
      repost → boost/repost    (data: post_id = original being reposted)
    """
    path = Path(path)
    with path.open() as f:
        data = json.load(f)

    raw_log: list[dict[str, Any]] = data.get("raw_log", [])

    # mastodon_action_events.jsonl sits two levels above the checkpoint file:
    #   outputs/{scenario}_experiment/{run}/checkpoints/step_N_checkpoint.json
    #                                  ↑ run dir
    output_dir = path.parent.parent
    events_file = output_dir / "action_events.jsonl"

    posts: list[Post] = []
    if events_file.is_file():
        with events_file.open() as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                label = event.get("label", "")
                episode = int(event.get("episode", 0))
                source = str(event.get("source_user", ""))
                ev_data = event.get("data", {}) or {}

                if label == "post":
                    posts.append(
                        Post(
                            id=int(ev_data.get("post_id", 0) or 0),
                            author=source,
                            content=str(ev_data.get("post_text", "")),
                            step=episode,
                        )
                    )
                elif label == "reply":
                    reply_to_raw = ev_data.get("reply_to_id")
                    posts.append(
                        Post(
                            id=int(ev_data.get("post_id", 0) or 0),
                            author=source,
                            content=str(ev_data.get("post_text", "")),
                            step=episode,
                            reply_to=int(reply_to_raw) if reply_to_raw is not None else None,
                        )
                    )
                elif label == "repost":
                    original_raw = ev_data.get("post_id")
                    posts.append(
                        Post(
                            id=-1,  # new repost id not logged; -1 marks synthetic
                            author=source,
                            content="",
                            step=episode,
                            boost_of=int(original_raw) if original_raw is not None else None,
                        )
                    )

    return _PostStore(posts), raw_log


def extract_agent_actions(  # noqa: C901
    raw_log: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Extract per-agent action records from raw_log.

    Returns: {agent_name: [action_dict, ...]} where each action_dict has
    keys: entity, parsed, result, step.
    """
    agent_actions: dict[str, list[dict[str, Any]]] = {}

    for entry in raw_log:
        step = entry.get("Step", 0)
        for key, val in entry.items():
            if not key.startswith("Entity [") or not isinstance(val, dict):
                continue
            agent_name = val.get("entity", "")
            if not agent_name:
                continue
            record = {
                "entity": agent_name,
                "parsed": val.get("parsed", {}),
                "result": val.get("result", {}),
                "step": step,
            }
            agent_actions.setdefault(agent_name, []).append(record)

    # Also check GM-level action entries (step 0 seed actions)
    for entry in raw_log:
        step = entry.get("Step", 0)
        for key, val in entry.items():
            if key in ("Step", "Summary") or key.startswith("Entity ["):
                continue
            if not isinstance(val, dict):
                continue
            actions = val.get("actions", {})
            for agent_name, action_data in actions.items():
                if not isinstance(action_data, dict):
                    continue
                # Avoid duplicates: only add if this agent doesn't already have
                # an Entity-level entry for this step
                existing_steps = {a["step"] for a in agent_actions.get(agent_name, [])}
                if step in existing_steps:
                    continue
                record = {
                    "entity": agent_name,
                    "parsed": action_data.get("parsed", {}),
                    "result": action_data.get("result", {}),
                    "step": step,
                }
                agent_actions.setdefault(agent_name, []).append(record)

    return agent_actions


# ---------------------------------------------------------------------------
# Text utilities
# ---------------------------------------------------------------------------


def tokenize(text: str) -> list[str]:
    """Tokenize text on whitespace and punctuation boundaries."""
    return re.findall(r"\w+", text.lower())


def ngrams(tokens: list[str], n: int) -> list[tuple[str, ...]]:
    """Extract n-grams from token list."""
    return [tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]


def jaccard_similarity(tokens_a: list[str], tokens_b: list[str]) -> float:
    """Token-level Jaccard similarity."""
    set_a = set(tokens_a)
    set_b = set(tokens_b)
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def bleu_score(candidate: list[str], reference: list[str], max_n: int = 4) -> float:
    """Simplified BLEU score (no brevity penalty, geometric mean of n-gram precisions)."""
    if not candidate or not reference:
        return 0.0

    precisions = []
    for n in range(1, max_n + 1):
        cand_ngrams = ngrams(candidate, n)
        ref_ngrams = ngrams(reference, n)
        if not cand_ngrams:
            break
        ref_counts = Counter(ref_ngrams)
        cand_counts = Counter(cand_ngrams)
        clipped = sum(min(cand_counts[ng], ref_counts[ng]) for ng in cand_counts)
        precision = clipped / len(cand_ngrams)
        if precision == 0:
            return 0.0
        precisions.append(precision)

    if not precisions:
        return 0.0
    log_avg = sum(math.log(p) for p in precisions) / len(precisions)
    return math.exp(log_avg)


def cosine_distance(vec_a: dict[str, float], vec_b: dict[str, float]) -> float:
    """Cosine distance (1 - cosine_similarity) between two sparse vectors."""
    all_keys = set(vec_a) | set(vec_b)
    if not all_keys:
        return 0.0
    dot = sum(vec_a.get(k, 0) * vec_b.get(k, 0) for k in all_keys)
    norm_a = math.sqrt(sum(v * v for v in vec_a.values()))
    norm_b = math.sqrt(sum(v * v for v in vec_b.values()))
    if norm_a == 0 or norm_b == 0:
        return 1.0
    return 1.0 - (dot / (norm_a * norm_b))


def tf_vector(tokens: list[str]) -> dict[str, float]:
    """Term frequency vector (normalized)."""
    if not tokens:
        return {}
    counts = Counter(tokens)
    total = len(tokens)
    return {word: count / total for word, count in counts.items()}


# ---------------------------------------------------------------------------
# Per-agent metrics
# ---------------------------------------------------------------------------


def compute_agent_metrics(  # noqa: C901, PLR0912, PLR0915
    agent_name: str,
    posts: list[Post],
    actions: list[dict[str, Any]],
) -> dict[str, float | None]:
    """Compute all 10 metrics for a single agent.

    Args:
        agent_name: Agent name.
        posts: All posts by this agent (from app state).
        actions: All action records for this agent (from raw_log).

    Returns
    -------
        Dict of metric_name -> value (None if insufficient data).
    """
    # Separate posts by type
    _ = [p for p in posts if p.step == 0]  # seed_posts (unused, kept for clarity)
    model_posts = [p for p in posts if p.step > 0]  # exclude step-0 seed posts
    text_posts = [p for p in model_posts if p.boost_of is None]  # exclude boosts

    # Action type distribution (from raw_log actions)
    action_types = []
    for act in actions:
        atype = act.get("result", {}).get("action_type")
        if not atype:
            atype = act.get("parsed", {}).get("action_type")
        if atype:
            action_types.append(atype)

    metrics: dict[str, float | None] = {}

    # ---- Repetition Metrics ----

    # 1. Self-BLEU
    if len(text_posts) >= MIN_POSTS_FOR_TEXT_METRICS:
        token_lists = [tokenize(p.content) for p in text_posts]
        bleu_scores = []
        for i, cand in enumerate(token_lists):
            refs = [token_lists[j] for j in range(len(token_lists)) if j != i]
            # Average BLEU against each reference
            if refs:
                avg_bleu = sum(bleu_score(cand, ref) for ref in refs) / len(refs)
                bleu_scores.append(avg_bleu)
        metrics["self_bleu"] = sum(bleu_scores) / len(bleu_scores) if bleu_scores else 0.0
    else:
        metrics["self_bleu"] = None

    # 2. Near-Duplicate Rate
    if len(text_posts) >= MIN_POSTS_FOR_TEXT_METRICS:
        token_lists = [tokenize(p.content) for p in text_posts]
        dup_count = 0
        for i in range(len(token_lists)):
            for j in range(i + 1, len(token_lists)):
                if jaccard_similarity(token_lists[i], token_lists[j]) > NEAR_DUPLICATE_THRESHOLD:
                    dup_count += 1
                    break  # count each post as duplicate at most once
        metrics["near_duplicate_rate"] = dup_count / len(text_posts)
    else:
        metrics["near_duplicate_rate"] = None

    # 3. Target Fixation
    reply_targets = [p.reply_to for p in model_posts if p.reply_to is not None]
    if reply_targets:
        target_counts = Counter(reply_targets)
        max_count = target_counts.most_common(1)[0][1]
        metrics["target_fixation"] = max_count / len(reply_targets)
    else:
        metrics["target_fixation"] = None

    # ---- Diversity Metrics ----

    # 4. Action Entropy (normalized Shannon entropy)
    if action_types:
        counts = Counter(action_types)
        total = sum(counts.values())
        n_types = len(ALL_ACTION_TYPES)
        entropy = -sum((c / total) * math.log2(c / total) for c in counts.values() if c > 0)
        max_entropy = math.log2(n_types) if n_types > 1 else 1.0
        metrics["action_entropy"] = entropy / max_entropy
    else:
        metrics["action_entropy"] = None

    # 5. Lexical Diversity (Type-Token Ratio)
    if len(text_posts) >= MIN_POSTS_FOR_TEXT_METRICS:
        all_tokens = []
        for p in text_posts:
            all_tokens.extend(tokenize(p.content))
        if all_tokens:
            metrics["lexical_diversity"] = len(set(all_tokens)) / len(all_tokens)
        else:
            metrics["lexical_diversity"] = 0.0
    else:
        metrics["lexical_diversity"] = None

    # 6. Content Evolution (avg cosine distance between consecutive posts)
    if len(text_posts) >= MIN_POSTS_FOR_TEXT_METRICS:
        sorted_posts = sorted(text_posts, key=lambda p: (p.step, p.id))
        distances = []
        for i in range(len(sorted_posts) - 1):
            vec_a = tf_vector(tokenize(sorted_posts[i].content))
            vec_b = tf_vector(tokenize(sorted_posts[i + 1].content))
            distances.append(cosine_distance(vec_a, vec_b))
        metrics["content_evolution"] = sum(distances) / len(distances) if distances else 0.0
    else:
        metrics["content_evolution"] = None

    # 7. Opener Variety (fraction of unique 5-word openings)
    if len(text_posts) >= MIN_POSTS_FOR_TEXT_METRICS:
        openers = []
        for p in text_posts:
            tokens = tokenize(p.content)
            opener = tuple(tokens[:5]) if len(tokens) >= 5 else tuple(tokens)  # noqa: PLR2004
            openers.append(opener)
        unique_openers = len(set(openers))
        metrics["opener_variety"] = unique_openers / len(openers)
    else:
        metrics["opener_variety"] = None

    # ---- Structural Metrics ----

    # 9. Action Diversity (distinct types used / total possible)
    if action_types:
        metrics["action_diversity"] = len(set(action_types)) / len(ALL_ACTION_TYPES)
    else:
        metrics["action_diversity"] = None

    # 10. New Post Rate (original posts / content-producing actions)
    content_actions = [
        a
        for a in actions
        if (a.get("result", {}).get("action_type") or a.get("parsed", {}).get("action_type"))
        in CONTENT_ACTION_TYPES
    ]
    original_posts_model = [p for p in model_posts if p.reply_to is None and p.boost_of is None]
    if content_actions:
        metrics["new_post_rate"] = len(original_posts_model) / len(content_actions)
    else:
        metrics["new_post_rate"] = None

    return metrics


# ---------------------------------------------------------------------------
# Corpus-level metric: Inter-Agent Distinctiveness
# ---------------------------------------------------------------------------


def compute_inter_agent_distinctiveness(
    agent_text_posts: dict[str, list[Post]],
) -> float | None:
    """Average pairwise cosine distance between agents' aggregate TF vectors.

    Only considers agents with >= MIN_POSTS_FOR_TEXT_METRICS non-boost posts.
    """
    # Build per-agent TF vectors
    agent_vecs: dict[str, dict[str, float]] = {}
    for agent, posts in agent_text_posts.items():
        text_posts = [p for p in posts if p.boost_of is None and p.step > 0]
        if len(text_posts) < MIN_POSTS_FOR_TEXT_METRICS:
            continue
        all_tokens = []
        for p in text_posts:
            all_tokens.extend(tokenize(p.content))
        if all_tokens:
            agent_vecs[agent] = tf_vector(all_tokens)

    if len(agent_vecs) < 2:  # noqa: PLR2004
        return None

    agents = list(agent_vecs.keys())
    distances = []
    for i in range(len(agents)):
        for j in range(i + 1, len(agents)):
            distances.append(cosine_distance(agent_vecs[agents[i]], agent_vecs[agents[j]]))  # noqa: PERF401

    return sum(distances) / len(distances) if distances else None


# ---------------------------------------------------------------------------
# Full evaluation
# ---------------------------------------------------------------------------


def load_probe_responses(checkpoint_path: Path) -> dict[str, dict[str, list[str]]]:
    """Load free-text probe responses from probe_events.jsonl.

    Returns: {probe_label: {agent_name: [response, ...]}}
    Only includes probes where raw_response is a non-empty string (i.e. FreeTextProbe).
    """
    output_dir = checkpoint_path.parent.parent
    probe_file = output_dir / "probe_events.jsonl"
    responses: dict[str, dict[str, list[str]]] = {}
    if not probe_file.is_file():
        return responses
    with probe_file.open() as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            data = event.get("data", {})
            raw = str(data.get("raw_response", "") or "").strip()
            # Skip non-text probes (numeric / binary responses are short)
            if not raw or len(raw) < 20:  # noqa: PLR2004
                continue
            label = str(event.get("label", ""))
            agent = str(event.get("source_user", ""))
            responses.setdefault(label, {}).setdefault(agent, []).append(raw)
    return responses


def compute_text_diversity(texts: list[str]) -> dict[str, float | None]:
    """Compute diversity metrics on a list of text samples (post or probe responses)."""
    if len(texts) < MIN_POSTS_FOR_TEXT_METRICS:
        return dict.fromkeys(
            ["self_bleu", "lexical_diversity", "opener_variety", "content_evolution"]
        )

    token_lists = [tokenize(t) for t in texts]

    bleu_scores = []
    for i, cand in enumerate(token_lists):
        refs = [token_lists[j] for j in range(len(token_lists)) if j != i]
        if refs:
            bleu_scores.append(sum(bleu_score(cand, r) for r in refs) / len(refs))
    sb = sum(bleu_scores) / len(bleu_scores) if bleu_scores else None

    all_tokens = [t for tl in token_lists for t in tl]
    ld = len(set(all_tokens)) / len(all_tokens) if all_tokens else None

    openers = [tuple(tl[:5]) for tl in token_lists]
    ov = len(set(openers)) / len(openers) if openers else None

    vecs = [tf_vector(tl) for tl in token_lists]
    dists = [cosine_distance(vecs[i], vecs[i + 1]) for i in range(len(vecs) - 1)]
    ce = sum(dists) / len(dists) if dists else None

    return {"self_bleu": sb, "lexical_diversity": ld, "opener_variety": ov, "content_evolution": ce}


def compute_probe_diversity(checkpoint_path: Path) -> dict[str, Any]:
    """Compute diversity of free-text probe responses.

    For each free-text probe label returns:
      - per_agent: within-agent diversity across steps (self-repetition in reasoning)
      - inter_agent: diversity treating each agent's concatenated responses as a document
    """
    probe_responses = load_probe_responses(checkpoint_path)
    result: dict[str, Any] = {}
    for label, agent_responses in probe_responses.items():
        # Per-agent: how diverse are an agent's responses across steps?
        per_agent: dict[str, dict[str, float | None]] = {}
        for agent, resps in agent_responses.items():
            per_agent[agent] = compute_text_diversity(resps)

        # Inter-agent: treat each agent's full response corpus as one document
        agent_docs = [" ".join(resps) for resps in agent_responses.values() if resps]
        inter_agent = compute_text_diversity(agent_docs)

        # Mean per-agent metrics
        mean_per_agent: dict[str, float | None] = {}
        for m in ["self_bleu", "lexical_diversity", "opener_variety", "content_evolution"]:
            vals = [per_agent[a][m] for a in per_agent if per_agent[a].get(m) is not None]
            mean_per_agent[m] = sum(vals) / len(vals) if vals else None

        result[label] = {
            "inter_agent": inter_agent,
            "mean_per_agent": mean_per_agent,
            "n_agents": len(agent_responses),
            "total_responses": sum(len(v) for v in agent_responses.values()),
        }
    return result


def evaluate_checkpoint(path: str | Path) -> dict[str, Any]:
    """Run all metrics on a single checkpoint.

    Returns a results dict with:
        - checkpoint: path string
        - agents: {name: {metric: value, ...}, ...}
        - aggregated: {metric: mean_value, ...}
        - corpus: {inter_agent_distinctiveness: value}
        - summary: {total_posts, total_actions, agents, steps, ...}
        - probe_diversity: {probe_label: {inter_agent: {...}, mean_per_agent: {...}}}
    """
    path = Path(path)
    app, raw_log = load_checkpoint(path)
    agent_actions = extract_agent_actions(raw_log)

    all_posts = app.get_all_posts()
    max_step = max((p.step for p in all_posts), default=0)

    # Group posts by author
    posts_by_author: dict[str, list[Post]] = {}
    for p in all_posts:
        posts_by_author.setdefault(p.author, []).append(p)

    # All known agents
    all_agents = sorted(set(posts_by_author.keys()) | set(agent_actions.keys()))

    # Compute per-agent metrics
    agent_metrics: dict[str, dict[str, float | None]] = {}
    for agent in all_agents:
        agent_posts = posts_by_author.get(agent, [])
        actions = agent_actions.get(agent, [])
        agent_metrics[agent] = compute_agent_metrics(agent, agent_posts, actions)

    # Corpus-level metric
    inter_agent_dist = compute_inter_agent_distinctiveness(posts_by_author)

    # Aggregate: mean of non-None values per metric
    metric_names = [
        "self_bleu",
        "near_duplicate_rate",
        "target_fixation",
        "action_entropy",
        "lexical_diversity",
        "content_evolution",
        "opener_variety",
        "action_diversity",
        "new_post_rate",
    ]
    aggregated: dict[str, float | None] = {}
    for metric in metric_names:
        values = [
            agent_metrics[a][metric] for a in all_agents if agent_metrics[a].get(metric) is not None
        ]
        aggregated[metric] = sum(values) / len(values) if values else None

    # Add corpus metric to aggregated
    aggregated["inter_agent_distinctiveness"] = inter_agent_dist

    # Summary stats
    model_posts = [p for p in all_posts if p.step > 0]
    boosts = [p for p in model_posts if p.boost_of is not None]
    replies = [p for p in model_posts if p.reply_to is not None]
    originals = [p for p in model_posts if p.reply_to is None and p.boost_of is None]

    total_actions = sum(len(acts) for acts in agent_actions.values())

    summary = {
        "total_posts": len(all_posts),
        "seed_posts": len([p for p in all_posts if p.step == 0]),
        "model_posts": len(model_posts),
        "replies": len(replies),
        "boosts": len(boosts),
        "original_posts": len(originals),
        "total_actions": total_actions,
        "agents": len(all_agents),
        "steps": max_step,
    }

    probe_diversity = compute_probe_diversity(path)

    return {
        "checkpoint": str(path),
        "agents": agent_metrics,
        "aggregated": aggregated,
        "summary": summary,
        "probe_diversity": probe_diversity,
    }


# ---------------------------------------------------------------------------
# Interpretive labels
# ---------------------------------------------------------------------------


def label_metric(name: str, value: float | None) -> str:  # noqa: C901, PLR0911, PLR0912
    """Return an interpretive label for a metric value."""
    if value is None:
        return "N/A"

    # Repetition metrics (lower is better)
    if name == "self_bleu":
        if value > 0.7:  # noqa: PLR2004
            return "HIGH REPETITION"
        if value > 0.4:  # noqa: PLR2004
            return "Moderate"
        return "Good"
    if name == "near_duplicate_rate":
        if value > 0.3:  # noqa: PLR2004
            return "HIGH DUPLICATES"
        if value > 0.1:  # noqa: PLR2004
            return "Some duplicates"
        return "Good"
    if name == "target_fixation":
        if value > 0.7:  # noqa: PLR2004
            return "FIXATED"
        if value > 0.4:  # noqa: PLR2004
            return "Moderate fixation"
        return "Good"

    # Diversity metrics (higher is better)
    if name == "action_entropy":
        if value < 0.1:  # noqa: PLR2004
            return "NEAR-ZERO"
        if value < 0.3:  # noqa: PLR2004
            return "Low"
        if value < 0.6:  # noqa: PLR2004
            return "Moderate"
        return "Good"
    if name == "lexical_diversity":
        if value < 0.3:  # noqa: PLR2004
            return "LOW VOCAB"
        if value < 0.5:  # noqa: PLR2004
            return "Moderate"
        return "Good"
    if name == "content_evolution":
        if value < 0.2:  # noqa: PLR2004
            return "STAGNANT"
        if value < 0.5:  # noqa: PLR2004
            return "Moderate"
        return "Good"
    if name == "opener_variety":
        if value < 0.3:  # noqa: PLR2004
            return "REPETITIVE OPENERS"
        if value < 0.6:  # noqa: PLR2004
            return "Moderate"
        return "Good"
    if name == "inter_agent_distinctiveness":
        if value < 0.2:  # noqa: PLR2004
            return "ALL SOUND SAME"
        if value < 0.4:  # noqa: PLR2004
            return "Moderate"
        return "Good"

    # Structural
    if name == "action_diversity":
        if value <= 1 / len(ALL_ACTION_TYPES) + 0.01:
            return "SINGLE ACTION TYPE"
        if value < 0.4:  # noqa: PLR2004
            return "Low"
        return "Good"
    if name == "new_post_rate":
        if value < 0.05:  # noqa: PLR2004
            return "ALMOST NONE"
        if value < 0.2:  # noqa: PLR2004
            return "Low"
        return "Good"

    return ""


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------


def format_value(value: float | None, decimals: int = 3) -> str:
    """Format a metric value for display."""
    if value is None:
        return "  N/A  "
    # Avoid displaying -0.00
    value = value + 0.0  # converts -0.0 to 0.0
    return f"{value:.{decimals}f}"


def print_report(results: dict[str, Any]) -> None:
    """Print human-readable evaluation report."""
    summary = results["summary"]
    aggregated = results["aggregated"]
    agent_metrics = results["agents"]
    checkpoint = results["checkpoint"]

    print(f"\n{'=' * 72}")
    print("  Style Diversity Evaluation")
    print(f"  Checkpoint: {checkpoint}")
    print(f"{'=' * 72}")

    print("\n  Summary:")
    print(f"    Agents: {summary['agents']}  |  Steps: {summary['steps']}")
    print(
        f"    Posts: {summary['total_posts']} total "
        f"({summary['seed_posts']} seed + {summary['model_posts']} model-generated)"
    )
    print(
        f"    Breakdown: {summary['replies']} replies, "
        f"{summary['original_posts']} original, "
        f"{summary['boosts']} boosts"
    )
    print(f"    Total actions logged: {summary['total_actions']}")

    # Aggregated metrics table
    print(f"\n{'─' * 72}")
    print("  AGGREGATED METRICS (mean across agents)")
    print(f"{'─' * 72}")

    metric_display = [
        ("self_bleu", "Self-BLEU", "lower=better"),
        ("near_duplicate_rate", "Near-Duplicate Rate", "lower=better"),
        ("target_fixation", "Target Fixation", "lower=better"),
        ("action_entropy", "Action Entropy (norm)", "higher=better"),
        ("lexical_diversity", "Lexical Diversity (TTR)", "higher=better"),
        ("content_evolution", "Content Evolution", "higher=better"),
        ("opener_variety", "Opener Variety", "higher=better"),
        ("inter_agent_distinctiveness", "Inter-Agent Distinct.", "higher=better"),
        ("action_diversity", "Action Diversity", "higher=better"),
        ("new_post_rate", "New Post Rate", "higher=better"),
    ]

    for key, display_name, direction in metric_display:
        val = aggregated.get(key)
        label = label_metric(key, val)
        val_str = format_value(val)
        print(f"    {display_name:<28s} {val_str:>8s}  ({direction})  {label}")

    # Per-agent table
    agents = sorted(agent_metrics.keys())
    print(f"\n{'─' * 72}")
    print("  PER-AGENT METRICS")
    print(f"{'─' * 72}")

    # Header
    col_metrics = [
        ("self_bleu", "S-BLEU"),
        ("near_duplicate_rate", "NearDup"),
        ("target_fixation", "TgtFix"),
        ("action_entropy", "ActEnt"),
        ("lexical_diversity", "LexDiv"),
        ("content_evolution", "CntEvo"),
        ("opener_variety", "OpnVar"),
        ("action_diversity", "ActDiv"),
        ("new_post_rate", "NewPst"),
    ]

    header = f"  {'Agent':<20s}"
    for _, col_name in col_metrics:
        header += f" {col_name:>7s}"
    print(header)
    print(f"  {'─' * 20}" + "─" * (8 * len(col_metrics)))

    for agent in agents:
        row = f"  {agent:<20s}"
        for metric_key, _ in col_metrics:
            val = agent_metrics[agent].get(metric_key)
            row += f" {format_value(val, 2):>7s}"
        print(row)

    print()


def print_comparison(all_results: list[dict[str, Any]]) -> None:
    """Print side-by-side comparison of multiple checkpoints."""
    print(f"\n{'=' * 80}")
    print("  COMPARISON: Style Diversity Across Checkpoints")
    print(f"{'=' * 80}")

    # Short labels for checkpoints
    labels = []
    for r in all_results:
        cp = Path(r["checkpoint"])
        # Use parent directory name as label
        label = cp.parent.parent.name if cp.parent.name == "checkpoints" else cp.stem
        labels.append(label)

    metric_display = [
        ("self_bleu", "Self-BLEU", "lower=better"),
        ("near_duplicate_rate", "Near-Duplicate Rate", "lower=better"),
        ("target_fixation", "Target Fixation", "lower=better"),
        ("action_entropy", "Action Entropy (norm)", "higher=better"),
        ("lexical_diversity", "Lexical Diversity (TTR)", "higher=better"),
        ("content_evolution", "Content Evolution", "higher=better"),
        ("opener_variety", "Opener Variety", "higher=better"),
        ("inter_agent_distinctiveness", "Inter-Agent Distinct.", "higher=better"),
        ("action_diversity", "Action Diversity", "higher=better"),
        ("new_post_rate", "New Post Rate", "higher=better"),
    ]

    # Header
    col_width = max(len(lbl) for lbl in labels) + 2
    col_width = max(col_width, 12)
    header = f"  {'Metric':<28s}"
    for label in labels:
        header += f" {label:>{col_width}s}"
    print(f"\n{header}")
    print(f"  {'─' * 28}" + "─" * ((col_width + 1) * len(labels)))

    for key, display_name, _direction in metric_display:
        row = f"  {display_name:<28s}"
        values = []
        for r in all_results:
            val = r["aggregated"].get(key)
            values.append(val)
            row += f" {format_value(val):>{col_width}s}"
        print(row)

        # Add labels row
        label_row = f"  {'':<28s}"
        for val in values:
            label = label_metric(key, val)
            label_row += f" {label:>{col_width}s}"
        print(label_row)

    # Summary comparison
    print(f"\n  {'─' * 28}" + "─" * ((col_width + 1) * len(labels)))
    summary_keys = [
        ("agents", "Agents"),
        ("steps", "Steps"),
        ("model_posts", "Model Posts"),
        ("replies", "Replies"),
        ("original_posts", "Original Posts"),
        ("boosts", "Boosts"),
    ]
    for key, display_name in summary_keys:
        row = f"  {display_name:<28s}"
        for r in all_results:
            val = r["summary"].get(key, 0)
            row += f" {val:>{col_width}d}"
        print(row)

    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point for evaluating style diversity."""
    parser = argparse.ArgumentParser(
        description="Evaluate style diversity of social media simulation outputs.",
    )
    parser.add_argument(
        "checkpoints",
        nargs="+",
        help="Path(s) to checkpoint JSON file(s)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="Directory to save JSON results",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Show side-by-side comparison table (multiple checkpoints)",
    )

    args = parser.parse_args()

    # Validate checkpoint paths
    for cp in args.checkpoints:
        if not Path(cp).exists():
            print(f"Error: checkpoint file not found: {cp}", file=sys.stderr)
            sys.exit(1)

    # Run evaluation
    all_results = []
    for cp in args.checkpoints:
        print(f"Evaluating: {cp} ...")
        results = evaluate_checkpoint(cp)
        all_results.append(results)

    # Print reports
    if args.compare and len(all_results) > 1:
        print_comparison(all_results)
    else:
        for results in all_results:
            print_report(results)

    # Save JSON if requested
    if args.output:
        out_path_arg = Path(args.output)
        # If -o ends with .json and only one checkpoint, use it as the direct file path.
        if out_path_arg.suffix == ".json" and len(all_results) == 1:
            out_path_arg.parent.mkdir(parents=True, exist_ok=True)
            with out_path_arg.open("w") as f:
                json.dump(all_results[0], f, indent=2, default=str)
            print(f"Saved: {out_path_arg}")
        else:
            out_dir = out_path_arg
            out_dir.mkdir(parents=True, exist_ok=True)

            for results in all_results:
                cp_name = Path(results["checkpoint"]).stem
                out_path = out_dir / f"eval_{cp_name}.json"
                with out_path.open("w") as f:
                    json.dump(results, f, indent=2, default=str)
                print(f"Saved: {out_path}")

        if len(all_results) > 1:
            # Save combined comparison
            combined_path = out_dir / "eval_comparison.json"
            with combined_path.open("w") as f:
                json.dump(all_results, f, indent=2, default=str)
            print(f"Saved: {combined_path}")


if __name__ == "__main__":
    main()
