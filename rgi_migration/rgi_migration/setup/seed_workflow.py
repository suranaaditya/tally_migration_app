"""Work Item 5 seed orchestration — preview, insert, verify, sample.

All functions are invoked via `bench --site <site> console` with a Python
heredoc per docs/mapper_design_notes.md §5. Keeps shell-escape hazards
out of the operator-facing command.

Usage:

    bench --site erp.jewonline.in console
    >>> from rgi_migration.rgi_migration.setup.seed_workflow import preview
    >>> preview()                                    # dry-check, no writes
    >>> from rgi_migration.rgi_migration.setup.seed_workflow import insert
    >>> insert()                                     # idempotent insert
    >>> from rgi_migration.rgi_migration.setup.seed_workflow import verify
    >>> verify()                                     # 5 assertions
    >>> from rgi_migration.rgi_migration.setup.seed_workflow import sample
    >>> sample()                                     # print 3 rule as_dict()

Source-of-truth rule data lives in scripts/seed_mapping_rules.py at the
app repo root; this module adds the app-package entry points so Frappe's
ORM can reach it without sys.path hackery in the heredoc.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import frappe

# scripts/ is a sibling of the app package on disk; put it on sys.path so
# we can import the seed data + helpers without duplicating them here.
_APP_ROOT = Path(frappe.get_app_path("rgi_migration")).parent.parent
_SCRIPTS = _APP_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from seed_mapping_rules import (  # noqa: E402  (sys.path set above)
    build_plan,
    real_seed,
    validate_deployed_schema,
)


# ---------------------------------------------------------------------------
# Preview — schema + count + hash-distinctness
# ---------------------------------------------------------------------------


def preview() -> dict:
    """Dry-check pass. No DB writes. Returns a result dict; aborts with
    print on any failure so the operator sees 'PREVIEW FAIL'."""
    plan = build_plan()

    print()
    print("=" * 70)
    print("Work Item 5 seed PREVIEW")
    print("=" * 70)
    print(f"  total               = {plan['total']}")
    print(f"  positive_count      = {plan['positive_count']}")
    print(f"  anti_pattern_count  = {plan['anti_pattern_count']}")
    print(f"  creates_account=1   = {plan['creates_erpnext_account_count']}")

    # Hash distinctness
    all_hashes = [
        r["source_hash"]
        for r in plan["positive_rules"] + plan["anti_pattern_rules"]
    ]
    duplicates = [h for h in all_hashes if all_hashes.count(h) > 1]
    print(f"  distinct hashes     = {len(set(all_hashes))} of {len(all_hashes)}")
    if duplicates:
        print(f"  DUPLICATE HASHES    = {duplicates}")

    # Schema alignment
    schema_errors = validate_deployed_schema(plan)
    if schema_errors:
        print(f"  SCHEMA ERRORS       = {len(schema_errors)}")
        for err in schema_errors:
            print(err)
    else:
        print("  schema alignment    = all keys resolve to deployed DocType fields")

    expected_total = 23
    expected_positive = 20
    expected_anti = 3
    expected_creates = 5

    ok = (
        plan["total"] == expected_total
        and plan["positive_count"] == expected_positive
        and plan["anti_pattern_count"] == expected_anti
        and plan["creates_erpnext_account_count"] == expected_creates
        and not duplicates
        and not schema_errors
    )

    print()
    print("VERDICT:", "PREVIEW PASS (safe to insert)" if ok else "PREVIEW FAIL")
    return {
        "ok": ok,
        "total": plan["total"],
        "positive": plan["positive_count"],
        "anti_pattern": plan["anti_pattern_count"],
        "creates_account": plan["creates_erpnext_account_count"],
        "duplicate_hashes": duplicates,
        "schema_errors": schema_errors,
    }


# ---------------------------------------------------------------------------
# Insert — idempotent seed via source_hash existence check
# ---------------------------------------------------------------------------


def insert(purge: bool = False) -> dict:
    """Real insert pass. Re-runs preview as a safety check; aborts if it
    fails. Returns a counts dict (inserted / skipped / errors)."""
    check = preview()
    if not check["ok"]:
        print()
        print("ABORT: preview check failed; not inserting.")
        return {"aborted": True}

    plan = build_plan()
    print()
    print("=" * 70)
    print("Work Item 5 seed INSERT")
    print("=" * 70)
    summary = real_seed(plan, purge=purge)
    return summary


# ---------------------------------------------------------------------------
# Verify — five hard assertions
# ---------------------------------------------------------------------------


def verify() -> dict:
    """Five assertions defined in Work Item 5. Raises AssertionError on
    failure; prints ALL PASS on success."""
    total = frappe.db.count("Mapping Rule")
    assert total == 23, f"expected 23 Mapping Rule rows, got {total}"

    hashes = [
        r["source_hash"]
        for r in frappe.get_all("Mapping Rule", fields=["source_hash"])
    ]
    assert len(set(hashes)) == 23, (
        f"duplicate source_hash found: {len(hashes)} rows, "
        f"{len(set(hashes))} distinct"
    )

    anti_count = frappe.db.count("Mapping Rule", {"is_anti_pattern": 1})
    assert anti_count == 3, f"expected 3 anti-patterns, got {anti_count}"

    creates_count = frappe.db.count(
        "Mapping Rule", {"creates_erpnext_account": 1}
    )
    assert creates_count == 5, (
        f"expected 5 rows with creates_erpnext_account=1, got {creates_count}"
    )

    confirmed = frappe.db.count("Mapping Rule", {"status": "confirmed"})
    assert confirmed == 23, f"expected 23 confirmed, got {confirmed}"

    print()
    print("=" * 70)
    print("Work Item 5 seed VERIFY — ALL 5 ASSERTIONS PASS")
    print("=" * 70)
    print(f"  frappe.db.count('Mapping Rule') == 23                             OK")
    print(f"  len(set(source_hashes)) == 23                                     OK")
    print(f"  frappe.db.count('Mapping Rule', is_anti_pattern=1) == 3           OK")
    print(f"  frappe.db.count('Mapping Rule', creates_erpnext_account=1) == 5   OK")
    print(f"  frappe.db.count('Mapping Rule', status='confirmed') == 23         OK")
    return {
        "total": total,
        "distinct_hashes": len(set(hashes)),
        "anti_pattern_count": anti_count,
        "creates_account_count": creates_count,
        "confirmed_count": confirmed,
    }


# ---------------------------------------------------------------------------
# Sample — 3 illustrative rule dumps
# ---------------------------------------------------------------------------


def sample() -> None:
    """Print doc.as_dict() for 3 illustrative rules:
    - sec 4.1 (positive, tally_pattern_alternates populated)
    - sec 11 R3 (anti-pattern, creates_erpnext_account=1)
    - sec 4.13 (positive, combine_amounts=1)
    """
    targets = [
        ("section 4.1 (positive, alternates + combine)", "§4.1"),
        ("section 11 R3 (anti-pattern + account creation)", "§11 R3"),
        ("section 4.13 (positive, combine_amounts=1)", "§4.13"),
    ]
    for label, section in targets:
        name = frappe.db.exists("Mapping Rule", {"source_section": section})
        if not name:
            print(f"\n--- {label} --- NOT FOUND (source_section={section!r})")
            continue
        doc = frappe.get_doc("Mapping Rule", name)
        d = doc.as_dict()
        print()
        print("=" * 70)
        print(f"--- {label} ---")
        print(f"DocType name: {name}")
        print("=" * 70)
        print(json.dumps(d, default=str, indent=2, ensure_ascii=False))
