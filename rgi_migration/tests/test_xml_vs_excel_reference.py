"""Regression gate for the Tally XML and Excel parsers.

Default fixture: the 500+ledger *sample* XML
(``sample_cacspu_masters_sample.xml``, committed) plus the full opening-TB
Excel (``sample_cacspu_opening_tb.xlsx``, committed).  The full 221 MB
masters XML is NOT committed and NOT inside the repo -- it lives wherever
the developer chose when regenerating (convention: ``~/tally-exports/``).
To run against it, set ``RGI_FULL_XML_PATH`` to its absolute path, e.g.::

    RGI_FULL_XML_PATH=~/tally-exports/cacspu_masters.xml pytest ...

Asserts, against the CACSPU reference fixtures, that:

XML parser:
1. (full only) ``main_ledgers + student_ledgers`` balance within 1% of
   grand total.  Skipped on the curated sample (cannot balance by design).
2. The student CSV contains at least 250 rows.
3. All 9 known-side diagnostic ledgers sit on the expected Dr/Cr side —
   direct regression gate for the sign rule
   (``docs/tally_sign_convention.md §1``).
4. Zero side-flips vs the reference opening-TB Excel — every ledger
   present in both files has XML (Dr, Cr) matching Excel Opening or
   Closing on the same side (``docs/tally_sign_convention.md §2``).
5. No ledger appears in more than one of ``main_ledgers`` /
   ``student_ledgers`` / ``system_ledgers`` — disjoint by
   ``(name, tally_id)`` tuple.
6. ``Profit & Loss A/c`` lives in ``main_ledgers`` with
   ``is_system_account=True`` (``docs/tally_sign_convention.md §4.1``).

Excel parser (hierarchy-informed by XML companion):
7. Zero cross-format side-flips — XML and Excel parsers agree on Dr/Cr
   for every common ledger.
8. Partition buckets (main vs student vs system) agree between XML and
   Excel for every common ledger.
9. Excel parser produces non-trivial output and Dr/Cr balance within
   tolerance (1% full-file, 10% sample).

Each assertion is a separate pytest function so a failure in one doesn't
mask the others.

-------------------------------------------------------------------------
Why these specific ledger names appear in the assertions
-------------------------------------------------------------------------
The committed sample XML is a curated subset of the full CACSPU All
Masters export.  Beyond the first 500 LEDGER elements in document order,
the sample unconditionally includes the 13 ledgers listed below (the
``MUST_INCLUDE`` set in ``scripts/build_sample_masters_xml.py``).  Each
one is referenced by at least one assertion; removing any of them would
silently weaken the test suite.

**Side-flip victims — the bug that started the sign-convention fix (§1):**
    Computer & accessories-9005                         (Fixed Asset, Dr)
    Furniture & fixture-9009                             (Fixed Asset, Dr)
    Building Revaluation-1373                            (Fixed Asset, Dr)
    Bank of Maharashtra (Cap) - 60451303968-11079        (Bank, Dr)

**Sign-rule reference "already-correct" ledgers — cross-checked as
a control group during the sign investigation:**
    Depreciation Fund-2125                               (Reserve, Cr)
    Income Expenditure A/c-2127                          (Reserve, Cr)
    CAFE SESSY-VC0096                                    (Creditor, Cr)
    Anupam Silver Works-VA0243                           (Creditor, Cr with bill advance)

**System account — validates is_system_account flag + no-<PARENT> path:**
    Profit & Loss A/c                                    (Equity, Cr — Tally internal)

**matches_neither edge cases — documented in §6, residual contributors:**
    Cash Purchases-SX0001                                (bill sum = Dr 1,851)
    SHIROLE NEHA DATTATRAY 21A0007BCAG1039               (bill sum = Dr 2,250)

**Not-in-Excel residual contributors — documented in §6.1:**
    MANUJA LAWNS-VM0073                                  (Creditor, bill sum = Cr 6,780)
    SHARAD EVENTS-VS0216                                 (Creditor, bill sum = Dr 36,000)
-------------------------------------------------------------------------
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from openpyxl import load_workbook

from rgi_migration.parsers.tally_xml_parser import parse_xml
from rgi_migration.parsers.tally_excel_parser import (
    parse_excel,
    hierarchy_from_parsed_tb,
)

FIXTURES = Path(__file__).parent / "fixtures"

# Default to the committed 500+ledger sample (small, always present).
# Full-file runs are opt-in: set RGI_FULL_XML_PATH to the absolute path of
# your locally-stored full Tally export (which lives OUTSIDE this repo;
# see fixtures/README.md).
_SAMPLE_XML = FIXTURES / "sample_cacspu_masters_sample.xml"
_full_path_env = os.getenv("RGI_FULL_XML_PATH", "")
_full_path = Path(os.path.expanduser(_full_path_env)) if _full_path_env else None
XML_PATH = _full_path if (_full_path and _full_path.exists()) else _SAMPLE_XML

XLSX_PATH = FIXTURES / "sample_cacspu_opening_tb.xlsx"

TOL = 1.0  # ₹1 tolerance, same as the diagnostic

# Sample mode vs full-file mode affects a few thresholds:
#   - Balance assertion: the sample is a curated subset (~587 ledgers out
#     of 6,027); it intentionally does NOT balance because we didn't
#     include every leaf.  The full file does balance to within 1%.
#   - Student count: full has 4,062 students, sample has ~481.  Both
#     comfortably clear the 250-row minimum.
#   - Cross-format parity and partition-disjoint assertions work on both.
_IS_SAMPLE = XML_PATH == _SAMPLE_XML


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def tb():
    """Parsed ParsedTallyTB for CACSPU from the XML (module-scoped;
    220 MB XML is slow)."""
    assert XML_PATH.exists(), f"XML fixture missing: {XML_PATH}"
    return parse_xml(str(XML_PATH))


@pytest.fixture(scope="module")
def xl_tb(tb):
    """Parsed ParsedTallyTB for CACSPU from the Excel, hierarchy-informed
    by the companion XML parse."""
    assert XLSX_PATH.exists(), f"Excel fixture missing: {XLSX_PATH}"
    known_groups, known_leaf_parents = hierarchy_from_parsed_tb(tb)
    return parse_excel(
        str(XLSX_PATH),
        use_columns="closing",
        known_groups=known_groups,
        known_ledger_parents=known_leaf_parents,
    )


@pytest.fixture(scope="module")
def excel_ledgers() -> dict[str, tuple[float, float, float, float]]:
    """Return {name: (op_dr, op_cr, cl_dr, cl_cr)} from the reference Excel."""
    assert XLSX_PATH.exists(), f"Excel fixture missing: {XLSX_PATH}"
    wb = load_workbook(str(XLSX_PATH), data_only=True)
    ws = wb.active

    def num(v) -> float:
        return float(v) if isinstance(v, (int, float)) else 0.0

    out: dict[str, tuple[float, float, float, float]] = {}
    for row in ws.iter_rows(min_row=9, max_row=ws.max_row):
        name = str(row[0].value or "").strip()
        if not name or name.lower().startswith("grand total"):
            continue
        out[name] = (
            round(num(row[1].value), 2),
            round(num(row[2].value), 2),
            round(num(row[3].value), 2),
            round(num(row[4].value), 2),
        )
    return out


# ---------------------------------------------------------------------------
# Assertion 1 — main + students sum matches the raw TB balance
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    _IS_SAMPLE,
    reason=(
        "The 500+ledger sample fixture is a curated subset and does not "
        "balance; set RGI_FULL_XML_PATH=/path/to/cacspu_masters.xml "
        "(the full 221 MB export, stored outside the repo) to exercise "
        "this assertion.  Balance correctness is preserved indirectly on "
        "the sample via the sign-flip and partition-disjoint assertions."
    ),
)
def test_main_plus_students_balance_raw_tb(tb):
    """Combined Dr and Cr over main + student ledgers must balance to
    within **1% of grand total**.

    Tolerance rationale (see docs/tally_sign_convention.md §6.1):

    * The raw Tally XML carries a real residual (₹342,358.48 on CACSPU —
      ~0.2% of grand total) that is baked into the source company's data,
      not introduced by the parser.  A ₹5K absolute tolerance we initially
      considered is too tight for real-world entities.
    * 1% of grand total matches the parser's own ``parse_warnings``
      imbalance-warning threshold (``_IMBALANCE_THRESHOLD`` in the parser).
      Having test and parser use the same number means both signals either
      fire together or stay silent together — no split policy.
    * The Week-3 JE builder absorbs the residual into the
      ``Temporary Opening - {ABBR}`` balancer entry (per
      RGI_Migration_Rules.md §3.7), so the residual is never lost — it
      becomes a reviewable line item.  A large Temp Opening value per
      entity is itself a diagnostic signal.

    The test still catches real bugs: if the residual exceeds 1% of grand
    total, the parser is losing material data or the source Tally export
    used the wrong setting.  Skipped on the sample fixture because curated
    subsets intentionally don't balance.
    """
    combined_dr = sum(l.opening_dr for l in tb.ledgers) + sum(
        l.opening_dr for l in tb.student_ledgers
    )
    combined_cr = sum(l.opening_cr for l in tb.ledgers) + sum(
        l.opening_cr for l in tb.student_ledgers
    )
    gap = abs(combined_dr - combined_cr)
    grand = max(combined_dr, combined_cr)
    tolerance = grand * 0.01  # 1% of grand total

    assert gap <= tolerance, (
        f"Main + student ledgers imbalance Rs.{gap:,.2f} exceeds 1% of "
        f"grand total Rs.{grand:,.2f} (threshold Rs.{tolerance:,.2f}).\n"
        f"  Combined Dr = {combined_dr:,.2f}\n"
        f"  Combined Cr = {combined_cr:,.2f}\n"
        f"Likely causes: (a) sign-fix regression, (b) export done without "
        f"'Export closing balances as opening balance' = Yes "
        f"(see docs/tally_sign_convention.md §3), or (c) partitioning bug "
        f"dropping ledgers.  A residual <= 1% is expected and is absorbed "
        f"by the Week-3 JE Temp Opening balancer."
    )


# ---------------------------------------------------------------------------
# Assertion 2 — student CSV contains at least 250 rows
# ---------------------------------------------------------------------------

def test_student_csv_has_at_least_250_rows(tb, tmp_path):
    """Sample has ~481 students; full file has ~4062.  Both comfortably
    clear the 250-row minimum."""
    out = tmp_path / "students.csv"
    n = tb.to_students_csv(str(out))
    assert n >= 250, f"Expected >= 250 student rows, got {n}"
    assert out.exists()
    assert out.stat().st_size > 0
    # Header sanity
    first_line = out.read_text(encoding="utf-8").splitlines()[0]
    assert first_line == "Student Name,Debit Amount,Credit Amount,Parent Group,Notes"


# Side-flip regression: every known-flipped diagnostic ledger must be on
# the correct Dr/Cr side.  This is the *point* of the regression test;
# it stays valid on both sample and full fixtures.
DIAGNOSTIC_SIDES = {
    # (name, tally_id): expected_side
    ("Computer & accessories", "9005"): "Dr",
    ("Furniture & fixture", "9009"): "Dr",
    ("Building Revaluation", "1373"): "Dr",
    ("Bank of Maharashtra (Cap) - 60451303968", "11079"): "Dr",
    ("Depreciation Fund", "2125"): "Cr",
    ("Income Expenditure A/c", "2127"): "Cr",
    ("CAFE SESSY-VC0096", None): "Cr",  # non-digit ID, no suffix strip
    ("Anupam Silver Works-VA0243", None): "Cr",
    ("Profit & Loss A/c", None): "Cr",
}


def test_diagnostic_ledgers_on_correct_side(tb):
    """Direct side check on the 9 diagnostic ledgers whose expected Dr/Cr
    is known from the CACSPU opening-TB Excel.  A regression in the sign
    rule would flip one or more of these.
    """
    all_leaves = tb.ledgers + tb.student_ledgers + tb.system_ledgers
    by_key = {(l.name, l.tally_id): l for l in all_leaves}
    errors: list[str] = []
    for (name, tid), expected_side in DIAGNOSTIC_SIDES.items():
        l = by_key.get((name, tid))
        if l is None:
            errors.append(f"MISSING diagnostic ledger: ({name!r}, {tid!r})")
            continue
        actual_side = (
            "Dr" if l.opening_dr > l.opening_cr
            else "Cr" if l.opening_cr > l.opening_dr
            else "Zero"
        )
        if actual_side != expected_side:
            errors.append(
                f"{name}-{tid or ''}: expected {expected_side}, got {actual_side} "
                f"(Dr={l.opening_dr:,.2f} Cr={l.opening_cr:,.2f})"
            )
    assert not errors, "Sign-rule regression detected:\n  " + "\n  ".join(errors)


# ---------------------------------------------------------------------------
# Assertion 3 — zero side-flips against the reference opening-TB Excel
# ---------------------------------------------------------------------------

def _bucket(xdr: float, xcr: float) -> str:
    """Classify a ledger's computed (Dr, Cr) pair as 'Dr', 'Cr', or 'Zero'."""
    if xdr > TOL and xcr <= TOL:
        return "Dr"
    if xcr > TOL and xdr <= TOL:
        return "Cr"
    if xdr <= TOL and xcr <= TOL:
        return "Zero"
    return "Both"  # both-sided account


