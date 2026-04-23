"""Item 6 — Phase C bench programmatic smoke for the composite Tier-2
matcher pipeline (norm_strong → acct_num → classical fuzzy).

Six probes:
    Probe 1 — 3-table calibration + composite audit + 75-84 band
              breakdown for Sub-matcher 3 (threshold-sensitive)
    Probe 2 — Runtime (Reset Parse + Run Mapper)
    Probe 3 — Generator compatibility: approve one tier2_fuzzy
              decision, call generate_main_opening_je, verify
              flow-through
    Probe 4 — Session.tier2_fuzzy_count parity
    Probe 5 — Synthetic tie handling across all 3 sub-matchers
    Probe 6 — Dialog-trigger preconditions (Phase B extension):
              Sub-matcher 3 Pending rows have matched_rule ==
              "tier2:fuzzy_classical" AND final_account IS NULL;
              Sub-matchers 1 and 2 have final_account auto-populated.

Invocation (from bench console):

    bench --site erp.jewonline.in console <<PY
    import sys
    sys.path.insert(0, '/home/frappe/frappe-bench/apps/rgi_migration/scripts')
    import item6_phase_c_smoke
    item6_phase_c_smoke.main()                     # threshold=75 default
    item6_phase_c_smoke.main(threshold=80)
    item6_phase_c_smoke.main(threshold=78, csv_export='/tmp/cal80.csv')
    PY

The threshold parameter is the Phase C calibration tooling: rerun
`main(threshold=N)` to recalibrate Probe 1 at different cutoffs.
Probes 2-6 behaviour is threshold-invariant (they verify structural
correctness, not score gating) — but are re-run anyway for
consistency so each invocation is a full pass.
"""
from __future__ import annotations

import csv
import json
import time
import traceback
from collections import Counter
from dataclasses import dataclass
from typing import Any

import frappe

from rgi_migration.generators.opening_je import (
    MainJEGenerationError,
    generate_main_opening_je,
)
from rgi_migration.mapper.mapper import CoaAccount
from rgi_migration.mapper.tier2_fuzzy import (
    SUBTIER_ACCT_NUM,
    SUBTIER_FUZZY,
    SUBTIER_NORM,
    Tier2Match,
    find_by_account_number,
    find_by_classical_fuzzy,
    find_by_normalization,
    find_tier2_match,
)
from rgi_migration.rgi_migration.doctype.tally_migration_session.tally_migration_session import (
    reset_parse, run_mapper,
)
from rgi_migration.rgi_migration.page.md_review.md_review import save_decision

SESSION = "TMS-CACSPU--00495"


_results: list[tuple[str, str, str]] = []


def _record(probe: str, outcome: str, detail: str) -> None:
    _results.append((probe, outcome, detail))
    print(f"    [{outcome:<4}] {probe}: {detail}")


def _hr(title: str) -> None:
    print()
    print("=" * 80)
    print(title)
    print("=" * 80)


@dataclass
class _StubLedger:
    name: str
    root_type: str


# ---------------------------------------------------------------------------
# COA loader
# ---------------------------------------------------------------------------


def _load_live_coa(company: str) -> dict[str, CoaAccount]:
    rows = frappe.get_all(
        "Account",
        filters={"company": company, "disabled": 0},
        fields=["name", "parent_account", "root_type", "is_group"],
        limit_page_length=0,
    )
    return {
        r.name: CoaAccount(
            name=r.name, parent_account=r.parent_account,
            root_type=r.root_type or "",
            is_group=bool(r.is_group), company_abbr="",
        )
        for r in rows
    }


# ---------------------------------------------------------------------------
# Configure threshold on the Mapper at run_mapper time
# ---------------------------------------------------------------------------


def _run_mapper_with_threshold(session_name: str, threshold: float) -> dict:
    """run_mapper uses defaults; override threshold by monkey-patching
    the Mapper constructor's kwarg-default for this invocation. Phase C
    calibration tooling — the production code's default is set by
    tier2_fuzzy.py; we override here per-run."""
    from rgi_migration.session import parse_and_map as pm

    original = pm.run_mapper_pipeline

    def wrapped(*args, **kwargs):
        kwargs["account_fuzzy_threshold"] = threshold
        return original(*args, **kwargs)

    pm.run_mapper_pipeline = wrapped
    try:
        return run_mapper(session_name)
    finally:
        pm.run_mapper_pipeline = original


