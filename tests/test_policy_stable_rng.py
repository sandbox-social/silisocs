"""FIX X3: the shared ``stable_rng`` helper must reproduce, byte-for-byte, the
inline ``sha256 -> int -> random.Random`` derivation it replaced at three policy
sites (participation, routing-fallback, RandomChoiceRouter). If this drifts, every
replay-stable seeded sequence in those policies changes.
"""

from __future__ import annotations

import hashlib
import random

from silisocs.simulation_engines.policies._rng import stable_rng


def _inline(key: str) -> random.Random:
    """The exact expression that lived inline before the refactor."""
    seed_int = int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big")
    return random.Random(seed_int)


def test_stable_rng_matches_inline_random_sequence() -> None:
    key = "1|participation|3|Alice"
    assert [stable_rng(key).random() for _ in range(1)] == [_inline(key).random()]
    a, b = stable_rng(key), _inline(key)
    assert [a.random() for _ in range(20)] == [b.random() for _ in range(20)]


def test_stable_rng_matches_inline_choice_and_choices() -> None:
    key = "7|flowA|2|Bob|routing-fallback"
    choices = ["gm_a", "gm_b", "gm_c"]
    assert stable_rng(key).choice(choices) == _inline(key).choice(choices)
    weights = [1.0, 2.0, 3.0]
    assert (
        stable_rng(key).choices(choices, weights=weights, k=1)[0]
        == _inline(key).choices(choices, weights=weights, k=1)[0]
    )


def test_stable_rng_distinct_keys_diverge() -> None:
    assert stable_rng("a").random() != stable_rng("b").random()
