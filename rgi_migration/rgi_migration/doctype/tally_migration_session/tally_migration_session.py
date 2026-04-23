# Copyright (c) 2026, Dux Digitech and contributors
# For license information, please see license.txt

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import frappe
from frappe.model.document import Document


class TallyMigrationSession(Document):
	def get_decisions(
		self,
		filters: dict | None = None,
		fields: list[str] | None = None,
		order_by: str = "creation",
	) -> list[dict[str, Any]]:
		"""Return ``Mapping Decision`` records linked to this session.

		Replaces the pre-Week-4 ``self.mapping_decisions`` child-table
		access pattern. ``Mapping Decision`` is now a standalone DocType
		(``istable=0``) with a ``session`` Link field back to
		``Tally Migration Session``. See
		``patches/v1_0/migrate_decisions_to_standalone.py`` for the
		migration that moved existing child rows.

		Args:
			filters: additional filter dict ``{"field": value}`` ANDed with
				the implicit ``session=self.name`` filter. Example:
				``{"review_action": "Pending"}``.
			fields: list of Mapping Decision fields to return. Defaults to
				``["*"]`` — callers trimming to a projection should pass an
				explicit list for network payload reduction on large
				sessions.
			order_by: SQL ORDER BY clause. Defaults to ``creation`` ascending
				so the reviewer sees decisions in the order the mapper
				emitted them.

		Returns:
			List of dicts (one per Mapping Decision). Empty list if the
			session has no decisions yet (e.g. before parse + map has run,
			or mid-migration between the schema flip and the patch).
		"""
		query_filters: dict[str, Any] = {"session": self.name}
		if filters:
			query_filters.update(filters)
		return frappe.get_all(
			"Mapping Decision",
			filters=query_filters,
			fields=fields or ["*"],
			order_by=order_by,
		)


# ---------------------------------------------------------------------------
# Item 2 Commit 2 — Run Mapper / Reset Parse
# ---------------------------------------------------------------------------
#
# Per docs/mapper_design_notes.md §9.1, Mapping Decision DocType is the
# authoritative persistence layer for reviewer decisions. This module owns
# the parse-and-map trigger that populates it from the session's source XML.
#
# Pure core lives in rgi_migration/session/parse_and_map.py — this file is
# the Frappe wrapper that assembles Frappe-sourced dependencies (COA,
# suppliers, rule source) and calls the pure core, then persists output.


# Status strings for the session state machine (see session JSON
# Select options). Keeping them as constants avoids typos drifting
# across status transitions and guards.
_STATUS_DRAFT = "Draft"
_STATUS_PARSING = "Parsing"
_STATUS_PARSED = "Parsed"
_STATUS_MAPPING = "Mapping"
_STATUS_REVIEWING = "Reviewing"
_STATUS_FAILED = "Failed"
_STATUS_SUBMITTED = "Submitted"

# Statuses from which Run Mapper is permitted. Reviewing / Generating /
# Generated / Submitted sessions must use Reset Parse first to drop
# existing decisions before re-running. Failed lets reviewers retry
# after fixing a configuration problem.
_RUN_MAPPER_ALLOWED_FROM = frozenset({_STATUS_DRAFT, _STATUS_FAILED})


