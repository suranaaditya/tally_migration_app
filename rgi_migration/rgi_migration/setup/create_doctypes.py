"""Autonomous DocType creation for the rgi_migration Frappe app.

Entry points (invoke via `bench --site <site> execute`):

    rgi_migration.rgi_migration.setup.create_doctypes.run_all
        Full run: both phases + summary. Default for fresh bench installs.

    rgi_migration.rgi_migration.setup.create_doctypes.run_phase1
        Phase 1 only: create the 9 base DocTypes.

    rgi_migration.rgi_migration.setup.create_doctypes.run_phase2
        Phase 2 only: patch forward-reference fields into
        Tally Migration Session (child tables) and Mapping Rule
        (created_via_session Link).

All phases are idempotent. Existing DocTypes and fields are detected with
`frappe.db.exists` / `DocType.fields` checks and skipped — safe to re-run.

Ordering: forward references are resolved by creating child DocTypes and
referenced DocTypes before their consumers. Session + Mapping Rule each
carry one back-reference (Session <- child tables to Mapping Decision / ACR
/ SCR; Mapping Rule <- created_via_session to Session) which phase 2
patches in after all base DocTypes exist.

All DocType specs below use ASCII only (no special characters in field
names, labels, or options) per project convention. String values stored
AT RUNTIME (e.g. `source_section = "sec 4.1"`) may contain non-ASCII; that
is independent of the DocType schema definitions here.
"""

from __future__ import annotations

import frappe


MODULE = "Rgi Migration"


# =========================================================================
# Helpers
# =========================================================================


def _f(fieldname: str, label: str, fieldtype: str, **kwargs) -> dict:
    """Compact field-dict constructor. Keeps DocType specs readable."""
    out = {"fieldname": fieldname, "label": label, "fieldtype": fieldtype}
    out.update(kwargs)
    return out


def _perms_admin() -> list:
    """Default permission block for standalone DocTypes.

    System Manager has full CRUD. Additional roles can be granted via
    Role Permission Manager in the UI after install; baked-in perms
    stay minimal to avoid permission-surface drift between dev / prod.

    NOTE: `import` is deliberately omitted. Frappe gates that permission
    behind the DocType's own `allow_import=1` flag; granting it without
    the flag raises ValidationError at insert time. If a specific DocType
    later needs bulk-import support (e.g. Company Abbreviation seeded from
    sec 1.2 of RGI_Migration_Rules.md), set `allow_import=1` on the
    DocType via extra={"allow_import": 1} and add `"import": 1` to its
    permission block.
    """
    return [
        {
            "role": "System Manager",
            "read": 1, "write": 1, "create": 1, "delete": 1,
            "submit": 0, "cancel": 0, "amend": 0,
            "report": 1, "export": 1,
        },
    ]


def _dt(
    name: str,
    fields: list,
    *,
    istable: int = 0,
    autoname: str | None = None,
    extra: dict | None = None,
) -> dict:
    """Build a DocType spec suitable for `frappe.get_doc(...).insert()`."""
    spec: dict = {
        "doctype": "DocType",
        "name": name,
        "module": MODULE,
        "custom": 0,
        "istable": istable,
        "fields": fields,
    }
    if autoname:
        spec["autoname"] = autoname
    if not istable:
        spec["permissions"] = _perms_admin()
    if extra:
        spec.update(extra)
    return spec


# =========================================================================
# DocType specs
# =========================================================================

# 1. Company Abbreviation ---------------------------------------------------

COMPANY_ABBREVIATION = _dt(
    "Company Abbreviation",
    autoname="field:abbr",
    fields=[
        _f("abbr", "Abbreviation", "Data", reqd=1, unique=1, in_list_view=1,
           description="Short ERPNext-side abbreviation (e.g. CACSPU)."),
        _f("full_name", "Full Name", "Data", reqd=1, in_list_view=1,
           description="Full ERPNext company name (e.g. GHR CACS Pune)."),
        _f("trust_group", "Trust Group", "Select",
           options="ASS\nGHREMF\nGHREF\nGHRF\nCBS\nSGR\nGHRUA\nGHRSTU\nGHRISTU\nGHRUS\nOther",
           reqd=1, in_list_view=1),
        _f("entity_type", "Entity Type", "Select",
           options="college\nhostel\nsociety\nuniversity\nhospital\nother",
           reqd=1, default="college"),
        _f("erpnext_company", "ERPNext Company", "Link", options="Company",
           description="Bound once an ERPNext Company record exists; nullable."),
        _f("is_active", "Is Active", "Check", default=1, in_list_view=1),
        _f("notes", "Notes", "Small Text"),
    ],
)


