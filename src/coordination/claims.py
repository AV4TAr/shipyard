"""ClaimManager — tracks which agents hold locks on which code areas."""

from __future__ import annotations

import fnmatch
from datetime import datetime, timezone

from .models import Claim, ClaimConflict, ConflictResolution


class ClaimManager:
    """In-memory manager for agent code-area claims.

    Provides acquire / release semantics, overlap detection via glob
    matching, and automatic expiry of stale claims.
    """

    def __init__(self) -> None:
        self._claims: dict[str, Claim] = {}  # claim_id (str) -> Claim

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def acquire(self, claim: Claim) -> ClaimConflict | None:
        """Try to acquire *claim*.

        Returns ``None`` on success (claim is stored), or a
        :class:`ClaimConflict` describing the collision with an existing
        claim.
        """
        self._expire_stale()

        overlapping = self.check_overlap(claim.paths)
        for existing in overlapping:
            # Same agent extending its own claim is fine.
            if existing.agent_id == claim.agent_id:
                continue

            overlap_paths = self._overlapping_paths(existing.paths, claim.paths)
            resolution = self.resolve_conflict(
                ClaimConflict(
                    existing_claim=existing,
                    new_claim=claim,
                    overlapping_paths=overlap_paths,
                    resolution=ConflictResolution.EXISTING_WINS,  # placeholder
                )
            )

            if resolution in (ConflictResolution.EXISTING_WINS, ConflictResolution.REJECT):
                return ClaimConflict(
                    existing_claim=existing,
                    new_claim=claim,
                    overlapping_paths=overlap_paths,
                    resolution=resolution,
                )

            if resolution == ConflictResolution.QUEUE:
                return ClaimConflict(
                    existing_claim=existing,
                    new_claim=claim,
                    overlapping_paths=overlap_paths,
                    resolution=ConflictResolution.QUEUE,
                )

            # NEW_WINS → evict existing, continue checking others
            if resolution == ConflictResolution.NEW_WINS:
                self._claims.pop(str(existing.claim_id), None)

        self._claims[str(claim.claim_id)] = claim
        return None

    def release(self, claim_id: str) -> None:
        """Release the claim identified by *claim_id*."""
        self._claims.pop(claim_id, None)

    def get_active_claims(self) -> list[Claim]:
        """Return all non-expired active claims."""
        self._expire_stale()
        return list(self._claims.values())

    def check_overlap(self, paths: list[str]) -> list[Claim]:
        """Find active claims whose path globs overlap with *paths*."""
        self._expire_stale()
        result: list[Claim] = []
        for claim in self._claims.values():
            if self._paths_overlap(claim.paths, paths):
                result.append(claim)
        return result

    def resolve_conflict(self, conflict: ClaimConflict) -> ConflictResolution:
        """Decide how to resolve *conflict*.

        Rules (in order):
        1. Higher priority wins.
        2. Equal priority → first-come-first-served (EXISTING_WINS).
        """
        if conflict.new_claim.priority > conflict.existing_claim.priority:
            return ConflictResolution.NEW_WINS
        if conflict.new_claim.priority < conflict.existing_claim.priority:
            return ConflictResolution.EXISTING_WINS
        # Equal priority — FIFO: existing was first.
        return ConflictResolution.EXISTING_WINS

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _expire_stale(self) -> None:
        """Remove claims whose ``expires_at`` is in the past."""
        now = datetime.now(timezone.utc)
        expired = [
            cid for cid, claim in self._claims.items() if claim.expires_at <= now
        ]
        for cid in expired:
            del self._claims[cid]

    @staticmethod
    def _paths_overlap(globs_a: list[str], globs_b: list[str]) -> bool:
        """Return True if any glob from *globs_a* matches any from *globs_b*.

        Since we're comparing globs (not concrete paths), we check both
        directions: does any pattern in A match a pattern in B treated as
        a literal, and vice-versa.
        """
        for a in globs_a:
            for b in globs_b:
                if fnmatch.fnmatch(a, b) or fnmatch.fnmatch(b, a):
                    return True
        return False

    @staticmethod
    def _overlapping_paths(globs_a: list[str], globs_b: list[str]) -> list[str]:
        """Return the subset of paths that overlap between two glob lists."""
        result: list[str] = []
        for a in globs_a:
            for b in globs_b:
                if fnmatch.fnmatch(a, b) or fnmatch.fnmatch(b, a):
                    result.append(a)
                    break
        return result
