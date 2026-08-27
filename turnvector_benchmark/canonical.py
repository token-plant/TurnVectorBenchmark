"""Strict canonical contract encoding and no-follow bounded IO helpers.

Implements the "Canonical contract encoding" rules from
docs/D0-AUTHORITY-DESIGN.md (accepted design revision
3aa2b1911970c86e1cce6d7a3d55f26279b6e76b5fa17aa74aa704beaf01d28a):

- Strict UTF-8 without BOM; LF line endings; one final LF.
- Canonical JSONL records: one compact JSON object per line, recursively
  lexical object keys, ASCII escaping, LF after every line including the
  last.
- Parsers reject duplicate object keys, unknown fields, floats, noncanonical
  bytes, invalid UTF-8, BOM, blank lines, and non-object records.
- ``canonical_jsonl_line`` is the strict inverse of the parser: it requires
  an object with recursively string keys and rejects floats (including
  NaN/Infinity), unpaired surrogate escapes, and non-JSON values before
  encoding, so the serializer can never emit bytes the parser rejects.
- Parser and structural-validation recursion failures (deeply nested
  attacker-shaped JSON) are translated to bounded ContractError; validation
  walks are iterative, and the parser's record limit bounds the number of
  decoded records retained during streaming/iteration.
- Authority/source inputs are read no-follow and bounded; symlinks,
  non-regular files, oversize inputs, and lstat/fstat races fail closed.

The typed field validators (``require_*``) implement the strict field rules
shared by the authority contract family. PR 6 (CoverageCompiler) and PR 7
(CompileCustody) reuse this module; it does not modify existing modules.
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .core import IDENTIFIER_RE, ContractError

POSIX_COMPONENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def read_no_follow_regular(path, max_bytes: int, what: str) -> bytes:
    """Read a strict regular file without following or blocking on it.

    The final component is lstat'ed before open and opened with O_NOFOLLOW
    where the platform provides it, so a symlink or any non-regular type is
    rejected from the pre-open lstat and the open never follows a link or
    blocks on a pipe. The post-open fstat must agree with the pre-open lstat
    on device/inode identity, and a second fstat after the bounded read must
    still agree on device/inode/size/mtime, so a swap-in-place or truncation
    during the read is detected rather than silently hashed. Files larger
    than *max_bytes* are rejected before any read. Every raw OSError is
    translated into a bounded ContractError; a close failure never masks an
    already-asserted ContractError.
    """
    try:
        before = path.lstat()
    except OSError as error:
        raise ContractError(f"cannot stat {what} {path}: {error}") from error
    if stat.S_ISLNK(before.st_mode):
        raise ContractError(f"{what} {path} is a symlink, not a regular file")
    if not stat.S_ISREG(before.st_mode):
        kind = "directory" if stat.S_ISDIR(before.st_mode) else "non-regular file"
        raise ContractError(f"{what} {path} is a {kind}, not a regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except OSError as error:
        raise ContractError(f"cannot open {what} {path}: {error}") from error
    try:
        try:
            after = os.fstat(fd)
        except OSError as error:
            raise ContractError(f"cannot stat {what} {path}: {error}") from error
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise ContractError(f"{what} {path} changed while opening")
        if not stat.S_ISREG(after.st_mode):
            raise ContractError(f"{what} {path} is not a regular file")
        size = after.st_size
        if size > max_bytes:
            raise ContractError(f"{what} {path} exceeds the {max_bytes}-byte bound")
        chunks = []
        remaining = size
        while remaining > 0:
            try:
                chunk = os.read(fd, min(65536, remaining))
            except OSError as error:
                raise ContractError(f"cannot read {what} {path}: {error}") from error
            if not chunk:
                raise ContractError(f"{what} {path} was truncated while reading")
            chunks.append(chunk)
            remaining -= len(chunk)
        try:
            final = os.fstat(fd)
        except OSError as error:
            raise ContractError(f"cannot stat {what} {path}: {error}") from error
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            final.st_dev,
            final.st_ino,
            final.st_size,
            final.st_mtime_ns,
        ):
            raise ContractError(f"{what} {path} changed while reading")
        return b"".join(chunks)
    finally:
        pending = sys.exc_info()[0]
        try:
            os.close(fd)
        except OSError as error:
            if pending is None:
                raise ContractError(f"cannot close {what} {path}: {error}") from error


def _reject_duplicate_keys(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    seen = set()
    for key, _value in pairs:
        if key in seen:
            raise ContractError(f"duplicate object key {key!r}")
        seen.add(key)
    return dict(pairs)


def _reject_constant(token: str) -> None:
    raise ContractError(f"noncanonical JSON constant {token!r}")


def _reject_noncanonical_values(value: Any, where: str) -> None:
    """Iteratively reject floats and unpaired surrogate escapes anywhere in a
    parsed JSON value, including in object keys.

    Parsed JSON keys are always strings, so non-string keys cannot occur here
    (the serializer checks them separately), but a key can still carry an
    unpaired surrogate escape and is rejected just like a value. The walk is
    iterative so attacker-shaped deep nesting cannot escape as RecursionError.
    """
    stack: List[Tuple[Any, str]] = [(value, where)]
    while stack:
        current, location = stack.pop()
        if isinstance(current, float):
            raise ContractError(f"{location} contains a float, which is noncanonical")
        if isinstance(current, str):
            for char in current:
                if 0xD800 <= ord(char) <= 0xDFFF:
                    raise ContractError(f"{location} contains an unpaired surrogate escape")
            continue
        if isinstance(current, dict):
            for key, child in current.items():
                for char in key:
                    if 0xD800 <= ord(char) <= 0xDFFF:
                        raise ContractError(f"{location} contains an unpaired surrogate escape")
                stack.append((child, f"{location}.{key}"))
        elif isinstance(current, list):
            for index, child in enumerate(current):
                stack.append((child, f"{location}[{index}]"))


def _validate_serializable_object(value: Any, where: str) -> None:
    """Iteratively validate *value* for canonical serialization.

    Rejects floats (including NaN/Infinity), non-string object keys, unpaired
    surrogate escapes in keys or values, and values json.dumps would coerce or
    reject (tuples, sets, and other non-JSON types). Iterative so deeply
    nested values cannot escape as RecursionError.
    """
    stack: List[Tuple[Any, str]] = [(value, where)]
    while stack:
        current, location = stack.pop()
        if isinstance(current, float):
            raise ContractError(f"{location} contains a float, which is noncanonical")
        if isinstance(current, str):
            for char in current:
                if 0xD800 <= ord(char) <= 0xDFFF:
                    raise ContractError(f"{location} contains an unpaired surrogate escape")
            continue
        if isinstance(current, dict):
            for key, child in current.items():
                if not isinstance(key, str):
                    raise ContractError(f"{location} contains a non-string object key")
                for char in key:
                    if 0xD800 <= ord(char) <= 0xDFFF:
                        raise ContractError(f"{location} contains an unpaired surrogate escape")
                stack.append((child, f"{location}.{key}"))
        elif isinstance(current, list):
            for index, child in enumerate(current):
                stack.append((child, f"{location}[{index}]"))
        elif isinstance(current, (bool, int)) or current is None:
            continue
        else:
            raise ContractError(
                f"{location} contains a value that cannot be canonically encoded"
            )


def _parse_canonical_json_line(line: bytes, what: str, index: int) -> Dict[str, Any]:
    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ContractError(f"{what} line {index} is not valid UTF-8") from error
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except ContractError:
        raise
    except RecursionError as error:
        raise ContractError(f"{what} line {index} is too deeply nested") from error
    except json.JSONDecodeError as error:
        raise ContractError(f"{what} line {index} is not valid JSON: {error}") from error
    if not isinstance(value, dict):
        raise ContractError(f"{what} line {index} must be a JSON object")
    _reject_noncanonical_values(value, f"{what} line {index}")
    try:
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except RecursionError as error:
        raise ContractError(f"{what} line {index} is too deeply nested") from error
    try:
        canonical_bytes = canonical.encode("ascii")
    except UnicodeEncodeError as error:
        raise ContractError(f"{what} line {index} is not canonically encoded") from error
    if canonical_bytes != line:
        raise ContractError(
            f"{what} line {index} is not canonical compact lexical-key JSON"
        )
    return value


def parse_canonical_jsonl_records(
    raw: bytes, what: str, record_limit: Optional[int] = None
) -> List[Dict[str, Any]]:
    """Parse strict canonical JSONL bytes into a list of record objects.

    Enforces: UTF-8 without BOM, LF-only line endings, exactly one compact
    JSON object per line with recursively lexical keys and ASCII escaping, a
    final LF, no blank lines, and per-line duplicate-key/float/constant/
    noncanonical-byte rejection.

    *record_limit*, when given, bounds the number of decoded records: the
    one-past record fails during streaming/iteration instead of after an
    unbounded list of decoded dicts has been retained.
    """
    if record_limit is not None and (
        isinstance(record_limit, bool) or not isinstance(record_limit, int) or record_limit < 0
    ):
        raise ContractError(f"{what} record limit must be a nonnegative integer")
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ContractError(f"{what} must not carry a UTF-8 BOM")
    if b"\r" in raw:
        raise ContractError(f"{what} must use LF line endings only")
    if not raw.endswith(b"\n"):
        raise ContractError(f"{what} must end with exactly one final LF")
    records: List[Dict[str, Any]] = []
    start = 0
    index = 0
    while start < len(raw):
        end = raw.find(b"\n", start)
        if end == -1:
            raise ContractError(f"{what} must end with exactly one final LF")
        line = raw[start:end]
        start = end + 1
        if not line:
            raise ContractError(f"{what} must not contain blank lines")
        if record_limit is not None and index >= record_limit:
            raise ContractError(f"{what} exceeds the {record_limit}-record bound")
        records.append(_parse_canonical_json_line(line, what, index))
        index += 1
    return records


def canonical_jsonl_line(value: Dict[str, Any]) -> bytes:
    """Encode one compact lexical-key JSON object plus one LF (canonical).

    The serializer is the strict inverse of
    :func:`parse_canonical_jsonl_records`: *value* must be an object whose
    keys are strings at every nesting level, with no floats (including
    NaN/Infinity), no unpaired surrogate escapes, and no values json.dumps
    would coerce or reject. Recursion, Unicode, and JSON encoding failures
    are translated to bounded :class:`~turnvector_benchmark.core.ContractError`.
    """
    require_object(value, "canonical JSONL record")
    _validate_serializable_object(value, "canonical JSONL record")
    try:
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except RecursionError as error:
        raise ContractError("canonical JSONL record is too deeply nested to encode") from error
    except (TypeError, ValueError) as error:
        raise ContractError(f"cannot encode canonical JSONL record: {error}") from error
    try:
        return canonical.encode("ascii") + b"\n"
    except UnicodeEncodeError as error:
        raise ContractError(f"cannot encode canonical JSONL record: {error}") from error


def require_object(value: Any, where: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{where} must be an object")
    return value


def require_strict_keys(value: Mapping[str, Any], required: Tuple[str, ...], where: str) -> None:
    required_set = set(required)
    missing = sorted(required_set - set(value))
    unknown = sorted(set(value) - required_set)
    if missing:
        raise ContractError(f"{where} is missing required fields: {', '.join(missing)}")
    if unknown:
        raise ContractError(f"{where} has unknown fields: {', '.join(unknown)}")


def require_string(value: Any, where: str, max_bytes: int | None = None) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError(f"{where} must be a non-empty string")
    if max_bytes is not None and len(value.encode("utf-8")) > max_bytes:
        raise ContractError(f"{where} must be at most {max_bytes} UTF-8 bytes")
    return value


def require_identifier(value: Any, where: str) -> str:
    text = require_string(value, where)
    if not IDENTIFIER_RE.fullmatch(text):
        raise ContractError(f"{where} must match {IDENTIFIER_RE.pattern}")
    return text


def require_sha256(value: Any, where: str) -> str:
    text = require_string(value, where)
    if not SHA256_RE.fullmatch(text):
        raise ContractError(f"{where} must be a lowercase 64-character SHA-256 digest")
    return text


def require_boolean(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise ContractError(f"{where} must be a boolean")
    return value


def require_u64(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"{where} must be an integer")
    if value < 0 or value > (1 << 64) - 1:
        raise ContractError(f"{where} must be a nonnegative u64")
    return value


def require_posix_path(value: Any, where: str) -> str:
    text = require_string(value, where)
    if "\\" in text or text.startswith("/") or "//" in text or text.endswith("/"):
        raise ContractError(f"{where} must be a normalized repository-relative POSIX path")
    for component in text.split("/"):
        if component in ("", ".", "..") or not POSIX_COMPONENT_RE.fullmatch(component):
            raise ContractError(f"{where} must be a normalized repository-relative POSIX path")
    return text