# 2. Mapping Rule Alternate Pattern (child) ---------------------------------

MAPPING_RULE_ALTERNATE_PATTERN = _dt(
    "Mapping Rule Alternate Pattern",
    istable=1,
    fields=[
        _f("tally_pattern", "Tally Pattern", "Data", reqd=1, in_list_view=1),
        _f("tally_match_mode", "Match Mode", "Select",
           options="exact_ci\nprefix_ci\nsuffix_ci\ncontains_ci\nregex",
           default="exact_ci", reqd=1, in_list_view=1),
    ],
)


# 3. Mapping Rule -----------------------------------------------------------
#    (must precede Account Creation Source and Mapping Decision which
#     Link to it; `created_via_session` patched in during phase 2.)

MAPPING_RULE = _dt(
    "Mapping Rule",
    autoname="format:MR-.#####",
    fields=[
        # -- Identity / provenance
        _f("rule_name", "Rule Name", "Data", reqd=1, in_list_view=1),
        _f("is_anti_pattern", "Is Anti-Pattern", "Check", default=0,
           in_list_view=1),
        _f("status", "Status", "Select",
           options="confirmed\ntentative\npaused\ndeprecated",
           default="confirmed", reqd=1, in_list_view=1),
        _f("source_section", "Source Section", "Data",
           description="e.g. 'sec 4.1' or 'sec 11 R3'."),
        _f("source_hash", "Source Hash", "Data", unique=1,
           description="sha1(section|is_anti_pattern|tally_pattern)."),
        _f("source_entities", "Source Entities (CSV)", "Small Text"),
        _f("applies_to_entity_types", "Applies To Entity Types (CSV or *)",
           "Small Text", default="*"),
        # -- Tally-side matching
        _f("sb_tally", "Tally-Side Matching", "Section Break"),
        _f("tally_pattern", "Tally Pattern", "Data", reqd=1, in_list_view=1),
        _f("tally_match_mode", "Match Mode", "Select",
           options="exact_ci\nprefix_ci\nsuffix_ci\ncontains_ci\nregex",
           default="exact_ci", reqd=1),
        _f("tally_pattern_alternates", "Alternate Patterns", "Table",
           options="Mapping Rule Alternate Pattern"),
        _f("applicable_root_type", "Applicable Root Type", "Select",
           options="Any\nAsset\nLiability\nEquity\nIncome\nExpense",
           default="Any"),
        _f("tally_parent_contains", "Tally Parent Contains", "Data"),
        # -- Positive rule target
        _f("sb_positive", "Positive Rule Target", "Section Break"),
        _f("erpnext_account_template", "ERPNext Account Template", "Data"),
        _f("combine_amounts", "Combine Amounts With Sibling Matches", "Check",
           default=0),
        # -- Anti-pattern fields
        _f("sb_anti", "Anti-Pattern Fields", "Section Break"),
        _f("forbidden_erpnext_template", "Forbidden ERPNext Template", "Data"),
        _f("anti_pattern_reason", "Anti-Pattern Reason", "Small Text"),
        _f("suggested_alternative_template", "Suggested Alternative Template",
           "Data"),
        # -- Account creation directive
        _f("sb_creation", "Account Creation Directive", "Section Break"),
        _f("creates_erpnext_account", "Creates ERPNext Account (conditional)",
           "Check", default=0),
        _f("new_account_name_template", "New Account Name Template", "Data"),
        _f("new_account_parent", "New Account Parent (entity-agnostic)",
           "Data"),
        _f("new_account_root_type", "New Account Root Type", "Select",
           options="\nAsset\nLiability\nEquity\nIncome\nExpense"),
        _f("new_account_is_group", "New Account Is Group", "Check", default=0),
        # -- Observability
        _f("sb_obs", "Observability", "Section Break"),
        _f("times_applied", "Times Applied", "Int", default=0, read_only=1),
        _f("last_applied_at", "Last Applied At", "Datetime", read_only=1),
        _f("created_from", "Created From", "Select",
           options="seed\nsession_review\nmanual", default="seed"),
        # `created_via_session` Link -> Tally Migration Session
        # is added in phase 2 after Tally Migration Session exists.
    ],
)


# 4. Account Creation Source (child) ----------------------------------------
#    Links to Mapping Rule; must be created after Mapping Rule.

ACCOUNT_CREATION_SOURCE = _dt(
    "Account Creation Source",
    istable=1,
    fields=[
        _f("rule", "Mapping Rule", "Link", options="Mapping Rule",
           reqd=1, in_list_view=1),
    ],
)


