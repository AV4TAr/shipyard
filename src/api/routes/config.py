"""Config editor API endpoints — read and write pipeline configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/config", tags=["config"])

_CONFIGS_DIR = Path(__file__).resolve().parents[3] / "configs"
_DEFAULT_YAML = _CONFIGS_DIR / "default.yaml"
_CONSTRAINTS_YAML = _CONFIGS_DIR / "constraints.yaml"

# ---- Required structure for default.yaml ----

_REQUIRED_TOP_KEYS = {
    "pipeline", "risk_thresholds", "signal_weights",
    "risk_factor_weights", "deploy_routes", "trust", "sandbox",
    "leases", "monitoring",
}

_VALID_DEPLOY_ROUTES = {
    "auto_deploy", "agent_review", "human_approval", "human_approval_canary",
}

_VALID_SEVERITIES = {"must", "should", "prefer"}


def _read_yaml(path: Path) -> dict:
    """Read a YAML file and return its contents as a dict."""
    with open(path, "r") as f:
        return yaml.safe_load(f) or {}


def _write_yaml(path: Path, data: dict) -> None:
    """Write a dict to a YAML file, preserving human-friendly formatting."""
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


def _validate_config(data: dict) -> list[str]:
    """Validate config data, returning a list of error messages (empty = valid)."""
    errors: list[str] = []

    # Check required top-level keys
    missing = _REQUIRED_TOP_KEYS - set(data.keys())
    if missing:
        errors.append(f"Missing required sections: {', '.join(sorted(missing))}")
        return errors  # Can't validate further without required sections

    # Pipeline section
    pipeline = data.get("pipeline", {})
    if not isinstance(pipeline, dict):
        errors.append("pipeline must be a mapping")
    else:
        if "max_sandbox_iterations" in pipeline:
            v = pipeline["max_sandbox_iterations"]
            if not isinstance(v, (int, float)) or v < 1:
                errors.append("pipeline.max_sandbox_iterations must be >= 1")
        if "sandbox_timeout" in pipeline:
            v = pipeline["sandbox_timeout"]
            if not isinstance(v, (int, float)) or v < 1:
                errors.append("pipeline.sandbox_timeout must be >= 1")

    # Risk thresholds (0-1 range)
    thresholds = data.get("risk_thresholds", {})
    if not isinstance(thresholds, dict):
        errors.append("risk_thresholds must be a mapping")
    else:
        for key in ("critical", "high", "medium", "low"):
            if key in thresholds:
                v = thresholds[key]
                if not isinstance(v, (int, float)) or v < 0 or v > 1:
                    errors.append(
                        f"risk_thresholds.{key} must be between 0 and 1"
                    )

    # Signal weights (must be > 0)
    sw = data.get("signal_weights", {})
    if not isinstance(sw, dict):
        errors.append("signal_weights must be a mapping")
    else:
        for key, v in sw.items():
            if not isinstance(v, (int, float)) or v <= 0:
                errors.append(f"signal_weights.{key} must be > 0")
            if isinstance(v, (int, float)) and v > 100:
                errors.append(f"signal_weights.{key} must be <= 100")

    # Risk factor weights (0-1 range)
    rfw = data.get("risk_factor_weights", {})
    if not isinstance(rfw, dict):
        errors.append("risk_factor_weights must be a mapping")
    else:
        for key, v in rfw.items():
            if not isinstance(v, (int, float)) or v < 0 or v > 1:
                errors.append(
                    f"risk_factor_weights.{key} must be between 0 and 1"
                )

    # Deploy routes — values must be valid strategies
    dr = data.get("deploy_routes", {})
    if not isinstance(dr, dict):
        errors.append("deploy_routes must be a mapping")
    else:
        for key, v in dr.items():
            if v not in _VALID_DEPLOY_ROUTES:
                errors.append(
                    f"deploy_routes.{key} must be one of: "
                    f"{', '.join(sorted(_VALID_DEPLOY_ROUTES))}"
                )

    # Trust section
    trust = data.get("trust", {})
    if not isinstance(trust, dict):
        errors.append("trust must be a mapping")
    else:
        if "baseline_trust" in trust:
            v = trust["baseline_trust"]
            if not isinstance(v, (int, float)) or v < 0 or v > 1:
                errors.append("trust.baseline_trust must be between 0 and 1")

    # Leases — all values must be positive
    leases = data.get("leases", {})
    if isinstance(leases, dict):
        for key, v in leases.items():
            if isinstance(v, (int, float)) and v < 0:
                errors.append(f"leases.{key} must be >= 0")

    # Monitoring
    monitoring = data.get("monitoring", {})
    if isinstance(monitoring, dict):
        if "error_rate_threshold" in monitoring:
            v = monitoring["error_rate_threshold"]
            if not isinstance(v, (int, float)) or v < 0 or v > 1:
                errors.append(
                    "monitoring.error_rate_threshold must be between 0 and 1"
                )

    return errors


def _validate_constraints(data: dict) -> list[str]:
    """Validate constraints data."""
    errors: list[str] = []

    if "constraints" not in data:
        errors.append("Missing 'constraints' key")
        return errors

    constraints = data["constraints"]
    if not isinstance(constraints, list):
        errors.append("'constraints' must be a list")
        return errors

    for i, c in enumerate(constraints):
        if not isinstance(c, dict):
            errors.append(f"constraints[{i}] must be a mapping")
            continue
        if "constraint_id" not in c:
            errors.append(f"constraints[{i}] missing 'constraint_id'")
        if "severity" in c:
            if c["severity"].lower() not in _VALID_SEVERITIES:
                errors.append(
                    f"constraints[{i}].severity must be one of: "
                    f"{', '.join(sorted(_VALID_SEVERITIES))}"
                )

    return errors


# ---- Endpoints ----

@router.get("")
def get_config() -> dict[str, Any]:
    """Return the current pipeline configuration."""
    try:
        return _read_yaml(_DEFAULT_YAML)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Config file not found")


@router.put("")
def update_config(body: dict[str, Any]) -> dict[str, Any]:
    """Validate and save pipeline configuration."""
    errors = _validate_config(body)
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))

    _write_yaml(_DEFAULT_YAML, body)

    # Read back to confirm
    return _read_yaml(_DEFAULT_YAML)


@router.get("/constraints")
def get_constraints() -> dict[str, Any]:
    """Return the current constraints configuration."""
    try:
        return _read_yaml(_CONSTRAINTS_YAML)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404, detail="Constraints file not found"
        )


@router.put("/constraints")
def update_constraints(body: dict[str, Any]) -> dict[str, Any]:
    """Validate and save constraints configuration."""
    errors = _validate_constraints(body)
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))

    _write_yaml(_CONSTRAINTS_YAML, body)

    # Read back to confirm
    return _read_yaml(_CONSTRAINTS_YAML)
