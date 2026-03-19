"""Lease-based task claim management.

When an agent claims a task, it gets a lease with an expiration time.
The agent must send heartbeats to renew the lease. Expired leases are
automatically swept, resetting tasks back to PENDING so other agents
can pick them up.
"""

from .manager import LeaseManager

__all__ = ["LeaseManager"]
