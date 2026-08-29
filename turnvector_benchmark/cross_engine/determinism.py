"""Fail-closed output, route, and state determinism assessment.

The three determinism dimensions deliberately have separate observation
surfaces.  Output is mandatory OpenAI-visible evidence; route and state are
optional, explicitly levelled native evidence.  Structural trial membership
is checked before any comparison so a subset or reordered stream cannot be
mistaken for deterministic replay.
"""

from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

from turnvector_benchmark.core import ContractError

from .contracts import bounded_string, canonical_json_bytes, identifier, integer, sha256_digest
from .routes import EVIDENCE_LEVELS


_OBSERVABLE_NATIVE_LEVELS = frozenset(set(EVIDENCE_LEVELS) - {"client_only"})


@dataclass(frozen=True)
class DeterminismIdentity:
    """One frozen repetition slot and the execution identities it binds."""

    request_id: str
    corpus_identity: str
    repetition: int
    request_identity: str
    config_identity: str
    model_identity: str

    def __post_init__(self) -> None:
        identifier(self.request_id, "determinism request_id")
        identifier(self.corpus_identity, "determinism corpus identity")
        integer(self.repetition, "determinism repetition")
        for value, where in (
            (self.request_identity, "determinism request identity"),
            (self.config_identity, "determinism config identity"),
            (self.model_identity, "determinism model identity"),
        ):
            bounded_string(value, where, maximum_bytes=256)

    @property
    def execution_identity(self) -> Tuple[str, str, str]:
        return self.request_identity, self.config_identity, self.model_identity


@dataclass(frozen=True)
class OutputObservation:
    """Canonical OpenAI-visible output identity for one repetition."""

    visible_output: bytes
    finish_reason: Optional[str]
    authoritative_usage: Optional[Mapping[str, Any]]
    terminal_sequence: Tuple[str, ...]
    derived_output_token_hash: Optional[str] = None
    native_output_token_hash: Optional[str] = None
    native_output_token_ids_validated: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.visible_output, bytes):
            raise ContractError("canonical visible output must be bytes")
        if self.finish_reason is not None:
            bounded_string(self.finish_reason, "finish reason", maximum_bytes=128)
        if self.authoritative_usage is not None:
            if not isinstance(self.authoritative_usage, Mapping):
                raise ContractError("authoritative usage must be an object or null")
            usage = deepcopy(dict(self.authoritative_usage))
            if any(not isinstance(key, str) for key in usage):
                raise ContractError("authoritative usage keys must be strings")
            # This both validates the retained value and fixes its equality encoding.
            canonical_json_bytes(usage)
            object.__setattr__(self, "authoritative_usage", usage)
        if not isinstance(self.terminal_sequence, tuple) or not self.terminal_sequence:
            raise ContractError("terminal/error sequence must be a non-empty tuple")
        for event in self.terminal_sequence:
            bounded_string(event, "terminal/error event", maximum_bytes=256)
        if self.derived_output_token_hash is not None:
            sha256_digest(
                self.derived_output_token_hash, "derived_output_token_hash"
            )
        if not isinstance(self.native_output_token_ids_validated, bool):
            raise ContractError("native output token validation flag must be a boolean")
        if self.native_output_token_hash is not None:
            sha256_digest(self.native_output_token_hash, "native_output_token_hash")
        if (self.native_output_token_hash is not None) != self.native_output_token_ids_validated:
            raise ContractError(
                "native_output_token_hash requires validated exposed native token IDs"
            )

    @property
    def visible_sha256(self) -> str:
        return hashlib.sha256(self.visible_output).hexdigest()

    @property
    def canonical_signature(self) -> Tuple[Any, ...]:
        usage = (
            None
            if self.authoritative_usage is None
            else canonical_json_bytes(self.authoritative_usage)
        )
        return (
            self.visible_output,
            self.visible_sha256,
            self.finish_reason,
            usage,
            self.terminal_sequence,
        )


@dataclass(frozen=True)
class RouteObservation:
    """A request-correlated route sequence at one declared native level."""

    observation_level: str
    route_sequence: Tuple[str, ...]

    def __post_init__(self) -> None:
        if self.observation_level not in _OBSERVABLE_NATIVE_LEVELS:
            raise ContractError(
                "route determinism requires a declared, corroborated, or "
                "benchmark-forced observation"
            )
        if not isinstance(self.route_sequence, tuple) or not self.route_sequence:
            raise ContractError("route sequence must be a non-empty tuple")
        for value in self.route_sequence:
            bounded_string(value, "route sequence value", maximum_bytes=512)

    @property
    def signature(self) -> Tuple[str, ...]:
        return self.route_sequence