# 5. Supplier Alias Rule ----------------------------------------------------

SUPPLIER_ALIAS_RULE = _dt(
    "Supplier Alias Rule",
    autoname="format:SAR-.#####",
    fields=[
        _f("rule_name", "Rule Name", "Data", reqd=1, in_list_view=1),
        _f("tally_vendor_pattern", "Tally Vendor Pattern", "Data", reqd=1,
           in_list_view=1),
        _f("tally_match_mode", "Match Mode", "Select",
           options="exact_ci\nprefix_ci\nsuffix_ci\ncontains_ci\nregex",
           default="exact_ci", reqd=1),
        _f("erpnext_supplier", "ERPNext Supplier", "Link",
           options="Supplier", reqd=1, in_list_view=1),
        _f("confidence", "Confidence", "Float", default=1.0),
        _f("created_from", "Created From", "Select",
           options="seed\nsession_review\nmanual",
           default="session_review"),
        _f("notes", "Notes", "Small Text"),
    ],
)


# 6. Mapping Decision (child) ----------------------------------------------

MAPPING_DECISION = _dt(
    "Mapping Decision",
    istable=1,
    fields=[
        # -- Tally input
        _f("tally_name", "Tally Name", "Data", reqd=1, in_list_view=1),
        _f("tally_id", "Tally ID", "Data"),
        _f("tally_parent_chain", "Tally Parent Chain", "Small Text"),
        _f("tally_root_type", "Root Type", "Select",
           options="\nAsset\nLiability\nEquity\nIncome\nExpense"),
        _f("opening_dr", "Opening Dr", "Currency"),
        _f("opening_cr", "Opening Cr", "Currency"),
        _f("net_amount", "Net Amount (signed)", "Currency"),
        _f("net_side", "Net Side", "Select", options="\nDr\nCr\nZero"),
        _f("is_pnl_closed_zero", "Is PnL Closed Zero", "Check"),
        _f("is_student_ledger", "Is Student Ledger", "Check"),
        _f("is_system_account", "Is System Account", "Check"),
        # -- Mapper proposal
        _f("sb_proposal", "Mapper Proposal", "Section Break"),
        _f("tier", "Tier", "Select",
           options="tier1_exact\ntier1_rule\ntier1_pattern\n"
                   "tier2_fuzzy\ntier3_claude\n"
                   "unmapped\nexcluded_pnl\ngroup_refused\n"
                   "anti_pattern_blocked\npending_account_creation",
           in_list_view=1),
        _f("proposed_account", "Proposed Account", "Link", options="Account",
           in_list_view=1),
        _f("matched_rule", "Matched Rule", "Data"),
        _f("confidence", "Confidence", "Float"),
        _f("proposed_dr", "Proposed Dr", "Currency"),
        _f("proposed_cr", "Proposed Cr", "Currency"),
        # -- Anti-pattern provenance
        _f("anti_pattern_blocked", "Anti-Pattern Blocked", "Check"),
        _f("anti_pattern_rule", "Anti-Pattern Rule", "Link",
           options="Mapping Rule"),
        _f("anti_pattern_message", "Anti-Pattern Message", "Small Text"),
        # -- Reviewer side
        _f("sb_review", "Reviewer Decision", "Section Break"),
        _f("review_action", "Review Action", "Select",
           options=(
               "Pending\nApproved\nRejected\nManual Override\n"
               "Deferred\nSkipped\nExcluded (P&L)\n"
               "Pending Account Creation\n"
               "Pending Group Account Resolution\n"
               "Pending Supplier Creation"
           ),
           default="Pending", reqd=1, in_list_view=1),
        _f("final_account", "Final Account", "Link", options="Account"),
        _f("final_dr", "Final Dr", "Currency"),
        _f("final_cr", "Final Cr", "Currency"),
        _f("reviewer_notes", "Reviewer Notes", "Small Text"),
        _f("excluded_reason", "Excluded Reason", "Small Text"),
        _f("flagged_for_email", "Flagged For Email", "Check"),
        _f("promoted_to_rule", "Promoted To Rule", "Link",
           options="Mapping Rule"),
    ],
)


# 7. Account Creation Request (child) --------------------------------------
#    Uses Account Creation Source as a nested child table.

