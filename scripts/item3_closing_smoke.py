"""Item 3 closing smoke test — validates Supplier workflow end-to-end.

Run from bench:
    cd ~/frappe-bench/sites && \\
        ~/frappe-bench/env/bin/python ~/frappe-bench/apps/rgi_migration/scripts/item3_closing_smoke.py

Scope (Item 3 Commit 4 per Aditya 2026-04-22 authorisation):
    Validates the three supplier resolution paths on real CACSPU bench
    data:
      - Row A: Map to existing Supplier → save_supplier_resolution
               wrapper → tier=tier1_supplier_exact, final_supplier set,
               review_action=Approved.
      - Row B: Create new via SCR → create_supplier_creation_request +
               approve_scr → SCR.status=Created, new Supplier inserted,
               decision updated to tier1_supplier_exact / Approved.
      - Row C: Reject at md-review level (no SCR created). Decision
               review_action=Rejected; tier stays pending_supplier_creation
               (mapper-authoritative). Generators silent-skip.

Prerequisites:
    - Session TMS-CACSPU--00495 exists and has a parsed Tally XML blob.
    - Bench has at least 1 enabled Supplier suitable for Row A mapping.
    - Baseline expectation: post-Run Mapper, tier breakdown includes
      pending_supplier_creation=18 on a fresh parse.

Known gap (documented finding — filed in WEEK4_DEFERRED_ITEMS.md):
    ``reset_parse`` deletes Mapping Decisions but does NOT clear the
    ``supplier_creation_requests`` child table on the session doc.
    SCRs from prior runs persist as orphans (their ``source_decisions``
    CSV tokens reference deleted MD names). Smoke works around this
    by proactively clearing the child table at step 1.

Idempotency:
    - Row A only mutates Mapping Decision fields (reverted on next
      Reset Parse — idempotent).
    - Row B creates a Supplier in ``tabSupplier``. Name is
      deterministic (``SMOKE-ITEM3-B-<tally_id>``) and the script
      pre-deletes any stale prior-run Supplier, then post-smoke
      deletes again. Idempotent across runs.
    - Row C only mutates Mapping Decision fields (reverted on next
      Reset Parse — idempotent).
    - Final step 6 re-runs reset+re-run to confirm tier distribution
      stability (no drift across cycles).
"""
from __future__ import annotations

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
from rgi_migration.rgi_migration.page.md_review.md_review import (
    approve_scr,
    create_supplier_creation_request,
    save_supplier_resolution,
)

SESSION = "TMS-CACSPU--00495"
EXPECTED_DECISIONS = 1939
# Pending-supplier-creation baseline is observed at step 1 and threaded
# through steps 4 + 5 rather than hardcoded. As Suppliers are added to
# the bench (e.g., via browser verification of approval flows), mapper
# auto-resolution drifts this count down. The smoke's purpose is to
# validate the 3-path workflow, not a specific count.
MIN_PENDING_SUPPLIER_CREATION = 3  # need at least 3 rows for A/B/C picks
SMOKE_B_PREFIX = "SMOKE-ITEM3-B-"


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


def _clear_scr_child_table(session_name: str) -> int:
    """Proactively clear the SCR child table on the session doc.

    Documents the known gap: reset_parse does NOT sweep SCRs. Smoke
    normalises baseline by clearing this before step 1.
    """
    session_doc = frappe.get_doc("Tally Migration Session", session_name)
    n = len(session_doc.supplier_creation_requests or [])
    if n:
        session_doc.set("supplier_creation_requests", [])
        session_doc.save(ignore_permissions=True)
        frappe.db.commit()
    return n


def _delete_smoke_b_supplier(tally_id: str) -> bool:
    """Pre/post-smoke cleanup for Row B's deterministic Supplier.

    Row B creates Supplier name ``SMOKE-ITEM3-B-<tally_id>``. Purged
    before approve_scr so a prior-run leftover can't DuplicateEntry;
    purged at end so re-runs don't pollute the Supplier master.
    """
    name = f"{SMOKE_B_PREFIX}{tally_id}"
    if frappe.db.exists("Supplier", name):
        frappe.delete_doc("Supplier", name, force=1, ignore_permissions=True)
        frappe.db.commit()
        return True
    return False


