"""Shared deterministic-RNG derivation for the engine policies.

Several replay-stable policy decisions (per-step participation draws, weighted
branch routing, the routing-fallback pick) need a *local* ``random.Random`` seeded
from a stable key string — never the global RNG, whose state concurrent turns would
perturb. The digest→int→``Random`` mechanic was written verbatim in three places;
this leaf module is its single home.

Only the mechanic is shared, NOT the key format: each caller keeps its own exact
key string (the domain salts differ, and replay stability plus cross-site
independence depend on the exact bytes). Change this and you change every one of
those seeded sequences, so treat the digest slice + byte order as frozen.
"""

from __future__ import annotations

import hashlib
import random


def stable_rng(key: str) -> random.Random:
    """Return a local ``random.Random`` seeded deterministically from ``key``'s bytes.

    The seed is the first 8 bytes of ``sha256(key)`` read big-endian — identical to
    the inline expression this replaced, so every existing seeded sequence is
    byte-for-byte preserved. Callers build their own domain-salted ``key``.
    """
    seed_int = int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big")
    return random.Random(seed_int)
