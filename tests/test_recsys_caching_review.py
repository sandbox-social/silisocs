"""Tests for recsys embedding-cache and index review fixes.

Covers three fixes in the Twitter-like / Reddit-like SQLite engines:

1. Reddit ``_rec_embedding`` caches per-post embeddings (key ``("post_emb", pid)``)
   so each candidate post is encoded once, mirroring the Twitter-like backend.
2. Reddit ``_init_db`` declares ``idx_posts_created`` to back the recsys
   candidate query (``ORDER BY created_at DESC LIMIT 1000``).
3. Twitter ``embeddings_cache`` does not grow without bound across repeated
   recsys updates with changing per-step user contexts.

``torch`` is not installed in the test environment, so a minimal numpy-backed
fake ``torch`` is injected for the embedding-path tests. The fake only
implements the small surface the engines use: ``stack`` and
``nn.functional.cosine_similarity``.
"""

from __future__ import annotations

import sys
import types
from collections import Counter

import numpy as np
import pytest

from silisocs.environments.backends.reddit_like.engine import RedditLikePlatform
from silisocs.environments.backends.twitter_like.engine import TwitterLikePlatform

# --------------------------------------------------------------------------- #
# Minimal numpy-backed fake torch + fake embedding model
# --------------------------------------------------------------------------- #


class _FakeTensor:
    """Thin numpy wrapper exposing the tiny tensor surface the engines use."""

    def __init__(self, array: np.ndarray):
        self.array = np.asarray(array, dtype=float)

    def unsqueeze(self, _dim: int) -> _FakeTensor:
        return _FakeTensor(self.array[np.newaxis, ...])

    def __getitem__(self, idx):  # supports new_embeddings[row]
        return _FakeTensor(self.array[idx])

    def __float__(self) -> float:  # supports float(scores[idx])
        return float(self.array)


def _install_fake_torch(monkeypatch: pytest.MonkeyPatch) -> None:
    torch_mod = types.ModuleType("torch")
    functional_mod = types.ModuleType("torch.nn.functional")
    nn_mod = types.ModuleType("torch.nn")

    def stack(tensors: list[_FakeTensor]) -> _FakeTensor:
        return _FakeTensor(np.stack([t.array for t in tensors], axis=0))

    def cosine_similarity(a: _FakeTensor, b: _FakeTensor) -> _FakeTensor:
        # a: (1, D) user emb ; b: (N, D) post embs -> (N,) sims
        av = a.array.reshape(-1)
        bm = b.array
        num = bm @ av
        denom = (np.linalg.norm(bm, axis=1) * np.linalg.norm(av)) + 1e-9
        return _FakeTensor(num / denom)

    functional_mod.cosine_similarity = cosine_similarity  # type: ignore[attr-defined]
    nn_mod.functional = functional_mod  # type: ignore[attr-defined]
    torch_mod.nn = nn_mod  # type: ignore[attr-defined]
    torch_mod.stack = stack  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "torch", torch_mod)
    monkeypatch.setitem(sys.modules, "torch.nn", nn_mod)
    monkeypatch.setitem(sys.modules, "torch.nn.functional", functional_mod)


class _SpyModel:
    """Deterministic embedding model that counts how often each text is encoded."""

    def __init__(self) -> None:
        self.encode_counts: Counter[str] = Counter()

    def _vec(self, text: str) -> np.ndarray:
        # Stable pseudo-embedding derived from the text bytes.
        rng = np.random.default_rng(abs(hash(text)) % (2**32))
        return rng.standard_normal(8)

    def encode(self, texts, batch_size: int = 32, convert_to_tensor: bool = True):
        if isinstance(texts, str):
            self.encode_counts[texts] += 1
            return _FakeTensor(self._vec(texts))
        vecs = []
        for text in texts:
            self.encode_counts[text] += 1
            vecs.append(self._vec(text))
        return _FakeTensor(np.stack(vecs, axis=0))


def _make_users(n: int) -> list[dict]:
    return [{"id": i, "username": f"u{i}", "bio": f"bio of user {i}"} for i in range(1, n + 1)]


def _make_posts(n: int) -> list[dict]:
    # Author each post to a user id outside the candidate user set so no post is
    # filtered out as self-authored.
    return [
        {
            "id": 100 + i,
            "user_id": 9000 + i,
            "content": f"post content number {i}",
            "created_at": float(i),
            "upvotes": 0,
            "downvotes": 0,
        }
        for i in range(1, n + 1)
    ]


# --------------------------------------------------------------------------- #
# Fix 1: Reddit per-post embedding cache
# --------------------------------------------------------------------------- #


def test_reddit_rec_embedding_caches_posts_across_updates(monkeypatch):
    """Each post is encoded exactly once even when update runs twice."""
    _install_fake_torch(monkeypatch)

    platform = RedditLikePlatform.__new__(RedditLikePlatform)  # skip DB init
    model = _SpyModel()
    state = {"type": "twhin", "model": model, "embeddings_cache": {}}

    users = _make_users(3)
    posts = _make_posts(5)

    first = platform._rec_embedding(users, posts, max_posts=3, recsys_state=state)
    assert first, "expected recommendations on first pass"

    # Every post text encoded exactly once after first pass.
    for post in posts:
        assert model.encode_counts[post["content"]] == 1

    # Per-post embeddings are cached under ("post_emb", pid).
    for post in posts:
        assert ("post_emb", post["id"]) in state["embeddings_cache"]

    second = platform._rec_embedding(users, posts, max_posts=3, recsys_state=state)
    assert second == first

    # Second pass over the SAME posts must NOT re-encode any post content.
    for post in posts:
        assert model.encode_counts[post["content"]] == 1, (
            f"post {post['id']} re-encoded; per-post cache not reused"
        )


