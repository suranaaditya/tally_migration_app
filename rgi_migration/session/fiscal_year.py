# Copyright (c) 2026, Dux Digitech and contributors
# For license information, please see license.txt

"""Fiscal-year helpers (Frappe-free).

Used by the ``Tally Migration Session`` ``before_insert`` hook to
auto-populate ``fiscal_year_short`` from ``fiscal_year``.
``fiscal_year_short`` is consumed by the session autoname template:
``TMS-{company_abbr}-{fiscal_year_short}-{#####}``.

Canonical input format: ``"YYYY-YYYY"`` with consecutive years
(e.g. ``"2025-2026"``). Canonical output: last two digits of each
year joined by ``-`` (e.g. ``"25-26"``). No ``FY`` prefix — that
would break the existing autoname convention.
"""

from __future__ import annotations

import re

_FISCAL_YEAR_PATTERN = re.compile(r"^(\d{4})-(\d{4})$")


def compute_fiscal_year_short(fiscal_year: str) -> str:
	"""Return the short form (``YY-YY``) for a ``YYYY-YYYY`` fiscal year.

	Raises ``ValueError`` when the input is malformed or the two years
	are not consecutive. The caller (Frappe controller) translates this
	into ``frappe.ValidationError``.
	"""
	if not isinstance(fiscal_year, str) or not fiscal_year.strip():
		raise ValueError(
			"fiscal_year must be in format 'YYYY-YYYY' with consecutive "
			"years (e.g., '2025-2026')"
		)
	match = _FISCAL_YEAR_PATTERN.match(fiscal_year.strip())
	if not match:
		raise ValueError(
			"fiscal_year must be in format 'YYYY-YYYY' with consecutive "
			"years (e.g., '2025-2026')"
		)
	start, end = int(match.group(1)), int(match.group(2))
	if end != start + 1:
		raise ValueError(
			"fiscal_year must be in format 'YYYY-YYYY' with consecutive "
			"years (e.g., '2025-2026')"
		)
	return f"{start % 100:02d}-{end % 100:02d}"
