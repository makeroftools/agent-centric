"""Meta-Harness: a deterministic control plane for governed, verifiable agents.

Public surface is intentionally minimal. The primary entry point is
``AgentManager`` from the control-plane package; contracts live in
``meta_harness.contracts``.
"""

from .agents import ToolContext, ToolRequest, ToolResult
from .contracts import (
    AgentComponentManifest,
    AgentManifestVersion,
    Capability,
    Failure,
    FailureReason,
    ParallelComposition,
    ParallelVersion,
    PipelineVersion,
    Policy,
    PolicyDecision,
    PolicyVersion,
    ResourceEnvelope,
    SequentialComposition,
    StageSpec,
    StepRecord,
    StepStatus,
    TaskSpecification,
    TaskSpecVersion,
    ToolDescriptor,
    ToolVersion,
    Trajectory,
    TrajectoryVersion,
    VerifiedResult,
    VerifiedResultVersion,
)
from .control_plane import (
    AgentManager,
    Outcome,
    Registry,
    ToolExecutionError,
    ToolRegistry,
)

__all__ = [
    "AgentManager",
    "Outcome",
    "Registry",
    "ToolContext",
    "ToolRequest",
    "ToolResult",
    "ToolRegistry",
    "ToolExecutionError",
    "AgentComponentManifest",
    "AgentManifestVersion",
    "Capability",
    "Failure",
    "FailureReason",
    "ParallelComposition",
    "ParallelVersion",
    "PipelineVersion",
    "Policy",
    "PolicyDecision",
    "PolicyVersion",
    "ResourceEnvelope",
    "SequentialComposition",
    "StageSpec",
    "StepRecord",
    "StepStatus",
    "TaskSpecification",
    "TaskSpecVersion",
    "ToolDescriptor",
    "ToolVersion",
    "Trajectory",
    "TrajectoryVersion",
    "VerifiedResult",
    "VerifiedResultVersion",
]