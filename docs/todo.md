# Shipyard TODO

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

- [x] **Command Center (Web UI)** — FastAPI backend (`src/api/`) + self-contained SPA frontend (`src/api/static/index.html`). 7 views: dashboard, goals, pipeline, agents, queue, constraints, projects. Dark theme, WebSocket, auto-refresh, structured feedback viewer. Same `CLIRuntime` underneath — CLI and UI are interchangeable.
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

- [x] **Agent Selection / Routing** — AgentRegistry, TaskRouter, RoutingBridge wired into CLIRuntime. Weighted scoring (capability 0.35, language 0.20, framework 0.15, trust 0.20, load 0.10). Domain-specific trust. SDK-to-routing bridge. 8 API endpoints, CLI commands (`route`, `agents register`). Persistence (memory + SQLite). Event notifications (TASK_ROUTED, ROUTING_FALLBACK).
- [x] **Storage Integration** — `src/storage/` repositories wired into GoalManager, TrustTracker, IntentRegistry, PipelineOrchestrator. `CLIRuntime.from_defaults(storage_backend="sqlite", db_path="...")` or `SHIPYARD_DB_PATH` env var.
- [x] **LLM Decomposer Integration** — `OPENROUTER_API_KEY` env var auto-selects LLMGoalDecomposer in `CLIRuntime.from_defaults()`, with fallback to rule-based.
- [x] **Notification Integration** — EventDispatcher wired into GoalManager (goal.created/activated/completed) and PipelineOrchestrator (pipeline.started/failed/passed, approval.needed).

## Medium Priority

- [x] OpenSandbox production hardening — resource usage metrics, JSON report parsing via `sandbox.files.read_file()`, timeout enforcement
- [x] Behavioral diffing — `RealBehavioralDiffRunner` runs tests before (main) and after (task branch), diffs results, detects regressions/fixes/new/removed tests
- [x] Command Center config editor — in-browser editing of pipeline thresholds, signal weights, deploy routes, constraints. Sliders, toggles, dropdowns.
- [x] Command Center UI polish — structured feedback viewer with collapsible stages, loading spinners, tab animations, toast notifications, enhanced agents tab with capability badges
- [x] Project Layer API routes + frontend views — 8 API endpoints, CLI `project` subcommand, Projects tab in Command Center (7th tab)
- [x] Lease-based task claims — `src/leases/manager.py`. Agents get leases on claim, heartbeat to renew, expired leases auto-reset tasks to PENDING. Background asyncio sweep loop.
- [x] Agent status tracking — AgentPhase enum (idle, claiming, calling_llm, writing_files, running_tests, submitting, waiting). Heartbeats carry phase. `GET /api/agents/status` endpoint. WebSocket broadcasts. Phase badges in UI.
- [x] Git worktree code workflow — `src/worktrees/manager.py`. Projects link to git repos. Tasks get isolated worktrees. Agents write real files. Pipeline validates real code (pytest, ruff, bandit). Approved changes merge to main.
- [x] All 5 validation signals real — RealStaticAnalysisRunner (ruff), RealSecurityScanRunner (bandit), RealResourceBoundsRunner (file sizes), RealBehavioralDiffRunner (worktree test diff), IntentAlignmentRunner (LLM).
- [x] Kill switch / controls — Pipeline freeze (blocks all claims/submissions), project pause/resume, agent ban/unban, lease revocation. Heartbeat returns `cancel: true` when frozen/banned/paused. Dashboard FREEZE button.
- [x] SDK enhancements — Auto-heartbeat (Python daemon thread, TS setInterval). `Workspace` class for file ops in worktrees. `client.workspace` property. `client.set_phase()`. HeartbeatRequest/Response with cancel signal.
- [x] Agent v4 — `agents/claude_agent.py`. Uses SDK client, worktrees, auto-heartbeat, phase tracking. Reads existing code from workspace for context. Writes files to worktree. Server generates diff.
- [x] Branding — AI-CICD → Shipyard throughout. `SHIPYARD_DB_PATH` env var.

## Low Priority

- [ ] Agent SDK rewrite — dedicated pip-installable package with async support
- [ ] Traffic replay behavioral diff — record/replay HTTP traffic for semantic regression detection
- [ ] Multi-language linters — extend static analysis beyond Python (ESLint, golangci-lint, etc.)
- [ ] Multi-repo support
- [ ] Cost tracking per agent / per deploy
- [ ] Audit log export
- [ ] Role-based access control for human operators
- [ ] Metrics/observability integration (Prometheus, Grafana)
- [ ] Plugin system for custom validation signals
