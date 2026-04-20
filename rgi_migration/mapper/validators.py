"""Structural checks used alongside Tier-1 rule resolution.

These are code-level invariants, not data-driven rules. They do not live in
the `Mapping Rule` DocType because they apply regardless of rule content.
See docs/mapper_design_notes.md §2.

Three validators:
    pnl_root_type_exclusion   — pre-resolution. Short-circuits Income/Expense.
    zero_balance_exclusion    — pre-resolution. Short-circuits Dr=0 AND Cr=0
                                ledgers (Tally definitional noise).
    group_account_refusal     — post-resolution. Refuses proposals targeting
                                an is_group account.

Each validator returns a `ValidatorOutcome` when it wants the mapper to
short-circuit with a specific `review_action`, or `None` to let resolution
continue.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ValidatorOutcome:
    review_action: str
    excluded_reason: str
    counter_name: str   # name of the Session-level diagnostic to increment


def pnl_root_type_exclusion(ledger: Any) -> ValidatorOutcome | None:
    """Income/Expense ledgers never receive a proposal. Current-year P&L is
    transferred via a separate process; it is not part of the opening JE.

    See docs/mapper_design_notes.md §2(a).
    """
    if ledger.root_type in ("Income", "Expense"):
        return ValidatorOutcome(
            review_action="Excluded (P&L)",
            excluded_reason=(
                "Income/Expense root type; handled by current-year P&L "
                "transfer, not opening JE."
            ),
            counter_name="pnl_excluded_count",
        )
    return None


def zero_balance_exclusion(ledger: Any) -> ValidatorOutcome | None:
    """Exclude ledgers with no opening balance (Dr=0 AND Cr=0).

    Tally exports every declared ledger regardless of balance — definitional
    ledgers, closed-out accounts, and never-posted-to rows all appear in the
    export. Per architectural decision 2026-04-20 these carry no migration
    relevance and are short-circuited at the mapper boundary: no rule
    evaluation, no exact-name lookup, no party routing, no Account Creation
    Request. If the accounting team needs one later, it's created manually
    in the ERPNext UI.

    Order-of-operations: runs AFTER ``pnl_root_type_exclusion`` so a stale
    non-zero P&L ledger still surfaces as ``excluded_pnl`` (its root_type,
    not its balance, drives the exclusion), and a zero-balance P&L ledger
    gets buckets as ``excluded_pnl`` too (consistent with root-type-first
    thinking — reviewers investigating P&L export-flag issues see all P&L
    in one bucket regardless of balance).
    """
    if ledger.opening_dr == 0 and ledger.opening_cr == 0:
        return ValidatorOutcome(
            review_action="Excluded (Zero Balance)",
            excluded_reason=(
                "Ledger carries no opening balance (Dr=0 AND Cr=0). Tally "
                "exports every declared ledger; zero-balance rows are "
                "definitional and have no migration relevance."
            ),
            counter_name="excluded_zero_balance_count",
        )
    return None


def group_account_refusal(
    resolved_name: str,
    coa: dict[str, Any],
) -> ValidatorOutcome | None:
    """Refuse if the resolved ERP account is a group (is_group=1). ERPNext
    rejects posting to group accounts at submission — the refusal here
    catches it at map time instead of losing the Decision to a downstream
    failure.

    Called only when `resolved_name` exists in the COA. Non-existent targets
    route to the account-creation workflow, handled in mapper.py.

    See docs/mapper_design_notes.md §2(b).
    """
    account = coa.get(resolved_name)
    if account is None or not account.is_group:
        return None
    return ValidatorOutcome(
        review_action="Pending Group Account Resolution",
        excluded_reason=(
            f"Proposed account '{resolved_name}' is a group (is_group=1) and "
            f"is non-postable in ERPNext. Reviewer selects a leaf under this "
            f"group or overrides with a different mapping."
        ),
        counter_name="group_account_refused_count",
    )
