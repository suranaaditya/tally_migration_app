"""Tests for ``dedupe_ledgers_by_identity``.

Surfaced during Week 4 Item 2 Commit 3 reviewer-override verification:
CACSPU's real 221 MB Tally export emits 25 Ledger pairs with
byte-identical identity + balance (one with non-zero opening that
would double-count in the Main JE without dedup). First-wins dedup
at the parser boundary.
"""

from __future__ import annotations

from rgi_migration.parsers.normalized_schema import (
    Ledger,
    dedupe_ledgers_by_identity,
)


def _ledger(
    name: str,
    *,
    tally_id: str | None = None,
    opening_dr: float = 0.0,
    opening_cr: float = 0.0,
) -> Ledger:
    net = opening_cr - opening_dr
    side = "Dr" if opening_dr > opening_cr else ("Cr" if opening_cr > opening_dr else "Zero")
    return Ledger(
        name=name,
        tally_id=tally_id,
        parent_group="",
        parent_chain=[],
        root_type="Asset",
        opening_dr=opening_dr,
        opening_cr=opening_cr,
        net_amount=net,
        net_side=side,
    )


def test_dedup_no_duplicates_is_identity() -> None:
    """No duplicates → list returned unchanged, zero warnings."""
    ledgers = [
        _ledger("A", tally_id="1", opening_dr=100.0),
        _ledger("B", tally_id="2", opening_cr=200.0),
    ]
    warnings: list[str] = []
    out = dedupe_ledgers_by_identity(ledgers, warnings)
    assert out == ledgers
    assert warnings == []


def test_dedup_identical_duplicates_first_wins() -> None:
    """CACSPU-shaped case: byte-identical dup — keep first, warn concisely."""
    ledgers = [
        _ledger("Furniture Material Work In Progress", tally_id="1141", opening_dr=455.48),
        _ledger("Furniture Material Work In Progress", tally_id="1141", opening_dr=455.48),
    ]
    warnings: list[str] = []
    out = dedupe_ledgers_by_identity(ledgers, warnings)
    assert len(out) == 1
    assert out[0] is ledgers[0]  # first-wins (identity check)
    assert len(warnings) == 1
    assert "Deduped 1" in warnings[0]
    assert "Furniture" in warnings[0]


def test_dedup_diverging_duplicates_first_wins_loud_warning() -> None:
    """Same identity but different balances — keep first, LOUD warning
    so reviewers know the source is corrupt."""
    ledgers = [
        _ledger("Sus Vendor", tally_id="V1", opening_cr=100.0),
        _ledger("Sus Vendor", tally_id="V1", opening_cr=250.0),
    ]
    warnings: list[str] = []
    out = dedupe_ledgers_by_identity(ledgers, warnings)
    assert len(out) == 1
    assert out[0].opening_cr == 100.0  # first wins
    assert any("DIVERGING DUPLICATES" in w for w in warnings)


def test_dedup_none_tally_id_identity() -> None:
    """Ledgers with tally_id=None share identity via "" key — and dedup."""
    ledgers = [
        _ledger("Dumm", tally_id=None),
        _ledger("Dumm", tally_id=None),
        _ledger("Real", tally_id="X"),
    ]
    warnings: list[str] = []
    out = dedupe_ledgers_by_identity(ledgers, warnings)
    assert len(out) == 2
    assert [l.name for l in out] == ["Dumm", "Real"]


def test_dedup_more_than_five_samples_shows_overflow_count() -> None:
    """Warning message truncates samples at 5 and shows "+N more"."""
    ledgers = []
    for i in range(8):
        name = f"Ledger-{i}"
        ledgers.append(_ledger(name, tally_id=str(i)))
        ledgers.append(_ledger(name, tally_id=str(i)))  # dup
    warnings: list[str] = []
    dedupe_ledgers_by_identity(ledgers, warnings)
    msg = warnings[0]
    assert "Deduped 8" in msg
    assert "+3 more" in msg  # 8 - 5 shown = 3 overflow


def test_dedup_preserves_order_of_firsts() -> None:
    """The first-encountered copy wins AND the output order matches first-seen order."""
    ledgers = [
        _ledger("B", tally_id="2"),
        _ledger("A", tally_id="1"),
        _ledger("B", tally_id="2"),  # dup of B
        _ledger("C", tally_id="3"),
    ]
    warnings: list[str] = []
    out = dedupe_ledgers_by_identity(ledgers, warnings)
    assert [l.name for l in out] == ["B", "A", "C"]