def test_zero_side_flips_against_excel(tb, excel_ledgers):
    """Every ledger present in both XML and Excel must be on the same side.

    A side-flip is a ledger where the XML places it on one side (Dr or Cr)
    while the Excel reference places it on the other side, on either the
    Opening columns OR the Closing columns.

    We compare XML against BOTH columns and pass if either matches the side,
    because the user's fixture was exported with 'closing as opening' so XML
    matches Closing — but Opening is a valid alternate reference for ledgers
    whose Op and Cl are on the same side.
    """
    # Collect every XML ledger across the three buckets.  The Excel reference
    # includes groups and leaves of every kind, so we match against the full
    # XML universe to avoid missing a side-flip that lives in a student row.
    xml_by_name: dict[str, tuple[float, float]] = {}
    for l in tb.ledgers + tb.student_ledgers + tb.system_ledgers:
        # Reconstruct the full Tally name (with -{ID} suffix) for matching
        full_name = f"{l.name}-{l.tally_id}" if l.tally_id else l.name
        xml_by_name[full_name] = (l.opening_dr, l.opening_cr)

    flips: list[tuple[str, str, str, str]] = []  # (name, xml_side, op_side, cl_side)
    compared = 0

    for name, (op_dr, op_cr, cl_dr, cl_cr) in excel_ledgers.items():
        xml_pair = xml_by_name.get(name)
        if xml_pair is None:
            continue  # not in XML (groups only in Excel, etc.)
        compared += 1

        xml_side = _bucket(xml_pair[0], xml_pair[1])
        op_side = _bucket(op_dr, op_cr)
        cl_side = _bucket(cl_dr, cl_cr)

        # A flip = XML side explicitly contradicts BOTH Excel columns.
        # Zero on one side vs a non-zero side on Excel is NOT a flip (data
        # merely missing), it's only a flip when Dr<->Cr directly.
        op_conflict = (xml_side == "Dr" and op_side == "Cr") or (
            xml_side == "Cr" and op_side == "Dr"
        )
        cl_conflict = (xml_side == "Dr" and cl_side == "Cr") or (
            xml_side == "Cr" and cl_side == "Dr"
        )
        if op_conflict and cl_conflict:
            flips.append((name, xml_side, op_side, cl_side))

    assert compared > 0, "No common ledgers found — fixture names may have drifted"
    assert not flips, (
        f"Side-flips detected (XML says one side, Excel says other on BOTH "
        f"Opening AND Closing columns) — {len(flips)} ledger(s) of {compared} "
        f"compared:\n"
        + "\n".join(
            f"  {n}: XML={xs}  Excel Op={os_}  Excel Cl={cs}"
            for n, xs, os_, cs in flips[:15]
        )
    )


