"""Control-plane components: the deterministic Agent Manager and the Verifier.

The control plane is the only part of the system that governs agents. It is
deterministic, enforces resource bounds hard, records a full trajectory, and
never accepts a result without passing the mandatory verification gate.
"""

from .manager import AgentManager, Outcome
from .registry import Registry
from .tools import ToolExecutionError, ToolRegistry
from .trajectory_store import (
    CorruptTrajectoryError,
    FileTrajectoryStore,
    InMemoryTrajectoryStore,
    StoredOutcome,
    StoredTrajectory,
    TrajectoryStore,
    TrajectoryStoreError,
)
from .verifier import VerificationResult, Verifier

__all__ = [
    "Verifier",
    "VerificationResult",
    "AgentManager",
    "Outcome",
    "Registry",
    "ToolRegistry",
    "ToolExecutionError",
    "TrajectoryStore",
    "TrajectoryStoreError",
    "CorruptTrajectoryError",
    "FileTrajectoryStore",
    "InMemoryTrajectoryStore",
    "StoredOutcome",
    "StoredTrajectory",
]
