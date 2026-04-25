"""Tests for Item 8.5 Stage 3 — multi-pass partial generation.

Covers:

* Pass-number resolution helpers (``pass_tracking`` module) — pure
  functions, unit-testable without Frappe.
* Migration Pass DocType schema shape (field presence + types).
* Mapping Decision ``generated_in_pass`` field presence.
* Session status enum extended with ``Partial Submitted``.
* Pass-aware artefact reference + filename suffix logic.
* Backfill patch pure function (dependency-injected) — synthetic
  scenarios with Submitted sessions.
* Static checks for Generate Pass N button, Deferred preset, lock
  icon wiring in md-review, session form intro helper.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PASS_JSON = ROOT / "rgi_migration" / "rgi_migration" / "doctype" / "migration_pass" / "migration_pass.json"
MD_JSON = ROOT / "rgi_migration" / "rgi_migration" / "doctype" / "mapping_decision" / "mapping_decision.json"
SESSION_JSON = ROOT / "rgi_migration" / "rgi_migration" / "doctype" / "tally_migration_session" / "tally_migration_session.json"
SESSION_JS = ROOT / "rgi_migration" / "rgi_migration" / "doctype" / "tally_migration_session" / "tally_migration_session.js"
SESSION_PY = ROOT / "rgi_migration" / "rgi_migration" / "doctype" / "tally_migration_session" / "tally_migration_session.py"
MD_REVIEW_JS = ROOT / "rgi_migration" / "rgi_migration" / "page" / "md_review" / "md_review.js"
MD_REVIEW_PY = ROOT / "rgi_migration" / "rgi_migration" / "page" / "md_review" / "md_review.py"
PATCHES_TXT = ROOT / "rgi_migration" / "patches.txt"
BACKFILL_PATCH = ROOT / "rgi_migration" / "patches" / "v1_0" / "backfill_pass_1_for_submitted_sessions.py"
OPENING_JE = ROOT / "rgi_migration" / "generators" / "opening_je.py"
ADVANCE_JE = ROOT / "rgi_migration" / "generators" / "advance_je.py"
OIT_CSV = ROOT / "rgi_migration" / "generators" / "oit_csv.py"
STUDENTS_CSV = ROOT / "rgi_migration" / "generators" / "students_csv.py"
SYNTHETIC_SESSIONS = ROOT / "rgi_migration" / "session" / "synthetic_sessions.py"


# ============================================================================
# Schema — Migration Pass child DocType
# ============================================================================


class TestMigrationPassSchema:
    def test_file_exists(self) -> None:
        assert MIGRATION_PASS_JSON.exists(), (
            "Migration Pass DocType JSON missing — Stage 3 schema not deployed."
        )

    def test_is_child_table(self) -> None:
        data = json.loads(MIGRATION_PASS_JSON.read_text(encoding="utf-8"))
        assert data.get("istable") == 1, (
            "Migration Pass must be istable=1 (child DocType) so it can "
            "be embedded as migration_passes child table on Session."
        )
        assert data.get("name") == "Migration Pass"
        assert data.get("module") == "Rgi Migration"

    def test_required_fields_present(self) -> None:
        data = json.loads(MIGRATION_PASS_JSON.read_text(encoding="utf-8"))
        fieldnames = {f["fieldname"] for f in data["fields"]}
        required = {
            # Core pass-tracking
            "pass_number", "pass_status", "generated_at", "submitted_at",
            # Artefact links (4)
            "main_je_name", "advance_je_name",
            "oit_file_url", "students_file_url",
            # Reference IDs
            "reference_id_main", "reference_id_advance",
            # Counts
            "decisions_included_count", "decisions_deferred_count",
            "student_decisions_count",
            # Aggregates
            "temp_opening_contribution",
            # Audit log
            "generator_error_log",
        }
        missing = required - fieldnames
        assert not missing, (
            f"Migration Pass DocType missing required fields: {missing}"
        )

    def test_pass_status_select_options(self) -> None:
        data = json.loads(MIGRATION_PASS_JSON.read_text(encoding="utf-8"))
        for f in data["fields"]:
            if f["fieldname"] == "pass_status":
                assert f["options"] == "Draft\nSubmitted\nFailed", (
                    "pass_status enum must be Draft|Submitted|Failed "
                    "per Q-A resolution."
                )
                return
        pytest.fail("pass_status field not in Migration Pass DocType")

    def test_pass_number_required(self) -> None:
        data = json.loads(MIGRATION_PASS_JSON.read_text(encoding="utf-8"))
        for f in data["fields"]:
            if f["fieldname"] == "pass_number":
                assert f.get("reqd") == 1, "pass_number must be reqd"
                assert f["fieldtype"] == "Int"
                return
        pytest.fail("pass_number not in Migration Pass DocType")


# ============================================================================
# Schema — Session + Mapping Decision extensions
# ============================================================================


class TestSessionSchemaStage3:
    def test_status_enum_has_partial_submitted(self) -> None:
        data = json.loads(SESSION_JSON.read_text(encoding="utf-8"))
        for f in data["fields"]:
            if f["fieldname"] == "status":
                assert "Partial Submitted" in f["options"], (
                    "Session status enum missing 'Partial Submitted' — "
                    "Stage 3 Q-D new non-terminal state required."
                )
                return
        pytest.fail("status field not in Session DocType")

    def test_migration_passes_table_registered(self) -> None:
        data = json.loads(SESSION_JSON.read_text(encoding="utf-8"))
        fieldnames_to_options = {
            f["fieldname"]: f.get("options") for f in data["fields"]
        }
        assert "migration_passes" in fieldnames_to_options, (
            "Session missing migration_passes child-table field."
        )
        assert fieldnames_to_options["migration_passes"] == "Migration Pass"

    def test_migration_passes_in_field_order(self) -> None:
        data = json.loads(SESSION_JSON.read_text(encoding="utf-8"))
        assert "migration_passes" in data["field_order"], (
            "migration_passes not in Session's field_order — won't render."
        )


class TestMappingDecisionSchemaStage3:
    def test_generated_in_pass_field_present(self) -> None:
        data = json.loads(MD_JSON.read_text(encoding="utf-8"))
        for f in data["fields"]:
            if f["fieldname"] == "generated_in_pass":
                assert f["fieldtype"] == "Int", (
                    "generated_in_pass must be Int (1-based pass counter)."
                )
                assert f.get("read_only") == 1, (
                    "generated_in_pass must be read_only — only generators "
                    "via bulk UPDATE should write it."
                )
                return
        pytest.fail("generated_in_pass field missing from Mapping Decision")

    def test_generated_in_pass_in_field_order(self) -> None:
        data = json.loads(MD_JSON.read_text(encoding="utf-8"))
        assert "generated_in_pass" in data["field_order"]


# ============================================================================
# pass_tracking helpers (pure, Frappe-free)
# ============================================================================


class _FakePassRow:
    """Plain object mimicking a Migration Pass child row for unit tests."""

    def __init__(self, pass_number: int, pass_status: str) -> None:
        self.pass_number = pass_number
        self.pass_status = pass_status


class _FakeSession:
    def __init__(self, passes: list[_FakePassRow]) -> None:
        self.migration_passes = passes


class TestPassTrackingDetermine:
    def test_no_passes_returns_1(self) -> None:
        from rgi_migration.generators.pass_tracking import (
            determine_current_pass_number,
        )
        assert determine_current_pass_number(_FakeSession([])) == 1

    def test_single_draft_returns_that_number(self) -> None:
        from rgi_migration.generators.pass_tracking import (
            determine_current_pass_number,
        )
        session = _FakeSession([_FakePassRow(1, "Draft")])
        assert determine_current_pass_number(session) == 1

    def test_single_submitted_returns_next(self) -> None:
        from rgi_migration.generators.pass_tracking import (
            determine_current_pass_number,
        )
        session = _FakeSession([_FakePassRow(1, "Submitted")])
        assert determine_current_pass_number(session) == 2

    def test_submitted_plus_draft_returns_draft(self) -> None:
        """Intra-pass regen: Pass 1 Submitted, Pass 2 Draft → 2."""
        from rgi_migration.generators.pass_tracking import (
            determine_current_pass_number,
        )
        session = _FakeSession([
            _FakePassRow(1, "Submitted"),
            _FakePassRow(2, "Draft"),
        ])
        assert determine_current_pass_number(session) == 2

    def test_two_submitted_returns_three(self) -> None:
        from rgi_migration.generators.pass_tracking import (
            determine_current_pass_number,
        )
        session = _FakeSession([
            _FakePassRow(1, "Submitted"),
            _FakePassRow(2, "Submitted"),
        ])
        assert determine_current_pass_number(session) == 3

    def test_failed_treated_like_draft(self) -> None:
        from rgi_migration.generators.pass_tracking import (
            determine_current_pass_number,
        )
        session = _FakeSession([_FakePassRow(1, "Failed")])
        assert determine_current_pass_number(session) == 1


class TestPassTrackingReferenceSuffix:
    def test_pass_1_no_suffix(self) -> None:
        from rgi_migration.generators.pass_tracking import build_reference_suffix
        assert build_reference_suffix(1) == ""

    def test_pass_2_suffix(self) -> None:
        from rgi_migration.generators.pass_tracking import build_reference_suffix
        assert build_reference_suffix(2) == "-P2"

    def test_pass_5_suffix(self) -> None:
        from rgi_migration.generators.pass_tracking import build_reference_suffix
        assert build_reference_suffix(5) == "-P5"

    def test_pass_0_and_negative_no_suffix(self) -> None:
        """Defensive: pass_number <= 1 returns empty (covers 0 / negative)."""
        from rgi_migration.generators.pass_tracking import build_reference_suffix
        assert build_reference_suffix(0) == ""
        assert build_reference_suffix(-1) == ""


class TestPassTrackingFilenameSuffix:
    def test_pass_1_no_suffix(self) -> None:
        from rgi_migration.generators.pass_tracking import build_filename_suffix
        assert build_filename_suffix(1) == ""

    def test_pass_2_suffix(self) -> None:
        from rgi_migration.generators.pass_tracking import build_filename_suffix
        assert build_filename_suffix(2) == "_p2"

    def test_pass_3_suffix(self) -> None:
        from rgi_migration.generators.pass_tracking import build_filename_suffix
        assert build_filename_suffix(3) == "_p3"


class TestIsFreshPassStart:
    def test_empty_session_is_fresh(self) -> None:
        from rgi_migration.generators.pass_tracking import is_fresh_pass_start
        assert is_fresh_pass_start(_FakeSession([])) is True

    def test_draft_pass_is_not_fresh(self) -> None:
        from rgi_migration.generators.pass_tracking import is_fresh_pass_start
        session = _FakeSession([_FakePassRow(1, "Draft")])
        assert is_fresh_pass_start(session) is False

    def test_all_submitted_is_fresh(self) -> None:
        from rgi_migration.generators.pass_tracking import is_fresh_pass_start
        session = _FakeSession([
            _FakePassRow(1, "Submitted"),
            _FakePassRow(2, "Submitted"),
        ])
        assert is_fresh_pass_start(session) is True


# ============================================================================
# Generator default_reference_id — pass-aware
# ============================================================================


class TestGeneratorReferenceIdsPassAware:
    def test_opening_je_pass_1(self) -> None:
        from rgi_migration.generators.opening_je import _default_reference_id
        assert _default_reference_id("CACSPU", "2025-2026") == "OB-CACSPU-2025-01"
        assert _default_reference_id(
            "CACSPU", "2025-2026", pass_number=1,
        ) == "OB-CACSPU-2025-01"

    def test_opening_je_pass_2(self) -> None:
        from rgi_migration.generators.opening_je import _default_reference_id
        assert _default_reference_id(
            "CACSPU", "2025-2026", pass_number=2,
        ) == "OB-CACSPU-2025-01-P2"

    def test_advance_je_pass_1(self) -> None:
        from rgi_migration.generators.advance_je import _default_reference_id
        assert _default_reference_id(
            "CACSPU", "2025-2026", pass_number=1,
        ) == "OB-CACSPU-2025-02"

    def test_advance_je_pass_3(self) -> None:
        from rgi_migration.generators.advance_je import _default_reference_id
        assert _default_reference_id(
            "CACSPU", "2025-2026", pass_number=3,
        ) == "OB-CACSPU-2025-02-P3"


# ============================================================================
# Backfill patch — dep-injected pure function
# ============================================================================


class _FakeSessionDoc:
    def __init__(
        self, name: str, *,
        completed_at=None, modified=None, generated_je_draft=None,
        generated_advance_je=None, generated_oit_file=None,
        student_ledger_file=None, generated_je_reference=None,
        generated_advance_je_reference=None, student_ledger_count=0,
        temp_opening_amount=0.0, migration_passes=None,
    ) -> None:
        self.name = name
        self.completed_at = completed_at
        self.modified = modified
        self.generated_je_draft = generated_je_draft
        self.generated_advance_je = generated_advance_je
        self.generated_oit_file = generated_oit_file
        self.student_ledger_file = student_ledger_file
        self.generated_je_reference = generated_je_reference
        self.generated_advance_je_reference = generated_advance_je_reference
        self.student_ledger_count = student_ledger_count
        self.temp_opening_amount = temp_opening_amount
        self.migration_passes = list(migration_passes or [])
        self.flags = type("Flags", (), {"ignore_permissions": False})()

    def append(self, field: str, row: dict) -> None:
        getattr(self, field).append(row)

    def save(self) -> None:
        pass


class TestBackfillPass1:
    def test_no_sessions_no_op(self) -> None:
        from rgi_migration.patches.v1_0.backfill_pass_1_for_submitted_sessions import (
            backfill_pass_1,
        )
        counters = backfill_pass_1(
            get_submitted_session_names=lambda: [],
            get_session_doc=lambda _: None,
            get_decisions_for_session=lambda _: [],
            set_decision_pass=lambda _n, _p: None,
            save_session=lambda _s: None,
            now_datetime=lambda: "2026-04-25T00:00:00",
        )
        assert counters == {
            "sessions_backfilled": 0,
            "decisions_stamped": 0,
            "sessions_skipped_has_passes": 0,
        }

    def test_sessions_with_passes_skipped(self) -> None:
        from rgi_migration.patches.v1_0.backfill_pass_1_for_submitted_sessions import (
            backfill_pass_1,
        )
        session = _FakeSessionDoc("TMS-TEST", migration_passes=[{"pass_number": 1}])
        counters = backfill_pass_1(
            get_submitted_session_names=lambda: ["TMS-TEST"],
            get_session_doc=lambda _: session,
            get_decisions_for_session=lambda _: [],
            set_decision_pass=lambda _n, _p: None,
            save_session=lambda _s: None,
            now_datetime=lambda: "2026-04-25T00:00:00",
        )
        assert counters["sessions_skipped_has_passes"] == 1
        assert counters["sessions_backfilled"] == 0

    def test_fresh_submitted_session_gets_pass_1_row(self) -> None:
        from rgi_migration.patches.v1_0.backfill_pass_1_for_submitted_sessions import (
            backfill_pass_1,
        )
        session = _FakeSessionDoc(
            "TMS-TEST",
            generated_je_draft="JE-001",
            generated_advance_je="JE-002",
            generated_oit_file="/files/oit.csv",
            student_ledger_file="/files/students.csv",
            generated_je_reference="OB-TEST-2025-01",
            student_ledger_count=10,
            temp_opening_amount=500.0,
        )
        decisions = [
            {"name": "MD-001", "review_action": "Approved"},
            {"name": "MD-002", "review_action": "Approved"},
            {"name": "MD-003", "review_action": "Rejected"},  # not emitted
            {"name": "MD-004", "review_action": "Pending"},  # excluded
        ]
        stamped: list[tuple[str, int]] = []
        counters = backfill_pass_1(
            get_submitted_session_names=lambda: ["TMS-TEST"],
            get_session_doc=lambda _: session,
            get_decisions_for_session=lambda _: decisions,
            set_decision_pass=lambda n, p: stamped.append((n, p)),
            save_session=lambda _s: None,
            now_datetime=lambda: "2026-04-25T00:00:00",
        )
        assert counters["sessions_backfilled"] == 1
        # Only Approved rows (2) should be stamped. Rejected + Pending excluded.
        assert counters["decisions_stamped"] == 2
        assert ("MD-001", 1) in stamped
        assert ("MD-002", 1) in stamped
        assert ("MD-003", 1) not in stamped
        assert ("MD-004", 1) not in stamped
        # Pass 1 row synthesized with artefact links mirrored from session
        assert len(session.migration_passes) == 1
        row = session.migration_passes[0]
        assert row["pass_number"] == 1
        assert row["pass_status"] == "Submitted"
        assert row["main_je_name"] == "JE-001"
        assert row["advance_je_name"] == "JE-002"
        assert row["decisions_included_count"] == 2
        assert row["student_decisions_count"] == 10
        assert row["temp_opening_contribution"] == 500.0


# ============================================================================
# Patches registry
# ============================================================================


class TestPatchesRegistry:
    def test_backfill_patch_registered(self) -> None:
        src = PATCHES_TXT.read_text(encoding="utf-8")
        assert (
            "rgi_migration.patches.v1_0.backfill_pass_1_for_submitted_sessions"
            in src
        ), "backfill_pass_1 patch not registered in patches.txt"


# ============================================================================
# Whitelist source — status transitions + pass-aware flow
# ============================================================================


class TestGenerateAllStage3Source:
    def test_partial_submitted_in_allowed_from(self) -> None:
        src = SESSION_PY.read_text(encoding="utf-8")
        # Generate All must accept Reviewing, Generated, AND Partial Submitted
        assert "_STATUS_PARTIAL_SUBMITTED" in src
        assert "_STATUS_REVIEWING, _STATUS_GENERATED, _STATUS_PARTIAL_SUBMITTED" in src

    def test_pass_number_in_return_dict(self) -> None:
        src = SESSION_PY.read_text(encoding="utf-8")
        assert '"pass_number": pass_number' in src, (
            "generate_all must return pass_number to the JS wrapper "
            "so the UI can label the success alert."
        )

    def test_upsert_migration_pass_row_helper_exists(self) -> None:
        src = SESSION_PY.read_text(encoding="utf-8")
        assert "_upsert_migration_pass_row" in src

    def test_stamp_pass_on_decisions_called(self) -> None:
        src = SESSION_PY.read_text(encoding="utf-8")
        assert "stamp_pass_on_decisions(session_name, pass_number)" in src

    def test_intra_pass_regen_clears_migration_pass_links(self) -> None:
        """Item 8.5 Stage 3 Phase C bug fix: intra-pass regeneration must
        clear the in-progress Migration Pass row's artefact Link fields
        up-front so Frappe's session.save() validation doesn't fail on
        stale Link references when generators delete and recreate
        artefacts. See docs/mapper_design_notes.md §5."""
        src = SESSION_PY.read_text(encoding="utf-8")
        # The fix block lives inside generate_all, AFTER the
        # is_fresh_pass_start branch and BEFORE the status=Generating
        # transition.
        assert "if not fresh_pass:" in src
        # The block must clear all artefact link fields on the
        # in-progress Draft row.
        assert "p.main_je_name = None" in src
        assert "p.advance_je_name = None" in src
        assert "p.oit_file_url = None" in src


class TestMarkSubmittedStage3Source:
    def test_branches_on_deferred_count(self) -> None:
        src = SESSION_PY.read_text(encoding="utf-8")
        # mark_submitted should pick PARTIAL_SUBMITTED vs SUBMITTED based on count
        assert "if deferred_count > 0:" in src
        assert "_STATUS_PARTIAL_SUBMITTED" in src

    def test_validates_current_pass_je_only(self) -> None:
        src = SESSION_PY.read_text(encoding="utf-8")
        # Per Q-E, validate Pass N's JEs (not cumulative)
        assert "current_pass_row" in src
        assert "main_je_name" in src and "advance_je_name" in src

    def test_pass_number_in_return(self) -> None:
        src = SESSION_PY.read_text(encoding="utf-8")
        assert '"pass_number": current_pass_number' in src


class TestResetParseStage3Source:
    def test_refuses_when_submitted_pass_exists(self) -> None:
        src = SESSION_PY.read_text(encoding="utf-8")
        assert "Submitted Migration Pass" in src, (
            "reset_parse must hard-refuse on sessions with any Submitted "
            "Migration Pass (Q-M)."
        )
        assert "passes_swept" in src, (
            "reset_parse must also sweep Draft/Failed Migration Pass rows."
        )


# ============================================================================
# Generator source — pass-aware loader + naming
# ============================================================================


class TestGeneratorSourceStage3:
    def test_opening_je_uses_pass_aware_loader(self) -> None:
        src = OPENING_JE.read_text(encoding="utf-8")
        assert "load_pass_pending_decisions_from_session" in src
        assert "determine_current_pass_number" in src

    def test_advance_je_uses_pass_aware_loader(self) -> None:
        src = ADVANCE_JE.read_text(encoding="utf-8")
        assert "load_pass_pending_decisions_from_session" in src
        assert "determine_current_pass_number" in src

    def test_oit_csv_uses_pass_aware_loader_and_filename_suffix(self) -> None:
        src = OIT_CSV.read_text(encoding="utf-8")
        assert "load_pass_pending_decisions_from_session" in src
        assert "build_filename_suffix(pass_number)" in src

    def test_students_csv_uses_filename_suffix(self) -> None:
        """Students CSV doesn't use MDs but does get pass-aware filename."""
        src = STUDENTS_CSV.read_text(encoding="utf-8")
        assert "build_filename_suffix(pass_number)" in src
        assert "determine_current_pass_number" in src


