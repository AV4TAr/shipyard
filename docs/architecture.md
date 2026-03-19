# Shipyard Architecture

> CI/CD reimagined for AI agents. The human is the air traffic controller, not the pilot.

## System Overview

```mermaid
graph TD
    subgraph Human["HUMAN INTERFACE LAYER"]
        Problem["Problem / Idea<br/>(natural language)"]
        Goals["Goals<br/>(what to build)"]
        Constraints["Constraints<br/>(the rules)"]
        Approve["Approve / Monitor<br/>(escalations)"]
        CLI["CLI: python -m src"]
        Problem --> Goals --> Constraints --> Approve
    end

    subgraph Decomposition["GOAL DECOMPOSITION"]
        Goal["Goal"]
        Breakdown["TaskBreakdown"]
        Tasks["AgentTask[]"]
        Goal --> Breakdown --> Tasks
    end

    subgraph Routing["AGENT ROUTING (the hospital)"]
        Analyzer["TaskAnalyzer"]
        Requirements["TaskRequirements"]
        Router["TaskRouter"]
        Decision["RouteDecision"]
        Analyzer --> Requirements --> Router --> Decision
        Registry["Agent Registry<br/>Frontend | Backend | Data<br/>Security | Mobile | QA | DevOps"]
        Generic["Generic Agent<br/>(the ER — always available)"]
        Registry --> Router
        Generic --> Router
    end

    subgraph Pipeline["CI/CD PIPELINE (agent-agnostic)"]
        Intent["1. Intent Declaration<br/>what, why, which files, which services<br/>scope check, risk classification, conflicts"]
        Sandbox["2. Sandbox Execution<br/>ephemeral env, agent iterates<br/>code → test → observe → fix → repeat"]
        Validation["3. Multi-Signal Validation<br/>static analysis | behavioral diff<br/>intent alignment | resource bounds<br/>security scan | constraint check"]
        Trust["4. Trust-Based Routing<br/>LOW → auto-deploy<br/>MEDIUM → agent review<br/>HIGH → human approval<br/>CRITICAL → human + canary + rollback"]
        Deploy["5. Deploy & Monitor<br/>canary → expand → full rollout<br/>auto-rollback on anomaly"]
        Intent --> Sandbox --> Validation --> Trust --> Deploy
    end

    subgraph LeaseWorktree["LEASE & WORKTREE LAYER"]
        Leases["Leases<br/>time-bound task claims<br/>heartbeat renewal"]
        Worktrees["Git Worktrees<br/>isolated branch per task<br/>merge on approval"]
    end

    subgraph Coordination["COORDINATION LAYER"]
        Claims["Claims<br/>agents lock code areas"]
        Merge["Semantic Merge<br/>compatibility checking"]
        Queue["Deploy Queue<br/>priority-ordered"]
        Feedback["Feedback<br/>machine-readable for agents"]
    end

    Human --> Decomposition --> Routing --> LeaseWorktree --> Pipeline --> Coordination
    Deploy -->|"anomaly"| Feedback
    Feedback -->|"structured feedback"| Sandbox
```

## Components

### Human Interface Layer

| Component | Module | Purpose |
|---|---|---|
| CLI | `src/cli/` | Primary human interface — create goals, approve changes, monitor agents |
| Goals | `src/goals/` | Human expresses WHAT to build, system decomposes into agent tasks |
| Constraints | `src/constraints/` | Architectural rules agents must follow (the "constitution") |

**Human input flow:** Problem/idea --> Goal (title + description + constraints + criteria) --> System handles everything else

### Agent Routing Layer

| Component | Module | Purpose |
|---|---|---|
| Registry | `src/routing/registry.py` | Tracks registered agents, their capabilities, and status |
| Analyzer | `src/routing/analyzer.py` | Extracts task requirements from task description and files |
| Router | `src/routing/router.py` | Matches tasks to best available agent, falls back to generic |

**The hospital model:**
- Specialist agents (cardiologist, neurologist) handle domain-specific tasks
- Generic agent (ER / general practitioner) handles anything no specialist matches
- Trust scores are domain-specific — trusted for frontend doesn't mean trusted for auth
- If no one scores above 0.5, the generic agent takes it

```mermaid
graph LR
    Task["Incoming Task"]
    Analyze["Analyze<br/>requirements"]
    Score["Score agents"]

    Task --> Analyze --> Score

    Score -->|"score > 0.5"| Specialist["Specialist Agent"]
    Score -->|"score < 0.5"| Generic["Generic Agent (ER)"]
    Score -->|"manual strategy"| Human["Human assigns"]

    style Generic fill:#f9a825
    style Specialist fill:#66bb6a
    style Human fill:#42a5f5
```

### CI/CD Pipeline (Agent-Agnostic)

