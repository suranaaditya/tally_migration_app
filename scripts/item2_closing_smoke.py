"""Item 2 closing smoke test — validates the pivot mechanism end-to-end.

Run from bench:
    cd ~/frappe-bench/sites && \
        ~/frappe-bench/env/bin/python ~/frappe-bench/apps/rgi_migration/scripts/item2_closing_smoke.py

Scope (Option 2 / Path C per Aditya 2026-04-22 authorisation):
    Validates Run Mapper persistence, status guards, loader preference
    logic (final_* beats proposed_*), and generator codepaths responding
    to persisted state changes.

DOES NOT validate:
    - Full generator output artefacts (JE/CSV/JE) — requires tier
      auto-lift logic from Items 3/4.
    - SCR/ACR approval workflows (Items 3/4).
    - ERPNext artefact submission (Item 9).
    - Temporary Opening netting to zero (Item 9).

Seed rationale:
    3 account seeds on previously-unmapped decisions chosen for
    diverse parent_chains (Bank, Fixed Asset, Inventory) — validates
    the pivot isn't biased by ledger shape. All point at
    "Temporary Opening - CACSPU" since Option 2 does not validate
    the downstream JE account choice; only that final_account is
    persisted and reaches the loader.

    3 supplier seeds on previously-pending_supplier_creation decisions,
    mapped to real Suppliers on the dev bench (Nilesh Traders, Gulab
    Hardware, Abhi Tria — all enabled, seen on the bench Supplier
    master). Validates final_supplier persistence + loader preference.

Idempotent: run multiple times produces identical output. Clears any
prior final_* state on the seed rows before seeding fresh values.
"""
from __future__ import annotations

import json
import time

import frappe

from rgi_migration.generators.advance_je import (
    AdvanceJEGenerationError,
    generate_advance_je,
)
from rgi_migration.generators.oit_csv import OITGenerationError, generate_oit_csv
from rgi_migration.generators.opening_je import (
    MainJEGenerationError,
    generate_main_opening_je,
)
from rgi_migration.rgi_migration.doctype.tally_migration_session.tally_migration_session import (
    reset_parse,
    run_mapper,
)
from rgi_migration.session.parse_and_map import load_decisions_from_session

SESSION = "TMS-CACSPU--00495"

# Seeds keyed by (tally_name, tally_id) — stable across run_mapper cycles.
# Mapping Decision autoname (MD-YYYY-#####) increments on each reset+re-run
# so MD names drift; the (tally_name, tally_id) tuple is the stable identity
# per the parser + persistence layer.
ACCOUNT_SEEDS = [
    # (tally_name, tally_id, final_account to set)
    ("Bank of Maharashtra ( N S S Camp 60041022214)", "1466", "Temporary Opening - CACSPU"),
    ("Electrical Fitting", "9007", "Temporary Opening - CACSPU"),
    ("General Store Inventory ", "9036", "Temporary Opening - CACSPU"),
]
SUPPLIER_SEEDS = [
    # (tally_name, tally_id, final_supplier to set)
    ("Anupam Silver Works-VA0243", None, "Nilesh Traders"),
    ("CAFE SESSY-VC0096", None, "Gulab Hardware"),
    ("Classic Enterprises-VC0011", None, "Abhi Tria"),
]


def _banner(title: str) -> None:
    bar = "=" * 72
    print(f"\n{bar}\n{title}\n{bar}", flush=True)


def _tier_counts(session_name: str) -> dict[str, int]:
    rows = frappe.db.sql(
        "SELECT tier, COUNT(*) c FROM `tabMapping Decision` "
        "WHERE session = %s GROUP BY tier",
        (session_name,),
        as_dict=True,
    )
    return {r["tier"]: int(r["c"]) for r in rows}


def step_1_fresh_run_mapper() -> None:
    _banner("STEP 1 — Fresh Reset + Run Mapper")
    r0 = reset_parse(SESSION)
    print(f"reset_parse → deleted {r0['deleted_count']} rows, status now Draft")
    t0 = time.time()
    r1 = run_mapper(SESSION)
    elapsed = time.time() - t0
    print(f"run_mapper → elapsed {elapsed:.2f}s")
    print(f"decision_count = {r1['decision_count']}")
    assert r1["decision_count"] == 1939, (
        f"expected 1939 post-dedup decisions, got {r1['decision_count']}"
    )
    tiers = _tier_counts(SESSION)
    print("tier breakdown:")
    for k, v in sorted(tiers.items(), key=lambda x: -x[1]):
        print(f"  {k:32s} {v}")


