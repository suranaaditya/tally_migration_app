"""Tests for generator-side reviewer rescue (production hotfix 2026-04-25).

When the mapper tags an MD as ``unmapped`` / ``pending_account_creation``
/ ``group_refused`` / ``anti_pattern_blocked`` (account-side) or
``pending_supplier_creation`` (supplier-side), and the reviewer rescues
it by setting ``review_action=Approved`` (or ``Manual Override``) with
a ``final_account`` (or ``final_supplier``), the generator MUST treat
it as a contribution rather than a refusal.

This is the documented "Loader-time tier auto-lift when final_* is
set" deferred item (WEEK4_DEFERRED_ITEMS.md), implemented at
generator-time rather than loader-time. Generator-side keeps the
persisted ``tier`` mapper-authoritative (audit truth: "mapper had
nothing"); rescue is signalled by review_action + final_*.

Pre-fix symptom: reviewer rescued an unmapped Tally ledger by picking
an existing ERPNext account, clicked Approve & Next; row turned
green but generate_all refused with a "Cannot generate main JE"
message because the mapper's tier=unmapped routed into the refusal
gate.
"""

from __future__ import annotations

import pytest

from rgi_migration.generators.opening_je import (
	MainJEGenerationError,
	build_je_payload,
)
from rgi_migration.generators.advance_je import (
	AdvanceJEGenerationError,
	SupplierInfo,
	build_advance_je_payload,
)
from rgi_migration.generators.oit_csv import (
	OITGenerationError,
	build_oit_rows,
)
from rgi_migration.mapper.mapper import MappedDecision
from rgi_migration.parsers.normalized_schema import Ledger, ParsedTallyTB


# ============================================================================
# Fixtures (mirrors test_generator_opening_je / test_generator_advance_je)
# ============================================================================


def _ledger(name, *, root_type="Asset", opening_dr=0.0, opening_cr=0.0,
            tally_id="1") -> Ledger:
	net = opening_cr - opening_dr
	return Ledger(
		name=name, tally_id=tally_id, parent_group="Root",
		parent_chain=["Root"], root_type=root_type,
		opening_dr=opening_dr, opening_cr=opening_cr, net_amount=net,
		net_side=("Cr" if net > 0 else "Dr" if net < 0 else "Zero"),
		is_leaf=True,
	)


def _account_decision(name, *, tally_id="1", tier="unmapped",
                       review_action="Pending", final_account=None,
                       opening_dr=0.0, opening_cr=0.0,
                       tally_root_type="Asset") -> MappedDecision:
	# Replicates the parse_and_map.decision_from_doc_row contract:
	# proposed_account is final_account when reviewer set it, else
	# the mapper's proposed_account (None for unmapped).
	return MappedDecision(
		tally_name=name, tally_id=tally_id, tally_root_type=tally_root_type,
		opening_dr=opening_dr, opening_cr=opening_cr,
		tier=tier,
		proposed_account=final_account,  # the final_account fallback
		review_action=review_action,
		matched_rule=None, confidence=0.0,
	)


def _supplier_decision(name, *, tally_id="V1", tier="pending_supplier_creation",
                        review_action="Pending", final_supplier=None,
                        opening_dr=0.0, opening_cr=0.0) -> MappedDecision:
	return MappedDecision(
		tally_name=name, tally_id=tally_id, tally_root_type="Liability",
		opening_dr=opening_dr, opening_cr=opening_cr,
		tier=tier, proposed_account=None,
		review_action=review_action,
		matched_rule=None, confidence=0.0,
		proposed_supplier=final_supplier,  # final_supplier fallback path
		supplier_match_score=1.0 if final_supplier else 0.0,
	)


def _tb(ledgers) -> ParsedTallyTB:
	dr = sum(l.opening_dr for l in ledgers)
	cr = sum(l.opening_cr for l in ledgers)
	return ParsedTallyTB(
		company_name="Test Co", tb_date="2026-04-01",
		source_format="xml", source_file="/tmp/t.xml",
		ledgers=ledgers, groups=[],
		total_dr=dr, total_cr=cr, is_balanced=abs(dr - cr) < 0.01,
	)