# ---------------------------------------------------------------------------
# Probe 1 — 3-table calibration + composite audit + 75-84 band
# ---------------------------------------------------------------------------


def probe_1_calibration(
    *, threshold: float, csv_export: str | None = None,
) -> dict:
    _hr(f"PROBE 1 — Calibration (threshold={threshold})")
    session = frappe.get_doc("Tally Migration Session", SESSION)
    erpnext_company = frappe.get_doc(
        "Company Abbreviation", session.company_abbr
    ).erpnext_company
    coa = _load_live_coa(erpnext_company)

    # Get the persisted tier-2 outcome set + original unmapped pool.
    tier2_rows = frappe.get_all(
        "Mapping Decision",
        filters={"session": SESSION, "tier": "tier2_fuzzy"},
        fields=["name", "tally_name", "proposed_account",
                "matched_rule", "confidence", "tally_root_type",
                "final_account", "review_action"],
        order_by="matched_rule, confidence desc",
        limit_page_length=0,
    )
    unmapped_rows = frappe.get_all(
        "Mapping Decision",
        filters={"session": SESSION, "tier": "unmapped",
                 "review_action": "Pending"},
        fields=["name", "tally_name", "tally_root_type",
                "matched_rule", "excluded_reason"],
        limit_page_length=0,
    )

    # -------- Per-sub-matcher tables (Q14 3-table requirement)
    groups: dict[str, list] = {
        SUBTIER_NORM: [], SUBTIER_ACCT_NUM: [], SUBTIER_FUZZY: [],
    }
    for r in tier2_rows:
        groups.setdefault(r.matched_rule or "(unset)", []).append(r)

    def _print_subtier_table(label: str, subtier_key: str, rows: list) -> None:
        print()
        print(f"  --- {label}  ({len(rows)} match{'es' if len(rows) != 1 else ''}) ---")
        if not rows:
            print("    (none)")
            return
        for r in rows:
            conf = f"{r.confidence:.2f}" if r.confidence else "1.00"
            final_state = (
                "final_account auto-populated"
                if r.final_account else "final_account NULL (needs dialog)"
            )
            print(f"    Tally     : {r.tally_name!r}")
            print(f"    Proposed  : {r.proposed_account!r}")
            print(f"    Root      : {r.tally_root_type}   "
                  f"Conf: {conf}   {final_state}")
            print()

    _print_subtier_table(
        "Sub-matcher 1 (tier2:norm_strong — letter-spacing / punctuation)",
        SUBTIER_NORM, groups.get(SUBTIER_NORM, []),
    )
    _print_subtier_table(
        "Sub-matcher 2 (tier2:acct_num — shared account identifier)",
        SUBTIER_ACCT_NUM, groups.get(SUBTIER_ACCT_NUM, []),
    )
    _print_subtier_table(
        "Sub-matcher 3 (tier2:fuzzy_classical — partial_ratio)",
        SUBTIER_FUZZY, groups.get(SUBTIER_FUZZY, []),
    )

    # -------- 75-84 band breakdown (Phase C expansion)
    fuzzy_classical = groups.get(SUBTIER_FUZZY, [])
    zone_75_84 = [r for r in fuzzy_classical
                  if r.confidence and 0.75 <= r.confidence < 0.85]
    zone_85_plus = [r for r in fuzzy_classical
                    if r.confidence and r.confidence >= 0.85]
    print()
    print("  --- Sub-matcher 3 score-band breakdown ---")
    print(f"    75-84 band: {len(zone_75_84)} match(es) "
          f"(dialog-gated; reviewer judgment)")
    for r in zone_75_84:
        print(f"      {r.confidence:.2f}  {r.tally_name!r} → "
              f"{r.proposed_account!r}")
    print(f"    85+   band: {len(zone_85_plus)} match(es)")
    for r in zone_85_plus:
        print(f"      {r.confidence:.2f}  {r.tally_name!r} → "
              f"{r.proposed_account!r}")

    # -------- Composite audit (Q14) — per-row manual-audit column
    # Claude Code fills "Y / N / ?" from COA lookup inspection. This
    # is the threshold-sensitive decision surface.
    _hr(f"PROBE 1 — Composite audit ({len(tier2_rows)} matches)")
    print(f"{'Tally':<48} | {'Sub':<10} | {'Conf':>5} | {'Proposed':<42} | Correct?")
    print("-" * 140)
    correctness_rows: list[dict] = []
    for r in tier2_rows:
        sub_short = (r.matched_rule or "").replace("tier2:", "")
        audit = _manual_audit_correctness(r, coa)
        conf_str = f"{r.confidence:.2f}" if r.confidence else "1.00"
        t = (r.tally_name or "")[:47]
        p = (r.proposed_account or "")[:41]
        print(f"{t:<48} | {sub_short:<10} | {conf_str:>5} | {p:<42} | {audit}")
        correctness_rows.append({
            "tally": r.tally_name, "subtier": r.matched_rule or "",
            "confidence": float(r.confidence or 0),
            "proposed": r.proposed_account, "audit": audit,
        })

    # -------- Summary stats
    total = len(tier2_rows)
    yes = sum(1 for x in correctness_rows if x["audit"] == "Y")
    no = sum(1 for x in correctness_rows if x["audit"] == "N")
    unknown = total - yes - no
    print()
    print(f"  SUMMARY: {yes}/{total} confirmed correct  |  "
          f"{no} wrong  |  {unknown} unverified (need reviewer eye)")
    print(f"  Unmapped after: {len(unmapped_rows)}")
    print(f"  Tier-2 refusals (unmapped with excluded_reason): "
          f"{sum(1 for r in unmapped_rows if r.matched_rule)}")

    # -------- CSV export
    if csv_export:
        with open(csv_export, "w", newline="", encoding="utf-8") as fp:
            w = csv.DictWriter(fp, fieldnames=[
                "tally", "subtier", "confidence", "proposed", "audit",
            ])
            w.writeheader()
            for row in correctness_rows:
                w.writerow(row)
        print(f"  CSV export: {csv_export}")

    if total == 0:
        _record("probe-1", "WARN", "zero tier-2 matches at this threshold")
    else:
        precision_pct = (yes * 100 // total)
        _record("probe-1", "OK",
                f"{total} matches @ threshold {threshold}; "
                f"{yes}/{total} correct (~{precision_pct}%); "
                f"75-84 band: {len(zone_75_84)}")

    return {
        "threshold": threshold, "total": total, "yes": yes,
        "no": no, "unknown": unknown,
        "zone_75_84": len(zone_75_84),
        "zone_85_plus": len(zone_85_plus),
        "unmapped_after": len(unmapped_rows),
        "rows": correctness_rows,
    }


def _manual_audit_correctness(row: Any, coa: dict[str, CoaAccount]) -> str:
    """Heuristic audit — Y when strong signal of correct match, N when
    COA has a clearly better target for the ledger, ? when ambiguous.

    This is a PROGRAMMATIC approximation — not a substitute for
    reviewer-eye audit. Good enough for surfacing obvious false-
    positives; reviewer reads the table and makes the final call.
    """
    tally = (row.tally_name or "").lower()
    proposed = (row.proposed_account or "").lower()
    root = row.tally_root_type or ""

    # Strong signal: proposed is normalized equal to tally OR proposed
    # contains / is contained by tally's distinctive tokens.
    from rgi_migration.mapper.tier2_fuzzy import normalize_strong
    if normalize_strong(row.tally_name or "") == normalize_strong(
        row.proposed_account or ""
    ):
        return "Y"

    # Weak signal: proposed shares a >=6-letter token with tally (good
    # proxy for semantic overlap).
    import re
    tally_tokens = {t for t in re.split(r"[^a-z0-9]+", tally) if len(t) >= 6}
    proposed_tokens = {t for t in re.split(r"[^a-z0-9]+", proposed) if len(t) >= 6}
    shared = tally_tokens & proposed_tokens
    if not shared:
        return "?"

    # Check for a clearly-better alternative — another leaf account in
    # the same root_type with MORE shared tokens.
    better_exists = False
    for acct in coa.values():
        if acct.is_group or acct.root_type != root:
            continue
        if acct.name == row.proposed_account:
            continue
        alt_tokens = {
            t for t in re.split(r"[^a-z0-9]+", acct.name.lower())
            if len(t) >= 6
        }
        alt_shared = tally_tokens & alt_tokens
        if len(alt_shared) > len(shared):
            better_exists = True
            break

    if better_exists:
        return "?"   # needs reviewer — heuristic can't tell
    return "Y" if len(shared) >= 2 else "?"


# ---------------------------------------------------------------------------
# Probe 2 — Runtime
# ---------------------------------------------------------------------------


def probe_2_runtime(*, threshold: float) -> dict:
    _hr(f"PROBE 2 — Runtime (threshold={threshold})")
    r = reset_parse(SESSION)
    print(f"  reset_parse: {r}")
    t0 = time.perf_counter()
    result = _run_mapper_with_threshold(SESSION, threshold)
    elapsed = time.perf_counter() - t0
    by_tier = result.get("summary", {}).get("by_tier", {})
    print(f"  run_mapper: {result.get('decision_count')} decisions in "
          f"{elapsed:.2f}s")
    print(f"  by_tier: {json.dumps(by_tier, indent=2, default=str)}")

    fuzzy = by_tier.get("tier2_fuzzy", 0)
    unmapped = by_tier.get("unmapped", 0)
    if elapsed > 5.0:
        _record("probe-2", "WARN", f"runtime {elapsed:.2f}s exceeds 5s budget")
    else:
        _record("probe-2", "OK",
                f"runtime {elapsed:.2f}s  tier2_fuzzy={fuzzy}  "
                f"unmapped={unmapped}")
    return {"elapsed_s": elapsed, "total": result.get("decision_count"),
            "by_tier": by_tier}


# ---------------------------------------------------------------------------
# Probe 3 — Generator compatibility
# ---------------------------------------------------------------------------


def probe_3_generator_compat(p2: dict) -> dict:
    _hr("PROBE 3 — opening_je eligibility for tier2_fuzzy+Approved")
    fuzzy_count = p2["by_tier"].get("tier2_fuzzy", 0)
    if fuzzy_count == 0:
        _record("probe-3", "SKIP", "no tier2_fuzzy rows to exercise")
        return {"skipped": True}

    # Sub-matcher 1/2 have final_account auto-populated; pick one of
    # those — closest to the "Approved tier2 row" state. Reviewer only
    # needs to flip review_action → Approved.
    equality_matches = frappe.get_all(
        "Mapping Decision",
        filters={
            "session": SESSION, "tier": "tier2_fuzzy",
            "review_action": "Pending",
            "matched_rule": ["in", [SUBTIER_NORM, SUBTIER_ACCT_NUM]],
            "final_account": ["is", "set"],
        },
        fields=["name", "tally_name", "final_account", "matched_rule"],
        limit_page_length=1,
    )
    if not equality_matches:
        _record("probe-3", "SKIP",
                "no eligible equality-match rows to exercise")
        return {"skipped": True}
    target = equality_matches[0]
    print(f"  Target: {target.name!r}  subtier={target.matched_rule}  "
          f"tally={target.tally_name!r}")

    save_decision(
        decision_name=target.name,
        review_action="Approved",
        final_account=target.final_account,
        reviewer_notes="",
    )
    print(f"  save_decision: Approved final_account={target.final_account!r}")

    try:
        generate_main_opening_je(SESSION)
        _record("probe-3", "OK",
                f"opening_je generated (!) — tier2_fuzzy row contributed")
        return {"approved": target.name, "generated": True}
    except MainJEGenerationError as exc:
        msg = str(exc)
        if target.name in msg or target.matched_rule in msg:
            _record("probe-3", "FAIL",
                    f"opening_je cited tier2_fuzzy row: {msg[:180]}")
            return {"approved": target.name, "generated": False,
                    "refusal_msg": msg}
        _record("probe-3", "OK",
                f"opening_je refused (expected — other pending rows); "
                f"no mention of {target.name} → tier2_fuzzy flowed through "
                f"_ELIGIBLE_TIERS correctly")
        return {"approved": target.name, "generated": False,
                "refusal_msg": msg}


# ---------------------------------------------------------------------------
# Probe 4 — Session counter parity
# ---------------------------------------------------------------------------


def probe_4_counter_parity(p2: dict) -> dict:
    _hr("PROBE 4 — Session.tier2_fuzzy_count parity")
    session = frappe.get_doc("Tally Migration Session", SESSION)
    counter = session.tier2_fuzzy_count or 0
    persisted = frappe.db.count(
        "Mapping Decision",
        {"session": SESSION, "tier": "tier2_fuzzy"},
    )
    by_tier = p2["by_tier"].get("tier2_fuzzy", 0)
    ok = counter == persisted == by_tier
    if ok:
        _record("probe-4", "OK",
                f"counter={counter} persisted={persisted} by_tier={by_tier}")
    else:
        _record("probe-4", "FAIL",
                f"counter={counter} persisted={persisted} by_tier={by_tier}")
    return {"counter": counter, "persisted": persisted,
            "by_tier": by_tier, "ok": ok}


# ---------------------------------------------------------------------------
# Probe 5 — Synthetic tie handling across sub-matchers
# ---------------------------------------------------------------------------


def probe_5_tie_handling() -> dict:
    _hr("PROBE 5 — Synthetic tie handling (all 3 sub-matchers)")

    # Sub-matcher 1 tie
    coa_1 = {
        "TDS On Salary -193 - CACSPU": CoaAccount(
            name="TDS On Salary -193 - CACSPU", parent_account=None,
            root_type="Liability", is_group=False, company_abbr="",
        ),
        "T.D.S. On Salary 193 - CACSPU": CoaAccount(
            name="T.D.S. On Salary 193 - CACSPU", parent_account=None,
            root_type="Liability", is_group=False, company_abbr="",
        ),
    }
    r1 = find_by_normalization(
        _StubLedger(name="T D S On Salary - 193", root_type="Liability"),
        coa_1,
    )
    sub1_tie_ok = r1.is_refusal and r1.subtier == SUBTIER_NORM
    print(f"  Sub-matcher 1 synthetic tie: {'PASS' if sub1_tie_ok else 'FAIL'}")

    # Sub-matcher 2 tie
    coa_2 = {
        "Account 99999999 Variant A - X": CoaAccount(
            name="Account 99999999 Variant A - X", parent_account=None,
            root_type="Asset", is_group=False, company_abbr="",
        ),
        "Account 99999999 Variant B - X": CoaAccount(
            name="Account 99999999 Variant B - X", parent_account=None,
            root_type="Asset", is_group=False, company_abbr="",
        ),
    }
    r2 = find_by_account_number(
        _StubLedger(name="Foo 99999999", root_type="Asset"), coa_2,
    )
    sub2_tie_ok = r2.is_refusal and r2.subtier == SUBTIER_ACCT_NUM
    print(f"  Sub-matcher 2 synthetic tie: {'PASS' if sub2_tie_ok else 'FAIL'}")

    # Sub-matcher 3 tie — harder to construct synthetically; may no-match
    coa_3 = {
        "Vendor Alpha1 - X": CoaAccount(
            name="Vendor Alpha1 - X", parent_account=None,
            root_type="Liability", is_group=False, company_abbr="",
        ),
        "Vendor Alpha2 - X": CoaAccount(
            name="Vendor Alpha2 - X", parent_account=None,
            root_type="Liability", is_group=False, company_abbr="",
        ),
    }
    r3 = find_by_classical_fuzzy(
        _StubLedger(name="Vendor Alpha", root_type="Liability"),
        coa_3, threshold=50.0,
    )
    sub3_status = (
        "TIE"
        if (r3.is_refusal and r3.subtier == SUBTIER_FUZZY)
        else "MATCH" if r3.is_match else "NO-MATCH"
    )
    print(f"  Sub-matcher 3 synthetic tie: {sub3_status}")

    if sub1_tie_ok and sub2_tie_ok:
        _record("probe-5", "OK",
                f"sub-1 TIE, sub-2 TIE, sub-3 {sub3_status}")
    else:
        _record("probe-5", "FAIL",
                f"sub-1 {'TIE' if sub1_tie_ok else 'FAIL'}, "
                f"sub-2 {'TIE' if sub2_tie_ok else 'FAIL'}, "
                f"sub-3 {sub3_status}")
    return {"sub1": sub1_tie_ok, "sub2": sub2_tie_ok, "sub3": sub3_status}


# ---------------------------------------------------------------------------
# Probe 6 — Dialog-trigger preconditions (Phase B extension)
# ---------------------------------------------------------------------------


def probe_6_dialog_preconditions() -> dict:
    _hr("PROBE 6 — Dialog-trigger preconditions")

    # Expected invariants:
    #   - tier2:fuzzy_classical rows: final_account IS NULL (dialog
    #     forces explicit Accept / Reject / Pick different)
    #   - tier2:norm_strong + tier2:acct_num rows: final_account IS
    #     auto-populated from proposed_account
    fuzzy_rows = frappe.get_all(
        "Mapping Decision",
        filters={"session": SESSION, "matched_rule": SUBTIER_FUZZY},
        fields=["name", "tally_name", "proposed_account",
                "final_account", "review_action"],
        limit_page_length=0,
    )
    norm_rows = frappe.get_all(
        "Mapping Decision",
        filters={"session": SESSION, "matched_rule": SUBTIER_NORM},
        fields=["name", "tally_name", "proposed_account",
                "final_account", "review_action"],
        limit_page_length=0,
    )
    acct_rows = frappe.get_all(
        "Mapping Decision",
        filters={"session": SESSION, "matched_rule": SUBTIER_ACCT_NUM},
        fields=["name", "tally_name", "proposed_account",
                "final_account", "review_action"],
        limit_page_length=0,
    )

    fuzzy_violations = [
        r for r in fuzzy_rows
        if r.review_action == "Pending" and r.final_account
    ]
    norm_violations = [
        r for r in norm_rows
        if r.proposed_account and not r.final_account
    ]
    acct_violations = [
        r for r in acct_rows
        if r.proposed_account and not r.final_account
    ]

    print(f"  fuzzy_classical rows: {len(fuzzy_rows)}  "
          f"(Pending-with-final_account-set violations: {len(fuzzy_violations)})")
    print(f"  norm_strong rows:      {len(norm_rows)}  "
          f"(missing-final_account violations: {len(norm_violations)})")
    print(f"  acct_num rows:         {len(acct_rows)}  "
          f"(missing-final_account violations: {len(acct_violations)})")

    if fuzzy_violations or norm_violations or acct_violations:
        _record("probe-6", "FAIL",
                f"invariant violations — "
                f"fuzzy={len(fuzzy_violations)}, "
                f"norm={len(norm_violations)}, "
                f"acct={len(acct_violations)}")
    else:
        _record("probe-6", "OK",
                f"all {len(fuzzy_rows)+len(norm_rows)+len(acct_rows)} "
                f"tier-2 rows honor dialog-trigger invariants")

    return {
        "fuzzy_count": len(fuzzy_rows),
        "norm_count": len(norm_rows),
        "acct_count": len(acct_rows),
        "violations": {
            "fuzzy": len(fuzzy_violations),
            "norm": len(norm_violations),
            "acct": len(acct_violations),
        },
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(
    *, threshold: float = 75.0, csv_export: str | None = None,
) -> None:
    # Clear previous run's results when re-invoking in same console
    _results.clear()
    _hr(f"ITEM 6 PHASE C — Composite matcher smoke (threshold={threshold})")
    if csv_export:
        print(f"  CSV export target: {csv_export}")

    # Probe 2 FIRST — Reset + re-map with threshold. Probe 1 reads
    # persisted state produced by Probe 2.
    try:
        p2 = probe_2_runtime(threshold=threshold)
    except Exception as e:
        traceback.print_exc()
        _record("probe-2", "FAIL", f"exception: {e!r}")
        p2 = {"by_tier": {}, "elapsed_s": 0.0}

    try:
        p1 = probe_1_calibration(threshold=threshold, csv_export=csv_export)
    except Exception as e:
        traceback.print_exc()
        _record("probe-1", "FAIL", f"exception: {e!r}")
        p1 = None

    try:
        p4 = probe_4_counter_parity(p2)
    except Exception as e:
        traceback.print_exc()
        _record("probe-4", "FAIL", f"exception: {e!r}")
        p4 = None

    try:
        p6 = probe_6_dialog_preconditions()
    except Exception as e:
        traceback.print_exc()
        _record("probe-6", "FAIL", f"exception: {e!r}")
        p6 = None

    try:
        p3 = probe_3_generator_compat(p2)
    except Exception as e:
        traceback.print_exc()
        _record("probe-3", "FAIL", f"exception: {e!r}")
        p3 = None

    try:
        p5 = probe_5_tie_handling()
    except Exception as e:
        traceback.print_exc()
        _record("probe-5", "FAIL", f"exception: {e!r}")
        p5 = None

    # Final cleanup — reset + baseline re-map (threshold restored to
    # production default). Tester may run main() again with a new
    # threshold; baseline state is what awaits them.
    _hr("CLEANUP — reset + baseline re-map")
    try:
        reset_parse(SESSION)
        run_mapper(SESSION)
        print("  session returned to baseline (production threshold)")
    except Exception as e:
        traceback.print_exc()
        _record("cleanup", "FAIL", f"exception: {e!r}")

    _hr(f"=== PHASE C SMOKE MATRIX (threshold={threshold}) ===")
    counts = Counter(r[1] for r in _results)
    for probe, outcome, detail in _results:
        print(f"  [{outcome:<4}] {probe}: {detail}")
    print()
    print(f"  TOTAL: {dict(counts)}")
    print("=" * 80)