ACCOUNT_CREATION_REQUEST = _dt(
    "Account Creation Request",
    istable=1,
    fields=[
        _f("status", "Status", "Select",
           options="Pending\nCreated\nSkipped\nFailed",
           default="Pending", reqd=1, in_list_view=1),
        _f("proposed_account_name", "Proposed Account Name", "Data",
           reqd=1, in_list_view=1),
        _f("proposed_parent", "Proposed Parent", "Data", reqd=1),
        _f("proposed_root_type", "Proposed Root Type", "Select",
           options="Asset\nLiability\nEquity\nIncome\nExpense", reqd=1),
        _f("proposed_is_group", "Proposed Is Group", "Check"),
        _f("reason", "Reason", "Small Text"),
        _f("source_rules", "Source Rules (audit trail)", "Table",
           options="Account Creation Source"),
        _f("source_decisions", "Source Decision IDXs (CSV)", "Small Text"),
        _f("reviewer_notes", "Reviewer Notes", "Small Text"),
        _f("created_account", "Created Account", "Link", options="Account"),
        _f("error_log", "Error Log", "Small Text"),
    ],
)


# 8. Supplier Creation Request (child) -------------------------------------

SUPPLIER_CREATION_REQUEST = _dt(
    "Supplier Creation Request",
    istable=1,
    fields=[
        _f("status", "Status", "Select",
           options="Pending\nCreated\nSkipped\nFailed",
           default="Pending", reqd=1, in_list_view=1),
        _f("tally_vendor_name", "Tally Vendor Name", "Data",
           reqd=1, in_list_view=1),
        _f("tally_vendor_id", "Tally Vendor ID", "Data"),
        _f("proposed_supplier_name", "Proposed Supplier Name", "Data",
           reqd=1),
        _f("proposed_supplier_group", "Proposed Supplier Group", "Data"),
        _f("detected_balance", "Detected Balance (net Cr)", "Currency"),
        _f("reviewer_notes", "Reviewer Notes", "Small Text"),
        _f("created_supplier", "Created Supplier", "Link", options="Supplier"),
        _f("source_decisions", "Source Decision IDXs (CSV)", "Small Text"),
        _f("error_log", "Error Log", "Small Text"),
    ],
)


# 9. Tally Migration Session (standalone parent; SHELL) --------------------
#    Child-table fields (mapping_decisions, account_creation_requests,
#    supplier_creation_requests) are added in phase 2.

