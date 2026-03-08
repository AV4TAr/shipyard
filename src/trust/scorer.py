"""Risk scoring engine — computes risk assessments from multiple weighted factors."""

from __future__ import annotations

import fnmatch
from datetime import datetime, timezone

from src.intent.schema import IntentDeclaration
from src.validation.models import ValidationVerdict

from .models import (
    AgentProfile,
    DeployRoute,
    RiskAssessment,
    RiskFactor,
    RiskLevel,
)

# ---------------------------------------------------------------------------
# File-sensitivity patterns (scored 0-1, higher = riskier)
# ---------------------------------------------------------------------------
_HIGH_SENSITIVITY_PATTERNS: list[str] = [
    "**/migrations/**",
    "**/deploy/**",
    "**/prod/**",
    "**/*.env",
    "**/secrets/**",
    "**/auth/**",
    "**/iam/**",
    "**/infra/**",
    "terraform/**",
    "**/Dockerfile*",
    "docker-compose*",
]

_LOW_SENSITIVITY_PATTERNS: list[str] = [
    "**/test*/**",
    "**/tests/**",
    "**/*_test.py",
    "**/test_*.py",
    "**/docs/**",
    "**/*.md",
    "**/*.txt",
    "**/README*",
]


def _fnmatch_any(path: str, patterns: list[str]) -> bool:
    """Check whether *path* matches any of the given glob patterns."""
    for pat in patterns:
        normalised = pat.replace("**", "*")
        if fnmatch.fnmatch(path, normalised):
            return True
    return False


