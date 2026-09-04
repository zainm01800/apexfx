"""Persistent, paper-only V14 regime-switch forward books.

The package is deliberately isolated from every broker/execution adapter.  It
contains a deterministic state transition, causal market-data validation and a
namespaced Supabase document store for the two experimental GBP accounts.
"""

from .spec import BOOKS, BookSpec, book_spec
from .state import (
    DataRevisionError,
    ForwardInvariantError,
    advance,
    enforce_persistence_deadline,
    new_state,
    public_payload,
    validate_state,
)

__all__ = [
    "BOOKS",
    "BookSpec",
    "DataRevisionError",
    "ForwardInvariantError",
    "advance",
    "book_spec",
    "enforce_persistence_deadline",
    "new_state",
    "public_payload",
    "validate_state",
]
