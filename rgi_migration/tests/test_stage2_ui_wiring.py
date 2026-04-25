"""Static checks for Item 8.5 Stage 2 UI changes.

Guards against accidental regression of:

* The amber Deferred indicator registration in md_review.js's
  REVIEW_ACTION_STATE dictionary (Q7)
* The corresponding CSS rule in md_review.css
* The conditional Deferred-warning checklist item in the Mark
  Submitted dialog (Q10)
* The Deferred-count log line format in generate_all's success branch
  (Q9)
* The SCR + ACR DocType status enum additive migration (Q1)

These are string-presence assertions. Runtime behavior is covered by
the Phase C bench smoke.
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MD_REVIEW_JS = ROOT / "rgi_migration" / "rgi_migration" / "page" / "md_review" / "md_review.js"
MD_REVIEW_CSS = ROOT / "rgi_migration" / "rgi_migration" / "page" / "md_review" / "md_review.css"
SESSION_JS = ROOT / "rgi_migration" / "rgi_migration" / "doctype" / "tally_migration_session" / "tally_migration_session.js"
SESSION_PY = ROOT / "rgi_migration" / "rgi_migration" / "doctype" / "tally_migration_session" / "tally_migration_session.py"
SCR_JSON = ROOT / "rgi_migration" / "rgi_migration" / "doctype" / "supplier_creation_request" / "supplier_creation_request.json"
ACR_JSON = ROOT / "rgi_migration" / "rgi_migration" / "doctype" / "account_creation_request" / "account_creation_request.json"


class TestDeferredIndicator:
    def test_review_action_state_maps_deferred_to_deferred_class(self) -> None:
        src = MD_REVIEW_JS.read_text(encoding="utf-8")
        assert '"Deferred": "deferred"' in src, (
            "REVIEW_ACTION_STATE['Deferred'] must map to 'deferred' "
            "(not 'muted') so the amber indicator renders. Stage 2 Q7."
        )

    def test_css_rule_for_deferred_indicator_exists(self) -> None:
        src = MD_REVIEW_CSS.read_text(encoding="utf-8")
        assert ".indicator-dot.deferred" in src, (
            "CSS rule for .indicator-dot.deferred missing — amber "
            "color won't render. Stage 2 Q7."
        )
        # Check it's an amber-family color (defensive)
        idx = src.index(".indicator-dot.deferred")
        rule_line = src[idx:idx + 200]
        assert "yellow" in rule_line or "amber" in rule_line or "#f0c000" in rule_line.lower(), (
            "Deferred indicator rule exists but doesn't use an amber/yellow color. "
            "Stage 2 Q7 spec'd amber."
        )


class TestMarkSubmittedDialog:
    def test_deferred_count_fetch_in_mark_submitted(self) -> None:
        """Confirm the frappe.client.get_count call with
        review_action:Deferred filter exists in the Mark Submitted
        flow — this is how the dialog decides whether to show the
        warning item."""
        src = SESSION_JS.read_text(encoding="utf-8")
        assert 'frappe.client.get_count' in src
        assert '"Mapping Decision"' in src
        assert '"Deferred"' in src

    def test_conditional_deferred_warning_copy(self) -> None:
        src = SESSION_JS.read_text(encoding="utf-8")
        # Key phrases from Aditya's refined copy (Phase A Q10):
        assert "NOT included in this pass's artefacts" in src
        assert "remain unresolved" in src
        assert "addressed before the migration is complete" in src

    def test_dialog_only_shows_warning_when_count_positive(self) -> None:
        """The conditional rendering uses deferred_count > 0 — verify
        the predicate exists (otherwise the warning would show on
        every submit regardless of Deferred state)."""
        src = SESSION_JS.read_text(encoding="utf-8")
        assert "deferred_count > 0" in src


class TestGenerateAllDeferredLog:
    def test_log_line_format(self) -> None:
        src = SESSION_PY.read_text(encoding="utf-8")
        # Grep-able format per Q9 resolution (Stage 2) + Stage 3 Q-D
        # pass-aware variant: "generate_all: completed Pass {N}
        # ({status} → Generated), 4 artefacts generated, K decisions
        # Deferred for next pass".
        assert "generate_all: completed Pass" in src
        assert "Deferred for next pass" in src

    def test_deferred_count_in_return_dict(self) -> None:
        """generate_all's success return dict should carry
        deferred_count so the JS can surface it to the reviewer."""
        src = SESSION_PY.read_text(encoding="utf-8")
        assert '"deferred_count": deferred_count' in src


class TestSCRACRSchemaMigration:
    def test_scr_status_has_deferred(self) -> None:
        data = json.loads(SCR_JSON.read_text(encoding="utf-8"))
        for field in data["fields"]:
            if field["fieldname"] == "status":
                assert "Deferred" in field["options"], (
                    "SCR status enum missing 'Deferred' option — "
                    "Stage 2 schema migration incomplete"
                )
                return
        raise AssertionError("No status field in SCR JSON")

    def test_acr_status_has_deferred(self) -> None:
        data = json.loads(ACR_JSON.read_text(encoding="utf-8"))
        for field in data["fields"]:
            if field["fieldname"] == "status":
                assert "Deferred" in field["options"], (
                    "ACR status enum missing 'Deferred' option"
                )
                return
        raise AssertionError("No status field in ACR JSON")

    def test_scr_status_default_unchanged(self) -> None:
        """Defensive: additive migration must not touch the default."""
        data = json.loads(SCR_JSON.read_text(encoding="utf-8"))
        for field in data["fields"]:
            if field["fieldname"] == "status":
                assert field.get("default") == "Pending"
                return

    def test_acr_status_default_unchanged(self) -> None:
        data = json.loads(ACR_JSON.read_text(encoding="utf-8"))
        for field in data["fields"]:
            if field["fieldname"] == "status":
                assert field.get("default") == "Pending"
                return


class TestSaveWithActionRace:
    """Regression guard — Item 8.5 Stage 2 Phase D discovered that
    `_saveWithAction` didn't await Control.set_value, causing
    saveDecision's get_value() to read the pre-set value and RPC
    with the wrong review_action. In Frappe 15+, Control.set_value
    returns a Promise. The fix: `await` the set_value call."""

    def test_set_value_is_awaited(self) -> None:
        src = MD_REVIEW_JS.read_text(encoding="utf-8")
        idx = src.index("async _saveWithAction")
        # Grab the function body up to the next function definition (~50 lines)
        body = src[idx:idx + 2500]
        # Must have `await this.section4_controls.review_action.set_value`
        assert "await this.section4_controls.review_action.set_value" in body, (
            "_saveWithAction must await set_value — otherwise the Promise-"
            "returning Frappe 15+ Control.set_value races with saveDecision's "
            "get_value(), causing the wrong review_action to be persisted. "
            "Fix: prefix set_value call with `await`."
        )


class TestSyncHelperIntegrationPoints:
    def test_save_decision_calls_sync_helper(self) -> None:
        src = (ROOT / "rgi_migration" / "rgi_migration" / "page" / "md_review" / "md_review.py").read_text(encoding="utf-8")
        assert "sync_creation_requests_for_decision" in src

    def test_undo_decision_calls_restore_helper(self) -> None:
        src = (ROOT / "rgi_migration" / "rgi_migration" / "page" / "md_review" / "md_review.py").read_text(encoding="utf-8")
        assert "restore_creation_requests_from_snapshot" in src

    def test_undo_cache_carries_cr_snapshots(self) -> None:
        """The _cr_scr_snapshot + _cr_acr_snapshot keys must be written
        into the undo cache payload — otherwise undo_decision can't
        restore SCR/ACR."""
        src = (ROOT / "rgi_migration" / "rgi_migration" / "page" / "md_review" / "md_review.py").read_text(encoding="utf-8")
        assert '"_cr_scr_snapshot"' in src
        assert '"_cr_acr_snapshot"' in src