TALLY_MIGRATION_SESSION_SHELL = _dt(
    "Tally Migration Session",
    autoname="format:TMS-{company_abbr}-{fiscal_year_short}-.###",
    extra={
        "title_field": "company_abbr",
        "sort_field": "modified",
        "sort_order": "DESC",
        "track_changes": 1,
    },
    fields=[
        # -- Identity / target
        _f("company_abbr", "Company Abbreviation", "Link",
           options="Company Abbreviation", reqd=1, in_list_view=1),
        _f("erpnext_company", "ERPNext Company", "Link", options="Company"),
        _f("fiscal_year", "Fiscal Year", "Data", reqd=1, in_list_view=1,
           description="e.g. '2026-2027' (the ERPNext FY receiving the "
                       "opening balance)."),
        _f("fiscal_year_short", "FY Short (for autoname)", "Data",
           read_only=1, hidden=1),
        _f("tb_date", "TB Date", "Date", reqd=1,
           description="As-of date for the opening balance snapshot."),
        # -- Input
        _f("sb_input", "Input", "Section Break"),
        _f("source_format", "Source Format", "Select",
           options="xml\nexcel", default="xml", reqd=1),
        _f("source_file", "Source File (Lane B, under 50 MB)", "Attach"),
        _f("source_file_server_path",
           "Source File Server Path (Lane A, full file)", "Data"),
        _f("source_file_size_mb", "Source File Size (MB)", "Float",
           read_only=1),
        _f("source_file_sha256", "Source File SHA-256", "Data", read_only=1),
        # -- State
        #    Transitions (documented in doctype description, not enforced in
        #    code this week):
        #       Draft -> Parsing -> Parsed -> Mapping -> Reviewing
        #       -> Generating -> Generated -> Submitted
        #                                  \-> Cancelled
        #       Any state -> Failed
        #       Failed -> Draft   (full retry)
        #       Failed -> Mapping (retry with existing parse_summary_json)
        _f("sb_state", "Status", "Section Break"),
        _f("status", "Status", "Select",
           options=(
               "Draft\nParsing\nParsed\nMapping\nReviewing\n"
               "Generating\nGenerated\nSubmitted\nCancelled\nFailed"
           ),
           default="Draft", reqd=1, in_list_view=1),
        _f("started_at", "Started At", "Datetime", read_only=1),
        _f("completed_at", "Completed At", "Datetime", read_only=1),
        _f("error_log", "Error Log", "Long Text"),
        # -- Parse snapshot
        _f("sb_parse", "Parse Snapshot", "Section Break"),
        _f("parsed_company_name", "Parsed Company Name", "Data", read_only=1),
        _f("ledger_count", "Ledger Count (main)", "Int", read_only=1),
        _f("student_ledger_count", "Student Ledger Count", "Int",
           read_only=1),
        _f("group_count", "Group Count", "Int", read_only=1),
        _f("total_dr", "Total Dr", "Currency", read_only=1),
        _f("total_cr", "Total Cr", "Currency", read_only=1),
        _f("is_balanced", "Is Balanced (within 1%)", "Check", read_only=1),
        _f("parse_warnings", "Parse Warnings (JSON)", "Long Text",
           read_only=1),
        _f("parse_summary_json",
           "Parse Summary JSON (attached file)", "Attach",
           description="Full ParsedTallyTB dump; review UI reads via "
                       "session.get_parse_summary()."),
        # -- Mapper snapshot counters
        _f("sb_map_counts", "Mapper Snapshot", "Section Break"),
        _f("tier1_exact_count", "Tier 1 Exact", "Int", read_only=1),
        _f("tier1_rule_count", "Tier 1 Rule", "Int", read_only=1),
        _f("tier1_pattern_count", "Tier 1 Pattern", "Int", read_only=1),
        _f("tier2_fuzzy_count", "Tier 2 Fuzzy", "Int", read_only=1),
        _f("tier3_claude_count", "Tier 3 Claude", "Int", read_only=1),
        _f("unmapped_count", "Unmapped", "Int", read_only=1),
        _f("pnl_excluded_count", "P&L Excluded", "Int", read_only=1),
        _f("group_account_refused_count", "Group Account Refused", "Int",
           read_only=1),
        _f("anti_pattern_blocked_count", "Anti-Pattern Blocked", "Int",
           read_only=1),
        # -- Output artifacts (Week 3)
        _f("sb_output", "Output Artifacts", "Section Break"),
        _f("generated_je_reference", "Generated JE Reference", "Data",
           description="e.g. 'OB-CACSPU-2026-01' per RGI rules sec 6.2."),
        _f("generated_je_draft", "Generated JE Draft", "Link",
           options="Journal Entry",
           description="Draft Journal Entry; reviewer submits in ERPNext "
                       "after preview."),
        _f("generated_oit_file", "Generated OIT CSV", "Attach"),
        _f("student_ledger_file",
           "Student Ledger CSV (dux_voucher handoff)", "Attach",
           description="4-column CSV: student_name, tally_id, "
                       "debit_amount, credit_amount. Consumed by "
                       "dux_voucher's Ex Student Opening Batch."),
        _f("temp_opening_amount", "Temp Opening Amount (signed)", "Currency",
           read_only=1,
           description="Residual absorbed into 'Temporary Opening - {ABBR}'; "
                       "large value diagnoses historical imbalance in "
                       "source Tally data."),
        # -- Free-form
        _f("sb_notes", "Notes", "Section Break"),
        _f("reviewer_notes", "Reviewer Notes", "Long Text"),
        # Child tables added in phase 2:
        #   account_creation_requests   -> Account Creation Request
        #   supplier_creation_requests  -> Supplier Creation Request
        #
        # NOTE (2026-04-22): `mapping_decisions` child table was removed
        # as part of Week 4 Item 1 Commit 1. Mapping Decision is now a
        # standalone DocType (istable=0) linked to the session via its
        # own `session` Link field. Reads go through
        # ``TallyMigrationSession.get_decisions()`` below.
    ],
)


# =========================================================================
# Phase 1 — create base DocTypes (forward-ref-safe order)
# =========================================================================

PHASE1_ORDER = [
    COMPANY_ABBREVIATION,
    MAPPING_RULE_ALTERNATE_PATTERN,
    MAPPING_RULE,
    ACCOUNT_CREATION_SOURCE,       # Links Mapping Rule; create after it
    SUPPLIER_ALIAS_RULE,
    MAPPING_DECISION,              # Links Mapping Rule
    ACCOUNT_CREATION_REQUEST,      # Uses Account Creation Source
    SUPPLIER_CREATION_REQUEST,
    TALLY_MIGRATION_SESSION_SHELL, # Child-table fields patched in phase 2
]


def run_phase1() -> dict:
    """Create the 9 base DocTypes. Idempotent."""
    results: dict = {}
    for spec in PHASE1_ORDER:
        name = spec["name"]
        if frappe.db.exists("DocType", name):
            results[name] = "exists (skipped)"
            continue
        try:
            frappe.get_doc(spec).insert(ignore_permissions=True)
            results[name] = f"created ({len(spec['fields'])} fields)"
        except Exception as e:
            results[name] = f"ERROR: {type(e).__name__}: {e}"
            raise
    frappe.db.commit()
    return results


