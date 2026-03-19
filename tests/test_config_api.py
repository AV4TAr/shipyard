"""Tests for the Config Editor API endpoints."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from src.api.app import create_app

# We operate on the real configs/ dir, so save and restore originals around tests.
_CONFIGS_DIR = Path(__file__).resolve().parents[1] / "configs"
_DEFAULT_YAML = _CONFIGS_DIR / "default.yaml"
_CONSTRAINTS_YAML = _CONFIGS_DIR / "constraints.yaml"


@pytest.fixture(autouse=True)
def _preserve_config_files():
    """Save original config files and restore them after each test."""
    default_backup = _DEFAULT_YAML.read_text() if _DEFAULT_YAML.exists() else None
    constraints_backup = (
        _CONSTRAINTS_YAML.read_text() if _CONSTRAINTS_YAML.exists() else None
    )
    yield
    if default_backup is not None:
        _DEFAULT_YAML.write_text(default_backup)
    if constraints_backup is not None:
        _CONSTRAINTS_YAML.write_text(constraints_backup)


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


@pytest.fixture
def valid_config():
    """Load and return the current default config as a dict."""
    with open(_DEFAULT_YAML) as f:
        return yaml.safe_load(f)


@pytest.fixture
def valid_constraints():
    """Load and return the current constraints config as a dict."""
    with open(_CONSTRAINTS_YAML) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# GET /api/config
# ---------------------------------------------------------------------------


class TestGetConfig:
    def test_returns_valid_json(self, client):
        resp = client.get("/api/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "pipeline" in data
        assert "risk_thresholds" in data
        assert "signal_weights" in data
        assert "risk_factor_weights" in data
        assert "deploy_routes" in data
        assert "trust" in data
        assert "sandbox" in data
        assert "leases" in data
        assert "monitoring" in data

    def test_matches_yaml_structure(self, client):
        resp = client.get("/api/config")
        data = resp.json()
        with open(_DEFAULT_YAML) as f:
            on_disk = yaml.safe_load(f)
        assert data == on_disk


# ---------------------------------------------------------------------------
# PUT /api/config — valid
# ---------------------------------------------------------------------------


class TestPutConfigValid:
    def test_valid_update_succeeds(self, client, valid_config):
        cfg = copy.deepcopy(valid_config)
        cfg["pipeline"]["max_sandbox_iterations"] = 10
        resp = client.put("/api/config", json=cfg)
        assert resp.status_code == 200
        assert resp.json()["pipeline"]["max_sandbox_iterations"] == 10

    def test_saved_config_is_readable(self, client, valid_config):
        """Write and then read back — should round-trip cleanly."""
        cfg = copy.deepcopy(valid_config)
        cfg["risk_thresholds"]["critical"] = 0.90
        client.put("/api/config", json=cfg)

        resp = client.get("/api/config")
        assert resp.status_code == 200
        assert resp.json()["risk_thresholds"]["critical"] == 0.90

    def test_yaml_file_not_corrupted(self, client, valid_config):
        """After a write, the YAML file should still be parseable."""
        cfg = copy.deepcopy(valid_config)
        cfg["trust"]["baseline_trust"] = 0.7
        client.put("/api/config", json=cfg)

        with open(_DEFAULT_YAML) as f:
            on_disk = yaml.safe_load(f)
        assert on_disk["trust"]["baseline_trust"] == 0.7
        assert "pipeline" in on_disk  # Not corrupted


# ---------------------------------------------------------------------------
# PUT /api/config — invalid
# ---------------------------------------------------------------------------


class TestPutConfigInvalid:
    def test_missing_required_section(self, client):
        resp = client.put("/api/config", json={"pipeline": {}})
        assert resp.status_code == 400
        assert "Missing required sections" in resp.json()["detail"]

    def test_negative_threshold(self, client, valid_config):
        cfg = copy.deepcopy(valid_config)
        cfg["risk_thresholds"]["critical"] = -0.5
        resp = client.put("/api/config", json=cfg)
        assert resp.status_code == 400
        assert "risk_thresholds.critical" in resp.json()["detail"]

    def test_threshold_above_one(self, client, valid_config):
        cfg = copy.deepcopy(valid_config)
        cfg["risk_thresholds"]["high"] = 1.5
        resp = client.put("/api/config", json=cfg)
        assert resp.status_code == 400

    def test_signal_weight_zero(self, client, valid_config):
        cfg = copy.deepcopy(valid_config)
        cfg["signal_weights"]["security_scan"] = 0
        resp = client.put("/api/config", json=cfg)
        assert resp.status_code == 400
        assert "signal_weights.security_scan" in resp.json()["detail"]

    def test_signal_weight_too_large(self, client, valid_config):
        cfg = copy.deepcopy(valid_config)
        cfg["signal_weights"]["security_scan"] = 200
        resp = client.put("/api/config", json=cfg)
        assert resp.status_code == 400

    def test_invalid_deploy_route(self, client, valid_config):
        cfg = copy.deepcopy(valid_config)
        cfg["deploy_routes"]["low"] = "yolo_deploy"
        resp = client.put("/api/config", json=cfg)
        assert resp.status_code == 400
        assert "deploy_routes.low" in resp.json()["detail"]

    def test_negative_sandbox_iterations(self, client, valid_config):
        cfg = copy.deepcopy(valid_config)
        cfg["pipeline"]["max_sandbox_iterations"] = -1
        resp = client.put("/api/config", json=cfg)
        assert resp.status_code == 400

    def test_baseline_trust_above_one(self, client, valid_config):
        cfg = copy.deepcopy(valid_config)
        cfg["trust"]["baseline_trust"] = 5.0
        resp = client.put("/api/config", json=cfg)
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/config/constraints
# ---------------------------------------------------------------------------


class TestGetConstraints:
    def test_returns_constraints(self, client):
        resp = client.get("/api/config/constraints")
        assert resp.status_code == 200
        data = resp.json()
        assert "constraints" in data
        assert isinstance(data["constraints"], list)
        assert len(data["constraints"]) > 0

    def test_constraint_has_expected_fields(self, client):
        resp = client.get("/api/config/constraints")
        c = resp.json()["constraints"][0]
        assert "constraint_id" in c
        assert "severity" in c
        assert "rule" in c


# ---------------------------------------------------------------------------
# PUT /api/config/constraints
# ---------------------------------------------------------------------------


class TestPutConstraints:
    def test_valid_update(self, client, valid_constraints):
        data = copy.deepcopy(valid_constraints)
        data["constraints"][0]["severity"] = "should"
        resp = client.put("/api/config/constraints", json=data)
        assert resp.status_code == 200
        assert resp.json()["constraints"][0]["severity"] == "should"

    def test_missing_constraints_key(self, client):
        resp = client.put("/api/config/constraints", json={"name": "test"})
        assert resp.status_code == 400
        assert "Missing 'constraints' key" in resp.json()["detail"]

    def test_invalid_severity(self, client, valid_constraints):
        data = copy.deepcopy(valid_constraints)
        data["constraints"][0]["severity"] = "yolo"
        resp = client.put("/api/config/constraints", json=data)
        assert resp.status_code == 400

    def test_round_trip(self, client, valid_constraints):
        """Write and read back constraints — should match."""
        data = copy.deepcopy(valid_constraints)
        data["description"] = "Updated description"
        client.put("/api/config/constraints", json=data)

        resp = client.get("/api/config/constraints")
        assert resp.status_code == 200
        assert resp.json()["description"] == "Updated description"