# ============================================================================
# md-review — Deferred preset + lock
# ============================================================================


class TestMdReviewDeferredPreset:
    def test_deferred_preset_button_rendered(self) -> None:
        src = MD_REVIEW_JS.read_text(encoding="utf-8")
        assert 'data-preset="deferred"' in src, (
            "Deferred preset pill must exist in FilterBar render HTML."
        )

    def test_deferred_preset_handled_in_getFilters(self) -> None:
        src = MD_REVIEW_JS.read_text(encoding="utf-8")
        assert 'this.state.preset === "deferred"' in src
        assert 'filters.review_action = "Deferred"' in src

    def test_setDeferredCount_helper_present(self) -> None:
        src = MD_REVIEW_JS.read_text(encoding="utf-8")
        assert "setDeferredCount" in src

    def test_refresh_deferred_count_controller_method(self) -> None:
        src = MD_REVIEW_JS.read_text(encoding="utf-8")
        assert "refresh_deferred_count" in src


class TestMdReviewLockOnSubmittedPass:
    def test_decision_lock_info_whitelist_exists(self) -> None:
        src = MD_REVIEW_PY.read_text(encoding="utf-8")
        assert "def decision_lock_info" in src
        assert "_decision_locked_pass" in src

    def test_save_decision_enforces_lock(self) -> None:
        src = MD_REVIEW_PY.read_text(encoding="utf-8")
        # Must call the lock helper BEFORE mutating the doc, and throw on hit.
        assert "locked_pass = _decision_locked_pass" in src
        # f-string is split across lines; check for the unique tail.
        assert "passes are immutable" in src


