"""Tests-only deterministic modeled-allocation fault seam."""

from turnvector_benchmark.authority.compiler_accounting import _FaultInjector


# The tests import a domain-named seam while exercising the exact production
# ordinal implementation; no parallel fault mechanism is introduced.
FaultInjection = _FaultInjector
