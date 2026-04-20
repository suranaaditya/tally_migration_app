"""Unit tests for the Mapping Decision child → standalone migration patch.

The patch's ``execute()`` entry point uses Frappe ORM calls which are
not available in the local pytest environment. Tests exercise the
pure-logic ``migrate_decisions`` function by supplying fake
implementations of its Frappe-dependency callables.

Covers:

* Baseline migration — child rows produce standalone records with
  session Link populated and field values intact.
* Idempotency (single run) — a session that already has standalone
  records is skipped, child rows reported as skipped.
* Idempotency (two runs) — running the migration twice on the same
  fixture produces the same end state; second run inserts nothing.
* Empty bench — zero sessions returns (0, 0) cleanly, no side effects.
"""

from __future__ import annotations

from rgi_migration.patches.v1_0.migrate_decisions_to_standalone import (
    migrate_decisions,
)


# ---------------------------------------------------------------------------
# Fixtures — minimal fakes that duck-type what migrate_decisions needs
# ---------------------------------------------------------------------------


class _FakeChild:
    """Duck-types a Frappe child-table row. Field access via attribute."""

    def __init__(self, **fields):
        for k, v in fields.items():
            setattr(self, k, v)


class _FakeSession:
    """Duck-types a Frappe Document with a mapping_decisions child list."""

    def __init__(self, name, children):
        self.name = name
        self.mapping_decisions = children


def _build_fixture(sessions_spec):
    """Build (sessions_dict, standalone_counts_dict) from a spec.

    sessions_spec is a dict of session_name -> list of child field dicts.
    """
    sessions = {}
    counts = {}
    for session_name, children_fields in sessions_spec.items():
        sessions[session_name] = _FakeSession(
            session_name, [_FakeChild(**fields) for fields in children_fields]
        )
        counts[session_name] = 0
    return sessions, counts