_OPENING_KW = dict(
	abbr="CACSPU", erpnext_company="GHR CACS Pune",
	full_name="GHR CACS Pune", fiscal_year="2026-2027",
	posting_date="2026-04-01", session_name="TMS-TEST",
	source_sha256="abc", reference_id="OB-CACSPU-2026-01",
	timestamp_iso="2026-04-20T12:00:00Z",
)
_ADVANCE_KW = dict(
	abbr="CACSPU", erpnext_company="GHR CACS Pune",
	full_name="GHR CACS Pune", fiscal_year="2026-2027",
	posting_date="2026-04-01", session_name="TMS-TEST",
	source_sha256="abc", reference_id="OB-CACSPU-2026-02",
	timestamp_iso="2026-04-20T12:00:00Z",
)
_OIT_KW = dict(
	abbr="CACSPU",
	posting_date="2026-04-01",
	session_name="TMS-TEST",
)


# ============================================================================
# Account-side rescue (opening_je)
# ============================================================================


class TestOpeningJEReviewerRescue:
	def test_unmapped_with_approved_final_account_contributes(self) -> None:
		"""The exact production scenario: reviewer picks an existing
		account for an unmapped Tally ledger and clicks Approve & Next.
		Pre-fix: refusal. Post-fix: contributes."""
		ledgers = [
			_ledger("Some Custom Ledger", opening_dr=1000.0, tally_id="X1"),
		]
		decisions = [
			_account_decision(
				"Some Custom Ledger", tally_id="X1", tier="unmapped",
				review_action="Approved",
				final_account="Cash - CACSPU",
				opening_dr=1000.0,
			),
		]
		payload = build_je_payload(
			tb=_tb(ledgers), decisions=decisions, **_OPENING_KW,
		)
		# Should succeed (not return None, not raise)
		assert payload is not None, (
			"Rescued unmapped MD must produce a JE payload."
		)
		assert payload.contributions_count == 1
		# The contribution should post to the reviewer's chosen account
		account_rows = [r for r in payload.rows if r["account"] == "Cash - CACSPU"]
		assert len(account_rows) == 1
		assert account_rows[0]["debit_in_account_currency"] == 1000.0

	def test_pending_account_creation_rescued_to_existing_account(self) -> None:
		"""Mapper said 'create new account'; reviewer found an existing
		one and Approved. Should contribute."""
		ledgers = [
			_ledger("Custom Capital", root_type="Equity",
			        opening_cr=5000.0, tally_id="X2"),
		]
		decisions = [
			_account_decision(
				"Custom Capital", tally_id="X2", tier="pending_account_creation",
				review_action="Approved",
				final_account="Share Capital - CACSPU",
				opening_cr=5000.0, tally_root_type="Equity",
			),
		]
		payload = build_je_payload(
			tb=_tb(ledgers), decisions=decisions, **_OPENING_KW,
		)
		assert payload is not None
		assert payload.contributions_count == 1

	def test_manual_override_also_rescues(self) -> None:
		"""Manual Override is the explicit-override review_action.
		Should rescue equivalently to Approved."""
		ledgers = [_ledger("L", opening_dr=100.0, tally_id="X3")]
		decisions = [
			_account_decision(
				"L", tally_id="X3", tier="anti_pattern_blocked",
				review_action="Manual Override",
				final_account="Cash - CACSPU",
				opening_dr=100.0,
			),
		]
		payload = build_je_payload(
			tb=_tb(ledgers), decisions=decisions, **_OPENING_KW,
		)
		assert payload is not None
		assert payload.contributions_count == 1

	def test_unmapped_pending_still_refuses(self) -> None:
		"""Pre-rescue state: tier=unmapped + review_action=Pending +
		no final_account. Generator must still refuse — reviewer hasn't
		acted yet."""
		ledgers = [_ledger("L", opening_dr=100.0, tally_id="X4")]
		decisions = [
			_account_decision(
				"L", tally_id="X4", tier="unmapped",
				review_action="Pending",
				final_account=None,
				opening_dr=100.0,
			),
		]
		with pytest.raises(MainJEGenerationError):
			build_je_payload(
				tb=_tb(ledgers), decisions=decisions, **_OPENING_KW,
			)

	def test_unmapped_approved_without_final_account_still_refuses(self) -> None:
		"""Defensive: review_action=Approved but no final_account picked
		is still incomplete. Refuse — reviewer needs to set the
		account."""
		ledgers = [_ledger("L", opening_dr=100.0, tally_id="X5")]
		decisions = [
			_account_decision(
				"L", tally_id="X5", tier="unmapped",
				review_action="Approved",
				final_account=None,  # missing!
				opening_dr=100.0,
			),
		]
		with pytest.raises(MainJEGenerationError):
			build_je_payload(
				tb=_tb(ledgers), decisions=decisions, **_OPENING_KW,
			)