# ============================================================================
# Session form JS — Generate Pass N, Mark Pass N, intro helper
# ============================================================================


class TestSessionFormPassAwareButtons:
    def test_generate_partial_submitted_allowed(self) -> None:
        src = SESSION_JS.read_text(encoding="utf-8")
        assert '"Partial Submitted"' in src, (
            "Partial Submitted must be in generate_allowed_from set."
        )

    def test_generate_button_label_has_pass_number(self) -> None:
        src = SESSION_JS.read_text(encoding="utf-8")
        assert "Generate Pass {0}" in src, (
            "Dynamic Pass N button label missing."
        )
        assert "Regenerate Pass {0}" in src

    def test_mark_submitted_has_pass_number_label(self) -> None:
        src = SESSION_JS.read_text(encoding="utf-8")
        assert "Mark Pass {0} Submitted" in src

    def test_render_pass_history_intro_helper(self) -> None:
        src = SESSION_JS.read_text(encoding="utf-8")
        assert "function renderPassHistoryIntro(frm)" in src
        assert "frm.set_intro" in src

    def test_reset_parse_hidden_on_submitted_pass(self) -> None:
        src = SESSION_JS.read_text(encoding="utf-8")
        assert "has_submitted_pass" in src, (
            "Reset Parse button must check for Submitted passes and hide "
            "itself (Q-M). Server-side refusal is authoritative but UI "
            "should not even present the button."
        )


