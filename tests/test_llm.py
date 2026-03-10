"""Tests for the LLM integration module.

All tests mock HTTP calls — no real API key required.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.goals.manager import GoalManager
from src.goals.models import Goal, GoalPriority, TaskBreakdown
from src.intent.schema import IntentDeclaration
from src.llm.alignment import AlignmentResult, LLMAlignmentChecker
from src.llm.client import LLMClient
from src.llm.decomposer import LLMGoalDecomposer
from src.llm.merge_analyzer import LLMMergeAnalyzer, MergeAnalysis

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_urlopen_response(body: dict) -> MagicMock:
    """Create a mock that behaves like urllib.request.urlopen's return value."""
    raw = json.dumps(body).encode("utf-8")
    mock_resp = MagicMock()
    mock_resp.read.return_value = raw
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


def _openrouter_response(content: str) -> dict:
    """Wrap content in an OpenRouter-style response envelope."""
    return {
        "choices": [{"message": {"content": content}}],
    }


def _sample_goal() -> Goal:
    return Goal(
        title="Add user auth",
        description="Implement JWT authentication for the API",
        constraints=["Must use HTTPS", "No plaintext passwords"],
        acceptance_criteria=["Users can log in", "Tokens expire after 1h"],
        priority=GoalPriority.HIGH,
        target_services=["auth-service"],
        target_paths=["src/auth/"],
    )


def _sample_intent() -> IntentDeclaration:
    return IntentDeclaration(
        agent_id="agent-1",
        description="Add login endpoint",
        rationale="Required for user authentication",
        target_files=["src/auth/login.py"],
        target_services=["auth-service"],
    )


# ---------------------------------------------------------------------------
# LLMClient tests
# ---------------------------------------------------------------------------

