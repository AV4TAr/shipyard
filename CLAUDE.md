# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Shipyard is an AI-native CI/CD pipeline where humans are "air traffic controllers" (set goals and constraints) and agents autonomously implement, test, and deploy code. Every change flows through a 5-stage pipeline regardless of which agent made it.

## Commands

```bash
# Run all tests (632 tests)
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
```

## Architecture

### Pipeline Flow (5 Stages)

Every agent change goes through: **INTENT → SANDBOX → VALIDATION → TRUST_ROUTING → DEPLOY**

- **Intent**: Validate agent's declared scope before any code runs
- **Sandbox**: Ephemeral execution environment (currently simulated, needs Docker/K8s)
- **Validation**: 5 parallel signals — static analysis, behavioral diff, intent alignment, resource bounds, security scan (security_scan has 2x weight)
- **Trust Routing**: Risk score determines deploy route — auto-deploy / agent-review / human-approval / canary
- **Deploy**: Execute the routed action

Orchestrator: `src/pipeline/orchestrator.py`. Failures at any stage halt the pipeline and return structured feedback.

### 10 Modules

| Module | Role |
|--------|------|
| `goals/` | Human expresses WHAT to build; `manager.py` handles lifecycle, `decomposer.py` breaks goals into agent tasks |
| `constraints/` | Inviolable architectural rules (the "constitution"); loaded from `configs/constraints.yaml` |
| `routing/` | Selects best agent via weighted scoring (capability 0.35, language 0.20, framework 0.15, trust 0.20, load 0.10); hospital model where generic agent is the ER fallback |
| `intent/` | Agent declares what it plans to change; validated against scope constraints before execution |
| `sandbox/` | Ephemeral execution environment (simulated) |
| `validation/` | Multi-signal gate in `gate.py` orchestrating 5 signal runners from `signals.py` |
| `trust/` | `scorer.py` computes risk from file sensitivity, blast radius, agent trust, validation confidence; `tracker.py` maintains agent profiles |
| `coordination/` | Code-area claims (locks), semantic merge checking, deploy queue |
| `pipeline/` | End-to-end orchestrator tying all stages together; `feedback.py` formats machine-readable feedback for agents |
| `cli/` | Human operator interface via argparse in `app.py` |

### Key Design Patterns

- **Pydantic v2 models** throughout for type safety and serialization
- **In-memory state** (dicts keyed by UUIDs) — no persistence layer yet
- **Simulation layers** with TODO markers where real integrations will go (Docker, LLM, etc.)
- **Structured feedback** — every failure returns data agents can parse, not human-readable strings
- **Trust is domain-specific** — trusted for frontend ≠ trusted for auth

### Configuration

- `configs/default.yaml` — risk thresholds, signal weights, trust parameters, sandbox limits
- `configs/constraints.yaml` — 13 architectural constraints with severity levels (MUST/SHOULD/PREFER)

### What's Simulated (Not Yet Real)

- Sandbox execution (no Docker/K8s)
- Validation signal implementations (static analysis, security scan, etc.)
- Goal decomposition (placeholder returning 3 tasks per goal; needs LLM)
- No persistent storage
- No external agent SDK/protocol

## Code Conventions

- Python >=3.11, line length 100 (ruff)
- One test file per module in `tests/`
- Enums for all state machines: `GoalStatus`, `TaskStatus`, `PipelineStatus`, `RiskLevel`, `DeployRoute`
- Risk scoring weights and thresholds live in `configs/default.yaml`, not hardcoded
