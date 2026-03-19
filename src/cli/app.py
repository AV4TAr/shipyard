"""Main CLI application for the AI-native CI/CD system.

Usage::

    python -m src goal create --title "Add rate limiting" --description "..."
    python -m src status
    python -m src approve <run_id>

Built entirely on :mod:`argparse` — no external dependencies.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from .formatters import (
    format_agent,
    format_goal,
    format_goal_with_tasks,
    format_project,
    format_status_dashboard,
    format_table,
)
from .runtime import CLIRuntime

# ---------------------------------------------------------------------------
# Parser construction
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build and return the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="shipyard",
        description="AI-native CI/CD system — Human CLI",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to a JSON config file (default: use in-memory defaults)",
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # -- goal ---------------------------------------------------------------
    goal_parser = sub.add_parser("goal", help="Manage goals")
    goal_sub = goal_parser.add_subparsers(dest="goal_action", help="Goal actions")

    # goal create
    gc = goal_sub.add_parser("create", help="Create a new goal")
    gc.add_argument("--title", required=True, help="Short title for the goal")
    gc.add_argument("--description", required=True, help="What you want done")
    gc.add_argument(
        "--constraints",
        nargs="*",
        default=[],
        help="Constraints the agent must respect",
    )
    gc.add_argument(
        "--criteria",
        nargs="*",
        default=[],
        help="Acceptance criteria for completion",
    )
    gc.add_argument(
        "--priority",
        choices=["low", "medium", "high", "urgent"],
        default="medium",
        help="Goal priority (default: medium)",
    )
    gc.add_argument(
        "--services",
        nargs="*",
        default=[],
        help="Target services",
    )

    # goal list
    gl = goal_sub.add_parser("list", help="List goals")
    gl.add_argument("--status", default=None, help="Filter by status")
    gl.add_argument("--priority", default=None, help="Filter by priority")

    # goal activate
    ga = goal_sub.add_parser("activate", help="Activate a goal (decompose into tasks)")
    ga.add_argument("goal_id", help="Goal UUID to activate")

    # goal show
    gs = goal_sub.add_parser("show", help="Show goal details and tasks")
    gs.add_argument("goal_id", help="Goal UUID")

    # goal cancel
    gx = goal_sub.add_parser("cancel", help="Cancel a goal")
    gx.add_argument("goal_id", help="Goal UUID to cancel")

    # -- project ------------------------------------------------------------
    proj_parser = sub.add_parser("project", help="Manage projects")
    proj_sub = proj_parser.add_subparsers(
        dest="project_action", help="Project actions"
    )

    pc = proj_sub.add_parser("create", help="Create a new project")
    pc.add_argument("--title", required=True, help="Short title for the project")
    pc.add_argument("--description", required=True, help="What the project is about")
    pc.add_argument(
        "--constraints", nargs="*", default=[],
        help="Constraints the project must respect",
    )
    pc.add_argument(
        "--priority", choices=["low", "medium", "high", "urgent"],
        default="medium", help="Project priority (default: medium)",
    )
    pc.add_argument("--services", nargs="*", default=[], help="Target services")
    pc.add_argument("--tags", nargs="*", default=[], help="Tags for the project")

    pl = proj_sub.add_parser("list", help="List projects")
    pl.add_argument("--status", default=None, help="Filter by status")

    ps = proj_sub.add_parser("show", help="Show project details")
    ps.add_argument("project_id", help="Project UUID")

    pa = proj_sub.add_parser(
        "activate", help="Activate a project (plan milestones and start)"
    )
    pa.add_argument("project_id", help="Project UUID to activate")

    # -- status -------------------------------------------------------------
    sub.add_parser("status", help="Show system status dashboard")

    # -- approve ------------------------------------------------------------
    ap = sub.add_parser("approve", help="Approve a pending pipeline run")
    ap.add_argument("run_id", help="Pipeline run UUID")
    ap.add_argument("--comment", default=None, help="Optional approval comment")

    # -- reject -------------------------------------------------------------
    rj = sub.add_parser("reject", help="Reject a pending pipeline run")
    rj.add_argument("run_id", help="Pipeline run UUID")
    rj.add_argument("--reason", required=True, help="Reason for rejection (sent to agent)")

    # -- route --------------------------------------------------------------
    rt = sub.add_parser("route", help="Auto-route all tasks for a goal")
    rt.add_argument("goal_id", help="Goal UUID to route tasks for")

    # -- agents -------------------------------------------------------------
    ag = sub.add_parser("agents", help="List agents and trust scores")
    ag.add_argument("--agent", default=None, help="Show details for a specific agent")
    ag_sub = ag.add_subparsers(dest="agents_action", help="Agent actions")

    ag_reg = ag_sub.add_parser("register", help="Register a new agent")
    ag_reg.add_argument("--id", required=True, dest="agent_id", help="Agent ID")
    ag_reg.add_argument("--name", required=True, help="Agent display name")
    ag_reg.add_argument(
        "--capabilities",
        required=True,
        help="Comma-separated capabilities (e.g. frontend,backend)",
    )

    # -- runs ---------------------------------------------------------------
    rn = sub.add_parser("runs", help="List recent pipeline runs")
    rn.add_argument("--agent", default=None, help="Filter by agent ID")
    rn.add_argument("--status", default=None, help="Filter by status")
    rn.add_argument("--limit", type=int, default=20, help="Max results (default: 20)")

    # -- queue --------------------------------------------------------------
    sub.add_parser("queue", help="Show the deploy queue")

    # -- constraints --------------------------------------------------------
    cn = sub.add_parser("constraints", help="View and check constraints")
    cn_sub = cn.add_subparsers(dest="constraints_action", help="Constraint actions")

    cn_sub.add_parser("show", help="Display active constraints by category")

    cc = cn_sub.add_parser("check", help="Check a file against constraints")
    cc.add_argument("file", help="Path to the file to check")

    return parser


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def _handle_goal(runtime: CLIRuntime, args: argparse.Namespace) -> int:
    """Dispatch goal sub-commands."""
    if args.goal_action == "create":
        goal = runtime.create_goal(
            title=args.title,
            description=args.description,
            constraints=args.constraints,
            acceptance_criteria=args.criteria,
            priority=args.priority,
            target_services=args.services,
        )
        print(format_goal(goal))
        print(f"\nGoal created: {goal.goal_id}")
        return 0

    if args.goal_action == "list":
        goals = runtime.list_goals(status=args.status, priority=args.priority)
        if not goals:
            print("No goals found.")
            return 0
        headers = ["ID", "Title", "Status", "Priority", "Tasks"]
        rows: list[list[str]] = []
        for g in goals:
            task_count = len(runtime.goal_manager.get_tasks(g.goal_id))
            rows.append([
                str(g.goal_id)[:8],
                g.title[:40],
                g.status.value,
                g.priority.value,
                str(task_count),
            ])
        print(format_table(headers, rows))
        return 0

    if args.goal_action == "activate":
        try:
            breakdown = runtime.activate_goal(args.goal_id)
        except (KeyError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print(f"Goal activated with {len(breakdown.tasks)} task(s):")
        for task in breakdown.tasks:
            er = task.estimated_risk
            risk = er.value if hasattr(er, "value") else str(er)
            print(f"  - {task.title}  [risk: {risk}]")
        return 0

    if args.goal_action == "show":
        try:
            goal, tasks = runtime.show_goal(args.goal_id)
        except (KeyError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print(format_goal_with_tasks(goal, tasks))
        return 0

    if args.goal_action == "cancel":
        try:
            goal = runtime.cancel_goal(args.goal_id)
        except (KeyError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print(f"Goal cancelled: {goal.goal_id}")
        return 0

    print("Usage: shipyard goal {create,list,activate,show,cancel}", file=sys.stderr)
    return 1


def _handle_project(runtime: CLIRuntime, args: argparse.Namespace) -> int:
    """Dispatch project sub-commands."""
    if args.project_action == "create":
        project = runtime.create_project(
            title=args.title,
            description=args.description,
            constraints=args.constraints,
            priority=args.priority,
            target_services=args.services,
            tags=args.tags,
        )
        print(format_project(project))
        print(f"\nProject created: {project.project_id}")
        return 0

    if args.project_action == "list":
        projects = runtime.list_projects(status=args.status)
        if not projects:
            print("No projects found.")
            return 0
        headers = ["ID", "Title", "Status", "Priority", "Milestones"]
        rows: list[list[str]] = []
        for p in projects:
            rows.append([
                str(p.project_id)[:8],
                p.title[:40],
                p.status.value,
                p.priority.value,
                str(len(p.milestones)),
            ])
        print(format_table(headers, rows))
        return 0

    if args.project_action == "show":
        try:
            project = runtime.show_project(args.project_id)
        except (KeyError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print(format_project(project))
        return 0

    if args.project_action == "activate":
        try:
            project = runtime.activate_project(args.project_id)
        except (KeyError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print(format_project(project))
        print(f"\nProject activated with {len(project.milestones)} milestone(s)")
        return 0

    print(
        "Usage: shipyard project {create,list,show,activate}",
        file=sys.stderr,
    )
    return 1


def _handle_status(runtime: CLIRuntime, _args: argparse.Namespace) -> int:
    data = runtime.get_status_data()
    print(format_status_dashboard(data))
    return 0


def _handle_approve(runtime: CLIRuntime, args: argparse.Namespace) -> int:
    try:
        run = runtime.approve_run(args.run_id, comment=args.comment)
    except (KeyError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Approved: {run.run_id}")
    return 0


def _handle_reject(runtime: CLIRuntime, args: argparse.Namespace) -> int:
    try:
        run = runtime.reject_run(args.run_id, reason=args.reason)
    except (KeyError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Rejected: {run.run_id}")
    print(f"Reason sent to agent: {args.reason}")
    return 0


def _handle_route(runtime: CLIRuntime, args: argparse.Namespace) -> int:
    """Auto-route all tasks for a goal."""
    try:
        decisions = runtime.auto_route_goal(args.goal_id)
    except (KeyError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not decisions:
        print("No tasks ready to route.")
        return 0

    headers = ["Task", "Agent", "Score", "Fallback"]
    rows = []
    for d in decisions:
        rows.append([
            d.task_id[:8] if d.task_id else "-",
            d.selected_agent_id or "(none)",
            f"{d.match_score:.4f}",
            "yes" if d.fallback_used else "no",
        ])
    print(format_table(headers, rows))
    return 0


def _handle_agents(runtime: CLIRuntime, args: argparse.Namespace) -> int:
    # Handle register subcommand.
    if getattr(args, "agents_action", None) == "register":
        caps = [c.strip() for c in args.capabilities.split(",") if c.strip()]
        try:
            reg = runtime.register_agent(
                agent_id=args.agent_id,
                name=args.name,
                capabilities=caps,
            )
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        cap_str = ", ".join(c.value for c in reg.capabilities)
        print(f"Agent registered: {reg.agent_id} ({reg.name})")
        print(f"  Capabilities: {cap_str}")
        return 0

    if args.agent:
        profile = runtime.get_agent(args.agent)
        print(format_agent(profile))
        return 0

    # Build a capabilities lookup from the routing registry.
    cap_map: dict[str, str] = {}
    registered = runtime.list_registered_agents()
    for ra in registered:
        cap_map[ra.agent_id] = ", ".join(c.value for c in ra.capabilities)

    profiles = runtime.list_agents()
    if not profiles and not registered:
        print("No agents registered.")
        return 0

    headers = ["Agent", "Trust", "Deployments", "Success Rate", "Capabilities"]
    rows = []
    for p in profiles:
        rows.append([
            p.agent_id,
            f"{p.trust_score:.4f}",
            str(p.total_deployments),
            f"{p.success_rate:.1%}",
            cap_map.get(p.agent_id, "-"),
        ])
    # Show agents that are in the routing registry but have no trust profile yet.
    seen = {p.agent_id for p in profiles}
    for ra in registered:
        if ra.agent_id not in seen:
            rows.append([
                ra.agent_id,
                "-",
                "0",
                "-",
                ", ".join(c.value for c in ra.capabilities),
            ])
    print(format_table(headers, rows))
    return 0


def _handle_runs(runtime: CLIRuntime, args: argparse.Namespace) -> int:
    runs = runtime.list_runs(
        agent_id=args.agent,
        status=args.status,
        limit=args.limit,
    )
    if not runs:
        print("No pipeline runs found.")
        return 0

    headers = ["Run ID", "Agent", "Status", "Stage", "Started"]
    rows = []
    for r in runs:
        rows.append([
            str(r.run_id)[:8],
            r.agent_id[:16] if r.agent_id else "-",
            r.status.value,
            r.current_stage.value,
            r.started_at.strftime("%Y-%m-%d %H:%M"),
        ])
    print(format_table(headers, rows))
    return 0


def _handle_queue(runtime: CLIRuntime, _args: argparse.Namespace) -> int:
    entries = runtime.list_queue()
    if not entries:
        print("Deploy queue is empty.")
        return 0

    headers = ["Position", "Intent ID", "Priority", "Enqueued"]
    rows = []
    for idx, entry in enumerate(entries, start=1):
        rows.append([
            str(idx),
            str(entry.intent_id)[:8],
            str(entry.priority),
            entry.enqueued_at.strftime("%Y-%m-%d %H:%M"),
        ])
    print(format_table(headers, rows))
    return 0


def _handle_constraints(runtime: CLIRuntime, args: argparse.Namespace) -> int:
    if args.constraints_action == "show":
        print("Constraints display requires a loaded constraint set.")
        print("Use: shipyard constraints check <file> to check a specific file.")
        return 0

    if args.constraints_action == "check":
        print(f"Checking file: {args.file}")
        print("Constraint checking requires a loaded constraint set.")
        print("Configure constraints via --config or place a constraints.yaml in configs/.")
        return 0

    print("Usage: shipyard constraints {show,check}", file=sys.stderr)
    return 1


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

_COMMAND_HANDLERS = {
    "goal": _handle_goal,
    "project": _handle_project,
    "status": _handle_status,
    "approve": _handle_approve,
    "reject": _handle_reject,
    "route": _handle_route,
    "agents": _handle_agents,
    "runs": _handle_runs,
    "queue": _handle_queue,
    "constraints": _handle_constraints,
}


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and dispatch to the appropriate handler."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    # Build the runtime
    if args.config:
        runtime = CLIRuntime.from_config(args.config)
    else:
        runtime = CLIRuntime.from_defaults()

    handler = _COMMAND_HANDLERS.get(args.command)
    if handler is None:
        parser.print_help()
        return 1

    return handler(runtime, args)
