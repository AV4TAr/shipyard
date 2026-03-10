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

## Completed

- [x] **Command Center (Web UI)** — FastAPI backend (`src/api/`) + self-contained SPA frontend (`src/api/static/index.html`). 6 views: dashboard, goals, pipeline, agents, queue, constraints. Dark theme, WebSocket, auto-refresh. Same `CLIRuntime` underneath — CLI and UI are interchangeable.
- [x] **Project Layer** — `src/projects/` — Project → Milestone → Goal hierarchy. Full lifecycle (draft → planning → active → completed). Rule-based planner generates 3-phase milestones (Foundation, Implementation, Polish).
- [x] **LLM Integration** — `src/llm/` — OpenRouter client (stdlib only), LLM goal decomposer with fallback, intent alignment checker, semantic merge analyzer.
- [x] **Persistent Storage** — `src/storage/` — Repository pattern with memory + SQLite backends. JSON-blob storage with indexed columns, factory pattern.
- [x] **Webhook Notifications** — `src/notifications/` — Event dispatcher with webhook + Slack channels. HMAC signing, Block Kit formatting. 13 event types.
- [x] **Agent SDK / Protocol** — `src/sdk/` — Protocol models, client SDK (stdlib only), FastAPI routes. Full lifecycle: register → get tasks → claim → submit → get feedback.
- [x] Real Docker/K8s sandbox integration — OpenSandbox backend (`src/sandbox/backends.py`). Set `OPENSANDBOX_SERVER_URL` env var to activate.
- [x] Real static analysis integration — `src/validation/real_runners.py` — ruff JSON output parsing with severity mapping.
- [x] Real security scanning integration — `src/validation/real_runners.py` — bandit JSON parsing with severity mapping.
- [x] LLM-based intent alignment checking — `src/llm/alignment.py`
- [x] LLM-based semantic merge analysis — `src/llm/merge_analyzer.py`

## High Priority

- [ ] **Agent Selection / Routing** — smart assignment of tasks to the best agent. This is a pipeline-level decision that considers:
  - Capability match (does the agent have the right skills?)
  - Domain-specific trust (trusted for API work but not DB migrations?)
  - Availability and queue depth
  - Historical performance on similar task types
  - Cost/speed tradeoffs
  - Requires an Agent Registry (capabilities, specializations, status) and a Task Router (matching algorithm)
  - This is a key differentiator — traditional CI/CD never had to solve "who should do this work"
- [x] **Storage Integration** — `src/storage/` repositories wired into GoalManager, TrustTracker, IntentRegistry, PipelineOrchestrator. `CLIRuntime.from_defaults(storage_backend="sqlite", db_path="...")` or `AI_CICD_DB_PATH` env var.
- [x] **LLM Decomposer Integration** — `OPENROUTER_API_KEY` env var auto-selects LLMGoalDecomposer in `CLIRuntime.from_defaults()`, with fallback to rule-based.
- [x] **Notification Integration** — EventDispatcher wired into GoalManager (goal.created/activated/completed) and PipelineOrchestrator (pipeline.started/failed/passed, approval.needed).

## Medium Priority

- [ ] OpenSandbox production hardening — resource usage metrics, JSON report parsing via `sandbox.files.read_file()`, timeout enforcement
- [ ] Behavioral diffing with traffic replay
- [ ] Command Center UI polish — structured feedback viewer, configuration editor, constraint editing
- [ ] Project Layer API routes + frontend views

## Low Priority

- [ ] Multi-repo support
- [ ] Cost tracking per agent / per deploy
- [ ] Audit log export
- [ ] Role-based access control for human operators
- [ ] Metrics/observability integration (Prometheus, Grafana)
- [ ] Plugin system for custom validation signals
