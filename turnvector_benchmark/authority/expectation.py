"""Private immutable BenchmarkExpectation input envelope."""

from dataclasses import dataclass
from typing import Final

from .bound_bytes import BoundBytesRef

LOGICAL_PATH: Final = "expectations/turnvector-implementation-v2.json"
LOGICAL_NAME: Final = "benchmark_expectation"


@dataclass(frozen=True)
class BenchmarkExpectation:
    """Nominal compiler input containing exactly the bound expectation bytes."""

    expectation_ref: BoundBytesRef

    def __post_init__(self) -> None:
        if not isinstance(self.expectation_ref, BoundBytesRef):
            from .errors import CompilerPreconditionViolation

            raise CompilerPreconditionViolation("input_identity_mismatch")
