"""Shared pass-tracking helpers for Item 8.5 Stage 3.

Used by ``generate_all`` / ``mark_submitted`` (the top-level whitelists) and
by each of the four generators to stay consistent about "which pass am I?"
and "what's my artefact name?" decisions.

Pure utility functions — take a session doc (with ``.migration_passes``
child table loaded) or a list of Migration Pass rows; return integers /
strings. No Frappe ORM calls, no side effects.

Architecture:

- ``determine_current_pass_number(session)`` — the single source of truth
  for pass-number resolution. Generators call this to know which
  ``generated_in_pass`` stamp to write on emitted MDs.

- ``build_reference_suffix(pass_number)`` — Pass 1 returns ``""``; Pass
  N>=2 returns ``"-P{N}"``. Applied to the RGI rules §6.2 base reference
  (``OB-{ABBR}-{FY}-01`` / ``-02``) to produce pass-aware artefact names.

- ``build_filename_suffix(pass_number)`` — Pass 1 returns ``""``; Pass
  N>=2 returns ``"_p{N}"``. Applied before the file extension.
"""

from __future__ import annotations

from typing import Any


def determine_current_pass_number(session: Any) -> int:
	"""Resolve the pass number for an in-progress or next generation.

	Rules:
	  - No Migration Pass rows exist → this is Pass 1.
	  - A Migration Pass row with ``pass_status=="Draft"`` exists →
	    we're re-running that pass (intra-pass idempotency). Return
	    that row's ``pass_number``.
	  - All existing rows are ``Submitted`` → starting a fresh new
	    pass. Return ``max(pass_number) + 1``.
	  - ``Failed`` rows are treated like Draft ones (recoverable by
	    re-run) — return the failed pass's number so re-run replaces it.

	Args:
		session: Tally Migration Session doc with ``migration_passes``
			child table loaded (Frappe doc, list of rows, or a stub
			object with ``.migration_passes`` attribute).

	Returns:
		Integer pass number >= 1.
	"""
	passes = _passes_from(session)
	if not passes:
		return 1
	in_progress = [
		p for p in passes if _get(p, "pass_status") in ("Draft", "Failed")
	]
	if in_progress:
		return max(int(_get(p, "pass_number") or 0) for p in in_progress)
	submitted = [
		p for p in passes if _get(p, "pass_status") == "Submitted"
	]
	if not submitted:
		# No Draft/Failed/Submitted rows? Defensive fallback — treat as
		# fresh Pass 1. Shouldn't happen in practice (pass_status is
		# reqd on the DocType) but guards against malformed rows.
		return 1
	return max(int(_get(p, "pass_number") or 0) for p in submitted) + 1


def build_reference_suffix(pass_number: int) -> str:
	"""Pass-aware suffix for artefact reference IDs.

	Pass 1: ``""`` (no suffix → ``OB-CACSPU-2025-26-01``).
	Pass N (N>=2): ``"-P{N}"`` (→ ``OB-CACSPU-2025-26-01-P2``).
	"""
	if pass_number <= 1:
		return ""
	return f"-P{pass_number}"


def build_filename_suffix(pass_number: int) -> str:
	"""Pass-aware suffix for artefact CSV filenames.

	Pass 1: ``""`` (no suffix → ``cacspu_opening_invoices.csv``).
	Pass N (N>=2): ``"_p{N}"`` (→ ``cacspu_opening_invoices_p2.csv``).
	"""
	if pass_number <= 1:
		return ""
	return f"_p{pass_number}"


def find_draft_pass_row(session: Any, pass_number: int) -> Any | None:
	"""Return the Migration Pass child row matching ``pass_number`` if its
	status is Draft or Failed (i.e. re-runnable). Returns ``None`` if no
	such row exists.
	"""
	for p in _passes_from(session):
		if (
			int(_get(p, "pass_number") or 0) == pass_number
			and _get(p, "pass_status") in ("Draft", "Failed")
		):
			return p
	return None


def is_fresh_pass_start(session: Any) -> bool:
	"""True if the next generate_all would start a *new* pass (no Draft
	row exists). False if there's an in-progress Draft we're re-running.

	Used by ``generate_all`` to decide whether to clear session-level
	artefact fields before calling generators: on a fresh new pass, we
	must detach prior-pass artefacts (Pass 1 data lives in its Pass 1
	Migration Pass row; session-level fields need to be blanked so Pass
	2 generators don't see stale Submitted refs).
	"""
	passes = _passes_from(session)
	if not passes:
		return True  # Pass 1, no prior artefacts to detach
	return not any(_get(p, "pass_status") in ("Draft", "Failed") for p in passes)


# ---------------------------------------------------------------------------
# Internal helpers — handle both Frappe doc rows (attribute access) and
# plain dicts (for unit tests).
# ---------------------------------------------------------------------------


def _passes_from(session: Any) -> list[Any]:
	if isinstance(session, list):
		return session
	return list(getattr(session, "migration_passes", None) or [])


def _get(row: Any, field: str) -> Any:
	if isinstance(row, dict):
		return row.get(field)
	return getattr(row, field, None)
