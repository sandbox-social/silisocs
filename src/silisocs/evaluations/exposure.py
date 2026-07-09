"""Exposure→action analysis: join what agents SAW against what they DID.

Reads the paired ``exposure_events.jsonl`` (post ids + source shown to each
agent per turn) and ``action_events.jsonl`` (what each agent did) for a run and
computes, per agent, how much of what they were shown they engaged with — the
scientific object for recommender / platform-design studies.

This is the minimal join; cross-run contrasts and confidence intervals belong in
a study-level analysis layer, not here.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from silisocs.evaluations.action_events import (
    resolve_action_event_files,
    resolve_exposure_event_files,
)

# Keys under an action event's ``data`` that may reference a post the agent
# engaged with. Includes reply/quote parents (the actually-engaged post) as well
# as the direct target; the acting agent's own newly-created post id is also here
# but harmlessly never matches an exposed id (a just-created post wasn't shown).
_TARGET_ID_KEYS = ("reply_to_id", "quote_of_id", "target_id", "post_id", "tweet_id", "id")


def _iter_jsonl(files: list[Path]) -> Iterator[dict[str, Any]]:
    """Stream event rows one at a time so large logs never sit fully in memory."""
    for path in files:
        with path.open(encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    yield row


def _action_target_ids(event: dict[str, Any]) -> set[str]:
    """Post ids (as strings) an action may have engaged with.

    Ids are compared as strings because backends log post ids as strings
    (``str(post_id)``) while exposure events carry them as JSON numbers.
    """
    data = event.get("data")
    if not isinstance(data, dict):
        return set()
    return {str(data[key]) for key in _TARGET_ID_KEYS if data.get(key) is not None}


def exposure_action_join(run_dir: str | Path) -> dict[str, dict[str, Any]]:
    """Per-agent exposure→action summary for a run directory.

    Returns ``{agent: {exposures, exposed_post_ids, engaged_post_ids,
    engagement_rate}}`` where ``exposures`` counts exposure events, the id sets
    are sorted unique post ids, and ``engagement_rate`` is
    ``|engaged ∩ exposed| / |exposed|`` (0 when nothing was shown).
    """
    # Compare post ids as strings throughout: exposure events log ids as JSON
    # numbers, backends log action target ids as strings.
    exposed: dict[str, set[str]] = defaultdict(set)
    exposure_counts: dict[str, int] = defaultdict(int)
    for event in _iter_jsonl(resolve_exposure_event_files(run_dir)):
        agent = str(event.get("agent") or "")
        if not agent:
            continue
        exposure_counts[agent] += 1
        for post in event.get("posts") or []:
            if isinstance(post, dict) and post.get("id") is not None:
                exposed[agent].add(str(post["id"]))

    acted_on: dict[str, set[str]] = defaultdict(set)
    for event in _iter_jsonl(resolve_action_event_files(run_dir)):
        agent = str(event.get("source_user") or "")
        if agent:
            acted_on[agent] |= _action_target_ids(event)

    summary: dict[str, dict[str, Any]] = {}
    for agent in set(exposure_counts) | set(exposed):
        exposed_ids = exposed.get(agent, set())
        engaged = exposed_ids & acted_on.get(agent, set())
        summary[agent] = {
            "exposures": exposure_counts.get(agent, 0),
            "exposed_post_ids": sorted(exposed_ids, key=str),
            "engaged_post_ids": sorted(engaged, key=str),
            "engagement_rate": (len(engaged) / len(exposed_ids)) if exposed_ids else 0.0,
        }
    return summary
