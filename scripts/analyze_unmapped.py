"""Diagnostic: what's in the 1160 unmapped ledgers on CACSPU?

Parses the full 221 MB CACSPU Tally XML, runs the Tier-1 mapper with the
committed seed rules + live Supplier master, and categorises every ledger
whose mapper decision came back ``tier == 'unmapped'`` into four buckets:

    1. zero-balance (Dr == 0 AND Cr == 0)
    2. Sundry Creditors leakage (vendor that tier1_supplier didn't catch)
    3. Student leakage (student receivable the parser flag didn't catch)
    4. genuine unmapped (non-zero, non-leaked — candidate for new Mapping Rule)

Run on the bench:

    cd ~/frappe-bench
    echo "exec(open(
        '/home/frappe/frappe-bench/apps/rgi_migration/scripts/analyze_unmapped.py'
    ).read())" | bench --site erp.jewonline.in console

(The ``rgi_migration.scripts.analyze_unmapped`` dotted path is NOT importable
— this file lives at the repo-root ``scripts/`` directory, same pattern as
``seed_mapping_rules.py`` and ``check_*.py``. Use the exec-via-console
pattern above.)

Does not mutate any DocType. Read-only.
"""

from __future__ import annotations

import random
from collections import Counter
from pathlib import Path


def run() -> None:
    import frappe  # type: ignore[import]

    from rgi_migration.mapper.mapper import CoaAccount, Mapper
    from rgi_migration.mapper.rule_source import JsonFileRuleSource
    from rgi_migration.mapper.supplier_source import (
        InMemorySupplierSource,
        Supplier,
    )
    from rgi_migration.parsers.tally_xml_parser import parse_xml

    XML_PATH = "/home/frappe/tally-exports/cacspu_masters.xml"
    ABBR = "CACSPU"
    COMPANY = "GHR CACS Pune"
    SEED_PATH = Path(
        "/home/frappe/frappe-bench/apps/rgi_migration/docs/seed_plan.json"
    )
    ENTITY_TYPE = "college"
    SAMPLE_SEED = 42

    print("=" * 72)
    print("UNMAPPED DIAGNOSTIC — CACSPU (post-commit 66c585c)")
    print("=" * 72)
    print("Source: " + XML_PATH)
    print("Seed:   " + str(SEED_PATH))
    print("Target: " + COMPANY + " (" + ABBR + ", entity_type=" + ENTITY_TYPE + ")")
    print("")

    print("Parsing Tally XML ...")
    tb = parse_xml(XML_PATH)
    print(
        "  main ledgers:    " + "{:>6,}".format(len(tb.ledgers))
        + "  student: " + "{:>5,}".format(len(tb.student_ledgers))
        + "  groups: " + "{:>5,}".format(len(tb.groups))
    )

    print("Loading COA (company=" + COMPANY + ") ...")
    coa_rows = frappe.get_all(
        "Account",
        filters={"company": COMPANY},
        fields=["name", "parent_account", "root_type", "is_group"],
        limit_page_length=0,
    )
    coa = {
        r["name"]: CoaAccount(
            name=r["name"],
            parent_account=r["parent_account"],
            root_type=r["root_type"] or "",
            is_group=bool(r["is_group"]),
            company_abbr=ABBR,
        )
        for r in coa_rows
    }
    print("  accounts:        " + "{:>6,}".format(len(coa)))

    print("Loading suppliers (disabled=0) ...")
    supplier_rows = frappe.get_all(
        "Supplier",
        filters={"disabled": 0},
        fields=["name", "supplier_name", "supplier_group", "disabled", "country"],
        limit_page_length=0,
    )
    suppliers = [
        Supplier(
            name=r["name"],
            supplier_name=r.get("supplier_name") or r["name"],
            supplier_group=r.get("supplier_group") or "",
            disabled=bool(r.get("disabled")),
            country=r.get("country"),
        )
        for r in supplier_rows
    ]
    supplier_source = InMemorySupplierSource(suppliers)
    print("  active suppliers:" + "{:>6,}".format(len(suppliers)))

    print("Running Tier-1 mapper ...")
    rule_source = JsonFileRuleSource(SEED_PATH)
    mapper = Mapper(
        rule_source, coa, abbr=ABBR, entity_type=ENTITY_TYPE,
        supplier_source=supplier_source,
    )
    decisions = mapper.map_all(tb.ledgers)
    print("  decisions:       " + "{:>6,}".format(len(decisions)))

    ledger_idx = {(l.name, l.tally_id): l for l in tb.ledgers}
    unmapped = [d for d in decisions if d.tier == "unmapped"]
    pairs: list[tuple[object, object]] = []
    for d in unmapped:
        l = ledger_idx.get((d.tally_name, d.tally_id))
        if l is not None:
            pairs.append((d, l))

    print("")
    print("=" * 72)
    print("OVERVIEW")
    print("=" * 72)
    print("Total decisions:       " + "{:>7,}".format(len(decisions)))
    print("Unmapped decisions:    " + "{:>7,}".format(len(unmapped)))
    print("Unmapped w/ ledger:    " + "{:>7,}".format(len(pairs)))
    print("")

    total_dr = sum(l.opening_dr for _, l in pairs)
    total_cr = sum(l.opening_cr for _, l in pairs)
    net = total_cr - total_dr
    zero_pairs = [(d, l) for d, l in pairs if l.opening_dr == 0 and l.opening_cr == 0]
    nonzero_pairs = [(d, l) for d, l in pairs if l.opening_dr != 0 or l.opening_cr != 0]

    print("=" * 72)
    print("SECTION 1 — Balance totals + zero-balance fraction")
    print("=" * 72)
    print("Unmapped Total Dr:  " + "{:>20,.2f}".format(total_dr))
    print("Unmapped Total Cr:  " + "{:>20,.2f}".format(total_cr))
    print("Unmapped Net (Cr-Dr):" + "{:>19,.2f}".format(net))
    print("")
    denom = max(1, len(pairs))
    print(
        "Zero-balance:       " + "{:>6,}".format(len(zero_pairs))
        + " / " + "{:,}".format(len(pairs))
        + "  (" + "{:.1%}".format(len(zero_pairs) / denom) + ")"
    )
    print(
        "Non-zero:           " + "{:>6,}".format(len(nonzero_pairs))
        + " / " + "{:,}".format(len(pairs))
        + "  (" + "{:.1%}".format(len(nonzero_pairs) / denom) + ")"
    )
    print("")

    root_counter: Counter = Counter()
    for _, l in pairs:
        root = l.parent_chain[0] if l.parent_chain else "<no parent_chain>"
        root_counter[root] += 1

    print("=" * 72)
    print("SECTION 2 — Top 20 parent-chain roots among unmapped")
    print("=" * 72)
    for i, (root, cnt) in enumerate(root_counter.most_common(20), 1):
        print(
            "  " + "{:>2}".format(i) + ". "
            + (root or "<blank>")[:60].ljust(60)
            + " " + "{:>5,}".format(cnt)
        )
    print("")

    def _chain_contains(ledger: object, needle: str) -> bool:
        return any(needle in (p or "").lower() for p in ledger.parent_chain)

    sundry_pairs = [(d, l) for d, l in pairs if _chain_contains(l, "sundry creditors")]
    student_pairs = [
        (d, l) for d, l in pairs
        if "student" in l.name.lower() or _chain_contains(l, "student")
    ]

    print("=" * 72)
    print("SECTION 3 — Sundry Creditors leakage")
    print("(parent_chain element matches 'sundry creditors' case-insensitive)")
    print("=" * 72)
    print("Count: " + "{:,}".format(len(sundry_pairs)))
    print("")
    for _, l in sundry_pairs[:10]:
        chain = " > ".join(l.parent_chain) if l.parent_chain else "<blank>"
        print("  - " + l.name + " [tally_id=" + str(l.tally_id) + "]")
        print("      chain: " + chain)
        print(
            "      Dr=" + "{:,.2f}".format(l.opening_dr)
            + "  Cr=" + "{:,.2f}".format(l.opening_cr)
        )
    print("")

    print("=" * 72)
    print("SECTION 4 — Student leakage")
    print("(ledger.name OR parent_chain contains 'student' case-insensitive)")
    print("=" * 72)
    print("Count: " + "{:,}".format(len(student_pairs)))
    print("")
    for _, l in student_pairs[:10]:
        chain = " > ".join(l.parent_chain) if l.parent_chain else "<blank>"
        print("  - " + l.name + " [tally_id=" + str(l.tally_id) + "]")
        print("      chain: " + chain)
        print(
            "      Dr=" + "{:,.2f}".format(l.opening_dr)
            + "  Cr=" + "{:,.2f}".format(l.opening_cr)
        )
    print("")

    sundry_keys = {(d.tally_name, d.tally_id) for d, _ in sundry_pairs}
    student_keys = {(d.tally_name, d.tally_id) for d, _ in student_pairs}
    genuine_pairs = [
        (d, l) for d, l in pairs
        if (l.opening_dr != 0 or l.opening_cr != 0)
        and (d.tally_name, d.tally_id) not in sundry_keys
        and (d.tally_name, d.tally_id) not in student_keys
    ]
    gen_dr = sum(l.opening_dr for _, l in genuine_pairs)
    gen_cr = sum(l.opening_cr for _, l in genuine_pairs)

    print("=" * 72)
    print("SECTION 5 — Genuine unmapped (non-zero AND non-leaked)")
    print("=" * 72)
    print("Count:           " + "{:>7,}".format(len(genuine_pairs)))
    print("Total Dr:        " + "{:>20,.2f}".format(gen_dr))
    print("Total Cr:        " + "{:>20,.2f}".format(gen_cr))
    print("Net (Cr - Dr):   " + "{:>20,.2f}".format(gen_cr - gen_dr))
    print("")
    print("Random sample of 30 (seeded with " + str(SAMPLE_SEED) + " for reproducibility):")
    rng = random.Random(SAMPLE_SEED)
    sample_size = min(30, len(genuine_pairs))
    sample = rng.sample(genuine_pairs, sample_size)
    for _, l in sample:
        chain = " > ".join(l.parent_chain) if l.parent_chain else "<blank>"
        print("  - " + l.name + " [tally_id=" + str(l.tally_id) + "]")
        print("      chain: " + chain)
        print(
            "      Dr=" + "{:,.2f}".format(l.opening_dr)
            + "  Cr=" + "{:,.2f}".format(l.opening_cr)
        )
    print("")

    print("=" * 72)
    print("SUMMARY — four diagnostic counts")
    print("=" * 72)
    print("zero_balance_count:     " + str(len(zero_pairs)))
    print("sundry_leakage_count:   " + str(len(sundry_pairs)))
    print("student_leakage_count:  " + str(len(student_pairs)))
    print("genuine_unmapped_count: " + str(len(genuine_pairs)))
    # Note: buckets are NOT disjoint above (a zero-balance ledger can also be
    # in the student bucket, for example). `genuine_unmapped_count` is the
    # non-zero, non-leaked remainder; the other three overlap in principle.
    print("")
    print("Note: zero/sundry/student buckets may overlap (a zero-balance")
    print("      student ledger counts in both). genuine_unmapped_count is")
    print("      the disjoint remainder: non-zero AND non-sundry AND non-student.")


if __name__ == "__main__":
    run()
