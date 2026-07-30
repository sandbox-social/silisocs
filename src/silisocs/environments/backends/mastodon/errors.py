"""Dependency-free error types shared by the Mastodon app and its ops modules.

Lives outside ``mastodon_ops`` so :mod:`apps` can import it at module top
without pulling in the optional live-server dependencies the ops modules need.
"""

from __future__ import annotations


class PartialDeletionError(RuntimeError):
    """A batch deletion failed after some posts were already deleted.

    Deletions on the live server are irreversible, so the caller must not
    report the batch as fully uncommitted: ``deleted`` carries the post ids
    that really were removed and ``failed`` the ones that were not.
    """

    def __init__(self, deleted: list[int], failed: list[int]) -> None:
        super().__init__(f"Failed to delete post(s): {failed}")
        self.deleted = deleted
        self.failed = failed