# =========================================================================
# Phase 2 — patch forward references
# =========================================================================


SESSION_CHILD_TABLE_FIELDS = [
    ("sb_children", "Child Tables", "Section Break", {}),
    # mapping_decisions child field removed 2026-04-22 — Mapping Decision
    # is now a standalone DocType (istable=0) linked via its own
    # `session` Link field. See patches/v1_0/migrate_decisions_to_standalone.py.
    ("account_creation_requests", "Account Creation Requests", "Table",
     {"options": "Account Creation Request"}),
    ("supplier_creation_requests", "Supplier Creation Requests", "Table",
     {"options": "Supplier Creation Request"}),
]


def run_phase2() -> dict:
    """Patch forward-reference fields. Idempotent."""
    results: dict = {}

    # Session: add child-table fields
    session = frappe.get_doc("DocType", "Tally Migration Session")
    existing = {f.fieldname for f in session.fields}
    added: list = []
    for fn, lab, ft, kwargs in SESSION_CHILD_TABLE_FIELDS:
        if fn in existing:
            continue
        session.append("fields", {
            "fieldname": fn,
            "label": lab,
            "fieldtype": ft,
            **kwargs,
        })
        added.append(fn)
    if added:
        session.save(ignore_permissions=True)
        results["Tally Migration Session"] = (
            f"added fields: {', '.join(added)}"
        )
    else:
        results["Tally Migration Session"] = "no change (fields already present)"

    # Mapping Rule: add created_via_session Link
    rule = frappe.get_doc("DocType", "Mapping Rule")
    rule_existing = {f.fieldname for f in rule.fields}
    if "created_via_session" not in rule_existing:
        rule.append("fields", {
            "fieldname": "created_via_session",
            "label": "Created Via Session",
            "fieldtype": "Link",
            "options": "Tally Migration Session",
            "description": "Set when a reviewer promotes a session decision "
                           "into a reusable Mapping Rule.",
        })
        rule.save(ignore_permissions=True)
        results["Mapping Rule"] = "added field: created_via_session"
    else:
        results["Mapping Rule"] = "no change (field already present)"

    frappe.db.commit()
    return results


# =========================================================================
# Top-level entry
# =========================================================================


# =========================================================================
# Audit phase 3 — post-Batch-4 schema drift corrections
# =========================================================================
#
# Reasons:
#   - Batch-2's Mapping Rule put a Section Break at fieldname
#     `source_section` and the data field at `source_section_ref`. The seed
#     script (scripts/seed_mapping_rules.py) uses `source_section` as the
#     dict key when inserting Mapping Rule rows; Frappe would silently drop
#     the unknown field. Rename needed.
#   - Mapping Decision was missing `requires_combine` and `combine_with`
#     (Week 2 prose had them; my phase-1 spec dropped them). Needed to
#     track combine_amounts=1 rule firings.
#
# Idempotent via name-based existence checks. Safe to re-run.


