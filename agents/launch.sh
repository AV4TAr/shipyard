#!/usr/bin/env bash
#
# Shipyard Agent Fleet Launcher
# Spins up all agents in a tmux session with individual panes.
#
# Usage:
#   ./agents/launch.sh              # launch all 4 agents
#   ./agents/launch.sh --test       # use test_agent.py (no LLM needed)
#   ./agents/launch.sh --once       # each agent processes one task and exits
#
# Requires: tmux, python3
# For real agents: OPENROUTER_API_KEY must be set
# Server must already be running on :8001

set -euo pipefail

SESSION="shipyard"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Defaults
USE_TEST_AGENT=false
ONCE_FLAG=""

for arg in "$@"; do
    case "$arg" in
        --test)  USE_TEST_AGENT=true ;;
        --once)  ONCE_FLAG="--once" ;;
        --help|-h)
            echo "Usage: ./agents/launch.sh [--test] [--once]"
            echo ""
            echo "  --test   Use test_agent.py instead of claude_agent.py (no LLM)"
            echo "  --once   Each agent processes one task and exits"
            echo ""
            echo "Server must be running: python3 -m uvicorn src.api.app:create_app --factory --host 0.0.0.0 --port 8001"
            echo ""
            echo "Keybindings inside tmux:"
            echo "  Ctrl-b o      Cycle through panes"
            echo "  Ctrl-b ←↑↓→  Navigate panes by direction"
            echo "  Ctrl-b z      Zoom/unzoom current pane"
            echo "  Ctrl-b d      Detach (agents keep running)"
            echo "  tmux attach -t shipyard   Re-attach"
            exit 0
            ;;
    esac
done

# Pre-flight checks
if ! command -v tmux &>/dev/null; then
    echo "Error: tmux is not installed. Install with: brew install tmux"
    exit 1
fi

if [ "$USE_TEST_AGENT" = false ] && [ -z "${OPENROUTER_API_KEY:-}" ]; then
    echo "Error: OPENROUTER_API_KEY not set (use --test for LLM-free mode)"
    echo "  export OPENROUTER_API_KEY='sk-or-v1-...'"
    exit 1
fi

# Check server is running
if ! curl -sf http://localhost:8001/api/status >/dev/null 2>&1; then
    echo "Error: Shipyard server not running on :8001"
    echo "  Start it first: cd $PROJECT_DIR && SHIPYARD_DB_PATH=data/shipyard.db python3 -m uvicorn src.api.app:create_app --factory --host 0.0.0.0 --port 8001"
    exit 1
fi

# Kill existing session if present
tmux kill-session -t "$SESSION" 2>/dev/null || true

# ── Build the session ──────────────────────────────────────────────
#
# Layout (single window, 4 panes in 2x2 grid):
#
#   ┌──────────────┬──────────────┐
#   │  suricata    │  lagarto     │
#   │  (Backend)   │  (QA)        │
#   ├──────────────┼──────────────┤
#   │  unicornio   │  rocky       │
#   │  (Frontend)  │  (Architect) │
#   └──────────────┴──────────────┘

echo ""
echo "  ┌─────────────────────────────────────────────┐"
echo "  │         SHIPYARD AGENT FLEET                 │"
echo "  │                                              │"
echo "  │  ┌────────────┬────────────┐                 │"
echo "  │  │ suricata   │ lagarto    │                 │"
echo "  │  │ (Backend)  │ (QA)       │                 │"
echo "  │  ├────────────┼────────────┤                 │"
echo "  │  │ unicornio  │ rocky      │                 │"
echo "  │  │ (Frontend) │ (Architect)│                 │"
echo "  │  └────────────┴────────────┘                 │"
echo "  │                                              │"
echo "  │  Ctrl-b o     = cycle panes                  │"
echo "  │  Ctrl-b ←↑↓→  = navigate panes              │"
echo "  │  Ctrl-b z     = zoom/unzoom pane             │"
echo "  │  Ctrl-b d     = detach (keeps running)       │"
echo "  └─────────────────────────────────────────────┘"
echo ""

# Agent definitions: name, profile
declare -a AGENTS=(
    "suricata:backend"
    "lagarto:qa"
    "unicornio:frontend"
    "rocky:architect"
    "docu:docs"
)

# Create session with first pane
tmux new-session -d -s "$SESSION" -n "fleet" -c "$PROJECT_DIR"

# Split into 4 panes (2x2 grid)
tmux split-window -h -t "$SESSION:fleet" -c "$PROJECT_DIR"    # pane 1 (right)
tmux split-window -v -t "$SESSION:fleet.0" -c "$PROJECT_DIR"  # pane 2 (bottom-left)
tmux split-window -v -t "$SESSION:fleet.1" -c "$PROJECT_DIR"  # pane 3 (bottom-right)

# Even out the grid
tmux select-layout -t "$SESSION:fleet" tiled

PANE=0
DELAY=0

for entry in "${AGENTS[@]}"; do
    IFS=":" read -r name profile <<< "$entry"

    if [ "$USE_TEST_AGENT" = true ]; then
        tmux send-keys -t "$SESSION:fleet.$PANE" \
            "sleep $DELAY && echo '── Agent: $name (test mode) ──' && python3 agents/test_agent.py $name" Enter
    else
        tmux send-keys -t "$SESSION:fleet.$PANE" \
            "sleep $DELAY && echo '── Agent: $name ($profile) ──' && OPENROUTER_API_KEY=$OPENROUTER_API_KEY python3 agents/claude_agent.py $name --profile agents/profiles/$profile.yaml $ONCE_FLAG" Enter
    fi

    PANE=$((PANE + 1))
    # Stagger agent starts to avoid thundering herd on register
    DELAY=$((DELAY + 2))
done

# Select top-left pane and attach
tmux select-pane -t "$SESSION:fleet.0"
tmux attach-session -t "$SESSION"
