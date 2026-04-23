"""Item 6 composite matcher — audit run.

Resets + re-maps TMS-CACSPU--00495 with the composite Tier-2 pipeline
enabled. Reports per-ledger:

    - Which sub-matcher fired (or none)
    - What target was proposed (or "refused / no match")
    - Manual correctness audit (Claude Code fills in Y/N by inspecting
      the COA for the ledger name + identifier)

Run:

    bench --site erp.jewonline.in console <<PY
    import sys
    sys.path.insert(0, '/home/frappe/frappe-bench/apps/rgi_migration/scripts')
    import item6_composite_audit
    item6_composite_audit.main()
    PY
"""
from __future__ import annotations

from collections import Counter
import json
import time

import frappe

from rgi_migration.rgi_migration.doctype.tally_migration_session.tally_migration_session import (
    reset_parse, run_mapper,
)

SESSION = "TMS-CACSPU--00495"


def _hr(t):
    print()
    print("=" * 80)
    print(t)
    print("=" * 80)


def main():
    _hr(f"ITEM 6 COMPOSITE — audit run on {SESSION}")

    # Reset + re-map with composite
    r = reset_parse(SESSION)
    print(f"  reset_parse: {r}")
    t0 = time.perf_counter()
    result = run_mapper(SESSION)
    elapsed = time.perf_counter() - t0
    print(f"  run_mapper: {result.get('decision_count')} decisions in "
          f"{elapsed:.2f}s")

    by_tier = result.get("summary", {}).get("by_tier", {})
    print(f"  by_tier: {json.dumps(by_tier, indent=2, default=str)}")

    # ------------------------------------------------------------------
    # All tier2_fuzzy decisions — audit per sub-matcher
    # ------------------------------------------------------------------
    _hr("Tier-2 MATCHES — grouped by sub-matcher")
    fuzzy_rows = frappe.get_all(
        "Mapping Decision",
        filters={"session": SESSION, "tier": "tier2_fuzzy"},
        fields=["name", "tally_name", "proposed_account",
                "matched_rule", "confidence", "tally_root_type"],
        order_by="matched_rule, confidence desc, tally_name",
        limit_page_length=0,
    )
    print(f"  Total tier2_fuzzy rows: {len(fuzzy_rows)}")

    sub_groups: dict[str, list] = {}
    for r in fuzzy_rows:
        sub_groups.setdefault(r.matched_rule or "(unset)", []).append(r)

    for subtier, rows in sorted(sub_groups.items()):
        print()
        print(f"  -- {subtier} ({len(rows)} match{'es' if len(rows) != 1 else ''}) --")
        for r in rows:
            conf = f"{r.confidence:.2f}" if r.confidence else "1.00"
            print(f"    Tally:    {r.tally_name!r}")
            print(f"    Proposed: {r.proposed_account!r}")
            print(f"    Root:     {r.tally_root_type}  Confidence: {conf}")
            print()

    # ------------------------------------------------------------------
    # Tier-2 REFUSALS — surfaced as unmapped with matched_rule set
    # ------------------------------------------------------------------
    _hr("Tier-2 REFUSALS (ambiguous-tie — stayed unmapped)")
    refusals = frappe.get_all(
        "Mapping Decision",
        filters={
            "session": SESSION,
            "tier": "unmapped",
            "matched_rule": ["like", "tier2:%"],
        },
        fields=["name", "tally_name", "matched_rule",
                "excluded_reason", "tally_root_type"],
        order_by="tally_name",
        limit_page_length=0,
    )
    if not refusals:
        print("  (no tier-2 refusals — all sub-matcher outcomes were clean)")
    for r in refusals:
        print(f"    Tally: {r.tally_name!r}  [{r.matched_rule}]")
        print(f"    Reason: {r.excluded_reason}")
        print()

    # ------------------------------------------------------------------
    # Still-unmapped — nothing fired for these
    # ------------------------------------------------------------------
    _hr("STILL UNMAPPED (no sub-matcher caught them)")
    still_unmapped = frappe.get_all(
        "Mapping Decision",
        filters={
            "session": SESSION,
            "tier": "unmapped",
            "review_action": "Pending",
            "matched_rule": ["is", "not set"],
        },
        fields=["name", "tally_name", "tally_root_type",
                "opening_dr", "opening_cr"],
        order_by="tally_name",
        limit_page_length=0,
    )
    print(f"  {len(still_unmapped)} ledgers with tier=unmapped and "
          f"no matched_rule set (genuine no-signal)")
    for r in still_unmapped:
        amt = r.opening_dr if r.opening_dr else r.opening_cr
        side = "Dr" if r.opening_dr else "Cr"
        print(f"    {r.tally_name!r:<55} root={r.tally_root_type:<10} "
              f"{amt:>14.2f} {side}")

    # ------------------------------------------------------------------
    # Summary counters
    # ------------------------------------------------------------------
    _hr("Summary counters")
    sub_counts = Counter(r.matched_rule or "(unset)" for r in fuzzy_rows)
    print(f"  Sub-matcher outcome counts:")
    for k, v in sorted(sub_counts.items()):
        print(f"    {k}: {v}")
    print(f"  tier-2 refusals (unmapped with excluded_reason): {len(refusals)}")
    print(f"  still unmapped (no signal at all): {len(still_unmapped)}")
    print(f"  runtime: {elapsed:.2f}s")
