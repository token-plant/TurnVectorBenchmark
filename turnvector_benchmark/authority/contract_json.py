"""Pair-preserving strict JSON parsing and canonical re-encoding helpers."""

from __future__ import annotations

import codecs
import json
from dataclasses import dataclass
from typing import Any, Iterable, Tuple, Union

from turnvector_benchmark.canonical import canonical_jsonl_line
from turnvector_benchmark.core import ContractError

JsonPathToken = Union[str, int]


class InvalidCanonicalJson(ContractError):
    """A document failed the stage-1 lexical/JSON contract."""

    def __init__(self, what: str, detail: str) -> None:
        self.what = what
        self.detail = detail
        super().__init__(f"{what}: {detail}")


@dataclass(frozen=True)
class DuplicateKey:
    """One retained duplicate-key occurrence beyond the first."""

    path: Tuple[JsonPathToken, ...]
    key: str
    pair_index: int
    occurrence: int


@dataclass(frozen=True)
class JsonObject:
    """An immutable JSON object retaining declaration-order key/value pairs."""

    pairs: Tuple[Tuple[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {key: _to_plain(value) for key, value in self.pairs}


@dataclass(frozen=True)
class ParsedJson:
    """Pair-preserving parsed object plus dispatcher-owned structural facts."""

    root: JsonObject
    duplicate_keys: Tuple[DuplicateKey, ...]

    @property
    def value(self) -> dict[str, Any]:
        """Return an ordinary value view; safe after duplicate-key dispatch."""
        return self.root.as_dict()

    @property
    def pairs(self) -> Tuple[Tuple[str, Any], ...]:
        return self.root.pairs


class _RejectedNumber(ValueError):
    pass


def _reject_float(token: str) -> None:
    raise _RejectedNumber(f"float {token!r}")


def _reject_constant(token: str) -> None:
    raise _RejectedNumber(f"constant {token!r}")


def _freeze(value: Any) -> Any:
    if isinstance(value, JsonObject):
        return JsonObject(tuple((key, _freeze(item)) for key, item in value.pairs))
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _to_plain(value: Any) -> Any:
    if isinstance(value, JsonObject):
        return value.as_dict()
    if isinstance(value, tuple):
        return [_to_plain(item) for item in value]
    return value


def _collect_duplicates(value: Any) -> Tuple[DuplicateKey, ...]:
    facts = []
    # Pair events are processed in document order. A duplicate fact is
    # retained when its key is encountered, before descending into its value.
    stack = [("value", value, (), None)]
    while stack:
        kind, current, path, duplicate = stack.pop()
        if kind == "pair":
            if duplicate is not None:
                facts.append(duplicate)
            stack.append(("value", current, path, None))
        elif isinstance(current, JsonObject):
            seen: dict[str, int] = {}
            pairs = []
            for index, (key, child) in enumerate(current.pairs):
                occurrence = seen.get(key, 0) + 1
                seen[key] = occurrence
                fact = None
                if occurrence > 1:
                    fact = DuplicateKey(path, key, index, occurrence)
                pairs.append(("pair", child, path + (key,), fact))
            stack.extend(reversed(pairs))
        elif isinstance(current, tuple):
            stack.extend(
                ("value", child, path + (index,), None)
                for index, child in reversed(tuple(enumerate(current)))
            )
    return tuple(facts)


def _reject_surrogates(value: Any, what: str) -> None:
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, str):
            if any(0xD800 <= ord(char) <= 0xDFFF for char in current):
                raise InvalidCanonicalJson(what, "unpaired surrogate escape")
        elif isinstance(current, JsonObject):
            for key, child in current.pairs:
                stack.append(key)
                stack.append(child)
        elif isinstance(current, tuple):
            stack.extend(current)


def _validate_string_tokens(text: str, what: str) -> None:
    """Reject noncanonical JSON string escapes without constraining spacing."""
    index = 0
    while index < len(text):
        if text[index] != '"':
            index += 1
            continue
        try:
            decoded, end = json.decoder.scanstring(text, index + 1, True)
        except (ValueError, json.JSONDecodeError) as error:
            raise InvalidCanonicalJson(what, "invalid JSON string") from error
        token = text[index:end]
        canonical = json.dumps(decoded, ensure_ascii=True, separators=(",", ":"))
        if token != canonical:
            raise InvalidCanonicalJson(what, "noncanonical JSON string escape")
        index = end


def parse_json_object(raw: Union[bytes, memoryview], what: str) -> ParsedJson:
    """Parse one strict-UTF-8 JSON object while retaining all object pairs.

    Duplicate keys and declaration/array order are facts, not parse failures.
    Canonical layout equality is deliberately handled by the re-validation
    helpers so the dispatcher can assign it to stage 6.
    """
    if type(raw) not in (bytes, memoryview):
        raise InvalidCanonicalJson(what, "input is not a byte buffer")
    try:
        text = codecs.decode(raw, "utf-8", "strict")
    except (UnicodeDecodeError, TypeError, ValueError) as error:
        raise InvalidCanonicalJson(what, "invalid UTF-8") from error
    if text.startswith("\ufeff"):
        raise InvalidCanonicalJson(what, "UTF-8 BOM is forbidden")
    if "\r" in text:
        raise InvalidCanonicalJson(what, "CR is forbidden")
    if not text.endswith("\n"):
        raise InvalidCanonicalJson(what, "missing final LF")
    if text.endswith("\n\n"):
        raise InvalidCanonicalJson(what, "blank trailing line")

    body = text[:-1]
    _validate_string_tokens(body, what)
    decoder = json.JSONDecoder(
        object_pairs_hook=lambda pairs: JsonObject(tuple(pairs)),
        parse_float=_reject_float,
        parse_constant=_reject_constant,
        strict=True,
    )
    try:
        root, end = decoder.raw_decode(body)
    except _RejectedNumber as error:
        raise InvalidCanonicalJson(what, str(error)) from error
    except (json.JSONDecodeError, RecursionError, ValueError) as error:
        raise InvalidCanonicalJson(what, "invalid JSON syntax") from error
    if body[end:].strip():
        raise InvalidCanonicalJson(what, "extra content after object")
    if not isinstance(root, JsonObject):
        raise InvalidCanonicalJson(what, "top-level value is not an object")
    try:
        frozen = _freeze(root)
    except RecursionError as error:
        raise InvalidCanonicalJson(what, "JSON nesting is too deep") from error
    _reject_surrogates(frozen, what)
    return ParsedJson(frozen, _collect_duplicates(frozen))


def canonical_json_bytes(value: Any, *, indent: int | None = None) -> bytes:
    """Serialize one object with recursive lexical keys and one final LF."""
    if isinstance(value, ParsedJson):
        if value.duplicate_keys:
            raise InvalidCanonicalJson("canonical JSON object", "duplicate object key")
        value = value.value
    elif isinstance(value, JsonObject):
        parsed = ParsedJson(value, _collect_duplicates(value))
        if parsed.duplicate_keys:
            raise InvalidCanonicalJson("canonical JSON object", "duplicate object key")
        value = parsed.value
    if not isinstance(value, dict):
        raise InvalidCanonicalJson("canonical JSON object", "value is not an object")
    try:
        # Reuse the repository validator as the compact serializer's strict
        # inverse check, including non-string keys, floats and surrogates.
        compact = canonical_jsonl_line(value)
        if indent is None:
            return compact
        text = json.dumps(value, sort_keys=True, indent=indent, ensure_ascii=True)
        return text.encode("ascii") + b"\n"
    except (ContractError, TypeError, ValueError, RecursionError, UnicodeError) as error:
        raise InvalidCanonicalJson("canonical JSON object", "value is not serializable") from error


def is_canonical_json(raw: Union[bytes, memoryview], parsed: ParsedJson, *, indent: int | None = None) -> bool:
    """Return whether *raw* equals the selected canonical object encoding."""
    if parsed.duplicate_keys:
        return False
    try:
        encoded = canonical_json_bytes(parsed, indent=indent)
        return raw == encoded
    except InvalidCanonicalJson:
        return False


def parse_pretty_object(ref: Any, *, logical_name: str) -> dict[str, Any]:
    """Parse and re-validate one exact two-space authority contract object."""
    from .bound_bytes import BoundBytesRef

    if not isinstance(ref, BoundBytesRef):
        raise InvalidCanonicalJson(logical_name, "input is not BoundBytesRef")
    parsed = parse_json_object(ref.buffer, logical_name)
    if not is_canonical_json(ref.buffer, parsed, indent=2):
        raise InvalidCanonicalJson(logical_name, "noncanonical two-space encoding")
    return parsed.value


def parse_jsonl_records(raw: bytes, what: str) -> Tuple[ParsedJson, ...]:
    """Parse pair-preserving JSONL records without rejecting stage-3 facts."""
    if type(raw) is not bytes:
        raise InvalidCanonicalJson(what, "input is not bytes")
    if not raw.endswith(b"\n"):
        raise InvalidCanonicalJson(what, "missing final LF")
    lines = raw.splitlines(keepends=True)
    if not lines or any(line == b"\n" for line in lines):
        raise InvalidCanonicalJson(what, "blank JSONL record")
    return tuple(
        parse_json_object(line, f"{what} record {index}")
        for index, line in enumerate(lines)
    )


def is_canonical_jsonl(raw: bytes, records: Iterable[ParsedJson]) -> bool:
    """Re-validate compact canonical JSONL record bytes."""
    try:
        encoded = b"".join(canonical_json_bytes(record) for record in records)
    except InvalidCanonicalJson:
        return False
    return raw == encoded
