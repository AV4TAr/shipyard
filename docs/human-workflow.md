# Human Operator Workflow

A practical reference for humans operating the AI-native CI/CD system. You are the air traffic controller -- you set the goals and rules, agents handle the implementation.

## Getting Started

### Install

```bash
pip install -e ".[dev]"
```

### Define Your Constraints

Before creating any goals, establish the rules agents must follow. Constraints act as an inviolable constitution -- no goal or agent can override them.

```bash
# Add architectural constraints
python -m src constraint add --rule "All new endpoints require authentication"
python -m src constraint add --rule "Database migrations must be backwards-compatible"
python -m src constraint add --rule "Never expose PII in logs"

# View active constraints
python -m src constraint list
```

Think of constraints as the guardrails of your airspace. Set them once, refine over time.

### Create Your First Goal

Goals describe WHAT you want built, not HOW to build it. The system decomposes goals into tasks and assigns them to agents.

```bash
python -m src goal create \
  --title "Add rate limiting" \
  --description "Rate limit /api/users to 100 req/min per client to prevent abuse" \
  --priority high
```

That is it. The system takes over from here: decomposing the goal into tasks, assigning them to agents, running the pipeline, and deploying validated changes.

## Day-to-Day Operations

### Check Status

```bash
# Overview of everything: active runs, pending approvals, queue
python -m src status

# List recent pipeline runs with outcomes
python -m src runs

# See what agents are working on
python -m src claims
```

### Review Approvals

High-risk changes are escalated to you for review. The system provides context: what changed, why, risk assessment, and validation results.

```bash
# See pending approvals
python -m src status

# Approve a change
python -m src approve <run_id>

# Reject with feedback the agent can act on
python -m src reject <run_id> --reason "This approach bypasses the auth layer. Use middleware instead."
```

When you reject with a reason, the agent receives structured feedback and can retry with your guidance.

### Monitor Agents

```bash
# List agents with trust scores and recent activity
python -m src agents

# View the deploy queue
python -m src queue
```

Trust scores update automatically based on agent track record: successful deploys increase trust, failures and rollbacks decrease it. Higher-trust agents get more autonomy.

## Tuning the System

### Adjust Risk Thresholds

Control how much autonomy agents get by adjusting the risk thresholds that determine routing.

```bash
# View current configuration
python -m src config

# Edit thresholds, weights, and routing rules
python -m src config --edit
```

Key settings:
- **Auto-deploy threshold** -- risk score below which changes deploy without review
- **Agent review threshold** -- risk range where a supervisor agent reviews
- **Human approval threshold** -- risk score above which you must approve
- **Risk weights** -- how much each factor (file type, blast radius, agent trust, time of day) contributes to the risk score

### Update Constraints

Constraints evolve as your system matures. Add new ones as you discover patterns, remove ones that are too restrictive.

```bash
# Add a new constraint
python -m src constraint add --rule "Services must not make synchronous calls to more than 2 downstream services"

# Remove a constraint
python -m src constraint remove <constraint_id>

# List all active constraints
python -m src constraint list
```

### Manage Agent Trust

If an agent is underperforming or you want to limit a new agent's autonomy while it builds a track record:

```bash
# View an agent's profile and history
python -m src agents

# Adjust trust (the system also adjusts trust automatically based on outcomes)
python -m src config --edit
```

## When Things Go Wrong

### Automatic Rollbacks

The system monitors deployments continuously. If anomalies are detected (error rate spikes, latency increases, resource usage jumps), the system rolls back automatically and notifies both the responsible agent and you.

No human action is needed for automatic rollbacks. The agent that shipped the change is "paged" with structured observations and is expected to diagnose and retry.

### Manual Intervention

If you need to intervene directly:

```bash
# Reject a running or pending change
python -m src reject <run_id> --reason "Causing downstream issues in service X"

# View what happened in a specific run
python -m src runs
```

### Pausing an Agent

If an agent is producing repeated failures or you need to investigate its behavior:

```bash
# Check the agent's recent track record
python -m src agents

# Lower the agent's trust score to force human approval on all its changes
python -m src config --edit
```

Setting an agent's trust to zero effectively pauses it -- all changes will require your explicit approval.

### Escalation Path

When something goes wrong, the typical flow is:

1. Anomaly detected by continuous verification
2. Automatic rollback executes
3. Agent receives structured feedback about the failure
4. Agent attempts a fix, re-enters the pipeline
5. If the agent fails repeatedly, trust score drops and changes escalate to you
6. You review, provide guidance via reject feedback, or update constraints to prevent the class of error

The system is designed so that failures make it smarter. Each rollback and rejection feeds back into risk scoring and trust computation, reducing the chance of the same problem recurring.
