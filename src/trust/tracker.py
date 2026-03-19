"""Trust tracker — maintains agent profiles and updates trust scores over time."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .models import AgentProfile

if TYPE_CHECKING:
    from src.storage.repositories import AgentProfileRepository


class TrustTracker:
    """Store for :class:`AgentProfile` objects with optional persistence.

    When *profile_repo* is provided, profiles are persisted through that
    repository.  Otherwise falls back to an internal dict.
    """

    def __init__(self, *, profile_repo: AgentProfileRepository | None = None) -> None:
        self._profile_repo = profile_repo
        self._profiles: dict[str, AgentProfile] = {}

    # ------------------------------------------------------------------
    # Storage helpers
    # ------------------------------------------------------------------

    def _save_profile(self, profile: AgentProfile) -> None:
        if self._profile_repo:
            self._profile_repo.save(profile)
        self._profiles[profile.agent_id] = profile

    def _get_profile(self, agent_id: str) -> AgentProfile | None:
        # Repo is source of truth when available
        if self._profile_repo:
            profile = self._profile_repo.get(agent_id)
            if profile is not None:
                self._profiles[agent_id] = profile  # update cache
                return profile
        # Fall back to memory
        return self._profiles.get(agent_id)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_profile(self, agent_id: str) -> AgentProfile:
        """Return the profile for *agent_id*, creating a default one if new."""
        profile = self._get_profile(agent_id)
        if profile is None:
            profile = AgentProfile(agent_id=agent_id)
            self._save_profile(profile)
        return profile

    def record_outcome(
        self,
        agent_id: str,
        *,
        success: bool,
        risk_score: float,
        domain: str | None = None,
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

        update_dict: dict[str, object] = {
            "total_deployments": new_total,
            "successful_deployments": new_successful,
            "rollbacks": new_rollbacks,
            "avg_risk_score": round(new_avg_risk, 4),
        }

        # Update domain-specific score when a domain is provided.
        if domain is not None:
            domain_scores = dict(profile.domain_scores)
            prev = domain_scores.get(domain, 0.5)
            # Exponential moving average: weight recent outcome more heavily.
            outcome_val = 1.0 if success else 0.0
            domain_scores[domain] = round(prev * 0.7 + outcome_val * 0.3, 4)
            update_dict["domain_scores"] = domain_scores

        updated = profile.model_copy(update=update_dict)
        self._save_profile(updated)
        return updated

    def compute_domain_trust(self, agent_id: str, domain: str) -> float:
        """Return the domain-specific trust score for *agent_id* and *domain*.

        If the agent has no domain-specific history, falls back to the
        overall trust score.
        """
        profile = self.get_profile(agent_id)
        if domain in profile.domain_scores:
            return profile.domain_scores[domain]
        return profile.trust_score

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
        if self._profile_repo:
            result = {}
            for p in self._profile_repo.list_all():
                self._profiles[p.agent_id] = p  # refresh cache
                result[p.agent_id] = p
            return result
        return dict(self._profiles)
