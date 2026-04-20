#!/usr/bin/env python
"""
seed_mapping_rules.py — seed the Mapping Rule DocType from RGI_Migration_Rules.md.

Schema contract: rule dicts key on 'source_section' (Data field).
A post-Batch-4 audit patch renamed the deployed Mapping Rule field from
'source_section_ref' to 'source_section' so the seed here and the DocType
stay aligned. See docs/mapper_design_notes.md "Schema mutation recipes"
for context. Do NOT "fix" this to source_section_ref based on older
DocType JSON dumps — that name is the stale one.


Seeds §4 CONFIRMED rules (§4.1–§4.9, §4.11–§4.19: 18 rules — §4.10
deprecated 2026-04, see below), plus one §3.3-derived rule (P&L A/c
routing), plus 3 §11 anti-patterns with specific Tally patterns.
Total: 22 rows (19 positive + 3 anti-pattern).

§4.10 Student Fee Outstanding is NOT seeded. Student receivables —
both per-student leaves and aggregate control accounts — are routed
by the PARSER to the students CSV (consumed by dux_voucher's Ex
Student Opening Batch). This is an architectural boundary between
rgi_migration and dux_voucher, not per-entity business logic. The
parser flags aggregate names via AGGREGATE_STUDENT_ACCOUNT_NAMES in
rgi_migration/parsers/tally_xml_parser.py. See
docs/mapper_design_notes.md §7 and docs/dux_voucher_integration.md.

Other §11 rows are either process rules (OIT/JE builder invariants) or structural
checks (Tier-1 code, not data) — see docs/mapper_design_notes.md §1 and §2.
Those, plus §5.6 TENTATIVE, are written to TENTATIVE_RULES.md at repo root.

Modes:
    --dry-run       Write plan JSON to docs/seed_plan.json. No Frappe required.
    --insert        Insert rows via frappe.get_doc. Requires `bench execute`
                    context (Frappe bench + installed rgi_migration app +
                    Mapping Rule DocType). Not yet usable — bench is scaffolded
                    in Week 2 step 1.
    --purge-seeded  (with --insert) Delete rows where created_from='seed'
                    before re-inserting. For dev re-seed cycles.

Idempotency: every row carries a `source_hash` derived from
(source_section, is_anti_pattern, tally_pattern). On re-run, updates mutable
fields on the existing row rather than duplicating. `times_applied`,
`last_applied_at`, `created_via_session` are preserved.

Companion doc: docs/mapper_design_notes.md.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

LOG = logging.getLogger("seed_mapping_rules")

REPO_ROOT = Path(__file__).resolve().parent.parent
RULES_MD = REPO_ROOT / "RGI_Migration_Rules.md"
TENTATIVE_MD = REPO_ROOT / "TENTATIVE_RULES.md"
DRY_RUN_PLAN = REPO_ROOT / "docs" / "seed_plan.json"
STUB_COA = REPO_ROOT / "rgi_migration" / "tests" / "fixtures" / "sample_cacspu_erpnext_coa.csv"


# ----------------------------------------------------------------------------
# Positive rules — §4.1 through §4.19 (all CONFIRMED per current RGI doc)
# ----------------------------------------------------------------------------

POSITIVE_RULES: list[dict[str, Any]] = [
    {
        # §4.1 — GHRIET and GHR College Of Engineering → single ERPNext branch
        "source_section": "§4.1",
        "rule_name": "GHRIET (For Women) / GHR College Of Engineering → GHRCE branch",
        "tally_pattern": "GHRIET (For Women)",
        "tally_match_mode": "exact_ci",
        "tally_pattern_alternates": [
            {"tally_pattern": "G H Raisoni Inst Of Engg & Tech For Women", "tally_match_mode": "exact_ci"},
            {"tally_pattern": "G H R College Of Engineering", "tally_match_mode": "exact_ci"},
            {"tally_pattern": "GH Raisoni College Of Engineering", "tally_match_mode": "exact_ci"},
        ],
        "applicable_root_type": "Any",
        "tally_parent_contains": None,
        "erpnext_account_template": "GH Raisoni College Of Engineering - {ABBR}",
        "combine_amounts": 1,
        "creates_erpnext_account": 0,
        "source_entities": "GHRCEMNDIP, GHRILS, ASSGHS, ASSCOE, ASSHST",
    },
    {
        # §4.2 — Caution Money → liability-side ERP account
        "source_section": "§4.2",
        "rule_name": "Caution Money £ (student deposits) → liability-side ERP account",
        "tally_pattern": "Caution Money £",
        "tally_match_mode": "exact_ci",
        "tally_pattern_alternates": [],
        "applicable_root_type": "Liability",
        "tally_parent_contains": "Current Liabilities",
        "erpnext_account_template": "Caution Money £ (Liability) - {ABBR}",
        "combine_amounts": 0,
        "creates_erpnext_account": 0,
        "source_entities": "GHRCE, GHRILS, ASSGHS, GHRCEMBA, GHRCEMNMBA, ASSHST",
    },
    {
        # §4.3 — Kitchen Crockery (and Tally typo variant) → Kitchen Equipment
        "source_section": "§4.3",
        "rule_name": "Kitchen Crockery → Kitchen Equipment (Club House)",
        "tally_pattern": "Kitchen Crockery",
        "tally_match_mode": "exact_ci",
        "tally_pattern_alternates": [
            {"tally_pattern": "Kitchen Croceery", "tally_match_mode": "exact_ci"},  # Tally source typo
        ],
        "applicable_root_type": "Asset",
        "tally_parent_contains": None,
        "erpnext_account_template": "Kitchen Equipment (Club House) - {ABBR}",
        "combine_amounts": 0,
        "creates_erpnext_account": 0,
        "source_entities": "ASSHST",
    },
    {
        # §4.4 — Security Dep With M S E B [Con No] / Deposit To M S E B
        "source_section": "§4.4",
        "rule_name": "Security Deposit MSEB (any connection-number variant)",
        "tally_pattern": r"^Security Dep With M S E B",  # strips trailing [Con No]
        "tally_match_mode": "regex",
        "tally_pattern_alternates": [
            {"tally_pattern": "Deposit To M S E B", "tally_match_mode": "exact_ci"},
        ],
        "applicable_root_type": "Asset",
        "tally_parent_contains": None,
        "erpnext_account_template": "Security Deposit MSEB - {ABBR}",
        "combine_amounts": 0,
        "creates_erpnext_account": 0,
        "source_entities": "GHRF, ASSHST",
    },
    {
        # §4.5 — FDR leaf mapping. §4.5 says "Canara Bank FDR" is the ERP GROUP
        # (non-postable); postable leaf is "FDR Canara Bank". Tier-1's group-account
        # validator (docs/mapper_design_notes.md §2(b)) catches mis-proposals at the
        # structural layer; this rule is the explicit positive redirect for Tally
        # ledgers named "Canara Bank FDR" so reviewers see the correct target
        # directly rather than "Pending Group Account Resolution".
        "source_section": "§4.5",
        "rule_name": "Canara Bank FDR (Tally) → FDR Canara Bank leaf (ERP)",
        "tally_pattern": "Canara Bank FDR",
        "tally_match_mode": "exact_ci",
        "tally_pattern_alternates": [],
        "applicable_root_type": "Asset",
        "tally_parent_contains": None,
        "erpnext_account_template": "FDR Canara Bank - {ABBR}",
        "combine_amounts": 0,
        "creates_erpnext_account": 0,
        "source_entities": "GHRCEMNDIP, GHRILS",
    },
    {
        # §4.6 — Unpaid Expenditure Account → Unpaid Expenditure Provision
        "source_section": "§4.6",
        "rule_name": "Unpaid Expenditure Account → Unpaid Expenditure Provision",
        "tally_pattern": "Unpaid Expenditure Account",
        "tally_match_mode": "exact_ci",
        "tally_pattern_alternates": [],
        "applicable_root_type": "Liability",
        "tally_parent_contains": None,
        "erpnext_account_template": "Unpaid Expenditure Provision - {ABBR}",
        "combine_amounts": 0,
        "creates_erpnext_account": 0,
        "source_entities": "Multiple entities",
    },
    {
        # §4.7 — Payable A/c → Payable Account (Tally abbreviates, ERP expands)
        "source_section": "§4.7",
        "rule_name": "Payable A/c → Payable Account (expand abbreviation)",
        "tally_pattern": "Payable A/c",
        "tally_match_mode": "exact_ci",
        "tally_pattern_alternates": [],
        "applicable_root_type": "Liability",
        "tally_parent_contains": None,
        "erpnext_account_template": "Payable Account - {ABBR}",
        "combine_amounts": 0,
        "creates_erpnext_account": 0,
        "source_entities": "Multiple entities",
    },
    {
        # §4.8 — Hostel Fee A/c → Hostel Fee Advance Payable
        # Paired with §11 R3 anti-pattern; anti-pattern owns creation request.
        "source_section": "§4.8",
        "rule_name": "Hostel Fee A/c (Tally BS liability) → Hostel Fee Advance Payable",
        "tally_pattern": "Hostel Fee A/c",
        "tally_match_mode": "exact_ci",
        "tally_pattern_alternates": [],
        "applicable_root_type": "Liability",
        "tally_parent_contains": "Current Liabilities",
        "erpnext_account_template": "Hostel Fee Advance Payable - {ABBR}",
        "combine_amounts": 0,
        "creates_erpnext_account": 0,  # §11 R3 anti-pattern owns creation
        "source_entities": "ASSHST",
    },
    {
        # §4.9 — Summer Term Exam Fee Collection → Summer Term Exam Fee Payable
        # Paired with §11 R2 anti-pattern; anti-pattern owns creation request.
        "source_section": "§4.9",
        "rule_name": "Summer Term Exam Fee Collection → Summer Term Exam Fee Payable",
        "tally_pattern": "Summer Term Exam Fee Collection",
        "tally_match_mode": "exact_ci",
        "tally_pattern_alternates": [],
        "applicable_root_type": "Liability",
        "tally_parent_contains": "Sundry Creditors",
        "erpnext_account_template": "Summer Term Exam Fee Payable - {ABBR}",
        "combine_amounts": 0,
        "creates_erpnext_account": 0,  # §11 R2 anti-pattern owns creation
        "source_entities": "ASSCOE",
    },
    # §4.10 Student Fee Outstanding — DEPRECATED 2026-04. Removed from the
    # seed library entirely. Architectural boundary, not per-entity business
    # logic: student receivables (both per-student leaves and aggregate
    # control accounts) are identified by the PARSER via is_student_ledger=True
    # and routed to dux_voucher's Ex Student Opening Batch via the CSV
    # handoff. The parser flags aggregate names via AGGREGATE_STUDENT_ACCOUNT_NAMES
    # in rgi_migration/parsers/tally_xml_parser.py — add new aggregate names
    # there, not here. See RGI_Migration_Rules.md §4.10 and
    # docs/mapper_design_notes.md §7.
    {
        # §4.11 — Computer & Accessories (+ Purchase A/c variant) combined
        "source_section": "§4.11",
        "rule_name": "Computer & Accessories (+ Purchase A/c variant) → combined",
        "tally_pattern": "Computer & Accessories",
        "tally_match_mode": "exact_ci",
        "tally_pattern_alternates": [
            {"tally_pattern": "Computer & Accessories Purchase A/c", "tally_match_mode": "exact_ci"},
        ],
        "applicable_root_type": "Asset",
        "tally_parent_contains": "Fixed Assets",
        "erpnext_account_template": "Computer & Accessories - {ABBR}",
        "combine_amounts": 1,
        "creates_erpnext_account": 0,
        "source_entities": "Multiple entities",
    },
    {
        # §4.12 — Building Revaluation Reserve (Old) (equity side only; asset-side
        # variant is §10 Q4, unresolved — let it emerge as Tier-1 miss on real data)
        "source_section": "§4.12",
        "rule_name": "Building Revaluation Reserve (Old) → equity reserve",
        "tally_pattern": "Building Revaluation Reserve (Old)",
        "tally_match_mode": "exact_ci",
        "tally_pattern_alternates": [],
        "applicable_root_type": "Equity",
        "tally_parent_contains": None,
        "erpnext_account_template": "Building Revaluation Reserve ( Old ) - {ABBR}",
        "combine_amounts": 0,
        "creates_erpnext_account": 1,
        "new_account_name_template": "Building Revaluation Reserve ( Old ) - {ABBR}",
        "new_account_parent": "Reserves & Surplus",
        "new_account_root_type": "Equity",
        "new_account_is_group": 0,
        "source_entities": "GHRCE, ASSHST, ASSGHS",
    },
    {
        # §4.13 — Office equipment (+ Purchase variant) combined
        "source_section": "§4.13",
        "rule_name": "Office equipment / Office Equipment Purchase → combined",
        "tally_pattern": "Office equipment",
        "tally_match_mode": "exact_ci",
        "tally_pattern_alternates": [
            {"tally_pattern": "Office Equipment Purchase", "tally_match_mode": "exact_ci"},
        ],
        "applicable_root_type": "Asset",
        "tally_parent_contains": "Fixed Assets",
        "erpnext_account_template": "Office equipment - {ABBR}",
        "combine_amounts": 1,
        "creates_erpnext_account": 0,
        "source_entities": "ASSGHS",
    },
    {
        # §4.14 — ASS Boys Hostel branch
        "source_section": "§4.14",
        "rule_name": "ASS BOYS HOSTEL → ASS Boys Hostel branch",
        "tally_pattern": "ASS BOYS HOSTEL",
        "tally_match_mode": "exact_ci",
        "tally_pattern_alternates": [],
        "applicable_root_type": "Any",
        "tally_parent_contains": None,
        "erpnext_account_template": "ASS Boys Hostel - {ABBR}",
        "combine_amounts": 0,
        "creates_erpnext_account": 0,
        "source_entities": "ASSGHS",
    },
    {
        # §4.15 — G H R Institute Of Life Science branch
        "source_section": "§4.15",
        "rule_name": "G H R Institute Of Life Science → GHR Institute Of Life Science branch",
        "tally_pattern": "G H R Institute Of Life Science",
        "tally_match_mode": "exact_ci",
        "tally_pattern_alternates": [],
        "applicable_root_type": "Any",
        "tally_parent_contains": None,
        "erpnext_account_template": "GHR Institute Of Life Science - {ABBR}",
        "combine_amounts": 0,
        "creates_erpnext_account": 0,
        "source_entities": "ASSGHS, ASSHST",
    },
    {
        # §4.16 — Badminton Court → Badminton Court (Club House)
        "source_section": "§4.16",
        "rule_name": "Badminton Court → Badminton Court (Club House)",
        "tally_pattern": "Badminton Court",
        "tally_match_mode": "exact_ci",
        "tally_pattern_alternates": [],
        "applicable_root_type": "Asset",
        "tally_parent_contains": None,
        "erpnext_account_template": "Badminton Court (Club House) - {ABBR}",
        "combine_amounts": 0,
        "creates_erpnext_account": 0,
        "source_entities": "ASSGHS",
    },
    {
        # §4.17 — FD [Autonomous Exam Fund] (conditional creation; rule body says "new account")
        "source_section": "§4.17",
        "rule_name": "Fixed Deposit [Autonomous Exam Fund] → FD [Autonomous Exam Fund]",
        "tally_pattern": "Fixed Deposit [Autonomous Exam Fund]",
        "tally_match_mode": "exact_ci",
        "tally_pattern_alternates": [],
        "applicable_root_type": "Asset",
        "tally_parent_contains": None,
        "erpnext_account_template": "FD [Autonomous Exam Fund] - {ABBR}",
        "combine_amounts": 0,
        "creates_erpnext_account": 1,
        "new_account_name_template": "FD [Autonomous Exam Fund] - {ABBR}",
        "new_account_parent": "Fixed Deposits with Bank",
        "new_account_root_type": "Asset",
        "new_account_is_group": 0,
        "source_entities": "ASSCOE",
    },
    {
        # §4.18 — Student Receivable Cybervidya
        "source_section": "§4.18",
        "rule_name": "STUDENT RECEIVABLE CYBERVIDYA → Student Receivable Cybervidya",
        "tally_pattern": "STUDENT RECEIVABLE CYBERVIDYA",
        "tally_match_mode": "exact_ci",
        "tally_pattern_alternates": [],
        "applicable_root_type": "Asset",
        "tally_parent_contains": None,
        "erpnext_account_template": "Student Receivable Cybervidya - {ABBR}",
        "combine_amounts": 0,
        "creates_erpnext_account": 0,
        "source_entities": "ASSCOE",
    },
    {
        # §4.19 — Receivable Account (Others) — ERP uses spaces around parens
        "source_section": "§4.19",
        "rule_name": "Receivable Account (Others) → ERP formatting with spaces around parens",
        "tally_pattern": "Receivable Account (Others)",
        "tally_match_mode": "exact_ci",
        "tally_pattern_alternates": [],
        "applicable_root_type": "Asset",
        "tally_parent_contains": None,
        "erpnext_account_template": "Receivable Account ( Others ) - {ABBR}",
        "combine_amounts": 0,
        "creates_erpnext_account": 0,
        "source_entities": "ASSHST",
    },
    {
        # §3.3 + derived — Tally's Profit & Loss A/c (current-year system
        # accumulator) and Income Expenditure A/c (user-created prior-year
        # reserve) are distinct in Tally but collapse to a single retained-
        # earnings account in ERPNext. The Tally year-end-close machinery
        # that distinguishes them isn't meaningful in ERPNext's COA, so
        # both land in `Income Expenditure A/c - {ABBR}` with combined
        # amounts. See docs/mapper_design_notes.md §4 "P&L A/c routing".
        "source_section": "§3.3 + derived",
        "rule_name": "Profit & Loss A/c + Income Expenditure A/c → combined I&E",
        "tally_pattern": "Profit & Loss A/c",
        "tally_match_mode": "exact_ci",
        "tally_pattern_alternates": [
            {"tally_pattern": "Income Expenditure A/c", "tally_match_mode": "exact_ci"},
        ],
        "applicable_root_type": "Any",
        "tally_parent_contains": None,
        "erpnext_account_template": "Income Expenditure A/c - {ABBR}",
        "combine_amounts": 1,
        "creates_erpnext_account": 0,
        "source_entities": "CACSPU",
    },
]


# ----------------------------------------------------------------------------
# Anti-patterns — §11 rows 2, 3, 4 only. Rows 5/6/7/10 are structural checks;
# rows 1/8/9 are process rules. See TENTATIVE_RULES.md + mapper_design_notes.md.
# ----------------------------------------------------------------------------

ANTI_PATTERN_RULES: list[dict[str, Any]] = [
    {
        # §11 R2 — Summer Term Exam Fee Collection: COA places under Indirect Incomes (P&L)
        "source_section": "§11 R2",
        "rule_name": "Refuse same-name match: Summer Term Exam Fee Collection (ERP is P&L)",
        "tally_pattern": "Summer Term Exam Fee Collection",
        "tally_match_mode": "exact_ci",
        "tally_pattern_alternates": [],
        "applicable_root_type": "Liability",
        "tally_parent_contains": None,
        "erpnext_account_template": None,
        "combine_amounts": 0,
        "forbidden_erpnext_template": "Summer Term Exam Fee Collection - {ABBR}",
        "anti_pattern_reason": (
            "Exact-name match would steer a Tally BS liability (Sundry Creditors / "
            "Current Liabilities) onto the ERP account of the same name, which is "
            "wrongly placed under Indirect Incomes (P&L). Posting there would (a) "
            "misclassify the balance as revenue and (b) be rejected by ERPNext's "
            "Opening Entry journal at submission. Steer to the suggested liability "
            "alternative."
        ),
        "suggested_alternative_template": "Summer Term Exam Fee Payable - {ABBR}",
        "creates_erpnext_account": 1,
        "new_account_name_template": "Summer Term Exam Fee Payable - {ABBR}",
        "new_account_parent": "Other Liabilities",
        "new_account_root_type": "Liability",
        "new_account_is_group": 0,
        "source_entities": "ASSCOE",
    },
    {
        # §11 R3 — Hostel Fee A/c: COA places under Direct Incomes (P&L)
        "source_section": "§11 R3",
        "rule_name": "Refuse same-name match: Hostel Fee A/c (ERP is P&L)",
        "tally_pattern": "Hostel Fee A/c",
        "tally_match_mode": "exact_ci",
        "tally_pattern_alternates": [],
        "applicable_root_type": "Liability",
        "tally_parent_contains": None,
        "erpnext_account_template": None,
        "combine_amounts": 0,
        "forbidden_erpnext_template": "Hostel Fee A/c - {ABBR}",
        "anti_pattern_reason": (
            "Exact-name match would steer a Tally BS liability (Current Liabilities) "
            "onto the ERP account of the same name, which is wrongly placed under "
            "Direct Incomes (P&L). Posting there would misclassify student advances "
            "as revenue and be rejected by ERPNext's Opening Entry journal at "
            "submission. Steer to the suggested liability alternative."
        ),
        "suggested_alternative_template": "Hostel Fee Advance Payable - {ABBR}",
        "creates_erpnext_account": 1,
        "new_account_name_template": "Hostel Fee Advance Payable - {ABBR}",
        "new_account_parent": "Liability For Students",
        "new_account_root_type": "Liability",
        "new_account_is_group": 0,
        "source_entities": "ASSHST",
    },
    {
        # §11 R4 — Caution Money £: COA has both an asset-side and a liability-side
        # account; student deposits must go to the liability version.
        "source_section": "§11 R4",
        "rule_name": "Refuse Caution Money £ asset-side match (student deposits are BS liability)",
        "tally_pattern": "Caution Money £",
        "tally_match_mode": "exact_ci",
        "tally_pattern_alternates": [],
        "applicable_root_type": "Liability",
        "tally_parent_contains": None,
        "erpnext_account_template": None,
        "combine_amounts": 0,
        "forbidden_erpnext_template": "Caution Money £ - {ABBR}",
        "anti_pattern_reason": (
            "Exact-name match would steer a Tally student-deposit liability onto "
            "the ERP 'Caution Money £' account placed under Loans & Advances "
            "(Asset) — wrong BS side. Student caution money is a liability held "
            "by the entity, not a receivable. Steer to the correctly-classified "
            "liability version."
        ),
        "suggested_alternative_template": "Caution Money £ (Liability) - {ABBR}",
        "creates_erpnext_account": 0,  # target exists per §4.2; pure steer
        "source_entities": "GHRCE, GHRILS, ASSGHS, GHRCEMBA, GHRCEMNMBA, ASSHST",
    },
]


# ----------------------------------------------------------------------------
# Skipped / tentative items — written to TENTATIVE_RULES.md
# ----------------------------------------------------------------------------

SKIPPED_ITEMS: list[dict[str, str]] = [
    {
        "section": "§11 R1",
        "title": "Post to Sundry Creditors directly via JE without party tagging",
        "category": "process_rule",
        "reason": "Not a Tally→ERP name mapping; governed by the OIT/JE builder "
                  "per §5.1–§5.3 (net-Cr → OIT, net-Dr → party-wise JE).",
    },
    {
        "section": "§11 R5",
        "title": "Post group account as opening balance target",
        "category": "structural_check",
        "reason": "Encoded as the Tier-1 group-account refusal validator. "
                  "See docs/mapper_design_notes.md §2(b).",
    },
    {
        "section": "§11 R6",
        "title": "Include P&L accounts in opening JE",
        "category": "structural_check",
        "reason": "Encoded as the Tier-1 P&L root-type exclusion + JE-builder "
                  "filter + ERPNext Opening Entry submission check (three layers). "
                  "See docs/mapper_design_notes.md §2(a).",
    },
    {
        "section": "§11 R7",
        "title": "Post Purchase Accounts (Tally group) as asset",
        "category": "structural_check",
        "reason": "Parser tags every Purchase Accounts leaf as root_type=Expense; "
                  "JE builder filters it out; ERPNext Opening Entry rejects P&L at "
                  "submission. The anti-pattern would guard an impossible failure "
                  "mode. See docs/mapper_design_notes.md §1 Worked Example A.",
    },
    {
        "section": "§11 R8",
        "title": "Net Dr vendor in OIT",
        "category": "process_rule",
        "reason": "OIT builder invariant per §5.3 (net-Dr vendors go through "
                  "party-wise JE, not OIT). Not mapping data.",
    },
    {
        "section": "§11 R9",
        "title": "Post parent group total AND child rows (double count)",
        "category": "process_rule",
        "reason": "JE builder emits only leaves per §7.4; group totals are never "
                  "posted. Not mapping data.",
    },
    {
        "section": "§11 R10",
        "title": "Use FDR Canara Bank group for FDR posting",
        "category": "structural_check",
        "reason": "Encoded as the Tier-1 group-account refusal validator — the same "
                  "code path that catches any group-account proposal, not just the "
                  "FDR case. See docs/mapper_design_notes.md §1 Worked Example B "
                  "and §2(b).",
    },
    {
        "section": "§5.6",
        "title": "Cash Purchases in Sundry Creditors",
        "category": "tentative_out_of_scope",
        "reason": "Status=TENTATIVE. §5 is OIT/party process rules, not a "
                  "Mapping Rule data source. Revisit during reviewer promotion.",
    },
]


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def source_hash(section: str, is_anti_pattern: bool, tally_pattern: str) -> str:
    key = f"{section}|{int(is_anti_pattern)}|{tally_pattern}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def finalize(rule: dict[str, Any], *, is_anti_pattern: bool) -> dict[str, Any]:
    r = dict(rule)
    r["doctype"] = "Mapping Rule"
    r["is_anti_pattern"] = 1 if is_anti_pattern else 0
    # Per-rule override via "status" key wins (e.g. §4.10 ships as paused);
    # anything without an explicit status defaults to confirmed.
    r.setdefault("status", "confirmed")
    r.setdefault("applies_to_entity_types", "*")
    r.setdefault("created_from", "seed")
    r["source_hash"] = source_hash(r["source_section"], is_anti_pattern, r["tally_pattern"])
    return r


def read_stub_coa_groups(path: Path) -> set[str]:
    """Return entity-agnostic group names from the stub COA (strips ' - {ABBR}')."""
    groups: set[str] = set()
    if not path.exists():
        return groups
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("is_group", "0").strip() == "1":
                name = row["account_name"]
                base = name.rsplit(" - ", 1)[0] if " - " in name else name
                groups.add(base)
    return groups


def verify_coa_parents(rules: list[dict[str, Any]]) -> dict[str, Any]:
    required: set[str] = set()
    for r in rules:
        if r.get("creates_erpnext_account"):
            p = r.get("new_account_parent")
            if p:
                required.add(p)
    groups = read_stub_coa_groups(STUB_COA)
    missing = sorted(p for p in required if p not in groups)
    result: dict[str, Any] = {
        "stub_coa": str(STUB_COA.relative_to(REPO_ROOT)) if STUB_COA.exists() else None,
        "required": sorted(required),
        "missing": missing,
    }
    if not STUB_COA.exists():
        result["note"] = "Stub COA not found; could not verify."
    return result


def verify_rules_md_consistency(rules_md: Path, plan: dict[str, Any]) -> list[str]:
    """Cross-check that every §4.X source_section referenced by a seeded rule
    appears in RGI_Migration_Rules.md with Status: CONFIRMED."""
    warnings: list[str] = []
    if not rules_md.exists():
        return [f"RGI_Migration_Rules.md not found at {rules_md}"]
    text = rules_md.read_text(encoding="utf-8")
    sections: dict[str, str] = {}
    for m in re.finditer(r"### (\d+\.\d+)[^\n]*\n(.*?)(?=\n### |\n---\n|\Z)",
                         text, flags=re.DOTALL):
        num, body = m.group(1), m.group(2)
        sm = re.search(r"\*\*Status:\*\*\s*(\w+)", body)
        sections[f"§{num}"] = sm.group(1).upper() if sm else "UNKNOWN"

    for r in plan["positive_rules"]:
        sec = r["source_section"]
        if not sec.startswith("§4"):
            continue  # only §4 gets this consistency check; §11 is parsed from a table
        if sec not in sections:
            warnings.append(f"{sec} referenced by seed but not found in rules doc")
        elif sections[sec] != "CONFIRMED":
            warnings.append(f"{sec} in seed expects CONFIRMED; rules doc says {sections[sec]}")
    return warnings


def build_plan() -> dict[str, Any]:
    positive = [finalize(r, is_anti_pattern=False) for r in POSITIVE_RULES]
    anti = [finalize(r, is_anti_pattern=True) for r in ANTI_PATTERN_RULES]
    all_rules = positive + anti
    plan: dict[str, Any] = {
        "total": len(all_rules),
        "positive_count": len(positive),
        "anti_pattern_count": len(anti),
        "creates_erpnext_account_count": sum(1 for r in all_rules if r.get("creates_erpnext_account")),
        "positive_rules": positive,
        "anti_pattern_rules": anti,
        "skipped": SKIPPED_ITEMS,
        "coa_parent_check": verify_coa_parents(all_rules),
    }
    return plan


def write_tentative_md(items: list[dict[str, str]], path: Path) -> None:
    by_cat: dict[str, list[dict[str, str]]] = {}
    for it in items:
        by_cat.setdefault(it["category"], []).append(it)

    lines: list[str] = [
        "# Tentative / Skipped Rules",
        "",
        "Items from `RGI_Migration_Rules.md` that `scripts/seed_mapping_rules.py`",
        "did NOT insert into the `Mapping Rule` DocType. Each carries a documented",
        "reason (structural check, process rule, or genuinely tentative). Promote",
        "manually during reviewer sign-off in a session if needed.",
        "",
        "Companion: [`docs/mapper_design_notes.md`](docs/mapper_design_notes.md) —",
        "the anti-pattern seeding test and Tier-1 structural-check list explain",
        "why several §11 rows are code, not data.",
        "",
    ]
    titles = {
        "structural_check": "Structural checks (encoded in Tier-1 code, not data)",
        "process_rule": "Process rules (OIT/JE builder invariants, not mapping data)",
        "tentative_out_of_scope": "Tentative / out-of-scope for §4 seed",
    }
    for cat in ["structural_check", "process_rule", "tentative_out_of_scope"]:
        block = by_cat.get(cat, [])
        if not block:
            continue
        lines.append(f"## {titles[cat]}")
        lines.append("")
        for it in block:
            lines.append(f"### {it['section']} — {it['title']}")
            lines.append("")
            lines.append(it["reason"])
            lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def dry_run(plan: dict[str, Any]) -> None:
    DRY_RUN_PLAN.parent.mkdir(parents=True, exist_ok=True)
    DRY_RUN_PLAN.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Dry-run: {plan['total']} Mapping Rule rows "
          f"({plan['positive_count']} positive + {plan['anti_pattern_count']} anti-pattern).")
    print(f"  creates_erpnext_account=1: {plan['creates_erpnext_account_count']} rows")
    pc = plan["coa_parent_check"]
    if pc.get("note"):
        print(f"  COA parent check: {pc['note']}")
    elif pc["missing"]:
        print(f"  COA parent check: MISSING {pc['missing']!r} — creation requests WOULD FAIL validation.")
    else:
        print(f"  COA parent check: all {len(pc['required'])} required parents present in stub.")
    print(f"  Skipped: {len(plan['skipped'])} items -> {TENTATIVE_MD.relative_to(REPO_ROOT)}")
    print(f"  Plan JSON: {DRY_RUN_PLAN.relative_to(REPO_ROOT)}")


def validate_deployed_schema(plan: dict[str, Any]) -> list[str]:
    """Preview check — every dict key in the plan's rules must resolve to a
    real field on the deployed Mapping Rule DocType.

    Returns a list of error strings (empty list = OK). Requires Frappe
    context. Catches the class of bug the Week-3 audit surfaced, where
    a spec key like `source_section` would silently drop if the
    DocType's data field was actually named `source_section_ref`.
    """
    import frappe  # type: ignore[import]

    deployed_fields = {
        f.fieldname
        for f in frappe.get_doc("DocType", "Mapping Rule").fields
    }
    # Frappe implicit / meta fields the ORM accepts on insert
    implicit = {
        "doctype", "name", "parent", "parenttype", "parentfield",
        "idx", "owner", "creation", "modified", "modified_by", "docstatus",
    }
    known = deployed_fields | implicit

    errors: list[str] = []
    for cat in ("positive_rules", "anti_pattern_rules"):
        for rule in plan[cat]:
            unknown = [k for k in rule.keys() if k not in known]
            if unknown:
                errors.append(
                    f"  {rule['source_section']} ({rule['rule_name']}): "
                    f"unknown field keys {unknown}"
                )
    return errors


def real_seed(plan: dict[str, Any], purge: bool = False) -> dict[str, Any]:
    """Insert all rules into the Mapping Rule DocType, idempotent by
    source_hash. Returns a counts dict (inserted / skipped / errors).

    Requires Frappe context. Invoke via bench console heredoc (preferred
    over bench execute per docs/mapper_design_notes.md §5).
    """
    import frappe  # type: ignore[import]

    summary: dict[str, Any] = {
        "inserted": 0,
        "skipped": 0,
        "errors": 0,
        "error_details": [],
    }

    if purge:
        purged = frappe.db.count("Mapping Rule", {"created_from": "seed"})
        if purged:
            frappe.db.delete("Mapping Rule", {"created_from": "seed"})
            frappe.db.commit()
            print(f"PURGED {purged} rows where created_from='seed'")

    all_rules = plan["positive_rules"] + plan["anti_pattern_rules"]

    for rule in all_rules:
        sh = rule["source_hash"]
        rule_name = rule["rule_name"]
        existing = frappe.db.exists("Mapping Rule", {"source_hash": sh})
        if existing:
            print(f"SKIP  {sh[:8]}  {rule_name}  (exists as {existing})")
            summary["skipped"] += 1
            continue
        try:
            doc = frappe.get_doc(rule).insert(ignore_permissions=True)
            print(f"OK    {sh[:8]}  {rule_name}  -> {doc.name}")
            summary["inserted"] += 1
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            print(f"ERROR {sh[:8]}  {rule_name}: {msg}")
            summary["errors"] += 1
            summary["error_details"].append({
                "source_hash": sh, "rule_name": rule_name, "error": msg,
            })

    frappe.db.commit()
    print(
        f"\nSummary: inserted={summary['inserted']} "
        f"skipped={summary['skipped']} errors={summary['errors']}"
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="Write plan JSON only.")
    p.add_argument("--insert", action="store_true", help="Insert via Frappe (requires bench execute).")
    p.add_argument("--purge-seeded", action="store_true",
                   help="(with --insert) Delete created_from='seed' rows first.")
    p.add_argument("--rules-file", default=str(RULES_MD))
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    plan = build_plan()
    warnings = verify_rules_md_consistency(Path(args.rules_file), plan)
    for w in warnings:
        LOG.warning(w)
    if warnings and args.insert:
        print("Refusing to insert with rules-doc inconsistencies. Fix the doc, "
              "or pass --dry-run.", file=sys.stderr)
        return 2

    write_tentative_md(SKIPPED_ITEMS, TENTATIVE_MD)

    if args.dry_run or not args.insert:
        dry_run(plan)
        return 0

    # Preflight: verify schema alignment before attempting any insert
    try:
        schema_errors = validate_deployed_schema(plan)
    except ImportError:
        print("ERROR: --insert mode requires Frappe bench context. "
              "Run via bench console heredoc per "
              "docs/mapper_design_notes.md §5.", file=sys.stderr)
        return 2
    if schema_errors:
        print("Schema validation FAILED:", file=sys.stderr)
        for e in schema_errors:
            print(e, file=sys.stderr)
        return 2

    summary = real_seed(plan, purge=args.purge_seeded)
    return 0 if summary["errors"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
