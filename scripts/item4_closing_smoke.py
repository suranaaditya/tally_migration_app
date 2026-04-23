"""Item 4 closing smoke test — validates ACR workflow end-to-end.

Run from bench:
    cd ~/frappe-bench/sites && \\
        ~/frappe-bench/env/bin/python \\
          ~/frappe-bench/apps/rgi_migration/scripts/item4_closing_smoke.py

Scope (Item 4 Commit 4 per Aditya 2026-04-23 authorisation):
    Validates the Account Creation Request workflow end-to-end on
    real CACSPU bench data. Mirrors the structure of Item 3's
    supplier smoke; exercises the 4 commits' surface:

      - Commit 1 — schema + dialog shell (new_account_* fields on
                   Mapping Decision; account_type on ACR).
      - Commit 2 — create_account_creation_request.
      - Commit 3 — approve_acr, reject_acr, bulk_approve_acrs,
                   Reset Parse sweep, opening_je silent-skip,
                   Account/Supplier delete-cascade-revert hooks.
      - Commit 4 — THIS smoke.

Eight steps:

    1. Baseline: clear SCR + ACR child tables → Reset Parse →
       Run Mapper → capture unmapped_baseline.
    2. Seed 4 rows (A Map / B ACR-approve / C ACR-reject / D
       direct-reject). Row A uses the Section 4 save path; does NOT
       contribute to refusal drop due to WEEK4_DEFERRED_ITEMS
       "Loader-time tier auto-lift when final_* is set" (still
       deferred). Annotated in step output.
    3. Bulk ACR smoke — create 3 ACRs, bulk_approve_acrs in one RPC.
    4. Cascade-revert smoke — delete Row B's Account + a smoke-
       created Supplier; verify on_trash hook reverts the source
       MDs to Pending and deletes the ACR / SCR child row; fresh
       ACR / supplier resolution on same decision succeeds.
    5. Decision state verification via load_decisions_from_session.
    6. opening_je refusal: refuses with (unmapped_baseline - 6)
       ledgers unmapped. Rows A contributes 0 (annotated), Row B
       contributes -1 (tier lift), Row C contributes -1 (silent-
       skip), Row D contributes -1, Bulk 3 contributes -3.
    7. Supplier generator regression — oit_csv / advance_je refusal
       still fires on pending_supplier_creation; guards against
       _append_error_log_block refactor regression on the SCR path.
    8. Reset Parse idempotency — clear + rerun mapper → tier counts
       match baseline. Post-smoke cleanup of any SMOKE-ITEM4-*
       Accounts / Suppliers that didn't get cleaned mid-flow.

Prerequisites:
    - Session TMS-CACSPU--00495 exists, status = Reviewing.
    - Session has ≥ 7 unmapped rows after Run Mapper (4 for
      step 2 + 3 for step 3 bulk). CACSPU currently ships with
      ~21 unmapped; plenty of headroom.
    - At least 1 enabled bench Account on the session's Company
      (for Row A's Map-to-existing target).

Idempotency:
    - Deterministic naming: SMOKE-ITEM4-<role>-<tally_id|idx>.
      Pre-delete stale at step 1; post-delete at step 8.
    - Row A / C / D touch only Mapping Decision fields; reset_parse
      at step 8 wipes those.
    - Row B + Bulk approvals create ERPNext Accounts. Named
      deterministically; pre-delete at step 1 + post-delete at
      step 8.

Parametrisation: out of scope for Item 4. SESSION hardcoded to
TMS-CACSPU--00495. A future smoke for a second entity would need
this extracted as a parameter — flag only, no implementation here.

NEVER auto-submits the session. Step 1 asserts session.status !=
"Submitted"; script aborts if violated.
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
    approve_acr,
    bulk_approve_acrs,
    create_account_creation_request,
    reject_acr,
    save_decision,
)

SESSION = "TMS-CACSPU--00495"
EXPECTED_DECISIONS = 1939
# 4 seeded rows in step 2 + 3 in bulk step 3 = 7 distinct unmapped rows
# needed. CACSPU has ~21; assert we have headroom before running.
MIN_UNMAPPED_REQUIRED = 7

# Deterministic naming prefix for smoke-created Accounts / Suppliers.
# Pre-delete stale at step 1 + post-delete at step 8 for idempotency.
SMOKE_PREFIX = "SMOKE-ITEM4"
SMOKE_ACCOUNT_B = f"{SMOKE_PREFIX}-B"          # Row B's Account bare name
SMOKE_ACCOUNT_BULK = f"{SMOKE_PREFIX}-BULK"    # Bulk approvals' bare names
SMOKE_ACCOUNT_RETRY = f"{SMOKE_PREFIX}-RETRY"  # Cascade-revert's fresh ACR
SMOKE_SUPPLIER = f"{SMOKE_PREFIX}-SUP"         # Cascade-revert's supplier


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _unmapped_count_excluding_rejected(session_name: str) -> int:
    """Count of unmapped decisions whose review_action is NOT Rejected —
    i.e., the count that opening_je's refusal message will report as
    'ledgers unmapped'. Rejected rows silent-skip and don't appear in
    the error tally (Commit 3 behavior).
    """
    n = frappe.db.count(
        "Mapping Decision",
        {
            "session": session_name,
            "tier": "unmapped",
            "review_action": ["!=", "Rejected"],
        },
    )
    return int(n)


def _clear_scr_child_table(session_name: str) -> int:
    """Pre-smoke normalization: clear the SCR child table. Belt-and-
    suspenders on top of Commit 3's reset_parse sweep — smoke's step 1
    calls reset_parse which sweeps SCRs, but a prior run could have
    left orphan rows before reset_parse was extended.
    """
    session_doc = frappe.get_doc("Tally Migration Session", session_name)
    n = len(session_doc.supplier_creation_requests or [])
    if n:
        session_doc.set("supplier_creation_requests", [])
        session_doc.save(ignore_permissions=True)
        frappe.db.commit()
    return n


def _clear_acr_child_table(session_name: str) -> int:
    """Parallel for ACR child table."""
    session_doc = frappe.get_doc("Tally Migration Session", session_name)
    n = len(session_doc.account_creation_requests or [])
    if n:
        session_doc.set("account_creation_requests", [])
        session_doc.save(ignore_permissions=True)
        frappe.db.commit()
    return n


def _delete_smoke_accounts() -> int:
    """Delete any Accounts whose bare name starts with SMOKE_PREFIX.
    ERPNext autonames append ' - <abbr>', so the stored Account name
    is e.g. 'SMOKE-ITEM4-B - CACSPU'.

    Idempotent: no-op if nothing matches. Uses the cascade-revert hook
    indirectly — any MDs still linked to these Accounts get reverted,
    which is fine (step 8 then does a full reset_parse anyway).
    """
    rows = frappe.get_all(
        "Account",
        filters={"name": ["like", f"{SMOKE_PREFIX}-%"]},
        pluck="name",
    )
    for name in rows:
        frappe.delete_doc("Account", name, force=1, ignore_permissions=True)
    if rows:
        frappe.db.commit()
    return len(rows)


def _delete_smoke_suppliers() -> int:
    """Parallel for Suppliers."""
    rows = frappe.get_all(
        "Supplier",
        filters={"name": ["like", f"{SMOKE_PREFIX}-%"]},
        pluck="name",
    )
    for name in rows:
        frappe.delete_doc("Supplier", name, force=1, ignore_permissions=True)
    if rows:
        frappe.db.commit()
    return len(rows)


def _unmapped_decisions(session_name: str) -> list[dict]:
    """All unmapped + Pending rows for the session, ordered by
    tally_name ASC. Stable order gives deterministic row picks across
    runs."""
    return frappe.get_all(
        "Mapping Decision",
        filters={
            "session": session_name,
            "tier": "unmapped",
            "review_action": "Pending",
        },
        fields=["name", "tally_name", "tally_id", "tally_root_type"],
        order_by="tally_name asc",
    )


def _pick_existing_account(company: str, root_type: str) -> str:
    """Pick a real bench Account to use as Row A's Map-to-existing
    target. Prefers leaf (is_group=0), non-disabled, matching the
    row's tally_root_type (so the picker wouldn't have filtered it out).

    Note: pick is first-alpha-sorted (deterministic), NOT semantically
    curated — if the COA has scratch / test accounts with weird names
    (e.g., '234567891234566 - Trial 34 - CACSPU' observed on CACSPU
    during Item 4 closing smoke), the pick may land on one of those.
    Smoke correctness isn't affected; it's just that Row A's
    final_account string may look odd in the output. Clean up test
    accounts before rollout (see WEEK4_DEFERRED_ITEMS.md "Pre-rollout
    bench COA hygiene audit").
    """
    rows = frappe.get_all(
        "Account",
        filters={
            "company": company,
            "is_group": 0,
            "disabled": 0,
            "root_type": root_type,
        },
        fields=["name"],
        order_by="name asc",
        limit=1,
    )
    if not rows:
        raise RuntimeError(
            f"No enabled leaf Account in {company!r} branch={root_type!r} "
            f"for Row A Map-to-existing target."
        )
    return rows[0]["name"]


def _parent_for(root_type: str) -> str:
    """Return a valid CACSPU parent account for the given root_type.
    Used for ACR creation in steps 2 / 3 / 4.
    """
    return {
        "Asset": "Bank Accounts - CACSPU",
        "Liability": "Current Liabilities - CACSPU",
    }.get(root_type, "Current Assets - CACSPU")


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def step_1_baseline() -> tuple[list[dict], int, str]:
    _banner("STEP 1 — Baseline: clear child tables + Reset Parse + Run Mapper")

    # Defensive: session must not be Submitted (never auto-submit).
    session_doc = frappe.get_doc("Tally Migration Session", SESSION)
    assert session_doc.status != "Submitted", (
        f"Refusing to run smoke: session {SESSION!r} is Submitted. "
        f"Submitted sessions are frozen; smoke would either fail or "
        f"have side effects that can't be cleaned up."
    )
    print(f"  session.status = {session_doc.status!r} (not Submitted ✓)")

    # Pre-clean any SMOKE-ITEM4-* Accounts/Suppliers from a prior run
    n_acc = _delete_smoke_accounts()
    n_sup = _delete_smoke_suppliers()
    print(f"  pre-delete: {n_acc} stale Account(s), {n_sup} stale Supplier(s)")

    # Belt-and-suspenders child-table clear before reset_parse. Commit 3's
    # reset_parse sweeps both child tables, but if a prior run wedged
    # orphans the sweep could race; clearing first is cheap + deterministic.
    scr_cleared = _clear_scr_child_table(SESSION)
    acr_cleared = _clear_acr_child_table(SESSION)
    print(f"  pre-clear: {scr_cleared} SCR row(s), {acr_cleared} ACR row(s)")

    r0 = reset_parse(SESSION)
    print(
        f"  reset_parse → deleted {r0['deleted_count']} decisions, "
        f"swept {r0.get('scr_swept', 0)} SCR / {r0.get('acr_swept', 0)} ACR row(s)"
    )

    t0 = time.time()
    r1 = run_mapper(SESSION)
    elapsed = time.time() - t0
    print(f"  run_mapper → {elapsed:.2f}s, decision_count={r1['decision_count']}")
    assert r1["decision_count"] == EXPECTED_DECISIONS, (
        f"expected {EXPECTED_DECISIONS} decisions, got {r1['decision_count']}"
    )

    tiers = _tier_counts(SESSION)
    unmapped_baseline = tiers.get("unmapped", 0)
    assert unmapped_baseline >= MIN_UNMAPPED_REQUIRED, (
        f"need ≥{MIN_UNMAPPED_REQUIRED} unmapped rows to run smoke; "
        f"got {unmapped_baseline}"
    )
    print(
        f"  tier[unmapped] = {unmapped_baseline} (baseline — dynamic; "
        f"drifts if mapper's COA-resolved rules grow)"
    )

    pending_rows = _unmapped_decisions(SESSION)
    assert len(pending_rows) == unmapped_baseline
    # Return the picker material and the company (for Row A account filter).
    company = session_doc.erpnext_company
    print(f"  session.erpnext_company = {company!r}")
    return pending_rows, unmapped_baseline, company


def step_2_seed_four_rows(
    pending_rows: list[dict], company: str
) -> tuple[dict, dict, dict, dict, str, str]:
    """Seed Row A (Map) + Row B (ACR approve) + Row C (ACR reject) +
    Row D (direct reject). Returns the picked row dicts + the resolved
    Account name for Row A + the created Account name for Row B.
    """
    _banner("STEP 2 — Seed 4 rows: Map / ACR-approve / ACR-reject / direct-reject")

    # Deterministic picks: first 4 unmapped rows by tally_name asc.
    # Row assignment order (head-to-tail) is fixed so re-runs hit the
    # same rows.
    if len(pending_rows) < 4:
        raise RuntimeError(
            f"need at least 4 unmapped rows to seed step 2; "
            f"got {len(pending_rows)}"
        )
    row_a = pending_rows[0]
    row_b = pending_rows[1]
    row_c = pending_rows[2]
    row_d = pending_rows[3]

    # --- Row A: Map to existing Account via Section 4 save_decision ---
    #
    # ANNOTATION (WEEK4_DEFERRED_ITEMS tier-auto-lift): save_decision
    # writes final_account + review_action=Approved but does NOT lift
    # tier. Row A stays tier=unmapped — opening_je STILL refuses this
    # row. Item 3's save_supplier_resolution does lift tier; this is
    # the account-side asymmetry that the "Loader-time tier auto-lift"
    # deferred item captures. Explicit choice (Phase A C4-1 option α):
    # test the path but accept the zero contribution to refusal drop.
    bench_account = _pick_existing_account(company, row_a["tally_root_type"])
    save_decision(
        decision_name=row_a["name"],
        review_action="Approved",
        final_account=bench_account,
        reviewer_notes="Item 4 smoke Row A — Map to existing bench account",
    )
    print(
        f"  Row A: {row_a['name']!r} ({row_a['tally_name']!r}) → "
        f"final_account={bench_account!r}, review_action=Approved "
        f"[tier STAYS unmapped per tier-auto-lift deferred item]"
    )

    # --- Row B: Create+Approve via ACR ---
    b_proposed = f"{SMOKE_ACCOUNT_B}"  # bare name; Frappe appends " - CACSPU"
    acr_result = create_account_creation_request(
        decision_name=row_b["name"],
        proposed_account_name=b_proposed,
        proposed_parent=_parent_for(row_b["tally_root_type"]),
        account_type=None,
        reason="Item 4 smoke Row B — ACR happy path",
        reviewer_notes="ACR created for smoke Row B",
    )
    print(f"  Row B: ACR child row created: {acr_result['acr_row_name']!r}")
    approve_result = approve_acr(
        session_name=SESSION, acr_row_name=acr_result["acr_row_name"]
    )
    assert approve_result["status"] == "ok", (
        f"Row B approve_acr returned non-ok: {approve_result!r}"
    )
    created_b = approve_result["created_account"]
    print(
        f"  Row B: approve_acr → Account {created_b!r} created, "
        f"{len(approve_result['updated_decisions'])} decision(s) updated"
    )

    # --- Row C: Reject via ACR ---
    c_acr = create_account_creation_request(
        decision_name=row_c["name"],
        proposed_account_name=f"{SMOKE_PREFIX}-C",
        proposed_parent=_parent_for(row_c["tally_root_type"]),
        account_type=None,
        reason="Item 4 smoke Row C — ACR will be rejected",
        reviewer_notes="ACR to be rejected",
    )
    reject_result = reject_acr(
        session_name=SESSION, acr_row_name=c_acr["acr_row_name"]
    )
    assert reject_result["status"] == "ok", (
        f"Row C reject_acr returned non-ok: {reject_result!r}"
    )
    print(
        f"  Row C: ACR rejected, {len(reject_result['updated_decisions'])} "
        f"decision(s) marked Rejected"
    )

    # --- Row D: direct Reject via set_value (no ACR) ---
    frappe.db.set_value(
        "Mapping Decision", row_d["name"], "review_action", "Rejected"
    )
    frappe.db.commit()
    print(f"  Row D: {row_d['name']!r} review_action → Rejected (no ACR)")

    return row_a, row_b, row_c, row_d, bench_account, created_b


def step_3_bulk_acr_smoke(
    pending_rows: list[dict], already_used: set[str]
) -> list[str]:
    """Create 3 ACRs + bulk_approve_acrs in one RPC. Returns the 3
    resolved Account names.
    """
    _banner("STEP 3 — Bulk ACR smoke: create 3 ACRs + bulk_approve_acrs")

    bulk_rows = [r for r in pending_rows if r["name"] not in already_used][:3]
    if len(bulk_rows) < 3:
        raise RuntimeError(
            f"need 3 unmapped rows for bulk smoke; "
            f"got {len(bulk_rows)} after excluding already-used"
        )

    acr_row_names: list[str] = []
    for i, row in enumerate(bulk_rows):
        proposed = f"{SMOKE_ACCOUNT_BULK}-{i + 1}"
        resp = create_account_creation_request(
            decision_name=row["name"],
            proposed_account_name=proposed,
            proposed_parent=_parent_for(row["tally_root_type"]),
            account_type=None,
            reason=f"Item 4 smoke bulk #{i + 1}",
            reviewer_notes=None,
        )
        acr_row_names.append(resp["acr_row_name"])
        print(
            f"  created ACR #{i + 1} for {row['name']!r} "
            f"→ proposed={proposed!r}, acr_row={resp['acr_row_name']!r}"
        )

    import json
    bulk_result = bulk_approve_acrs(
        session_name=SESSION, acr_row_names=json.dumps(acr_row_names)
    )
    ok = bulk_result.get("ok", [])
    failed = bulk_result.get("failed", [])
    print(f"  bulk_approve_acrs → ok={len(ok)} failed={len(failed)}")
    assert not failed, f"bulk approve had failures: {failed}"
    assert set(ok) == set(acr_row_names), (
        f"bulk ok set mismatch: ok={ok}, expected={acr_row_names}"
    )

    # Resolve the 3 created Account names from the ACR rows
    session_doc = frappe.get_doc("Tally Migration Session", SESSION)
    created_accounts: list[str] = []
    for acr_name in acr_row_names:
        for r in session_doc.account_creation_requests:
            if r.name == acr_name:
                assert r.status == "Created", (
                    f"ACR {acr_name!r} status={r.status!r} after bulk approve"
                )
                assert r.created_account, (
                    f"ACR {acr_name!r} has no created_account after approve"
                )
                created_accounts.append(r.created_account)
                break
    print(f"  created Accounts: {created_accounts}")
    return created_accounts


def step_4_cascade_revert(
    row_b: dict, created_b: str, pending_rows: list[dict],
    already_used: set[str],
) -> None:
    """Cascade-revert smoke — Account delete + Supplier delete.

    Part A: delete Row B's Account → MD reverts to Pending + tier
    restored + ACR child row gone → create fresh ACR on same decision
    → approve succeeds.

    Part B: create a smoke Supplier, link to a pending_supplier_creation
    decision → delete the Supplier → MD reverts + SCR-side nothing to
    delete (no SCR row existed; manual-pick case).
    """
    _banner("STEP 4 — Cascade-revert smoke (Account + Supplier)")

    # --- Part A: Account ---
    b_pre = frappe.get_doc("Mapping Decision", row_b["name"])
    assert b_pre.tier == "tier1_exact", f"Row B tier={b_pre.tier!r} pre-delete"
    assert b_pre.final_account == created_b, (
        f"Row B final_account={b_pre.final_account!r} pre-delete"
    )
    print(f"  PRE-DELETE: {row_b['name']!r} tier=tier1_exact final_account={created_b!r}")

    frappe.delete_doc("Account", created_b, ignore_permissions=True)
    frappe.db.commit()
    print(f"  Deleted Account {created_b!r}")

    b_post = frappe.get_doc("Mapping Decision", row_b["name"])
    # Revert heuristic: new_account_name was NULL (Row B is unmapped,
    # not pending_account_creation → mapper never set new_account_name).
    # So tier reverts to 'unmapped'.
    assert b_post.tier == "unmapped", (
        f"Row B post-delete tier={b_post.tier!r} (expected unmapped — "
        f"new_account_name was NULL)"
    )
    assert b_post.review_action == "Pending", (
        f"Row B post-delete review_action={b_post.review_action!r}"
    )
    assert b_post.final_account is None, (
        f"Row B post-delete final_account={b_post.final_account!r}"
    )
    assert b_post.reviewer_notes and "deleted from COA" in b_post.reviewer_notes, (
        f"Row B chronology note missing; reviewer_notes={b_post.reviewer_notes!r}"
    )
    print(
        f"  POST-DELETE: tier=unmapped ✓ review_action=Pending ✓ "
        f"final_account=None ✓ chronology note appended ✓"
    )

    session_doc = frappe.get_doc("Tally Migration Session", SESSION)
    b_acrs = [
        r for r in session_doc.account_creation_requests
        if row_b["name"] in (r.source_decisions or "")
    ]
    assert not b_acrs, (
        f"Row B ACR row NOT cascade-deleted; found {len(b_acrs)} remaining"
    )
    print(f"  ACR child row for {row_b['name']!r} cascade-deleted ✓")

    # --- Fresh ACR cycle on same decision ---
    retry_proposed = f"{SMOKE_ACCOUNT_RETRY}"
    retry_resp = create_account_creation_request(
        decision_name=row_b["name"],
        proposed_account_name=retry_proposed,
        proposed_parent=_parent_for(row_b["tally_root_type"]),
        account_type=None,
        reason="Item 4 smoke — cascade-revert retry",
        reviewer_notes=None,
    )
    retry_approve = approve_acr(
        session_name=SESSION, acr_row_name=retry_resp["acr_row_name"]
    )
    assert retry_approve["status"] == "ok", (
        f"fresh ACR after cascade-revert failed: {retry_approve!r}"
    )
    retry_account = retry_approve["created_account"]
    print(f"  Fresh ACR cycle on {row_b['name']!r} → Account {retry_account!r} ✓")

    # Final state after retry: tier=tier1_exact, final_account=retry_account
    b_final = frappe.get_doc("Mapping Decision", row_b["name"])
    assert b_final.tier == "tier1_exact"
    assert b_final.final_account == retry_account

    # --- Part B: Supplier ---
    smoke_sup_name = f"{SMOKE_SUPPLIER}"
    if frappe.db.exists("Supplier", smoke_sup_name):
        frappe.delete_doc(
            "Supplier", smoke_sup_name, force=1, ignore_permissions=True
        )
        frappe.db.commit()
    sup_doc = frappe.get_doc({
        "doctype": "Supplier",
        "supplier_name": smoke_sup_name,
        "supplier_group": frappe.db.get_value(
            "Supplier Group", {"is_group": 0}, "name"
        ),
        "supplier_type": "Company",
    }).insert(ignore_permissions=True)
    frappe.db.commit()
    created_sup = sup_doc.name
    print(f"  Created smoke Supplier {created_sup!r}")

    # Find a pending_supplier_creation decision + link final_supplier
    sup_rows = frappe.get_all(
        "Mapping Decision",
        filters={
            "session": SESSION,
            "tier": "pending_supplier_creation",
        },
        fields=["name", "tally_name", "final_supplier"],
        order_by="tally_name asc",
        limit=1,
    )
    if not sup_rows:
        print("  SKIP: no pending_supplier_creation decisions to link against")
        return
    sup_md = sup_rows[0]
    original_final_supplier = sup_md["final_supplier"]
    md = frappe.get_doc("Mapping Decision", sup_md["name"])
    md.final_supplier = created_sup
    md.save(ignore_permissions=True)
    frappe.db.commit()
    print(f"  Linked {sup_md['name']!r}.final_supplier → {created_sup!r}")

    frappe.delete_doc("Supplier", created_sup, ignore_permissions=True)
    frappe.db.commit()
    print(f"  Deleted Supplier {created_sup!r}")

    md_post = frappe.get_doc("Mapping Decision", sup_md["name"])
    assert md_post.final_supplier is None, (
        f"Supplier cascade-revert: final_supplier={md_post.final_supplier!r} "
        f"(expected None)"
    )
    assert md_post.reviewer_notes and "deleted from the Supplier master" in (
        md_post.reviewer_notes or ""
    ), (
        f"Supplier cascade chronology note missing; "
        f"reviewer_notes={md_post.reviewer_notes!r}"
    )
    print(
        f"  POST-DELETE: {sup_md['name']!r}.final_supplier=None ✓ "
        f"chronology note appended ✓"
    )

    # Restore original final_supplier so step-8 baseline is clean
    md_post.final_supplier = original_final_supplier
    md_post.reviewer_notes = None  # wipe smoke chronology
    md_post.save(ignore_permissions=True)
    frappe.db.commit()


def step_5_verify_states(
    row_a: dict, row_b: dict, row_c: dict, row_d: dict,
    bench_account: str,
) -> None:
    _banner("STEP 5 — Decision state verification across 4 seeded rows")

    a_doc = frappe.get_doc("Mapping Decision", row_a["name"])
    # Row A annotation: tier STAYS unmapped per deferred item.
    assert a_doc.tier == "unmapped", (
        f"Row A tier={a_doc.tier!r} — save_decision should NOT lift tier "
        f"(WEEK4_DEFERRED tier-auto-lift remains deferred)"
    )
    assert a_doc.final_account == bench_account
    assert a_doc.review_action == "Approved"
    print(
        f"  Row A ✓ tier=unmapped (as deferred), final_account={bench_account!r}, "
        f"review_action=Approved"
    )

    b_doc = frappe.get_doc("Mapping Decision", row_b["name"])
    # Row B was deleted + re-approved in step 4; tier should be tier1_exact
    # again with final_account pointing at the RETRY Account.
    assert b_doc.tier == "tier1_exact", f"Row B post-retry tier={b_doc.tier!r}"
    assert (b_doc.final_account or "").startswith(SMOKE_ACCOUNT_RETRY), (
        f"Row B post-retry final_account={b_doc.final_account!r} "
        f"(expected prefix {SMOKE_ACCOUNT_RETRY!r})"
    )
    assert b_doc.review_action == "Approved"
    print(
        f"  Row B ✓ tier=tier1_exact (after cascade-retry), "
        f"final_account={b_doc.final_account!r}, review_action=Approved"
    )

    c_doc = frappe.get_doc("Mapping Decision", row_c["name"])
    # Row C: ACR rejected → review_action=Rejected; tier stays unmapped.
    assert c_doc.tier == "unmapped", f"Row C tier={c_doc.tier!r}"
    assert c_doc.review_action == "Rejected"
    print(f"  Row C ✓ tier=unmapped (unchanged), review_action=Rejected")

    d_doc = frappe.get_doc("Mapping Decision", row_d["name"])
    assert d_doc.tier == "unmapped", f"Row D tier={d_doc.tier!r}"
    assert d_doc.review_action == "Rejected"
    print(f"  Row D ✓ tier=unmapped (unchanged), review_action=Rejected")


def step_6_opening_je_refusal(
    unmapped_baseline: int, row_a: dict
) -> None:
    """Validate opening_je refusal arithmetic.

    Row B drops -1 (tier1_exact); Row C drops -1 (silent-skip); Row D
    drops -1 (silent-skip); Bulk 3 drops -3 (tier1_exact). Row A does
    NOT drop (still tier=unmapped + review_action=Approved, which is
    NOT Rejected — it remains in opening_je's "ledgers unmapped"
    refusal bucket).

    Net: expected remaining = unmapped_baseline - 6.

    ANNOTATION — Row A's persistence in the refusal bucket is the
    direct consequence of ``save_decision`` / ``apply_decision_save``
    NOT lifting tier when final_account is set. This asymmetry vs
    ``save_supplier_resolution`` (which DOES lift tier to
    ``tier1_supplier_exact``) is tracked in
    ``docs/WEEK4_DEFERRED_ITEMS.md`` under "Loader-time tier auto-lift
    when final_* is set." Until that deferred item lands, Row A is
    expected to still refuse. When it lands, the arithmetic here must
    change from ``-6`` to ``-7`` AND the Row A invariant probe below
    must flip its expectation.
    """
    _banner(
        f"STEP 6 — opening_je refusal count "
        f"({unmapped_baseline} → {unmapped_baseline - 6})"
    )

    # --- Future-forcing Row A invariant probe ---
    # If WEEK4_DEFERRED tier-auto-lift is closed (save_decision starts
    # lifting tier), Row A will no longer be (tier=unmapped,
    # review_action=Approved). This assertion fires at that point,
    # pointing the next maintainer at the remedy.
    a_doc = frappe.get_doc("Mapping Decision", row_a["name"])
    assert a_doc.tier == "unmapped" and a_doc.review_action == "Approved", (
        f"Row A invariant broken: tier={a_doc.tier!r}, "
        f"review_action={a_doc.review_action!r}. Expected "
        f"(unmapped, Approved) per WEEK4_DEFERRED_ITEMS 'Loader-time "
        f"tier auto-lift when final_* is set' (still deferred). "
        f"If this is failing because that deferred item was closed "
        f"(save_decision now lifts tier), REMEDY: update step_6's "
        f"arithmetic from 'unmapped_baseline - 6' to "
        f"'unmapped_baseline - 7' and flip this invariant probe to "
        f"expect tier='tier1_exact'."
    )
    print(
        f"  Row A invariant ✓ tier=unmapped, review_action=Approved "
        f"(WEEK4_DEFERRED tier-auto-lift still active)"
    )

    # --- Refusal-count assertion (direct arithmetic) ---
    actual_remaining = _unmapped_count_excluding_rejected(SESSION)
    assert actual_remaining == unmapped_baseline - 6, (
        f"Refusal arithmetic mismatch: actual unmapped "
        f"(!=Rejected)={actual_remaining}, expected="
        f"{unmapped_baseline - 6} (= baseline {unmapped_baseline} "
        f"minus 6 = Row B + Row C + Row D + Bulk 3). "
        f"If the tier-auto-lift deferred item landed since this smoke "
        f"was written, update the '- 6' to '- 7' (Row A now also drops)."
    )
    print(
        f"  DB unmapped (!=Rejected) count = {actual_remaining} "
        f"(== baseline {unmapped_baseline} - 6) ✓"
    )

    # --- opening_je refusal error-message probe ---
    try:
        generate_main_opening_je(SESSION)
        raise AssertionError(
            "generate_main_opening_je did NOT refuse — expected "
            "refusals on remaining unmapped rows"
        )
    except MainJEGenerationError as e:
        msg = str(e)
        expected_sub = f"{actual_remaining} ledgers unmapped"
        assert expected_sub in msg, (
            f"opening_je refusal missing {expected_sub!r}; got: {msg[:200]}"
        )
        print(f"  opening_je: refuses with {expected_sub!r} ✓")
        # Rejected rows should not be in the count — silent-skip semantic.
        # Assertion above proves this indirectly (we filter !=Rejected
        # on the actual count and it matches the generator's count).


def step_7_supplier_generator_regression() -> None:
    """Item 3 path regression — make sure the _append_error_log_block
    refactor didn't break SCR's error_log write path. Indirect
    validation: oit_csv + advance_je still refuse with their Item 3
    Commit 3 error message format."""
    _banner("STEP 7 — Supplier generator regression (Item 3 path intact)")

    pending_sup = frappe.db.count(
        "Mapping Decision",
        {
            "session": SESSION,
            "tier": "pending_supplier_creation",
            "review_action": ["!=", "Rejected"],
        },
    )
    print(f"  tier[pending_supplier_creation] (!=Rejected) = {pending_sup}")

    probes = [
        ("generate_oit_csv", generate_oit_csv, OITGenerationError),
        ("generate_advance_je", generate_advance_je, AdvanceJEGenerationError),
    ]
    for name, fn, ExcType in probes:
        if pending_sup == 0:
            print(f"  {name}: no pending_supplier_creation rows — skipping probe")
            continue
        try:
            fn(SESSION)
            # If it didn't refuse, something surprising happened —
            # either the generator now resolves pending_supplier_creation
            # (shouldn't without ACR-style approval path) or the path
            # changed. Flag.
            print(
                f"  {name}: did NOT refuse — surprising. "
                f"pending_supplier_creation={pending_sup}, "
                f"either resolved unexpectedly or behavior changed."
            )
        except ExcType as e:
            msg = str(e)
            expected_sub = f"{pending_sup} suppliers pending creation"
            assert expected_sub in msg, (
                f"{name} refusal missing {expected_sub!r}; got: {msg[:200]}"
            )
            print(f"  {name}: refuses with {expected_sub!r} ✓")


def step_8_idempotency(unmapped_baseline: int) -> None:
    """Reset Parse + Run Mapper → tier counts match baseline. Post-smoke
    cleanup of any leftover SMOKE-ITEM4-* Accounts (should be caught
    by cascade-revert hook at delete time too — this is belt-and-
    suspenders)."""
    _banner("STEP 8 — Reset Parse idempotency + post-smoke cleanup")

    r = reset_parse(SESSION)
    print(
        f"  reset_parse → deleted {r['deleted_count']} decisions, "
        f"swept {r.get('scr_swept', 0)} SCR / {r.get('acr_swept', 0)} ACR row(s)"
    )
    assert r["deleted_count"] == EXPECTED_DECISIONS

    # Post-smoke cleanup BEFORE rerun so the mapper doesn't see any
    # smoke-created Accounts in the COA (would bias tier distribution).
    n_acc = _delete_smoke_accounts()
    n_sup = _delete_smoke_suppliers()
    print(f"  post-cleanup: deleted {n_acc} SMOKE Account(s), {n_sup} SMOKE Supplier(s)")

    t0 = time.time()
    r2 = run_mapper(SESSION)
    elapsed = time.time() - t0
    assert r2["decision_count"] == EXPECTED_DECISIONS
    print(f"  re-run: {elapsed:.2f}s, decisions={r2['decision_count']}")

    tiers_after = _tier_counts(SESSION)
    after_unmapped = tiers_after.get("unmapped", 0)
    assert after_unmapped == unmapped_baseline, (
        f"post-reset tier[unmapped]={after_unmapped}, "
        f"expected baseline={unmapped_baseline} "
        f"(drift would mean smoke polluted the bench COA)"
    )
    print(
        f"  tier[unmapped] = {after_unmapped} ✓ (same as baseline "
        f"{unmapped_baseline} — no smoke-side bias)"
    )


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


def main() -> None:
    print("Item 4 closing smoke test — starting")
    pending_rows, unmapped_baseline, company = step_1_baseline()
    row_a, row_b, row_c, row_d, bench_account, created_b = step_2_seed_four_rows(
        pending_rows, company
    )
    already_used = {row_a["name"], row_b["name"], row_c["name"], row_d["name"]}
    created_bulk = step_3_bulk_acr_smoke(pending_rows, already_used)
    step_4_cascade_revert(row_b, created_b, pending_rows, already_used)
    step_5_verify_states(row_a, row_b, row_c, row_d, bench_account)
    step_6_opening_je_refusal(unmapped_baseline, row_a)
    step_7_supplier_generator_regression()
    step_8_idempotency(unmapped_baseline)
    print("\n" + "=" * 72)
    print("ALL 8 STEPS PASSED — Item 4 ACR workflow validated end-to-end")
    print("=" * 72)


if __name__ == "__main__":
    frappe.init(site="erp.jewonline.in")
    frappe.connect()
    try:
        main()
    finally:
        frappe.destroy()