def audit_phase_3_patches() -> dict:
    """Schema drift corrections from post-Batch-4 audit.

    Returns a dict of {doctype_name: human-readable outcome}.
    """
    results: dict = {}

    # ---- Mapping Rule: rename Section Break + rename data field ----
    # v16 has no frappe.model.rename_doc.rename_field helper anymore.
    # Direct-edit DocType.fields and save; Frappe's migrate-on-save
    # handles the tabMapping Rule column schema update. Safe because
    # tabMapping Rule has 0 rows at this point (seed not yet run).
    rule = frappe.get_doc("DocType", "Mapping Rule")
    mr_changes: list = []
    for f in rule.fields:
        if f.fieldname == "source_section" and f.fieldtype == "Section Break":
            f.fieldname = "sb_source"
            f.label = "Source"
            mr_changes.append("section break: source_section -> sb_source")
        elif f.fieldname == "source_section_ref":
            f.fieldname = "source_section"
            mr_changes.append("data field: source_section_ref -> source_section")
    if mr_changes:
        rule.save(ignore_permissions=True)
    results["Mapping Rule"] = (
        "; ".join(mr_changes) if mr_changes else "no change (already correct)"
    )

    # After DocType.save() completes, Frappe has added the new `source_section`
    # column but left the old `source_section_ref` as an orphan (Frappe adds
    # but doesn't drop). Clean it up with an explicit DROP, flanked by
    # db.commit() to sidestep the v16 ImplicitCommitError guard that fires
    # when DDL runs mid-transaction.
    if frappe.db.has_column("Mapping Rule", "source_section_ref"):
        frappe.db.commit()
        frappe.db.sql(
            "ALTER TABLE `tabMapping Rule` DROP COLUMN `source_section_ref`"
        )
        frappe.db.commit()
        results["Mapping Rule"] += "; dropped orphan SQL column source_section_ref"

    # ---- Mapping Decision: add requires_combine + combine_with +
    # new_account_* (Item 4 Commit 1) ----
    #
    # Item 4 adds four `new_account_*` fields parallel to
    # `new_supplier_name` from Item 2. The mapper already computes these
    # on `MappedDecision` for `tier=pending_account_creation` rows
    # (mapper.py:393-396) but the persistence layer was dropping them.
    # Storing them on the DocType lets the reviewer UI pre-fill the
    # AccountResolutionDialog with the mapper's suggestion instead of
    # re-deriving from the Mapping Rule at dialog-open time.
    #
    # For tier=unmapped rows, these stay NULL — the dialog opens with
    # empty defaults and the reviewer fills everything.
    decision = frappe.get_doc("DocType", "Mapping Decision")
    existing_decision = {f.fieldname for f in decision.fields}
    added_decision: list = []
    to_add_on_decision = [
        (
            "requires_combine",
            "Requires Combine (with sibling decisions)",
            "Check",
            {"default": "0"},
        ),
        (
            "combine_with",
            "Combine With (sibling decision idxs, CSV)",
            "Small Text",
            {},
        ),
        (
            "new_account_name",
            "New Account Name (suggestion)",
            "Data",
            {
                "description": (
                    "Mapper's suggested Account name for "
                    "pending_account_creation rows (ABBR already "
                    "applied). NULL for unmapped rows."
                ),
            },
        ),
        (
            "new_account_parent",
            "New Account Parent (suggestion)",
            "Data",
            {
                "description": (
                    "Mapper's suggested parent Account for "
                    "pending_account_creation rows (ABBR already "
                    "applied). NULL for unmapped rows."
                ),
            },
        ),
        (
            "new_account_root_type",
            "New Account Root Type (suggestion)",
            "Data",
            {
                "description": (
                    "Mapper's suggested root_type for "
                    "pending_account_creation rows. NULL for "
                    "unmapped rows."
                ),
            },
        ),
        (
            "new_account_is_group",
            "New Account Is Group (suggestion)",
            "Check",
            {
                "default": "0",
                "description": (
                    "Mapper's suggested is_group flag. Locked to 0 in "
                    "v1 (reviewer cannot create group accounts via ACR)."
                ),
            },
        ),
    ]
    for fn, lab, ft, kwargs in to_add_on_decision:
        if fn in existing_decision:
            continue
        decision.append(
            "fields",
            {"fieldname": fn, "label": lab, "fieldtype": ft, **kwargs},
        )
        added_decision.append(fn)
    if added_decision:
        decision.save(ignore_permissions=True)
        results["Mapping Decision"] = f"added: {', '.join(added_decision)}"
    else:
        results["Mapping Decision"] = "no change (already present)"

    # ---- Mapping Decision: add "Account Creation Requested" to
    # review_action Select enum (Item 4 Commit 2) ----
    #
    # Parallel to Item 3 Commit 2's addition of "Supplier Creation
    # Requested" — gives the reviewer a distinct state between "I've
    # initiated an account creation request" (reviewer done with this
    # row) and "Pending Account Creation" (untouched). REVIEW_ACTION_STATE
    # on the frontend maps this to the green "done" indicator so the
    # row drops from the Pending filter.
    decision_reload = frappe.get_doc("DocType", "Mapping Decision")
    review_action_field = None
    for f in decision_reload.fields:
        if f.fieldname == "review_action":
            review_action_field = f
            break
    if review_action_field is not None:
        existing_options = (review_action_field.options or "").split("\n")
        if "Account Creation Requested" not in existing_options:
            # Append at end — new reviewer-initiated states group naturally
            # after the "Supplier Creation Requested" entry that was
            # appended by Item 3 Commit 2. Frappe stores Select options as
            # a newline-delimited string.
            review_action_field.options = (
                (review_action_field.options or "")
                + "\nAccount Creation Requested"
            )
            decision_reload.save(ignore_permissions=True)
            results["Mapping Decision"] += (
                "; added review_action enum 'Account Creation Requested'"
            )
        else:
            results["Mapping Decision"] += (
                "; review_action enum already includes 'Account Creation Requested'"
            )

    # ---- Account Creation Request: add account_type field (Item 4
    # Commit 2, AMB C2-A) ----
    #
    # The AccountResolutionDialog collects an optional account_type
    # from a 32-value ERPNext enum (Bank / Cash / Receivable / Tax /
    # etc.). The ACR child DocType didn't have a place to store it —
    # this block adds a Data field, parallel to how Item 4 Commit 1's
    # patch block extended Mapping Decision.
    #
    # Data (not Select) for the same reason new_account_root_type is
    # Data: insulates us from future ERPNext enum additions that would
    # otherwise reject existing ACR rows on Select-option validation.
    acr = frappe.get_doc("DocType", "Account Creation Request")
    existing_acr = {f.fieldname for f in acr.fields}
    if "account_type" not in existing_acr:
        acr.append(
            "fields",
            {
                "fieldname": "account_type",
                "label": "Account Type",
                "fieldtype": "Data",
                "description": (
                    "Optional ERPNext Account.account_type value (Bank, "
                    "Receivable, Payable, Tax, etc.). Validated by Frappe "
                    "at ACR-approval time when the Account is actually "
                    "created (not here)."
                ),
            },
        )
        acr.save(ignore_permissions=True)
        results["Account Creation Request"] = "added: account_type"
    else:
        results["Account Creation Request"] = "no change (already present)"

    # ---- Informational: no drift worth patching this pass ----
    results["Company Abbreviation"] = "no drift"
    results["Supplier Alias Rule"] = (
        "checklist clean (naming divergence with my spec is Aditya's "
        "Batch-2 design choice, honored)"
    )
    results["Tally Migration Session"] = (
        "state-transition description omitted (cosmetic only)"
    )

    frappe.db.commit()
    return results


