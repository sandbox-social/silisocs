"""Concordia-only memory compatibility backends."""

from __future__ import annotations

import hashlib
import re
import threading
from collections.abc import Callable, Iterable, Sequence
from io import StringIO
from typing import Any

import numpy as np
import pandas as pd

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
_EMBEDDING_DIMS = 32


def deterministic_sentence_embedder(text: str) -> np.ndarray:
    """Return a small deterministic lexical embedding for Concordia compat."""
    vector = np.zeros(_EMBEDDING_DIMS, dtype=np.float32)
    for token in _TOKEN_RE.findall(str(text).lower()):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        sign = 1.0 if digest[1] % 2 else -1.0
        vector[digest[0] % _EMBEDDING_DIMS] += sign
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm else vector


class ListMemoryBank:
    """List-backed memory bank implementing the subset Concordia agents use."""

    def __init__(self, sentence_embedder: Callable[[str], np.ndarray] | None = None):
        self._memory_bank_lock = threading.Lock()
        self._memory_bank: list[str] = []
        self._stored_hashes: set[int] = set()
        self._embedder = sentence_embedder

    @staticmethod
    def _normalize(text: str) -> str:
        return text.replace("\n", " ").strip()

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        return {token.lower() for token in _TOKEN_RE.findall(text)}

    def get_state(self) -> dict[str, Any]:
        with self._memory_bank_lock:
            return {
                "stored_hashes": list(self._stored_hashes),
                "memory_bank": list(self._memory_bank),
            }

    def set_state(self, state: dict[str, Any]) -> None:
        with self._memory_bank_lock:
            stored_hashes = state.get("stored_hashes", [])
            memory_bank = state.get("memory_bank", [])
            if isinstance(memory_bank, str):
                memory_bank = pd.read_json(StringIO(memory_bank))["text"].tolist()
            if not isinstance(stored_hashes, Sequence) or isinstance(stored_hashes, (str, bytes)):
                stored_hashes = []
            if not isinstance(memory_bank, Sequence) or isinstance(memory_bank, (str, bytes)):
                memory_bank = []
            self._stored_hashes = {int(value) for value in stored_hashes}
            self._memory_bank = [str(value) for value in memory_bank]

    def add(self, text: str) -> None:
        text = self._normalize(str(text))
        if not text:
            return
        hashed = hash(text)
        with self._memory_bank_lock:
            if hashed in self._stored_hashes:
                return
            self._memory_bank.append(text)
            self._stored_hashes.add(hashed)

    def extend(self, texts: Iterable[str]) -> None:
        for text in texts:
            self.add(text)

    def get_data_frame(self) -> pd.DataFrame:
        with self._memory_bank_lock:
            return pd.DataFrame({"text": list(self._memory_bank)})

    def retrieve_recent(self, k: int = 1) -> Sequence[str]:
        if k <= 0:
            raise ValueError("Limit must be positive.")
        with self._memory_bank_lock:
            return list(self._memory_bank[-k:])

    def scan(self, selector_fn: Callable[[str], bool]) -> Sequence[str]:
        with self._memory_bank_lock:
            return [memory for memory in self._memory_bank if selector_fn(memory)]

    def retrieve_associative(self, query: str, k: int = 1) -> Sequence[str]:
        if k <= 0:
            raise ValueError("Limit must be positive.")
        query_tokens = self._tokenize(query)
        with self._memory_bank_lock:
            scored = [
                (len(query_tokens & self._tokenize(memory)), idx, memory)
                for idx, memory in enumerate(self._memory_bank)
            ]
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        top = [memory for score, _, memory in scored if score > 0][:k]
        if len(top) < k:
            seen = set(top)
            for memory in reversed(self.retrieve_recent(k=k)):
                if memory not in seen:
                    top.append(memory)
                    seen.add(memory)
                if len(top) >= k:
                    break
        return top[:k]

    def get_all_memories_as_text(self) -> Sequence[str]:
        with self._memory_bank_lock:
            return list(self._memory_bank)

    def set_embedder(self, embedder: Callable[[str], np.ndarray]) -> None:
        self._embedder = embedder

    def __len__(self) -> int:
        with self._memory_bank_lock:
            return len(self._memory_bank)


def create_memory_bank(
    memory_backend: str = "list",
    sentence_embedder: Callable[[str], np.ndarray] | None = None,
) -> Any:
    """Create the requested Concordia memory backend."""
    normalized = str(memory_backend or "list").strip().lower()
    if normalized == "list":
        return ListMemoryBank(sentence_embedder=sentence_embedder)
    if normalized == "associative":
        from concordia.associative_memory import basic_associative_memory

        return basic_associative_memory.AssociativeMemoryBank(
            sentence_embedder=sentence_embedder or deterministic_sentence_embedder
        )
    raise ValueError(
        f"Unsupported Concordia memory backend `{memory_backend}`. "
        "Supported values: list, associative."
    )
