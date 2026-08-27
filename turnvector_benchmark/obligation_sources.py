"""No-follow bounded source/range/hash verification for obligation citations.

Implements the PR 5 "range/hash validation" contract from
docs/D0-AUTHORITY-DESIGN.md (accepted design revision
3aa2b1911970c86e1cce6d7a3d55f26279b6e76b5fa17aa74aa704beaf01d28a):

- Byte-range convention: ``section_start``/``section_end`` are 0-based byte
  offsets into the source file, and the section is the half-open range
  ``[section_start, section_end)``. ``section_end`` may equal the file length
  (end-of-file) but must never exceed it. Zero-length and reversed ranges are
  rejected.
- ``source_file_sha256`` is verified over the entire source file;
  ``section_sha256`` is verified over the exact nonempty range bytes.
- The source root must be an absolute canonical path. It is opened no-follow
  component by component, rejecting relative/noncanonical roots, symlinked
  ancestors, and a symlinked final root. One root descriptor is held for the
  whole pass: every cited path is opened relative to that descriptor with
  ``O_NOFOLLOW`` per component, and the root pathname/identity (canonical
  path, device, inode, uid, gid, mode) is revalidated at the end so a
  rename/replacement mid-pass is rejected. Descent keeps O(1) descriptors
  open (each intermediate is closed once its child opens), so deep
  many-component paths cannot exhaust the descriptor table.
- Resource caps: each file <= authority_file_bytes_max, distinct file count
  <= authority_file_count_max, total source bytes (each file read once) <=
  authority_total_bytes_max, section count <= authority_section_count_max,
  and section bytes with overlap multiplicity <=
  authority_section_bytes_total_max. Per-path byte and total-path-byte caps
  follow the execution-closure path limits; every accumulation is checked
  u64.
- Every obligation record (required and out-of-scope) is verified: the
  catalog-content gate must review all complete authority sources and every
  range.

No final TurnVector obligation bodies are read or inferred; only the exact
cited source bytes are hashed.
"""

from __future__ import annotations

import hashlib
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .compile_limits import CompileLimits, checked_add
from .core import ContractError
from .obligation_catalog import ObligationCatalog

_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)

# device, inode, uid, gid, mode -- the root identity bound across the pass.
_RootIdentity = Tuple[int, int, int, int, int]
_Identity = Tuple[int, int, int, int]


@dataclass(frozen=True)
class SourceVerification:
    """Bounded summary of a successful source verification pass."""

    path_count: int
    source_bytes_total: int
    section_count: int
    section_bytes_total: int
    # Canonical source-root identity bound across the whole pass.
    root_path: str
    root_device: int
    root_inode: int
    root_uid: int
    root_gid: int
    root_mode: int


def _identity(entry) -> _Identity:
    return (entry.st_dev, entry.st_ino, entry.st_size, entry.st_mtime_ns)


def _root_identity(entry) -> _RootIdentity:
    return (entry.st_dev, entry.st_ino, entry.st_uid, entry.st_gid, entry.st_mode)


def _close_fd(fd: int, what: str, pending) -> None:
    try:
        os.close(fd)
    except OSError as error:
        if pending is None:
            raise ContractError(f"cannot close {what}: {error}") from error