# ---------------------------------------------------------------------------
# Assertion 4 — no ledger appears in two partitions
# ---------------------------------------------------------------------------

def test_no_ledger_is_in_two_partitions(tb):
    """main / student / system partitions must be disjoint by ``(name, tally_id)``.

    The unique key is the ``(cleaned_name, tally_id)`` tuple — not ``name``
    alone.  Two different Tally ledgers can share the same cleaned display
    name while having distinct numeric IDs; this is a legitimate real-world
    condition, not a data anomaly.

    Example from CACSPU:
        * ``Sachin  Gawande-52926``, parent=``Personal Advance`` — staff cash
          advance; lives in ``main_ledgers``.
        * ``Sachin  Gawande-30``, parent=``STUDENTS`` — student ledger;
          lives in ``student_ledgers``.
    Same display name, two real distinct Tally accounts.  Using ``name`` alone
    as the key would raise a spurious disjointness violation.
    """
    def key_set(lst):
        return {(l.name, l.tally_id) for l in lst}

    main_keys = key_set(tb.ledgers)
    student_keys = key_set(tb.student_ledgers)
    system_keys = key_set(tb.system_ledgers)

    main_vs_student = main_keys & student_keys
    main_vs_system = main_keys & system_keys
    student_vs_system = student_keys & system_keys

    assert not main_vs_student, (
        f"Ledgers in both main and student lists: {sorted(main_vs_student)[:20]}"
    )
    assert not main_vs_system, (
        f"Ledgers in both main and system lists: {sorted(main_vs_system)[:20]}"
    )
    assert not student_vs_system, (
        f"Ledgers in both student and system lists: "
        f"{sorted(student_vs_system)[:20]}"
    )


