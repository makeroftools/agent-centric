"""Versioned core contracts for the Meta-Harness.

This package defines the stable, strongly-typed contracts that the control
plane, agents, and verifiers all agree on. Each contract carries an explicit
version so that it can evolve without silently breaking the correctness model.
"""

from .capability import Capability
from .critical_path import CpmMetric, CpmVersion, CriticalPathResult, CriticalPathStage
from .handoff import HandoffSchema, is_valid_schema, validate_handoff
from .manifest import AgentComponentManifest, AgentManifestVersion
from .model import (
    ModelProvider,
    ModelProviderError,
    ModelProviderVersion,
    ModelResponse,
)
from .parallel import ParallelComposition, ParallelVersion
from .pipeline import PipelineVersion, SequentialComposition, StageSpec
from .policy import Policy, PolicyDecision, PolicyVersion
from .replay import ReplayDiff, ReplayResult, ReplayVersion
from .result import Failure, FailureReason, VerifiedResult, VerifiedResultVersion
from .summary import (
    ModelSummary,
    PolicySummary,
    RunState,
    StageKind,
    StageSummary,
    SummaryVersion,
    ToolSummary,
    TrajectorySummary,
)
from .task import ResourceEnvelope, TaskSpecification, TaskSpecVersion
from .tool import ToolDescriptor, ToolVersion
from .trajectory import (
    StepRecord,
    StepStatus,
    Trajectory,
    TrajectoryVersion,
)

__all__ = [
    "Capability",
    "CpmMetric",
    "CpmVersion",
    "CriticalPathResult",
    "CriticalPathStage",
    "HandoffSchema",
    "is_valid_schema",
    "validate_handoff",
    "ModelProvider",
    "ModelProviderError",
    "ModelProviderVersion",
    "ModelResponse",
    "AgentComponentManifest",
    "AgentManifestVersion",
    "ParallelComposition",
    "ParallelVersion",
    "PipelineVersion",
    "SequentialComposition",
    "StageSpec",
    "Policy",
    "PolicyDecision",
    "PolicyVersion",
    "ResourceEnvelope",
    "TaskSpecification",
    "TaskSpecVersion",
    "ToolDescriptor",
    "ToolVersion",
    "StepRecord",
    "StepStatus",
    "Trajectory",
    "TrajectoryVersion",
    "Failure",
    "FailureReason",
    "VerifiedResult",
    "VerifiedResultVersion",
    "ReplayResult",
    "ReplayDiff",
    "ReplayVersion",
    "TrajectorySummary",
    "SummaryVersion",
    "RunState",
    "StageKind",
    "StageSummary",
    "ToolSummary",
    "ModelSummary",
    "PolicySummary",
]
