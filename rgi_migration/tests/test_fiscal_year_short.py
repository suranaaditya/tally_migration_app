"""Tests for ``rgi_migration.session.fiscal_year.compute_fiscal_year_short``.

Pure-Python unit tests — the function has no Frappe dependency.
Used by the ``Tally Migration Session.before_insert`` hook to
populate ``fiscal_year_short`` for the autoname template.
"""

from __future__ import annotations

import pytest

from rgi_migration.session.fiscal_year import compute_fiscal_year_short


class TestHappyPath:
	def test_standard_range(self) -> None:
		assert compute_fiscal_year_short("2025-2026") == "25-26"

	def test_next_year(self) -> None:
		assert compute_fiscal_year_short("2026-2027") == "26-27"

	def test_century_rollover(self) -> None:
		assert compute_fiscal_year_short("2099-2100") == "99-00"

	def test_pre_2000(self) -> None:
		assert compute_fiscal_year_short("1999-2000") == "99-00"

	def test_whitespace_trimmed(self) -> None:
		assert compute_fiscal_year_short("  2025-2026  ") == "25-26"


class TestFormatValidation:
	@pytest.mark.parametrize(
		"bad_input",
		[
			"",
			"   ",
			"FY2025-2026",
			"2025-26",
			"25-26",
			"2025_2026",
			"2025/2026",
			"2025-2026-2027",
			"abcd-efgh",
			"2025-",
			"-2026",
			"20250-20260",
		],
	)
	def test_malformed_raises(self, bad_input: str) -> None:
		with pytest.raises(ValueError, match="YYYY-YYYY"):
			compute_fiscal_year_short(bad_input)

	def test_none_raises(self) -> None:
		with pytest.raises(ValueError, match="YYYY-YYYY"):
			compute_fiscal_year_short(None)  # type: ignore[arg-type]


class TestConsecutiveYearCheck:
	@pytest.mark.parametrize(
		"bad_input",
		[
			"2025-2025",  # same year
			"2025-2027",  # gap of 2
			"2026-2025",  # reversed
			"2000-2020",  # gap of 20
		],
	)
	def test_non_consecutive_raises(self, bad_input: str) -> None:
		with pytest.raises(ValueError, match="consecutive years"):
			compute_fiscal_year_short(bad_input)
