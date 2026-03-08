"""LLM-powered semantic merge analysis."""

from __future__ import annotations

import json
import logging

from pydantic import BaseModel, Field

from src.llm.client import LLMClient

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a merge conflict analyzer for an AI-native CI/CD system. Your job is to
determine whether two concurrent code changes from different agents can be safely
merged together without semantic conflicts.

Two changes may modify different files but still conflict semantically (e.g., one
changes an API contract that the other depends on). Conversely, two changes to the
same file may be compatible if they touch independent functions.

Analyze both diffs and determine:
1. Whether the changes are semantically compatible.
2. Your confidence level (0.0 to 1.0).
3. A brief explanation.
4. Specific conflicts found (if any).

Respond with a JSON object (no markdown fences):
{
  "compatible": true,
  "confidence": 0.9,
  "explanation": "The changes are independent.",
  "conflicts": []
}
"""


class MergeAnalysis(BaseModel):
    """Result of semantic merge analysis."""

    compatible: bool
    confidence: float  # 0.0 to 1.0
    explanation: str
    conflicts: list[str] = Field(default_factory=list)


class LLMMergeAnalyzer:
    """Analyzes if concurrent changes from different agents conflict semantically."""

    def __init__(self, client: LLMClient | None = None):
        self._client = client or LLMClient()

    def analyze(
        self, diff_a: str, diff_b: str, context: str = ""
    ) -> MergeAnalysis:
        """Analyze two diffs for semantic merge conflicts."""
        user_prompt = self._build_user_prompt(diff_a, diff_b, context)
        raw = self._client.complete(_SYSTEM_PROMPT, user_prompt)

        # Strip markdown fences if present
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        data = json.loads(text)
        return MergeAnalysis(
            compatible=data.get("compatible", False),
            confidence=float(data.get("confidence", 0.0)),
            explanation=data.get("explanation", ""),
            conflicts=data.get("conflicts", []),
        )

    def _build_user_prompt(
        self, diff_a: str, diff_b: str, context: str
    ) -> str:
        parts = [
            "## Change A",
            diff_a,
            "",
            "## Change B",
            diff_b,
        ]
        if context:
            parts.extend(["", "## Additional Context", context])
        return "\n".join(parts)
