"""Immutable, metadata-validated byte-buffer references for CoverageCompiler."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from turnvector_benchmark.compile_limits import checked_mul
from turnvector_benchmark.core import ContractError

from .errors import CompilerPreconditionViolation


Buffer = Union[bytes, memoryview]


def _input_identity_mismatch() -> CompilerPreconditionViolation:
    return CompilerPreconditionViolation("input_identity_mismatch")


@dataclass(frozen=True)
class BoundBytesRef:
    """Hold one admitted immutable byte buffer without reading its payload.

    Exact ``bytes`` objects and positive-dimensional, read-only,
    C-contiguous, format-``B`` memoryviews exported ultimately by exact
    ``bytes`` are admitted. Construction inspects metadata only.
    """

    buffer: Buffer

    def __post_init__(self) -> None:
        buffer = self.buffer
        if type(buffer) is bytes:
            return
        if type(buffer) is not memoryview:
            raise _input_identity_mismatch()

        try:
            readonly = buffer.readonly
            format_name = buffer.format
            itemsize = buffer.itemsize
            ndim = buffer.ndim
            shape = buffer.shape
            nbytes = buffer.nbytes
            c_contiguous = buffer.c_contiguous
            exporter = buffer.obj
        except (TypeError, ValueError, AttributeError):
            # Released views and objects without canonical buffer metadata.
            raise _input_identity_mismatch() from None

        if (
            readonly is not True
            or format_name != "B"
            or itemsize != 1
            or ndim < 1
            or shape is None
            or len(shape) != ndim
            or c_contiguous is not True
        ):
            raise _input_identity_mismatch()

        try:
            product = 1
            for dimension in shape:
                product = checked_mul(product, dimension, "BoundBytesRef shape")
        except ContractError:
            raise _input_identity_mismatch() from None
        if product != nbytes:
            raise _input_identity_mismatch()

        # CPython normally exposes the ultimate exporter directly. Following
        # memoryview wrappers as well keeps the contract explicit without
        # indexing or otherwise touching payload bytes.
        seen = set()
        while type(exporter) is memoryview:
            if id(exporter) in seen:
                raise _input_identity_mismatch()
            seen.add(id(exporter))
            try:
                exporter = exporter.obj
            except (TypeError, ValueError, AttributeError):
                raise _input_identity_mismatch() from None
        if type(exporter) is not bytes:
            raise _input_identity_mismatch()

    @property
    def nbytes(self) -> int:
        """Return the O(1) actual byte count of the admitted buffer."""
        if type(self.buffer) is bytes:
            return len(self.buffer)
        return self.buffer.nbytes
