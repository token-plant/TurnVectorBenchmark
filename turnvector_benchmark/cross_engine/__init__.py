"""Python core for the Benchmark-owned cross-engine serving surface."""

from .artifacts import (
    ArtifactRecord,
    ArtifactSpec,
    build_artifact_manifest,
    load_artifact_manifest,
    validate_artifact_manifest,
    write_artifact_manifest,
    write_sha256s_from_manifest,
)
from .contracts import (
    ARTIFACT_MANIFEST_SCHEMA_VERSION,
    LIFECYCLE_PROTOCOL_VERSION,
    OPENAI_SERVING_PROTOCOL_VERSION,
    CrossEngineContractError,
)
from .lifecycle import (
    EngineLifecycleClient,
    LifecycleRemoteError,
    LifecycleState,
    LifecycleStateMachine,
)
from .metrics import (
    RequestMetrics,
    RequestObservation,
    TrialMetrics,
    nearest_rank,
    observation_from_stream_result,
    reduce_request_metrics,
    reduce_trial_metrics,
    summarize_observations,
)
from .openai import (
    OpenAIEndpoint,
    OpenAIHTTPClient,
    OpenAIHTTPError,
    OpenAIProtocolError,
    OpenAIStreamResult,
    ParsedChatCompletion,
    SSEParser,
    build_chat_request,
    parse_endpoint_descriptor,
)

__all__ = (
    "ARTIFACT_MANIFEST_SCHEMA_VERSION",
    "ArtifactRecord",
    "ArtifactSpec",
    "CrossEngineContractError",
    "EngineLifecycleClient",
    "LIFECYCLE_PROTOCOL_VERSION",
    "LifecycleRemoteError",
    "LifecycleState",
    "LifecycleStateMachine",
    "OPENAI_SERVING_PROTOCOL_VERSION",
    "OpenAIEndpoint",
    "OpenAIHTTPClient",
    "OpenAIHTTPError",
    "OpenAIProtocolError",
    "OpenAIStreamResult",
    "ParsedChatCompletion",
    "RequestMetrics",
    "RequestObservation",
    "SSEParser",
    "TrialMetrics",
    "build_artifact_manifest",
    "build_chat_request",
    "load_artifact_manifest",
    "nearest_rank",
    "observation_from_stream_result",
    "parse_endpoint_descriptor",
    "reduce_request_metrics",
    "reduce_trial_metrics",
    "summarize_observations",
    "validate_artifact_manifest",
    "write_artifact_manifest",
    "write_sha256s_from_manifest",
)
