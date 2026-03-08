"""In-memory registry of active intents."""

from __future__ import annotations

import uuid
from typing import Sequence

from .schema import IntentDeclaration, IntentVerdict, ScopeConstraint
from .validator import IntentValidator


class IntentRegistry:
    """Stores and manages active :class:`IntentDeclaration` instances.

    Uses :class:`IntentValidator` internally to validate each intent before
    it is registered.
    """

    def __init__(
        self,
        constraints: Sequence[ScopeConstraint] | None = None,
        validator: IntentValidator | None = None,
    ) -> None:
        self._active: dict[uuid.UUID, IntentDeclaration] = {}
        self._constraints = list(constraints or [])
        self._validator = validator or IntentValidator()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(self, intent: IntentDeclaration) -> IntentVerdict:
        """Validate and, if approved, register an intent.

        Returns the :class:`IntentVerdict` regardless of the outcome.
        """
        verdict = self._validator.validate(
            intent,
            constraints=self._constraints,
            active_intents=list(self._active.values()),
        )
        if verdict.approved:
            self._active[intent.intent_id] = intent
        return verdict

    def release(self, intent_id: uuid.UUID) -> bool:
        """Remove an intent from the active set.

        Returns ``True`` if the intent was found and removed, ``False``
        otherwise.
        """
        return self._active.pop(intent_id, None) is not None

    def get_active(self) -> list[IntentDeclaration]:
        """Return a list of all currently active intents."""
        return list(self._active.values())

    def get_conflicts(self, intent: IntentDeclaration) -> list[uuid.UUID]:
        """Return intent IDs that conflict with the given intent."""
        return self._validator._detect_conflicts(
            intent, list(self._active.values())
        )
