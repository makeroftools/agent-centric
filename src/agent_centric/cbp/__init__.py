"""Agent-centric CBP foundation.

This package is the pivot to a truly agent-centric, component-based architecture.
It implements the deterministic core of the model:

- a rooted, recursive **tree of nodes** (no central AgentManager),
- hierarchical **context** as the governance mechanism,
- the **init / run / kill** node contract,
- the **shell** as the root node,
- **verification on the upward path** (the correctness spine),
- the **directive/response protocol** as the crux (the language agents speak),
- the **abstract Agent** (one type) with a steppable async poll loop over a
  dynamic channel set,
- **ZeroMQ** (`zmq_poll`) as the first-class comms channel,
- the **fractal principle**: every task is an agent, recursively, down to
  individual instructions,
- **Critical Path Method (CPM)** as a first-class, deterministic, read-only
  observational tool.

The core is pure, deterministic, and offline-testable via ``inproc://``.
Real distribution (``tcp://``/``ipc://``) and a FastAPI layer are deferred
adapters (see ``spec.md`` and ``protocol.md``).
"""

from __future__ import annotations

from .activity import (
    ACTIVITY_KINDS,
    MAX_ACTIVITY_ENTRIES,
    ActivityEntry,
    ActivityError,
    ActivityFeed,
)
from .agent import Agent
from .audit import AuditChain, ChainEvent, reconstruct_chains
from .bills import (
    BillsError,
    accept_draft,
    bill_total,
    draft_from_intake,
    mark_bill_status,
    project_calendar,
)
from .bills_agent import (
    TASK_ACCEPT_DETERMINISTIC,
    TASK_REGISTRY,
    TASK_RULE_ADD,
    BillsAgent,
)
from .cache import (
    CacheError,
    ContentAddressedCache,
    atomic_write,
    sha256_bytes,
    sha256_file,
)
from .chat_pipeline import (
    PinCache,
    canonical_json,
    canonicalize,
    request_key,
    schema_parse,
)
from .chatstore import ChatHistoryError, ChatHistoryStore, open_chat_history
from .component_boot import (
    BootedComponent,
    BootedTree,
    BootError,
    boot_from_lock,
)
from .component_bundle import (
    BUNDLE_MANIFEST,
    BundleError,
    build_bundle,
    build_bundle_from_dir,
    bundle_sha256,
    component_json,
    list_members,
    load_manifest,
    read_member,
)
from .component_graph import (
    ComponentGraphResolver,
    GraphError,
    ResolvedChild,
    ResolvedNode,
)
from .component_process import (
    ComponentProcessError,
    extract_bundle,
    run_component,
)
from .component_runtime import (
    AllowlistedEntryResolver,
    EntryNotAllowed,
)
from .component_source import (
    GitError,
    GitSource,
    PinnedComponent,
    bundle_at,
    git_commit,
    pin_from_git,
)
from .component_state import (
    StateError,
    StateScope,
    StateSovereigntyError,
    check_integrity,
    connect_state,
    materialize_state,
    state_path,
)
from .config import AgentConfig
from .context import Context, Verifier
from .critical_path import CpmAnalysis, CpmError, CpmNode, analyse_cpm, cpm_from_dict
from .design import (
    CompiledDesign,
    DesignError,
    DesignReport,
    compile_design,
    validate_design,
)
from .determinism import DeterminismRating, Rule, RuleSet, resolve_with_rules, score_determinism
from .domainrepo import (
    Artifact,
    ArtifactVault,
    DomainRecord,
    DomainRegistry,
    RegistryError,
)
from .driver import (
    CbpDriver,
    ledger_callables,
    load_ledger,
    replay_ledger,
    summarise_ledger,
)
from .envelopes import EnvelopeGuard, ResourceEnvelope
from .experts import (
    EXPERT_DETERMINISTIC,
    EXPERT_HUMAN,
    EXPERT_KINDS,
    EXPERT_LEARNED,
    CostAccount,
    CostLedger,
    Domain,
    ExpertSelection,
    ExpertsError,
    domain_from_dict,
    select_expert,
)
from .flow import (
    BoundedConnection,
    FlowError,
    InformationPacket,
    run_flow,
)
from .intake import draft_from_email, draft_from_file
from .ledger import DirectiveLedger
from .message import (
    DIRECTIVE_AUDIT,
    DIRECTIVE_CONFIGURE,
    DIRECTIVE_KILL,
    DIRECTIVE_PING,
    DIRECTIVE_REGISTER,
    DIRECTIVE_RESOLVE,
    DIRECTIVE_RUN,
    DIRECTIVE_SPAWN,
    DIRECTIVE_STATE_GET,
    DIRECTIVE_STATE_SET,
    MESSAGE_ACK,
    MESSAGE_DIRECTIVE,
    MESSAGE_RESPONSE,
    PROTOCOL_VERSION,
    RESPONSE_ERROR,
    RESPONSE_OK,
    RESPONSE_RESULT,
    RESPONSE_TELEMETRY,
    Ack,
    Directive,
    ProtocolError,
    Response,
    validate_ack,
    validate_directive,
    validate_response,
)
from .model_agent import TASK_MODEL, ModelAgent
from .model_catalog import (
    BASE_MODELS,
    SIZE_CLASSES,
    SIZE_EDGE,
    SIZE_LARGE,
    SIZE_MID,
    SIZE_SMALL,
    BaseModel,
    CatalogError,
    catalog,
    min_tier_for,
    resolve_base,
)
from .model_record import (
    MAX_MODEL_CONFIDENCE,
    MODEL_RECORD_SCHEMA,
    PROVIDER_EXTERNAL,
    PROVIDER_KINDS,
    PROVIDER_STUB,
    ModelConfidence,
    ModelRecord,
    ModelRecordError,
    ModelRecordLog,
    is_content_hash,
    score_model_confidence,
)
from .network import (
    DEFAULT_CONNECTION_CAPACITY,
    MAX_CONNECTION_CAPACITY,
    Component,
    ComponentNetwork,
    Edge,
    NetworkError,
    network_from_dict,
    run_network,
)
from .node import AgentNode, Node
from .node import Response as NodeResponse
from .orchestrate import (
    SCHEMAS,
    SUPPORTED_INTENTS,
    extract_intent,
    plan_from_artifact,
    plan_from_schema,
    run_artifact_plan,
)
from .pdf_intake import draft_from_pdf_text, extract_text
from .provider_runtime import (
    PROVIDER_EVIDENCE_ACTIONS,
    PROVIDER_EVIDENCE_SCHEMA,
    InMemoryProviderAdapter,
    NotificationSink,
    NullNotificationSink,
    ProviderAdapter,
    ProviderCall,
    ProviderCallKind,
    ProviderEvidence,
    ProviderLedger,
    ProviderResult,
    ProviderRuntime,
    ProviderRuntimeError,
    ReconcileResult,
    emit_notification,
)
from .providers import (
    ModalSlmProvider,
    ProviderRegistry,
    ReplicateSlmProvider,
    RunpodSlmProvider,
    TrainingCredentials,
    TrainingHttpClient,
    build_credential_client,
    build_modal_from_env,
    build_modal_provider,
    credentials_from_env,
    get_provider,
    provider_names,
    stdlib_http_client,
)
from .providers import (
    redact_secrets as redact_training_secrets,
)
from .provision import (
    ProvisionError,
    ProvisionResult,
    provision_expert,
)
from .resolver import (
    ComponentSource,
    DirectorySource,
    ResolvedComponent,
    ResolveError,
    Resolver,
)
from .review import (
    ReviewError,
    ReviewQueue,
    enqueue_for_review,
)
from .security import (
    INTEGRITY_TAG,
    PeerAuthz,
    PeerPolicy,
    TlsCreds,
    attach_integrity,
    configure_tls,
    integrity_headers,
    sign_payload,
    verify_and_strip_integrity,
    verify_payload,
)
from .security import (
    canonical_json as security_canonical_json,
)
from .settlement import (
    Settlement,
    SettlementError,
    SettlementGrant,
    SettlementProvider,
    StubSettlementProvider,
    settle_run,
)
from .shell import Shell
from .signing import (
    GpgVerifier,
    MinisignVerifier,
    SignatureError,
    SignatureVerifier,
    SystemVerifier,
    tool_available,
)
from .signing_service import (
    GpgSigner,
    MinisignSigner,
    SignedLock,
    Signer,
    SigningError,
    record_release,
    sign_lock,
    signing_backend,
)
from .slm import (
    SlmError,
    SlmExpert,
    SlmProvider,
    SlmSpec,
    StubSlmProvider,
    build_domain_expert,
)
from .store import StateStore, StoreError, TrajectoryStore, open_state, open_trajectory
from .store_agent import STORE_GET, STORE_KEYS, STORE_SET, StoreAgent
from .training_plan import (
    TIER_CPU,
    TIER_KINDS,
    TIER_MULTI_GPU,
    TIER_SINGLE_GPU,
    TIERS,
    TrainingError,
    TrainingPlan,
    TrainingTier,
    plan_training,
)
from .transparency import (
    GENESIS,
    LOG_SCHEMA,
    TransparencyEntry,
    TransparencyError,
    TransparencyLog,
)
from .transport import (
    IPC_SOCKET_MODE,
    SECURITY_DEFAULT,
    SECURITY_LOCAL,
    SECURITY_LOOPBACK,
    SECURITY_TLS,
    TransportSecurityError,
    check_tcp_bind,
    enforce_ipc_socket_mode,
    validate_ipc_socket_mode,
)
from .web import DEFAULT_HOST, DEFAULT_PORT, CbpLandingServer
from .web import serve as serve_landing
from .work_runtime import (
    BoundTask,
    MaterializedWork,
    TaskLoader,
    WorkBinder,
    WorkRuntimeError,
    compile_and_materialize,
    materialize_work,
)
from .workspace import (
    DIRECTORY,
    FILE,
    WorkspaceEntry,
    WorkspaceError,
    WorkspaceFS,
    WorkspaceLayout,
)

