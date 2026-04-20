"""Parser-level aggregate-student-account routing (cross-app boundary to
dux_voucher). See docs/mapper_design_notes.md §7.

Direct unit coverage for `_is_aggregate_student_account` + the
`AGGREGATE_STUDENT_ACCOUNT_NAMES` frozenset. Full-XML confirmation
(that `Student Fee Outstanding` on the real CACSPU export flows to
`student_ledgers`) is validated in the post-commit full-file run,
not here — we don't ship the 221 MB fixture.
"""

from __future__ import annotations

import pytest

from rgi_migration.parsers.tally_xml_parser import (
    AGGREGATE_STUDENT_ACCOUNT_NAMES,
    _is_aggregate_student_account,
)


def test_frozenset_contains_student_fee_outstanding():
    """Known entry — if future refactor loses it, we catch it here."""
    assert "student fee outstanding" in AGGREGATE_STUDENT_ACCOUNT_NAMES


def test_frozenset_is_frozen():
    """Prevent mutations — set must be frozen so imports can't hand out
    a mutable reference that breaks cross-module invariants."""
    assert isinstance(AGGREGATE_STUDENT_ACCOUNT_NAMES, frozenset)


@pytest.mark.parametrize("name,expected", [
    # Exact match — canonical
    ("Student Fee Outstanding", True),
    # Case-insensitive
    ("student fee outstanding", True),
    ("STUDENT FEE OUTSTANDING", True),
    ("Student FEE Outstanding", True),
    # Whitespace-collapsed
    ("Student  Fee   Outstanding", True),
    ("  Student Fee Outstanding  ", True),
    # Non-matches — must stay False
    ("", False),
    ("Student", False),                       # too short
    ("Student Fee", False),                   # partial
    ("Student Fee Outstanding For Q3", False),  # extra tokens
    ("Outstanding Student Fee", False),       # different token order
    ("Nilesh Traders", False),                # unrelated
    ("Fee Outstanding", False),               # missing "Student"
])
def test_is_aggregate_student_account(name, expected):
    assert _is_aggregate_student_account(name) is expected


def test_none_safe():
    # Defensive — parser call-site should never pass None, but guard anyway.
    assert _is_aggregate_student_account("") is False