@dataclass(frozen=True)
class StateObservation:
    """Cache and/or engine-native state identity at one declared level."""

    observation_level: str
    cache_transition: Optional[Tuple[str, ...]] = None
    engine_native_trace_digest: Optional[str] = None

    def __post_init__(self) -> None:
        if self.observation_level not in _OBSERVABLE_NATIVE_LEVELS:
            raise ContractError(
                "state determinism requires a declared, corroborated, or "
                "benchmark-forced observation"
            )
        if self.cache_transition is None and self.engine_native_trace_digest is None:
            raise ContractError("state observation must expose cache transition or native trace")
        if self.cache_transition is not None:
            if not isinstance(self.cache_transition, tuple) or not self.cache_transition:
                raise ContractError("cache transition must be a non-empty tuple")
            for value in self.cache_transition:
                bounded_string(value, "cache transition value", maximum_bytes=512)
        if self.engine_native_trace_digest is not None:
            sha256_digest(
                self.engine_native_trace_digest, "engine_native_trace_digest"
            )

    @property
    def signature(self) -> Tuple[Any, ...]:
        return self.cache_transition, self.engine_native_trace_digest


@dataclass(frozen=True)
class DeterminismObservation:
    identity: DeterminismIdentity
    output: OutputObservation
    route: Optional[RouteObservation] = None
    state: Optional[StateObservation] = None

    def __post_init__(self) -> None:
        if not isinstance(self.identity, DeterminismIdentity):
            raise ContractError("determinism observation identity is invalid")
        if not isinstance(self.output, OutputObservation):
            raise ContractError("determinism output observation is required")
        if self.route is not None and not isinstance(self.route, RouteObservation):
            raise ContractError("determinism route observation is invalid")
        if self.state is not None and not isinstance(self.state, StateObservation):
            raise ContractError("determinism state observation is invalid")


@dataclass(frozen=True)
class DimensionDeterminism:
    deterministic: Optional[bool]
    observation_levels: Tuple[str, ...]
    violating_corpus_identities: Tuple[str, ...]
    not_observable_corpus_identities: Tuple[str, ...]

    @property
    def not_observable(self) -> bool:
        return bool(self.not_observable_corpus_identities)

    def as_dict(self) -> Mapping[str, Any]:
        return {
            "deterministic": self.deterministic,
            "observation_levels": list(self.observation_levels),
            "violating_corpus_identities": list(self.violating_corpus_identities),
            "not_observable_corpus_identities": list(
                self.not_observable_corpus_identities
            ),
            "not_observable": self.not_observable,
        }


@dataclass(frozen=True)
class DeterminismAssessment:
    output: DimensionDeterminism
    route: DimensionDeterminism
    state: DimensionDeterminism

    @property
    def semantic_claim(self) -> str:
        return "determinism"

    @property
    def output_deterministic(self) -> bool:
        # Output is mandatory, so this dimension can never be unavailable.
        return self.output.deterministic is True

    @property
    def route_deterministic(self) -> Optional[bool]:
        return self.route.deterministic

    @property
    def state_deterministic(self) -> Optional[bool]:
        return self.state.deterministic

    @property
    def not_observable(self) -> Tuple[str, ...]:
        dimensions = (("route", self.route), ("state", self.state))
        return tuple(name for name, result in dimensions if result.not_observable)

    def as_dict(self) -> Mapping[str, Any]:
        return {
            "semantic_claim": self.semantic_claim,
            "output_deterministic": self.output_deterministic,
            "route_deterministic": self.route_deterministic,
            "state_deterministic": self.state_deterministic,
            "not_observable": list(self.not_observable),
            "dimensions": {
                "output": self.output.as_dict(),
                "route": self.route.as_dict(),
                "state": self.state.as_dict(),
            },
        }


def _group_observations(
    observations: Tuple[DeterminismObservation, ...],
) -> Tuple[Tuple[str, Tuple[DeterminismObservation, ...]], ...]:
    grouped: Dict[str, list[DeterminismObservation]] = {}
    for observation in observations:
        grouped.setdefault(observation.identity.corpus_identity, []).append(observation)
    return tuple((key, tuple(values)) for key, values in grouped.items())