__all__ = [
    # Agent
    "Agent",
    "AgentConfig",
    "CbpDriver",
    # Context / governance
    "Context",
    "Verifier",
    # Protocol
    "PROTOCOL_VERSION",
    "MESSAGE_DIRECTIVE",
    "MESSAGE_ACK",
    "MESSAGE_RESPONSE",
    "DIRECTIVE_CONFIGURE",
    "DIRECTIVE_RUN",
    "DIRECTIVE_SPAWN",
    "DIRECTIVE_PING",
    "DIRECTIVE_KILL",
    "DIRECTIVE_REGISTER",
    "DIRECTIVE_RESOLVE",
    "DIRECTIVE_STATE_GET",
    "DIRECTIVE_STATE_SET",
    "DIRECTIVE_AUDIT",
    "RESPONSE_OK",
    "RESPONSE_RESULT",
    "RESPONSE_ERROR",
    "RESPONSE_TELEMETRY",
    "Directive",
    "Ack",
    "Response",
    "ProtocolError",
    "validate_directive",
    "validate_ack",
    "validate_response",
    # Foundation node contract
    "AgentNode",
    "Node",
    "NodeResponse",
    "Shell",
    # Durable, on-demand persistence (state + trajectory/audit)
    "StateStore",
    "TrajectoryStore",
    "StoreError",
    "open_state",
    "open_trajectory",
    # Durable chat-history (model-box transcript; explicit opt-in)
    "ChatHistoryStore",
    "ChatHistoryError",
    "open_chat_history",
    # Operator activity feed (bounded, append-only, optionally-durable audit)
    "ActivityFeed",
    "ActivityEntry",
    "ActivityError",
    "ACTIVITY_KINDS",
    "MAX_ACTIVITY_ENTRIES",
    # Post-return determinism + orchestration (chat → CBP seam)
    "PinCache",
    "canonical_json",
    "canonicalize",
    "schema_parse",
    "request_key",
    "SUPPORTED_INTENTS",
    "extract_intent",
    "plan_from_artifact",
    "plan_from_schema",
    "run_artifact_plan",
    "SCHEMAS",
    # Component Networks (visual programming core)
    "Component",
    "ComponentNetwork",
    "Edge",
    "NetworkError",
    "BoundedConnection",
    "InformationPacket",
    "FlowError",
    "run_flow",
    "DEFAULT_CONNECTION_CAPACITY",
    "MAX_CONNECTION_CAPACITY",
    "network_from_dict",
    "run_network",
    "StoreAgent",
    "STORE_GET",
    "STORE_SET",
    "STORE_KEYS",
    # Transport trust-boundary enforcement (docs/transport_trust_boundary.md)
    "TransportSecurityError",
    "check_tcp_bind",
    "enforce_ipc_socket_mode",
    "validate_ipc_socket_mode",
    "SECURITY_LOOPBACK",
    "SECURITY_LOCAL",
    "SECURITY_TLS",
    "SECURITY_DEFAULT",
    "IPC_SOCKET_MODE",
    # Transport security primitives (security.py: §5.2/§5.4/§5.5)
    "PeerAuthz",
    "PeerPolicy",
    "TlsCreds",
    "configure_tls",
    "sign_payload",
    "verify_payload",
    "integrity_headers",
    "attach_integrity",
    "verify_and_strip_integrity",
    "security_canonical_json",
    "INTEGRITY_TAG",
    # Component distribution (SPEC-0007): lock, cache, resolver, signing
    "CacheError",
    "ContentAddressedCache",
    "atomic_write",
    "sha256_bytes",
    "sha256_file",
    "ComponentSource",
    "DirectorySource",
    "ResolveError",
    "ResolvedComponent",
    "Resolver",
    "GpgVerifier",
    "MinisignVerifier",
    "SignatureError",
    "SignatureVerifier",
    "SystemVerifier",
    "tool_available",
    # Release signing + transparency (SPEC-0007 Phase 0.5b)
    "Signer",
    "SigningError",
    "GpgSigner",
    "MinisignSigner",
    "SignedLock",
    "sign_lock",
    "record_release",
    "signing_backend",
    "TransparencyLog",
    "TransparencyEntry",
    "TransparencyError",
    "LOG_SCHEMA",
    "GENESIS",
    # Component boot (SPEC-0007 Phase 2): resolve -> verify -> instantiate
    "BootError",
    "BootedComponent",
    "BootedTree",
    "boot_from_lock",
    # Component process isolation (SPEC-0007 Phase 2)
    "ComponentProcessError",
    "extract_bundle",
    "run_component",
    # Component bundle / source / runtime (SPEC-0007 Phase 1a)
    "BUNDLE_MANIFEST",
    "BundleError",
    "build_bundle",
    "build_bundle_from_dir",
    "bundle_sha256",
    "component_json",
    "load_manifest",
    "list_members",
    "read_member",
    "ComponentGraphResolver",
    "GraphError",
    "ResolvedChild",
    "ResolvedNode",
    "StateError",
    "StateScope",
    "StateSovereigntyError",
    "check_integrity",
    "connect_state",
    "materialize_state",
    "state_path",
    "AllowlistedEntryResolver",
    "EntryNotAllowed",
    "GitError",
    "GitSource",
    "PinnedComponent",
    "bundle_at",
    "git_commit",
    "pin_from_git",
    # Design document: validate/compile (SPEC-0008 Phase 1)
    "DesignError",
    "DesignReport",
    "CompiledDesign",
    "validate_design",
    "compile_design",
    # CPM: a read-only, deterministic capability (not an agent)
    "CpmAnalysis",
    "CpmNode",
    "CpmError",
    "analyse_cpm",
    "cpm_from_dict",
    # Determinism rating + approved rules (determinize-then-decide)
    "DeterminismRating",
    "Rule",
    "RuleSet",
    "score_determinism",
    "resolve_with_rules",
    # Resource envelopes (hard, enforced bounds in the CBP tree)
    "ResourceEnvelope",
    "EnvelopeGuard",
    # Expert selection + cost/residue ledger (Network of Experts AI)
    "Domain",
    "ExpertSelection",
    "select_expert",
    "CostAccount",
    "CostLedger",
    "EXPERT_KINDS",
    "EXPERT_DETERMINISTIC",
    "EXPERT_LEARNED",
    "EXPERT_HUMAN",
    "ExpertsError",
    "domain_from_dict",
    # Domain Registry + artifact vault (observability/provenance)
    "DomainRegistry",
    "DomainRecord",
    "ArtifactVault",
    "Artifact",
    "RegistryError",
    # Domain-expert SLM provider contract (the learned tier)
    "SlmProvider",
    "SlmSpec",
    "SlmExpert",
    "SlmError",
    "StubSlmProvider",
    "build_domain_expert",
    # Micro-payment / settlement adapter (the paid tier; opt-in, never auto-charge)
    "SettlementProvider",
    "SettlementGrant",
    "Settlement",
    "SettlementError",
    "StubSettlementProvider",
    "settle_run",
    # External training-provider adapters (the learned tier; opt-in, operator-selected)
    "ProviderRegistry",
    "provider_names",
    "get_provider",
    "ModalSlmProvider",
    "RunpodSlmProvider",
    "ReplicateSlmProvider",
    "build_modal_provider",
    "TrainingHttpClient",
    "TrainingCredentials",
    "credentials_from_env",
    "build_credential_client",
    "build_modal_from_env",
    "stdlib_http_client",
    "redact_training_secrets",
    # Recommended base-model catalog (model_catalog.py)
    "BaseModel",
    "CatalogError",
    "BASE_MODELS",
    "SIZE_CLASSES",
    "SIZE_EDGE",
    "SIZE_SMALL",
    "SIZE_MID",
    "SIZE_LARGE",
    "resolve_base",
    "min_tier_for",
    "catalog",
    # End-to-end expert provisioning (select -> plan -> train -> account -> settle)
    "provision_expert",
    "ProvisionResult",
    "ProvisionError",
    # Deterministic training-plan tier (strategic hardware provisioning)
    "TrainingPlan",
    "TrainingTier",
    "TrainingError",
    "plan_training",
    "TIERS",
    "TIER_KINDS",
    "TIER_CPU",
    "TIER_SINGLE_GPU",
    "TIER_MULTI_GPU",
    # Model agent (an LLM as an ordinary first-class agent)
    "ModelAgent",
    "TASK_MODEL",
    # Deterministic model boundary (record + confidence score; SPEC-0009 §4)
    "MODEL_RECORD_SCHEMA",
    "MAX_MODEL_CONFIDENCE",
    "PROVIDER_STUB",
    "PROVIDER_EXTERNAL",
    "PROVIDER_KINDS",
    "ModelConfidence",
    "ModelRecord",
    "ModelRecordLog",
    "ModelRecordError",
    "is_content_hash",
    "score_model_confidence",
    # Persistent append-only Review queue (review.v1; SPEC-0002 §4, §6)
    "ReviewQueue",
    "ReviewError",
    "enqueue_for_review",
    # Provider hosting runtime (provider.v1; SPEC-0020 §1-§4)
    "ProviderRuntime",
    "ProviderRuntimeError",
    "ProviderCall",
    "ProviderCallKind",
    "ProviderResult",
    "ProviderAdapter",
    "ProviderEvidence",
    "ProviderLedger",
    "PROVIDER_EVIDENCE_SCHEMA",
    "PROVIDER_EVIDENCE_ACTIONS",
    "ReconcileResult",
    "NotificationSink",
    "NullNotificationSink",
    "emit_notification",
    "InMemoryProviderAdapter",
    # Bills loop (real end-to-end CBP graph)
    "BillsAgent",
    "TASK_ACCEPT_DETERMINISTIC",
    "TASK_REGISTRY",
    "TASK_RULE_ADD",
    "BillsError",
    "bill_total",
    "draft_from_intake",
    "accept_draft",
    "mark_bill_status",
    "project_calendar",
    # Read-only tree-audit reconstruction (audit as proof)
    "AuditChain",
    "ChainEvent",
    "reconstruct_chains",
    # Durable, recoverable directive ledger (crash-safe replay)
    "DirectiveLedger",
    "load_ledger",
    "ledger_callables",
    "replay_ledger",
    "summarise_ledger",
    # PDF intake (port of main's deterministic, offline capability)
    "extract_text",
    "draft_from_pdf_text",
    # Structured intake (port of main's deterministic, offline heuristics)
    "draft_from_file",
    "draft_from_email",
    # Allowlisted workspace capability (port of main's hardened workspace)
    "WorkspaceFS",
    "WorkspaceLayout",
    "WorkspaceEntry",
    "WorkspaceError",
    "FILE",
    "DIRECTORY",
    # Work runtime (work.v1; SPEC-0021): compile/materialize + task-load binder
    "WorkRuntimeError",
    "MaterializedWork",
    "materialize_work",
    "compile_and_materialize",
    "TaskLoader",
    "BoundTask",
    "WorkBinder",
    # Landing-page server (stdlib http.server; local-only, actionable)
    "CbpLandingServer",
    "serve_landing",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
]


# The CBP source tree, used by the ``cbp-web --reload`` dev watcher.
from pathlib import Path  # noqa: E402  (kept at the end, after __all__)

_CBP_DIR = Path(__file__).resolve().parent