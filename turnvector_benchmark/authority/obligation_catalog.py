"""Private immutable obligation-catalog input envelope."""

from dataclasses import dataclass
from typing import Final

from .bound_bytes import BoundBytesRef

LOGICAL_PATH: Final = "authority/obligation-catalog-v1.jsonl"
LOGICAL_NAME: Final = "obligation_catalog"


@dataclass(frozen=True)
class ObligationCatalog:
    """Nominal compiler input containing exactly the bound catalog bytes."""

    catalog_ref: BoundBytesRef

    def __post_init__(self) -> None:
        if not isinstance(self.catalog_ref, BoundBytesRef):
            from .errors import CompilerPreconditionViolation

            raise CompilerPreconditionViolation("input_identity_mismatch")