# ============================================================================
# Synthetic session helpers — Stage 3 additions (Q-P)
# ============================================================================


class TestPhaseDExtensionS7DisabledState:
    """Item 8.5 Stage 3 Phase D extension: Generate Pass N button must
    be visually disabled with a tooltip when zero Deferred decisions
    have been resolved since the previous pass submitted (Q-J).
    Server-side enforcement exists; client affordance was missing in
    Phase B."""

    def test_pre_flight_resolvable_count_query_present(self) -> None:
        src = SESSION_JS.read_text(encoding="utf-8")
        # Must call frappe.client.get_count with the right filters
        # immediately after rendering the Partial Submitted button.
        assert 'if (status === "Partial Submitted") {' in src
        assert '"Mapping Decision"' in src
        assert "generated_in_pass: " in src  # JS object key (unquoted)
        assert '"is", "not set"' in src
        assert '"not in"' in src
        # Must include the same non-emittable states as the server
        # filter in tally_migration_session.py:758.
        assert '"Deferred"' in src
        assert '"Rejected"' in src
        assert '"Pending Account Creation"' in src

    def test_disabled_state_applied_when_resolvable_zero(self) -> None:
        src = SESSION_JS.read_text(encoding="utf-8")
        # Check for the disable + tooltip + opacity-fade triplet
        assert '$generate_btn.prop("disabled", true)' in src
        assert 'Resolve at least one Deferred decision before generating Pass' in src
        assert '$generate_btn.css("opacity"' in src

    def test_button_captured_for_post_render_mutation(self) -> None:
        """The button must be captured (not just chained) so the
        async pre-flight callback can mutate it after fetch returns."""
        src = SESSION_JS.read_text(encoding="utf-8")
        assert "const $generate_btn = frm.add_custom_button" in src


