"""Item 5 Commit 2 — Phase C bench programmatic smoke.

Exercises ``promote_supplier_decision_to_alias_rule`` end-to-end
against real supplier-tier MDs on ``TMS-CACSPU--00495``. Uses the real
``save_supplier_resolution`` whitelist to drive reviewer approvals
(mirror of Commit 1's Path A).

Probe matrix (8 probes per Phase A S-7):
    1. Refusals (account-tier / not-Approved / missing final_supplier /
       already-promoted — though already-promoted is handled by
       idempotency path, not refusal)
    2. Preview on a tier1_supplier_exact Approved MD
    3. Commit creates Supplier Alias Rule row; verify field shape
    4. Idempotency (second commit → duplicate; times_applied bumped)
    5. Constructed conflict (two Approved MDs → same cleaned pattern,
       different final_supplier)
    6. _clean() parity (stored tally_name_pattern equals
       tier1_supplier._clean(MD.tally_name))
    7. α invariant: Supplier Alias Rule rows exist in DB, but
       find_alias_rule_supplier still returns None (stub intact)
    8. Layer-2 live unchanged: run_mapper tier distribution on a
       fresh parse identical to pre-promotion baseline (strongest
       α-invariant verification, adds reset/parse/map cycle)

Run on the bench:
    bench --site erp.jewonline.in console <<'PYEOF'
    import runpy
    runpy.run_path('/home/frappe/frappe-bench/apps/rgi_migration/scripts/item5_c2_phase_c_smoke.py', run_name='__main__')
    PYEOF
"""
from __future__ import annotations

import json
import traceback

import frappe

from rgi_migration.mapper.alias_promotion import (
    compute_supplier_source_hash,
)
from rgi_migration.mapper.tier1_supplier import _clean as tier1_clean
from rgi_migration.rgi_migration.page.md_review.md_review import (
    promote_supplier_decision_to_alias_rule,
    save_supplier_resolution,
)

SESSION = "TMS-CACSPU--00495"

_results: list[tuple[str, str, str]] = []


def _record(probe: str, outcome: str, detail: str) -> None:
    _results.append((probe, outcome, detail))
    print(f"    [{outcome:<4}] {probe}: {detail}")


