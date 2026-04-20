"""Tally XML parser — supports All Masters and display-report formats.

**All Masters** (preferred, ``<REPORTNAME>All Masters</REPORTNAME>``):
  - Explicit ``<GROUP>``/``<LEDGER>`` elements with ``<PARENT>`` tags
  - ``<OPENINGBALANCE>`` directly on every ledger
  - Bill-wise allocations in ``BILLALLOCATIONS.LIST`` children
  - Company name in ``<SVCURRENTCOMPANY>``
  - Sign convention: ``ISDEEMEDPOSITIVE=Yes`` → positive = Dr balance
                     ``ISDEEMEDPOSITIVE=No``  → positive = Cr balance

**Display-report** (legacy, flat DSPACCNAME/DSPACCINFO pairs):
  - No explicit hierarchy — reconstructed via amount rollup
  - ``DSPCLDRAMTA`` = −(Dr balance), ``DSPCLCRAMTA`` = +(Cr balance)
  - No company name or bill allocations
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from rgi_migration.parsers.normalized_schema import BillAllocation, Group, Ledger, ParsedTallyTB

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tally root group → ERPNext root_type  (CLAUDE.md § Key Tally Quirks)
# ---------------------------------------------------------------------------
TALLY_ROOT_TYPES: dict[str, str] = {
    "Capital Account": "Equity",
    "Loans (Liability)": "Liability",
    "Current Liabilities": "Liability",
    "Fixed Assets": "Asset",
    "Investments": "Asset",
    "Current Assets": "Asset",
    "Branch / Divisions": "Asset",
    "Misc. Expenses (ASSET)": "Asset",
    "Suspense A/c": "Liability",
    "Sales Accounts": "Income",
    "Purchase Accounts": "Expense",
    "Direct Incomes": "Income",
    "Direct Expenses": "Expense",
    "Indirect Incomes": "Income",
    "Indirect Expenses": "Expense",
}

# Fallback when a root group is not in TALLY_ROOT_TYPES
# (e.g. user-created roots like "Expenses", "Income")
_DEEMED_ROOT_FALLBACK: dict[bool, str] = {True: "Asset", False: "Liability"}

# Strip trailing -<digits> Tally ledger-ID suffix.
# Alphanumeric party codes like -VA0017 are intentionally NOT stripped.
_ID_RE = re.compile(r"-(\d+)$")

# Tolerance for amount-rollup matching in display-report hierarchy reconstruction
_AMT_TOL = 0.02

# ---------------------------------------------------------------------------
# Routing constants — used to set is_system_account / is_student_ledger /
# is_pnl_closed_zero flags on each parsed Ledger (see docs/tally_sign_convention.md).
# ---------------------------------------------------------------------------

# P&L root groups — an Income/Expense leaf under one of these whose
# opening balance is zero gets ``is_pnl_closed_zero = True``.
_PNL_ROOTS: frozenset[str] = frozenset({
    "Sales Accounts",
    "Purchase Accounts",
    "Direct Incomes",
    "Direct Expenses",
    "Indirect Incomes",
    "Indirect Expenses",
})

# Student sub-group names (case-insensitive substring match against any group
# in the parent_chain).  When any of these match, the leaf is routed to the
# ``student_ledgers`` output instead of the main ledger list.  Students are
# typically nested under Sundry Debtors but client-specific sub-groupings
# (GHRIMR STUDENT, PASSOUT-BBA-THIRD YEAR, CYBERVIDYA-*, etc.) vary widely.
_STUDENT_GROUP_MARKERS: tuple[str, ...] = (
    "STUDENT",      # catches "STUDENTS", "GHRIMR STUDENT", "STUDENT ACCOUNT"
    "CYBERVIDYA",   # entity-specific student programmes
    "PASSOUT",      # graduated-student carry-forward sub-groups
    "CYBER VIDYA",  # spacing variants
)


def _is_student(parent_chain: list[str]) -> bool:
    """Return True if any ancestor group matches a student sub-group marker."""
    for ancestor in parent_chain:
        up = ancestor.upper()
        for marker in _STUDENT_GROUP_MARKERS:
            if marker in up:
                return True
    return False


# AGGREGATE_STUDENT_ACCOUNT_NAMES — names that represent aggregate student
# receivables (as opposed to per-student leaves under a student group).
# These are routed to dux_voucher's Ex Student Opening Batch via the CSV
# workflow, same as per-student leaves. Add entries here as other entities
# surface similar aggregate account names. This is a parser-level exclusion,
# not a Mapping Rule, because it's an architectural boundary between
# rgi_migration and dux_voucher, not per-entity business logic.
#
# Matching is case-insensitive, whitespace-collapsed, exact equality. Do
# NOT add patterns — if a variant surfaces on some entity, add the exact
# cleaned name to this set.
AGGREGATE_STUDENT_ACCOUNT_NAMES: frozenset[str] = frozenset({
    "student fee outstanding",
})


def _is_aggregate_student_account(cleaned_name: str) -> bool:
    """Return True if the ledger's cleaned name is a known aggregate
    student-receivables control account (see AGGREGATE_STUDENT_ACCOUNT_NAMES).

    Operates on the name AFTER the parser's `-{digits}` suffix strip, since
    the set is defined against cleaned names.
    """
    if not cleaned_name:
        return False
    key = re.sub(r"\s+", " ", cleaned_name).strip().lower()
    return key in AGGREGATE_STUDENT_ACCOUNT_NAMES


def _norm(s: str) -> str:
    """Normalize a Tally account/group name for fuzzy key lookup.

    Tally sometimes writes literal ``&`` in XML text content (not ``&amp;``).
    lxml's recover mode drops the ``&``, turning ``Reserves & Surplus`` into
    ``Reserves  Surplus`` (double space, no ampersand).  Collapsing whitespace
    makes both sides compare equal under this lookup.
    """
    return re.sub(r"\s+", " ", s.replace("&", "")).strip()


# ===========================================================================
# Public helpers
# ===========================================================================

def strip_tally_id(raw_name: str) -> tuple[str, str | None]:
    """Strip the trailing ``-{digits}`` Tally ledger-ID suffix.

    Args:
        raw_name: Account name as exported by Tally.

    Returns:
        ``(cleaned_name, tally_id)`` — tally_id is None if no digit suffix.

    Examples:
        >>> strip_tally_id("Depreciation Fund-2125")
        ('Depreciation Fund', '2125')
        >>> strip_tally_id("Caution Money £-2010")
        ('Caution Money £', '2010')
        >>> strip_tally_id("Anand Sales Corporation-VA0017")
        ('Anand Sales Corporation-VA0017', None)
    """
    m = _ID_RE.search(raw_name)
    if m:
        return raw_name[: m.start()], m.group(1)
    return raw_name, None


# ===========================================================================
# Entry point — auto-detects format
# ===========================================================================

def parse_xml(
    path: str,
    *,
    company_name: str = "",
    tb_date: str = "",
) -> ParsedTallyTB:
    """Parse a Tally XML export into a normalised ``ParsedTallyTB``.

    Auto-detects whether the file is an **All Masters** export or a legacy
    **display-report** (DSPACCNAME/DSPACCINFO) export and delegates accordingly.

    Args:
        path:         Absolute or relative path to the ``.xml`` file.
        company_name: Entity name override.  Extracted from XML when omitted
                      (All Masters only); falls back to the filename stem.
        tb_date:      ISO closing date (``"2026-03-31"``).  Inferred from max
                      bill date when omitted; warns if neither is available.

    Returns:
        Fully populated ``ParsedTallyTB``.

    Raises:
        ValueError: File cannot be parsed as a Tally XML.
    """
    warnings: list[str] = []
    file_path = Path(path)
    fmt = _detect_format(file_path)
    _log.debug("Detected format %r for %s", fmt, file_path.name)

    if fmt == "masters":
        return _parse_masters(
            file_path, company_name=company_name, tb_date=tb_date, warnings=warnings
        )
    return _parse_display(
        file_path, company_name=company_name, tb_date=tb_date, warnings=warnings
    )


def _detect_format(path: Path) -> str:
    """Peek at the first 8 KB to decide between 'masters' and 'display'."""
    raw = path.read_bytes()
    try:
        if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
            peek = raw[:8000].decode("utf-16", errors="replace")
        else:
            peek = raw[:4000].decode("utf-8", errors="replace")
    except Exception:
        peek = ""

    if "All Masters" in peek or "TALLYMESSAGE" in peek:
        return "masters"
    if "DSPACCNAME" in peek:
        return "display"
    # Default: assume masters (newer format)
    return "masters"


# ===========================================================================
# Internal dataclasses
# ===========================================================================

@dataclass
class _RawGroup:
    name: str
    parent: str | None      # None = root (empty <PARENT/> or self-referential)
    deemed_positive: bool   # True = Dr nature (Asset/Expense); False = Cr nature


@dataclass
class _GroupInfo:
    name: str
    parent: str | None
    deemed_positive: bool
    root_name: str
    root_type: str
    parent_chain: list[str]  # root first, does NOT include self


# ===========================================================================
# ALL MASTERS parser
# ===========================================================================

def _parse_masters(
    path: Path,
    *,
    company_name: str,
    tb_date: str,
    warnings: list[str],
) -> ParsedTallyTB:
    """Parse a Tally 'All Masters' XML export."""
    parser = etree.XMLParser(recover=True, encoding=None)
    try:
        tree = etree.parse(str(path), parser)
    except etree.XMLSyntaxError as exc:
        raise ValueError(f"Cannot parse Tally XML at {path!r}: {exc}") from exc

    root = tree.getroot()

    # --- Company name ---
    if not company_name:
        cmp_el = root.find(".//SVCURRENTCOMPANY")
        if cmp_el is not None and cmp_el.text:
            company_name = _clean_company_name(cmp_el.text)
        else:
            company_name = path.stem
            warnings.append(
                f"company_name not found in XML; inferred from filename: {company_name!r}"
            )

    # --- Parse GROUPs ---
    raw_groups: dict[str, _RawGroup] = {}
    for elem in root.iter("GROUP"):
        grp = _parse_group_element(elem)
        if grp:
            raw_groups[grp.name] = grp

    group_map, norm_map = _build_group_map(raw_groups, warnings)

    # --- Parse LEDGERs, then partition into main / student / system buckets ---
    parsed_ledgers: list[Ledger] = []
    bill_dates: list[str] = []
    for elem in root.iter("LEDGER"):
        ledger, dates = _parse_ledger_element(elem, group_map, norm_map, warnings)
        if ledger is not None:
            parsed_ledgers.append(ledger)
        bill_dates.extend(dates)

    # Partitioning cut-line is only between main_ledgers and student_ledgers.
    # is_system_account-flagged ledgers (currently: Profit & Loss A/c, which
    # has no <PARENT>) STAY in main_ledgers because they are real balance-
    # sheet accounts in Tally's model — the flag exists so the mapper can
    # route them specially (e.g. to ERPNext retained earnings), not for
    # parser-side exclusion.  See docs/tally_sign_convention.md §4.1.
    #
    # system_ledgers is kept on ParsedTallyTB as an empty list, reserved for
    # future exports that emit truly ledger-disjoint system accounts (if any).
    main_ledgers: list[Ledger] = []
    student_ledgers: list[Ledger] = []
    system_ledgers: list[Ledger] = []
    for l in parsed_ledgers:
        if l.is_student_ledger:
            student_ledgers.append(l)
        else:
            main_ledgers.append(l)

    # --- Infer TB date if not provided ---
    if not tb_date:
        if bill_dates:
            max_d = max(bill_dates)
            tb_date = f"{max_d[:4]}-{max_d[4:6]}-{max_d[6:8]}"
            warnings.append(
                f"tb_date not provided; inferred from max bill date: {tb_date!r}"
            )
        else:
            warnings.append(
                "tb_date not provided and no bill dates found; set to empty string"
            )

    # --- Build Group objects (uses all ledgers so group.children is complete) ---
    groups = _build_group_objects(group_map, norm_map, parsed_ledgers)

    # Totals cover main + student ledgers.  system_ledgers is currently empty
    # (P&L A/c lives in main; see partition comment above), but is included
    # defensively so future truly-ledger-disjoint system accounts would also
    # be excluded from `total_*` automatically.  ``is_balanced`` reflects the
    # migration-scoped TB balance (main + students), which must match the
    # raw Tally TB within ₹5000 after the known edge cases from
    # docs/tally_sign_convention.md §6 are accounted for.
    total_dr = round(
        sum(l.opening_dr for l in main_ledgers)
        + sum(l.opening_dr for l in student_ledgers),
        2,
    )
    total_cr = round(
        sum(l.opening_cr for l in main_ledgers)
        + sum(l.opening_cr for l in student_ledgers),
        2,
    )

    # Defensive imbalance warning (see docs/tally_sign_convention.md §5)
    _IMBALANCE_THRESHOLD = 0.01  # 1% of grand total
    grand = max(total_dr, total_cr)
    if grand > 0:
        diff = abs(total_dr - total_cr)
        pct = diff / grand
        if pct > _IMBALANCE_THRESHOLD:
            warnings.append(
                f"Large imbalance detected (Dr - Cr = Rs.{total_dr - total_cr:,.2f} on "
                f"total Rs.{grand:,.2f}, {pct*100:.2f}%). This usually means the Tally "
                f"export was done without 'Export closing balances as opening balance' = Yes. "
                f"Other possible causes: mid-year cutoff, hand-edited XML, unusual "
                f"both-sided group accounts. See docs/tally_sign_convention.md §3 for "
                f"required export settings."
            )

    return ParsedTallyTB(
        company_name=company_name,
        tb_date=tb_date,
        source_format="xml",
        source_file=str(path),
        ledgers=main_ledgers,
        groups=groups,
        total_dr=total_dr,
        total_cr=total_cr,
        is_balanced=abs(total_dr - total_cr) < 0.01,
        parse_warnings=warnings,
        student_ledgers=student_ledgers,
        system_ledgers=system_ledgers,
    )


def _clean_company_name(raw: str) -> str:
    """Strip Tally date suffix and collapse repeated spaces.

    Tally writes ``G H R C A C S -A0007 - (from 1-Apr-23)``; this returns
    ``G H R C A C S -A0007``.  Callers can pass ``company_name=`` override
    for a shorter canonical name.
    """
    # Strip " - (from ...)" suffix
    name = re.sub(r"\s*-?\s*\(from\s+[^)]+\)\s*$", "", raw.strip()).strip()
    return name


def _parse_group_element(elem: etree._Element) -> _RawGroup | None:
    """Extract name, parent, deemed_positive from a GROUP element."""
    name = (elem.get("NAME") or "").strip()
    if not name:
        return None
    parent_text = (elem.findtext("PARENT") or "").strip()
    # Self-referential entries (root groups) and empty-parent entries are both roots
    parent: str | None = parent_text if (parent_text and parent_text != name) else None
    dp_text = (elem.findtext("ISDEEMEDPOSITIVE") or "Yes").strip()
    return _RawGroup(name=name, parent=parent, deemed_positive=(dp_text == "Yes"))


def _build_group_map(
    raw_groups: dict[str, _RawGroup], warnings: list[str]
) -> tuple[dict[str, _GroupInfo], dict[str, _GroupInfo]]:
    """Resolve root_name, root_type, and parent_chain for every group.

    Returns ``(group_map, norm_map)`` where norm_map is keyed by
    ``_norm(name)`` for fuzzy lookup when lxml strips ``&`` from text.
    """
    # Pre-build normalized raw-group lookup so _resolve_chain can also fuzzy-match
    norm_raw: dict[str, _RawGroup] = {_norm(k): v for k, v in raw_groups.items()}

    group_map: dict[str, _GroupInfo] = {}
    for name in raw_groups:
        chain, root_name, root_type = _resolve_chain(
            name, raw_groups, norm_raw, warnings, set()
        )
        raw = raw_groups[name]
        group_map[name] = _GroupInfo(
            name=name,
            parent=raw.parent,
            deemed_positive=raw.deemed_positive,
            root_name=root_name,
            root_type=root_type,
            parent_chain=chain,
        )
    norm_map = {_norm(k): v for k, v in group_map.items()}
    return group_map, norm_map


def _infer_root_type(name: str, deemed_positive: bool) -> str:
    """Infer root_type for unknown root groups by name pattern + deemed_positive."""
    nl = name.lower()
    if any(k in nl for k in ("income", "revenue", "sales", "receipt")):
        return "Income"
    if any(k in nl for k in ("expense", "expenditure", "cost", "purchase")):
        return "Expense"
    if any(k in nl for k in ("liability", "loan", "payable", "creditor", "suspense")):
        return "Liability"
    if any(k in nl for k in ("asset", "fixed", "invest", "receivable", "debtor", "advance")):
        return "Asset"
    if any(k in nl for k in ("capital", "equity", "reserve", "surplus", "fund")):
        return "Equity"
    # Last resort: Dr nature → Asset, Cr nature → Liability
    return "Asset" if deemed_positive else "Liability"


def _resolve_chain(
    name: str,
    raw_groups: dict[str, _RawGroup],
    norm_raw: dict[str, _RawGroup],
    warnings: list[str],
    visited: set[str],
) -> tuple[list[str], str, str]:
    """Recursively trace the parent chain to the root group.

    Returns ``(parent_chain, root_name, root_type)`` where parent_chain
    is ``[root, ..., immediate_parent]`` (does NOT include *name* itself).

    Uses normalised lookup (``norm_raw``) as fallback when the direct key is
    not found — handles Tally's habit of writing ``&`` unescaped in PARENT
    text elements, which lxml silently drops in recover mode.
    """
    if name in visited:
        warnings.append(f"Circular parent reference for group {name!r}; stopping")
        return [], name, "Asset"

    raw = raw_groups.get(name) or norm_raw.get(_norm(name))
    if raw is None:
        warnings.append(f"Group {name!r} referenced but not defined; treating as root")
        return [], name, "Asset"

    if raw.parent is None:
        # This group IS the root
        root_type = TALLY_ROOT_TYPES.get(raw.name)
        if root_type is None:
            root_type = _infer_root_type(raw.name, raw.deemed_positive)
            warnings.append(
                f"Unknown root group {raw.name!r}; inferred root_type={root_type!r}"
            )
        return [], raw.name, root_type

    visited.add(name)
    parent_chain, root_name, root_type = _resolve_chain(
        raw.parent, raw_groups, norm_raw, warnings, visited
    )
    return parent_chain + [raw.parent], root_name, root_type


def _parse_ledger_element(
    elem: etree._Element,
    group_map: dict[str, _GroupInfo],
    norm_map: dict[str, _GroupInfo],
    warnings: list[str],
) -> tuple[Ledger | None, list[str]]:
    """Parse a LEDGER element into a Ledger.

    Returns ``(ledger | None, bill_dates)`` where bill_dates is a list of
    YYYYMMDD strings collected for TB-date inference.

    Falls back to a normalised key lookup when the direct parent name lookup
    fails — handles Tally's habit of writing literal ``&`` in XML text content
    (instead of ``&amp;``), which lxml's recover mode silently drops.
    """
    raw_name = (elem.get("NAME") or "").strip()
    if not raw_name:
        return None, []

    name, tally_id = strip_tally_id(raw_name)

    parent_text = (elem.findtext("PARENT") or "").strip()

    is_system_account = False
    if not parent_text:
        # Reserved system ledger (e.g. "Profit & Loss A/c") with no <PARENT>.
        # root_type=Equity is the conventional placement of P&L A/c in Tally.
        parent_group = ""
        root_type = "Equity"
        parent_chain: list[str] = []
        is_system_account = True
    else:
        parent_group = parent_text
        # Try direct lookup first; fall back to normalized key (& stripping)
        info = group_map.get(parent_text) or norm_map.get(_norm(parent_text))
        if info is None:
            warnings.append(
                f"Ledger {raw_name!r}: parent {parent_text!r} not found in group map"
            )
            root_type = "Asset"
            parent_chain = [parent_text]
        else:
            root_type = info.root_type
            parent_chain = info.parent_chain + [info.name]  # use canonical name
            parent_group = info.name  # normalize to canonical name

    # Bill allocations parsed FIRST so their sum can fill in a missing ledger OB.
    # Party accounts with ISBILLWISEON=Yes often have NO direct <OPENINGBALANCE>
    # on the ledger element itself; their net balance is the sum of all bill OBs.
    #
    # Sign rule (same for ledger OB and bill OBs, independent of ISDEEMEDPOSITIVE):
    #   value < 0  →  Dr side, amount = abs(value)
    #   value > 0  →  Cr side, amount = value
    # See docs/tally_sign_convention.md §1 for derivation and evidence.
    bill_allocs: list[BillAllocation] = []
    bill_dates: list[str] = []
    raw_bill_sum = 0.0

    for bill_el in elem.findall("BILLALLOCATIONS.LIST"):
        bill_name = (bill_el.findtext("NAME") or "").strip()
        bill_date = (bill_el.findtext("BILLDATE") or "").strip()
        bill_ob_text = (bill_el.findtext("OPENINGBALANCE") or "").strip()

        if not bill_ob_text:
            continue
        if bill_date and bill_date.isdigit() and len(bill_date) == 8:
            bill_dates.append(bill_date)

        bill_ob = float(bill_ob_text)
        raw_bill_sum += bill_ob

        if not bill_name:
            continue
        # Sign rule: negative → Dr, positive → Cr (see docstring above)
        b_side = "Dr" if bill_ob < 0 else "Cr"
        bill_allocs.append(
            BillAllocation(bill_name=bill_name, amount=abs(bill_ob), dr_cr=b_side)
        )

    # Ledger opening balance.
    # Prefer the direct <OPENINGBALANCE>; fall back to the sum of bill OBs for
    # bill-wise party accounts that omit the direct tag.
    ob_text = (elem.findtext("OPENINGBALANCE") or "").strip()
    ob = float(ob_text) if ob_text else raw_bill_sum

    # Sign rule: negative → Dr, positive → Cr.
    if ob < 0:
        opening_dr, opening_cr = abs(ob), 0.0
    elif ob > 0:
        opening_dr, opening_cr = 0.0, ob
    else:
        opening_dr = opening_cr = 0.0

    net = round(opening_cr - opening_dr, 2)
    if opening_dr > opening_cr:
        net_side = "Dr"
    elif opening_cr > opening_dr:
        net_side = "Cr"
    else:
        net_side = "Zero"

    # Diagnostic/routing flags (see docs/tally_sign_convention.md §4)
    #
    # Two routes to is_student_ledger=True:
    #   1. Per-student leaves: parent chain contains a student-group marker.
    #   2. Aggregate control accounts: cleaned name matches
    #      AGGREGATE_STUDENT_ACCOUNT_NAMES.
    # Both route to dux_voucher's Ex Student Opening Batch via the CSV
    # handoff; neither flows through the main Mapping Rule library.
    # See docs/mapper_design_notes.md §7 "Leaf-only posting principle".
    is_student_ledger = (
        _is_student(parent_chain)
        or _is_aggregate_student_account(name)
    )
    is_pnl_closed_zero = (
        root_type in ("Income", "Expense")
        and opening_dr == 0.0
        and opening_cr == 0.0
        and any(anc in _PNL_ROOTS for anc in parent_chain)
    )

    return (
        Ledger(
            name=name,
            tally_id=tally_id,
            parent_group=parent_group,
            parent_chain=parent_chain,
            root_type=root_type,
            opening_dr=round(opening_dr, 2),
            opening_cr=round(opening_cr, 2),
            net_amount=net,
            net_side=net_side,
            is_leaf=True,
            bill_allocations=bill_allocs,
            source_row=None,
            is_system_account=is_system_account,
            is_student_ledger=is_student_ledger,
            is_pnl_closed_zero=is_pnl_closed_zero,
        ),
        bill_dates,
    )


def _build_group_objects(
    group_map: dict[str, _GroupInfo],
    norm_map: dict[str, _GroupInfo],
    all_ledgers: list[Ledger],
) -> list[Group]:
    """Build Group dataclass objects and populate their children lists."""
    groups_out: dict[str, Group] = {
        name: Group(
            name=name,
            parent=info.parent,
            root_type=info.root_type,
            children=[],
        )
        for name, info in group_map.items()
    }

    # Sub-group → parent relationships (use normalized lookup for parent name)
    for name, info in group_map.items():
        if info.parent:
            parent_grp = groups_out.get(info.parent)
            if parent_grp is None:
                parent_info = norm_map.get(_norm(info.parent))
                if parent_info:
                    parent_grp = groups_out.get(parent_info.name)
            if parent_grp is not None:
                parent_grp.children.append(name)

    # Ledger → parent group relationships
    for ledger in all_ledgers:
        if ledger.parent_group in groups_out:
            groups_out[ledger.parent_group].children.append(ledger.name)

    return list(groups_out.values())


# ===========================================================================
# DISPLAY-REPORT parser  (legacy — flat DSPACCNAME/DSPACCINFO format)
# ===========================================================================

def _parse_display(
    path: Path,
    *,
    company_name: str,
    tb_date: str,
    warnings: list[str],
) -> ParsedTallyTB:
    """Parse a Tally display-report XML (DSPACCNAME/DSPACCINFO flat format)."""
    if not company_name:
        company_name = path.stem
        warnings.append(
            f"Display-report XML has no company name; inferred from filename: {company_name!r}"
        )
    if not tb_date:
        warnings.append("tb_date not provided; pass it explicitly for accurate output")

    raw = _display_parse_raw(path, warnings)
    ledgers, groups = _display_build_hierarchy(raw, warnings)

    total_dr = round(sum(l.opening_dr for l in ledgers), 2)
    total_cr = round(sum(l.opening_cr for l in ledgers), 2)

    return ParsedTallyTB(
        company_name=company_name,
        tb_date=tb_date,
        source_format="xml",
        source_file=str(path),
        ledgers=ledgers,
        groups=groups,
        total_dr=total_dr,
        total_cr=total_cr,
        is_balanced=abs(total_dr - total_cr) < 0.01,
        parse_warnings=warnings,
    )


@dataclass
class _DisplayEntry:
    raw_name: str
    dr: float  # always >= 0
    cr: float  # always >= 0


def _display_parse_raw(path: Path, warnings: list[str]) -> list[_DisplayEntry]:
    """Read the display-report XML and return flat (name, dr, cr) records."""
    try:
        tree = etree.parse(str(path))
    except etree.XMLSyntaxError as exc:
        raise ValueError(f"Cannot parse Tally XML at {path!r}: {exc}") from exc

    root = tree.getroot()
    children = list(root)
    if len(children) % 2 != 0:
        warnings.append(
            f"Odd child count ({len(children)}); last DSPACCNAME may lack DSPACCINFO"
        )

    entries: list[_DisplayEntry] = []
    for name_el, info_el in zip(children[::2], children[1::2]):
        raw_name = (name_el.findtext("DSPDISPNAME") or "").strip()
        if not raw_name:
            continue
        # Tally display convention: DSPCLDRAMTA is NEGATIVE of the Dr balance.
        dr_raw = (info_el.findtext("DSPCLDRAMT/DSPCLDRAMTA") or "").strip()
        cr_raw = (info_el.findtext("DSPCLCRAMT/DSPCLCRAMTA") or "").strip()
        entries.append(
            _DisplayEntry(
                raw_name=raw_name,
                dr=abs(float(dr_raw)) if dr_raw else 0.0,
                cr=float(cr_raw) if cr_raw else 0.0,
            )
        )
    return entries


def _display_build_hierarchy(
    raw: list[_DisplayEntry], warnings: list[str]
) -> tuple[list[Ledger], list[Group]]:
    """Reconstruct tree from the flat ordered display-report list."""
    root_positions = [
        i for i, e in enumerate(raw) if strip_tally_id(e.raw_name)[0] in TALLY_ROOT_TYPES
    ]

    if not root_positions:
        warnings.append("No known Tally root groups found — treating all as leaves")
        return _display_leaves_only(raw, "Unknown", "Asset"), []

    if root_positions[0] > 0:
        warnings.append(
            f"{root_positions[0]} entries before first known root group — skipped"
        )

    all_ledgers: list[Ledger] = []
    all_groups: list[Group] = []

    for pos_i, root_pos in enumerate(root_positions):
        next_root = (
            root_positions[pos_i + 1] if pos_i + 1 < len(root_positions) else len(raw)
        )
        root_entry = raw[root_pos]
        root_clean, _ = strip_tally_id(root_entry.raw_name)
        root_type = TALLY_ROOT_TYPES[root_clean]

        root_group_idx = len(all_groups)
        all_groups.append(
            Group(name=root_clean, parent=None, root_type=root_type, children=[])
        )

        subtree = raw[root_pos + 1 : next_root]
        sub_ledgers, sub_groups, direct_children = _display_process_subtree(
            subtree,
            parent_name=root_clean,
            parent_chain=[root_clean],
            root_type=root_type,
            warnings=warnings,
        )
        all_ledgers.extend(sub_ledgers)
        all_groups.extend(sub_groups)
        all_groups[root_group_idx].children.extend(direct_children)

    return all_ledgers, all_groups


def _display_process_subtree(
    entries: list[_DisplayEntry],
    parent_name: str,
    parent_chain: list[str],
    root_type: str,
    warnings: list[str],
) -> tuple[list[Ledger], list[Group], list[str]]:
    ledgers: list[Ledger] = []
    groups: list[Group] = []
    direct_children: list[str] = []
    i = 0

    while i < len(entries):
        e = entries[i]
        clean, tally_id = strip_tally_id(e.raw_name)
        child_end = _display_child_block_end(entries, i)

        if child_end is not None:
            direct_children.append(clean)
            child_entries = entries[i + 1 : child_end]
            sub_ledgers, sub_groups, sub_direct = _display_process_subtree(
                child_entries,
                parent_name=clean,
                parent_chain=parent_chain + [clean],
                root_type=root_type,
                warnings=warnings,
            )
            groups.append(
                Group(name=clean, parent=parent_name, root_type=root_type, children=sub_direct)
            )
            groups.extend(sub_groups)
            ledgers.extend(sub_ledgers)
            i = child_end
        else:
            direct_children.append(clean)
            net = round(e.cr - e.dr, 2)
            ledgers.append(
                Ledger(
                    name=clean,
                    tally_id=tally_id,
                    parent_group=parent_name,
                    parent_chain=list(parent_chain),
                    root_type=root_type,
                    opening_dr=e.dr,
                    opening_cr=e.cr,
                    net_amount=net,
                    net_side="Dr" if e.dr > e.cr else "Cr" if e.cr > e.dr else "Zero",
                    bill_allocations=[],
                    source_row=None,
                )
            )
            i += 1

    return ledgers, groups, direct_children


def _display_child_block_end(entries: list[_DisplayEntry], group_idx: int) -> int | None:
    """Amount-rollup: find exclusive end of group_idx's child block, or None."""
    g = entries[group_idx]
    if g.dr == 0.0 and g.cr == 0.0:
        return None

    acc_dr = acc_cr = 0.0
    for j in range(group_idx + 1, len(entries)):
        acc_dr += entries[j].dr
        acc_cr += entries[j].cr
        if abs(acc_dr - g.dr) < _AMT_TOL and abs(acc_cr - g.cr) < _AMT_TOL:
            return j + 1
        if (g.dr > 0 and acc_dr > g.dr + _AMT_TOL) or (
            g.cr > 0 and acc_cr > g.cr + _AMT_TOL
        ):
            break
    return None