def _descend_directories(
    base_fd: int,
    components: Tuple[str, ...],
    path_text: str,
    what: str,
    close_base: bool,
) -> int:
    """Open *components* as directories relative to *base_fd* no-follow.

    Each component is lstat'ed and opened with ``O_NOFOLLOW`` relative to its
    parent descriptor, and the post-open fstat must agree on device/inode, so
    a symlink swap or path-component race fails closed. Each previous
    intermediate descriptor is closed as soon as its child is open, so only
    O(1) descriptors are held while descending; the base descriptor is never
    closed unless *close_base* is true (it is the caller's held root).
    Every OSError is translated to a bounded ContractError and a close
    failure never masks an already-asserted ContractError.
    """
    open_fds: List[int] = [base_fd]
    try:
        for index, component in enumerate(components):
            parent = open_fds[-1]
            try:
                pre = os.stat(component, dir_fd=parent, follow_symlinks=False)
            except OSError as error:
                raise ContractError(f"{what}: cannot stat {path_text}: {error}") from error
            if stat.S_ISLNK(pre.st_mode):
                raise ContractError(
                    f"{what}: {path_text}: component {component!r} is a symlink"
                )
            if not stat.S_ISDIR(pre.st_mode):
                raise ContractError(
                    f"{what}: {path_text}: component {component!r} is not a directory"
                )
            try:
                child = os.open(
                    component,
                    os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC,
                    dir_fd=parent,
                )
            except OSError as error:
                raise ContractError(f"{what}: cannot open {path_text}: {error}") from error
            open_fds.append(child)
            try:
                post = os.fstat(child)
            except OSError as error:
                raise ContractError(f"{what}: cannot stat {path_text}: {error}") from error
            if (pre.st_dev, pre.st_ino) != (post.st_dev, post.st_ino):
                raise ContractError(
                    f"{what}: {path_text}: component {component!r} changed while opening"
                )
            # Close the previous intermediate once its child is open. The held
            # base (close_base=False) is never closed.
            if close_base or len(open_fds) >= 3:
                doomed = open_fds.pop(-2)
                pending = sys.exc_info()[0]
                try:
                    os.close(doomed)
                except OSError as error:
                    if pending is None:
                        raise ContractError(
                            f"{what}: cannot close {path_text}: {error}"
                        ) from error
        return open_fds[-1]
    except Exception:
        for fd in open_fds:
            if not (not close_base and fd == base_fd):
                try:
                    os.close(fd)
                except OSError:
                    pass
        raise


def _open_canonical_root(source_root: Path, what: str) -> Tuple[int, _RootIdentity]:
    """Open the absolute canonical source root no-follow, component by component.

    Rejects relative paths, noncanonical paths ('.', '..', '//', trailing
    slash, or the bare filesystem root), symlinked ancestors, a symlinked
    final root, and non-directory roots. Returns the root descriptor (held by
    the caller for the entire verification pass) and its bound identity
    (device, inode, uid, gid, mode).
    """
    text = os.fspath(source_root)
    if not isinstance(text, str):
        raise ContractError(f"{what}: source root must be a path string")
    if not os.path.isabs(text):
        raise ContractError(f"{what}: source root {text} must be an absolute path")
    parts = text.split("/")
    if parts[0] != "":
        raise ContractError(f"{what}: source root {text} is not a canonical absolute path")
    components: List[str] = []
    for part in parts[1:]:
        if part in ("", ".", ".."):
            raise ContractError(f"{what}: source root {text} is not a canonical absolute path")
        components.append(part)
    if not components:
        raise ContractError(f"{what}: source root {text} must not be the filesystem root")
    try:
        base = os.open("/", os.O_RDONLY | _O_DIRECTORY | _O_CLOEXEC)
    except OSError as error:
        raise ContractError(f"{what}: cannot open source root {text}: {error}") from error
    try:
        root_fd = _descend_directories(
            base, tuple(components), f"source root {text}", what, close_base=True
        )
    except Exception:
        # _descend_directories already closed every transient descriptor,
        # including the base when close_base is true.
        raise
    try:
        entry = os.fstat(root_fd)
    except OSError as error:
        _close_fd(root_fd, f"source root {text}", None)
        raise ContractError(f"{what}: cannot stat source root {text}: {error}") from error
    return root_fd, _root_identity(entry)


