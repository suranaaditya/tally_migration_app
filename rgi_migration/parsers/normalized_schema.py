"""Normalized output schema shared by XML and Excel Tally parsers.

Both parsers produce a ``ParsedTallyTB`` — callers should depend only on this
module, never on parser internals.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass(frozen=True)
class BillAllocation:
    """Single outstanding bill attached to a ledger (XML-only; empty for Excel).

    Args:
        bill_name: Bill/reference number as it appears in Tally.
        amount:    Absolute amount (always positive).
        dr_cr:     "Dr" or "Cr".
    """

    bill_name: str
    amount: float
    dr_cr: str  # "Dr" | "Cr"


@dataclass
class Ledger:
    """One leaf account extracted from the Tally trial balance.

    Args:
        name:               Cleaned ledger name — trailing ``-{digits}`` ID stripped.
        tally_id:           Original numeric suffix (e.g. ``"2127"``), or None.
        parent_group:       Immediate parent group name.
        parent_chain:       Full ancestor chain, root first (e.g. ``["Indirect Expenses", "Admin Expenses"]``).
        root_type:          One of ``Asset | Liability | Income | Expense | Equity``.
        opening_dr:         Opening debit amount (positive, 0 if none).
        opening_cr:         Opening credit amount (positive, 0 if none).
        net_amount:         ``opening_cr - opening_dr`` (signed).
        net_side:           ``"Dr"``, ``"Cr"``, or ``"Zero"``.
        is_leaf:            Always True for Ledger; retained for downstream filtering.
        bill_allocations:   Outstanding bills (XML only; empty list for Excel).
        source_row:         Excel row number for debugging, None for XML.
        is_system_account:  Tally internal account (e.g. "Profit & Loss A/c" — no <PARENT>).
                            Excluded from ``total_dr`` / ``total_cr`` and from the migration ledger list.
        is_student_ledger:  Ledger routes to the students CSV (consumed by
                            dux_voucher's Ex Student Opening Batch) instead
                            of the main ledger list. True in two cases:
                              (a) Per-student leaf — parent_chain contains
                                  a student-group marker (STUDENTS /
                                  CYBERVIDYA-* / PASSOUT-* / GHRIMR STUDENT
                                  / etc.).
                              (b) Aggregate control account — cleaned name
                                  matches AGGREGATE_STUDENT_ACCOUNT_NAMES
                                  in tally_xml_parser (currently
                                  "Student Fee Outstanding").
                            See docs/mapper_design_notes.md §7.
        is_pnl_closed_zero: Diagnostic flag for the review UI. ``True`` when
                            ``root_type`` is Income|Expense AND ``opening_dr == 0 AND opening_cr == 0``
                            AND ``parent_chain`` includes one of Sales Accounts, Purchase Accounts,
                            Direct Incomes, Direct Expenses, Indirect Incomes, Indirect Expenses.
                            See ``docs/tally_sign_convention.md §4``.
    """

    name: str
    tally_id: str | None
    parent_group: str
    parent_chain: list[str]
    root_type: str
    opening_dr: float
    opening_cr: float
    net_amount: float
    net_side: str  # "Dr" | "Cr" | "Zero"
    is_leaf: bool = True
    bill_allocations: list[BillAllocation] = field(default_factory=list)
    source_row: int | None = None
    is_system_account: bool = False
    is_student_ledger: bool = False
    is_pnl_closed_zero: bool = False


@dataclass
class Group:
    """A non-leaf node in the Tally account hierarchy.

    Args:
        name:      Group name (cleaned).
        parent:    Parent group name, or None for root groups.
        root_type: One of ``Asset | Liability | Income | Expense | Equity``.
        children:  Direct children names (both sub-groups and leaves).
    """

    name: str
    parent: str | None
    root_type: str
    children: list[str] = field(default_factory=list)


@dataclass
class ParsedTallyTB:
    """Complete normalised trial balance produced by either parser.

    Args:
        company_name:    Entity name as declared in the Tally export.
        tb_date:         Trial balance as-of date, ISO format (``"2026-03-31"``).
        source_format:   ``"xml"`` or ``"excel"``.
        source_file:     Absolute path of the source file that was parsed.
        ledgers:         Main migration ledgers — leaves with
                         ``is_student_ledger == False``.  Includes Tally's
                         Profit & Loss A/c (``is_system_account=True``), which
                         is a balance-sheet account under Tally's Equity root
                         and must remain in this list for the TB to balance.
                         See ``docs/tally_sign_convention.md §4.1``.
        student_ledgers: Leaves with ``is_student_ledger == True``.  Routed separately
                         so the review UI and generator don't have to scroll through
                         thousands of student rows.
        system_ledgers:  Reserved for future exports that emit truly
                         ledger-disjoint system accounts (e.g. suspense /
                         error ledgers that shouldn't participate in the
                         migration totals).  Currently always empty —
                         Profit & Loss A/c is a real BS account and stays in
                         ``ledgers`` with ``is_system_account=True``.
        groups:          All non-leaf groups.
        total_dr:        Sum of ``opening_dr`` across ``ledgers`` +
                         ``student_ledgers`` (``system_ledgers`` currently
                         empty and also excluded).  ``is_balanced`` asserted
                         against this scope.
        total_cr:        Sum of ``opening_cr`` across ``ledgers`` +
                         ``student_ledgers``.
        is_balanced:     ``abs(total_dr - total_cr) < 0.01`` — reflects whether
                         the migration-scoped TB balances.  A true Tally
                         export with "Export closing balances as opening
                         balance" = Yes will produce ``total_dr == total_cr``
                         within a few thousand rupees (see
                         ``docs/tally_sign_convention.md §5, §6``).
        parse_warnings:  Non-fatal issues discovered during parsing.
    """

    company_name: str
    tb_date: str
    source_format: str  # "xml" | "excel"
    source_file: str
    ledgers: list[Ledger]
    groups: list[Group]
    total_dr: float
    total_cr: float
    is_balanced: bool
    parse_warnings: list[str] = field(default_factory=list)
    student_ledgers: list[Ledger] = field(default_factory=list)
    system_ledgers: list[Ledger] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict representation.

        ``BillAllocation`` and ``Ledger`` are converted recursively via
        ``dataclasses.asdict``.
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ParsedTallyTB:
        """Reconstruct a ``ParsedTallyTB`` from a plain dict (e.g. loaded from JSON fixture).

        Args:
            data: Dict as produced by ``to_dict()`` or loaded from an
                  ``expected_output.json`` fixture file.

        Returns:
            Fully populated ``ParsedTallyTB`` instance.
        """
        def _to_ledger(l: dict[str, Any]) -> Ledger:
            return Ledger(
                **{
                    **l,
                    "bill_allocations": [
                        BillAllocation(**b) for b in l.get("bill_allocations", [])
                    ],
                }
            )

        ledgers = [_to_ledger(l) for l in data.get("ledgers", [])]
        student_ledgers = [_to_ledger(l) for l in data.get("student_ledgers", [])]
        system_ledgers = [_to_ledger(l) for l in data.get("system_ledgers", [])]
        groups = [Group(**g) for g in data.get("groups", [])]
        return cls(
            company_name=data["company_name"],
            tb_date=data["tb_date"],
            source_format=data["source_format"],
            source_file=data["source_file"],
            ledgers=ledgers,
            groups=groups,
            total_dr=data["total_dr"],
            total_cr=data["total_cr"],
            is_balanced=data["is_balanced"],
            parse_warnings=data.get("parse_warnings", []),
            student_ledgers=student_ledgers,
            system_ledgers=system_ledgers,
        )

    @classmethod
    def from_json(cls, path: str) -> ParsedTallyTB:
        """Load a ``ParsedTallyTB`` directly from a JSON fixture file.

        Args:
            path: Path to the JSON file produced by ``to_dict()`` + ``json.dump``.

        Returns:
            Fully populated ``ParsedTallyTB`` instance.
        """
        with open(path, encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))

    def to_json(self, path: str, *, indent: int = 2) -> None:
        """Write this object to a JSON fixture file.

        Args:
            path:   Destination file path.
            indent: JSON indentation (default 2).
        """
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, ensure_ascii=False, indent=indent)

    def to_students_csv(self, path: str) -> int:
        """Write ``student_ledgers`` to a CSV file and return the row count.

        Columns written:
            ``Student Name, Debit Amount, Credit Amount, Parent Group, Notes``

        ``Notes`` aggregates diagnostic hints per row: bill count, net_side,
        and any noteworthy flags.  Zero-balance students are included so the
        CSV is a complete record of every student ledger routed out of the
        main migration list.

        Args:
            path: Destination ``.csv`` path.

        Returns:
            Number of data rows written (excludes header).
        """
        import csv
        count = 0
        with open(path, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["Student Name", "Debit Amount", "Credit Amount",
                        "Parent Group", "Notes"])
            for l in self.student_ledgers:
                notes_parts: list[str] = []
                if l.bill_allocations:
                    notes_parts.append(f"{len(l.bill_allocations)} bill(s)")
                if l.net_side == "Zero":
                    notes_parts.append("net zero")
                elif l.opening_dr > 0 and l.opening_cr > 0:
                    notes_parts.append("both-sided")
                if l.tally_id:
                    notes_parts.append(f"tally_id={l.tally_id}")
                w.writerow([
                    l.name,
                    f"{l.opening_dr:.2f}",
                    f"{l.opening_cr:.2f}",
                    l.parent_group,
                    "; ".join(notes_parts),
                ])
                count += 1
        return count

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        balanced = "balanced" if self.is_balanced else "UNBALANCED"
        return (
            f"<ParsedTallyTB {self.company_name!r} {self.tb_date} | "
            f"{len(self.ledgers)} ledgers {len(self.groups)} groups | "
            f"Dr {self.total_dr:,.2f}  Cr {self.total_cr:,.2f} [{balanced}] | "
            f"{len(self.parse_warnings)} warning(s)>"
        )


def dedupe_ledgers_by_identity(
    ledgers: list[Ledger],
    warnings: list[str],
) -> list[Ledger]:
    """First-wins dedup on (name, tally_id) identity tuple.

    Surfaced during Week 4 Item 2 Commit 3 reviewer-override testing:
    CACSPU's real export contains 25 Ledger pairs with byte-identical
    identity + balance + parent_chain (e.g. "Furniture Material Work
    In Progress" tally_id=1141 appears twice with opening_dr=455.48 on
    both, which would double-count to 910.96 in the Main JE without
    dedup).

    Strategy — first-wins:
      * If two Ledgers share ``(name, tally_id)`` AND have identical
        opening_dr + opening_cr, keep the first and silently drop the
        rest (they carry no new information). Still record a concise
        parse_warning so reviewers can audit the source.
      * If opening balances differ between same-identity rows, this is
        data corruption or a parser bug — keep first but log a LOUD
        warning with both values. Caller can decide whether to refuse.

    Runs BEFORE the main/student partition in both parsers so
    downstream code never sees duplicates. Identity is tuple
    ``(name, tally_id or "")`` — matches the persistence layer's
    identity key in ``session.parse_and_map.index_ledgers_by_identity``.
    """
    seen: dict[tuple[str, str], Ledger] = {}
    dup_identical: list[tuple[str, str]] = []
    dup_diverging: list[str] = []

    deduped: list[Ledger] = []
    for l in ledgers:
        key = (l.name, l.tally_id or "")
        if key in seen:
            prior = seen[key]
            if (
                abs(l.opening_dr - prior.opening_dr) < 0.005
                and abs(l.opening_cr - prior.opening_cr) < 0.005
            ):
                dup_identical.append(key)
            else:
                dup_diverging.append(
                    f"{l.name!r} (tally_id={l.tally_id!r}): "
                    f"Dr {prior.opening_dr:.2f}/{l.opening_dr:.2f}, "
                    f"Cr {prior.opening_cr:.2f}/{l.opening_cr:.2f}"
                )
            continue
        seen[key] = l
        deduped.append(l)

    if dup_identical:
        sample = ", ".join(
            f"{name!r}(id={tid!r})" for name, tid in dup_identical[:5]
        )
        more = (
            f" (+{len(dup_identical) - 5} more)"
            if len(dup_identical) > 5 else ""
        )
        warnings.append(
            f"Deduped {len(dup_identical)} identity-identical ledger "
            f"duplicate(s) at parser boundary. Samples: {sample}{more}."
        )
    if dup_diverging:
        warnings.append(
            f"DIVERGING DUPLICATES ({len(dup_diverging)}) — same "
            f"identity but different balances; first-wins applied but "
            f"inspect the Tally export for corruption. "
            + "; ".join(dup_diverging[:5])
        )
    return deduped
