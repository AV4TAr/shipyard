"""Lightweight test agent that exercises the full SDK flow without needing an LLM.

Registers, polls for tasks, claims one, submits a fake diff, and triggers the pipeline.
Used to verify the auto-cascade chain works end-to-end.
"""

import sys
import time
import uuid

import requests

BASE = "http://localhost:8001"
AGENT_NAME = sys.argv[1] if len(sys.argv) > 1 else "test-bot"
AGENT_ID = f"agent-{AGENT_NAME}"


def log(msg):
    print(f"[{AGENT_ID}] {msg}", flush=True)


def register():
    r = requests.post(f"{BASE}/api/agents/sdk/register", json={
        "agent_id": AGENT_ID,
        "name": AGENT_NAME,
        "capabilities": ["backend", "testing"],
        "languages": ["python"],
    })
    log(f"Registered: {r.status_code}")


def poll_tasks():
    try:
        r = requests.get(f"{BASE}/api/agents/sdk/tasks", timeout=10)
        return r.json()
    except Exception as e:
        log(f"Poll error: {e}")
        return []


def claim_task(task_id):
    try:
        r = requests.post(f"{BASE}/api/agents/sdk/tasks/{task_id}/claim", timeout=10)
        if r.status_code == 200:
            log(f"Claimed: {r.json().get('title', task_id)}")
            return True
        log(f"Claim failed: {r.status_code} {r.text[:100]}")
    except Exception as e:
        log(f"Claim error: {e}")
    return False


def submit_work(task_id):
    """Submit a fake diff to trigger the pipeline."""
    try:
        r = requests.post(f"{BASE}/api/agents/sdk/tasks/{task_id}/submit", json={
            "task_id": str(task_id),
            "agent_id": AGENT_ID,
            "intent_id": str(uuid.uuid4()),
            "diff": "--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1,5 @@\n-pass\n+# implemented by " + AGENT_ID,
            "description": f"Simulated implementation by {AGENT_ID}",
            "files_changed": ["src/app.py"],
        }, timeout=30)
        if r.status_code == 200:
            data = r.json()
            status = data.get("status", "?")
            log(f"Submitted: status={status}")
            return True
        log(f"Submit failed: {r.status_code} {r.text[:200]}")
    except Exception as e:
        log(f"Submit error: {e}")
    return False


def main():
    register()
    log("Polling for tasks...")

    while True:
        tasks = poll_tasks()
        if not tasks:
            log("No tasks, waiting 5s...")
            time.sleep(5)
            continue

        log(f"Found {len(tasks)} task(s)")
        task = tasks[0]
        task_id = task["task_id"]
        log(f"Working on: {task['title']}")

        if claim_task(task_id):
            time.sleep(2)  # simulate work
            submit_work(task_id)

        time.sleep(3)


if __name__ == "__main__":
    main()