# ============================================================================
# Supplier-side rescue (oit_csv + advance_je)
# ============================================================================


class TestSupplierReviewerRescue:
	def test_advance_je_rescues_pending_supplier_with_final_supplier(self) -> None:
		"""Reviewer picked an existing Supplier for what the mapper
		flagged pending_supplier_creation. Net-Dr → Advance JE."""
		decisions = [
			_supplier_decision(
				"Some Vendor", tally_id="V1",
				tier="pending_supplier_creation",
				review_action="Approved",
				final_supplier="Existing Vendor",
				opening_dr=2500.0,  # Net Dr → Advance JE
			),
		]
		supplier_index = {
			"Existing Vendor": SupplierInfo(
				name="Existing Vendor", supplier_name="Existing Vendor",
				disabled=False,
			),
		}
		payload = build_advance_je_payload(
			decisions=decisions, supplier_index=supplier_index,
			**_ADVANCE_KW,
		)
		assert payload is not None
		assert payload.supplier_count == 1

	def test_oit_csv_rescues_pending_supplier_with_final_supplier(self) -> None:
		"""Same rescue path on the OIT CSV (net-Cr) side."""
		decisions = [
			_supplier_decision(
				"Some Vendor", tally_id="V2",
				tier="pending_supplier_creation",
				review_action="Approved",
				final_supplier="Existing Vendor",
				opening_cr=3000.0,  # Net Cr → OIT CSV
			),
		]
		supplier_index = {
			"Existing Vendor": SupplierInfo(
				name="Existing Vendor", supplier_name="Existing Vendor",
				disabled=False,
			),
		}
		rows = build_oit_rows(
			decisions=decisions, supplier_index=supplier_index,
			**_OIT_KW,
		)
		assert len(rows) == 1
		assert rows[0].party_id == "Existing Vendor"

	def test_advance_je_pending_supplier_pending_action_still_refuses(self) -> None:
		"""Pre-rescue state — refuse."""
		decisions = [
			_supplier_decision(
				"Some Vendor", tally_id="V3",
				tier="pending_supplier_creation",
				review_action="Pending",
				final_supplier=None,
				opening_dr=2500.0,
			),
		]
		with pytest.raises(AdvanceJEGenerationError):
			build_advance_je_payload(
				decisions=decisions, supplier_index={}, **_ADVANCE_KW,
			)

	def test_oit_csv_pending_supplier_pending_action_still_refuses(self) -> None:
		decisions = [
			_supplier_decision(
				"Some Vendor", tally_id="V4",
				tier="pending_supplier_creation",
				review_action="Pending",
				final_supplier=None,
				opening_cr=3000.0,
			),
		]
		with pytest.raises(OITGenerationError):
			build_oit_rows(
				decisions=decisions, supplier_index={}, **_OIT_KW,
			)
