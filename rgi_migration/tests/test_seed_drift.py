"""Pure tests for `compute_seed_drift` — the one-shot probe that
surfaces divergence between ``docs/seed_plan.json`` and DB rows with
``created_from='seed'``.

Per Phase A Q3: warn-only (not a runtime gate). Probe runs in Phase C
bench smoke against the real DB; this file covers the pure logic.
"""

from __future__ import annotations

import json
from pathlib import Path

from rgi_migration.mapper.seed_drift import compute_seed_drift


def _json_rule(**overrides) -> dict:
    base = {
        "source_section": "§4.1",
        "source_hash": "abc123",
        "rule_name": "test",
        "is_anti_pattern": 0,
        "tally_pattern": "Foo",
        "tally_match_mode": "exact_ci",
        "erpnext_account_template": "Foo - {ABBR}",
        "applicable_root_type": "Any",
    }
    base.update(overrides)
    return base


def _db_row(**overrides) -> dict:
    base = {
        "name": "MR-00001",
        "source_section": "§4.1",
        "source_hash": "abc123",
        "is_anti_pattern": 0,
        "tally_pattern": "Foo",
        "tally_match_mode": "exact_ci",
        "erpnext_account_template": "Foo - {ABBR}",
        "applicable_root_type": "Any",
    }
    base.update(overrides)
    return base


def test_no_drift_returns_empty() -> None:
    assert compute_seed_drift([_json_rule()], [_db_row()]) == []


def test_source_hash_drift_warns() -> None:
    out = compute_seed_drift(
        [_json_rule(source_hash="abc123")],
        [_db_row(source_hash="xyz789")],
    )
    assert len(out) == 1
    assert "source_hash" in out[0]
    assert "abc123" in out[0] and "xyz789" in out[0]


def test_template_drift_warns() -> None:
    out = compute_seed_drift(
        [_json_rule(erpnext_account_template="Foo - {ABBR}")],
        [_db_row(erpnext_account_template="Bar - {ABBR}")],
    )
    assert len(out) == 1
    assert "erpnext_account_template" in out[0]


def test_missing_db_row_warns() -> None:
    out = compute_seed_drift([_json_rule(source_section="§4.99")], [])
    assert len(out) == 1
    assert "§4.99" in out[0]
    assert "no matching DB row" in out[0]


def test_extra_db_row_warns() -> None:
    out = compute_seed_drift(
        [],
        [_db_row(source_section="§4.99", name="MR-99999")],
    )
    assert len(out) == 1
    assert "§4.99" in out[0]
    assert "no matching JSON rule" in out[0]


def test_multiple_drifts_all_reported() -> None:
    out = compute_seed_drift(
        [_json_rule(source_hash="J1", tally_pattern="J_pat")],
        [_db_row(source_hash="D1", tally_pattern="D_pat")],
    )
    assert len(out) == 2


def test_anti_pattern_drift_warns() -> None:
    out = compute_seed_drift(
        [_json_rule(is_anti_pattern=0)],
        [_db_row(is_anti_pattern=1)],
    )
    assert len(out) == 1
    assert "is_anti_pattern" in out[0]


def test_real_seed_plan_self_consistent() -> None:
    """Sanity check: seed_plan.json rules are internally valid — every
    rule has a source_section and a source_hash. This guards against
    malformed seed rows reaching the drift probe on bench."""
    app_root = Path(__file__).resolve().parents[2]
    seed_path = app_root / "docs" / "seed_plan.json"
    data = json.loads(seed_path.read_text(encoding="utf-8"))
    all_rules = data["positive_rules"] + data["anti_pattern_rules"]
    assert len(all_rules) == 22
    sections = set()
    for r in all_rules:
        assert r["source_section"], f"rule missing source_section: {r.get('rule_name')}"
        assert r["source_hash"], f"rule missing source_hash: {r.get('rule_name')}"
        sections.add(r["source_section"])
    assert len(sections) == 22, "source_section collisions in seed_plan.json"