# ---------------------------------------------------------------------------
# Assertion 5 — P&L A/c lives in main_ledgers with is_system_account=True
# ---------------------------------------------------------------------------

def test_pnl_a_c_is_in_main_with_system_flag(tb):
    """P&L A/c is a BS account (equity-nature) — must stay in main_ledgers so
    the TB balance check works, but flagged so the mapper knows to route it
    to ERPNext retained earnings.  See docs/tally_sign_convention.md §4.1.
    """
    pnl = next((l for l in tb.ledgers if l.name == "Profit & Loss A/c"), None)
    assert pnl is not None, "Profit & Loss A/c must be in main_ledgers (tb.ledgers)"
    assert pnl.is_system_account is True, (
        "P&L A/c must carry is_system_account=True for mapper routing"
    )
    assert pnl.is_student_ledger is False, (
        "P&L A/c must not be flagged as a student ledger"
    )
    # And it must NOT be in student_ledgers or system_ledgers
    assert not any(
        l.name == "Profit & Loss A/c" for l in tb.student_ledgers
    ), "P&L A/c must not be in student_ledgers"
    assert not any(
        l.name == "Profit & Loss A/c" for l in tb.system_ledgers
    ), "P&L A/c must not be in system_ledgers (reserved for future use)"


# ===========================================================================
# EXCEL parser regression tests
# ===========================================================================