@frappe.whitelist()
def run_mapper(session_name: str) -> dict[str, Any]:
	"""Parse + map the session's source file and persist Mapping Decisions.

	Atomic bulk insert — all decisions succeed or none do. On any failure
	the transaction rolls back and ``status`` lands at ``Failed`` with
	the error recorded on ``error_log``.

	Guards:
	  * session status must be Draft or Failed
	  * Company Abbreviation must resolve an active ERPNext Company
	  * source_file must be attached
	  * session must have zero existing Mapping Decisions (use
	    ``reset_parse`` to clear first)

	State transitions on success:
	  Draft/Failed -> Parsing -> Parsed -> Mapping -> Reviewing

	Returns a dict ``{"status": "ok", "decision_count": int,
	"summary": dict}`` on success. Raises ``frappe.ValidationError`` on
	guard failure, propagates the underlying exception on unexpected
	errors (with session ``status=Failed`` set first).
	"""
	from rgi_migration.session.parse_and_map import (
		decision_to_row_dict,
		index_ledgers_by_identity,
		run_parse_and_map,
		translate_matched_rule,
	)

	session = frappe.get_doc("Tally Migration Session", session_name)

	# ----- Guards -------------------------------------------------------

	if session.status not in _RUN_MAPPER_ALLOWED_FROM:
		frappe.throw(
			f"Cannot run mapper on session {session_name!r} with status "
			f"{session.status!r}. Use Reset Parse to drop existing "
			f"decisions and return to Draft first. Allowed statuses: "
			f"{sorted(_RUN_MAPPER_ALLOWED_FROM)}."
		)

	if not session.company_abbr:
		frappe.throw(
			f"Session {session_name!r} has no company_abbr set."
		)

	abbr_doc = frappe.get_doc("Company Abbreviation", session.company_abbr)
	if not abbr_doc.is_active:
		frappe.throw(
			f"Company Abbreviation {session.company_abbr!r} is not active."
		)
	if not abbr_doc.erpnext_company:
		frappe.throw(
			f"Company Abbreviation {session.company_abbr!r} has no linked "
			f"ERPNext Company. Set erpnext_company on the Company "
			f"Abbreviation row before running the mapper."
		)
	erpnext_company = abbr_doc.erpnext_company
	if not frappe.db.exists("Company", erpnext_company):
		frappe.throw(
			f"ERPNext Company {erpnext_company!r} (linked from Company "
			f"Abbreviation {session.company_abbr!r}) does not exist."
		)

	source_path = session.source_file_server_path or session.source_file
	if not source_path:
		frappe.throw(
			f"Session {session_name!r} has no source file "
			f"(source_file_server_path and source_file are both empty)."
		)

	existing_count = frappe.db.count(
		"Mapping Decision", {"session": session_name}
	)
	if existing_count > 0:
		frappe.throw(
			f"Session {session_name!r} already has {existing_count} "
			f"Mapping Decision rows. Use Reset Parse to drop them "
			f"before re-running the mapper."
		)

	# ----- Transition to Parsing + commit (visible to observers) --------

	session.status = _STATUS_PARSING
	session.started_at = frappe.utils.now_datetime()
	session.completed_at = None
	session.save(ignore_permissions=True)
	frappe.db.commit()

	try:
		# ---- Assemble Frappe-sourced dependencies ------------------
		coa = _load_coa(erpnext_company, session.company_abbr)
		suppliers = _load_suppliers()
		rule_source = _load_rule_source()
		entity_type = abbr_doc.entity_type or "*"

		# ---- Run pure-core pipeline ----------------------------------
		result = run_parse_and_map(
			source_path=str(source_path),
			source_format=session.source_format or "xml",
			abbr=session.company_abbr,
			entity_type=entity_type,
			coa=coa,
			suppliers=suppliers,
			rule_source=rule_source,
		)

		# ---- Transition to Parsed + persist summary ------------------
		session.reload()
		session.status = _STATUS_PARSED
		_populate_parse_summary(session, result)
		session.status = _STATUS_MAPPING
		session.save(ignore_permissions=True)

		# ---- Bulk insert Mapping Decisions (single transaction) ----
		# Pre-build source_section -> Mapping Rule.name map. The mapper's
		# MappedDecision.matched_rule carries the JSON rule's
		# source_section (e.g. "§4.6"); the DocType's matched_rule is a
		# Link → Mapping Rule expecting the doc's autoname (e.g.
		# "MR-00458"). Translate in the Frappe wrapper so pure-core
		# decision_to_row_dict stays Frappe-free. Lookup misses default
		# to None — the persistence path is audit-logged via tier +
		# matched_rule's source_section would have been, but a missing
		# translation is not a run-blocker.
		rule_name_by_section = _load_rule_name_by_section()

		ledger_index = index_ledgers_by_identity(result.tb)
		inserted = 0
		for d in result.decisions:
			key = (d.tally_name, d.tally_id or "")
			ledger = ledger_index.get(key)
			if ledger is None:
				raise RuntimeError(
					f"Ledger index miss for decision "
					f"{d.tally_name!r} (tally_id={d.tally_id!r}) — "
					f"mapper output diverged from parser output."
				)
			row = decision_to_row_dict(d, ledger, session.name)
			# Translate source_section → MR-xxxxx for Tier-1 entries;
			# pass tier2:* subtier labels through verbatim (Item 6).
			# anti_pattern_rule is always a Mapping Rule Link, so still
			# goes through the direct lookup (no tier2 semantics there).
			row["matched_rule"] = translate_matched_rule(
				row.get("matched_rule"), rule_name_by_section,
			)
			if row.get("anti_pattern_rule"):
				row["anti_pattern_rule"] = rule_name_by_section.get(
					row["anti_pattern_rule"]
				)
			frappe.get_doc(row).insert(ignore_permissions=True)
			inserted += 1

		# ---- Post-verify: row counts must align --------------------
		db_count = frappe.db.count(
			"Mapping Decision", {"session": session.name}
		)
		if db_count != inserted or inserted != len(result.decisions):
			raise RuntimeError(
				f"Row count mismatch: mapper emitted {len(result.decisions)}, "
				f"insert loop inserted {inserted}, SQL count reports "
				f"{db_count}. Rolling back."
			)

		# ---- Transition to Reviewing + finalize ---------------------
		session.reload()
		session.status = _STATUS_REVIEWING
		session.completed_at = frappe.utils.now_datetime()
		session.save(ignore_permissions=True)
		frappe.db.commit()

		return {
			"status": "ok",
			"decision_count": db_count,
			"summary": result.summary,
		}

	except Exception as exc:
		frappe.db.rollback()
		_record_failure(session_name, exc)
		raise


