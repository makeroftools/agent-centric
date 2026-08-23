"""Agent-centric FBP foundation (branch `agent-centric-fbp`).

This package is the pivot to a truly agent-centric, flow-based architecture.
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

from .agent import Agent, register_callable
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
from .chat_pipeline import (
    PinCache,
    canonical_json,
    canonicalize,
    request_key,
    schema_parse,
)
from .chatstore import ChatHistoryError, ChatHistoryStore, open_chat_history
from .config import AgentConfig
from .context import Context, Verifier
from .critical_path import CpmAnalysis, CpmError, CpmNode, analyse_cpm, cpm_from_dict
from .determinism import DeterminismRating, Rule, RuleSet, resolve_with_rules, score_determinism
from .domainrepo import (
    Artifact,
    ArtifactVault,
    DomainRecord,
    DomainRegistry,
    RegistryError,
)
from .driver import (
    FbpDriver,
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
from .network import (
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
from .security import (
    INTEGRITY_TAG,
    PeerAuthz,
    PeerPolicy,
    TlsCreds,
    configure_tls,
    integrity_headers,
    sign_payload,
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
from .web import DEFAULT_HOST, DEFAULT_PORT, FbpLandingServer
from .web import serve as serve_landing
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
    "FbpDriver",
    "register_callable",
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
    # Post-return determinism + orchestration (chat → FBP seam)
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
    "security_canonical_json",
    "INTEGRITY_TAG",
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
    # Resource envelopes (hard, enforced bounds in the FBP tree)
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
    # Bills loop (real end-to-end FBP graph)
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
    # Landing-page server (stdlib http.server; local-only, actionable)
    "FbpLandingServer",
    "serve_landing",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
]


# The FBP source tree, used by the ``fbp-web --reload`` dev watcher.
from pathlib import Path  # noqa: E402  (kept at the end, after __all__)

_FBP_DIR = Path(__file__).resolve().parent