def _hr(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def _dump(label: str, obj) -> None:
    print(f"  {label}:")
    try:
        print("    " + json.dumps(obj, indent=2, default=str).replace("\n", "\n    "))
    except Exception:
        print(f"    {obj!r}")


# ---------------------------------------------------------------------------
# Probe selection
# ---------------------------------------------------------------------------


def select_targets() -> dict:
    """Pick MDs for all supplier-side probes."""
    supplier_ex = frappe.get_all(
        "Mapping Decision",
        filters={
            "session": SESSION,
            "tier": "tier1_supplier_exact",
            "review_action": "Pending",
        },
        fields=["name", "tally_name", "proposed_supplier"],
        order_by="creation asc",
    )
    if len(supplier_ex) < 3:
        raise RuntimeError(
            f"Need >= 3 tier1_supplier_exact Pending MDs; found {len(supplier_ex)}"
        )

    # Target 1 (happy path): Smart IT Concept — first in the pool.
    happy = supplier_ex[0]
    # Target 2 (conflict-first): another distinct supplier MD.
    conflict_first = supplier_ex[1]
    # Target 3 (conflict-second): will have tally_name mutated to match
    # conflict_first's cleaned pattern, then Approved with a DIFFERENT
    # final_supplier.
    conflict_second = supplier_ex[2]

    # Refusal candidates:
    # - account-tier MD (e.g. unmapped Pending)
    account_tier = frappe.get_all(
        "Mapping Decision",
        filters={
            "session": SESSION,
            "tier": "tier1_exact",
            "review_action": "Pending",
        },
        fields=["name", "tally_name"],
        limit=1,
    )[0]
    # - supplier-tier MD that will stay Pending (no dialog)
    pending_supplier = frappe.get_all(
        "Mapping Decision",
        filters={
            "session": SESSION,
            "tier": "tier1_supplier_fuzzy",
            "review_action": "Pending",
        },
        fields=["name", "tally_name"],
        limit=1,
    )
    pending_supplier = pending_supplier[0] if pending_supplier else None

    targets = {
        "happy": happy,
        "conflict_first": conflict_first,
        "conflict_second": conflict_second,
        "refusal_account": account_tier,
        "refusal_supplier_pending": pending_supplier,
    }
    _hr("Target pool selection")
    for role, m in targets.items():
        if m:
            print(f"  {role:<28} {m['name']} tally={m['tally_name']!r}")
        else:
            print(f"  {role:<28} (none available)")
    return targets


# ---------------------------------------------------------------------------
# Probe 1 — refusals
# ---------------------------------------------------------------------------


def probe_1(targets: dict) -> None:
    _hr("Probe 1 — non-promotable refusals")

    # 1a: account-tier refusal
    try:
        promote_supplier_decision_to_alias_rule(
            targets["refusal_account"]["name"], confirm=False,
        )
        _record("1a account-tier refusal", "FAIL", "no throw")
    except Exception as exc:
        msg = str(exc)
        if "account-target" in msg or "supplier-target" in msg:
            _record("1a account-tier refusal", "PASS",
                    f"raised: {msg[:80]}")
        else:
            _record("1a account-tier refusal", "FAIL",
                    f"wrong message: {msg[:80]}")

    # 1b: Pending (not Approved) supplier-tier refusal
    if targets["refusal_supplier_pending"]:
        try:
            promote_supplier_decision_to_alias_rule(
                targets["refusal_supplier_pending"]["name"], confirm=False,
            )
            _record("1b Pending supplier refusal", "FAIL", "no throw")
        except Exception as exc:
            msg = str(exc)
            if "review_action" in msg or "Approved" in msg:
                _record("1b Pending supplier refusal", "PASS",
                        f"raised: {msg[:80]}")
            else:
                _record("1b Pending supplier refusal", "FAIL",
                        f"wrong message: {msg[:80]}")
    else:
        _record("1b Pending supplier refusal", "SKIP",
                "no tier1_supplier_fuzzy Pending MD available")


# ---------------------------------------------------------------------------
# Probe 2/3/4/6 — happy path (preview, commit, idempotency, _clean parity)
# ---------------------------------------------------------------------------


def probe_2_3_4_6(targets: dict) -> dict:
    _hr("Probe 2/3/4/6 — happy path + _clean parity")

    md = targets["happy"]
    md_name = md["name"]

    # Approve via real save_supplier_resolution whitelist — this is the
    # real dialog-commit path, just driven programmatically.
    saved = save_supplier_resolution(
        decision_name=md_name,
        final_supplier=md["proposed_supplier"],
        reviewer_notes="Commit 2 Phase C smoke — happy path",
    )
    print(f"  approved via save_supplier_resolution: {md_name} "
          f"final_supplier={saved.get('final_supplier')!r} "
          f"tier={saved.get('tier')!r} "
          f"review_action={saved.get('review_action')!r}")

    pre_modified = frappe.db.get_value(
        "Mapping Decision", md_name, "modified",
    )

    # Probe 2: preview
    preview = promote_supplier_decision_to_alias_rule(md_name, confirm=False)
    _dump("Probe 2 preview return", preview)
    ok = True
    if preview.get("status") != "preview":
        ok = False
        _record("2 preview status", "FAIL",
                f"status={preview.get('status')!r}")
    payload = preview.get("payload") or {}
    for field in (
        "tally_name_pattern",
        "tally_match_mode",
        "erpnext_supplier",
        "applies_to_entity_types",
        "created_from",
        "source_hash",
        "session_name",
    ):
        if field not in payload:
            ok = False
            _record("2 preview payload", "FAIL", f"missing field: {field}")

    if payload.get("tally_match_mode") != "exact_ci":
        ok = False
        _record("2 preview match_mode", "FAIL",
                f"got {payload.get('tally_match_mode')!r}, expected exact_ci")

    # Probe 6 — _clean parity: the preview's tally_name_pattern must
    # equal tier1_supplier._clean(MD.tally_name).
    expected_cleaned = tier1_clean(md["tally_name"])
    if payload.get("tally_name_pattern") != expected_cleaned:
        ok = False
        _record("6 _clean parity", "FAIL",
                f"stored={payload.get('tally_name_pattern')!r} "
                f"expected_cleaned={expected_cleaned!r}")
    else:
        _record("6 _clean parity", "PASS",
                f"stored pattern {expected_cleaned!r} matches tier1_supplier._clean output")

    if ok:
        _record("2 preview shape", "PASS",
                f"pattern={payload['tally_name_pattern']!r} "
                f"supplier={payload['erpnext_supplier']!r}")

    pre_rule_count = frappe.db.count("Supplier Alias Rule")

    # Probe 3: commit
    commit = promote_supplier_decision_to_alias_rule(md_name, confirm=True)
    _dump("Probe 3 commit return", commit)
    post_rule_count = frappe.db.count("Supplier Alias Rule")
    rule_name = commit.get("rule_name")
    ok = True
    if commit.get("status") != "created":
        ok = False
        _record("3 commit status", "FAIL",
                f"status={commit.get('status')!r}")
    if post_rule_count != pre_rule_count + 1:
        ok = False
        _record("3 commit rule count delta", "FAIL",
                f"{pre_rule_count} -> {post_rule_count}")
    md_reload = frappe.get_doc("Mapping Decision", md_name)
    if md_reload.promoted_to_rule != rule_name:
        ok = False
        _record("3 commit promoted_to_rule", "FAIL",
                f"MD.promoted_to_rule={md_reload.promoted_to_rule!r}, "
                f"expected {rule_name!r}")
    post_modified = frappe.db.get_value(
        "Mapping Decision", md_name, "modified",
    )
    if post_modified != pre_modified:
        ok = False
        _record("3 commit db_set modified", "FAIL",
                f"MD.modified changed: {pre_modified} -> {post_modified}")
    rule_doc = frappe.get_doc("Supplier Alias Rule", rule_name)
    if rule_doc.created_from != "session_review":
        ok = False
        _record("3 rule.created_from", "FAIL",
                f"got {rule_doc.created_from!r}")
    if rule_doc.tally_match_mode != "exact_ci":
        ok = False
        _record("3 rule.tally_match_mode", "FAIL",
                f"got {rule_doc.tally_match_mode!r}")
    if int(rule_doc.times_applied or 0) != 0:
        ok = False
        _record("3 rule.times_applied init", "FAIL",
                f"got {rule_doc.times_applied!r}")
    if ok:
        _record("3 commit full verification", "PASS",
                f"rule={rule_name!r} match_mode=exact_ci "
                f"times_applied=0 MD.modified unchanged")

    # Probe 4: idempotency
    pre_times = int(rule_doc.times_applied or 0)
    commit2 = promote_supplier_decision_to_alias_rule(md_name, confirm=True)
    _dump("Probe 4 idempotency return", commit2)
    post_rule_count_2 = frappe.db.count("Supplier Alias Rule")
    rule_doc.reload()
    post_times = int(rule_doc.times_applied or 0)
    ok = True
    if commit2.get("status") != "duplicate":
        ok = False
        _record("4 idempotency status", "FAIL",
                f"status={commit2.get('status')!r}")
    if commit2.get("rule_name") != rule_name:
        ok = False
        _record("4 idempotency rule_name", "FAIL",
                f"got {commit2.get('rule_name')!r}")
    if post_rule_count_2 != post_rule_count:
        ok = False
        _record("4 idempotency count stable", "FAIL",
                f"{post_rule_count} -> {post_rule_count_2}")
    if post_times != pre_times + 1:
        ok = False
        _record("4 idempotency times_applied bump", "FAIL",
                f"{pre_times} -> {post_times}")
    if ok:
        _record("4 idempotency detect-and-skip", "PASS",
                f"times_applied {pre_times} -> {post_times}, no duplicate row")

    return {"md": md_name, "rule_name": rule_name}


# ---------------------------------------------------------------------------
# Probe 5 — constructed conflict
# ---------------------------------------------------------------------------


def probe_5(targets: dict) -> dict:
    _hr("Probe 5 — constructed conflict (same pattern → different Supplier)")

    first = targets["conflict_first"]
    second = targets["conflict_second"]

    original_tally_second = second["tally_name"]
    first_cleaned = tier1_clean(first["tally_name"])

    # Mutate second's tally_name so _clean output equals first's.
    # Appending a deterministic party-ID suffix mimics the real Tally
    # shape; _clean will strip it and produce identity with first's
    # cleaned pattern.
    mutated_tally = f"{first_cleaned}-VX0999"
    assert tier1_clean(mutated_tally) == first_cleaned, (
        "Mutation construction bug: cleaned forms must match"
    )
    frappe.db.set_value(
        "Mapping Decision", second["name"], "tally_name", mutated_tally,
    )
    frappe.db.commit()
    print(f"  first={first['name']} tally={first['tally_name']!r} "
          f"cleaned={first_cleaned!r}")
    print(f"  second={second['name']} tally ORIGINAL={original_tally_second!r} "
          f"MUTATED={mutated_tally!r}")

    # Approve first normally.
    save_supplier_resolution(
        decision_name=first["name"],
        final_supplier=first["proposed_supplier"],
        reviewer_notes="Commit 2 smoke — conflict first",
    )
    # Approve second with a DIFFERENT final_supplier (pick any valid
    # Supplier that isn't first's target).
    valid_suppliers = frappe.get_all(
        "Supplier",
        filters={"disabled": 0, "name": ["!=", first["proposed_supplier"]]},
        fields=["name"],
        limit=1,
    )
    if not valid_suppliers:
        _record("5 conflict setup", "FAIL",
                "no alternate Supplier available on bench")
        return {"first_rule": None, "second_md": second["name"],
                "original_tally": original_tally_second}
    alt_supplier = valid_suppliers[0]["name"]
    save_supplier_resolution(
        decision_name=second["name"],
        final_supplier=alt_supplier,
        reviewer_notes="Commit 2 smoke — conflict second (alt target)",
    )

    # Promote first (expect clean created).
    first_commit = promote_supplier_decision_to_alias_rule(
        first["name"], confirm=True,
    )
    _dump("Probe 5 first commit", first_commit)
    if first_commit.get("status") != "created":
        _record("5 first commit", "FAIL",
                f"status={first_commit.get('status')!r}")
        return {"first_rule": None, "second_md": second["name"],
                "original_tally": original_tally_second}
    first_rule_name = first_commit["rule_name"]
    pre_rule_count = frappe.db.count("Supplier Alias Rule")

    # Preview second — expect conflict.
    preview = promote_supplier_decision_to_alias_rule(
        second["name"], confirm=False,
    )
    _dump("Probe 5 second preview", preview)
    ok = True
    if preview.get("conflict") is None:
        ok = False
        _record("5 conflict detection (preview)", "FAIL",
                "no conflict in preview output")
    else:
        conflict = preview["conflict"]
        if conflict.get("rule_name") != first_rule_name:
            ok = False
            _record("5 conflict.rule_name", "FAIL",
                    f"got {conflict.get('rule_name')!r}")
        # S-5 required the 5-field conflict surface.
        for field in (
            "rule_name",
            "existing_erpnext_supplier",
            "existing_entity_abbr",
            "existing_session",
            "existing_created_at",
        ):
            if field not in conflict:
                ok = False
                _record("5 conflict payload", "FAIL",
                        f"missing field: {field}")
        if conflict.get("existing_erpnext_supplier") != first["proposed_supplier"]:
            ok = False
            _record("5 conflict.existing_erpnext_supplier", "FAIL",
                    f"got {conflict.get('existing_erpnext_supplier')!r}")

    # Commit second — expect refusal (no write).
    commit = promote_supplier_decision_to_alias_rule(
        second["name"], confirm=True,
    )
    _dump("Probe 5 second commit", commit)
    if commit.get("status") != "conflict":
        ok = False
        _record("5 conflict commit refusal", "FAIL",
                f"status={commit.get('status')!r}")
    post_rule_count = frappe.db.count("Supplier Alias Rule")
    if post_rule_count != pre_rule_count:
        ok = False
        _record("5 conflict no-write", "FAIL",
                f"count {pre_rule_count} -> {post_rule_count}")
    second_md_doc = frappe.get_doc("Mapping Decision", second["name"])
    if second_md_doc.promoted_to_rule:
        ok = False
        _record("5 second MD.promoted_to_rule stays null", "FAIL",
                f"got {second_md_doc.promoted_to_rule!r}")
    if ok:
        _record("5 conflict detection + refusal", "PASS",
                f"existing_rule={first_rule_name!r} 5-field surface "
                f"verified, no-write, MD stays un-promoted")

    return {
        "first_rule": first_rule_name,
        "second_md": second["name"],
        "original_tally": original_tally_second,
    }


# ---------------------------------------------------------------------------
# Probe 7 — α invariant: find_alias_rule_supplier stub still returns None
# ---------------------------------------------------------------------------


def probe_7() -> None:
    _hr("Probe 7 — α invariant (find_alias_rule_supplier stub intact)")

    from rgi_migration.mapper.supplier_source import InMemorySupplierSource
    from rgi_migration.mapper.tier1_supplier import find_alias_rule_supplier

    alias_rules = frappe.get_all(
        "Supplier Alias Rule",
        filters={"created_from": "session_review"},
        fields=["name", "tally_name_pattern", "erpnext_supplier"],
    )
    print(f"  session_review Supplier Alias Rule rows: {len(alias_rules)}")
    for r in alias_rules:
        print(f"    {r['name']} pattern={r['tally_name_pattern']!r} "
              f"supplier={r['erpnext_supplier']!r}")

    # Call the stub with a pattern that SHOULD match if the stub were
    # replaced by a DocType read. It must still return None.
    if alias_rules:
        probe_pattern = alias_rules[0]["tally_name_pattern"]
    else:
        probe_pattern = "GAJANAN FOODS AND HOSPITALITY"
    result = find_alias_rule_supplier(probe_pattern, InMemorySupplierSource([]))
    if result is None:
        _record("7 α invariant stub intact", "PASS",
                f"find_alias_rule_supplier({probe_pattern!r}) -> None "
                f"(as expected; Item 8 will replace)")
    else:
        _record("7 α invariant stub intact", "FAIL",
                f"unexpected return: {result!r}")


# ---------------------------------------------------------------------------
# Probe 8 — Layer-2 live end-to-end unchanged
# ---------------------------------------------------------------------------


def probe_8() -> None:
    _hr("Probe 8 — Layer-2 live end-to-end (run_mapper tier distribution)")

    # Capture pre-reset supplier Alias Rule rows — they survive
    # reset_parse per architectural decision (Rules persist beyond
    # session lifecycle).
    pre_alias_count = frappe.db.count("Supplier Alias Rule")
    print(f"  Supplier Alias Rule rows pre-reset: {pre_alias_count}")

    from rgi_migration.rgi_migration.doctype.tally_migration_session.tally_migration_session import (
        reset_parse,
        run_mapper,
    )

    # Reset + remap.
    print("  Resetting parse and re-running mapper...")
    reset_result = reset_parse(SESSION)
    print(f"    reset_parse returned: {reset_result}")
    mapper_result = run_mapper(SESSION)
    print(f"    run_mapper returned keys: {sorted((mapper_result or {}).keys())}")

    # Capture post-run tier distribution.
    post_counts = frappe.db.sql(
        """select tier, count(*) as n
           from `tabMapping Decision`
           where session=%s
           group by tier order by tier""",
        (SESSION,),
        as_dict=True,
    )
    print(f"  post-run tier distribution ({len(post_counts)} tiers):")
    for r in post_counts:
        print(f"    tier={r['tier']!r:<32} n={r['n']}")

    # Baseline from Item 4 Commit 4 closing smoke (reset + baseline):
    expected_baseline = {
        "excluded_pnl": 325,
        "excluded_zero_balance": 1550,
        "pending_supplier_creation": 12,
        "tier1_exact": 20,
        "tier1_rule": 6,
        "tier1_supplier_exact": 3,
        "tier1_supplier_fuzzy": 3,
        "unmapped": 20,
    }
    actual = {r["tier"]: r["n"] for r in post_counts}

    drift = []
    for tier, expected_n in expected_baseline.items():
        actual_n = actual.get(tier, 0)
        if actual_n != expected_n:
            drift.append((tier, expected_n, actual_n))
    extras = set(actual) - set(expected_baseline)
    if extras:
        drift.extend((t, 0, actual[t]) for t in extras)

    post_alias_count = frappe.db.count("Supplier Alias Rule")
    if post_alias_count != pre_alias_count:
        _record("8 alias rows survive reset", "FAIL",
                f"{pre_alias_count} -> {post_alias_count}")
    else:
        _record("8 alias rows survive reset", "PASS",
                f"Supplier Alias Rule rows persisted through "
                f"reset_parse: {pre_alias_count}")

    if drift:
        _record("8 α invariant tier distribution", "FAIL",
                f"drift: {drift}")
    else:
        _record("8 α invariant tier distribution", "PASS",
                f"tier distribution matches baseline byte-for-byte "
                f"despite {post_alias_count} alias rules present")


# ---------------------------------------------------------------------------
# Cleanup — restore mutated Probe 5 second target
# ---------------------------------------------------------------------------


def cleanup(conflict_ctx: dict) -> None:
    _hr("Cleanup")
    if not conflict_ctx:
        return
    md_name = conflict_ctx.get("second_md")
    original = conflict_ctx.get("original_tally")
    # Note: post Probe 8's reset_parse, the session's MDs have been
    # deleted and re-created by run_mapper. The original tally_name
    # of the constructed-conflict second MD is already back to its
    # mapper-generated value (the reset obliterated the mutation
    # along with the row). Document in the transcript rather than
    # re-mutating.
    if md_name:
        current = frappe.db.get_value("Mapping Decision", md_name, "tally_name")
        print(f"  Probe 5 mutation trace: MD {md_name} "
              f"(or its replacement after reset) now tally_name={current!r}")
        print(f"  (Original pre-mutation value was {original!r}; "
              f"reset_parse during Probe 8 recreates MDs from fresh parse, "
              f"so the explicit restore is moot.)")

    survivors = frappe.get_all(
        "Supplier Alias Rule",
        filters={"created_from": "session_review"},
        fields=["name", "tally_name_pattern", "erpnext_supplier"],
    )
    print(f"  session_review Supplier Alias Rule rows left: {len(survivors)}")
    for r in survivors:
        print(f"    {r['name']} pattern={r['tally_name_pattern']!r} "
              f"supplier={r['erpnext_supplier']!r}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    print("\n\n########## ITEM 5 COMMIT 2 — PHASE C SMOKE ##########\n")
    targets = select_targets()
    conflict_ctx = None
    try:
        probe_1(targets)
        probe_2_3_4_6(targets)
        conflict_ctx = probe_5(targets)
        probe_7()
        probe_8()
    except Exception:
        print("\n!!! UNCAUGHT EXCEPTION IN PROBE RUN !!!")
        traceback.print_exc()
        _record("probe run", "FAIL", "uncaught exception — see trace")
    finally:
        cleanup(conflict_ctx)

    _hr("=== PHASE C SMOKE MATRIX (Commit 2) ===")
    counts = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    for probe, outcome, detail in _results:
        counts[outcome] = counts.get(outcome, 0) + 1
        print(f"  [{outcome:<4}] {probe:<48} {detail[:100]}")
    print()
    print(f"  totals: PASS={counts['PASS']} FAIL={counts['FAIL']} "
          f"SKIP={counts['SKIP']}")


if __name__ == "__main__":
    main()
