"""Memory backends used by the simulation.

Provides a lightweight list-backed memory bank as a drop-in replacement for
Concordia's associative memory bank when embedding/search is too expensive.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Iterable, Sequence
from io import StringIO

import numpy as np
import pandas as pd

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


class ListMemoryBank:
    """List-backed memory bank implementing the subset used by Concordia components.

    This backend avoids embedding on add and supports:
    - recent retrieval
    - selector scans
    - approximate associative retrieval by lexical overlap
    """

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
        return {t.lower() for t in _TOKEN_RE.findall(text)}

    def get_state(self) -> dict[str, object]:
        with self._memory_bank_lock:
            return {
                "stored_hashes": list(self._stored_hashes),
                "memory_bank": list(self._memory_bank),
            }

    def set_state(self, state: dict[str, object]) -> None:
        with self._memory_bank_lock:
            stored_hashes = state.get("stored_hashes", [])
            memory_bank = state.get("memory_bank", [])

            if isinstance(memory_bank, str):
                # Backward compatibility with serialized JSON payloads.
                memory_bank = pd.read_json(StringIO(memory_bank))["text"].tolist()

            self._stored_hashes = {int(x) for x in stored_hashes}
            self._memory_bank = [str(x) for x in memory_bank]

    def add(self, text: str) -> None:
        text = self._normalize(text)
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
            return [mem for mem in self._memory_bank if selector_fn(mem)]

    def retrieve_associative(self, query: str, k: int = 1) -> Sequence[str]:
        """Approximate associative retrieval by lexical overlap.

        If no overlap is found, falls back to most recent memories.
        """
        if k <= 0:
            raise ValueError("Limit must be positive.")

        query_tokens = self._tokenize(query)
        with self._memory_bank_lock:
            if not self._memory_bank:
                return []

            scored: list[tuple[int, int, str]] = []
            for idx, mem in enumerate(self._memory_bank):
                overlap = len(query_tokens & self._tokenize(mem))
                scored.append((overlap, idx, mem))

        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        top = [mem for score, _, mem in scored if score > 0][:k]
        if len(top) < k:
            recent = self.retrieve_recent(k=k)
            seen = set(top)
            for mem in reversed(recent):
                if mem not in seen:
                    top.append(mem)
                    seen.add(mem)
                if len(top) >= k:
                    break
        return top[:k]

    def __len__(self) -> int:
        with self._memory_bank_lock:
            return len(self._memory_bank)

    def get_all_memories_as_text(self) -> Sequence[str]:
        with self._memory_bank_lock:
            return list(self._memory_bank)

    def set_embedder(self, embedder: Callable[[str], np.ndarray]) -> None:
        self._embedder = embedder


def create_memory_bank(
    backend: str,
    sentence_embedder: Callable[[str], np.ndarray] | None,
):
    """Factory for supported memory backends.

    Supported values:
    - "associative": Concordia AssociativeMemoryBank (embedding-backed)
    - "list": list-backed memory without embedding on add
    """
    from concordia.associative_memory import basic_associative_memory as associative_memory

    normalized = (backend or "associative").strip().lower()
    if normalized == "associative":
        if sentence_embedder is None:
            raise ValueError("Associative memory backend requires a sentence encoder.")
        return associative_memory.AssociativeMemoryBank(sentence_embedder=sentence_embedder)
    if normalized == "list":
        return ListMemoryBank(sentence_embedder=sentence_embedder)
    raise ValueError(
        f"Unsupported memory backend '{backend}'. Expected 'associative' or 'list'."
    )