def _pick_existing_bench_supplier(exclude_names: set[str]) -> str:
    """Pick first enabled bench Supplier alphabetically whose name is
    not in ``exclude_names``. Fails loud if none available.
    """
    rows = frappe.get_all(
        "Supplier",
        filters={"disabled": 0},
        fields=["name"],
        order_by="name asc",
    )
    for r in rows:
        if r["name"] not in exclude_names:
            return r["name"]
    raise RuntimeError(
        "No enabled bench Supplier available for Row A mapping "
        f"(excluded: {sorted(exclude_names)!r})."
    )


def _pending_supplier_decisions(session_name: str) -> list[dict]:
    """All pending_supplier_creation rows for the session, ordered by
    tally_name ASC. Returns dicts with name / tally_name / tally_id /
    proposed_supplier.
    """
    return frappe.get_all(
        "Mapping Decision",
        filters={
            "session": session_name,
            "tier": "pending_supplier_creation",
        },
        fields=["name", "tally_name", "tally_id", "proposed_supplier"],
        order_by="tally_name asc",
    )


def _pick_row_b(pending_rows: list[dict]) -> dict:
    """First row whose tally_id produces a SMOKE-ITEM3-B-<tid> name that
    does NOT currently exist in tabSupplier. Since the name prefix is
    smoke-owned, collision is only possible with a stale prior-run
    leftover (which _delete_smoke_b_supplier will clean).
    """
    for row in pending_rows:
        if row.get("tally_id"):
            return row
    raise RuntimeError(
        "No pending_supplier_creation row has a tally_id — "
        "cannot build deterministic SMOKE-ITEM3-B- name."
    )


def _pick_row_a(
    pending_rows: list[dict], row_b: dict, row_c: dict, bench_target: str
) -> dict:
    """First pending row that is neither Row B nor Row C."""
    exclude = {row_b["name"], row_c["name"]}
    for row in pending_rows:
        if row["name"] not in exclude:
            return row
    raise RuntimeError("No eligible pending row left for Row A after Row B/C picks.")


def _pick_row_c(pending_rows: list[dict], row_b: dict) -> dict:
    """Second row that is not Row B. Picks from tail of list so Row A
    gets the head (deterministic separation).
    """
    for row in reversed(pending_rows):
        if row["name"] != row_b["name"]:
            return row
    raise RuntimeError("No eligible pending row left for Row C after Row B pick.")


def step_1_baseline() -> tuple[dict, dict, dict, str, int]:
    _banner("STEP 1 — Baseline: clear SCRs + Reset Parse + Run Mapper")
    cleared = _clear_scr_child_table(SESSION)
    print(f"  cleared {cleared} leftover SCR child row(s)")
    r0 = reset_parse(SESSION)
    print(f"  reset_parse → deleted {r0['deleted_count']} decisions")
    t0 = time.time()
    r1 = run_mapper(SESSION)
    elapsed = time.time() - t0
    print(f"  run_mapper → {elapsed:.2f}s, decision_count={r1['decision_count']}")
    assert r1["decision_count"] == EXPECTED_DECISIONS, (
        f"expected {EXPECTED_DECISIONS} decisions, got {r1['decision_count']}"
    )
    tiers = _tier_counts(SESSION)
    pending_baseline = tiers.get("pending_supplier_creation", 0)
    assert pending_baseline >= MIN_PENDING_SUPPLIER_CREATION, (
        f"need ≥{MIN_PENDING_SUPPLIER_CREATION} pending_supplier_creation "
        f"rows to run A/B/C smoke; got {pending_baseline}"
    )
    print(f"  tier[pending_supplier_creation] = {pending_baseline} "
          f"(baseline — dynamic, drifts as bench Suppliers grow)")

    pending_rows = _pending_supplier_decisions(SESSION)
    assert len(pending_rows) == pending_baseline
    row_b = _pick_row_b(pending_rows)
    row_c = _pick_row_c(pending_rows, row_b)
    bench_target = _pick_existing_bench_supplier(exclude_names=set())
    row_a = _pick_row_a(pending_rows, row_b, row_c, bench_target)

    print(f"  Row A (Map-to-existing): MD={row_a['name']!r}, "
          f"tally_name={row_a['tally_name']!r} → Supplier={bench_target!r}")
    print(f"  Row B (Create-new):      MD={row_b['name']!r}, "
          f"tally_name={row_b['tally_name']!r}, tally_id={row_b['tally_id']!r}")
    print(f"  Row C (Reject):          MD={row_c['name']!r}, "
          f"tally_name={row_c['tally_name']!r}")
    return row_a, row_b, row_c, bench_target, pending_baseline


