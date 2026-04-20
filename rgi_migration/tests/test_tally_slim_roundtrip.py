"""Round-trip contract test for the Work Item 8 slim preprocessor.

If you add a whitelist entry and this test still passes, the entry was
noise. If you add a parser field that reads a new tag and forget to
whitelist it, this test FAILS — which is the whole point.

Two tiers:

- **Tier 1** (``test_roundtrip_small_sample``) uses the committed
  ``sample_cacspu_masters_sample.xml`` (10 MB curated subset) and runs
  in CI on every ``pytest``.  Regressions in the whitelist fail this
  test.

- **Tier 2** (``test_roundtrip_full_cacspu``) uses the full 221 MB
  CACSPU export, gated on the ``RGI_FULL_XML_PATH`` environment variable
  (same convention as the rest of the full-file test suite per
  ``rgi_migration/tests/fixtures/README.md``).  Also asserts
  ``slim_size < 10 MB`` as a whitelist-bloat sanity check.

Assertions cover every field on ``TrialBalance``, ``Ledger``, ``Group``,
and ``BillAllocation`` — float comparisons tolerated within 0.01, all
other fields compared by equality.  Per-ledger comparison uses
``(name, tally_id)`` sort key per CLAUDE.md's "identity is always
(name, tally_id)" rule.  Group children compared as sets since the
preprocessor may reorder TALLYMESSAGE emission; set-equality preserves
semantic correctness without over-constraining order.

``parse_warnings`` intentionally NOT asserted equal — the preprocessor
may emit one extra warning for envelope synthesis on degenerate inputs,
but shouldn't drift further.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from rgi_migration.parsers.tally_xml_parser import parse_xml
from tools.tally_slim.preprocessor import preprocess

_FIXTURES = Path(__file__).parent / "fixtures"
_SMALL_SAMPLE = _FIXTURES / "sample_cacspu_masters_sample.xml"

_SLIM_SIZE_CAP_BYTES = 10 * 1024 * 1024  # 10 MB — whitelist-bloat sanity check


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ledger_key(l) -> tuple[str, str]:  # noqa: ANN001 — local type, trivial
    """CLAUDE.md identity rule: (name, tally_id) tuple, never name alone."""
    return (l.name, l.tally_id or "")


def _assert_trial_balance_equal(full, slim) -> None:       # noqa: ANN001
    assert full.company_name == slim.company_name, "company_name mismatch"
    assert full.tb_date == slim.tb_date, "tb_date mismatch"
    assert full.source_format == slim.source_format == "xml"
    assert len(full.ledgers) == len(slim.ledgers), (
        f"main ledger count: full={len(full.ledgers)} slim={len(slim.ledgers)}"
    )
    assert len(full.student_ledgers) == len(slim.student_ledgers), (
        f"student ledger count: full={len(full.student_ledgers)} "
        f"slim={len(slim.student_ledgers)}"
    )
    assert len(full.system_ledgers) == len(slim.system_ledgers), (
        f"system ledger count: full={len(full.system_ledgers)} "
        f"slim={len(slim.system_ledgers)}"
    )
    assert len(full.groups) == len(slim.groups), (
        f"group count: full={len(full.groups)} slim={len(slim.groups)}"
    )
    assert abs(full.total_dr - slim.total_dr) < 0.01, (
        f"total_dr: full={full.total_dr} slim={slim.total_dr}"
    )
    assert abs(full.total_cr - slim.total_cr) < 0.01, (
        f"total_cr: full={full.total_cr} slim={slim.total_cr}"
    )
    assert full.is_balanced == slim.is_balanced


def _assert_bill_allocations_equal(
    full_bills, slim_bills, ledger_name: str          # noqa: ANN001
) -> None:
    assert len(full_bills) == len(slim_bills), (
        f"ledger {ledger_name!r}: bill count {len(full_bills)} vs {len(slim_bills)}"
    )
    # Sort defensively — bill order preserved by lxml but set-wise equality
    # is the semantic contract.
    fs = sorted(full_bills, key=lambda b: (b.bill_name, b.dr_cr, round(b.amount, 2)))
    ss = sorted(slim_bills, key=lambda b: (b.bill_name, b.dr_cr, round(b.amount, 2)))
    for fb, sb in zip(fs, ss, strict=True):
        assert fb.bill_name == sb.bill_name, (
            f"ledger {ledger_name!r}: bill_name {fb.bill_name!r} vs {sb.bill_name!r}"
        )
        assert fb.dr_cr == sb.dr_cr, (
            f"ledger {ledger_name!r} bill {fb.bill_name!r}: "
            f"dr_cr {fb.dr_cr!r} vs {sb.dr_cr!r}"
        )
        assert abs(fb.amount - sb.amount) < 0.01, (
            f"ledger {ledger_name!r} bill {fb.bill_name!r}: "
            f"amount {fb.amount} vs {sb.amount}"
        )


def _assert_ledgers_equal(full_list, slim_list, label: str) -> None:  # noqa: ANN001
    full_sorted = sorted(full_list, key=_ledger_key)
    slim_sorted = sorted(slim_list, key=_ledger_key)
    for f, s in zip(full_sorted, slim_sorted, strict=True):
        tag = f"{label} ledger {f.name!r} (tally_id={f.tally_id!r})"
        assert f.name == s.name, f"{tag}: name {f.name!r} vs {s.name!r}"
        assert f.tally_id == s.tally_id, f"{tag}: tally_id"
        assert f.parent_group == s.parent_group, f"{tag}: parent_group"
        assert f.parent_chain == s.parent_chain, (
            f"{tag}: parent_chain {f.parent_chain!r} vs {s.parent_chain!r}"
        )
        assert f.root_type == s.root_type, f"{tag}: root_type"
        assert abs(f.opening_dr - s.opening_dr) < 0.01, (
            f"{tag}: opening_dr {f.opening_dr} vs {s.opening_dr}"
        )
        assert abs(f.opening_cr - s.opening_cr) < 0.01, (
            f"{tag}: opening_cr {f.opening_cr} vs {s.opening_cr}"
        )
        assert abs(f.net_amount - s.net_amount) < 0.01, f"{tag}: net_amount"
        assert f.net_side == s.net_side, f"{tag}: net_side"
        assert f.is_leaf == s.is_leaf, f"{tag}: is_leaf"
        assert f.is_system_account == s.is_system_account, f"{tag}: is_system_account"
        assert f.is_student_ledger == s.is_student_ledger, f"{tag}: is_student_ledger"
        assert f.is_pnl_closed_zero == s.is_pnl_closed_zero, f"{tag}: is_pnl_closed_zero"
        # source_row is Excel-only; both sides XML so both None.
        assert f.source_row == s.source_row, f"{tag}: source_row"
        _assert_bill_allocations_equal(f.bill_allocations, s.bill_allocations, f.name)


def _assert_groups_equal(full_list, slim_list) -> None:       # noqa: ANN001
    full_sorted = sorted(full_list, key=lambda g: g.name)
    slim_sorted = sorted(slim_list, key=lambda g: g.name)
    for f, s in zip(full_sorted, slim_sorted, strict=True):
        tag = f"group {f.name!r}"
        assert f.name == s.name, tag
        assert f.parent == s.parent, f"{tag}: parent {f.parent!r} vs {s.parent!r}"
        assert f.root_type == s.root_type, f"{tag}: root_type"
        # Semantic equality: set, not list.  Preprocessor may emit TALLYMESSAGE
        # in a different order than source, which would reorder Group.children.
        assert set(f.children) == set(s.children), (
            f"{tag}: children set differ; "
            f"only-in-full={sorted(set(f.children) - set(s.children))} "
            f"only-in-slim={sorted(set(s.children) - set(f.children))}"
        )


def _run_roundtrip(full_xml_path: Path, slim_output: Path) -> int:
    """Run the preprocess + parse-both + assert-equal pipeline.

    Returns the slim output size in bytes (used by Tier 2 for the
    whitelist-bloat sanity check).
    """
    # --no-strict: we do our own parse below for the comparison anyway;
    # --strict would just double-parse.
    preprocess(full_xml_path, slim_output, strict=False)

    full = parse_xml(str(full_xml_path))
    slim = parse_xml(str(slim_output))

    _assert_trial_balance_equal(full, slim)
    _assert_ledgers_equal(full.ledgers, slim.ledgers, "main")
    _assert_ledgers_equal(full.student_ledgers, slim.student_ledgers, "student")
    _assert_ledgers_equal(full.system_ledgers, slim.system_ledgers, "system")
    _assert_groups_equal(full.groups, slim.groups)

    # Warning drift sanity check (not asserted equal — see module docstring).
    assert len(slim.parse_warnings) <= len(full.parse_warnings) + 1, (
        f"slim emitted {len(slim.parse_warnings)} warnings vs "
        f"full={len(full.parse_warnings)} — unexpected drift. "
        f"slim warnings: {slim.parse_warnings!r}"
    )

    return slim_output.stat().st_size


# ---------------------------------------------------------------------------
# Tier 1 — always runs
# ---------------------------------------------------------------------------

def test_roundtrip_small_sample(tmp_path: Path) -> None:
    """Runs in CI. Uses the committed 10 MB sample_cacspu_masters_sample.xml.

    A regression in the whitelist (dropped field parser needs, or
    attribute stripped that parser reads) fails here.
    """
    assert _SMALL_SAMPLE.exists(), (
        f"committed sample fixture missing at {_SMALL_SAMPLE} — "
        f"rebuild via scripts/build_sample_masters_xml.py"
    )
    slim = tmp_path / "slim.xml"
    _run_roundtrip(_SMALL_SAMPLE, slim)


# ---------------------------------------------------------------------------
# Tier 2 — gated on env var (full 221 MB CACSPU export)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not os.environ.get("RGI_FULL_XML_PATH"),
    reason="RGI_FULL_XML_PATH not set; Tier 2 full-file round-trip skipped",
)
def test_roundtrip_full_cacspu(tmp_path: Path) -> None:
    """Runs when RGI_FULL_XML_PATH points to the full CACSPU masters XML.

    Also asserts slim output is under 10 MB — if this fails, the
    whitelist has grown to retain tags the parser doesn't need.
    """
    full_path = Path(os.environ["RGI_FULL_XML_PATH"])
    assert full_path.exists(), (
        f"RGI_FULL_XML_PATH points to missing file: {full_path}"
    )
    slim = tmp_path / "slim.xml"
    slim_size = _run_roundtrip(full_path, slim)
    assert slim_size < _SLIM_SIZE_CAP_BYTES, (
        f"Slim output {slim_size:,} bytes ({slim_size / 1024 / 1024:.1f} MB) "
        f"exceeds 10 MB cap — whitelist may have regressed to include "
        f"unnecessary tags"
    )