@frappe.whitelist()
def reset_parse(session_name: str) -> dict[str, Any]:
	"""Drop all Mapping Decisions for a session and reset status to Draft.

	Required before re-running the mapper after a source-file change or
	a failed partial run. Clears sb_parse + sb_map_counts fields; leaves
	``error_log`` intact for audit.

	Refuses on Submitted sessions (those are frozen by policy).

	Returns ``{"status": "ok", "deleted_count": int}``.
	"""
	session = frappe.get_doc("Tally Migration Session", session_name)
	if session.status == _STATUS_SUBMITTED:
		frappe.throw(
			f"Cannot reset session {session_name!r} — status is "
			f"Submitted. Submitted sessions are frozen."
		)

	deleted_count = frappe.db.count(
		"Mapping Decision", {"session": session_name}
	)
	if deleted_count:
		frappe.db.sql(
			"DELETE FROM `tabMapping Decision` WHERE session = %s",
			(session_name,),
		)

	# Item 4 Commit 3 (AMB-8 combined fix): sweep both child tables.
	# Orphan SCR / ACR rows from prior runs now reference deleted
	# Mapping Decision names; keeping them would leave stale entries
	# in the Process-* panel dialogs. Wipe-all-statuses semantic per
	# AMB C3-7 — Reset Parse is a "start over" action; Created /
	# Skipped / Failed rows are all equally stale once their source
	# MDs are gone.
	#
	# session.set("<table>", []) rather than row-by-row delete —
	# Frappe's save() below persists the child-row deletion in a
	# single transaction.
	scr_swept = len(session.supplier_creation_requests or [])
	acr_swept = len(session.account_creation_requests or [])
	session.set("supplier_creation_requests", [])
	session.set("account_creation_requests", [])

	# Clear all parse + map snapshot fields. error_log preserved.
	session.status = _STATUS_DRAFT
	session.started_at = None
	session.completed_at = None
	session.parsed_company_name = None
	session.ledger_count = 0
	session.student_ledger_count = 0
	session.group_count = 0
	session.total_dr = 0
	session.total_cr = 0
	session.is_balanced = 0
	session.parse_warnings = None
	session.tier1_exact_count = 0
	session.tier1_rule_count = 0
	session.tier1_pattern_count = 0
	session.tier2_fuzzy_count = 0
	session.tier3_claude_count = 0
	session.unmapped_count = 0
	session.pnl_excluded_count = 0
	session.group_account_refused_count = 0
	session.anti_pattern_blocked_count = 0
	session.save(ignore_permissions=True)
	frappe.db.commit()

	return {
		"status": "ok",
		"deleted_count": deleted_count,
		"scr_swept": scr_swept,
		"acr_swept": acr_swept,
	}


# ---------------------------------------------------------------------------
# Frappe-sourced dependency loaders — Account / Supplier / Rule
# ---------------------------------------------------------------------------


def _load_coa(erpnext_company: str, abbr: str) -> dict:
	"""Load the target company's Chart of Accounts as
	``{account_name: CoaAccount}``. Mirrors the shape expected by
	``rgi_migration.mapper.mapper.Mapper``.
	"""
	from rgi_migration.mapper.mapper import CoaAccount

	coa_rows = frappe.get_all(
		"Account",
		filters={"company": erpnext_company},
		fields=["name", "parent_account", "root_type", "is_group"],
		limit_page_length=0,
	)
	return {
		r["name"]: CoaAccount(
			name=r["name"],
			parent_account=r["parent_account"],
			root_type=r["root_type"] or "",
			is_group=bool(r["is_group"]),
			company_abbr=abbr,
		)
		for r in coa_rows
	}


def _load_suppliers() -> list:
	"""Load every Supplier as a list of ``Supplier`` value objects."""
	from rgi_migration.mapper.supplier_source import Supplier

	rows = frappe.get_all(
		"Supplier",
		fields=[
			"name", "supplier_name", "supplier_group", "disabled", "country",
		],
		limit_page_length=0,
	)
	return [
		Supplier(
			name=r["name"],
			supplier_name=r.get("supplier_name") or r["name"],
			supplier_group=r.get("supplier_group") or "",
			disabled=bool(r.get("disabled")),
			country=r.get("country"),
		)
		for r in rows
	]


