# Copyright (c) 2026, Dux Digitech and contributors
# For license information, please see license.txt

from __future__ import annotations

import json
from typing import Any

import frappe
from frappe.model.document import Document


class TallyMigrationSession(Document):
	def before_insert(self) -> None:
		"""Auto-populate ``fiscal_year_short`` from ``fiscal_year``.

		``fiscal_year_short`` is spliced into the session autoname
		(``TMS-{company_abbr}-{fiscal_year_short}-{#####}``) and is
		read-only in the UI. Computing it here — rather than asking
		the user — prevents malformed autonames like
		``TMS-CACSPU--00495`` (the in-flight session that predates
		this hook).

		Validation is strict: fiscal_year must be exactly ``YYYY-YYYY``
		with consecutive years. A malformed input cascades into a
		broken session name, so we raise at insert time rather than
		silently leaving ``fiscal_year_short`` blank.
		"""
		from rgi_migration.session.fiscal_year import (
			compute_fiscal_year_short,
		)

		try:
			self.fiscal_year_short = compute_fiscal_year_short(
				self.fiscal_year or ""
			)
		except ValueError as exc:
			frappe.throw(str(exc))

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
_STATUS_GENERATING = "Generating"
_STATUS_GENERATED = "Generated"
_STATUS_FAILED = "Failed"
_STATUS_SUBMITTED = "Submitted"

# Statuses from which Run Mapper is permitted. Reviewing / Generating /
# Generated / Submitted sessions must use Reset Parse first to drop
# existing decisions before re-running. Failed lets reviewers retry
# after fixing a configuration problem.
_RUN_MAPPER_ALLOWED_FROM = frozenset({_STATUS_DRAFT, _STATUS_FAILED})

# Statuses from which Generate All is permitted. Item 8.5 Stage 1 — only
# Reviewing (reviewer has cleared the pending queue) OR Generated (re-run
# to replace Draft artefacts) are accepted. Draft/Parsing/Mapping are
# pre-review; Submitted is frozen.
_GENERATE_ALLOWED_FROM = frozenset({_STATUS_REVIEWING, _STATUS_GENERATED})


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
		alias_rule_source = _load_alias_rule_source()
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
			alias_rule_source=alias_rule_source,
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

	Returns ``{"status": "ok", "deleted_count": int, "scr_swept": int,
	"acr_swept": int}``.

	**Wiped (session-local state):**
	  * ``Mapping Decision`` rows where ``session == session_name``
	    (direct SQL delete)
	  * ``Supplier Creation Request`` child rows on this session
	  * ``Account Creation Request`` child rows on this session
	  * ``Account Creation Source`` rows (transitively, via ACR parent
	    deletion)

	**Preserved (cross-entity learning artifacts):**
	  * ``Mapping Rule`` rows where
	    ``created_via_session == session_name``. These are rule
	    promotions from reviewer approvals (Item 5 Commit 1).
	  * ``Supplier Alias Rule`` rows where
	    ``created_from == "session_review"``. These are alias
	    promotions (Item 5 Commit 2).

	**Why preserved?** Per CLAUDE.md architectural decisions: the rules
	library compounds. Every approved mapping becomes a reusable rule;
	the goal is Tier 1 auto-mapping 70 %+ by entity 10. Promoted rules
	are cross-entity learning artifacts, not session-local state. The
	``created_via_session`` / ``created_from`` fields carry provenance,
	not scope. Wiping them on Reset Parse would destroy the learning
	loop and defeat the 59-entity rollout strategy.

	**Note to future contributors:** do not modify Reset Parse to wipe
	promoted Mapping Rules or Supplier Alias Rules. The Item 7 Fix 3
	audit (2026-04-24) explicitly evaluated this and concluded that
	preserving them is correct. Any change here needs an architectural
	decision note in ``docs/mapper_design_notes.md``, not a silent
	tweak.
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
	"""Load the live rule library from the ``Mapping Rule`` DocType.

	Item 8 (α→γ unlock): seeds and session-review promotions are now
	both persisted as ``Mapping Rule`` rows; the ``created_from`` field
	distinguishes provenance but they are architecturally equivalent.
	Reviewers can deprecate any rule regardless of ``created_from``.

	``docs/seed_plan.json`` is retained in the repo as a seed-bootstrap
	artefact for fresh-bench setup (Item 13); it is NOT read at runtime.
	``JsonFileRuleSource`` is kept in ``rgi_migration.mapper.rule_source``
	as a reference implementation and debugging aid.
	"""
	from rgi_migration.mapper.rule_source import FrappeRuleSource

	return FrappeRuleSource()


