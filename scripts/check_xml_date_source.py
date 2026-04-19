"""Diagnostic: is the XML's OPENINGBALANCE = Excel Opening (1-Apr-25) or Closing (31-Mar-26)?

Answers definitively whether the Tally All Masters export was done with
"closing as opening" enabled (-&gt; XML matches Excel Closing) or as a stale
prior-year snapshot (-&gt; XML matches Excel Opening).

Runs raw against the XML --does NOT use the current parser, because the
current parser has a known sign-convention bug we're about to fix.  Instead,
we apply the proposed sign rule directly:

    OPENINGBALANCE < 0   -&gt;   Dr, amount = abs(value)
    OPENINGBALANCE > 0   -&gt;   Cr, amount = value

…and compare the resulting (Dr, Cr) pair against Excel's Opening-Dr/Cr
columns and Closing-Dr/Cr columns separately.

No code changes outside this file.  No doc writes.  Diagnostic only.
"""
from __future__ import annotations

from pathlib import Path

from lxml import etree
from openpyxl import load_workbook

XML_PATH = "rgi_migration/tests/fixtures/sample_cacspu_masters.xml"
XLSX_PATH = "rgi_migration/tests/fixtures/sample_cacspu_opening_tb.xlsx"
TOL = 1.0  # ₹1 tolerance


# ---------------------------------------------------------------------------
# 1. Parse XML --raw, with proposed sign rule applied
# ---------------------------------------------------------------------------
def parse_xml(path: str) -> dict[str, tuple[float, float]]:
    """Return {full_ledger_name: (dr, cr)} applying negative-&gt;Dr / positive-&gt;Cr rule."""
    parser = etree.XMLParser(recover=True)
    tree = etree.parse(path, parser)
    root = tree.getroot()

    out: dict[str, tuple[float, float]] = {}
    for elem in root.iter("LEDGER"):
        name = (elem.get("NAME") or "").strip()
        if not name:
            continue

        # Direct OPENINGBALANCE --use the first child element only (not the
        # same-named tag nested inside BILLALLOCATIONS.LIST).  lxml's findtext
        # is scoped to direct children, so this is safe.
        ob_text = ""
        for child in elem:
            if child.tag == "OPENINGBALANCE":
                ob_text = (child.text or "").strip()
                break

        if ob_text:
            ob = float(ob_text)
        else:
            # No direct OB -&gt; sum bill-allocation OPENINGBALANCE values
            ob = 0.0
            for be in elem.findall("BILLALLOCATIONS.LIST"):
                bv = (be.findtext("OPENINGBALANCE") or "").strip()
                if bv:
                    ob += float(bv)

        # Proposed sign rule
        if ob < 0:
            dr, cr = abs(ob), 0.0
        elif ob > 0:
            dr, cr = 0.0, ob
        else:
            dr, cr = 0.0, 0.0

        out[name] = (round(dr, 2), round(cr, 2))
    return out