def _open_source_no_follow(
    root_fd: int, components: Tuple[str, ...], what: str
) -> int:
    """Open the final regular file relative to the held root descriptor.

    Every intermediate component must be a real directory and the final
    component a regular file; each is lstat'ed and then opened with
    ``O_NOFOLLOW`` relative to its parent descriptor, and the post-open fstat
    must agree on device/inode, so a symlink swap or path-component race
    fails closed. The held root descriptor is never closed; each intermediate
    descriptor is closed once its child opens (O(1) descriptors while
    descending). Every OSError is translated to a bounded ContractError.
    """
    open_fds: List[int] = [root_fd]
    try:
        for index, component in enumerate(components):
            final = index == len(components) - 1
            parent = open_fds[-1]
            try:
                pre = os.stat(component, dir_fd=parent, follow_symlinks=False)
            except OSError as error:
                raise ContractError(f"{what}: cannot stat {component!r}: {error}") from error
            if stat.S_ISLNK(pre.st_mode):
                raise ContractError(f"{what}: {component!r} is a symlink")
            if final:
                if not stat.S_ISREG(pre.st_mode):
                    kind = "directory" if stat.S_ISDIR(pre.st_mode) else "non-regular file"
                    raise ContractError(
                        f"{what}: {component!r} is a {kind}, not a regular file"
                    )
                flags = os.O_RDONLY | _O_NOFOLLOW | _O_CLOEXEC
            else:
                if not stat.S_ISDIR(pre.st_mode):
                    raise ContractError(f"{what}: {component!r} is not a directory")
                flags = os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC
            try:
                child = os.open(component, flags, dir_fd=parent)
            except OSError as error:
                raise ContractError(f"{what}: cannot open {component!r}: {error}") from error
            open_fds.append(child)
            try:
                post = os.fstat(child)
            except OSError as error:
                raise ContractError(f"{what}: cannot stat {component!r}: {error}") from error
            if (pre.st_dev, pre.st_ino) != (post.st_dev, post.st_ino):
                raise ContractError(f"{what}: {component!r} changed while opening")
            # Close each intermediate once its child is open (O(1) fds). The
            # held root descriptor is never closed.
            if len(open_fds) >= 3:
                doomed = open_fds.pop(-2)
                pending = sys.exc_info()[0]
                try:
                    os.close(doomed)
                except OSError as error:
                    if pending is None:
                        raise ContractError(
                            f"{what}: cannot close {component!r}: {error}"
                        ) from error
        return open_fds[-1]
    except Exception:
        for fd in open_fds:
            if fd != root_fd:
                try:
                    os.close(fd)
                except OSError:
                    pass
        raise


def _hash_fd(fd: int, max_bytes: int, hash_buffer: int, what: str) -> Tuple[int, str, _Identity]:
    """Hash a whole regular file descriptor with bounded identity checks."""
    try:
        before = os.fstat(fd)
    except OSError as error:
        raise ContractError(f"{what}: cannot stat: {error}") from error
    if not stat.S_ISREG(before.st_mode):
        raise ContractError(f"{what} is not a regular file")
    size = before.st_size
    if size > max_bytes:
        raise ContractError(f"{what} exceeds the {max_bytes}-byte bound")
    digest = hashlib.sha256()
    remaining = size
    while remaining > 0:
        try:
            chunk = os.read(fd, min(hash_buffer, remaining))
        except OSError as error:
            raise ContractError(f"{what}: cannot read: {error}") from error
        if not chunk:
            raise ContractError(f"{what} was truncated while reading")
        digest.update(chunk)
        remaining -= len(chunk)
    try:
        after = os.fstat(fd)
    except OSError as error:
        raise ContractError(f"{what}: cannot stat: {error}") from error
    if _identity(before) != _identity(after):
        raise ContractError(f"{what} changed while reading")
    return size, digest.hexdigest(), _identity(after)


def _hash_range_fd(
    fd: int, start: int, end: int, expected: _Identity, hash_buffer: int, what: str
) -> str:
    """Hash the half-open range [start, end) with identity revalidation."""
    try:
        before = os.fstat(fd)
    except OSError as error:
        raise ContractError(f"{what}: cannot stat: {error}") from error
    if _identity(before) != expected:
        raise ContractError(f"{what}: source changed between verification passes")
    if start >= end:
        raise ContractError(f"{what}: section must be a nonempty half-open range")
    try:
        os.lseek(fd, start, os.SEEK_SET)
    except OSError as error:
        raise ContractError(f"{what}: cannot seek: {error}") from error
    digest = hashlib.sha256()
    remaining = end - start
    while remaining > 0:
        try:
            chunk = os.read(fd, min(hash_buffer, remaining))
        except OSError as error:
            raise ContractError(f"{what}: cannot read: {error}") from error
        if not chunk:
            raise ContractError(f"{what} was truncated while reading")
        digest.update(chunk)
        remaining -= len(chunk)
    try:
        after = os.fstat(fd)
    except OSError as error:
        raise ContractError(f"{what}: cannot stat: {error}") from error
    if _identity(after) != expected:
        raise ContractError(f"{what}: source changed while reading section")
    return digest.hexdigest()
