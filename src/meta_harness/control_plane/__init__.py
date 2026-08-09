"""Control-plane components: the deterministic Agent Manager and the Verifier.

The control plane is the only part of the system that governs agents. It is
deterministic, enforces resource bounds hard, records a full trajectory, and
never accepts a result without passing the mandatory verification gate.
"""

from ..contracts.summary import (
    ModelSummary,
    PolicySummary,
    RunState,
    StageKind,
    StageSummary,
    SummaryVersion,
    ToolSummary,
    TrajectorySummary,
)
from .critical_path import analyse_critical_path
from .execution import (
    AgentExecutionError,
    ExecutionBackend,
    InProcessBackend,
    SubprocessBackend,
)
from .manager import AgentManager, Outcome
from .registry import Registry
from .summary import summarise_stored, summarise_trajectory
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
    "analyse_critical_path",
    "AgentManager",
    "Outcome",
    "ExecutionBackend",
    "InProcessBackend",
    "SubprocessBackend",
    "AgentExecutionError",
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
    "TrajectorySummary",
    "SummaryVersion",
    "RunState",
    "StageKind",
    "StageSummary",
    "ToolSummary",
    "ModelSummary",
    "PolicySummary",
    "summarise_stored",
    "summarise_trajectory",
]
