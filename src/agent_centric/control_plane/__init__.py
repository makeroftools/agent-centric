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
from .bills_registry import BillsOps, ensure_bills_layout
from .critical_path import analyse_critical_path
from .email_tools import EmailTools
from .execution import (
    AgentExecutionError,
    ExecutionBackend,
    InProcessBackend,
    SubprocessBackend,
)
from .intake import IntakeOps, ensure_intake_layout
from .manager import AgentManager, Outcome
from .mcp_tools import (
    LocalMcpServer,
    McpProtocolError,
    McpTimeoutError,
    McpToolAdapter,
    McpToolCallError,
    McpToolError,
)
from .registry import Registry
from .replay import ReplayDiff, ReplayResult, ReplayVersion, verify_replay
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
from .workspace import Workspace, WorkspaceError

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
    "McpToolAdapter",
    "McpToolError",
    "McpProtocolError",
    "McpToolCallError",
    "McpTimeoutError",
    "LocalMcpServer",
    "Registry",
    "ToolRegistry",
    "ToolExecutionError",
    "Workspace",
    "WorkspaceError",
    "EmailTools",
    "BillsOps",
    "ensure_bills_layout",
    "IntakeOps",
    "ensure_intake_layout",
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
    "ReplayResult",
    "ReplayDiff",
    "ReplayVersion",
    "verify_replay",
]