def _key(l):
    return (l.name, l.tally_id)


def test_excel_parser_has_no_cross_format_side_flips(tb, xl_tb):
    """Every (name, tally_id) ledger present in both the XML and the Excel
    parse must be on the same side (Dr or Cr).  A Dr<->Cr swap across the two
    parsers would indicate the sign rule is being applied inconsistently.

    P&L zero-zero differences are expected (Excel Closing columns show
    pre-close activity while XML OPENINGBALANCE shows post-close zero) and
    are NOT side-flips — ``Zero`` on one side vs a non-zero side on the other
    is data-asymmetric but not a sign contradiction.
    """
    xml_by_key = {
        _key(l): l
        for l in tb.ledgers + tb.student_ledgers + tb.system_ledgers
    }
    xl_by_key = {
        _key(l): l
        for l in xl_tb.ledgers + xl_tb.student_ledgers + xl_tb.system_ledgers
    }
    common = set(xml_by_key) & set(xl_by_key)

    flips: list[tuple] = []
    for k in common:
        xl = xl_by_key[k]
        xm = xml_by_key[k]
        xl_side = (
            "Dr" if xl.opening_dr > xl.opening_cr
            else "Cr" if xl.opening_cr > xl.opening_dr
            else "Zero"
        )
        xm_side = (
            "Dr" if xm.opening_dr > xm.opening_cr
            else "Cr" if xm.opening_cr > xm.opening_dr
            else "Zero"
        )
        if (xl_side == "Dr" and xm_side == "Cr") or (
            xl_side == "Cr" and xm_side == "Dr"
        ):
            flips.append((k, xl_side, xm_side, xl, xm))

    assert not flips, (
        f"Cross-format side-flips found — XML and Excel parsers disagree on "
        f"Dr/Cr side for {len(flips)} ledger(s):\n"
        + "\n".join(
            f"  {(n + ('-' + t if t else ''))}: XML={xm_s}  Excel={xl_s}"
            for (n, t), xl_s, xm_s, _, _ in flips[:15]
        )
    )