class TestPhaseDExtensionS3LockIcon:
    """Item 8.5 Stage 3 Phase D extension: lock icon affordance for
    decisions stamped with a Submitted pass (Q-L). Server-side refuse
    in save_decision is authoritative; this is the visual UX."""

    def test_master_pane_render_row_includes_lock_check(self) -> None:
        src = MD_REVIEW_JS.read_text(encoding="utf-8")
        # The render path must consult pass_status_map and conditionally
        # add the lock decoration only when pass is Submitted.
        assert "const stamped_pass = decision.generated_in_pass" in src
        assert "this.pass_status_map[stamped_pass]" in src
        assert 'pass_meta.pass_status === "Submitted"' in src

    def test_lock_glyph_html_with_tooltip(self) -> None:
        src = MD_REVIEW_JS.read_text(encoding="utf-8")
        assert 'class="md-row-lock"' in src
        assert "Locked — included in Pass" in src
        assert "Use ERPNext Amend for corrections" in src

    def test_locked_row_class_added(self) -> None:
        src = MD_REVIEW_JS.read_text(encoding="utf-8")
        # Row gets `.locked` class so CSS can mute its appearance
        assert 'is_locked ? "master-row locked" : "master-row"' in src

    def test_pass_status_map_setter_re_renders(self) -> None:
        """setPassStatusMap must trigger a re-render so newly-Submitted
        passes' lock icons appear without a full page reload."""
        src = MD_REVIEW_JS.read_text(encoding="utf-8")
        assert "setPassStatusMap(map)" in src
        # Re-render call inside setter
        assert "this._render_rows(this.current_decisions)" in src

    def test_controller_refresh_pass_status_map_method(self) -> None:
        src = MD_REVIEW_JS.read_text(encoding="utf-8")
        assert "refresh_pass_status_map" in src
        # Initial fetch on page load
        assert "controller.refresh_pass_status_map()" in src

    def test_css_lock_decoration_rules(self) -> None:
        css_path = ROOT / "rgi_migration" / "rgi_migration" / "page" / "md_review" / "md_review.css"
        src = css_path.read_text(encoding="utf-8")
        assert ".master-row.locked" in src
        assert ".md-row-lock" in src
        # Cursor: help on the glyph for hover-tooltip affordance
        assert "cursor: help" in src

    def test_default_decision_fields_includes_generated_in_pass(self) -> None:
        """Item 8.5 Stage 3 Phase D extension bug: master pane's
        DEFAULT_DECISION_FIELDS must include generated_in_pass; otherwise
        the JS row render's lock-icon branch never fires (decision
        field is undefined → stamped_pass=0 → no lock)."""
        from rgi_migration.rgi_migration.page.md_review.query import (
            DEFAULT_DECISION_FIELDS,
        )
        assert "generated_in_pass" in DEFAULT_DECISION_FIELDS, (
            "generated_in_pass must be in DEFAULT_DECISION_FIELDS — "
            "without it the master pane's lock-icon decoration code "
            "starves on the data layer."
        )

    def test_draft_pass_decision_NOT_locked(self) -> None:
        """Edge case from spec: an MD stamped generated_in_pass=N where
        pass N is still Draft must NOT be rendered locked. The render
        check is `pass_meta.pass_status === 'Submitted'` — Draft / Failed
        statuses skip the lock branch."""
        src = MD_REVIEW_JS.read_text(encoding="utf-8")
        # The conjunction must include the Submitted check; otherwise
        # any stamped MD would be locked, breaking intra-pass review.
        assert "pass_meta && pass_meta.pass_status === \"Submitted\"" in src


