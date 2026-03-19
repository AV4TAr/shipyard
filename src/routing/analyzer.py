"""TaskAnalyzer — extracts routing requirements from an AgentTask."""

from __future__ import annotations

import os

from src.goals.models import AgentTask

from .models import AgentCapability, TaskComplexity, TaskRequirements


# Keyword → capability mapping.  Keys are checked against lowercased description.
_CAPABILITY_KEYWORDS: dict[AgentCapability, list[str]] = {
    AgentCapability.FRONTEND: ["frontend", "ui", "css", "react", "component"],
    AgentCapability.BACKEND: ["api", "endpoint", "backend", "server", "database"],
    AgentCapability.DATA: ["data", "migration", "schema", "etl", "pipeline"],
    AgentCapability.SECURITY: ["security", "auth", "encryption", "vulnerability"],
    AgentCapability.MOBILE: ["mobile", "ios", "android", "app"],
    AgentCapability.QA: ["test", "qa", "e2e", "coverage"],
    AgentCapability.DEVOPS: ["deploy", "docker", "k8s", "infra", "ci"],
    AgentCapability.DOCUMENTATION: ["docs", "readme", "documentation"],
    AgentCapability.ARCHITECTURE: ["architecture", "design", "module", "structure"],
    AgentCapability.DESIGN: ["design", "layout", "wireframe", "spec"],
}

# File extension → language mapping.
_EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".c": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cs": "csharp",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".php": "php",
}

# Framework detection: keyword in description or file path → framework name.
_FRAMEWORK_KEYWORDS: dict[str, str] = {
    "fastapi": "fastapi",
    "django": "django",
    "flask": "flask",
    "react": "react",
    "next": "nextjs",
    "nextjs": "nextjs",
    "vue": "vue",
    "angular": "angular",
    "spring": "spring",
    "express": "express",
    "rails": "rails",
    "gin": "gin",
}


class TaskAnalyzer:
    """Analyzes an :class:`AgentTask` to extract :class:`TaskRequirements`."""

    def analyze(self, task: AgentTask) -> TaskRequirements:
        """Extract routing requirements from *task*."""
        description_lower = task.description.lower()
        title_lower = task.title.lower()
        text = f"{description_lower} {title_lower}"

        required_caps = self._infer_capabilities(text)
        preferred_caps = self._infer_preferred_capabilities(text, required_caps)
        languages = self._infer_languages(task.target_files)
        frameworks = self._infer_frameworks(text, task.target_files)
        complexity = self._estimate_complexity(task)

        return TaskRequirements(
            required_capabilities=required_caps,
            preferred_capabilities=preferred_caps,
            required_languages=languages,
            required_frameworks=frameworks,
            risk_level=task.estimated_risk,
            estimated_complexity=complexity,
        )

    # ------------------------------------------------------------------
    # Inference helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _infer_capabilities(text: str) -> list[AgentCapability]:
        """Identify required capabilities from description keywords."""
        found: list[AgentCapability] = []
        for cap, keywords in _CAPABILITY_KEYWORDS.items():
            for kw in keywords:
                if kw in text:
                    found.append(cap)
                    break
        return found

    @staticmethod
    def _infer_preferred_capabilities(
        text: str, required: list[AgentCapability]
    ) -> list[AgentCapability]:
        """Weaker signals that don't rise to 'required'."""
        # For now, preferred = empty; extensions welcome.
        return []

    @staticmethod
    def _infer_languages(target_files: list[str]) -> list[str]:
        """Infer programming languages from file extensions."""
        languages: set[str] = set()
        for fp in target_files:
            ext = os.path.splitext(fp)[1].lower()
            lang = _EXTENSION_TO_LANGUAGE.get(ext)
            if lang:
                languages.add(lang)
        return sorted(languages)

    @staticmethod
    def _infer_frameworks(text: str, target_files: list[str]) -> list[str]:
        """Detect frameworks from description text and file paths."""
        combined = text + " " + " ".join(target_files).lower()
        found: set[str] = set()
        for keyword, framework in _FRAMEWORK_KEYWORDS.items():
            if keyword in combined:
                found.add(framework)
        return sorted(found)

    @staticmethod
    def _estimate_complexity(task: AgentTask) -> TaskComplexity:
        """Estimate task complexity from file count and service count."""
        n_files = len(task.target_files)
        n_services = len(task.target_services)
        total = n_files + n_services

        if total == 0:
            return TaskComplexity.SIMPLE
        if total <= 2:
            return TaskComplexity.TRIVIAL
        if total <= 5:
            return TaskComplexity.SIMPLE
        if total <= 10:
            return TaskComplexity.MODERATE
        return TaskComplexity.COMPLEX