def _mandatory_dimension(
    groups: Tuple[Tuple[str, Tuple[DeterminismObservation, ...]], ...],
    signature: Callable[[DeterminismObservation], Any],
    *,
    observation_level: str,
) -> DimensionDeterminism:
    violations = tuple(
        corpus
        for corpus, values in groups
        if len({signature(value) for value in values}) != 1
    )
    return DimensionDeterminism(
        deterministic=not violations,
        observation_levels=(observation_level,),
        violating_corpus_identities=violations,
        not_observable_corpus_identities=(),
    )


def _optional_dimension(
    groups: Tuple[Tuple[str, Tuple[DeterminismObservation, ...]], ...],
    observation: Callable[[DeterminismObservation], Any],
    signature: Callable[[Any], Any],
    level: Callable[[Any], str],
    surface: Callable[[Any], Any] = lambda _value: None,
) -> DimensionDeterminism:
    violations = []
    unavailable = []
    levels = set()
    for corpus, values in groups:
        observed = tuple(observation(value) for value in values)
        if any(value is None for value in observed):
            unavailable.append(corpus)
            continue
        observed_levels = {level(value) for value in observed}
        observed_surfaces = {surface(value) for value in observed}
        if len(observed_levels) != 1 or len(observed_surfaces) != 1:
            unavailable.append(corpus)
            continue
        levels.update(observed_levels)
        if len({signature(value) for value in observed}) != 1:
            violations.append(corpus)
    deterministic: Optional[bool]
    if violations:
        deterministic = False
    elif unavailable:
        deterministic = None
    else:
        deterministic = True
    return DimensionDeterminism(
        deterministic=deterministic,
        observation_levels=tuple(sorted(levels)),
        violating_corpus_identities=tuple(violations),
        not_observable_corpus_identities=tuple(unavailable),
    )


def assess_determinism(
    observations: Sequence[DeterminismObservation],
    *,
    planned_identities: Sequence[DeterminismIdentity],
) -> DeterminismAssessment:
    """Assess frozen repetitions without inferring, sorting, or imputing rows.

    ``planned_identities`` is the canonical sequence.  Missing, duplicate,
    extra, or reordered observations all fail the structural contract before
    any dimension is reported.
    """

    planned = tuple(planned_identities)
    values = tuple(observations)
    if not planned:
        raise ContractError("determinism identity plan must not be empty")
    if any(not isinstance(identity, DeterminismIdentity) for identity in planned):
        raise ContractError("determinism identity plan contains an invalid identity")
    if any(not isinstance(value, DeterminismObservation) for value in values):
        raise ContractError("determinism observations contain an invalid row")
    planned_slots = tuple(
        (identity.request_id, identity.corpus_identity, identity.repetition)
        for identity in planned
    )
    if len(planned_slots) != len(set(planned_slots)):
        raise ContractError("determinism identity plan contains duplicate slots")
    request_ids = tuple(identity.request_id for identity in planned)
    if len(request_ids) != len(set(request_ids)):
        raise ContractError("determinism identity plan contains duplicate request IDs")
    observed_identities = tuple(value.identity for value in values)
    if observed_identities != planned:
        raise ContractError(
            "determinism observations are missing, duplicated, extra, reordered, "
            "or identity-drifted"
        )

    grouped_identities: Dict[str, list[DeterminismIdentity]] = {}
    for identity in planned:
        grouped_identities.setdefault(identity.corpus_identity, []).append(identity)
    for corpus, identities in grouped_identities.items():
        if len(identities) < 2:
            raise ContractError(
                "determinism corpus %s must contain at least two frozen repetitions" % corpus
            )
        repetitions = [identity.repetition for identity in identities]
        if len(repetitions) != len(set(repetitions)):
            raise ContractError(
                "determinism corpus %s contains duplicate repetition identities" % corpus
            )
        if len({identity.execution_identity for identity in identities}) != 1:
            raise ContractError(
                "determinism repetitions must retain request/config/model identity"
            )

    groups = _group_observations(values)
    output = _mandatory_dimension(
        groups,
        lambda value: value.output.canonical_signature,
        observation_level="client_only",
    )
    route = _optional_dimension(
        groups,
        lambda value: value.route,
        lambda value: value.signature,
        lambda value: value.observation_level,
    )
    state = _optional_dimension(
        groups,
        lambda value: value.state,
        lambda value: value.signature,
        lambda value: value.observation_level,
        lambda value: (
            value.cache_transition is not None,
            value.engine_native_trace_digest is not None,
        ),
    )
    return DeterminismAssessment(output=output, route=route, state=state)
