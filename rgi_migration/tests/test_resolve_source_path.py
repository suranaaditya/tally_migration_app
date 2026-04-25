"""Tests for ``resolve_session_source_path`` — Lane A vs Lane B path resolution.

Bug surfaced 2026-04-25 on production frappe.cloud bench:
``run_mapper`` passed Lane B Attach URLs (``/private/files/...``)
straight to ``Path(...).read_bytes()`` which interpreted them as
filesystem paths and raised ``FileNotFoundError``. Pre-existing Stage
1 bug; never tripped on the dev bench because all testing used Lane
A (`source_file_server_path` = absolute path to a local fixture).

This file pins the expected behaviour:

* Lane A (server_path set) → returned verbatim
* Lane B with File doc found → resolved via ``get_full_path()``
* Lane B with File doc missing + URL ``/private/files/...`` → site-path mangled
* Lane B with File doc missing + URL ``/files/...`` → site-path mangled
* Neither set → ``""`` (caller throws user-friendly message)

The function is Frappe-coupled (reads File DocType + uses
``frappe.utils.get_site_path``) so we mock ``frappe`` via
``unittest.mock`` rather than spinning up a bench.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _session(server_path: str | None = None, file_url: str | None = None):
	return SimpleNamespace(
		source_file_server_path=server_path,
		source_file=file_url,
	)


def test_lane_a_server_path_returned_verbatim() -> None:
	from rgi_migration.session.parse_and_map import (
		resolve_session_source_path,
	)
	# No frappe access needed — Lane A short-circuits.
	with patch.dict(sys.modules, {"frappe": MagicMock()}):
		result = resolve_session_source_path(
			_session(server_path="/home/frappe/tally-exports/big.xml"),
		)
	assert result == "/home/frappe/tally-exports/big.xml"


def test_lane_a_takes_precedence_over_lane_b() -> None:
	"""If both fields are set, Lane A wins — matches the existing
	`session.source_file_server_path or session.source_file` semantic."""
	from rgi_migration.session.parse_and_map import (
		resolve_session_source_path,
	)
	with patch.dict(sys.modules, {"frappe": MagicMock()}):
		result = resolve_session_source_path(
			_session(
				server_path="/abs/server.xml",
				file_url="/private/files/lane_b.xml",
			),
		)
	assert result == "/abs/server.xml"


def test_lane_b_resolves_via_file_doc_get_full_path() -> None:
	"""Canonical happy path — File doc exists, get_full_path() returns
	the absolute filesystem path."""
	from rgi_migration.session.parse_and_map import (
		resolve_session_source_path,
	)
	mock_frappe = MagicMock()
	mock_frappe.db.get_value.return_value = "FILE-001"
	mock_file_doc = MagicMock()
	mock_file_doc.get_full_path.return_value = (
		"/home/frappe/frappe-bench/sites/erp.test/private/files/foo.xml"
	)
	mock_frappe.get_doc.return_value = mock_file_doc

	with patch.dict(sys.modules, {"frappe": mock_frappe}):
		result = resolve_session_source_path(
			_session(file_url="/private/files/foo.xml"),
		)
	assert result == (
		"/home/frappe/frappe-bench/sites/erp.test/private/files/foo.xml"
	)
	mock_frappe.db.get_value.assert_called_once_with(
		"File", {"file_url": "/private/files/foo.xml"}, "name",
	)


def test_lane_b_falls_back_to_site_path_when_file_doc_missing() -> None:
	"""Defensive: File doc deleted but URL still on session — translate
	by hand against site root."""
	from rgi_migration.session.parse_and_map import (
		resolve_session_source_path,
	)
	mock_frappe = MagicMock()
	mock_frappe.db.get_value.return_value = None  # File doc missing
	mock_frappe.utils.get_site_path.return_value = (
		"./sites/erp.test/private/files/orphaned.xml"
	)

	with patch.dict(sys.modules, {"frappe": mock_frappe}):
		result = resolve_session_source_path(
			_session(file_url="/private/files/orphaned.xml"),
		)
	assert result == "./sites/erp.test/private/files/orphaned.xml"
	mock_frappe.utils.get_site_path.assert_called_once_with(
		"private", "files", "orphaned.xml",
	)


def test_lane_b_public_files_url_falls_back_to_public_dir() -> None:
	from rgi_migration.session.parse_and_map import (
		resolve_session_source_path,
	)
	mock_frappe = MagicMock()
	mock_frappe.db.get_value.return_value = None
	mock_frappe.utils.get_site_path.return_value = (
		"./sites/erp.test/public/files/sample.xml"
	)

	with patch.dict(sys.modules, {"frappe": mock_frappe}):
		result = resolve_session_source_path(
			_session(file_url="/files/sample.xml"),
		)
	assert result == "./sites/erp.test/public/files/sample.xml"
	mock_frappe.utils.get_site_path.assert_called_once_with(
		"public", "files", "sample.xml",
	)


def test_lane_b_filename_with_spaces_preserves_spaces() -> None:
	"""Real-world case from the bug report — filename has a space.
	Path translation must not URL-decode or modify the filename."""
	from rgi_migration.session.parse_and_map import (
		resolve_session_source_path,
	)
	mock_frappe = MagicMock()
	mock_frappe.db.get_value.return_value = None
	mock_frappe.utils.get_site_path.return_value = (
		"./sites/erp.test/private/files/GHRCACS PUNE_slim.xml"
	)

	with patch.dict(sys.modules, {"frappe": mock_frappe}):
		result = resolve_session_source_path(
			_session(file_url="/private/files/GHRCACS PUNE_slim.xml"),
		)
	# The space in the filename must be preserved, not URL-encoded.
	assert "GHRCACS PUNE_slim.xml" in result
	mock_frappe.utils.get_site_path.assert_called_once_with(
		"private", "files", "GHRCACS PUNE_slim.xml",
	)


def test_neither_field_set_returns_empty_string() -> None:
	"""Caller is responsible for the user-friendly throw on empty input."""
	from rgi_migration.session.parse_and_map import (
		resolve_session_source_path,
	)
	with patch.dict(sys.modules, {"frappe": MagicMock()}):
		result = resolve_session_source_path(_session())
	assert result == ""


def test_get_full_path_exception_falls_back_to_site_path() -> None:
	"""Defensive: File doc found but get_full_path() raises (e.g.
	permission edge case). Fall through to string-mangling."""
	from rgi_migration.session.parse_and_map import (
		resolve_session_source_path,
	)
	mock_frappe = MagicMock()
	mock_frappe.db.get_value.return_value = "FILE-001"
	mock_file_doc = MagicMock()
	mock_file_doc.get_full_path.side_effect = RuntimeError("permission")
	mock_frappe.get_doc.return_value = mock_file_doc
	mock_frappe.utils.get_site_path.return_value = (
		"./sites/erp.test/private/files/fallback.xml"
	)

	with patch.dict(sys.modules, {"frappe": mock_frappe}):
		result = resolve_session_source_path(
			_session(file_url="/private/files/fallback.xml"),
		)
	assert result == "./sites/erp.test/private/files/fallback.xml"


# ---------------------------------------------------------------------------
# Integration: confirm callers (run_mapper, students_csv) use the resolver.
# Static-string check — pin against accidental revert.
# ---------------------------------------------------------------------------


def test_run_mapper_uses_resolver() -> None:
	from pathlib import Path

	src = (
		Path(__file__).resolve().parents[1]
		/ "rgi_migration" / "doctype" / "tally_migration_session"
		/ "tally_migration_session.py"
	).read_text(encoding="utf-8")
	assert "resolve_session_source_path(session)" in src, (
		"run_mapper must use resolve_session_source_path; otherwise "
		"Lane B Attach URLs slip through as raw URLs and the parser "
		"crashes with FileNotFoundError."
	)


def test_students_csv_uses_resolver() -> None:
	from pathlib import Path

	src = (
		Path(__file__).resolve().parents[1]
		/ "generators" / "students_csv.py"
	).read_text(encoding="utf-8")
	assert "resolve_session_source_path(session)" in src
