"""Generators — build the four output artefacts from a Tally Migration Session.

Work Item 7 scope:

* ``opening_je``     — Main Opening Journal Entry (Draft) for non-party,
                      non-student leaf opening balances. (This module.)
* ``oit_csv``        — Opening Invoice Tool CSV for net-Cr vendors. (Pending.)
* ``party_advance_je`` — Party-wise Dr JE for net-Dr vendors. (Pending.)
* ``students_csv``   — 4-column CSV handoff to dux_voucher. (Pending.)
"""

from __future__ import annotations

from rgi_migration.generators.oit_csv import (
    OITGenerationError,
    OITRow,
    SupplierInfo,
    SupplierIssue,
    build_oit_rows,
    format_csv,
    generate_oit_csv,
)
from rgi_migration.generators.opening_je import (
    MainJEGenerationError,
    MainJEPayload,
    build_je_payload,
    generate_main_opening_je,
)

__all__ = [
    # Generator #1 — Main Opening JE
    "MainJEGenerationError",
    "MainJEPayload",
    "build_je_payload",
    "generate_main_opening_je",
    # Generator #2 — OIT CSV
    "OITGenerationError",
    "OITRow",
    "SupplierInfo",
    "SupplierIssue",
    "build_oit_rows",
    "format_csv",
    "generate_oit_csv",
]
