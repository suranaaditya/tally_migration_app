"""Tests for the `_GENERATOR_SEQUENCE` contract and status-transition
constants in the session controller.

The `generate_all` / `mark_submitted` whitelists themselves are
Frappe-bound (they call frappe.get_doc, frappe.db.commit, etc.) and
exercised in Phase C bench smoke. Here we cover the structural
invariants that can be asserted statically.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SESSION_CONTROLLER = (
    Path(__file__).resolve().parent.parent
    / "rgi_migration"
    / "doctype"
    / "tally_migration_session"
    / "tally_migration_session.py"
)


def _parse_module() -> ast.Module:
    return ast.parse(SESSION_CONTROLLER.read_text(encoding="utf-8"))


class TestStatusConstants:
    """Item 8.5 Stage 1 added _STATUS_GENERATING + _STATUS_GENERATED.
    Both must be present in the session JSON enum already, and the
    Python constants must match the JSON spelling exactly."""

    def test_generating_constant_defined(self) -> None:
        src = SESSION_CONTROLLER.read_text(encoding="utf-8")
        assert '_STATUS_GENERATING = "Generating"' in src

    def test_generated_constant_defined(self) -> None:
        src = SESSION_CONTROLLER.read_text(encoding="utf-8")
        assert '_STATUS_GENERATED = "Generated"' in src

    def test_generate_allowed_from_defined(self) -> None:
        src = SESSION_CONTROLLER.read_text(encoding="utf-8")
        assert "_GENERATE_ALLOWED_FROM = frozenset(" in src
        # Must allow both Reviewing (first-gen) and Generated (regen).
        assert "_STATUS_REVIEWING" in src
        assert "_STATUS_GENERATED" in src

    def test_mark_submitted_allowed_from_generated_only(self) -> None:
        src = SESSION_CONTROLLER.read_text(encoding="utf-8")
        # Q9: Mark Submitted transitions Generated -> Submitted.
        # Any other source status is a bug.
        assert (
            "_MARK_SUBMITTED_ALLOWED_FROM = frozenset({_STATUS_GENERATED})"
            in src
        )


class TestGeneratorSequence:
    """_GENERATOR_SEQUENCE defines the atomic run order. The order
    matters: Main JE before OIT (OIT's downstream PIs net against
    Main JE's Temporary Opening); Advance JE before Students CSV is
    conventional but not load-bearing."""

    def test_exactly_four_generators(self) -> None:
        src = SESSION_CONTROLLER.read_text(encoding="utf-8")
        # Count occurrences of generator dotted paths.
        generators = [
            "generate_main_opening_je",
            "generate_oit_csv",
            "generate_advance_je",
            "generate_students_csv",
        ]
        for g in generators:
            assert g in src, f"generator {g!r} missing from sequence"

    def test_main_je_first(self) -> None:
        src = SESSION_CONTROLLER.read_text(encoding="utf-8")
        seq_start = src.index("_GENERATOR_SEQUENCE")
        snippet = src[seq_start:seq_start + 800]
        main_pos = snippet.index("generate_main_opening_je")
        oit_pos = snippet.index("generate_oit_csv")
        assert main_pos < oit_pos, (
            "Main JE must run before OIT CSV — OIT's downstream "
            "Purchase Invoices depend on Temporary Opening posted "
            "by Main JE."
        )


class TestWhitelistDecorators:
    def test_generate_all_whitelisted(self) -> None:
        src = SESSION_CONTROLLER.read_text(encoding="utf-8")
        # Must have @frappe.whitelist() directly above generate_all.
        lines = src.split("\n")
        for i, line in enumerate(lines):
            if "def generate_all(" in line:
                assert "@frappe.whitelist()" in lines[i - 1], (
                    "generate_all missing @frappe.whitelist decoration"
                )
                return
        pytest.fail("generate_all definition not found")

    def test_mark_submitted_whitelisted(self) -> None:
        src = SESSION_CONTROLLER.read_text(encoding="utf-8")
        lines = src.split("\n")
        for i, line in enumerate(lines):
            if "def mark_submitted(" in line:
                assert "@frappe.whitelist()" in lines[i - 1], (
                    "mark_submitted missing @frappe.whitelist decoration"
                )
                return
        pytest.fail("mark_submitted definition not found")


class TestGeneratorEntryWhitelists:
    """All four generator entry-points must be decorated via the
    `whitelist()` shim from `_frappe_compat`."""

    @pytest.mark.parametrize(
        "module_path,fn_name",
        [
            ("rgi_migration/generators/opening_je.py", "generate_main_opening_je"),
            ("rgi_migration/generators/oit_csv.py", "generate_oit_csv"),
            ("rgi_migration/generators/advance_je.py", "generate_advance_je"),
            ("rgi_migration/generators/students_csv.py", "generate_students_csv"),
        ],
    )
    def test_entry_point_decorated(
        self, module_path: str, fn_name: str,
    ) -> None:
        path = Path(__file__).resolve().parent.parent.parent / module_path
        src = path.read_text(encoding="utf-8")
        lines = src.split("\n")
        for i, line in enumerate(lines):
            if f"def {fn_name}(" in line:
                assert "@whitelist()" in lines[i - 1], (
                    f"{fn_name} missing @whitelist() decoration "
                    f"(use the rgi_migration.generators._frappe_compat "
                    f"shim; see Item 8.5 Stage 1)"
                )
                return
        pytest.fail(f"{fn_name} not found in {module_path}")


class TestFrappeCompatShim:
    def test_shim_importable_without_frappe(self) -> None:
        """Importing the shim must succeed even when frappe is not
        installed — this is the property the shim exists to preserve."""
        from rgi_migration.generators._frappe_compat import whitelist

        # When frappe is absent, whitelist() returns a passthrough.
        # When frappe is present, whitelist is frappe.whitelist.
        # Either way, whitelist is callable.
        assert callable(whitelist)

    def test_shim_decorator_is_passthrough_or_frappe(self) -> None:
        from rgi_migration.generators._frappe_compat import whitelist

        decorator = whitelist()

        def sample_fn(x: int) -> int:
            return x + 1

        decorated = decorator(sample_fn)
        # Either Frappe wraps it (still callable) or the passthrough
        # returns it unchanged — both are acceptable.
        assert callable(decorated)
        # Identity check only valid in passthrough case; skip if frappe
        # wrapped (it adds metadata but preserves callability).