def _load_alias_rule_source():
	"""Load the live supplier alias rule library from the
	``Supplier Alias Rule`` DocType.

	Item 8 (α→γ unlock): Layer-2 supplier alias matching now reads
	promoted SAR rows live. Only ``status="confirmed"`` rules are
	returned. ``exact_ci`` is the only supported match mode today;
	the SAR DocType allows ``fuzzy_85`` / ``fuzzy_90`` Select values
	but ``find_alias_rule_supplier`` raises ``NotImplementedError``
	on those — defensive against write-path drift.
	"""
	from rgi_migration.mapper.alias_rule_source import FrappeAliasRuleSource

	return FrappeAliasRuleSource()


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


# ---------------------------------------------------------------------------
# Item 8.5 Stage 1 — Generate All orchestrator
# ---------------------------------------------------------------------------
#
# Atomic single-pass invocation of the four generators in a fixed order:
#
#   1. Main Opening JE   — opening_je.generate_main_opening_je
#   2. OIT CSV           — oit_csv.generate_oit_csv
#   3. Advance JE        — advance_je.generate_advance_je
#   4. Students CSV      — students_csv.generate_students_csv
#
# Stop-on-first-failure. On any generator raising, the wrapper rolls
# status back to Reviewing (NOT Failed — generation attempts are
# recoverable; Failed is reserved for unrecoverable session-level
# errors like parser crashes) and captures which generators succeeded
# vs failed in the return dict so the UI can surface mid-sequence
# failures clearly.
#
# Per Phase A Q5: generators are self-idempotent. Re-running over a
# Generated session deletes Draft JEs/Files and recreates. Submitted
# artefacts refuse per each generator's internal contract (Q6).


_GENERATOR_SEQUENCE = (
	(
		"Main Opening JE",
		"rgi_migration.generators.opening_je.generate_main_opening_je",
	),
	(
		"OIT CSV",
		"rgi_migration.generators.oit_csv.generate_oit_csv",
	),
	(
		"Advance JE",
		"rgi_migration.generators.advance_je.generate_advance_je",
	),
	(
		"Students CSV",
		"rgi_migration.generators.students_csv.generate_students_csv",
	),
)


@frappe.whitelist()
def generate_all(session_name: str) -> dict[str, Any]:
	"""Run all four generators in sequence.

	Guards:
	  * session status must be Reviewing or Generated (re-generate)
	  * Submitted sessions are frozen (refused)

	State transitions on success:
	  Reviewing|Generated -> Generating -> Generated

	On mid-sequence failure:
	  Generating -> Reviewing (with error appended to error_log). Prior
	  successful generators remain in their Draft state; a subsequent
	  ``generate_all`` call will replace them via each generator's own
	  idempotency logic.

	Returns a dict shaped for the JS wrapper::

	  {
	    "status": "ok" | "partial_failure",
	    "succeeded": [{"name": str, "artefact": str}, ...],
	    "failed": {"name": str, "error": str} | None,
	    "skipped": [str, ...],   # generator names after first failure
	  }
	"""
	session = frappe.get_doc("Tally Migration Session", session_name)

	if session.status not in _GENERATE_ALLOWED_FROM:
		frappe.throw(
			f"Cannot generate on session {session_name!r} with status "
			f"{session.status!r}. Allowed statuses: "
			f"{sorted(_GENERATE_ALLOWED_FROM)}. Reset Parse + Run Mapper "
			f"required to reach Reviewing if currently Draft/Failed."
		)

	# Transition to Generating + commit so UI / concurrent readers see it.
	session.status = _STATUS_GENERATING
	session.save(ignore_permissions=True)
	frappe.db.commit()

	succeeded: list[dict[str, str]] = []
	failed: dict[str, str] | None = None
	skipped: list[str] = []

	for i, (label, dotted_path) in enumerate(_GENERATOR_SEQUENCE):
		if failed is not None:
			# Already failed upstream; don't try to run this one.
			continue
		try:
			fn = frappe.get_attr(dotted_path)
			artefact_name = fn(session_name)
			succeeded.append({"name": label, "artefact": artefact_name})
		except Exception as exc:  # noqa: BLE001 — capture-all for UI
			failed = {
				"name": label,
				"error": f"{type(exc).__name__}: {exc}",
			}
			# Mark remaining as skipped for clear UI messaging.
			for remaining_label, _ in _GENERATOR_SEQUENCE[i + 1:]:
				skipped.append(remaining_label)

	# Transition to terminal state.
	session.reload()
	if failed is None:
		session.status = _STATUS_GENERATED

		# Item 8.5 Stage 2: log Deferred-count as provenance when non-
		# zero. Grep-able format — Stage 3's delta-gen audit reads
		# this to understand Pass 1's scope.
		deferred_count = frappe.db.count(
			"Mapping Decision",
			{"session": session_name, "review_action": "Deferred"},
		)
		if deferred_count:
			ts = frappe.utils.now_datetime().isoformat(timespec="seconds")
			log_line = (
				f"[{ts}] generate_all: completed Reviewing → Generated, "
				f"4 artefacts generated, {deferred_count} decisions "
				f"Deferred for next pass"
			)
			existing = session.error_log or ""
			separator = "\n\n" if existing else ""
			session.error_log = f"{existing}{separator}{log_line}"

		session.save(ignore_permissions=True)
		frappe.db.commit()
		return {
			"status": "ok",
			"succeeded": succeeded,
			"failed": None,
			"skipped": [],
			"deferred_count": deferred_count,
		}

	# Mid-sequence failure → back to Reviewing with error captured.
	ts = frappe.utils.now_datetime().isoformat(timespec="seconds")
	block_lines = [
		f"[{ts}] generate_all partial failure:",
		f"  succeeded: {[s['name'] for s in succeeded]}",
		f"  failed: {failed['name']} — {failed['error']}",
		f"  skipped: {skipped}",
	]
	block = "\n".join(block_lines)
	existing = session.error_log or ""
	separator = "\n\n" if existing else ""
	session.error_log = f"{existing}{separator}{block}"
	session.status = _STATUS_REVIEWING
	session.save(ignore_permissions=True)
	frappe.db.commit()

	return {
		"status": "partial_failure",
		"succeeded": succeeded,
		"failed": failed,
		"skipped": skipped,
	}


