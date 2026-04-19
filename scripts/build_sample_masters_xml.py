"""Build a size-reduced copy of the full CACSPU All Masters XML for git.

Rationale (see fixtures README): the real Tally export is ~221 MB and too big
to commit.  We need a smaller fixture that still exercises every assertion in
``test_xml_vs_excel_reference.py``.  Pure "first N LEDGERs in document order"
produces a sample that's mostly student accounts and would let the side-flip
regression test pass trivially (none of the known-flipped ledgers would be
in the subset).

Strategy implemented here:

* Keep every ``<TALLYMESSAGE>`` that wraps a ``<GROUP>`` (all 403).
* Keep every ``<TALLYMESSAGE>`` that wraps a ``<CURRENCY>`` (a handful,
  needed by the envelope).
* Keep the first ``LEDGER_LIMIT`` ``<TALLYMESSAGE>``'s that wrap a
  ``<LEDGER>`` in document order.
* Additionally keep any ``<LEDGER>`` whose NAME is in the ``MUST_INCLUDE``
  allow-list (diagnostic ledgers the regression test depends on).
* Drop everything else (STOCKITEM, UNIT, COSTCENTRE, VOUCHERTYPE,
  GODOWN, etc.) -- irrelevant to the accounting parser.

Output is UTF-8 XML, parseable by ``lxml.etree.parse`` without
``recover=True`` and by ``parse_xml()`` without any code changes.
"""
from __future__ import annotations

import sys
from pathlib import Path

from lxml import etree

SRC = Path("rgi_migration/tests/fixtures/sample_cacspu_masters.xml")
DST = Path("rgi_migration/tests/fixtures/sample_cacspu_masters_sample.xml")
LEDGER_LIMIT = 500

# Diagnostic ledgers the regression test depends on -- always included
MUST_INCLUDE: set[str] = {
    # Side-flip diagnostic quartet
    "Computer & accessories-9005",
    "Furniture & fixture-9009",
    "Building Revaluation-1373",
    "Bank of Maharashtra (Cap) - 60451303968-11079",
    # Sign-rule reference ledgers (Cr side)
    "Depreciation Fund-2125",
    "Income Expenditure A/c-2127",
    "CAFE SESSY-VC0096",
    "Anupam Silver Works-VA0243",
    # System account (no <PARENT>)
    "Profit & Loss A/c",
    # matches_neither edge cases -- contribute to residual
    "Cash Purchases-SX0001",
    "SHIROLE NEHA DATTATRAY 21A0007BCAG1039",
    # Not-in-Excel residual contributors
    "MANUJA LAWNS-VM0073",
    "SHARAD EVENTS-VS0216",
}


def main() -> int:
    if not SRC.exists():
        print(f"ERROR: source fixture missing: {SRC}", file=sys.stderr)
        return 2

    print(f"Reading {SRC} ({SRC.stat().st_size / 1024 / 1024:.1f} MB) ...")
    parser = etree.XMLParser(recover=True, huge_tree=True)
    tree = etree.parse(str(SRC), parser)
    root = tree.getroot()

    # Walk every TALLYMESSAGE; decide keep/drop based on its master child
    n_group = n_currency = n_ledger = n_must = 0
    n_dropped = 0
    kept_ledger_names: set[str] = set()
    to_drop: list = []

    for tm in list(root.iter("TALLYMESSAGE")):
        keep = False
        for ch in tm:
            tag = etree.QName(ch).localname if isinstance(ch.tag, str) else ""
            name = (ch.get("NAME") or "").strip() if hasattr(ch, "get") else ""

            if tag == "GROUP":
                keep = True
                n_group += 1
                break
            if tag == "CURRENCY":
                keep = True
                n_currency += 1
                break
            if tag == "LEDGER":
                if n_ledger < LEDGER_LIMIT:
                    keep = True
                    n_ledger += 1
                    kept_ledger_names.add(name)
                elif name in MUST_INCLUDE and name not in kept_ledger_names:
                    keep = True
                    n_must += 1
                    kept_ledger_names.add(name)
                break

        if not keep:
            to_drop.append(tm)

    for tm in to_drop:
        parent = tm.getparent()
        if parent is not None:
            parent.remove(tm)
        n_dropped += 1

    # Diagnostic: did we cover all must-include names?
    missing = MUST_INCLUDE - kept_ledger_names
    if missing:
        print(f"WARNING: must-include ledgers not found in source XML: {missing}")

    DST.parent.mkdir(parents=True, exist_ok=True)
    tree.write(
        str(DST),
        encoding="utf-8",
        xml_declaration=True,
        pretty_print=False,
    )
    size_mb = DST.stat().st_size / 1024 / 1024

    print(f"Kept TALLYMESSAGE blocks:")
    print(f"  GROUP     : {n_group}")
    print(f"  CURRENCY  : {n_currency}")
    print(f"  LEDGER    : {n_ledger} (first in document order) + {n_must} (must-include)")
    print(f"  Total     : {n_group + n_currency + n_ledger + n_must}")
    print(f"Dropped     : {n_dropped} TALLYMESSAGE blocks (stock items, units, etc.)")
    print(f"Output size : {size_mb:.2f} MB -> {DST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
