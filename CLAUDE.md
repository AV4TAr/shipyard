# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Shipyard is an AI-native CI/CD pipeline where humans are "air traffic controllers" (set goals and constraints) and agents autonomously implement, test, and deploy code. Every change flows through a 5-stage pipeline regardless of which agent made it.

## Commands

```bash
# Run all tests (806 tests)
python3 -m pytest tests/

# Run a single test file
python3 -m pytest tests/test_pipeline.py

# Run a specific test
python3 -m pytest tests/test_pipeline.py::test_function_name -v

# Lint
python3 -m ruff check src/ tests/

# Run the CLI
python -m src goal create --title "..." --description "..." --priority high
python -m src status

# Run the server
SHIPYARD_DB_PATH=data/shipyard.db python3 -m uvicorn src.api.app:create_app --factory --host 0.0.0.0 --port 8001
```

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `SHIPYARD_DB_PATH` | SQLite database path (e.g. `data/shipyard.db`). Omit for in-memory. |
| `OPENROUTER_API_KEY` | Enables LLM-powered goal decomposition and intent alignment |
| `OPENSANDBOX_SERVER_URL` | URL of an OpenSandbox server for real sandboxed execution |

## Architecture

### Pipeline Flow (5 Stages)

Every agent change goes through: **INTENT → SANDBOX → VALIDATION → TRUST_ROUTING → DEPLOY**

- **Intent**: Validate agent's declared scope before any code runs
- **Sandbox**: Ephemeral execution environment (currently simulated, needs Docker/K8s)
- **Validation**: 5 parallel signals — static analysis, behavioral diff, intent alignment, resource bounds, security scan (security_scan has 2x weight)
- **Trust Routing**: Risk score determines deploy route — auto-deploy / agent-review / human-approval / canary
- **Deploy**: Execute the routed action

Orchestrator: `src/pipeline/orchestrator.py`. Failures at any stage halt the pipeline and return structured feedback.

### 18 Modules

| Module | Role |
|--------|------|
| `goals/` | Human expresses WHAT to build; `manager.py` handles lifecycle, `decomposer.py` breaks goals into agent tasks |
| `constraints/` | Inviolable architectural rules (the "constitution"); loaded from `configs/constraints.yaml` |
| `routing/` | Selects best agent via weighted scoring (capability 0.35, language 0.20, framework 0.15, trust 0.20, load 0.10); hospital model where generic agent is the ER fallback |
| `intent/` | Agent declares what it plans to change; validated against scope constraints before execution |
| `sandbox/` | Ephemeral execution environment (simulated + OpenSandbox backend) |
| `validation/` | Multi-signal gate in `gate.py` orchestrating 5 real signal runners: static analysis (ruff), security scan (bandit), behavioral diff (worktree test diff), resource bounds (file sizes), intent alignment (LLM) |
| `trust/` | `scorer.py` computes risk from file sensitivity, blast radius, agent trust, validation confidence; `tracker.py` maintains agent profiles |
| `coordination/` | Code-area claims (locks), semantic merge checking, deploy queue |
| `pipeline/` | End-to-end orchestrator tying all stages together; `feedback.py` formats machine-readable feedback for agents |
| `cli/` | Human operator interface via argparse in `app.py` |
| `api/` | FastAPI backend + Command Center SPA (8 tabs). Config editor, kill switch, WebSocket. |
| `storage/` | Repository pattern with memory + SQLite backends. JSON-blob storage with indexed columns. |
| `llm/` | OpenRouter client, LLM goal decomposer, intent alignment checker, semantic merge analyzer |
| `sdk/` | Agent protocol, client SDK with auto-heartbeat, workspace file ops, phase tracking |
| `notifications/` | Event dispatcher with webhook + Slack channels. HMAC signing. |
| `projects/` | Project → Milestone → Goal hierarchy. Full lifecycle, auto-cascade. |
| `leases/` | Time-bound task claims with heartbeat renewal. Background asyncio sweep auto-resets expired leases. |
| `worktrees/` | Git worktree isolation per task. Agents write real files. Approved changes merge to main. |

### Key Design Patterns

- **Pydantic v2 models** throughout for type safety and serialization
- **Dual-write storage** — memory + SQLite via repository pattern (`SHIPYARD_DB_PATH` env var)
- **Lease-based concurrency** — agents heartbeat to hold task claims; expired leases auto-reset
- **Git worktree isolation** — each task gets its own branch; approved changes merge to main
- **Structured feedback** — every failure returns data agents can parse, not human-readable strings
- **Trust is domain-specific** — trusted for frontend ≠ trusted for auth

### Configuration

- `configs/default.yaml` — risk thresholds, signal weights, trust parameters, sandbox limits
- `configs/constraints.yaml` — 13 architectural constraints with severity levels (MUST/SHOULD/PREFER)

### What's Simulated (Not Yet Real)

- Sandbox execution — simulated by default; set `OPENSANDBOX_SERVER_URL` for real sandboxed execution
- Post-deploy monitoring — anomaly detection logic exists but not connected to real metrics

### What's Real

- All 5 validation signals: static analysis (ruff), security scan (bandit), behavioral diff (worktree test diff), resource bounds (file sizes), intent alignment (LLM)
- Git worktree code workflow: agents write real files, pipeline validates real code, approved changes merge to main
- Lease-based task claims with heartbeat renewal and auto-expiry
- Agent phase tracking and kill switch (freeze, ban, pause, revoke)
- SQLite persistence, LLM decomposition (OpenRouter), webhook notifications
- Config editor UI for pipeline thresholds, signal weights, deploy routes, constraints

## Code Conventions

- Python >=3.11, line length 100 (ruff)
- One test file per module in `tests/`
- Enums for all state machines: `GoalStatus`, `TaskStatus`, `PipelineStatus`, `RiskLevel`, `DeployRoute`
- Risk scoring weights and thresholds live in `configs/default.yaml`, not hardcoded