# ---------------------------------------------------------------------------
# Item 8.5 Stage 1 — Mark Submitted (Gap 11 closure)
# ---------------------------------------------------------------------------
#
# Reviewer affirmation that all four artefacts have been submitted
# (JEs) or consumed (CSVs) in their downstream systems. Transitions
# the session to its terminal Submitted state, after which it is
# frozen (per reset_parse's refusal at Submitted and the "never
# regenerate" principle).
#
# Validation: hard check that Main JE and Advance JE are Submitted in
# ERPNext (docstatus=1). OIT CSV and Students CSV are Files, not
# submittable; they rely on the reviewer's explicit confirmation via
# the JS confirm prompt. Temporary Opening balance verification is
# manual by design per docs/mapper_design_notes.md §5.


_MARK_SUBMITTED_ALLOWED_FROM = frozenset({_STATUS_GENERATED})


@frappe.whitelist()
def mark_submitted(session_name: str) -> dict[str, Any]:
	"""Finalize a session: status Generated -> Submitted.

	Guards:
	  * session status must be Generated
	  * both Main Opening JE and Advance JE (if present) must have
	    docstatus=1 (Submitted in ERPNext Desk)

	The reviewer is expected to have separately:
	  * Imported the OIT CSV via ERPNext's Opening Invoice Tool and
	    submitted the resulting Purchase Invoices
	  * Handed the Students CSV to dux_voucher's Ex Student Opening Batch
	  * Verified Temporary Opening - {ABBR} balance nets to zero in
	    ERPNext GL (manual per §5 architectural decision)

	The JS confirm prompt surfaces these expectations; this function
	trusts the reviewer on them. Only the JE docstatus check is
	enforced programmatically.

	Returns ``{"status": "ok"}`` on success; raises
	``frappe.ValidationError`` on guard failures.
	"""
	session = frappe.get_doc("Tally Migration Session", session_name)

	if session.status not in _MARK_SUBMITTED_ALLOWED_FROM:
		frappe.throw(
			f"Cannot mark session {session_name!r} Submitted — status "
			f"is {session.status!r}. Allowed: "
			f"{sorted(_MARK_SUBMITTED_ALLOWED_FROM)}."
		)

	unsubmitted: list[str] = []
	for field_name, je_label in (
		("generated_je_draft", "Main Opening JE"),
		("generated_advance_je", "Advance JE"),
	):
		je_name = getattr(session, field_name, None)
		if not je_name:
			# Generator may not have produced this artefact (e.g. no
			# advance-Dr vendors → no advance_je). Skip silently.
			continue
		docstatus = frappe.db.get_value(
			"Journal Entry", je_name, "docstatus",
		)
		if docstatus != 1:
			status_label = {0: "Draft", 2: "Cancelled"}.get(
				docstatus, f"docstatus={docstatus}"
			)
			unsubmitted.append(f"{je_label} ({je_name}) is {status_label}")

	if unsubmitted:
		frappe.throw(
			"Cannot mark Submitted — the following Journal Entries "
			"are not Submitted in ERPNext yet:\n\n  "
			+ "\n  ".join(unsubmitted)
			+ "\n\nSubmit each JE from its ERPNext Desk page, then try again."
		)

	session.status = _STATUS_SUBMITTED
	session.completed_at = frappe.utils.now_datetime()
	session.save(ignore_permissions=True)
	frappe.db.commit()

	return {"status": "ok", "session_status": _STATUS_SUBMITTED}
