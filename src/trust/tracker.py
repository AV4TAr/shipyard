"""Trust tracker — maintains agent profiles and updates trust scores over time."""

from __future__ import annotations

from datetime import datetime, timezone

from .models import AgentProfile


class TrustTracker:
    """In-memory store for :class:`AgentProfile` objects.

    Provides methods to record deployment outcomes and recompute trust scores.
    """

    def __init__(self) -> None:
        self._profiles: dict[str, AgentProfile] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_profile(self, agent_id: str) -> AgentProfile:
        """Return the profile for *agent_id*, creating a default one if new."""
        if agent_id not in self._profiles:
            self._profiles[agent_id] = AgentProfile(agent_id=agent_id)
        return self._profiles[agent_id]

    def record_outcome(
        self,
        agent_id: str,
        *,
        success: bool,
        risk_score: float,
    ) -> AgentProfile:
        """Record a deployment outcome and update the agent's profile.

        Parameters
        ----------
        agent_id:
            Identifier of the agent.
        success:
            Whether the deployment succeeded (``True``) or resulted in a
            rollback (``False``).
        risk_score:
            The risk score of the change that was deployed (0-1).

        Returns
        -------
        AgentProfile
            The updated profile.
        """
        profile = self.get_profile(agent_id)

        # Update counters
        new_total = profile.total_deployments + 1
        new_successful = profile.successful_deployments + (1 if success else 0)
        new_rollbacks = profile.rollbacks + (0 if success else 1)

        # Running average of risk scores
        new_avg_risk = (
            (profile.avg_risk_score * profile.total_deployments + risk_score)
            / new_total
        )

        updated = profile.model_copy(
            update={
                "total_deployments": new_total,
                "successful_deployments": new_successful,
                "rollbacks": new_rollbacks,
                "avg_risk_score": round(new_avg_risk, 4),
            }
        )
        self._profiles[agent_id] = updated
        return updated

    def compute_trust_score(self, agent_id: str) -> float:
        """Return the current trust score for *agent_id*.

        Formula:
            success_rate * 0.6 + (1 - rollback_rate) * 0.3 + tenure_bonus * 0.1

        This is identical to :attr:`AgentProfile.trust_score` but exposed
        as a convenience method on the tracker.
        """
        return self.get_profile(agent_id).trust_score

    @property
    def profiles(self) -> dict[str, AgentProfile]:
        """Read-only access to all stored profiles."""
        return dict(self._profiles)
