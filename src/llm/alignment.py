"""LLM-powered intent alignment checking."""

from __future__ import annotations

import json
import logging

from pydantic import BaseModel, Field

from src.intent.schema import IntentDeclaration
from src.llm.client import LLMClient

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a code review safety checker for an AI-native CI/CD system. Your job is
to determine whether an agent's actual code changes (diff) align with what it
declared it would do (intent).

Analyze the declared intent and the actual diff. Determine:
1. Whether the changes align with the declared intent.
2. Your confidence level (0.0 to 1.0).
3. A brief explanation.
4. Any concerns (undeclared file modifications, scope creep, suspicious patterns).

Respond with a JSON object (no markdown fences):
{
  "aligned": true,
  "confidence": 0.95,
  "explanation": "The changes match the declared intent.",
  "concerns": []
}
"""


class AlignmentResult(BaseModel):
    """Result of checking intent alignment."""

    aligned: bool
    confidence: float  # 0.0 to 1.0
    explanation: str
    concerns: list[str] = Field(default_factory=list)


class LLMAlignmentChecker:
    """Checks if an agent's actual changes align with its declared intent."""

    def __init__(self, client: LLMClient | None = None):
        self._client = client or LLMClient()

    def check(self, intent: IntentDeclaration, diff: str) -> AlignmentResult:
        """Compare declared intent vs actual diff."""
        user_prompt = self._build_user_prompt(intent, diff)
        raw = self._client.complete(_SYSTEM_PROMPT, user_prompt)

        # Strip markdown fences if present
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        data = json.loads(text)
        return AlignmentResult(
            aligned=data.get("aligned", False),
            confidence=float(data.get("confidence", 0.0)),
            explanation=data.get("explanation", ""),
            concerns=data.get("concerns", []),
        )

    def _build_user_prompt(self, intent: IntentDeclaration, diff: str) -> str:
        parts = [
            "## Declared Intent",
            f"Agent: {intent.agent_id}",
            f"Description: {intent.description}",
            f"Rationale: {intent.rationale}",
            f"Target files: {', '.join(intent.target_files)}",
        ]
        if intent.target_services:
            parts.append(f"Target services: {', '.join(intent.target_services)}")
        parts.extend([
            "",
            "## Actual Diff",
            diff,
        ])
        return "\n".join(parts)