def step_2_status_guard_on_draft() -> None:
    _banner("STEP 2 — Status guard rejects on Draft")
    reset_parse(SESSION)
    s = frappe.get_doc("Tally Migration Session", SESSION)
    assert s.status == "Draft", f"expected Draft, got {s.status!r}"

    for name, fn in [
        ("generate_main_opening_je", generate_main_opening_je),
        ("generate_oit_csv", generate_oit_csv),
        ("generate_advance_je", generate_advance_je),
    ]:
        try:
            fn(SESSION)
            raise AssertionError(f"{name} did NOT refuse on Draft")
        except frappe.ValidationError as e:
            msg = str(e)
            assert "Run Mapper first" in msg, f"{name} refusal missing direction: {msg[:200]}"
            print(f"  {name}: refused correctly — {msg[:100]}")


def step_3_pending_count_guard() -> None:
    _banner("STEP 3 — Pending-count guard rejects on fresh Reviewing session")
    run_mapper(SESSION)
    probes = [
        ("generate_main_opening_je", generate_main_opening_je, MainJEGenerationError, "21 ledgers unmapped"),
        ("generate_oit_csv", generate_oit_csv, OITGenerationError, "18 suppliers pending creation"),
        ("generate_advance_je", generate_advance_je, AdvanceJEGenerationError, "18 suppliers pending creation"),
    ]
    for name, fn, ExcType, expected_substr in probes:
        try:
            fn(SESSION)
            raise AssertionError(f"{name} did NOT refuse")
        except ExcType as e:
            msg = str(e)
            assert expected_substr in msg, (
                f"{name} refusal missing expected substring {expected_substr!r}: {msg[:200]}"
            )
            print(f"  {name}: refused with {expected_substr!r}")


def _resolve_md_name(tally_name: str, tally_id: str | None) -> str:
    """Look up the Mapping Decision doc name for a (tally_name, tally_id)
    identity. Fails loudly if 0 or >1 matches in the current session."""
    filters = {"session": SESSION, "tally_name": tally_name}
    if tally_id is None:
        filters["tally_id"] = ["is", "not set"]
    else:
        filters["tally_id"] = tally_id
    rows = frappe.get_all("Mapping Decision", filters=filters, fields=["name"])
    assert len(rows) == 1, (
        f"expected 1 match for (tally_name={tally_name!r}, "
        f"tally_id={tally_id!r}); got {len(rows)}"
    )
    return rows[0]["name"]


def _clear_seed_rows() -> None:
    for tn, tid, _ in ACCOUNT_SEEDS:
        mdn = _resolve_md_name(tn, tid)
        frappe.db.set_value("Mapping Decision", mdn, "final_account", None)
    for tn, tid, _ in SUPPLIER_SEEDS:
        mdn = _resolve_md_name(tn, tid)
        frappe.db.set_value("Mapping Decision", mdn, "final_supplier", None)
    frappe.db.commit()


def step_4_hand_seed() -> None:
    _banner("STEP 4 — Hand-seeded resolution (3 final_account + 3 final_supplier)")
    _clear_seed_rows()
    for tn, tid, acct in ACCOUNT_SEEDS:
        mdn = _resolve_md_name(tn, tid)
        frappe.db.set_value("Mapping Decision", mdn, "final_account", acct)
        print(f"  {mdn} ({tn!r}, tid={tid!r}): final_account ← {acct!r}")
    for tn, tid, sup in SUPPLIER_SEEDS:
        mdn = _resolve_md_name(tn, tid)
        frappe.db.set_value("Mapping Decision", mdn, "final_supplier", sup)
        print(f"  {mdn} ({tn!r}, tid={tid!r}): final_supplier ← {sup!r}")
    frappe.db.commit()


