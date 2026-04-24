"""One-shot probe that compares ``docs/seed_plan.json`` against the
live ``Mapping Rule`` DocType rows with ``created_from="seed"``.

Not a runtime gate — Item 8 resolution Q3: warn-only, surfaces
bootstrap divergence for fresh-bench reproducibility (Item 13). Run
via the Phase C bench smoke or a dedicated pytest that injects a
fake Frappe loader.

Pure — takes the JSON seed list and the DB-row list as arguments so
unit tests exercise it without a bench.
"""

from __future__ import annotations

from typing import Any


def compute_seed_drift(
    json_rules: list[dict[str, Any]],
    db_seed_rows: list[dict[str, Any]],
) -> list[str]:
    """Compare JSON seed rules against DB rows with ``created_from="seed"``.

    Returns a list of warning strings — empty list means no drift.
    Caller decides what to do with the warnings (log, print, raise —
    per Phase A Q3, ship as warn-only).

    Comparison keys: ``source_section`` (1:1 invariant in the seed plan).
    For each JSON rule, find the DB row with the same ``source_section``.
    Report per-field diffs for the drift-sensitive subset: ``source_hash``
    (the primary drift signal), ``tally_pattern``, ``tally_match_mode``,
    ``erpnext_account_template``, ``applicable_root_type``,
    ``is_anti_pattern``.
    """
    warnings: list[str] = []

    db_by_section: dict[str, dict[str, Any]] = {
        (r.get("source_section") or ""): r for r in db_seed_rows
    }
    json_sections: set[str] = set()

    for jr in json_rules:
        section = jr.get("source_section") or ""
        json_sections.add(section)
        dr = db_by_section.get(section)
        if dr is None:
            warnings.append(
                f"seed drift: JSON rule {section!r} has no "
                f"matching DB row (created_from='seed')"
            )
            continue
        for field in (
            "source_hash",
            "tally_pattern",
            "tally_match_mode",
            "erpnext_account_template",
            "applicable_root_type",
        ):
            jv = jr.get(field)
            dv = dr.get(field)
            if jv != dv:
                warnings.append(
                    f"seed drift: {section!r} field {field!r} "
                    f"differs — JSON={jv!r}, DB={dv!r}"
                )
        jv_anti = bool(jr.get("is_anti_pattern", 0))
        dv_anti = bool(dr.get("is_anti_pattern", 0))
        if jv_anti != dv_anti:
            warnings.append(
                f"seed drift: {section!r} field 'is_anti_pattern' "
                f"differs — JSON={jv_anti}, DB={dv_anti}"
            )

    # DB seed rows with no JSON counterpart (spurious seed in DB)
    for section, dr in db_by_section.items():
        if section not in json_sections:
            warnings.append(
                f"seed drift: DB seed row {section!r} "
                f"({dr.get('name')!r}) has no matching JSON rule"
            )

    return warnings
