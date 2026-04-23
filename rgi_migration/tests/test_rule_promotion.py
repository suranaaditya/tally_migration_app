"""Tests for Item 5 Commit 1 — reviewer-promotion of approved Mapping
Decisions into reusable ``Mapping Rule`` rows.

Coverage layers (parallels ``test_acr_approval.py`` / ``test_scr_approval.py``
philosophy):

1. **Pure-core unit tests** — ``rgi_migration.mapper.rule_promotion``.
   ``substitute_abbr``, ``compute_source_hash``, ``clean_tally_pattern``,
   ``build_rule_payload``. No Frappe dependency.
2. **α-codification integration test** — a promoted rule's payload does
   NOT appear in ``JsonFileRuleSource(docs/seed_plan.json)`` output, even
   when the ``Mapping Rule`` DocType is assumed populated. This test
   **will start failing when Item 8 lands ``FrappeRuleSource``** — that
   is the intended future-forcing behavior per Phase A A-14. Item 8
   author should update this test to reflect γ (DocType-backed rule
   source) rather than silencing it.

The Frappe wrapper ``promote_decision_to_rule`` (md_review.py) is
exercised via Phase C bench programmatic smoke, same policy as the
ACR / SCR approval wrappers — the ORM-bound paths don't admit clean
unit mocks without duplicating the Frappe document model.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from rgi_migration.mapper.rule_promotion import (
    AbbrSubstitutionResult,
    NotPromotable,
    RulePayload,
    build_rule_payload,
    clean_tally_pattern,
    compute_source_hash,
    derive_rule_name,
    substitute_abbr,
)


# ---------------------------------------------------------------------------
# substitute_abbr — Phase A A-9 spec
# ---------------------------------------------------------------------------


def test_substitute_abbr_single_whole_word_occurrence() -> None:
    r = substitute_abbr("Salaries - CACSPU", "CACSPU")
    assert r == AbbrSubstitutionResult(
        substituted="Salaries - {ABBR}", occurrences=1, is_literal=False
    )


def test_substitute_abbr_all_occurrences_not_just_first() -> None:
    # A-9 explicit: "Substitute ALL occurrences (not just first)".
    r = substitute_abbr("CACSPU — Salaries - CACSPU (CACSPU legacy)", "CACSPU")
    assert r.substituted == "{ABBR} — Salaries - {ABBR} ({ABBR} legacy)"
    assert r.occurrences == 3
    assert r.is_literal is False


def test_substitute_abbr_is_case_sensitive() -> None:
    # A-9: "Case-sensitive, whole-word match". Lowercase abbr must NOT
    # substitute into a name spelled uppercase.
    r = substitute_abbr("Salaries - cacspu", "CACSPU")
    assert r.substituted == "Salaries - cacspu"
    assert r.occurrences == 0
    assert r.is_literal is True


def test_substitute_abbr_rejects_substring_of_word() -> None:
    # A-9: "No substring-of-word matching (e.g., 'CAC' in 'CACAO')".
    # "CAC" as an abbr must not substitute into "CACAO" or "CACSPU".
    r = substitute_abbr("Cocoa - CACAO Liability - CACSPU", "CAC")
    assert r.substituted == "Cocoa - CACAO Liability - CACSPU"
    assert r.occurrences == 0
    assert r.is_literal is True


def test_substitute_abbr_at_start_and_end_of_string() -> None:
    # Word-boundary matching must cover both ends.
    r = substitute_abbr("CACSPU Bank", "CACSPU")
    assert r.substituted == "{ABBR} Bank"
    assert r.occurrences == 1

    r2 = substitute_abbr("Bank - CACSPU", "CACSPU")
    assert r2.substituted == "Bank - {ABBR}"
    assert r2.occurrences == 1


def test_substitute_abbr_literal_template_zero_occurrence_is_valid() -> None:
    # A-9: "Zero-occurrence case = literal rule, not error". The pure
    # function returns is_literal=True; the UI layer surfaces this to
    # the reviewer for explicit confirmation, but the builder accepts it.
    r = substitute_abbr("Common Shared Account", "CACSPU")
    assert r.substituted == "Common Shared Account"
    assert r.occurrences == 0
    assert r.is_literal is True


def test_substitute_abbr_empty_abbr_returns_literal() -> None:
    # Defensive no-op — the Frappe wrapper refuses empty abbrs, but the
    # pure function stays total.
    r = substitute_abbr("Salaries - CACSPU", "")
    assert r.substituted == "Salaries - CACSPU"
    assert r.occurrences == 0
    assert r.is_literal is True


def test_substitute_abbr_regex_meta_in_abbr_are_escaped_not_interpreted() -> None:
    # Hypothetical abbr containing regex-meaningful characters must not
    # be interpreted as regex. Current company_abbr values are
    # letters-only so this is defensive; the assertion here is the
    # negative form (no spurious match on an unrelated string), which
    # is robust to the \b word-boundary behavior at the abbr's trailing
    # non-word char. ``A.C`` as literal regex would match ``AxC``;
    # ``re.escape``ing it prevents that.
    r = substitute_abbr("Bank - AxC", "A.C")
    assert r.substituted == "Bank - AxC"
    assert r.occurrences == 0
    assert r.is_literal is True


# ---------------------------------------------------------------------------
# compute_source_hash — seed-script parity
# ---------------------------------------------------------------------------


def test_source_hash_matches_seed_script_construction() -> None:
    # Seed-script formula (scripts/seed_mapping_rules.py:550) must remain
    # byte-for-byte identical so session-promoted and seed rules collide
    # on the same logical pattern.
    expected_key = "§4.1|0|Salaries"
    expected = hashlib.sha1(expected_key.encode("utf-8")).hexdigest()
    assert compute_source_hash("§4.1", False, "Salaries") == expected


def test_source_hash_anti_pattern_bit_changes_hash() -> None:
    # Different is_anti_pattern values → different hashes on identical
    # pattern. Confirms the bit is carried into the key.
    pos = compute_source_hash("§4.1", False, "Salaries")
    neg = compute_source_hash("§4.1", True, "Salaries")
    assert pos != neg


def test_source_hash_different_patterns_different_hashes() -> None:
    h1 = compute_source_hash("session-MR-MD-001", False, "Foo")
    h2 = compute_source_hash("session-MR-MD-001", False, "Bar")
    assert h1 != h2


def test_source_hash_deterministic_same_input_same_output() -> None:
    # Sanity: recomputing the same inputs returns the same hex string.
    a = compute_source_hash("session-MR-MD-001", False, "Foo")
    b = compute_source_hash("session-MR-MD-001", False, "Foo")
    assert a == b


# ---------------------------------------------------------------------------
# clean_tally_pattern
# ---------------------------------------------------------------------------


def test_clean_tally_pattern_strips_outer_whitespace() -> None:
    assert clean_tally_pattern("  Salaries  ") == "Salaries"


def test_clean_tally_pattern_collapses_internal_whitespace() -> None:
    # Tally exports occasionally carry double-spaces and tabs inside
    # ledger names; collapse to single spaces so we don't produce two
    # distinct rules for "A  B" vs "A B".
    assert clean_tally_pattern("Salaries   Payable") == "Salaries Payable"
    assert clean_tally_pattern("Salaries\tPayable") == "Salaries Payable"


def test_clean_tally_pattern_empty_and_whitespace_only_returns_empty() -> None:
    assert clean_tally_pattern("") == ""
    assert clean_tally_pattern("   ") == ""


# ---------------------------------------------------------------------------
# derive_rule_name
# ---------------------------------------------------------------------------


def test_derive_rule_name_abbr_prefix_format() -> None:
    assert (
        derive_rule_name("Salaries Payable", "CACSPU")
        == "CACSPU: Salaries Payable"
    )


# ---------------------------------------------------------------------------
# build_rule_payload — happy path + refusals
# ---------------------------------------------------------------------------


def _happy_inputs(**overrides) -> dict:
    base = {
        "md_name": "MD-2026-00001",
        "tally_name": "Salaries Payable",
        "tally_root_type": "Liability",
        "final_account": "Salaries Payable - CACSPU",
        "session_name": "TMS-CACSPU--00495",
        "abbr": "CACSPU",
        "entity_type": "college",
    }
    base.update(overrides)
    return base


def test_build_payload_happy_path() -> None:
    p = build_rule_payload(**_happy_inputs())
    assert isinstance(p, RulePayload)
    assert p.rule_name == "CACSPU: Salaries Payable"
    assert p.tally_pattern == "Salaries Payable"
    assert p.tally_match_mode == "exact_ci"
    assert p.applicable_root_type == "Liability"
    assert p.erpnext_account_template == "Salaries Payable - {ABBR}"
    assert p.raw_final_account == "Salaries Payable - CACSPU"
    assert p.abbr_occurrences == 1
    assert p.is_literal_template is False
    assert p.source_section == "session-MR-MD-2026-00001"
    assert p.source_hash == compute_source_hash(
        "session-MR-MD-2026-00001", False, "Salaries Payable"
    )
    assert p.applies_to_entity_types == "college"
    assert p.created_from == "session_review"
    assert p.created_via_session == "TMS-CACSPU--00495"
    assert p.status == "confirmed"
    assert p.is_anti_pattern is False


def test_build_payload_literal_template_flag_surfaced() -> None:
    # final_account doesn't contain the abbr → literal rule, template
    # equals raw. Builder accepts; UI layer gates on is_literal_template.
    p = build_rule_payload(
        **_happy_inputs(final_account="Common Cross-Entity Account")
    )
    assert p.erpnext_account_template == "Common Cross-Entity Account"
    assert p.raw_final_account == "Common Cross-Entity Account"
    assert p.abbr_occurrences == 0
    assert p.is_literal_template is True


def test_build_payload_multi_occurrence_surfaced() -> None:
    # Unusual but valid — abbr appears more than once in the final
    # account. Count makes it into the payload for the preview dialog
    # to surface.
    p = build_rule_payload(
        **_happy_inputs(final_account="CACSPU Bank Account - CACSPU")
    )
    assert p.erpnext_account_template == "{ABBR} Bank Account - {ABBR}"
    assert p.abbr_occurrences == 2
    assert p.is_literal_template is False


def test_build_payload_entity_type_defaults_to_star_when_missing() -> None:
    # A-10: entity-type-scoped by default. But when the Company
    # Abbreviation row doesn't have an entity_type set (or the wrapper
    # passes None), the payload defaults back to "*" — same as
    # _filter_by_entity's default scope.
    p = build_rule_payload(**_happy_inputs(entity_type=None))
    assert p.applies_to_entity_types == "*"

    p2 = build_rule_payload(**_happy_inputs(entity_type=""))
    assert p2.applies_to_entity_types == "*"

    p3 = build_rule_payload(**_happy_inputs(entity_type="   "))
    assert p3.applies_to_entity_types == "*"


def test_build_payload_root_type_normalizes_unknown_to_any() -> None:
    # Tally root_type is one of the five canonical ERPNext root types
    # OR empty; unknown/empty values default to "Any" per the DocType
    # schema default.
    p = build_rule_payload(**_happy_inputs(tally_root_type=None))
    assert p.applicable_root_type == "Any"

    p2 = build_rule_payload(**_happy_inputs(tally_root_type=""))
    assert p2.applicable_root_type == "Any"

    p3 = build_rule_payload(**_happy_inputs(tally_root_type="NonsenseValue"))
    assert p3.applicable_root_type == "Any"


def test_build_payload_tally_pattern_cleaning_applied() -> None:
    # Builder must run clean_tally_pattern before hashing; a leading-
    # space ledger name produces the same rule as the clean name.
    p = build_rule_payload(**_happy_inputs(tally_name="  Salaries  "))
    assert p.tally_pattern == "Salaries"
    # Hash is over the cleaned pattern.
    assert p.source_hash == compute_source_hash(
        p.source_section, False, "Salaries"
    )


def test_build_payload_refuses_empty_tally_name() -> None:
    with pytest.raises(NotPromotable, match="tally_name"):
        build_rule_payload(**_happy_inputs(tally_name=""))

    with pytest.raises(NotPromotable, match="tally_name"):
        build_rule_payload(**_happy_inputs(tally_name="   "))


def test_build_payload_refuses_empty_final_account() -> None:
    with pytest.raises(NotPromotable, match="final_account"):
        build_rule_payload(**_happy_inputs(final_account=""))


def test_build_payload_refuses_empty_session_name() -> None:
    with pytest.raises(NotPromotable, match="session"):
        build_rule_payload(**_happy_inputs(session_name=""))


def test_build_payload_refuses_empty_abbr() -> None:
    with pytest.raises(NotPromotable, match="company_abbr"):
        build_rule_payload(**_happy_inputs(abbr=""))


# ---------------------------------------------------------------------------
# Idempotency — same (md_name, tally_pattern) produces same hash
# ---------------------------------------------------------------------------


def test_build_payload_idempotent_source_hash() -> None:
    # Two builds from the same MD produce the same source_hash, which
    # is the idempotency key the wrapper's _lookup_duplicate hits on.
    p1 = build_rule_payload(**_happy_inputs())
    p2 = build_rule_payload(**_happy_inputs())
    assert p1.source_hash == p2.source_hash


def test_build_payload_different_mds_different_hashes() -> None:
    # Different MDs on the same tally pattern produce different hashes
    # — the source_section component carries the MD name, so distinct
    # MDs can co-exist at the pattern level. Conflict detection happens
    # at the (tally_pattern, root_type, template) triple, not at the
    # source_hash.
    p1 = build_rule_payload(**_happy_inputs(md_name="MD-001"))
    p2 = build_rule_payload(**_happy_inputs(md_name="MD-002"))
    assert p1.source_hash != p2.source_hash
    assert p1.tally_pattern == p2.tally_pattern
    assert p1.erpnext_account_template == p2.erpnext_account_template


# ---------------------------------------------------------------------------
# α-codification integration test
# ---------------------------------------------------------------------------
#
# This test encodes Phase A decision A-0: "DocType-write only. Inert
# until Item 8." Promoted rules sit in the ``Mapping Rule`` DocType
# but the live mapper reads from ``docs/seed_plan.json`` via
# ``JsonFileRuleSource``. The promotion workflow must not accidentally
# touch the seed plan.
#
# When Item 8 ships ``FrappeRuleSource``, the expected behavior
# changes: promoted rows should then flow into live mapping. The
# test at that point should be UPDATED (not deleted, not skipped) to
# codify the new γ semantic. Leave this docstring so the Item 8
# author notices.


def test_promoted_rule_does_not_appear_in_json_rule_source() -> None:
    """α codification: a rule payload built via the promotion path
    does NOT affect live Tier-1 matching, because the live path reads
    ``docs/seed_plan.json`` (which promotion does not touch).

    Failure of this test when Item 8 lands is **expected** — update
    to codify γ (FrappeRuleSource-backed live matching) instead of
    silencing."""
    from rgi_migration.mapper.rule_source import JsonFileRuleSource

    repo_root = Path(__file__).resolve().parents[2]
    seed_path = repo_root / "docs" / "seed_plan.json"
    if not seed_path.exists():
        pytest.skip(f"seed_plan.json not found at {seed_path}")

    payload = build_rule_payload(**_happy_inputs())

    rs = JsonFileRuleSource(seed_path)
    positive_rules = rs.positive_rules()
    promoted_hashes = {r.source_hash for r in positive_rules}
    promoted_sections = {r.source_section for r in positive_rules}

    # The promoted payload's identity markers must NOT already exist in
    # the seed plan (unless some unlucky collision — re-roll md_name if so).
    assert payload.source_hash not in promoted_hashes
    assert payload.source_section not in promoted_sections

    # And a second confirmation: promoted rules carry the
    # "session-MR-" prefix which no seed rule does.
    assert payload.source_section.startswith("session-MR-")
    for sec in promoted_sections:
        assert not sec.startswith("session-MR-"), (
            f"Seed contains a session-MR-prefixed rule ({sec!r}); the "
            "seed plan has been polluted by a promotion write — "
            "investigate before Item 5 ships."
        )
