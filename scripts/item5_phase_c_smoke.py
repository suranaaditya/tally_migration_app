"""Item 5 Commit 1 — Phase C bench programmatic smoke.

Exercises ``promote_decision_to_rule`` end-to-end against real MDs on
``TMS-CACSPU--00495``. Path A per Aditya Phase C authorization: drive
reviewer approvals via the real ``save_decision`` whitelist (not raw
``db_set``), then promote via the real ``promote_decision_to_rule``
whitelist.

Probe order is prescribed:
    5 (refusals, no state) → 1 (preview) → 2 (commit) → 3 (idempotency)
    → 1b (skipped, documented) → 4 (constructed conflict) → 6 (α live)
    → 7 (source_hash parity).

Run on the bench:
    bench --site erp.jewonline.in execute \\
        rgi_migration.scripts.item5_phase_c_smoke.main

Output is a structured pass/fail transcript on stdout; ends with
``=== PHASE C SMOKE MATRIX ===`` summary.
"""
from __future__ import annotations

import json
import traceback

import frappe

from rgi_migration.mapper.rule_promotion import compute_source_hash
from rgi_migration.rgi_migration.page.md_review.md_review import (
    promote_decision_to_rule,
    save_decision,
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
    """Pick MDs for all probes. Returns a dict of role -> MD name."""
    tier1_ex = frappe.get_all(
        "Mapping Decision",
        filters={
            "session": SESSION,
            "tier": "tier1_exact",
            "review_action": "Pending",
        },
        fields=["name", "tally_name", "proposed_account", "tally_root_type"],
        order_by="creation asc",
    )
    if len(tier1_ex) < 4:
        raise RuntimeError(
            f"Need >= 4 tier1_exact Pending MDs; found {len(tier1_ex)}"
        )

    # Bank account is low-stakes and the ICICI one is in the canonical pool.
    bank = next(m for m in tier1_ex if "ICICI BANK" in m["tally_name"])

    # Two distinct non-bank MDs for the conflict pair. Pick two Liability
    # entries so the root_type lines up cleanly after we mutate one's
    # tally_name.
    prof_tax = next(m for m in tier1_ex if m["tally_name"] == "Professional Tax")
    prov_fund = next(m for m in tier1_ex if m["tally_name"] == "Provident Fund")

    # A fourth tier1_exact that we leave Pending for refusal Probe 5b.
    refusal_tier1 = next(
        m for m in tier1_ex
        if m["name"] not in {bank["name"], prof_tax["name"], prov_fund["name"]}
    )

    pending_sc = frappe.get_all(
        "Mapping Decision",
        filters={"session": SESSION, "tier": "pending_supplier_creation"},
        fields=["name", "tally_name"],
        limit=1,
    )[0]
    unmapped = frappe.get_all(
        "Mapping Decision",
        filters={"session": SESSION, "tier": "unmapped", "review_action": "Pending"},
        fields=["name", "tally_name"],
        limit=1,
    )[0]

    targets = {
        "happy": bank,
        "conflict_first": prof_tax,
        "conflict_second": prov_fund,
        "refusal_pending_tier1": refusal_tier1,
        "refusal_supplier": pending_sc,
        "refusal_unmapped": unmapped,
    }
    _hr("Target pool selection")
    for role, m in targets.items():
        print(f"  {role:<24} {m['name']} tally={m['tally_name']!r}")
    return targets


# ---------------------------------------------------------------------------
# Probe 5 — refusals (no state changes on MD side)
# ---------------------------------------------------------------------------


def probe_5(targets: dict) -> None:
    _hr("Probe 5 — non-promotable refusals")

    # 5a: supplier-tier refusal
    try:
        promote_decision_to_rule(targets["refusal_supplier"]["name"], confirm=False)
        _record("5a supplier-tier refusal", "FAIL", "no throw")
    except Exception as exc:
        msg = str(exc)
        if "supplier" in msg.lower():
            _record("5a supplier-tier refusal", "PASS",
                    f"raised with supplier message: {msg[:80]}")
        else:
            _record("5a supplier-tier refusal", "FAIL",
                    f"raised wrong message: {msg[:80]}")

    # 5b: tier1_exact Pending (not Approved) refusal
    try:
        promote_decision_to_rule(targets["refusal_pending_tier1"]["name"], confirm=False)
        _record("5b pending tier1 refusal", "FAIL", "no throw")
    except Exception as exc:
        msg = str(exc)
        if "review_action" in msg or "Approved" in msg:
            _record("5b pending tier1 refusal", "PASS",
                    f"raised with review_action message: {msg[:80]}")
        else:
            _record("5b pending tier1 refusal", "FAIL",
                    f"raised wrong message: {msg[:80]}")

    # 5c: unmapped Pending refusal (same review_action guard)
    try:
        promote_decision_to_rule(targets["refusal_unmapped"]["name"], confirm=False)
        _record("5c unmapped refusal", "FAIL", "no throw")
    except Exception as exc:
        msg = str(exc)
        if "review_action" in msg or "Approved" in msg:
            _record("5c unmapped refusal", "PASS",
                    f"raised with review_action message: {msg[:80]}")
        else:
            _record("5c unmapped refusal", "FAIL",
                    f"raised wrong message: {msg[:80]}")

    # 5d: Submitted-session refusal — skipped, destructive state mutation
    # that would break the rest of Phase C. Covered at unit-test layer
    # (see test_rule_promotion tests; session-state guard is a wrapper
    # concern, not in pure-core). Documented here for audit.
    _record("5d submitted-session refusal", "SKIP",
            "skipped per plan — destructive state mutation; unit-test "
            "coverage at _validate_promotable level sufficient")


# ---------------------------------------------------------------------------
# Probe 1/2/3 — happy path preview, commit, idempotency
# ---------------------------------------------------------------------------


def probe_1_2_3(targets: dict) -> dict:
    _hr("Probe 1/2/3 — happy path (preview / commit / idempotency)")

    md = targets["happy"]
    md_name = md["name"]

    # Approve via real save_decision whitelist
    saved = save_decision(
        decision_name=md_name,
        review_action="Approved",
        final_account=md["proposed_account"],
        reviewer_notes="Phase C smoke — Probe 1/2/3 happy path",
    )
    print(f"  approved via save_decision: {md_name} "
          f"final_account={saved.get('final_account')!r} "
          f"review_action={saved.get('review_action')!r}")

    # Capture modified timestamp pre-promotion for db_set verification later.
    pre_modified = frappe.db.get_value("Mapping Decision", md_name, "modified")

    # --- Probe 1: preview ---
    preview = promote_decision_to_rule(md_name, confirm=False)
    _dump("Probe 1 preview return", preview)
    ok = True
    if preview.get("status") != "preview":
        ok = False
        _record("1 preview status", "FAIL", f"status={preview.get('status')!r}")
    payload = preview.get("payload") or {}
    for field in (
        "tally_pattern",
        "tally_match_mode",
        "applicable_root_type",
        "erpnext_account_template",
        "raw_final_account",
        "source_hash",
        "source_section",
        "applies_to_entity_types",
    ):
        if field not in payload:
            ok = False
            _record("1 preview payload", "FAIL", f"missing field: {field}")
    subst = preview.get("substitution") or {}
    if subst.get("abbr") != "CACSPU":
        ok = False
        _record("1 preview substitution.abbr", "FAIL",
                f"got {subst.get('abbr')!r}, expected 'CACSPU'")
    if subst.get("occurrences") != 1:
        ok = False
        _record("1 preview occurrences", "FAIL",
                f"got {subst.get('occurrences')!r}, expected 1")
    if subst.get("is_literal") is not False:
        ok = False
        _record("1 preview is_literal", "FAIL",
                f"got {subst.get('is_literal')!r}, expected False")
    if "{ABBR}" not in payload.get("erpnext_account_template", ""):
        ok = False
        _record("1 preview template", "FAIL",
                f"template {payload.get('erpnext_account_template')!r} "
                f"missing {{ABBR}}")
    if ok:
        _record("1 preview shape + substitution", "PASS",
                f"template={payload['erpnext_account_template']!r} "
                f"occurrences={subst['occurrences']} literal={subst['is_literal']}")

    pre_rule_count = frappe.db.count("Mapping Rule")

    # --- Probe 2: commit ---
    commit = promote_decision_to_rule(md_name, confirm=True)
    _dump("Probe 2 commit return", commit)
    post_rule_count = frappe.db.count("Mapping Rule")
    rule_name = commit.get("rule_name")
    ok = True
    if commit.get("status") != "created":
        ok = False
        _record("2 commit status", "FAIL", f"status={commit.get('status')!r}")
    if post_rule_count != pre_rule_count + 1:
        ok = False
        _record("2 commit rule count delta", "FAIL",
                f"{pre_rule_count} -> {post_rule_count}")
    md_reload = frappe.get_doc("Mapping Decision", md_name)
    if md_reload.promoted_to_rule != rule_name:
        ok = False
        _record("2 commit promoted_to_rule", "FAIL",
                f"MD.promoted_to_rule={md_reload.promoted_to_rule!r}, "
                f"expected {rule_name!r}")
    post_modified = frappe.db.get_value("Mapping Decision", md_name, "modified")
    if post_modified != pre_modified:
        ok = False
        _record("2 commit db_set modified", "FAIL",
                f"MD.modified changed: {pre_modified} -> {post_modified}")
    rule_doc = frappe.get_doc("Mapping Rule", rule_name)
    if rule_doc.created_from != "session_review":
        ok = False
        _record("2 commit rule.created_from", "FAIL",
                f"got {rule_doc.created_from!r}")
    if rule_doc.created_via_session != SESSION:
        ok = False
        _record("2 commit rule.created_via_session", "FAIL",
                f"got {rule_doc.created_via_session!r}")
    if not rule_doc.raw_final_account:
        ok = False
        _record("2 commit raw_final_account", "FAIL", "empty")
    if int(rule_doc.times_applied or 0) != 0:
        ok = False
        _record("2 commit times_applied init", "FAIL",
                f"got {rule_doc.times_applied!r}, expected 0 (schema default)")
    if ok:
        _record("2 commit full verification", "PASS",
                f"rule={rule_name!r} raw_final_account={rule_doc.raw_final_account!r} "
                f"times_applied={rule_doc.times_applied} MD.modified unchanged")

    # --- Probe 3: idempotency ---
    pre_times_applied = int(rule_doc.times_applied or 0)
    pre_entities = (rule_doc.source_entities or "")
    commit2 = promote_decision_to_rule(md_name, confirm=True)
    _dump("Probe 3 idempotency return", commit2)
    post_rule_count_2 = frappe.db.count("Mapping Rule")
    rule_doc.reload()
    post_times_applied = int(rule_doc.times_applied or 0)
    post_entities = (rule_doc.source_entities or "")
    ok = True
    if commit2.get("status") != "duplicate":
        ok = False
        _record("3 idempotency status", "FAIL", f"status={commit2.get('status')!r}")
    if commit2.get("rule_name") != rule_name:
        ok = False
        _record("3 idempotency rule_name", "FAIL",
                f"got {commit2.get('rule_name')!r}, expected {rule_name!r}")
    if post_rule_count_2 != post_rule_count:
        ok = False
        _record("3 idempotency count stable", "FAIL",
                f"{post_rule_count} -> {post_rule_count_2}")
    if post_times_applied != pre_times_applied + 1:
        ok = False
        _record("3 idempotency times_applied bump", "FAIL",
                f"{pre_times_applied} -> {post_times_applied}")
    if ok:
        _record("3 idempotency detect-and-skip", "PASS",
                f"times_applied {pre_times_applied} -> {post_times_applied}, "
                f"source_entities stable={pre_entities == post_entities}")

    return {"md": md_name, "rule_name": rule_name}


# ---------------------------------------------------------------------------
# Probe 4 — constructed conflict
# ---------------------------------------------------------------------------


def probe_4(targets: dict) -> dict:
    _hr("Probe 4 — constructed conflict (same pattern, different final_account)")

    first = targets["conflict_first"]   # Professional Tax (natural)
    second = targets["conflict_second"]  # Provident Fund → mutate to Professional Tax

    # Capture mutation inputs for transcript.
    original_tally = second["tally_name"]
    print(f"  first={first['name']} tally={first['tally_name']!r} "
          f"final={first['proposed_account']!r}")
    print(f"  second={second['name']} ORIGINAL tally={original_tally!r}")

    # Construct collision via direct db_set: tally_name mutation is
    # outside the reviewer flow (reviewers don't edit tally_name via
    # md_review). This is the fair-game manual DB probe Aditya
    # authorized for Probe 4 construction.
    frappe.db.set_value(
        "Mapping Decision",
        second["name"],
        "tally_name",
        "Professional Tax",
    )
    frappe.db.commit()
    print(f"  second={second['name']} MUTATED tally='Professional Tax' "
          f"(root_type={first['tally_root_type']!r} matches)")

    # Approve first (normal).
    save_decision(
        decision_name=first["name"],
        review_action="Approved",
        final_account=first["proposed_account"],
        reviewer_notes="Phase C smoke — Probe 4 first (normal)",
    )
    # Approve second with DIFFERENT final_account (manual override
    # picks the Provident Fund target despite the mutated tally_name).
    save_decision(
        decision_name=second["name"],
        review_action="Approved",
        final_account=second["proposed_account"],  # different target
        reviewer_notes="Phase C smoke — Probe 4 second (conflict target)",
    )

    # Promote first (expect clean created).
    first_commit = promote_decision_to_rule(first["name"], confirm=True)
    _dump("Probe 4 first commit", first_commit)
    if first_commit.get("status") != "created":
        _record("4 first commit", "FAIL", f"status={first_commit.get('status')!r}")
        return {"first_rule": None, "second_md": second["name"],
                "original_tally": original_tally}

    first_rule_name = first_commit["rule_name"]
    pre_rule_count = frappe.db.count("Mapping Rule")

    # Preview second — expect conflict.
    preview = promote_decision_to_rule(second["name"], confirm=False)
    _dump("Probe 4 second preview", preview)
    ok = True
    if preview.get("conflict") is None:
        ok = False
        _record("4 conflict detection (preview)", "FAIL",
                "no conflict in preview output")
    else:
        conflict = preview["conflict"]
        if conflict.get("rule_name") != first_rule_name:
            ok = False
            _record("4 conflict.rule_name", "FAIL",
                    f"got {conflict.get('rule_name')!r}, expected {first_rule_name!r}")
        for field in (
            "existing_template",
            "existing_raw_final_account",
            "existing_source_section",
            "existing_entity_abbr",
            "existing_session",
            "existing_created_at",
        ):
            if field not in conflict:
                ok = False
                _record("4 conflict payload", "FAIL",
                        f"missing field: {field}")
        if conflict.get("existing_session") != SESSION:
            ok = False
            _record("4 conflict.existing_session", "FAIL",
                    f"got {conflict.get('existing_session')!r}")
        if conflict.get("existing_entity_abbr") != "CACSPU":
            ok = False
            _record("4 conflict.existing_entity_abbr", "FAIL",
                    f"got {conflict.get('existing_entity_abbr')!r}")

    # Commit second — expect refusal (no row written).
    commit = promote_decision_to_rule(second["name"], confirm=True)
    _dump("Probe 4 second commit", commit)
    if commit.get("status") != "conflict":
        ok = False
        _record("4 conflict commit refusal", "FAIL",
                f"status={commit.get('status')!r}")
    post_rule_count = frappe.db.count("Mapping Rule")
    if post_rule_count != pre_rule_count:
        ok = False
        _record("4 conflict no-write", "FAIL",
                f"rule count {pre_rule_count} -> {post_rule_count}")
    second_md_doc = frappe.get_doc("Mapping Decision", second["name"])
    if second_md_doc.promoted_to_rule:
        ok = False
        _record("4 second MD.promoted_to_rule stays null", "FAIL",
                f"got {second_md_doc.promoted_to_rule!r}")
    if ok:
        _record("4 conflict detection + refusal", "PASS",
                f"existing_rule={first_rule_name!r} no-write verified, "
                f"MD.promoted_to_rule null, cross-entity context surfaced")

    return {
        "first_rule": first_rule_name,
        "second_md": second["name"],
        "original_tally": original_tally,
    }


# ---------------------------------------------------------------------------
# Probe 6 — α invariant live verification
# ---------------------------------------------------------------------------


def probe_6(ctx: dict, conflict_ctx: dict) -> None:
    _hr("Probe 6 — α invariant (promoted rules do NOT influence live mapper)")

    # Capture pre-state: mapping rule rows count (should be 2 after probes
    # 2 and 4 — the happy-path rule and the first-conflict rule).
    pre_rules = frappe.db.count("Mapping Rule")
    promoted_rules = frappe.get_all(
        "Mapping Rule",
        filters={"created_from": "session_review"},
        fields=["name", "tally_pattern", "source_section", "times_applied"],
    )
    print(f"  pre-mapper rule count total={pre_rules} "
          f"session_review={len(promoted_rules)}")
    for r in promoted_rules:
        print(f"    {r['name']} pattern={r['tally_pattern']!r} "
              f"section={r['source_section']!r} times_applied={r['times_applied']}")

    # Capture pre-mapper tier distribution on the session. Session is
    # still in Reviewing status from Phase C probes — we don't want to
    # reset_parse + run_mapper here because that's destructive. Instead
    # verify the invariant indirectly by confirming _load_rule_source
    # returns JsonFileRuleSource and the Mapping Rule DocType is not
    # consulted for live matching.
    from rgi_migration.mapper.rule_source import JsonFileRuleSource
    from rgi_migration.rgi_migration.doctype.tally_migration_session.tally_migration_session import (
        _load_rule_source,
    )

    rs = _load_rule_source()
    rs_class_name = type(rs).__name__
    if rs_class_name == "JsonFileRuleSource":
        _record("6a live rule_source is JsonFileRuleSource", "PASS", rs_class_name)
    else:
        _record("6a live rule_source is JsonFileRuleSource", "FAIL",
                f"got {rs_class_name!r}")

    # Verify promoted rules have source_section starting with "session-MR-"
    # (the promotion-specific prefix) and that none of these appear in
    # the JsonFileRuleSource output.
    json_sections = {r.source_section for r in rs.positive_rules()}
    leaked = [
        r for r in promoted_rules
        if r["source_section"] in json_sections
    ]
    if leaked:
        _record("6b promoted sections absent from JSON source", "FAIL",
                f"leaked={leaked}")
    else:
        _record("6b promoted sections absent from JSON source", "PASS",
                f"{len(promoted_rules)} promoted sections, 0 in seed_plan.json")

    session_mr_prefixed = [
        r for r in promoted_rules
        if str(r["source_section"]).startswith("session-MR-")
    ]
    if len(session_mr_prefixed) == len(promoted_rules):
        _record("6c all promoted sections carry session-MR- prefix",
                "PASS", f"n={len(session_mr_prefixed)}")
    else:
        _record("6c all promoted sections carry session-MR- prefix",
                "FAIL",
                f"{len(session_mr_prefixed)}/{len(promoted_rules)}")


# ---------------------------------------------------------------------------
# Probe 7 — source_hash parity with seed script
# ---------------------------------------------------------------------------


def probe_7() -> None:
    _hr("Probe 7 — source_hash parity with seed script")

    # Import the seed script's source_hash. Running on the bench, the
    # scripts/ directory is at ~/frappe-bench/apps/rgi_migration/scripts
    import importlib.util
    from pathlib import Path

    app_path = Path(frappe.get_app_path("rgi_migration")).parent
    seed_script = app_path / "scripts" / "seed_mapping_rules.py"
    if not seed_script.exists():
        _record("7 seed script path", "FAIL", f"not found at {seed_script}")
        return

    spec = importlib.util.spec_from_file_location(
        "_seed_mapping_rules_probe7", seed_script
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    seed_hash_fn = mod.source_hash

    # Identical inputs → identical hashes across the two implementations.
    for (section, is_anti, pattern) in [
        ("§4.1", False, "Salaries"),
        ("§4.10", True, "University Theory Exam Advance"),
        ("session-MR-MD-2026-24064", False, "ICICI BANK - 624205021153"),
    ]:
        seed_h = seed_hash_fn(section, is_anti, pattern)
        promo_h = compute_source_hash(section, is_anti, pattern)
        if seed_h == promo_h:
            _record("7 source_hash parity",
                    "PASS",
                    f"section={section!r} anti={is_anti} "
                    f"pattern={pattern!r} hash={seed_h[:12]}... matches")
        else:
            _record("7 source_hash parity",
                    "FAIL",
                    f"seed={seed_h} promo={promo_h}")


# ---------------------------------------------------------------------------
# Cleanup — restore mutated Probe 4 second target + leave Mapping Rules
# ---------------------------------------------------------------------------


def cleanup(conflict_ctx: dict) -> None:
    _hr("Cleanup — restore Probe 4 second MD tally_name (pre-mutation state)")
    if not conflict_ctx:
        return
    md_name = conflict_ctx.get("second_md")
    original = conflict_ctx.get("original_tally")
    if md_name and original:
        frappe.db.set_value("Mapping Decision", md_name, "tally_name", original)
        frappe.db.commit()
        current = frappe.db.get_value("Mapping Decision", md_name, "tally_name")
        print(f"  restored {md_name}.tally_name -> {current!r}")

    # Mapping Rule rows persist per Option 2 (Aditya's Phase C
    # authorization): they're valid session_review rules from real
    # approved MDs on a real session, left in place as legitimate
    # audit trail.
    survivors = frappe.get_all(
        "Mapping Rule",
        filters={"created_from": "session_review"},
        fields=["name", "tally_pattern", "source_section", "created_via_session"],
    )
    print(f"  session_review Mapping Rule rows left in DB: {len(survivors)}")
    for r in survivors:
        print(f"    {r['name']} pattern={r['tally_pattern']!r} "
              f"via={r['created_via_session']!r}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    print("\n\n########## ITEM 5 COMMIT 1 — PHASE C SMOKE ##########\n")
    targets = select_targets()
    conflict_ctx = None
    try:
        probe_5(targets)
        happy_ctx = probe_1_2_3(targets)
        # Probe 1b (literal template) — skipped + documented:
        _hr("Probe 1b — literal template (SKIPPED)")
        _record("1b literal template path", "SKIP",
                "skipped — no CACSPU-free Account exists on company "
                "'GHR CACS Pune' (ERPNext naming convention: every Account "
                "ends with ' - <abbr>'). Unit test coverage: "
                "test_build_payload_literal_template_flag_surfaced + "
                "test_substitute_abbr_literal_template_zero_occurrence_is_valid")
        conflict_ctx = probe_4(targets)
        probe_6(happy_ctx, conflict_ctx)
        probe_7()
    except Exception:
        print("\n!!! UNCAUGHT EXCEPTION IN PROBE RUN !!!")
        traceback.print_exc()
        _record("probe run", "FAIL", "uncaught exception — see trace")
    finally:
        cleanup(conflict_ctx)

    _hr("=== PHASE C SMOKE MATRIX ===")
    counts = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    for probe, outcome, detail in _results:
        counts[outcome] = counts.get(outcome, 0) + 1
        print(f"  [{outcome:<4}] {probe:<48} {detail[:100]}")
    print()
    print(f"  totals: PASS={counts['PASS']} FAIL={counts['FAIL']} SKIP={counts['SKIP']}")


if __name__ == "__main__":
    main()
