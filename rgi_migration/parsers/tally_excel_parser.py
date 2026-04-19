"""Tally trial-balance Excel export parser -- 'opening TB' format with
explicit Dr/Cr columns.

Column layout (confirmed by the GHRCACS reference fixture
``sample_ghrcacs_opening_tb.xlsx``):

    A = account name; hierarchy conveyed via ``cell.alignment.indent``
    B = Opening Balance -- Debit
    C = Opening Balance -- Credit
    D = Closing Balance -- Debit
    E = Closing Balance -- Credit

The parser defaults to the **Closing** columns (D, E) because ERPNext
opening-balance migration carries forward each year's closing as the next
year's opening -- identical semantics to the XML parser's "Export closing
balances as opening balance" mode.  Pass ``use_columns="opening"`` to read
the B/C columns instead for diagnostic / reconciliation use.

**Known limitation -- inconsistent indentation.**  Tally Excel exports use
inconsistent indentation within subtrees, particularly for bill-wise party
accounts (students, creditors).  The parser uses an indent-stack hierarchy
which is correct for clean subtrees (Fixed Assets, Capital Account, Bank
Accounts, etc.) and approximate for student/creditor sub-groups.  When
paired with a companion XML export of the same entity, pass its group map
via ``known_groups=`` for exact parent resolution.  Regardless of
hierarchy resolution, per-ledger flags (``is_student_ledger``,
``is_system_account``, ``is_pnl_closed_zero``) are applied using the same
name-pattern logic as the XML parser, so they remain reliable.

**Group totals.**  Unlike the XML master format, Excel TB rows DO carry
rolled-up Dr/Cr totals on group rows.  The parser recognises group rows
and excludes them from ``ledgers``/``student_ledgers`` (they would
double-count against their own children).  Group totals are used for a
sum-of-children sanity check -- mismatches are logged to
``parse_warnings``, not raised as exceptions.

See ``docs/tally_sign_convention.md`` for the parser conventions shared
with the XML parser.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from openpyxl import load_workbook

from rgi_migration.parsers.normalized_schema import (
    BillAllocation,
    Group,
    Ledger,
    ParsedTallyTB,
)
from rgi_migration.parsers.tally_xml_parser import (
    TALLY_ROOT_TYPES,
    _PNL_ROOTS,
    _infer_root_type,
    _is_student,
    strip_tally_id,
)

_log = logging.getLogger(__name__)

UseColumns = Literal["opening", "closing"]

# Header band in the fixture occupies rows 1-8; data starts at row 9.
# We auto-detect rather than hard-code.
_MAX_HEADER_ROW = 30

# Tolerance for group-total vs sum-of-children sanity check
_GROUP_TOTAL_TOL = 1.0  # Rs. 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_excel(
    path: str,
    *,
    company_name: str = "",
    tb_date: str = "",
    use_columns: UseColumns = "closing",
    known_groups: dict[str, str] | None = None,
    known_ledger_parents: dict[str, str] | None = None,
) -> ParsedTallyTB:
    """Parse a Tally 'opening TB' Excel file into a ``ParsedTallyTB``.

    Args:
        path:                 Path to the ``.xlsx`` (or misnamed ``.xls``-but-
                              really-xlsx) file.
        company_name:         Entity name override.  If omitted, extracted from
                              the header band; falls back to filename stem.
        tb_date:              ISO date to stamp on the output.  If omitted and
                              ``use_columns="closing"``, inferred from the
                              header period string.
        use_columns:          ``"closing"`` (default, reads cols D/E = 31-Mar
                              close) or ``"opening"`` (reads cols B/C = 1-Apr
                              start).  The Closing columns match the XML
                              ``<OPENINGBALANCE>`` values when the XML was
                              exported with "Export closing balances as
                              opening balance" = Yes.
        known_groups:         Optional ``{group_name: parent_name}`` dict
                              from the companion XML parser (derived from
                              ``ParsedTallyTB.groups``).  Supplies the
                              authoritative group list AND the group-to-
                              parent-group mapping.  When provided, the
                              indent-based group-vs-leaf classification is
                              fully bypassed in favour of the XML's decision.
        known_ledger_parents: Optional ``{ledger_raw_name: parent_group_name}``
                              dict from the companion XML parser.  Used to
                              set each leaf row's parent accurately, which in
                              turn makes ``is_student_ledger`` detection
                              reliable (Tally Excel indentation is chaotic for
                              bill-wise party accounts).  When ``known_groups``
                              is supplied without this, the parser falls
                              back to indent-based parent resolution for
                              leaves only.

    Returns:
        Fully populated ``ParsedTallyTB`` with ``source_format="excel"``.

    Raises:
        ValueError: Workbook is unreadable or the column header band cannot
                    be located.
    """
    warnings: list[str] = []
    file_path = Path(path)

    try:
        wb = load_workbook(str(file_path), data_only=True)
    except Exception as exc:
        raise ValueError(f"Cannot open Excel file {path!r}: {exc}") from exc

    ws = wb.active
    if ws is None:
        raise ValueError(f"Workbook has no active sheet: {path!r}")

    # --- Header detection ---
    header = _detect_header(ws)
    if header["data_start_row"] is None:
        raise ValueError(
            f"Could not locate the data header row in {path!r}. "
            f"Expected rows 1-{_MAX_HEADER_ROW} to contain 'Opening Balance' "
            f"and 'Closing Balance' markers."
        )

    data_start_row = header["data_start_row"]

    if not company_name:
        company_name = header["company_name"] or file_path.stem
        if not header["company_name"]:
            warnings.append(
                f"company_name not in Excel header; inferred from filename: "
                f"{company_name!r}"
            )
    if not tb_date:
        tb_date = _infer_tb_date(header, use_columns, warnings)

    # --- Choose which columns to read ---
    if use_columns == "closing":
        dr_col, cr_col = 4, 5  # D, E (1-based)
    else:
        dr_col, cr_col = 2, 3  # B, C

    # --- Parse rows ---
    rows_raw = _read_rows(ws, data_start_row, dr_col, cr_col)

    # Drop a trailing Grand Total row if present
    grand_total_row = None
    if rows_raw and rows_raw[-1].name.lower().startswith("grand total"):
        grand_total_row = rows_raw.pop()

    # --- Classify each row as group vs leaf ---
    leaves, groups = _classify_and_resolve(
        rows_raw, warnings, known_groups, known_ledger_parents
    )

    # --- Sanity check vs Grand Total row ---
    if grand_total_row is not None:
        exp_dr, exp_cr = grand_total_row.dr, grand_total_row.cr
        sum_dr = round(sum(l.opening_dr for l in leaves), 2)
        sum_cr = round(sum(l.opening_cr for l in leaves), 2)
        if abs(sum_dr - exp_dr) > _GROUP_TOTAL_TOL:
            warnings.append(
                f"Grand Total Dr mismatch: leaf sum Rs.{sum_dr:,.2f} != "
                f"Excel row Rs.{exp_dr:,.2f}"
            )
        if abs(sum_cr - exp_cr) > _GROUP_TOTAL_TOL:
            warnings.append(
                f"Grand Total Cr mismatch: leaf sum Rs.{sum_cr:,.2f} != "
                f"Excel row Rs.{exp_cr:,.2f}"
            )

    # --- Partition leaves into main / student / system ---
    main_ledgers: list[Ledger] = []
    student_ledgers: list[Ledger] = []
    for l in leaves:
        if l.is_student_ledger:
            student_ledgers.append(l)
        else:
            main_ledgers.append(l)

    # --- Totals + imbalance warning (same logic as XML parser) ---
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

    grand = max(total_dr, total_cr)
    if grand > 0:
        diff = abs(total_dr - total_cr)
        if diff / grand > 0.01:
            warnings.append(
                f"Large imbalance detected (Dr - Cr = Rs.{total_dr - total_cr:,.2f} "
                f"on total Rs.{grand:,.2f}, {(diff/grand)*100:.2f}%). "
                f"This usually means the Tally export was done without 'Export "
                f"closing balances as opening balance' = Yes. See "
                f"docs/tally_sign_convention.md §3 / §5 for required export "
                f"settings and other possible causes."
            )

    return ParsedTallyTB(
        company_name=company_name,
        tb_date=tb_date,
        source_format="excel",
        source_file=str(file_path),
        ledgers=main_ledgers,
        groups=groups,
        total_dr=total_dr,
        total_cr=total_cr,
        is_balanced=abs(total_dr - total_cr) < 0.01,
        parse_warnings=warnings,
        student_ledgers=student_ledgers,
        system_ledgers=[],  # reserved (see normalized_schema.ParsedTallyTB)
    )


# ---------------------------------------------------------------------------
# Internal raw-row dataclass
# ---------------------------------------------------------------------------

@dataclass
class _Row:
    row_num: int
    indent: int
    name: str            # stripped; keeps -{ID} suffix intact for now
    dr: float            # the chosen-column Dr value (already coerced from None)
    cr: float            # the chosen-column Cr value


# ---------------------------------------------------------------------------
# Header detection
# ---------------------------------------------------------------------------

def _detect_header(ws) -> dict:
    """Locate the header band and return company_name, period, data_start_row."""
    info: dict = {
        "company_name": "",
        "period": "",
        "data_start_row": None,
    }

    for r in range(1, min(_MAX_HEADER_ROW, ws.max_row) + 1):
        row = [ws.cell(r, c).value for c in range(1, 7)]
        joined = " ".join(str(v) for v in row if v is not None).strip()

        if not info["company_name"] and joined and r <= 5 and _looks_like_company(joined):
            info["company_name"] = joined

        if re.search(r"\d+[-/][A-Za-z]+[-/]\d+", joined):
            info["period"] = joined

        # Header row 8 in the fixture: [None, 'Debit', 'Credit', 'Debit', 'Credit', None]
        cells_upper = [str(v).strip().upper() if v is not None else "" for v in row]
        if (
            cells_upper[1] == "DEBIT"
            and cells_upper[2] == "CREDIT"
            and cells_upper[3] == "DEBIT"
            and cells_upper[4] == "CREDIT"
        ):
            info["data_start_row"] = r + 1
            break

    return info


def _looks_like_company(s: str) -> bool:
    """Very loose: a plausible company-name line is mostly letters/spaces/hyphens."""
    if not s or any(k in s.upper() for k in ("TRIAL BALANCE", "PARTICULARS", "DEBIT", "CREDIT")):
        return False
    if re.fullmatch(r"[A-Za-z&.\-\s,/()]+", s):
        return True
    return False


def _infer_tb_date(header: dict, use_columns: str, warnings: list[str]) -> str:
    """Best-effort ISO date from the 'period' header string."""
    period = header.get("period") or ""
    # Match something like "1-Apr-25 to 31-Mar-26"
    m = re.search(
        r"(\d{1,2})[-/](\w{3,})[-/](\d{2,4})\s*(?:to|-)\s*"
        r"(\d{1,2})[-/](\w{3,})[-/](\d{2,4})",
        period,
    )
    if not m:
        warnings.append(
            f"tb_date not provided and period string not parseable "
            f"({period!r}); returning empty string"
        )
        return ""

    d1, mo1, y1, d2, mo2, y2 = m.groups()
    pick = (d2, mo2, y2) if use_columns == "closing" else (d1, mo1, y1)
    iso = _to_iso(*pick)
    if iso is None:
        warnings.append(f"Could not parse date components from period {period!r}")
        return ""
    return iso


_MONTHS = {
    "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04",
    "MAY": "05", "JUN": "06", "JUL": "07", "AUG": "08",
    "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12",
}


def _to_iso(d: str, mo: str, y: str) -> str | None:
    try:
        day = int(d)
        mon = _MONTHS.get(mo[:3].upper())
        if mon is None:
            return None
        yr = int(y)
        if yr < 100:
            yr += 2000
        return f"{yr:04d}-{mon}-{day:02d}"
    except (ValueError, KeyError):
        return None


# ---------------------------------------------------------------------------
# Row reading
# ---------------------------------------------------------------------------

def _read_rows(ws, data_start: int, dr_col: int, cr_col: int) -> list[_Row]:
    """Collect non-empty rows with their indent, name, and chosen Dr/Cr values."""
    out: list[_Row] = []
    for row in ws.iter_rows(min_row=data_start, max_row=ws.max_row):
        name_cell = row[0]
        raw_name = name_cell.value
        if raw_name is None:
            continue
        name = str(raw_name).replace("_x000D_", "").strip()
        if not name:
            continue

        indent_val = 0
        if name_cell.alignment and name_cell.alignment.indent is not None:
            indent_val = int(name_cell.alignment.indent)

        def _num(v) -> float:
            return float(v) if isinstance(v, (int, float)) else 0.0

        out.append(
            _Row(
                row_num=name_cell.row,
                indent=indent_val,
                name=name,
                dr=round(_num(row[dr_col - 1].value), 2),
                cr=round(_num(row[cr_col - 1].value), 2),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Classification: groups vs leaves, hierarchy via indent stack
# ---------------------------------------------------------------------------

def _classify_and_resolve(
    rows: list[_Row],
    warnings: list[str],
    known_groups: dict[str, str] | None,
    known_ledger_parents: dict[str, str] | None,
) -> tuple[list[Ledger], list[Group]]:
    """Determine groups vs leaves, build ``Ledger``/``Group`` objects.

    Group classification:
        * If ``known_groups`` is supplied, a row is a GROUP iff its name is
          a key in that dict.  This is exact when the companion XML is
          available.
        * Otherwise (fallback): a row is a GROUP if it has at least one
          descendant in the indent tree (some later row has strictly greater
          indent before returning to <= current indent).

    Parent resolution for leaves:
        1. ``known_ledger_parents[raw_name]`` (exact, from XML) if supplied.
        2. Otherwise: parent = top of indent stack (most recent group with
           strictly lower indent).  Approximate; can miscategorise
           chaotically-indented student rows.

    Parent resolution for groups:
        1. ``known_groups[group_name]`` (exact, from XML) if supplied.
        2. Otherwise: same indent-stack fallback.
    """
    n = len(rows)
    known_parents = known_groups or {}
    known_leaf = known_ledger_parents or {}

    # ---------- Group classification ----------
    is_group: list[bool] = [False] * n
    if known_groups:
        known_group_names = set(known_parents.keys())
        for i, r in enumerate(rows):
            # Consider the row a group if XML says so, OR it's at indent=0
            # (root groups always) -- handles user-created roots not in
            # known_groups but still at top-level.
            is_group[i] = r.name in known_group_names or r.indent == 0
    else:
        # Fallback: indent-based descendant test
        for i in range(n):
            for j in range(i + 1, n):
                if rows[j].indent <= rows[i].indent:
                    break
                if rows[j].indent > rows[i].indent:
                    is_group[i] = True
                    break

    # ---------- Parent / chain / root_type per row ----------
    parent_of: list[str] = [""] * n
    parent_chain_of: list[list[str]] = [[] for _ in range(n)]
    root_type_of: list[str] = [""] * n

    # Indent stack for fallback hierarchy
    stack: list[tuple[int, str, str]] = []  # (indent, name, root_type)

    for i, r in enumerate(rows):
        # Maintain indent stack regardless (used for fallback AND to track
        # current "path" even when known_groups is supplied)
        while stack and stack[-1][0] >= r.indent:
            stack.pop()

        parent_name = ""
        chain: list[str] = []
        root_type = ""

        if is_group[i]:
            # Group: prefer known_groups lookup for parent + chain
            kn_parent = known_parents.get(r.name)
            if kn_parent:
                chain, root_type = _resolve_chain_known(r.name, known_parents)
                parent_name = kn_parent
            elif stack:
                parent_name = stack[-1][1]
                root_type = stack[-1][2]
                chain = [s[1] for s in stack]
            else:
                # Root group
                parent_name = ""
                root_type = TALLY_ROOT_TYPES.get(r.name) or _infer_root_type(
                    r.name, True
                )
                if r.name not in TALLY_ROOT_TYPES:
                    warnings.append(
                        f"Unknown root group {r.name!r} (row {r.row_num}); "
                        f"inferred root_type={root_type!r}"
                    )
                chain = []
        else:
            # Leaf: prefer known_ledger_parents lookup
            kn_leaf_parent = known_leaf.get(r.name)
            if kn_leaf_parent and kn_leaf_parent in known_parents:
                chain, root_type = _resolve_chain_known(kn_leaf_parent, known_parents)
                # Append the parent itself so chain ends at immediate parent
                chain = chain + [kn_leaf_parent]
                parent_name = kn_leaf_parent
            elif kn_leaf_parent:
                # Parent is known but not a resolvable group -- rare
                parent_name = kn_leaf_parent
                chain = [kn_leaf_parent]
                root_type = _infer_root_type(kn_leaf_parent, True)
            elif stack:
                parent_name = stack[-1][1]
                root_type = stack[-1][2]
                chain = [s[1] for s in stack]
            else:
                # Orphan leaf (no parent on stack and not in known_leaf) --
                # shouldn't happen in a well-formed TB; default to Asset
                parent_name = ""
                chain = []
                root_type = "Asset"

        parent_of[i] = parent_name
        parent_chain_of[i] = chain
        root_type_of[i] = root_type

        if is_group[i]:
            stack.append((r.indent, r.name, root_type))

    # --- Build Ledger and Group objects ---
    leaves: list[Ledger] = []
    groups_list: list[Group] = []

    for i, r in enumerate(rows):
        if is_group[i]:
            groups_list.append(
                Group(
                    name=r.name,
                    parent=parent_of[i] or None,
                    root_type=root_type_of[i],
                    children=[],  # populated below
                )
            )
        else:
            name_clean, tally_id = strip_tally_id(r.name)
            net = round(r.cr - r.dr, 2)
            if r.dr > r.cr:
                net_side = "Dr"
            elif r.cr > r.dr:
                net_side = "Cr"
            else:
                net_side = "Zero"

            chain = parent_chain_of[i]
            parent_group = parent_of[i]
            root_type = root_type_of[i] or "Asset"
            is_student = _is_student(chain) or _is_student([parent_group])
            is_pnl_zero = (
                root_type in ("Income", "Expense")
                and r.dr == 0.0
                and r.cr == 0.0
                and any(anc in _PNL_ROOTS for anc in chain)
            )

            leaves.append(
                Ledger(
                    name=name_clean,
                    tally_id=tally_id,
                    parent_group=parent_group,
                    parent_chain=list(chain),
                    root_type=root_type,
                    opening_dr=r.dr,
                    opening_cr=r.cr,
                    net_amount=net,
                    net_side=net_side,
                    is_leaf=True,
                    bill_allocations=[],
                    source_row=r.row_num,
                    is_system_account=False,  # no <PARENT>-less rows in Excel
                    is_student_ledger=is_student,
                    is_pnl_closed_zero=is_pnl_zero,
                )
            )

    # Populate Group.children
    name_to_group = {g.name: g for g in groups_list}
    for i, r in enumerate(rows):
        if is_group[i]:
            continue  # skip leaf-population for group rows themselves
        parent = parent_of[i]
        if parent and parent in name_to_group:
            name_to_group[parent].children.append(rows[i].name)
    # Also add sub-groups as children of their parents
    for g in groups_list:
        if g.parent and g.parent in name_to_group:
            name_to_group[g.parent].children.append(g.name)

    return leaves, groups_list


def _resolve_chain_known(
    name: str, known_parents: dict[str, str]
) -> tuple[list[str], str]:
    """Walk ``known_parents`` to build a root-first parent chain + root_type.

    The returned chain does NOT include ``name`` itself; it starts at the
    root and ends at the immediate parent of ``name``.
    """
    chain: list[str] = []
    visited: set[str] = {name}
    cur = known_parents.get(name)
    while cur and cur not in visited:
        visited.add(cur)
        chain.insert(0, cur)
        cur = known_parents.get(cur)
    if not chain:
        # ``name`` is a root (or isolated); treat its own mapping as root_type
        root_type = TALLY_ROOT_TYPES.get(name) or _infer_root_type(name, True)
        return [], root_type
    root_name = chain[0]
    root_type = TALLY_ROOT_TYPES.get(root_name) or _infer_root_type(root_name, True)
    return chain, root_type


def hierarchy_from_parsed_tb(
    xml_tb: ParsedTallyTB,
) -> tuple[dict[str, str], dict[str, str]]:
    """Extract ``(known_groups, known_ledger_parents)`` dicts from a parsed
    XML trial balance, ready to pass to ``parse_excel(...)``.

    * ``known_groups``: ``{group_name: parent_name}`` -- empty parent for roots.
    * ``known_ledger_parents``: ``{ledger_raw_name: parent_group_name}``
      where ``raw_name`` is the name WITH the ``-{tally_id}`` suffix
      reattached, to match the Excel column-A string exactly.
    """
    known_groups: dict[str, str] = {
        g.name: (g.parent or "") for g in xml_tb.groups
    }

    known_ledger_parents: dict[str, str] = {}
    for lst in (xml_tb.ledgers, xml_tb.student_ledgers, xml_tb.system_ledgers):
        for l in lst:
            raw_name = f"{l.name}-{l.tally_id}" if l.tally_id else l.name
            known_ledger_parents[raw_name] = l.parent_group
    return known_groups, known_ledger_parents


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        prog="python -m rgi_migration.parsers.tally_excel_parser",
        description="Parse a Tally 'opening TB' Excel file into a normalised "
                    "ParsedTallyTB and print a summary.",
    )
    ap.add_argument("path", help="Path to the Excel file")
    ap.add_argument("--company-name", default="", help="Override company name")
    ap.add_argument("--tb-date", default="", help="Override TB date (ISO)")
    ap.add_argument("--use-columns", choices=("closing", "opening"), default="closing")
    ap.add_argument(
        "--xml-companion",
        metavar="PATH",
        help="Path to the companion XML file for the same entity.  When "
             "supplied, its parsed hierarchy is used to resolve the Excel's "
             "chaotic indent-based hierarchy -- strongly recommended.",
    )
    ap.add_argument("--json", metavar="OUT", help="Write ParsedTallyTB as JSON")
    ap.add_argument("--students-csv", metavar="OUT", help="Write student CSV")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING)

    known_groups = None
    known_leaf_parents = None
    if args.xml_companion:
        from rgi_migration.parsers.tally_xml_parser import parse_xml
        xml_tb = parse_xml(args.xml_companion)
        known_groups, known_leaf_parents = hierarchy_from_parsed_tb(xml_tb)

    tb = parse_excel(
        args.path,
        company_name=args.company_name,
        tb_date=args.tb_date,
        use_columns=args.use_columns,
        known_groups=known_groups,
        known_ledger_parents=known_leaf_parents,
    )
    print(repr(tb))
    print(f"  use_columns: {args.use_columns}")

    def _tot(lst):
        return sum(l.opening_dr for l in lst), sum(l.opening_cr for l in lst)

    mdr, mcr = _tot(tb.ledgers)
    sdr, scr = _tot(tb.student_ledgers)
    print(f"  main    : {len(tb.ledgers):5d}  Dr {mdr:>16,.2f}  Cr {mcr:>16,.2f}")
    print(f"  student : {len(tb.student_ledgers):5d}  Dr {sdr:>16,.2f}  Cr {scr:>16,.2f}")
    print(f"  groups  : {len(tb.groups):5d}")

    if args.json:
        tb.to_json(args.json)
        print(f"  JSON -> {args.json}")
    if args.students_csv:
        n = tb.to_students_csv(args.students_csv)
        print(f"  students CSV ({n} rows) -> {args.students_csv}")

    if tb.parse_warnings:
        print(f"  Warnings ({len(tb.parse_warnings)}):")
        for w in tb.parse_warnings:
            print(f"    - {w}")