def _run(sessions, counts, inserted):
    """Run migrate_decisions against the fixture and return its result."""

    def insert_standalone(data):
        inserted.append(data)
        counts[data["session"]] += 1

    return migrate_decisions(
        get_session_names=lambda: list(sessions.keys()),
        get_session_doc=lambda name: sessions[name],
        count_standalone=lambda name: counts[name],
        insert_standalone=insert_standalone,
        commit=lambda: None,
        log=lambda msg: None,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_migrates_child_rows_to_standalone_with_session_link():
    """Happy path — every child row becomes a standalone record with
    the session Link populated and its field values intact."""

    sessions, counts = _build_fixture({
        "CACSPU-2026-01": [
            {
                "tally_name": "Cash A/c",
                "tally_id": "101",
                "tier": "tier1_exact",
                "review_action": "Approved",
                "final_account": "Cash - CACSPU",
                "opening_dr": 100000.0,
                "opening_cr": 0.0,
                "net_amount": -100000.0,
                "net_side": "Dr",
                "idx": 1,  # framework field, must be filtered
            },
            {
                "tally_name": "Bank of Baroda - 4432",
                "tally_id": "102",
                "tier": "unmapped",
                "review_action": "Pending",
                "opening_dr": 500000.0,
                "opening_cr": 0.0,
                "idx": 2,
            },
        ],
        "JEWIPL-2026-01": [
            {
                "tally_name": "Sundry Creditor X",
                "tier": "pending_supplier_creation",
                "review_action": "Pending Supplier Creation",
                "opening_cr": 25000.0,
                "opening_dr": 0.0,
                "idx": 1,
            },
        ],
    })

    inserted = []
    total_migrated, total_skipped = _run(sessions, counts, inserted)

    assert total_migrated == 3
    assert total_skipped == 0
    assert len(inserted) == 3

    # Every inserted record carries doctype + session link
    for record in inserted:
        assert record["doctype"] == "Mapping Decision"
        assert record["session"] in {"CACSPU-2026-01", "JEWIPL-2026-01"}
        # Framework fields must NOT leak in
        assert "idx" not in record
        assert "parent" not in record

    # Field values intact end-to-end
    cash_record = next(r for r in inserted if r.get("tally_name") == "Cash A/c")
    assert cash_record["session"] == "CACSPU-2026-01"
    assert cash_record["tier"] == "tier1_exact"
    assert cash_record["review_action"] == "Approved"
    assert cash_record["final_account"] == "Cash - CACSPU"
    assert cash_record["opening_dr"] == 100000.0
    assert cash_record["net_side"] == "Dr"

    vendor_record = next(
        r for r in inserted if r.get("tally_name") == "Sundry Creditor X"
    )
    assert vendor_record["session"] == "JEWIPL-2026-01"
    assert vendor_record["tier"] == "pending_supplier_creation"
    assert vendor_record["opening_cr"] == 25000.0


def test_idempotent_skips_session_with_existing_standalone_records():
    """If a session already has standalone records linked, skip it —
    don't duplicate the child rows as additional standalone records."""

    sessions, counts = _build_fixture({
        "CACSPU-2026-01": [
            {"tally_name": "Cash", "tier": "tier1_exact", "idx": 1},
        ],
    })
    # Pre-populate: 1 standalone record already exists for this session
    counts["CACSPU-2026-01"] = 1

    inserted = []
    total_migrated, total_skipped = _run(sessions, counts, inserted)

    assert total_migrated == 0
    assert total_skipped == 1
    assert inserted == []


def test_idempotent_two_runs_produce_same_end_state():
    """Running migrate_decisions twice on the same fixture — second run
    is a no-op. Guards against accidental double-migration."""

    sessions, counts = _build_fixture({
        "CACSPU-2026-01": [
            {"tally_name": "A", "tier": "tier1_exact", "idx": 1},
            {"tally_name": "B", "tier": "unmapped", "idx": 2},
        ],
    })

    inserted = []

    first = _run(sessions, counts, inserted)
    assert first == (2, 0)
    assert counts["CACSPU-2026-01"] == 2
    assert len(inserted) == 2

    # Second run: both child rows are now skipped because the session
    # has 2 standalone records already.
    second = _run(sessions, counts, inserted)
    assert second == (0, 2)
    assert counts["CACSPU-2026-01"] == 2  # unchanged by second run
    assert len(inserted) == 2  # no new inserts


def test_empty_bench_no_sessions_returns_cleanly():
    """Matches the observed erp.jewonline.in baseline (0 sessions).
    Patch is a clean no-op; exit status (0, 0)."""

    inserted = []
    total_migrated, total_skipped = migrate_decisions(
        get_session_names=lambda: [],
        get_session_doc=lambda name: None,
        count_standalone=lambda name: 0,
        insert_standalone=lambda data: inserted.append(data),
        commit=lambda: None,
        log=lambda msg: None,
    )

    assert total_migrated == 0
    assert total_skipped == 0
    assert inserted == []


def test_session_with_no_child_decisions_is_skipped_silently():
    """Session exists but has no child mapping_decisions — patch
    shouldn't crash, shouldn't insert, shouldn't count as skipped."""

    sessions, counts = _build_fixture({
        "EMPTY-SESSION": [],
    })

    inserted = []
    total_migrated, total_skipped = _run(sessions, counts, inserted)

    assert total_migrated == 0
    assert total_skipped == 0
    assert inserted == []


def test_none_valued_fields_are_filtered_out():
    """Fields explicitly set to None on the child shouldn't be forwarded
    to the standalone insert — Frappe treats None and missing differently
    for Link fields (None would try to clear the field, missing uses the
    default). Safer to filter."""

    sessions, counts = _build_fixture({
        "S-001": [
            {
                "tally_name": "X",
                "tier": "unmapped",
                "final_account": None,  # should be filtered
                "matched_rule": None,  # should be filtered
                "opening_dr": 0.0,  # NOT filtered — 0.0 is meaningful
                "idx": 1,
            },
        ],
    })

    inserted = []
    _run(sessions, counts, inserted)

    assert len(inserted) == 1
    record = inserted[0]
    assert "final_account" not in record
    assert "matched_rule" not in record
    assert record["opening_dr"] == 0.0  # Falsy-but-not-None kept
