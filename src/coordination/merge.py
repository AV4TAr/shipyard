"""SemanticMergeChecker — decides whether two changes are logically compatible."""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Claim, MergeCheck


@dataclass
class Change:
    """Lightweight representation of a code change for merge analysis."""

    intent_id: str
    files: list[str] = field(default_factory=list)
    description: str = ""


class SemanticMergeChecker:
    """Checks whether two changes can be merged without semantic conflicts.

    Current implementation uses simple heuristics.  A future version will
    use LLM-based semantic analysis for deeper understanding.
    """

    def check(self, change_a: Change, change_b: Change) -> MergeCheck:
        """Determine if *change_a* and *change_b* are logically compatible.

        Heuristic rules:
        * Disjoint file sets → compatible, auto-resolvable.
        * Overlapping files → incompatible, not auto-resolvable.
        """
        files_a = set(change_a.files)
        files_b = set(change_b.files)
        shared = sorted(files_a & files_b)

        if not shared:
            return MergeCheck(compatible=True, conflicts=[], auto_resolvable=True)

        conflict_descriptions = [
            f"Both changes modify '{f}'" for f in shared
        ]

        # TODO: Use LLM to perform deeper semantic analysis — e.g. two
        #       changes to the same file may still be compatible if they
        #       touch non-overlapping functions / classes.
        # TODO: Parse ASTs to detect whether changed regions actually
        #       overlap at the symbol level.
        # TODO: Consider service-level dependency analysis (e.g. API
        #       contract changes that affect downstream consumers).

        return MergeCheck(
            compatible=False,
            conflicts=conflict_descriptions,
            auto_resolvable=False,
        )

    def suggest_order(self, claims: list[Claim]) -> list[Claim]:
        """Return *claims* sorted in optimal merge order.

        Strategy: higher priority first, then earlier ``acquired_at``
        (FIFO among equal priorities).

        TODO: Incorporate dependency graph so that prerequisite changes
              land before dependent ones.
        """
        return sorted(
            claims,
            key=lambda c: (-c.priority, c.acquired_at),
        )