def step_2_resolve_three_rows(
    row_a: dict, row_b: dict, row_c: dict, bench_target: str
) -> str:
    _banner("STEP 2 — Resolve 3 rows (Map / Create+Approve / Reject)")

    # --- Row A: Map to existing ---
    save_supplier_resolution(
        decision_name=row_a["name"],
        final_supplier=bench_target,
        reviewer_notes="Item 3 smoke Row A — Map to existing bench supplier",
    )
    print(f"  Row A: mapped {row_a['name']!r} → Supplier {bench_target!r}")

    # --- Row B: Create new via SCR, then approve ---
    proposed_b_name = f"{SMOKE_B_PREFIX}{row_b['tally_id']}"
    purged = _delete_smoke_b_supplier(row_b["tally_id"])
    if purged:
        print(f"  Row B: purged stale prior-run Supplier {proposed_b_name!r}")

    scr_result = create_supplier_creation_request(
        decision_name=row_b["name"],
        proposed_supplier_name=proposed_b_name,
        supplier_group="Services",
        reviewer_notes="Item 3 smoke Row B — Create new via SCR",
    )
    scr_row_name = scr_result["scr_row_name"]
    print(f"  Row B: SCR child row created: {scr_row_name!r}")

    approve_result = approve_scr(
        session_name=SESSION, scr_row_name=scr_row_name
    )
    assert approve_result["status"] == "ok", (
        f"approve_scr returned non-ok: {approve_result!r}"
    )
    created_supplier = approve_result["created_supplier"]
    assert created_supplier == proposed_b_name, (
        f"Supplier name drift: expected {proposed_b_name!r}, "
        f"got {created_supplier!r} (naming collision suffix?)"
    )
    print(f"  Row B: approve_scr → Supplier {created_supplier!r} created, "
          f"SCR.status=Created, {len(approve_result['updated_decisions'])} "
          f"decision(s) updated")

    # --- Row C: Reject at md-review level (no SCR) ---
    frappe.db.set_value(
        "Mapping Decision",
        row_c["name"],
        "review_action",
        "Rejected",
    )
    frappe.db.commit()
    print(f"  Row C: {row_c['name']!r} review_action → Rejected (no SCR)")

    return created_supplier


def step_3_verify_states(
    row_a: dict, row_b: dict, row_c: dict, bench_target: str, created_b: str
) -> None:
    _banner("STEP 3 — Verify decision states")

    a_doc = frappe.get_doc("Mapping Decision", row_a["name"])
    assert a_doc.tier == "tier1_supplier_exact", f"Row A tier={a_doc.tier!r}"
    assert a_doc.final_supplier == bench_target, (
        f"Row A final_supplier={a_doc.final_supplier!r}"
    )
    assert a_doc.review_action == "Approved", (
        f"Row A review_action={a_doc.review_action!r}"
    )
    print(f"  Row A ✓ tier=tier1_supplier_exact, final_supplier={bench_target!r}, "
          f"review_action=Approved")

    b_doc = frappe.get_doc("Mapping Decision", row_b["name"])
    assert b_doc.tier == "tier1_supplier_exact", f"Row B tier={b_doc.tier!r}"
    assert b_doc.final_supplier == created_b, (
        f"Row B final_supplier={b_doc.final_supplier!r}"
    )
    assert b_doc.review_action == "Approved", (
        f"Row B review_action={b_doc.review_action!r}"
    )
    print(f"  Row B ✓ tier=tier1_supplier_exact, final_supplier={created_b!r}, "
          f"review_action=Approved")
    assert frappe.db.exists("Supplier", created_b), (
        f"Supplier {created_b!r} not found in tabSupplier after approve_scr"
    )
    print(f"        Supplier {created_b!r} exists in tabSupplier ✓")

    c_doc = frappe.get_doc("Mapping Decision", row_c["name"])
    assert c_doc.tier == "pending_supplier_creation", (
        f"Row C tier={c_doc.tier!r} (should stay pending — mapper-authoritative)"
    )
    assert c_doc.review_action == "Rejected", (
        f"Row C review_action={c_doc.review_action!r}"
    )
    print(f"  Row C ✓ tier=pending_supplier_creation (unchanged), "
          f"review_action=Rejected")


