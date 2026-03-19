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
            echo "  Ctrl-b w      List windows"
            echo "  Ctrl-b n/p    Next/prev window"
            echo "  Ctrl-b 0-3    Jump to window by number"
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
# Layout (4 windows):
#
#   0: suricata   — Backend agent
#   1: lagarto    — QA agent
#   2: unicornio  — Frontend agent
#   3: rocky      — Architect agent

echo ""
echo "  ┌─────────────────────────────────────────────┐"
echo "  │         SHIPYARD AGENT FLEET                 │"
echo "  │                                              │"
echo "  │  Window 0: suricata  (Backend)               │"
echo "  │  Window 1: lagarto   (QA)                    │"
echo "  │  Window 2: unicornio (Frontend)              │"
echo "  │  Window 3: rocky     (Architect)             │"
echo "  │                                              │"
echo "  │  Ctrl-b w  = list windows                    │"
echo "  │  Ctrl-b n  = next window                     │"
echo "  │  Ctrl-b d  = detach (keeps running)          │"
echo "  └─────────────────────────────────────────────┘"
echo ""

# Agent definitions: name, profile
declare -a AGENTS=(
    "suricata:backend"
    "lagarto:qa"
    "unicornio:frontend"
    "rocky:architect"
)

FIRST=true
DELAY=0

for entry in "${AGENTS[@]}"; do
    IFS=":" read -r name profile <<< "$entry"

    if [ "$FIRST" = true ]; then
        # Create session with first agent window
        tmux new-session -d -s "$SESSION" -n "$name" -c "$PROJECT_DIR"
        FIRST=false
    else
        tmux new-window -t "$SESSION" -n "$name" -c "$PROJECT_DIR"
    fi

    if [ "$USE_TEST_AGENT" = true ]; then
        tmux send-keys -t "$SESSION:$name" \
            "sleep $DELAY && echo '── Agent: $name (test mode) ──' && python3 agents/test_agent.py $name" Enter
    else
        tmux send-keys -t "$SESSION:$name" \
            "sleep $DELAY && echo '── Agent: $name ($profile) ──' && OPENROUTER_API_KEY=$OPENROUTER_API_KEY python3 agents/claude_agent.py $name --profile agents/profiles/$profile.yaml $ONCE_FLAG" Enter
    fi

    # Stagger agent starts to avoid thundering herd on register
    DELAY=$((DELAY + 2))
done

# Attach to session
tmux select-window -t "$SESSION:suricata"
tmux attach-session -t "$SESSION"
