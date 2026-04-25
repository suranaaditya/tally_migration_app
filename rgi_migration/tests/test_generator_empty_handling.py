"""Tests for the empty-payload guard in opening_je and advance_je.

Item 8.5 Stage 3 Phase C bug fix. See ``docs/mapper_design_notes.md §5``
("Generator empty-payload guard"). Triggered when:

* Pass 2+ resolves only supplier-side decisions (Main JE has no
  contributions; previously crashed with "Both Debit and Credit values
  cannot be zero" on the 0/0 balancer row).
* Pass 2+ resolves only account-side decisions (Advance JE has no
  net-Dr supplier rows; same crash mode).
* Single-pass session (Stage 1/2 regression) where the source data
  legitimately has no advance vendors.

Generators must return ``None`` from their ``build_*_payload`` pure
functions so the caller can detect "no JE this pass" and skip Frappe
``.insert()`` rather than try to insert an invalid 0/0 row.

Pure-function tests; no Frappe needed.
"""

from __future__ import annotations

import pytest

from rgi_migration.generators.opening_je import (
	build_je_payload,
	MainJEPayload,
)
from rgi_migration.generators.advance_je import (
	build_advance_je_payload,
	AdvanceJEPayload,
	SupplierInfo,
)
from rgi_migration.mapper.mapper import MappedDecision
from rgi_migration.parsers.normalized_schema import Ledger, ParsedTallyTB


# ============================================================================
# Test fixtures (mirrors test_generator_opening_je / test_generator_advance_je)
# ============================================================================


def _ledger(
	name: str,
	*,
	root_type: str = "Asset",
	opening_dr: float = 0.0,
	opening_cr: float = 0.0,
	tally_id: str | None = "1",
) -> Ledger:
	net = opening_cr - opening_dr
	return Ledger(
		name=name,
		tally_id=tally_id,
		parent_group="Root",
		parent_chain=["Root"],
		root_type=root_type,
		opening_dr=opening_dr,
		opening_cr=opening_cr,
		net_amount=net,
		net_side=("Cr" if net > 0 else "Dr" if net < 0 else "Zero"),
		is_leaf=True,
	)


def _account_decision(
	name: str,
	*,
	tally_id: str = "1",
	proposed_account: str | None = None,
	opening_dr: float = 0.0,
	opening_cr: float = 0.0,
	tally_root_type: str = "Asset",
	tier: str = "tier1_exact",
) -> MappedDecision:
	return MappedDecision(
		tally_name=name,
		tally_id=tally_id,
		tally_root_type=tally_root_type,
		opening_dr=opening_dr,
		opening_cr=opening_cr,
		tier=tier,
		proposed_account=proposed_account,
		review_action="Pending",
		matched_rule=None,
		confidence=1.0,
	)


def _supplier_decision(
	name: str,
	*,
	tally_id: str = "S1",
	proposed_supplier: str | None = None,
	opening_dr: float = 0.0,
	opening_cr: float = 0.0,
	tier: str = "tier1_supplier_exact",
) -> MappedDecision:
	return MappedDecision(
		tally_name=name,
		tally_id=tally_id,
		tally_root_type="Liability",
		opening_dr=opening_dr,
		opening_cr=opening_cr,
		tier=tier,
		proposed_account=None,
		review_action="Pending",
		matched_rule=None,
		confidence=1.0,
		proposed_supplier=proposed_supplier,
		supplier_match_score=1.0 if proposed_supplier else 0.0,
	)


def _tb(ledgers: list[Ledger]) -> ParsedTallyTB:
	total_dr = sum(l.opening_dr for l in ledgers)
	total_cr = sum(l.opening_cr for l in ledgers)
	return ParsedTallyTB(
		company_name="Test Co",
		tb_date="2026-04-01",
		source_format="xml",
		source_file="/tmp/test.xml",
		ledgers=ledgers,
		groups=[],
		total_dr=total_dr,
		total_cr=total_cr,
		is_balanced=abs(total_dr - total_cr) < 0.01,
	)


_OPENING_BUILD_KW = dict(
	abbr="CACSPU",
	erpnext_company="GHR CACS Pune",
	full_name="GH Raisoni CACS Pune",
	fiscal_year="2026-2027",
	posting_date="2026-04-01",
	session_name="TMS-TEST",
	source_sha256="abc",
	reference_id="OB-CACSPU-2026-01",
	timestamp_iso="2026-04-20T12:00:00Z",
)


_ADVANCE_BUILD_KW = dict(
	abbr="CACSPU",
	erpnext_company="GHR CACS Pune",
	full_name="GH Raisoni CACS Pune",
	fiscal_year="2026-2027",
	posting_date="2026-04-01",
	session_name="TMS-TEST",
	source_sha256="abc",
	reference_id="OB-CACSPU-2026-02",
	timestamp_iso="2026-04-20T12:00:00Z",
)


# ============================================================================
# opening_je empty-payload guard
# ============================================================================