| Component | Module | Purpose |
|---|---|---|
| Intent | `src/intent/` | Agent declares what it wants to change and why |
| Sandbox | `src/sandbox/` | Ephemeral execution environment with pluggable backends (simulated or OpenSandbox). Agent iterates until green |
| Validation | `src/validation/` | Multi-signal gate — static, behavioral, security, constraints |
| Trust/Risk | `src/trust/` | Risk scoring, deploy route determination, agent trust tracking |
| Pipeline | `src/pipeline/` | Orchestrates all stages, produces machine-readable feedback |

**Key principle:** The pipeline is agent-agnostic. A frontend agent and a backend agent go through the exact same stages. The pipeline validates work, not identity.

```mermaid
graph TD
    subgraph Validation Signals
        SA["Static Analysis"]
        BD["Behavioral Diff"]
        IA["Intent Alignment"]
        RB["Resource Bounds"]
        SS["Security Scan"]
        CC["Constraint Check"]
    end

    Code["Agent's Code"] --> SA & BD & IA & RB & SS & CC
    SA & BD & IA & RB & SS & CC --> Gate["Validation Gate"]
    Gate -->|"all pass"| Risk["Risk Scoring"]
    Gate -->|"failure"| Feedback["Structured Feedback → Agent"]

    Risk -->|"LOW"| AutoDeploy["Auto Deploy"]
    Risk -->|"MEDIUM"| AgentReview["Agent Review"]
    Risk -->|"HIGH"| HumanApproval["Human Approval"]
    Risk -->|"CRITICAL"| Canary["Human + Canary + Auto-rollback"]

    style AutoDeploy fill:#66bb6a
    style AgentReview fill:#f9a825
    style HumanApproval fill:#ef5350
    style Canary fill:#b71c1c,color:#fff
```

### Lease & Worktree Layer

| Component | Module | Purpose |
|---|---|---|
| Leases | `src/leases/manager.py` | Time-bound task claims with heartbeat renewal. Expired leases auto-reset tasks to PENDING. Background asyncio sweep loop. |
| Worktrees | `src/worktrees/manager.py` | Git worktree isolation per task. Projects link to repos via `repo_url`. Agents write real files. Approved changes merge to main. |

### Coordination Layer

| Component | Module | Purpose |
|---|---|---|
| Claims | `src/coordination/claims.py` | Agents lock code areas to prevent conflicts |
| Merge | `src/coordination/merge.py` | Checks if concurrent changes are compatible |
| Queue | `src/coordination/queue.py` | Priority-ordered deploy queue |
| Feedback | `src/pipeline/feedback.py` | Structured, machine-readable output for agents |

## Data Flow

### Happy Path: Goal to Deploy

```mermaid
sequenceDiagram
    actor Human
    participant CLI
    participant GoalManager
    participant Decomposer
    participant Router
    participant Agent
    participant Pipeline
    participant Monitor

    Human->>CLI: goal create --title "Add rate limiting" --priority high
    CLI->>GoalManager: create(GoalInput)
    Human->>CLI: goal activate <goal_id>
    CLI->>GoalManager: activate(goal_id)
    GoalManager->>Decomposer: decompose(goal)
    Decomposer-->>GoalManager: TaskBreakdown [impl, test, docs]

    loop For each ready task
        GoalManager->>Router: route(task)
        Router-->>GoalManager: RouteDecision (agent selected)
        GoalManager->>Agent: assign task
        Agent->>Pipeline: declare intent
        Pipeline->>Pipeline: sandbox loop (iterate until green)
        Pipeline->>Pipeline: validate (all signals)
        Pipeline->>Pipeline: assess risk
        alt LOW/MEDIUM risk
            Pipeline->>Monitor: auto-deploy
        else HIGH/CRITICAL risk
            Pipeline->>Human: approval needed
            Human->>Pipeline: approve
            Pipeline->>Monitor: deploy with canary
        end
    end

    Monitor-->>Monitor: continuous verification
    alt anomaly detected
        Monitor->>Agent: structured feedback (auto-rollback)
    else all clear
        Monitor->>GoalManager: task completed
        GoalManager-->>Human: goal auto-completed
    end
```

### Retry & Recovery Flow

When an agent's work is rejected by the pipeline, a multi-level retry system kicks in:

```mermaid
graph TD
    Generate["Agent generates code"] --> Write["Write files to worktree"]
    Write --> Ruff["Run ruff --fix (auto-format)"]
    Ruff --> LocalTest["Run pytest locally"]
    LocalTest -->|"pass"| Submit["Submit to pipeline"]
    LocalTest -->|"fail"| LocalFix{"Local fix attempts < 2?"}
    LocalFix -->|"yes"| AskFix["Send test output to Claude → fix"] --> Write
    LocalFix -->|"no"| Submit
    Submit --> Pipeline["Pipeline: real tests + ruff + bandit + behavioral diff"]
    Pipeline -->|"accepted"| Merge["Merge branch to main ✓"]
    Pipeline -->|"rejected"| RetryCheck{"Pipeline attempts < 3?"}
    RetryCheck -->|"yes"| FeedbackLoop["Extract feedback → retry from Generate"]
    FeedbackLoop --> Generate
    RetryCheck -->|"no"| ResetCheck{"Total claims < 5?"}
    ResetCheck -->|"yes"| Reset["Task → PENDING (another agent can try)"]
    ResetCheck -->|"no"| Dead["Task → FAILED (human intervention needed)"]

    style Merge fill:#66bb6a,color:#fff
    style Dead fill:#ef5350,color:#fff
    style Reset fill:#42a5f5,color:#fff
```