def step_5_pivot_validation() -> None:
    _banner("STEP 5 — Pivot validation (loader probe + refusal count stability)")
    decisions, _ = load_decisions_from_session(SESSION)

    print("  account seed loader probe:")
    for tn, tid, expected_final in ACCOUNT_SEEDS:
        key = (tn, tid or "")
        d = next((d for d in decisions if (d.tally_name, d.tally_id or "") == key), None)
        assert d is not None, f"loader missed (tally_name={tn!r}, tally_id={tid!r})"
        assert d.proposed_account == expected_final, (
            f"({tn!r}, {tid!r}): loader returned proposed_account="
            f"{d.proposed_account!r}, expected {expected_final!r}"
        )
        print(f"    ({tn!r}, tid={tid!r}): proposed_account={d.proposed_account!r} ✓")

    print("  supplier seed loader probe:")
    for tn, tid, expected_final in SUPPLIER_SEEDS:
        key = (tn, tid or "")
        d = next((d for d in decisions if (d.tally_name, d.tally_id or "") == key), None)
        assert d is not None, f"loader missed (tally_name={tn!r}, tally_id={tid!r})"
        assert d.proposed_supplier == expected_final, (
            f"({tn!r}, {tid!r}): loader returned proposed_supplier="
            f"{d.proposed_supplier!r}, expected {expected_final!r}"
        )
        print(f"    ({tn!r}, tid={tid!r}): proposed_supplier={d.proposed_supplier!r} ✓")

    # Refusal count change: seeding 3 final_account does NOT change tier
    # (tier remains 'unmapped' on those rows), so opening_je still refuses
    # with the SAME count. The ref count change test is actually different
    # than the handoff described once we re-read the generator logic:
    # _select_contributions refuses based on TIER. Seeded rows still have
    # tier='unmapped' / tier='pending_supplier_creation'. Refusal count
    # will NOT decrease unless tier is also lifted.
    #
    # What DOES change: the refusal aggregation still runs the loader
    # through the full pipeline. If the loader produced bad output, the
    # pipeline would crash or produce different refusal messages. So we
    # probe by confirming refusal counts are STABLE (21/18/18) — proving
    # the pivot is not breaking the refusal path, and the loader probe
    # above proves the override mechanism works.
    probes = [
        ("generate_main_opening_je", generate_main_opening_je, MainJEGenerationError, "21 ledgers unmapped"),
        ("generate_oit_csv", generate_oit_csv, OITGenerationError, "18 suppliers pending creation"),
        ("generate_advance_je", generate_advance_je, AdvanceJEGenerationError, "18 suppliers pending creation"),
    ]
    print("  generator refusal count after seeding (tier unchanged — counts stable):")
    for name, fn, ExcType, expected in probes:
        try:
            fn(SESSION)
            raise AssertionError(f"{name} ran to completion — guard regression!")
        except ExcType as e:
            assert expected in str(e), f"{name}: {str(e)[:200]}"
            print(f"    {name}: still refuses with {expected!r} ✓")


def step_6_override_propagation() -> None:
    _banner("STEP 6 — Override propagation (change one seed, re-probe)")
    tn, tid, seed_value = ACCOUNT_SEEDS[0]
    mdn = _resolve_md_name(tn, tid)
    other_account = "Sundry Creditors - CACSPU"
    frappe.db.set_value("Mapping Decision", mdn, "final_account", other_account)
    frappe.db.commit()

    decisions, _ = load_decisions_from_session(SESSION)
    key = (tn, tid or "")
    d = next(d for d in decisions if (d.tally_name, d.tally_id or "") == key)
    assert d.proposed_account == other_account, (
        f"override did NOT propagate: loader saw {d.proposed_account!r}, "
        f"expected {other_account!r}"
    )
    print(f"  {mdn}: final_account → {other_account!r}, loader saw {d.proposed_account!r} ✓")

    frappe.db.set_value("Mapping Decision", mdn, "final_account", seed_value)
    frappe.db.commit()
    decisions2, _ = load_decisions_from_session(SESSION)
    d2 = next(d for d in decisions2 if (d.tally_name, d.tally_id or "") == key)
    assert d2.proposed_account == seed_value, "restore failed"
    print(f"  {mdn}: restored to {seed_value!r}, loader saw {d2.proposed_account!r} ✓")


def step_7_reset_idempotency() -> None:
    _banner("STEP 7 — Reset Parse idempotency")
    # Capture tier distribution from current Reviewing state
    tiers_before = _tier_counts(SESSION)

    r = reset_parse(SESSION)
    print(f"  reset: deleted {r['deleted_count']} rows")
    assert r["deleted_count"] == 1939

    t0 = time.time()
    r2 = run_mapper(SESSION)
    elapsed = time.time() - t0
    print(f"  re-run: {elapsed:.2f}s, decisions={r2['decision_count']}")
    assert r2["decision_count"] == 1939

    tiers_after = _tier_counts(SESSION)
    assert tiers_before == tiers_after, (
        f"tier distribution drift:\n  before: {json.dumps(tiers_before, indent=2)}\n"
        f"  after:  {json.dumps(tiers_after, indent=2)}"
    )
    print(f"  tier distribution stable across reset+re-run ({len(tiers_after)} tiers)")


def main() -> None:
    print("Item 2 closing smoke test — starting")
    step_1_fresh_run_mapper()
    step_2_status_guard_on_draft()
    step_3_pending_count_guard()
    step_4_hand_seed()
    step_5_pivot_validation()
    step_6_override_propagation()
    step_7_reset_idempotency()
    print("\n" + "=" * 72)
    print("ALL 7 STEPS PASSED — Item 2 pivot validated end-to-end")
    print("=" * 72)


if __name__ == "__main__":
    frappe.init(site="erp.jewonline.in")
    frappe.connect()
    main()