class TestOpeningJeEmptyPayloadGuard:
	def test_zero_decisions_returns_none(self) -> None:
		"""Pass 2 with no main-JE-eligible decisions → build returns None."""
		payload = build_je_payload(
			tb=_tb([]), decisions=[], **_OPENING_BUILD_KW,
		)
		assert payload is None, (
			"build_je_payload must return None when no contributions are "
			"eligible — caller skips JE creation rather than constructing "
			"a 0/0 balancer row that Frappe rejects."
		)

	def test_only_supplier_tier_decisions_returns_none(self) -> None:
		"""Pass 2 with ONLY supplier-tier decisions (handled by OIT/advance)
		→ Main JE finds zero eligible → returns None."""
		# Supplier decisions silently skip in Main JE — they don't have
		# matching ledgers in the main-JE-eligible set.
		ledgers = [
			_ledger("Supplier1", root_type="Liability", opening_cr=500.0,
			        tally_id="S1"),
		]
		decisions = [
			_supplier_decision("Supplier1", tally_id="S1",
			                    proposed_supplier="Supplier1",
			                    opening_cr=500.0),
		]
		payload = build_je_payload(
			tb=_tb(ledgers), decisions=decisions, **_OPENING_BUILD_KW,
		)
		assert payload is None

	def test_with_contributions_still_returns_payload(self) -> None:
		"""Regression — happy path unaffected by the new guard."""
		ledgers = [
			_ledger("Cash", root_type="Asset", opening_dr=100.0,
			        tally_id="1"),
		]
		decisions = [
			_account_decision("Cash", tally_id="1",
			                  proposed_account="Cash - CACSPU",
			                  opening_dr=100.0),
		]
		payload = build_je_payload(
			tb=_tb(ledgers), decisions=decisions, **_OPENING_BUILD_KW,
		)
		assert payload is not None
		assert isinstance(payload, MainJEPayload)
		assert payload.contributions_count == 1


# ============================================================================
# advance_je empty-payload guard
# ============================================================================


class TestAdvanceJeEmptyPayloadGuard:
	def test_zero_decisions_returns_none(self) -> None:
		"""Pass 2 with no eligible supplier decisions → build returns None."""
		payload = build_advance_je_payload(
			decisions=[], supplier_index={}, **_ADVANCE_BUILD_KW,
		)
		assert payload is None, (
			"build_advance_je_payload must return None when no net-Dr "
			"supplier rows are eligible — caller skips JE creation."
		)

	def test_all_net_cr_decisions_returns_none(self) -> None:
		"""All eligible supplier decisions are net-Cr (→ OIT, not Advance JE).
		Advance JE finds zero net-Dr rows → returns None."""
		decisions = [
			_supplier_decision(
				"VendorA", tally_id="V1", proposed_supplier="VendorA",
				opening_cr=500.0,  # Cr → OIT, not Advance
			),
		]
		supplier_index = {
			"VendorA": SupplierInfo(
				name="VendorA", supplier_name="VendorA", disabled=False,
			),
		}
		payload = build_advance_je_payload(
			decisions=decisions, supplier_index=supplier_index,
			**_ADVANCE_BUILD_KW,
		)
		assert payload is None

	def test_with_net_dr_supplier_still_returns_payload(self) -> None:
		"""Regression — happy path unaffected by the new guard."""
		decisions = [
			_supplier_decision(
				"VendorB", tally_id="V2", proposed_supplier="VendorB",
				opening_dr=1500.0,  # Dr → Advance JE
			),
		]
		supplier_index = {
			"VendorB": SupplierInfo(
				name="VendorB", supplier_name="VendorB", disabled=False,
			),
		}
		payload = build_advance_je_payload(
			decisions=decisions, supplier_index=supplier_index,
			**_ADVANCE_BUILD_KW,
		)
		assert payload is not None
		assert isinstance(payload, AdvanceJEPayload)
		assert payload.supplier_count == 1


# ============================================================================
# Static check — generators handle None payload + clear session fields
# ============================================================================


class TestGeneratorEmptyHandlerSource:
	"""These ensure the Frappe-aware generator wrappers correctly detect
	the None sentinel and don't crash. We can't run Frappe in unit tests,
	but we can grep the source to confirm the empty-handler exists."""

	def test_opening_je_handles_none_payload(self) -> None:
		from pathlib import Path
		src = Path(__file__).resolve().parents[1] / "generators" / "opening_je.py"
		text = src.read_text(encoding="utf-8")
		assert "if payload is None:" in text, (
			"generate_main_opening_je must handle the None empty-payload "
			"sentinel from build_je_payload."
		)
		# Must clear session fields so Migration Pass row's main_je_name
		# lands as None on this pass.
		assert "session.generated_je_draft = None" in text
		assert "session.generated_je_reference = None" in text

	def test_advance_je_handles_none_payload(self) -> None:
		from pathlib import Path
		src = Path(__file__).resolve().parents[1] / "generators" / "advance_je.py"
		text = src.read_text(encoding="utf-8")
		assert "if payload is None:" in text
		assert "session.generated_advance_je = None" in text
		assert "session.generated_advance_je_reference = None" in text

	def test_both_generators_return_empty_string_sentinel(self) -> None:
		"""Per Phase C bug fix resolution: empty-payload skip returns ""
		(empty string), not None or some other sentinel. The orchestrator
		``generate_all`` already records this in succeeded list as
		``{"name": label, "artefact": ""}``."""
		from pathlib import Path
		opening_src = (Path(__file__).resolve().parents[1] / "generators"
		               / "opening_je.py").read_text(encoding="utf-8")
		advance_src = (Path(__file__).resolve().parents[1] / "generators"
		               / "advance_je.py").read_text(encoding="utf-8")
		# Both must contain a return "" inside their None-handler block.
		# Defensive substring — relies on the new code's literal ``return ""``.
		assert opening_src.count('return ""') >= 1
		assert advance_src.count('return ""') >= 1