**Three levels of retry:**

| Level | Scope | Max attempts | What happens |
|-------|-------|-------------|--------------|
| **Local fix** | Same agent, same claim | 2 | Claude sees test output, fixes code, re-runs tests locally |
| **Pipeline retry** | Same agent, same claim | 3 | Claude sees pipeline feedback (ruff, bandit, test failures), regenerates and re-submits |
| **Task re-claim** | Any agent, new claim | 5 total | Task resets to PENDING on rejection. Another agent (or the same one) claims it fresh |

After 5 total claims with no success, the task is permanently FAILED and requires human intervention (reassign, adjust constraints, or implement manually).

**Key fields on AgentTask:**
- `retry_count: int` — incremented each time the task is claimed (across all agents)
- `max_retries: int = 5` — configurable cap

### Escalation Path

When all retries are exhausted:

```mermaid
graph TD
    TaskFail["Task FAILED (5 attempts)"] --> Notify["Human notified via dashboard"]
    Notify --> Reassign["Reset retry count → agents try again"]
    Notify --> Adjust["Adjust constraints or project description"]
    Notify --> Manual["Implement manually, push to repo"]

    style TaskFail fill:#ef5350,color:#fff
    style Notify fill:#42a5f5,color:#fff
```

### Kill Switch & Controls

The human operator has multiple levels of control:

| Control | Effect | Reversible |
|---------|--------|------------|
| **Freeze pipeline** | Blocks ALL claims and submissions globally | Yes (unfreeze) |
| **Pause project** | Hides project's tasks from agents, heartbeat sends cancel | Yes (resume) |
| **Cancel project** | Revokes all leases, stops all work | No |
| **Ban agent** | Agent can't claim any tasks, heartbeat sends cancel | Yes (unban) |
| **Revoke lease** | Force-expire a specific lease, task → PENDING | Yes |
| **Reset task** | Reset a failed task to PENDING for retry | Yes |

## Key Design Decisions

1. **Pipeline is agent-agnostic** — specialization is a routing concern, not a pipeline concern. Any agent goes through the same validation.

2. **Generic fallback always available** — the system never gets stuck because no specialist is registered. The generic agent is the ER.

3. **Constraints are a constitution** — no goal can override them. They're checked at validation time, not just at intent time.

4. **Feedback is machine-readable** — every failure, warning, and suggestion is structured data agents can parse and act on. No log dumps for human eyes.

5. **Trust is earned** — new agents start with low trust (more human oversight). Trust grows with successful deploys and shrinks with rollbacks.

6. **Human works at WHAT/WHY level** — humans express problems, set rules, approve high-risk changes. They never write implementation details.

## Module Dependency Graph

```mermaid
graph LR
    cli --> goals
    cli --> routing
    cli --> trust
    cli --> coordination
    cli --> constraints
    cli --> sandbox
    cli --> leases
    cli --> worktrees

    goals --> intent
    routing --> pipeline
    routing --> trust
    leases --> goals
    worktrees --> pipeline
    pipeline --> validation
    pipeline --> sandbox
    pipeline --> trust
    validation --> constraints
    validation --> worktrees
```

## Current Status

| Layer | Status | Tests |
|---|---|---|
| Intent | Built | 21 |
| Sandbox | Built (simulated + OpenSandbox backend) | 46 |
| Validation | Built (all 5 signals real) | 51 |
| Trust/Risk | Built | 27 |
| Coordination | Built | 24 |
| Pipeline | Built | 17 |
| Goals | Built | 37 |
| Constraints | Built | 29 |
| CLI | Built | 45 |
| Routing | Built | 39 |
| API (Command Center) | Built | 15 |
| Storage | Built (memory + SQLite) | 63 |
| LLM | Built (OpenRouter) | 22 |
| SDK | Built (auto-heartbeat, workspace, phases) | 25 |
| Notifications | Built | 45 |
| Projects | Built | 52 |
| Leases | Built (heartbeat renewal, auto-expiry) | ~87 |
| Worktrees | Built (git worktree isolation per task) | ~87 |
| **Total** | | **806** |

## What's Not Built Yet

See [todo.md](./todo.md) for the full list. Key remaining gaps:

- **Agent SDK rewrite** — dedicated pip-installable package with async support
- **Traffic replay behavioral diff** — record/replay HTTP traffic for deeper semantic regression detection
- **Multi-language linters** — extend static analysis beyond Python (ESLint, golangci-lint, etc.)
- **Multi-repo support** — orchestrate across multiple git repositories