def _load_rule_source():
	"""Load rules from ``docs/seed_plan.json`` — the committed seed is the
	source of truth for Tier-1 rules until Item 8 lands FrappeRuleSource.
	"""
	from rgi_migration.mapper.rule_source import JsonFileRuleSource

	app_root = Path(frappe.get_app_path("rgi_migration")).parent
	seed_path = app_root / "docs" / "seed_plan.json"
	return JsonFileRuleSource(seed_path)


def _load_rule_name_by_section() -> dict[str, str]:
	"""Build ``{source_section: Mapping Rule name}`` for the matched_rule /
	anti_pattern_rule translation.

	Historical: the DocType field was a Link → Mapping Rule requiring
	an autoname lookup. Item 6 relaxed the field to Data so Tier-2
	subtier labels (``tier2:norm_strong`` / ``tier2:acct_num`` /
	``tier2:fuzzy_classical``) can persist directly. Tier-1 entries
	still go through the source_section translation below for UI
	consistency with existing rows.

	Collisions on source_section (two Mapping Rule docs with the same
	section string) are resolved last-wins; if this ever triggers in
	practice, revisit — seed plan uniqueness should guarantee 1:1.
	"""
	rows = frappe.get_all(
		"Mapping Rule",
		fields=["name", "source_section"],
		limit_page_length=0,
	)
	return {r["source_section"]: r["name"] for r in rows if r["source_section"]}


# translate_matched_rule lives in rgi_migration.session.parse_and_map
# (Frappe-free) so pure-core unit tests can import it. Re-exported
# via ``from rgi_migration.session.parse_and_map import
# translate_matched_rule`` at the import site above.


# ---------------------------------------------------------------------------
# Summary population — translates mapper.summarize() output onto
# the session's sb_parse + sb_map_counts fields
# ---------------------------------------------------------------------------


def _populate_parse_summary(session, result) -> None:
	"""Populate sb_parse + sb_map_counts fields from a ``ParseAndMapResult``.

	Tiers without a dedicated Int column (tier1_supplier_*, pending_*,
	excluded_zero_balance) are carried in the summary dict returned to
	the caller but not stored on discrete session fields — add columns
	to the session DocType if dashboard surfaces need them.
	"""
	tb = result.tb
	summary = result.summary
	by_tier = summary.get("by_tier", {})

	session.parsed_company_name = tb.company_name
	session.ledger_count = len(tb.ledgers)
	session.student_ledger_count = len(tb.student_ledgers)
	session.group_count = len(tb.groups)
	session.total_dr = tb.total_dr
	session.total_cr = tb.total_cr
	session.is_balanced = 1 if tb.is_balanced else 0
	session.parse_warnings = (
		json.dumps(tb.parse_warnings, indent=2) if tb.parse_warnings else None
	)

	session.tier1_exact_count = by_tier.get("tier1_exact", 0)
	session.tier1_rule_count = by_tier.get("tier1_rule", 0)
	session.tier1_pattern_count = by_tier.get("tier1_pattern", 0)
	session.tier2_fuzzy_count = by_tier.get("tier2_fuzzy", 0)
	session.tier3_claude_count = by_tier.get("tier3_claude", 0)
	session.unmapped_count = by_tier.get("unmapped", 0)
	session.pnl_excluded_count = by_tier.get("excluded_pnl", 0)
	session.group_account_refused_count = by_tier.get("group_refused", 0)
	session.anti_pattern_blocked_count = by_tier.get("anti_pattern_blocked", 0)


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


def _record_failure(session_name: str, exc: Exception) -> None:
	"""After a rolled-back run, set status=Failed and append the error
	to error_log as a timestamped block. Swallows its own exceptions
	so the original error propagates to the caller unmodified.
	"""
	try:
		session = frappe.get_doc("Tally Migration Session", session_name)
		ts = frappe.utils.now_datetime().isoformat(timespec="seconds")
		block = (
			f"[{ts}] run_mapper failed: {type(exc).__name__}: {exc}"
		)
		existing = session.error_log or ""
		separator = "\n\n" if existing else ""
		session.error_log = f"{existing}{separator}{block}"
		session.status = _STATUS_FAILED
		session.completed_at = frappe.utils.now_datetime()
		session.save(ignore_permissions=True)
		frappe.db.commit()
	except Exception:  # noqa: BLE001 — don't mask original failure
		pass
