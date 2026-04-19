"""Structural checks used alongside Tier-1 rule resolution.

These are code-level invariants, not data-driven rules. They do not live in
the `Mapping Rule` DocType because they apply regardless of rule content.
See docs/mapper_design_notes.md §2.

Two validators:
    pnl_root_type_exclusion   — pre-resolution. Short-circuits Income/Expense.
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