class TestLLMClient:
    """Tests for the OpenRouter client wrapper."""

    def test_raises_when_no_api_key(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        with pytest.raises(ValueError, match="API key required"):
            LLMClient()

    def test_uses_env_var(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-123")
        client = LLMClient()
        assert client._api_key == "test-key-123"

    def test_explicit_key_overrides_env(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
        client = LLMClient(api_key="explicit-key")
        assert client._api_key == "explicit-key"

    @patch("src.llm.client.urllib.request.urlopen")
    def test_complete_sends_correct_request(self, mock_urlopen, monkeypatch):
        mock_urlopen.return_value = _make_urlopen_response(
            _openrouter_response("Hello!")
        )

        client = LLMClient(api_key="test-key")
        result = client.complete("system prompt", "user prompt")

        assert result == "Hello!"

        # Verify the request was constructed properly
        call_args = mock_urlopen.call_args
        request = call_args[0][0]
        assert request.full_url == LLMClient.BASE_URL
        assert request.get_header("Authorization") == "Bearer test-key"
        assert request.get_header("Content-type") == "application/json"

        body = json.loads(request.data.decode("utf-8"))
        assert body["model"] == "anthropic/claude-sonnet-4-20250514"
        assert len(body["messages"]) == 2
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][0]["content"] == "system prompt"
        assert body["messages"][1]["role"] == "user"
        assert body["messages"][1]["content"] == "user prompt"
        assert body["temperature"] == 0.3
        assert body["max_tokens"] == 2000

    @patch("src.llm.client.urllib.request.urlopen")
    def test_complete_custom_params(self, mock_urlopen):
        mock_urlopen.return_value = _make_urlopen_response(
            _openrouter_response("response")
        )

        client = LLMClient(api_key="k", model="openai/gpt-4o")
        client.complete("sys", "usr", temperature=0.7, max_tokens=500)

        body = json.loads(mock_urlopen.call_args[0][0].data.decode("utf-8"))
        assert body["model"] == "openai/gpt-4o"
        assert body["temperature"] == 0.7
        assert body["max_tokens"] == 500


# ---------------------------------------------------------------------------
# LLMGoalDecomposer tests
# ---------------------------------------------------------------------------

class TestLLMGoalDecomposer:
    """Tests for LLM-powered goal decomposition."""

    def test_parses_llm_response_into_task_breakdown(self):
        llm_response = json.dumps({
            "tasks": [
                {
                    "title": "Implement: JWT auth",
                    "description": "Add JWT token generation and validation",
                    "target_files": ["src/auth/jwt.py"],
                    "target_services": ["auth-service"],
                    "constraints": ["Use HTTPS"],
                    "depends_on_indices": [],
                    "estimated_risk": "high",
                },
                {
                    "title": "Test: JWT auth",
                    "description": "Write tests for JWT authentication",
                    "target_files": ["tests/test_auth.py"],
                    "target_services": [],
                    "constraints": [],
                    "depends_on_indices": [0],
                    "estimated_risk": "low",
                },
            ]
        })

        mock_client = MagicMock()
        mock_client.complete.return_value = llm_response

        decomposer = LLMGoalDecomposer(client=mock_client)
        goal = _sample_goal()
        breakdown = decomposer.decompose(goal)

        assert isinstance(breakdown, TaskBreakdown)
        assert breakdown.goal_id == goal.goal_id
        assert len(breakdown.tasks) == 2
        assert breakdown.tasks[0].title == "Implement: JWT auth"
        assert breakdown.tasks[0].estimated_risk.value == "high"
        assert breakdown.tasks[1].depends_on == [breakdown.tasks[0].task_id]

    def test_falls_back_on_llm_failure(self):
        mock_client = MagicMock()
        mock_client.complete.side_effect = RuntimeError("API down")

        decomposer = LLMGoalDecomposer(client=mock_client)
        goal = _sample_goal()
        breakdown = decomposer.decompose(goal)

        # Should still return a valid breakdown from the fallback
        assert isinstance(breakdown, TaskBreakdown)
        assert breakdown.goal_id == goal.goal_id
        assert len(breakdown.tasks) >= 2  # At least impl + test

    def test_falls_back_on_invalid_json(self):
        mock_client = MagicMock()
        mock_client.complete.return_value = "not valid json {{"

        decomposer = LLMGoalDecomposer(client=mock_client)
        goal = _sample_goal()
        breakdown = decomposer.decompose(goal)

        assert isinstance(breakdown, TaskBreakdown)
        assert len(breakdown.tasks) >= 2

    def test_strips_markdown_fences(self):
        inner = json.dumps({
            "tasks": [
                {
                    "title": "Implement: feature",
                    "description": "Do the thing",
                    "target_files": [],
                    "target_services": [],
                    "constraints": [],
                    "depends_on_indices": [],
                    "estimated_risk": "low",
                },
            ]
        })
        llm_response = f"```json\n{inner}\n```"

        mock_client = MagicMock()
        mock_client.complete.return_value = llm_response

        decomposer = LLMGoalDecomposer(client=mock_client)
        breakdown = decomposer.decompose(_sample_goal())

        assert len(breakdown.tasks) == 1
        assert breakdown.tasks[0].title == "Implement: feature"

    def test_handles_empty_tasks_list(self):
        mock_client = MagicMock()
        mock_client.complete.return_value = json.dumps({"tasks": []})

        decomposer = LLMGoalDecomposer(client=mock_client)
        breakdown = decomposer.decompose(_sample_goal())

        # Empty tasks triggers fallback
        assert isinstance(breakdown, TaskBreakdown)
        assert len(breakdown.tasks) >= 2


# ---------------------------------------------------------------------------
# LLMAlignmentChecker tests
# ---------------------------------------------------------------------------

class TestLLMAlignmentChecker:
    """Tests for LLM-powered intent alignment checking."""

    def test_returns_alignment_result(self):
        llm_response = json.dumps({
            "aligned": True,
            "confidence": 0.95,
            "explanation": "Changes match declared intent.",
            "concerns": [],
        })

        mock_client = MagicMock()
        mock_client.complete.return_value = llm_response

        checker = LLMAlignmentChecker(client=mock_client)
        result = checker.check(
            _sample_intent(),
            "diff --git a/src/auth/login.py\n+def login():\n+    pass",
        )

        assert isinstance(result, AlignmentResult)
        assert result.aligned is True
        assert result.confidence == 0.95
        assert result.explanation == "Changes match declared intent."
        assert result.concerns == []

    def test_returns_misaligned_result(self):
        llm_response = json.dumps({
            "aligned": False,
            "confidence": 0.8,
            "explanation": "Agent modified files outside declared scope.",
            "concerns": ["Modified src/billing/charge.py not in target_files"],
        })

        mock_client = MagicMock()
        mock_client.complete.return_value = llm_response

        checker = LLMAlignmentChecker(client=mock_client)
        result = checker.check(_sample_intent(), "some diff")

        assert result.aligned is False
        assert len(result.concerns) == 1

    def test_strips_markdown_fences(self):
        inner = json.dumps({
            "aligned": True,
            "confidence": 0.9,
            "explanation": "OK",
            "concerns": [],
        })

        mock_client = MagicMock()
        mock_client.complete.return_value = f"```json\n{inner}\n```"

        checker = LLMAlignmentChecker(client=mock_client)
        result = checker.check(_sample_intent(), "diff")

        assert result.aligned is True


# ---------------------------------------------------------------------------
# LLMMergeAnalyzer tests
# ---------------------------------------------------------------------------

class TestLLMMergeAnalyzer:
    """Tests for LLM-powered semantic merge analysis."""

    def test_returns_compatible_analysis(self):
        llm_response = json.dumps({
            "compatible": True,
            "confidence": 0.9,
            "explanation": "Changes are independent.",
            "conflicts": [],
        })

        mock_client = MagicMock()
        mock_client.complete.return_value = llm_response

        analyzer = LLMMergeAnalyzer(client=mock_client)
        result = analyzer.analyze("diff A", "diff B")

        assert isinstance(result, MergeAnalysis)
        assert result.compatible is True
        assert result.confidence == 0.9
        assert result.conflicts == []

    def test_returns_incompatible_analysis(self):
        llm_response = json.dumps({
            "compatible": False,
            "confidence": 0.85,
            "explanation": "Both changes modify the User model.",
            "conflicts": ["Conflicting changes to User.email field"],
        })

        mock_client = MagicMock()
        mock_client.complete.return_value = llm_response

        analyzer = LLMMergeAnalyzer(client=mock_client)
        result = analyzer.analyze("diff A", "diff B", context="User model")

        assert result.compatible is False
        assert len(result.conflicts) == 1

    def test_with_context(self):
        mock_client = MagicMock()
        mock_client.complete.return_value = json.dumps({
            "compatible": True,
            "confidence": 0.7,
            "explanation": "OK",
            "conflicts": [],
        })

        analyzer = LLMMergeAnalyzer(client=mock_client)
        analyzer.analyze("diff A", "diff B", context="shared service")

        # Verify context was included in the prompt
        call_args = mock_client.complete.call_args
        user_prompt = call_args[0][1]
        assert "shared service" in user_prompt


# ---------------------------------------------------------------------------
# Model serialization tests
# ---------------------------------------------------------------------------

class TestModelSerialization:
    """Tests that Pydantic models serialize and deserialize correctly."""

    def test_alignment_result_roundtrip(self):
        result = AlignmentResult(
            aligned=True,
            confidence=0.95,
            explanation="All good",
            concerns=["minor concern"],
        )
        data = result.model_dump()
        restored = AlignmentResult.model_validate(data)
        assert restored == result

    def test_alignment_result_json_roundtrip(self):
        result = AlignmentResult(
            aligned=False,
            confidence=0.5,
            explanation="Issues found",
            concerns=["scope creep", "undeclared files"],
        )
        json_str = result.model_dump_json()
        restored = AlignmentResult.model_validate_json(json_str)
        assert restored == result

    def test_merge_analysis_roundtrip(self):
        analysis = MergeAnalysis(
            compatible=False,
            confidence=0.8,
            explanation="Conflicts detected",
            conflicts=["API contract change"],
        )
        data = analysis.model_dump()
        restored = MergeAnalysis.model_validate(data)
        assert restored == analysis

    def test_merge_analysis_json_roundtrip(self):
        analysis = MergeAnalysis(
            compatible=True,
            confidence=1.0,
            explanation="Independent",
            conflicts=[],
        )
        json_str = analysis.model_dump_json()
        restored = MergeAnalysis.model_validate_json(json_str)
        assert restored == analysis

    def test_alignment_result_defaults(self):
        result = AlignmentResult(
            aligned=True, confidence=0.9, explanation="Fine"
        )
        assert result.concerns == []

    def test_merge_analysis_defaults(self):
        analysis = MergeAnalysis(
            compatible=True, confidence=0.9, explanation="OK"
        )
        assert analysis.conflicts == []


# ---------------------------------------------------------------------------
# CLIRuntime LLM decomposer wiring tests
# ---------------------------------------------------------------------------

class TestCLIRuntimeDecomposerWiring:
    """Tests that CLIRuntime.from_defaults() wires the correct decomposer."""

    def test_uses_llm_decomposer_when_api_key_set(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-for-wiring")
        from src.cli.runtime import CLIRuntime

        runtime = CLIRuntime.from_defaults()
        assert isinstance(runtime.goal_manager._decomposer, LLMGoalDecomposer)

    def test_falls_back_to_rule_based_when_no_api_key(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        from src.cli.runtime import CLIRuntime
        from src.goals.decomposer import GoalDecomposer

        runtime = CLIRuntime.from_defaults()
        assert isinstance(runtime.goal_manager._decomposer, GoalDecomposer)
        assert not isinstance(runtime.goal_manager._decomposer, LLMGoalDecomposer)

    def test_llm_decomposer_works_with_goal_manager_activate(self):
        """LLMGoalDecomposer integrates with GoalManager.activate() (mocked LLM)."""
        llm_response = json.dumps({
            "tasks": [
                {
                    "title": "Implement: feature X",
                    "description": "Build feature X",
                    "target_files": ["src/x.py"],
                    "target_services": [],
                    "constraints": [],
                    "depends_on_indices": [],
                    "estimated_risk": "medium",
                },
                {
                    "title": "Test: feature X",
                    "description": "Test feature X",
                    "target_files": ["tests/test_x.py"],
                    "target_services": [],
                    "constraints": [],
                    "depends_on_indices": [0],
                    "estimated_risk": "low",
                },
            ]
        })

        mock_client = MagicMock()
        mock_client.complete.return_value = llm_response

        decomposer = LLMGoalDecomposer(client=mock_client)
        manager = GoalManager(decomposer=decomposer)

        from src.goals.models import GoalInput, GoalPriority

        goal = manager.create(
            GoalInput(
                title="Feature X",
                description="Build feature X end to end",
                priority=GoalPriority.MEDIUM,
            ),
            created_by="test-user",
        )

        breakdown = manager.activate(goal.goal_id)
        assert len(breakdown.tasks) == 2
        assert breakdown.tasks[0].title == "Implement: feature X"
        assert breakdown.tasks[1].depends_on == [breakdown.tasks[0].task_id]
