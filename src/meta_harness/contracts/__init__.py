"""Versioned core contracts for the Meta-Harness.

This package defines the stable, strongly-typed contracts that the control
plane, agents, and verifiers all agree on. Each contract carries an explicit
version so that it can evolve without silently breaking the correctness model.
"""

from .capability import Capability
from .handoff import HandoffSchema, is_valid_schema, validate_handoff
from .manifest import AgentComponentManifest, AgentManifestVersion
from .pipeline import PipelineVersion, SequentialComposition, StageSpec
from .policy import Policy, PolicyDecision, PolicyVersion
from .result import Failure, FailureReason, VerifiedResult, VerifiedResultVersion
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
    "HandoffSchema",
    "is_valid_schema",
    "validate_handoff",
    "AgentComponentManifest",
    "AgentManifestVersion",
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
]
