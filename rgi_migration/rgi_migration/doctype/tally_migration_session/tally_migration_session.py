# Copyright (c) 2026, Dux Digitech and contributors
# For license information, please see license.txt

from __future__ import annotations

from typing import Any

import frappe
from frappe.model.document import Document


class TallyMigrationSession(Document):
	def get_decisions(
		self,
		filters: dict | None = None,
		fields: list[str] | None = None,
		order_by: str = "creation",
	) -> list[dict[str, Any]]:
		"""Return ``Mapping Decision`` records linked to this session.

		Replaces the pre-Week-4 ``self.mapping_decisions`` child-table
		access pattern. ``Mapping Decision`` is now a standalone DocType
		(``istable=0``) with a ``session`` Link field back to
		``Tally Migration Session``. See
		``patches/v1_0/migrate_decisions_to_standalone.py`` for the
		migration that moved existing child rows.

		Args:
			filters: additional filter dict ``{"field": value}`` ANDed with
				the implicit ``session=self.name`` filter. Example:
				``{"review_action": "Pending"}``.
			fields: list of Mapping Decision fields to return. Defaults to
				``["*"]`` — callers trimming to a projection should pass an
				explicit list for network payload reduction on large
				sessions.
			order_by: SQL ORDER BY clause. Defaults to ``creation`` ascending
				so the reviewer sees decisions in the order the mapper
				emitted them.

		Returns:
			List of dicts (one per Mapping Decision). Empty list if the
			session has no decisions yet (e.g. before parse + map has run,
			or mid-migration between the schema flip and the patch).
		"""
		query_filters: dict[str, Any] = {"session": self.name}
		if filters:
			query_filters.update(filters)
		return frappe.get_all(
			"Mapping Decision",
			filters=query_filters,
			fields=fields or ["*"],
			order_by=order_by,
		)