# ---------------------------------------------------------------------------
# 2. Parse Excel --Opening-Dr/Cr and Closing-Dr/Cr per row
# ---------------------------------------------------------------------------
def parse_excel(path: str) -> dict[str, tuple[float, float, float, float]]:
    """Return {name: (op_dr, op_cr, cl_dr, cl_cr)}.

    Columns (confirmed by header rows 7-8):
        A=name, B=Opening Dr, C=Opening Cr, D=Closing Dr, E=Closing Cr
    Data starts at row 9; grand total row is excluded.
    """
    wb = load_workbook(path, data_only=True)
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
# 3. Classify and summarise
# ---------------------------------------------------------------------------
def main() -> None:
    xml = parse_xml(XML_PATH)
    xlsx = parse_excel(XLSX_PATH)

    common = set(xml) & set(xlsx)

    print(f"XML ledgers (with NAME)         : {len(xml)}")
    print(f"Excel rows (incl. groups/leaves): {len(xlsx)}")
    print(f"Common by full name             : {len(common)}")
    print()

    # Four top-level buckets
    m_open: list[tuple] = []
    m_close: list[tuple] = []
    m_both: list[tuple] = []
    m_neither: list[tuple] = []

    # Discriminating sub-buckets.  A ledger gives STRONG evidence only when
    # (a) Op != Cl in Excel AND (b) both Op and Cl are non-zero (so zero-zero
    # "match" for P&L accounts post-close doesn't pollute the signal).
    strong_open: list[tuple] = []   # XML = Op, and Op != Cl, and both non-zero
    strong_close: list[tuple] = []  # XML = Cl, and Op != Cl, and both non-zero
    # Weak evidence: one side of Excel is zero, the other isn't
    weak_open: list[tuple] = []
    weak_close: list[tuple] = []
    # Ambiguous: XML=0 AND Op=0 but Cl!=0 -- consistent with either
    # post-close P&L zero OR stale opening export.
    ambig_pnl: list[tuple] = []

    def strong_bucket(xdr, xcr, odr, ocr, cdr, ccr):
        op_nonzero = (odr > TOL) or (ocr > TOL)
        cl_nonzero = (cdr > TOL) or (ccr > TOL)
        return op_nonzero and cl_nonzero

    for name in sorted(common):
        xdr, xcr = xml[name]
        odr, ocr, cdr, ccr = xlsx[name]

        match_op = abs(xdr - odr) < TOL and abs(xcr - ocr) < TOL
        match_cl = abs(xdr - cdr) < TOL and abs(xcr - ccr) < TOL
        op_eq_cl = abs(odr - cdr) < TOL and abs(ocr - ccr) < TOL
        xml_zero = xdr < TOL and xcr < TOL
        op_zero = odr < TOL and ocr < TOL
        cl_zero = cdr < TOL and ccr < TOL

        row = (name, xdr, xcr, odr, ocr, cdr, ccr)

        if match_op and match_cl:
            m_both.append(row)
            continue

        if match_op:
            m_open.append(row)
        elif match_cl:
            m_close.append(row)
        else:
            m_neither.append(row)

        # Discriminating classification
        if op_eq_cl:
            continue  # non-discriminating

        if strong_bucket(xdr, xcr, odr, ocr, cdr, ccr):
            # Both Op and Cl non-zero and different -- strongest signal
            if match_op:
                strong_open.append(row)
            elif match_cl:
                strong_close.append(row)
        else:
            # One side zero -- interpret carefully
            if xml_zero and op_zero and not cl_zero:
                # P&L-style: XML=0, Op=0, Cl=value.  XML equally consistent
                # with (a) stale opening export or (b) post-year-end-close export.
                ambig_pnl.append(row)
            elif match_op:
                weak_open.append(row)
            elif match_cl:
                weak_close.append(row)

    # --- All-ledger summary ---
    print("=" * 70)
    print("ALL COMMON LEDGERS")
    print("=" * 70)
    print(f"  matches_opening:  {len(m_open):5d}")
    print(f"  matches_closing:  {len(m_close):5d}")
    print(f"  matches_both:     {len(m_both):5d}   (inconclusive --no FY25-26 txns)")
    print(f"  matches_neither:  {len(m_neither):5d}")
    print()

    # --- Refined discriminating buckets ---
    print("=" * 70)
    print("DISCRIMINATING EVIDENCE (Op != Cl in Excel)")
    print("=" * 70)
    print(f"  STRONG    (both Op and Cl non-zero):")
    print(f"    -&gt; matches_opening: {len(strong_open):5d}")
    print(f"    -&gt; matches_closing: {len(strong_close):5d}")
    print(f"  WEAK      (one side zero, XML = non-zero side):")
    print(f"    -&gt; matches_opening: {len(weak_open):5d}")
    print(f"    -&gt; matches_closing: {len(weak_close):5d}")
    print(f"  AMBIGUOUS (XML=0 AND Op=0, Cl non-zero - P&L post-close or stale):")
    print(f"    {len(ambig_pnl):5d}  (cannot distinguish the two cases)")
    print()

    def dump(bucket: list, label: str, n: int = 10) -> None:
        if not bucket:
            print(f"  [{label}] (none)\n")
            return
        print(f"  [{label}] first {min(n, len(bucket))} of {len(bucket)}:")
        print(
            f'    {"name":<48s} {"XML Dr":>13s} {"XML Cr":>13s} '
            f'{"Op Dr":>13s} {"Op Cr":>13s} {"Cl Dr":>13s} {"Cl Cr":>13s}'
        )
        for row in bucket[:n]:
            name, xdr, xcr, odr, ocr, cdr, ccr = row
            disp = name if len(name) <= 48 else name[:45] + "..."
            print(
                f"    {disp:<48s} {xdr:>13,.2f} {xcr:>13,.2f} "
                f"{odr:>13,.2f} {ocr:>13,.2f} {cdr:>13,.2f} {ccr:>13,.2f}"
            )
        print()

    print("=" * 70)
    print("STRONG SAMPLES  (both Op and Cl non-zero AND different)")
    print("=" * 70)
    dump(strong_open, "STRONG: matches_opening")
    dump(strong_close, "STRONG: matches_closing")

    print("=" * 70)
    print("WEAK SAMPLES  (one side zero, XML = non-zero side)")
    print("=" * 70)
    dump(weak_open, "WEAK: matches_opening (Cl=0, XML=Op)")
    dump(weak_close, "WEAK: matches_closing (Op=0, XML=Cl)")

    print("=" * 70)
    print("AMBIGUOUS SAMPLES  (XML=0, Op=0, Cl != 0 -- could be either)")
    print("=" * 70)
    dump(ambig_pnl, "AMBIGUOUS (likely P&L accounts post-close)", 5)

    # Dump ALL matches_neither for investigation
    print("=" * 70)
    print(f"ALL matches_neither ({len(m_neither)}) -- for investigation:")
    print("=" * 70)
    dump(m_neither, "matches_neither (full list)", n=len(m_neither))

    print("=" * 70)
    print("VERDICT")
    print("=" * 70)
    strong_total = len(strong_open) + len(strong_close)
    weak_total = len(weak_open) + len(weak_close)
    if strong_total + weak_total == 0:
        print("  INCONCLUSIVE.")
    else:
        open_pts = len(strong_open) * 3 + len(weak_open)
        close_pts = len(strong_close) * 3 + len(weak_close)
        print(f"  Weighted score (strong x3 + weak x1):")
        print(f"    opening-side: {open_pts}")
        print(f"    closing-side: {close_pts}")
        if close_pts > open_pts * 3:
            print("  --&gt; XML = CLOSING.  'Closing as opening' flag WAS enabled during export.")
            print("      XML is the correct source.  Callout #1 was wrong.")
        elif open_pts > close_pts * 3:
            print("  --&gt; XML = OPENING.  The XML is a year stale.  Re-export needed.")
            print("      Callout #1 stands.")
        else:
            print("  --&gt; MIXED.  Report to Aditya; deeper investigation needed.")
        print()
        print(f"  Note: {len(ambig_pnl)} ambiguous P&L-style ledgers excluded from vote --")
        print(f"  XML=0 for them is consistent with either interpretation.")


if __name__ == "__main__":
    main()