class TestStage3SyntheticHelpers:
    def test_advance_pass_1_helper_exists(self) -> None:
        src = SYNTHETIC_SESSIONS.read_text(encoding="utf-8")
        assert "def advance_session_to_submitted_pass_1" in src

    def test_add_resolved_deferred_helper_exists(self) -> None:
        src = SYNTHETIC_SESSIONS.read_text(encoding="utf-8")
        assert "def add_synthetic_resolved_deferred_decision" in src

    def test_advance_helper_mocks_je_docstatus(self) -> None:
        """Helper must have a way to force JE docstatus=1 so
        mark_submitted's check passes without going through ERPNext UI."""
        src = SYNTHETIC_SESSIONS.read_text(encoding="utf-8")
        assert "mock_je_docstatus" in src

    def test_resolved_deferred_inserts_null_pass(self) -> None:
        """New decision must have generated_in_pass=None so it's picked up
        by the next generate_all — defensive against a future refactor
        that silently stamps new rows."""
        src = SYNTHETIC_SESSIONS.read_text(encoding="utf-8")
        assert '"generated_in_pass": None' in src


# ============================================================================
# pass-aware loader
# ============================================================================


class TestLoadPassPendingDecisions:
    def test_function_exists(self) -> None:
        from rgi_migration.session import parse_and_map
        assert hasattr(parse_and_map, "load_pass_pending_decisions_from_session")
        assert hasattr(parse_and_map, "stamp_pass_on_decisions")