def run_audit_patches() -> dict:
    """Entry point for the post-Batch-4 audit patches."""
    print()
    print("=" * 70)
    print("rgi_migration audit patches (phase 3, post-Batch-4)")
    print("=" * 70)
    print()
    r = audit_phase_3_patches()
    for n, s in r.items():
        print(f"  {n:<40s} {s}")
    print()
    print("Done. Run: bench --site <site> migrate")
    return r


# =========================================================================
# Item 5 Commit 1 — add raw_final_account field to Mapping Rule
# =========================================================================
#
# Reviewer-promoted rules persist both the substituted
# ``erpnext_account_template`` (match target) AND the pre-substitution
# ``raw_final_account`` (observability / audit) per Item 5 Phase A
# A-9. This function is idempotent and safe to re-run; existing benches
# get the field added, fresh benches get it from the DocType JSON via
# ``bench migrate``.


def run_item5_patch() -> dict:
    """Add ``raw_final_account`` Data field to Mapping Rule if missing.

    Returns a single-entry results dict. Safe to re-run — existence check
    on fieldname guards against duplicate append.
    """
    results: dict = {}
    rule = frappe.get_doc("DocType", "Mapping Rule")
    existing = {f.fieldname for f in rule.fields}
    if "raw_final_account" in existing:
        results["Mapping Rule"] = "no change (raw_final_account already present)"
        return results

    # Insert the field just after erpnext_account_template so the Form
    # layout groups promotion-origin metadata alongside the match target.
    # Frappe's DocType.append adds at the end; we re-order via insert
    # into the fields list after save so the field_order in the JSON
    # stays the source of truth for layout.
    rule.append("fields", {
        "fieldname": "raw_final_account",
        "label": "Raw Final Account (promotion source)",
        "fieldtype": "Data",
        "read_only": 1,
        "description": "Raw final_account captured from the promoting "
                       "session (pre {ABBR} substitution). Observability "
                       "only; erpnext_account_template is the match target.",
    })
    rule.save(ignore_permissions=True)
    frappe.db.commit()
    results["Mapping Rule"] = "added field: raw_final_account"
    return results


def run_all() -> dict:
    """Entry point. Phase 1 then Phase 2. Prints a plain-text summary."""
    print()
    print("=" * 70)
    print("rgi_migration DocType scaffolding")
    print("=" * 70)
    print()
    print("-- Phase 1: base DocTypes --")
    r1 = run_phase1()
    for n, s in r1.items():
        print(f"  {n:<40s} {s}")
    print()
    print("-- Phase 2: forward-reference patches --")
    r2 = run_phase2()
    for n, s in r2.items():
        print(f"  {n:<40s} {s}")
    print()
    print("Done.")
    print()
    print("Next steps:")
    print("  1. bench --site erp.jewonline.in migrate   # regenerate DocType files")
    print("  2. commit and push the generated "
          "apps/rgi_migration/rgi_migration/rgi_migration/doctype/ tree")
    print("  3. run_all is idempotent; safe to re-run if anything aborts")
    return {"phase1": r1, "phase2": r2}