def test_reddit_rec_embedding_encodes_only_new_posts(monkeypatch):
    """A post added after the first pass is the only newly encoded post."""
    _install_fake_torch(monkeypatch)

    platform = RedditLikePlatform.__new__(RedditLikePlatform)
    model = _SpyModel()
    state = {"type": "twhin", "model": model, "embeddings_cache": {}}

    users = _make_users(2)
    posts = _make_posts(3)
    platform._rec_embedding(users, posts, max_posts=3, recsys_state=state)

    new_post = {
        "id": 999,
        "user_id": 9999,
        "content": "a brand new post",
        "created_at": 99.0,
        "upvotes": 0,
        "downvotes": 0,
    }
    platform._rec_embedding(users, [*posts, new_post], max_posts=3, recsys_state=state)

    # Only the new post is encoded a single time; old posts stay at one encode.
    assert model.encode_counts[new_post["content"]] == 1
    for post in posts:
        assert model.encode_counts[post["content"]] == 1


# --------------------------------------------------------------------------- #
# Fix 2: Reddit created_at index
# --------------------------------------------------------------------------- #


def test_reddit_init_db_creates_idx_posts_created(tmp_path):
    db_path = str(tmp_path / "reddit_index.db")
    platform = RedditLikePlatform(db_path=db_path, use_queue=False)
    try:
        with platform.get_connection() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'posts'"
            ).fetchall()
        index_names = {row["name"] for row in rows}
        assert "idx_posts_created" in index_names, (
            f"idx_posts_created missing; found {sorted(index_names)}"
        )
    finally:
        platform.shutdown()


# --------------------------------------------------------------------------- #
# Fix 3: Twitter bounded embeddings_cache
# --------------------------------------------------------------------------- #


def test_prune_user_context_cache_drops_stale_user_keys():
    cache = {
        ("post_emb", 1): "keep",
        ("post_emb", 2): "keep",
        ("user", 1, "ctx-old"): "drop",
        ("user", 2, "ctx-old"): "drop",
        ("user", 1, "ctx-new"): "keep",
    }
    live = {("user", 1, "ctx-new")}
    TwitterLikePlatform._prune_user_context_cache(cache, live)

    # Stale user keys removed; live user key and all post embeddings retained.
    assert ("user", 1, "ctx-old") not in cache
    assert ("user", 2, "ctx-old") not in cache
    assert ("user", 1, "ctx-new") in cache
    assert ("post_emb", 1) in cache
    assert ("post_emb", 2) in cache


def test_twitter_embeddings_cache_stays_bounded_across_updates(monkeypatch):
    """Per-step user-context keys do not accumulate without bound."""
    _install_fake_torch(monkeypatch)

    platform = TwitterLikePlatform.__new__(TwitterLikePlatform)
    model = _SpyModel()
    # context_recent_posts > 0 so each user's context string changes per step,
    # which is exactly the unbounded-growth scenario the fix targets.
    state = {
        "type": "twitter",
        "backend": "sentence_transformer",
        "model": model,
        "embeddings_cache": {},
        "user_context_recent_posts": 2,
        "include_like_trace": False,
        "like_trace_window": 0,
        "like_trace_weight": 0.0,
        "include_like_trace_in_context": True,
    }

    num_users = 4
    posts = [
        {"id": 200 + i, "user_id": 8000 + i, "content": f"twitter post {i}", "likes": 0}
        for i in range(1, 6)
    ]

    cache_sizes: list[int] = []
    for step in range(6):
        users = [
            {
                "id": uid,
                "username": f"u{uid}",
                "bio": f"bio {uid}",
                # Recent posts differ every step -> new context string each step.
                "recent_posts": [f"step{step}-recent-{uid}-a", f"step{step}-recent-{uid}-b"],
                "liked_posts": [],
            }
            for uid in range(1, num_users + 1)
        ]
        platform._rec_embedding(users, posts, max_posts=3, recsys_state=state)
        cache_sizes.append(len(state["embeddings_cache"]))

    cache = state["embeddings_cache"]
    user_keys = [k for k in cache if isinstance(k, tuple) and k and k[0] == "user"]
    post_keys = [k for k in cache if isinstance(k, tuple) and k and k[0] == "post_emb"]

    # Exactly one live user-context key per user (stale per-step keys evicted),
    # not num_users * num_steps.
    assert len(user_keys) == num_users, f"user-context cache grew unbounded: {len(user_keys)}"
    assert len(post_keys) == len(posts)

    # Total cache size is bounded by users + posts regardless of step count.
    assert max(cache_sizes) <= num_users + len(posts)
    # And it stabilizes rather than monotonically increasing with steps.
    assert cache_sizes[-1] == cache_sizes[1]