def _display_leaves_only(
    entries: list[_DisplayEntry], parent: str, root_type: str
) -> list[Ledger]:
    result = []
    for e in entries:
        clean, tid = strip_tally_id(e.raw_name)
        net = round(e.cr - e.dr, 2)
        result.append(
            Ledger(
                name=clean,
                tally_id=tid,
                parent_group=parent,
                parent_chain=[parent],
                root_type=root_type,
                opening_dr=e.dr,
                opening_cr=e.cr,
                net_amount=net,
                net_side="Dr" if e.dr > e.cr else "Cr" if e.cr > e.dr else "Zero",
            )
        )
    return result


# ===========================================================================
# CLI smoke test
# ===========================================================================

if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser(
        prog="python -m rgi_migration.parsers.tally_xml_parser",
        description="Parse a Tally XML (All Masters or display-report) into "
                    "a normalised ParsedTallyTB and print a summary.",
    )
    ap.add_argument("path", help="Path to the Tally XML file")
    ap.add_argument("--company-name", default="", help="Override company name")
    ap.add_argument("--tb-date", default="", help="Override TB date (ISO, e.g. 2026-03-31)")
    ap.add_argument("--json", metavar="OUT", help="Write full ParsedTallyTB as JSON to OUT")
    ap.add_argument("--students-csv", metavar="OUT",
                    help="Write student ledgers as CSV to OUT (cols: "
                         "Student Name, Debit, Credit, Parent Group, Notes)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING)
    tb = parse_xml(args.path, company_name=args.company_name, tb_date=args.tb_date)

    print(repr(tb))

    def _totals(lst):
        return sum(l.opening_dr for l in lst), sum(l.opening_cr for l in lst)

    main_dr, main_cr = _totals(tb.ledgers)
    stu_dr, stu_cr = _totals(tb.student_ledgers)
    sys_dr, sys_cr = _totals(tb.system_ledgers)
    print(f"  Bucket totals:")
    print(f"    main    : {len(tb.ledgers):5d}  Dr {main_dr:>16,.2f}  Cr {main_cr:>16,.2f}")
    print(f"    student : {len(tb.student_ledgers):5d}  Dr {stu_dr:>16,.2f}  Cr {stu_cr:>16,.2f}")
    print(f"    system  : {len(tb.system_ledgers):5d}  Dr {sys_dr:>16,.2f}  Cr {sys_cr:>16,.2f}")

    nonzero = sum(1 for l in tb.ledgers if l.opening_dr > 0 or l.opening_cr > 0)
    with_bills = sum(1 for l in tb.ledgers if l.bill_allocations)
    pnl_zero = sum(1 for l in tb.ledgers if l.is_pnl_closed_zero)
    print(f"  Non-zero balance ledgers (main)   : {nonzero}")
    print(f"  Ledgers with bill allocs  (main)  : {with_bills}")
    print(f"  is_pnl_closed_zero ledgers (main) : {pnl_zero}")

    if args.json:
        tb.to_json(args.json)
        print(f"  JSON written -> {args.json}")
    if args.students_csv:
        n = tb.to_students_csv(args.students_csv)
        print(f"  Students CSV written ({n} rows) -> {args.students_csv}")

    if tb.parse_warnings:
        print(f"  Warnings ({len(tb.parse_warnings)}):")
        for w in tb.parse_warnings:
            print(f"    - {w}")
