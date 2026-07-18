"""Cacheable static asset bodies shared by Studio and backend platform viewers.

Both surfaces serve the same kind of thing: a body that only changes when the
installed package does (a vendored bundle, a generated stylesheet). Reading it
per request and answering without validators makes the browser re-download it on
every navigation — for the ~4.5MB Plotly bundle that dominates page-to-page
latency. This holds the body once and derives the validators; the HTTP framing
stays with each surface's own ``Response``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

CACHE_CONTROL = "public, max-age=86400"


@dataclass(frozen=True)
class CachedAsset:
    """One immutable body plus the headers that let a client skip re-fetching it."""

    body: bytes
    media_type: str
    etag: str

    @classmethod
    def build(cls, text: str, media_type: str) -> CachedAsset:
        """Encode a body and derive its content-addressed ETag."""
        return cls.build_bytes(text.encode("utf-8"), media_type)

    @classmethod
    def build_bytes(cls, body: bytes, media_type: str) -> CachedAsset:
        """Build an asset from an already encoded or binary body."""
        return cls(body, media_type, f'"{hashlib.sha256(body).hexdigest()[:32]}"')

    def headers(self) -> dict[str, str]:
        """Return the validators plus the cache directive, for 200 and 304 alike."""
        return {"ETag": self.etag, "Cache-Control": CACHE_CONTROL}

    def matches(self, if_none_match: str | None) -> bool:
        """Whether a client's ``If-None-Match`` already covers this body."""
        if not if_none_match:
            return False
        return any(
            candidate.strip().removeprefix("W/") in {self.etag, "*"}
            for candidate in if_none_match.split(",")
        )

    def response(self, if_none_match: str | None) -> tuple[int, bytes | None, dict[str, str]]:
        """Return the 304-vs-200 decision as framework-neutral parts.

        The caller builds its own ``Response`` from the tuple so this module
        stays free of any web-framework dependency. A ``None`` body means "send a
        bodyless 304"; a bytes body means "send a 200 with this content".
        """
        if self.matches(if_none_match):
            return 304, None, self.headers()
        return 200, self.body, self.headers()