# ============================================================================
# Backfill patch — additional edge cases
# ============================================================================


class TestBackfillPatchEdgeCases:
    def test_session_with_deferred_decisions(self) -> None:
        """A pre-Stage-3 Submitted session with Deferred rows should still
        get a Pass 1 row — the Deferred rows remain unstamped (they'll be
        candidates for future Pass 2 generation)."""
        from rgi_migration.patches.v1_0.backfill_pass_1_for_submitted_sessions import (
            backfill_pass_1,
        )
        session = _FakeSessionDoc("TMS-WITH-DEFERRED")
        decisions = [
            {"name": "MD-APPROVED", "review_action": "Approved"},
            {"name": "MD-DEFERRED", "review_action": "Deferred"},
        ]
        stamped: list[tuple[str, int]] = []
        counters = backfill_pass_1(
            get_submitted_session_names=lambda: ["TMS-WITH-DEFERRED"],
            get_session_doc=lambda _: session,
            get_decisions_for_session=lambda _: decisions,
            set_decision_pass=lambda n, p: stamped.append((n, p)),
            save_session=lambda _s: None,
            now_datetime=lambda: "2026-04-25T00:00:00",
        )
        assert counters["sessions_backfilled"] == 1
        assert counters["decisions_stamped"] == 1
        assert ("MD-APPROVED", 1) in stamped
        assert ("MD-DEFERRED", 1) not in stamped
        # The Pass 1 row records the Deferred count for audit
        row = session.migration_passes[0]
        assert row["decisions_deferred_count"] == 1
