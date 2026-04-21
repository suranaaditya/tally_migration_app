"""Unit tests for the get_decision_detail query core.

Exercises ``rgi_migration.rgi_migration.page.md_review.query.fetch_decision_detail``,
the Frappe-free pure-logic implementation. The Frappe wrapper
``md_review.get_decision_detail`` can't be unit-tested from a local
pytest environment (no Frappe ORM available) — it's exercised via the
server-side bench-console smoke test instead.

Covers (per Commit 4a authorization, 4 tests):

1. Valid decision returns expected shape (decision + docinfo + company_abbr)
2. Nonexistent decision propagates the provider's not-found exception
3. Decision missing its session link raises ValueError (defensive guard
   against the Commit 1 reqd=1 invariant being violated)
4. Docinfo payload passes through from provider to response unchanged
"""

from __future__ import annotations

import pytest

from rgi_migration.rgi_migration.page.md_review.query import (
    fetch_decision_detail,
)


# ---------------------------------------------------------------------------
# Fakes — providers that fetch_decision_detail expects
# ---------------------------------------------------------------------------


def _fake_decision(
    name: str = "MD-TEST-001",
    session: str = "S-001",
    **extra,
) -> dict:
    """Build a minimal decision dict matching what Frappe's
    ``Document.as_dict()`` returns for a Mapping Decision.

    Only fields the pure function cares about are mandatory; extras
    pass through so tests can add domain-field assertions.
    """
    base = {
        "name": name,
        "session": session,
        "tally_name": "Test Ledger",
        "tier": "unmapped",
        "review_action": "Pending",
        "opening_dr": 1000.0,
        "opening_cr": 0.0,
        "net_amount": 1000.0,
        "net_side": "Dr",
    }
    base.update(extra)
    return base


def _fake_docinfo(with_assignments: bool = False) -> dict:
    """Minimal docinfo shape — matches Frappe's ``get_docinfo`` return."""
    return {
        "assignments": (
            [{"owner": "aditya@jewonline.in", "status": "Open"}]
            if with_assignments
            else []
        ),
        "comments": [],
        "versions": [],
        "attachments": [],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_valid_decision_returns_expected_shape():
    """Happy path — the response has all three keys with the right values
    threaded through from the providers."""

    decision_dict = _fake_decision(
        name="MD-2026-00042", session="TMS-CACSPU--00495", tally_name="Cash"
    )
    docinfo_dict = _fake_docinfo()

    permission_calls = []

    def _permission_check():
        permission_calls.append(True)

    result = fetch_decision_detail(
        decision_name="MD-2026-00042",
        get_decision_fn=lambda _name: decision_dict,
        get_session_company_fn=lambda _sn: "CACSPU",
        get_docinfo_fn=lambda _name: docinfo_dict,
        permission_check_fn=_permission_check,
    )

    # Response shape
    assert set(result.keys()) == {"decision", "docinfo", "session_company_abbr"}
    # Threaded values
    assert result["decision"] == decision_dict
    assert result["decision"]["tally_name"] == "Cash"
    assert result["docinfo"] == docinfo_dict
    assert result["session_company_abbr"] == "CACSPU"
    # Permission check was called exactly once
    assert permission_calls == [True]


def test_nonexistent_decision_raises_does_not_exist():
    """When the provider raises (simulating Frappe's DoesNotExistError),
    the exception propagates through the pure function unchanged."""

    class _FakeDoesNotExistError(Exception):
        pass

    def _raising_get_decision(_name):
        raise _FakeDoesNotExistError("Mapping Decision MD-MISSING not found")

    with pytest.raises(_FakeDoesNotExistError, match="not found"):
        fetch_decision_detail(
            decision_name="MD-MISSING",
            get_decision_fn=_raising_get_decision,
            get_session_company_fn=lambda _sn: "should not be called",
            get_docinfo_fn=lambda _name: {},
            permission_check_fn=lambda: None,
        )


def test_missing_session_field_raises_validation_error():
    """Defensive guard — if a Mapping Decision somehow exists without a
    ``session`` field populated (violates the Commit 1 reqd=1 invariant),
    the pure function refuses to proceed. Protects against corrupt-state
    edge cases that would otherwise produce silently-broken detail-pane
    behavior."""

    orphan_decision = _fake_decision(name="MD-ORPHAN", session="")

    permission_calls = []

    with pytest.raises(ValueError, match="no session link"):
        fetch_decision_detail(
            decision_name="MD-ORPHAN",
            get_decision_fn=lambda _name: orphan_decision,
            get_session_company_fn=lambda _sn: "WILL NOT BE CALLED",
            get_docinfo_fn=lambda _name: {},
            permission_check_fn=lambda: permission_calls.append(True),
        )

    # Permission check should NOT have been called — we bail before that
    # gate, because there's no session to permission-check against.
    assert permission_calls == []


def test_docinfo_passthrough():
    """The docinfo provider's return value is passed through to the
    response unchanged — nothing mutates or filters the payload. Section
    5 (Assignment) depends on ``docinfo['assignments']`` reaching the
    frontend intact."""

    rich_docinfo = _fake_docinfo(with_assignments=True)
    # Add a couple of fields that the pure function doesn't know about,
    # to prove it doesn't project or filter.
    rich_docinfo["custom_extension"] = {"future_field": 42}
    rich_docinfo["comments"] = [
        {"name": "C-1", "content": "test comment"},
    ]

    result = fetch_decision_detail(
        decision_name="MD-TEST",
        get_decision_fn=lambda _name: _fake_decision(),
        get_session_company_fn=lambda _sn: "CACSPU",
        get_docinfo_fn=lambda _name: rich_docinfo,
        permission_check_fn=lambda: None,
    )

    # Exact-object identity check — proves no copy / filter happened
    assert result["docinfo"] is rich_docinfo
    # Specific fields survive
    assert result["docinfo"]["assignments"][0]["owner"] == "aditya@jewonline.in"
    assert result["docinfo"]["custom_extension"]["future_field"] == 42
    assert result["docinfo"]["comments"][0]["name"] == "C-1"