class RiskScorer:
    """Computes a :class:`RiskAssessment` for an intent by combining multiple factors.

    Each factor produces a score in [0, 1] and has a weight.  The final
    ``risk_score`` is the weighted sum, clamped to [0, 1].

    Configurable thresholds map the score to a :class:`RiskLevel`, and each
    level maps to a :class:`DeployRoute`.
    """

    def __init__(
        self,
        *,
        risk_thresholds: dict[RiskLevel, float] | None = None,
        route_map: dict[RiskLevel, DeployRoute] | None = None,
        factor_weights: dict[str, float] | None = None,
    ) -> None:
        # risk_score >= threshold  =>  that RiskLevel (evaluated highest first)
        self.risk_thresholds: dict[RiskLevel, float] = risk_thresholds or {
            RiskLevel.CRITICAL: 0.85,
            RiskLevel.HIGH: 0.60,
            RiskLevel.MEDIUM: 0.35,
            RiskLevel.LOW: 0.0,
        }

        self.route_map: dict[RiskLevel, DeployRoute] = route_map or {
            RiskLevel.LOW: DeployRoute.AUTO_DEPLOY,
            RiskLevel.MEDIUM: DeployRoute.AGENT_REVIEW,
            RiskLevel.HIGH: DeployRoute.HUMAN_APPROVAL,
            RiskLevel.CRITICAL: DeployRoute.HUMAN_APPROVAL_CANARY,
        }

        self.factor_weights: dict[str, float] = factor_weights or {
            "file_sensitivity": 0.25,
            "blast_radius": 0.20,
            "agent_trust": 0.20,
            "validation_confidence": 0.20,
            "time_of_day": 0.15,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def assess(
        self,
        intent: IntentDeclaration,
        validation_verdict: ValidationVerdict,
        agent_profile: AgentProfile,
    ) -> RiskAssessment:
        """Compute a full risk assessment for the given intent."""
        factors = [
            self._file_sensitivity_factor(intent),
            self._blast_radius_factor(intent),
            self._agent_trust_factor(agent_profile),
            self._validation_confidence_factor(validation_verdict),
            self._time_of_day_factor(),
        ]

        risk_score = self._weighted_score(factors)
        risk_level = self._score_to_level(risk_score)
        route = self.route_map[risk_level]

        return RiskAssessment(
            intent_id=intent.intent_id,
            risk_level=risk_level,
            risk_score=round(risk_score, 4),
            factors=factors,
            recommended_route=route,
        )

    # ------------------------------------------------------------------
    # Factor computations
    # ------------------------------------------------------------------

    def _file_sensitivity_factor(self, intent: IntentDeclaration) -> RiskFactor:
        """Score based on how sensitive the touched files are."""
        if not intent.target_files:
            score = 0.5  # unknown files — moderate risk
        else:
            scores: list[float] = []
            for path in intent.target_files:
                if _fnmatch_any(path, _HIGH_SENSITIVITY_PATTERNS):
                    scores.append(0.9)
                elif _fnmatch_any(path, _LOW_SENSITIVITY_PATTERNS):
                    scores.append(0.1)
                else:
                    scores.append(0.5)
            score = max(scores)  # worst-case file drives the score

        return RiskFactor(
            name="file_sensitivity",
            weight=self.factor_weights["file_sensitivity"],
            score=score,
            description=f"Sensitivity of {len(intent.target_files)} target file(s)",
        )

    def _blast_radius_factor(self, intent: IntentDeclaration) -> RiskFactor:
        """Score based on the number of services affected."""
        n_services = len(intent.target_services)
        if n_services == 0:
            score = 0.2
        elif n_services == 1:
            score = 0.3
        elif n_services <= 3:
            score = 0.6
        else:
            score = min(0.5 + n_services * 0.1, 1.0)

        return RiskFactor(
            name="blast_radius",
            weight=self.factor_weights["blast_radius"],
            score=score,
            description=f"Blast radius: {n_services} service(s) affected",
        )

    def _agent_trust_factor(self, profile: AgentProfile) -> RiskFactor:
        """Lower trust => higher risk score (inverted)."""
        score = 1.0 - profile.trust_score
        return RiskFactor(
            name="agent_trust",
            weight=self.factor_weights["agent_trust"],
            score=round(score, 4),
            description=f"Agent trust score: {profile.trust_score:.2f} (inverted for risk)",
        )

    def _validation_confidence_factor(
        self, verdict: ValidationVerdict
    ) -> RiskFactor:
        """Lower validation confidence => higher risk."""
        if not verdict.signals:
            confidence = 0.0
        else:
            confidence = sum(s.confidence for s in verdict.signals) / len(
                verdict.signals
            )

        # Invert: low confidence = high risk
        score = 1.0 - confidence
        if not verdict.overall_passed:
            score = max(score, 0.8)  # failing validation is always high risk

        return RiskFactor(
            name="validation_confidence",
            weight=self.factor_weights["validation_confidence"],
            score=round(score, 4),
            description=f"Validation confidence: {confidence:.2f} (passed={verdict.overall_passed})",
        )

    def _time_of_day_factor(
        self, *, now: datetime | None = None
    ) -> RiskFactor:
        """Deploys outside business hours carry higher risk."""
        now = now or datetime.now(timezone.utc)
        hour = now.hour

        # Business hours: 09-17 UTC — low risk
        # Evening (17-22) — moderate
        # Night (22-06) — high risk
        # Early morning (06-09) — moderate
        if 9 <= hour < 17:
            score = 0.1
        elif 6 <= hour < 9 or 17 <= hour < 22:
            score = 0.4
        else:
            score = 0.8

        return RiskFactor(
            name="time_of_day",
            weight=self.factor_weights["time_of_day"],
            score=score,
            description=f"Time-of-day risk (UTC hour={hour})",
        )

    # ------------------------------------------------------------------
    # Aggregation helpers
    # ------------------------------------------------------------------

    def _weighted_score(self, factors: list[RiskFactor]) -> float:
        total_weight = sum(f.weight for f in factors)
        if total_weight == 0:
            return 0.0
        raw = sum(f.weight * f.score for f in factors) / total_weight
        return min(max(raw, 0.0), 1.0)

    def _score_to_level(self, score: float) -> RiskLevel:
        # Evaluate from highest threshold down
        for level in (
            RiskLevel.CRITICAL,
            RiskLevel.HIGH,
            RiskLevel.MEDIUM,
            RiskLevel.LOW,
        ):
            if score >= self.risk_thresholds[level]:
                return level
        return RiskLevel.LOW