def test_excel_parser_matches_xml_partition_buckets(tb, xl_tb):
    """Every common ledger must be in the same partition (main vs student vs
    system) in both parsers.  A mismatch would indicate the student-group
    detection heuristic diverges between the two formats — and since we
    pipe the XML's group map into the Excel parser, divergence here would
    be a regression."""
    xml_main = {_key(l) for l in tb.ledgers}
    xml_stu = {_key(l) for l in tb.student_ledgers}
    xml_sys = {_key(l) for l in tb.system_ledgers}
    xl_main = {_key(l) for l in xl_tb.ledgers}
    xl_stu = {_key(l) for l in xl_tb.student_ledgers}
    xl_sys = {_key(l) for l in xl_tb.system_ledgers}

    common = (xml_main | xml_stu | xml_sys) & (xl_main | xl_stu | xl_sys)

    def _bucket(k, main, stu, sys):
        if k in main:
            return "main"
        if k in stu:
            return "student"
        if k in sys:
            return "system"
        return "?"

    mismatches: list[tuple] = []
    for k in common:
        xml_b = _bucket(k, xml_main, xml_stu, xml_sys)
        xl_b = _bucket(k, xl_main, xl_stu, xl_sys)
        if xml_b != xl_b:
            mismatches.append((k, xml_b, xl_b))

    assert not mismatches, (
        f"Partition bucket mismatches across XML / Excel: {len(mismatches)}.\n"
        + "\n".join(
            f"  {(n + ('-' + t if t else ''))}: XML={xb}  Excel={lb}"
            for (n, t), xb, lb in mismatches[:15]
        )
    )


def test_excel_parser_produces_ledger_count_and_totals_in_range(xl_tb):
    """Sanity envelope on the Excel parser output.

    * Non-empty ledgers list -- parser didn't fail silently.
    * Student list has at least 100 rows (Excel shows a subset of the full
      student universe; the XML has 4000+, Excel typically shows only the
      ones with non-zero or recent activity).
    * Combined Dr/Cr totals are within 1% of each other.

    Note: unlike the XML assertion, this one is NOT skipped on sample mode,
    because Excel parsing uses the full opening-TB xlsx regardless of which
    XML fixture the companion came from.  The xlsx is small (29 KB),
    always committed, and naturally balances.
    """
    assert len(xl_tb.ledgers) > 100, (
        f"Excel main list too small ({len(xl_tb.ledgers)}) — parser likely "
        f"failed to classify leaves"
    )
    assert len(xl_tb.student_ledgers) >= 100 or _IS_SAMPLE, (
        # On sample mode the Excel parser uses the XML sample's group map,
        # so its student classification is limited to whatever students the
        # sample's XML contained.  Relax the 100-row floor in that case.
        f"Excel student list too small ({len(xl_tb.student_ledgers)}) — "
        f"expected at least 100 from the visible student rows"
    )
    combined_dr = xl_tb.total_dr
    combined_cr = xl_tb.total_cr
    grand = max(combined_dr, combined_cr)
    gap = abs(combined_dr - combined_cr)
    # On sample mode the Excel parser's hierarchy comes from the XML sample
    # which excludes many group definitions -- individual leaves may be
    # mis-classified as groups or vice versa, so the sum can drift from
    # the Excel Grand Total by a larger margin.  Relax to 10% in that case.
    tolerance_pct = 0.10 if _IS_SAMPLE else 0.01
    assert gap <= grand * tolerance_pct, (
        f"Excel main + student imbalance Rs.{gap:,.2f} > "
        f"{tolerance_pct*100:.0f}% of grand Rs.{grand:,.2f}.  "
        f"Dr={combined_dr:,.2f}  Cr={combined_cr:,.2f}."
    )
