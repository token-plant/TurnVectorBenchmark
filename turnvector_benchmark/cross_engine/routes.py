from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from ..core import ContractError


ROUTE_MAPPER_VERSION = "turnvector.benchmark.cross-engine-route-mapper.v1"
ROUTE_DIMENSIONS = ("backend", "execution", "cache", "model_state", "fallback")
ROUTE_VALUES = {
    "backend": frozenset({"mlx", "llama.cpp", "other"}),
    "execution": frozenset({"direct", "mtp", "ngram", "speculative", "delegated", "other"}),
    "cache": frozenset({"none", "memory_prefix", "disk_prefix", "other"}),
    "model_state": frozenset({"cold", "resident", "restored", "other"}),
    "fallback": frozenset(
        {"none", "unsupported", "correctness", "capacity", "runtime_error", "other"}
    ),
}
EVIDENCE_LEVELS = ("client_only", "declared", "corroborated", "benchmark_forced")
POSITIVE_EVIDENCE_LEVELS = frozenset({"corroborated", "benchmark_forced"})


@dataclass(frozen=True)
class NormalizedRoute:
    mapper_version: str
    backend: str
    execution: str
    cache: str
    model_state: str
    fallback: str

    def __post_init__(self) -> None:
        if self.mapper_version != ROUTE_MAPPER_VERSION:
            raise ContractError("normalized route has an unsupported mapper version")
        for dimension in ROUTE_DIMENSIONS:
            value = getattr(self, dimension)
            if value not in ROUTE_VALUES[dimension]:
                raise ContractError(f"normalized route {dimension} has an unknown value")

    @property
    def route_purity_known(self) -> bool:
        return all(
            getattr(self, dimension) != "other"
            for dimension in ("backend", "execution", "cache", "fallback")
        )


@dataclass(frozen=True)
class NativeRouteRecord:
    request_id: str
    record: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.request_id:
            raise ContractError("route request_id must be non-empty")
        if not isinstance(self.record, Mapping):
            raise ContractError("native route record must be an object")
        object.__setattr__(self, "record", deepcopy(dict(self.record)))


@dataclass(frozen=True)
class RouteCapture:
    native: NativeRouteRecord
    process_identity_verified: bool = False
    config_identity_verified: bool = False
    route_specific_corroboration: bool = False
    benchmark_forced: bool = False

    def __post_init__(self) -> None:
        for field in (
            "process_identity_verified",
            "config_identity_verified",
            "route_specific_corroboration",
            "benchmark_forced",
        ):
            if not isinstance(getattr(self, field), bool):
                raise ContractError(f"route capture {field} must be a boolean")


@dataclass(frozen=True)
class RouteEvidence:
    request_id: str
    native: Optional[NativeRouteRecord]
    normalized: NormalizedRoute
    observation_level: str

    def __post_init__(self) -> None:
        if self.observation_level not in EVIDENCE_LEVELS:
            raise ContractError("route evidence has an unknown observation level")
        if self.native is not None and self.native.request_id != self.request_id:
            raise ContractError("native route record is correlated to another request")
        if self.native is None and self.observation_level != "client_only":
            raise ContractError("absent native route evidence must remain client_only")

    def supports_positive_claim(self, claim: str) -> bool:
        if self.observation_level not in POSITIVE_EVIDENCE_LEVELS:
            return False
        if not self.normalized.route_purity_known:
            return False
        if claim == "prefix_reuse":
            return (
                self.normalized.cache in {"memory_prefix", "disk_prefix"}
                and self.normalized.fallback == "none"
            )
        if claim == "mtp":
            return (
                self.normalized.execution == "mtp"
                and self.normalized.fallback == "none"
            )
        if claim == "direct":
            return (
                self.normalized.execution == "direct"
                and self.normalized.fallback == "none"
            )
        if claim == "fallback":
            return self.normalized.fallback not in {"none", "other"}
        raise ContractError(f"unknown positive route claim: {claim}")


def normalize_native_route(
    native: Mapping[str, Any],
    value_maps: Optional[Mapping[str, Mapping[Any, str]]] = None,
) -> NormalizedRoute:
    """Derive mapper-v1 fields without modifying or guessing the native record."""
    if not isinstance(native, Mapping):
        raise ContractError("native route record must be an object")
    mappings = value_maps or {}
    normalized: Dict[str, str] = {}
    for dimension in ROUTE_DIMENSIONS:
        native_value = native.get(dimension)
        try:
            if dimension in mappings:
                value = mappings[dimension].get(native_value, "other")
            else:
                value = native_value if native_value in ROUTE_VALUES[dimension] else "other"
        except TypeError:
            # Structured native values are retained above but are not registered route atoms.
            value = "other"
        normalized[dimension] = (
            value
            if isinstance(value, str) and value in ROUTE_VALUES[dimension]
            else "other"
        )
    return NormalizedRoute(mapper_version=ROUTE_MAPPER_VERSION, **normalized)


def build_route_evidence(
    request_id: str,
    capture: Optional[RouteCapture] = None,
    *,
    value_maps: Optional[Mapping[str, Mapping[Any, str]]] = None,
) -> RouteEvidence:
    if capture is None:
        return RouteEvidence(
            request_id=request_id,
            native=None,
            normalized=normalize_native_route({}),
            observation_level="client_only",
        )
    if capture.native.request_id != request_id:
        raise ContractError("route capture request correlation mismatch")
    identities_verified = (
        capture.process_identity_verified and capture.config_identity_verified
    )
    if capture.benchmark_forced and identities_verified:
        level = "benchmark_forced"
    elif capture.route_specific_corroboration and identities_verified:
        level = "corroborated"
    else:
        level = "declared"
    return RouteEvidence(
        request_id=request_id,
        native=capture.native,
        normalized=normalize_native_route(capture.native.record, value_maps),
        observation_level=level,
    )


def correlate_route_records(
    request_ids: Sequence[str],
    captures: Sequence[RouteCapture],
    *,
    value_maps: Optional[Mapping[str, Mapping[Any, str]]] = None,
) -> Tuple[RouteEvidence, ...]:
    """Join captures to the frozen request plan; missing captures stay client-only."""
    if len(request_ids) != len(set(request_ids)):
        raise ContractError("planned route request IDs must be unique")
    by_request: Dict[str, RouteCapture] = {}
    planned = set(request_ids)
    for capture in captures:
        request_id = capture.native.request_id
        if request_id not in planned:
            raise ContractError(f"route record has unplanned request_id: {request_id}")
        if request_id in by_request:
            raise ContractError(f"duplicate route record for request_id: {request_id}")
        by_request[request_id] = capture
    return tuple(
        build_route_evidence(request_id, by_request.get(request_id), value_maps=value_maps)
        for request_id in request_ids
    )