def step_4_refusal_count(pending_baseline: int) -> None:
    _banner(f"STEP 4 — Generator refusal count drops by 3 "
            f"({pending_baseline} → {pending_baseline - 3})")
    expected_remaining = pending_baseline - 3
    probes = [
        ("generate_oit_csv", generate_oit_csv, OITGenerationError),
        ("generate_advance_je", generate_advance_je, AdvanceJEGenerationError),
    ]
    for name, fn, ExcType in probes:
        try:
            fn(SESSION)
            raise AssertionError(f"{name} did NOT refuse")
        except ExcType as e:
            msg = str(e)
            expected_sub = f"{expected_remaining} suppliers pending creation"
            assert expected_sub in msg, (
                f"{name} refusal missing {expected_sub!r}; got: {msg[:200]}"
            )
            print(f"  {name}: refuses with {expected_sub!r} ✓")

    # Main JE still refuses on account-side unmapped (unchanged from Item 2)
    try:
        generate_main_opening_je(SESSION)
        raise AssertionError("generate_main_opening_je did NOT refuse")
    except MainJEGenerationError as e:
        print(f"  generate_main_opening_je: still refuses (account-side) — "
              f"{str(e)[:80]}")


def step_5_idempotency(
    created_b: str, row_b_tally_id: str, pending_baseline: int
) -> None:
    _banner("STEP 5 — Reset Parse idempotency + post-smoke Supplier cleanup")

    _clear_scr_child_table(SESSION)
    r = reset_parse(SESSION)
    assert r["deleted_count"] == EXPECTED_DECISIONS
    print(f"  reset_parse → deleted {r['deleted_count']} decisions")

    # Delete Row B's Supplier BEFORE re-running mapper so the mapper
    # doesn't auto-resolve that tally_name against the smoke-created
    # Supplier (which would mask whether baseline is stable).
    purged = _delete_smoke_b_supplier(row_b_tally_id)
    print(f"  post-smoke cleanup: Supplier {created_b!r} "
          f"{'deleted' if purged else 'already gone'}")

    t0 = time.time()
    r2 = run_mapper(SESSION)
    elapsed = time.time() - t0
    assert r2["decision_count"] == EXPECTED_DECISIONS
    print(f"  re-run: {elapsed:.2f}s, decisions={r2['decision_count']}")

    tiers_after = _tier_counts(SESSION)
    after_pending = tiers_after.get("pending_supplier_creation", 0)
    assert after_pending == pending_baseline, (
        f"post-reset pending_supplier_creation={after_pending}, "
        f"expected baseline={pending_baseline} "
        f"(drift would mean smoke polluted the bench Supplier master)"
    )
    print(f"  tier[pending_supplier_creation] = {after_pending} ✓ "
          f"(same as baseline {pending_baseline} — no smoke-side bias)")

    # Post-smoke Supplier cleanup (Row B side-effect)
    purged = _delete_smoke_b_supplier(row_b_tally_id)
    print(f"  post-smoke cleanup: Supplier {created_b!r} "
          f"{'deleted' if purged else 'already gone'}")


def main() -> None:
    print("Item 3 closing smoke test — starting")
    row_a, row_b, row_c, bench_target, pending_baseline = step_1_baseline()
    created_b = step_2_resolve_three_rows(row_a, row_b, row_c, bench_target)
    step_3_verify_states(row_a, row_b, row_c, bench_target, created_b)
    step_4_refusal_count(pending_baseline)
    step_5_idempotency(created_b, row_b["tally_id"], pending_baseline)
    print("\n" + "=" * 72)
    print("ALL 5 STEPS PASSED — Item 3 supplier workflow validated end-to-end")
    print("=" * 72)


if __name__ == "__main__":
    frappe.init(site="erp.jewonline.in")
    frappe.connect()
    main()
