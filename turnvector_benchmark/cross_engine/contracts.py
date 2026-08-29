from __future__ import annotations

import json
import math
import re
from pathlib import PurePosixPath
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..core import ContractError


LIFECYCLE_PROTOCOL_VERSION = "turnvector.benchmark.cross-engine-lifecycle.v1"
OPENAI_SERVING_PROTOCOL_VERSION = "turnvector.benchmark.openai-serving.v1"
ARTIFACT_MANIFEST_SCHEMA_VERSION = (
    "turnvector.benchmark.cross-engine-artifact-manifest.v1"
)
MAX_JSONL_LINE_BYTES = 64 * 1024
MAX_IDENTIFIER_BYTES = 128
MAX_TEXT_BYTES = 4096
MAX_ARRAY_ITEMS = 1024

IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
ENVIRONMENT_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
POSIX_COMPONENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")


CrossEngineContractError = ContractError


def strict_object(
    value: Any,
    required: Iterable[str],
    optional: Iterable[str] = (),
    where: str = "value",
) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{where} must be an object")
    required_set = set(required)
    allowed = required_set | set(optional)
    missing = sorted(required_set - set(value))
    unknown = sorted(set(value) - allowed)
    if missing:
        raise ContractError(f"{where} is missing required fields: {', '.join(missing)}")
    if unknown:
        raise ContractError(f"{where} has unknown fields: {', '.join(unknown)}")
    return value


def bounded_string(
    value: Any,
    where: str,
    *,
    maximum_bytes: int = MAX_TEXT_BYTES,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str) or (not value and not allow_empty):
        qualifier = "a string" if allow_empty else "a non-empty string"
        raise ContractError(f"{where} must be {qualifier}")
    if "\x00" in value:
        raise ContractError(f"{where} must not contain NUL")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ContractError(f"{where} must be valid UTF-8 text") from error
    if len(encoded) > maximum_bytes:
        raise ContractError(f"{where} exceeds the {maximum_bytes}-byte bound")
    return value


def identifier(value: Any, where: str) -> str:
    text = bounded_string(value, where, maximum_bytes=MAX_IDENTIFIER_BYTES)
    if not IDENTIFIER_RE.fullmatch(text):
        raise ContractError(f"{where} must match {IDENTIFIER_RE.pattern}")
    return text


def environment_name(value: Any, where: str) -> str:
    text = bounded_string(value, where, maximum_bytes=MAX_IDENTIFIER_BYTES)
    if not ENVIRONMENT_NAME_RE.fullmatch(text):
        raise ContractError(f"{where} must be an environment variable name")
    return text


def integer(
    value: Any,
    where: str,
    *,
    minimum: int = 0,
    maximum: int = 2**63 - 1,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"{where} must be an integer")
    if value < minimum or value > maximum:
        raise ContractError(f"{where} must be between {minimum} and {maximum}")
    return value


def finite_number(value: Any, where: str, *, minimum: Optional[float] = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{where} must be a number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ContractError(f"{where} must be finite")
    if minimum is not None and parsed < minimum:
        raise ContractError(f"{where} must be greater than or equal to {minimum}")
    return parsed


def boolean(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise ContractError(f"{where} must be a boolean")
    return value


def sha256_digest(value: Any, where: str) -> str:
    text = bounded_string(value, where, maximum_bytes=64)
    if not SHA256_RE.fullmatch(text):
        raise ContractError(f"{where} must be a lowercase 64-character SHA-256 digest")
    return text


def bounded_array(
    value: Any,
    where: str,
    *,
    maximum_items: int = MAX_ARRAY_ITEMS,
    allow_empty: bool = True,
) -> List[Any]:
    if not isinstance(value, list):
        raise ContractError(f"{where} must be an array")
    if not allow_empty and not value:
        raise ContractError(f"{where} must not be empty")
    if len(value) > maximum_items:
        raise ContractError(f"{where} exceeds the {maximum_items}-item bound")
    return value


def unique_strings(
    value: Any,
    where: str,
    *,
    maximum_items: int = MAX_ARRAY_ITEMS,
    parse=bounded_string,
    allow_empty: bool = True,
) -> Tuple[str, ...]:
    raw = bounded_array(
        value, where, maximum_items=maximum_items, allow_empty=allow_empty
    )
    parsed = tuple(parse(item, f"{where}[{index}]") for index, item in enumerate(raw))
    if len(parsed) != len(set(parsed)):
        raise ContractError(f"{where} must not contain duplicates")
    return parsed


def relative_posix_path(value: Any, where: str) -> str:
    text = bounded_string(value, where, maximum_bytes=1024)
    if (
        text.startswith("/")
        or "\\" in text
        or "//" in text
        or text.endswith("/")
    ):
        raise ContractError(f"{where} must be a normalized relative POSIX path")
    path = PurePosixPath(text)
    parts = text.split("/")
    if (
        path.is_absolute()
        or not parts
        or any(
            part in ("", ".", "..") or not POSIX_COMPONENT_RE.fullmatch(part)
            for part in parts
        )
        or path.as_posix() != text
    ):
        raise ContractError(f"{where} must be a normalized relative POSIX path")
    return text


def _reject_duplicate_pairs(where: str):
    def hook(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ContractError(f"{where} has duplicate object key {key!r}")
            result[key] = value
        return result

    return hook


def strict_json_loads(raw: bytes, where: str = "JSON") -> Any:
    if not isinstance(raw, bytes):
        raise ContractError(f"{where} must be bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ContractError(f"{where} is not valid UTF-8") from error

    def reject_constant(value: str) -> None:
        raise ContractError(f"{where} contains non-finite number {value}")

    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs(where),
            parse_constant=reject_constant,
        )
    except ContractError:
        raise
    except (json.JSONDecodeError, RecursionError) as error:
        raise ContractError(f"{where} is not valid JSON: {error}") from error


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as error:
        raise ContractError(f"value cannot be encoded as canonical JSON: {error}") from error


def encode_jsonl_line(value: Mapping[str, Any], max_bytes: int = MAX_JSONL_LINE_BYTES) -> bytes:
    if not isinstance(value, dict):
        raise ContractError("JSONL record must be an object")
    integer(max_bytes, "max_bytes", minimum=1)
    raw = canonical_json_bytes(value) + b"\n"
    if len(raw) > max_bytes:
        raise ContractError(f"JSONL line exceeds the {max_bytes}-byte bound")
    return raw


def decode_jsonl_line(
    raw: bytes,
    *,
    max_bytes: int = MAX_JSONL_LINE_BYTES,
    where: str = "JSONL line",
) -> Dict[str, Any]:
    integer(max_bytes, "max_bytes", minimum=1)
    if not isinstance(raw, bytes):
        raise ContractError(f"{where} must be bytes")
    if len(raw) > max_bytes:
        raise ContractError(f"{where} exceeds the {max_bytes}-byte bound")
    if not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        raise ContractError(f"{where} must end with exactly one LF")
    body = raw[:-1]
    if not body or b"\n" in body or b"\r" in body:
        raise ContractError(f"{where} must contain one compact LF-terminated record")
    value = strict_json_loads(body, where)
    if not isinstance(value, dict):
        raise ContractError(f"{where} JSON value must be an object")
    return value
