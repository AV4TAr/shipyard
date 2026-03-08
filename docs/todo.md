# AI-CICD TODO

## Recently Built / In Progress

### Human-in-the-Loop Dashboard / CLI
The primary interface between the human operator (air traffic controller) and the agent pipeline.

**Dashboard supports:**
- View all active pipeline runs and their current stage
- View pending approvals (high/critical risk changes awaiting human review)
- Approve or reject changes with optional feedback to the agent
- View agent trust profiles and deployment history
- View active claims (which agents are working on what)
- View the deploy queue and reorder/remove entries
- Real-time logs and structured feedback from sandbox runs
- Anomaly alerts from continuous verification

**CLI supports:**
- `python -m src status` — overview of active runs, pending approvals, queue
- `python -m src approve <run_id>` — approve a pending change
- `python -m src reject <run_id> --reason "..."` — reject with feedback the agent can act on
- `python -m src agents` — list agents with trust scores and activity
- `python -m src runs` — list recent pipeline runs with outcomes
- `python -m src claims` — show active code claims
- `python -m src queue` — show deploy queue
- `python -m src config` — view/edit pipeline configuration (thresholds, weights, constraints)

### Goal and Constraint System
- Goal creation and decomposition into tasks
- Constraint definitions (architectural rules agents must follow)
- Priority-based task assignment to agents

## High Priority

- [ ] **Command Center (Web UI)** — browser-based control panel for the human air traffic controller. Everything the CLI can do, plus real-time visuals. The human should never *need* the terminal to operate the system.
  - **Dashboard** — live overview: active pipeline runs, pending approvals count, agent activity, deploy queue depth, system health
  - **Pipeline Monitor** — view each run's 5-stage progress in real time, expand stages to see logs, validation signals, risk scores
  - **Escalation / Approval Queue** — review HIGH/CRITICAL risk changes, see the diff + intent + validation results, approve/reject with structured feedback
  - **Goal Management** — create, activate, cancel goals; see decomposition into tasks; track progress per goal
  - **Constraint Editor** — view/edit the architectural constitution (`constraints.yaml`), severity levels, add/remove rules
  - **Agent Profiles** — trust scores over time, domain-specific trust breakdown, deployment history, active claims
  - **Configuration** — edit risk thresholds, signal weights, trust parameters, sandbox limits (currently in `configs/default.yaml`)
  - **Deploy Queue** — reorder, pause, remove entries; see canary status for active deploys
  - **Structured Feedback Viewer** — browse feedback history per agent, see what the system told agents and how they responded
  - **Tech:** FastAPI backend exposing the same `CLIRuntime` API over REST/WebSocket, lightweight frontend (React or similar)
  - **Design principle:** the UI is a *window into the system*, not a separate system. Same `CLIRuntime`, same data, same rules. CLI and UI are interchangeable.
- [ ] **Agent Selection / Routing** — smart assignment of tasks to the best agent. This is a pipeline-level decision that considers:
  - Capability match (does the agent have the right skills?)
  - Domain-specific trust (trusted for API work but not DB migrations?)
  - Availability and queue depth
  - Historical performance on similar task types
  - Cost/speed tradeoffs
  - Requires an Agent Registry (capabilities, specializations, status) and a Task Router (matching algorithm)
  - This is a key differentiator — traditional CI/CD never had to solve "who should do this work"
- [ ] **Project Layer** — sits above Goals. Handles how work actually starts: raw idea/problem → project with scope, milestones, phases → goals → tasks
- [ ] Integration with real LLM for goal decomposition — replace placeholder logic with actual LLM calls to break goals into concrete, actionable tasks
- [ ] Persistent storage — replace in-memory dicts with a real database for intents, runs, agent profiles, goals, and constraints
- [ ] Webhook notifications — Slack, email, and custom webhook support for approvals, failures, and anomalies
- [ ] Agent SDK / protocol — define how external agents connect to this system, authenticate, declare intents, and receive structured feedback

## Medium Priority

- [x] Real Docker/K8s sandbox integration — OpenSandbox backend added (`src/sandbox/backends.py`). Set `OPENSANDBOX_SERVER_URL` env var to activate. Simulated backend remains the default.
- [ ] OpenSandbox production hardening — resource usage metrics, JSON report parsing via `sandbox.files.read_file()`, timeout enforcement
- [ ] Real static analysis integration (ruff, mypy, semgrep)
- [ ] Real security scanning integration (bandit, trivy)
- [ ] Behavioral diffing with traffic replay
- [ ] LLM-based intent alignment checking
- [ ] LLM-based semantic merge analysis

## Low Priority

- [ ] Multi-repo support
- [ ] Cost tracking per agent / per deploy
- [ ] Audit log export
- [ ] Role-based access control for human operators
- [ ] Metrics/observability integration (Prometheus, Grafana)
- [ ] Plugin system for custom validation signals
