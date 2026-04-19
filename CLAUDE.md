# RGI Tally → ERPNext Opening Balance Migration

Custom Frappe app that automates opening balance migration from Tally ERP 9 to ERPNext v16 for the Raisoni Group of Institutions (59 operating companies).

## Why This App Exists

Opening balance migration is currently manual: read Tally trial balance, map accounts to ERPNext COA, build Journal Entry + Opening Invoice Tool files, get client confirmation on ambiguous items. Each entity takes 2–3 hours. 59 entities × 2.5 hours = prohibitive. This app turns it into a 15–20 minute review per entity by automating the deterministic work and using Claude API surgically for judgment calls.

## High-Level Flow (per entity)

1. User uploads Tally XML export (primary) or Excel TB (fallback) and picks target ERPNext company
2. **Parser** extracts ledgers, group hierarchy, opening balances, bill allocations → normalized dict
3. **Mapper** matches each Tally leaf ledger to an ERPNext account in three tiers:
   - Tier 1: rule-based auto-match (exact name, saved `Mapping Rule` doctype hits, canonical patterns)
   - Tier 2: fuzzy-match (embedding similarity above threshold) — flagged for quick human OK
   - Tier 3: Claude API (ambiguous cases, abnormal balances, new-account suggestions)
4. **Review UI** — one table showing all mappings. Bulk-approve Tier 1. Quick-approve Tier 2. Individual attention on Tier 3. Keyboard shortcuts.
5. **Generator** builds draft Journal Entry + OIT CSV. Calculates `Temporary Opening - {ABBR}` balancer. Validates Dr = Cr.
6. Human previews and submits. Approved mappings auto-save as `Mapping Rule` rows (learning loop).

## Architecture Decisions (already made — do not relitigate)

- **In-app, not external.** Installed as a Frappe custom app. Direct ORM access (`frappe.get_all`, `frappe.get_doc`). No REST API, no external auth.
- **Tally XML is primary source**, Excel TB is fallback. XML gives us group hierarchy, opening balances, party bill allocations natively.
- **Three-tier mapper**, not pure-LLM. Cost, speed, and auditability.
- **Rules library compounds.** Every approved mapping becomes a `Mapping Rule` doctype row. By entity 10, Tier 1 auto-maps 70%+.
- **Never auto-submit.** Draft JE + OIT, human previews in ERPNext UI, human submits.
- **Test company first.** Every first run of a new entity goes to a test company first.

## Tech Stack

- Python 3.10+ (Frappe v15 baseline, runs on ERPNext v16)
- `lxml` for Tally XML parsing (not `xml.etree` — need XPath, better UTF-8 handling)
- `openpyxl` for Excel TB parsing (not `pandas` for the parse layer — we need cell-level control; pandas OK downstream if needed)
- `rapidfuzz` for fuzzy name matching (Tier 2)
- Anthropic Python SDK for Claude API calls (Tier 3)
- Frappe ORM for all ERPNext access

## Frappe App Structure (target)

```
rgi_migration/
├── rgi_migration/
│   ├── __init__.py
│   ├── hooks.py
│   ├── doctype/
│   │   ├── tally_migration_session/     # one row per entity migration run
│   │   ├── mapping_decision/            # child table of session, one per account
│   │   └── mapping_rule/                # learning library, persists across sessions
│   ├── parsers/
│   │   ├── __init__.py
│   │   ├── tally_xml_parser.py          # WEEK 1 — build first
│   │   ├── tally_excel_parser.py        # WEEK 1 — build second
│   │   └── normalized_schema.py         # shared output schema
│   ├── mapper/
│   │   ├── tier1_rules.py
│   │   ├── tier2_fuzzy.py
│   │   └── tier3_claude.py
│   ├── generators/
│   │   ├── je_builder.py
│   │   └── oit_builder.py
│   └── tests/
│       ├── fixtures/                    # sample Tally XMLs + expected outputs
│       └── test_*.py
├── setup.py
└── README.md
```

## Current Phase: Week 1 — Parser Only

**In scope right now:**
- `tally_xml_parser.py` — parse Tally native XML export to normalized dict
- `tally_excel_parser.py` — parse Tally TB Excel export to same normalized dict
- `normalized_schema.py` — shared Python dataclasses for the output
- Unit tests with fixtures from at least 2 real entities (GHRCE, ASSHST)
- CLI smoke test: `python -m rgi_migration.parsers.tally_xml_parser <path>` prints summary

**Out of scope right now (do not build):**
- DocTypes / Frappe app scaffolding (we'll do that in week 2)
- Mapper logic
- JE/OIT builders
- Claude API calls
- UI
- Any ERPNext integration

Parser must be runnable as a plain Python module first, independent of Frappe. We'll wire it into a Frappe DocType later.

## Normalized Output Schema (both parsers produce this)

```python
@dataclass
class Ledger:
    name: str                      # cleaned, no "-{ID}" suffix
    tally_id: str | None           # original numeric ID if present
    parent_group: str              # immediate parent
    parent_chain: list[str]        # full chain to root, root first
    root_type: str                 # Asset | Liability | Income | Expense | Equity
    opening_dr: float              # always positive, 0 if not Dr
    opening_cr: float              # always positive, 0 if not Cr
    net_amount: float              # cr - dr, signed
    net_side: str                  # "Dr" | "Cr" | "Zero"
    is_leaf: bool
    bill_allocations: list[BillAllocation]   # XML only, empty for Excel
    source_row: int | None         # for Excel: row number for debugging
    is_system_account: bool        # Tally internal account (e.g. "Profit & Loss A/c" — no <PARENT>). Excluded from total_dr/total_cr and from the migration ledger list.
    is_student_ledger: bool        # routed to separate student CSV instead of the main ledger output. True when parent_chain contains STUDENTS / CYBERVIDYA-* / PASSOUT-* / GHRIMR STUDENT / similar student sub-groups under Sundry Debtors.
    is_pnl_closed_zero: bool       # diagnostic flag for review UI. True when root_type is Income|Expense AND opening_dr==0 AND opening_cr==0 AND parent_chain includes one of: Sales Accounts / Purchase Accounts / Direct Incomes / Direct Expenses / Indirect Incomes / Indirect Expenses. See docs/tally_sign_convention.md §4.

@dataclass
class BillAllocation:
    bill_name: str
    amount: float
    dr_cr: str                     # "Dr" | "Cr"

@dataclass
class Group:
    name: str
    parent: str | None             # None if root
    root_type: str
    children: list[str]            # names of direct children (leaves + groups)

@dataclass
class ParsedTallyTB:
    company_name: str
    tb_date: str                   # ISO: 2026-03-31
    source_format: str             # "xml" | "excel"
    source_file: str
    ledgers: list[Ledger]
    groups: list[Group]
    total_dr: float
    total_cr: float
    is_balanced: bool              # abs(total_dr - total_cr) < 0.01
    parse_warnings: list[str]      # non-fatal issues (hierarchy math mismatch, encoding fallback, etc.)
```

## Code Conventions

- Type hints on every function signature. `from __future__ import annotations` at top of every file.
- Dataclasses for all structured data. Frozen where possible.
- No `print()` for diagnostics — use `logging.getLogger(__name__)`. Parser must be silent by default.
- `parse_warnings` list on the output object for non-fatal issues. Raise exceptions only for unparseable files.
- Every public function has a docstring with one-line summary, args, returns, raises.
- Test fixtures go in `tests/fixtures/` — put anonymized sample XML + Excel files there. Include expected output JSON for regression tests.
- Every parser rule that handles a Tally quirk must reference the quirk in a comment (e.g., `# Tally exports negative OPENINGBALANCE for Cr; this flips it`).

## Key Tally Quirks to Handle in the Parser

- **Account name has `-{ID}` suffix**: `Income Expenditure A/c-2127`. Strip the final `-{digits}` for `name`, keep raw in `tally_id`.
- **UTF-8 characters**: account names contain `₹`, `£` (yes, pound sign appears in `Caution Money £`). Force UTF-8 read, do not transcode.
- **Sign convention in XML**: `<OPENINGBALANCE>` is a string like `-12345.67` or `12345.67 Dr`. Tally is inconsistent across exports — handle both the `ISDEBIT=Yes/No` tag AND the sign/suffix. Test with real files.
- **Both-sided accounts**: a ledger can have separate Dr and Cr amounts in the same TB (from children rolling up). In Excel this shows as two numbers on one row. Parser preserves both as `opening_dr` and `opening_cr`; netting is the mapper's job, not the parser's.
- **Hierarchy in Excel**: detected via indentation (leading spaces in column A) OR outline level (`row.outline_level`). Use outline level if present, fall back to leading-space count.
- **Group vs leaf detection in Excel**: a row is a leaf if the next row at equal or lesser indent is not its child. In XML, use `<ISBILLWISEON>` or check if `<LEDGER>` has any child `<LEDGER>` elements — but simpler: any entity declared as `<GROUP>` is a group, any `<LEDGER>` is a leaf.
- **Parent chain to root type**: roots in Tally are fixed — `Capital Account`, `Loans (Liability)`, `Current Liabilities`, `Fixed Assets`, `Investments`, `Current Assets`, `Branch / Divisions`, `Misc. Expenses (ASSET)`, `Suspense A/c`, `Sales Accounts`, `Purchase Accounts`, `Direct Incomes`, `Direct Expenses`, `Indirect Incomes`, `Indirect Expenses`. Map each to `Asset | Liability | Equity | Income | Expense`.
- **Excel "Grand Total" rows**: the last row is usually a totals row with both Dr and Cr. Detect and exclude from ledgers; use for `total_dr` / `total_cr` verification.

## Validation the Parser Must Do

- Hierarchy math: for each group, `sum(children opening_dr) == group opening_dr` and same for Cr. Mismatches go to `parse_warnings`, not exceptions.
- Grand total math: `sum(all leaves opening_dr) == total_dr`, same for Cr.
- `is_balanced` flag: `abs(total_dr - total_cr) < 0.01`.
- Every ledger must resolve to a root type. If parent chain doesn't reach a known Tally root, add a warning.

## Testing

- `pytest` for all tests.
- Fixtures: put 2 anonymized real samples per format (XML and Excel) in `tests/fixtures/`. Use GHRCE (large, complex) and ASSHST (deficit entity, abnormal balances) as the two reference entities.
- Each fixture has a matching `expected_output.json` for snapshot comparison.
- One test per major Tally quirk (UTF-8, sign convention, both-sided, hierarchy math).
- CI: `pytest` must pass before any merge.

## Things NOT to Do

- Do not use `pandas.read_excel` for the primary parse — we need cell-level access (outline level, merged cells, formatting). OK to use pandas downstream.
- Do not use `xml.etree.ElementTree` — use `lxml`. Faster, better XPath, handles malformed real-world Tally XML.
- Do not hardcode any entity-specific logic in the parser. All entity quirks belong in the mapper's rules library (`Mapping Rule` doctype + `tier1_rules.py`).
- Do not call Claude API from the parser. Parser is deterministic.
- Do not install the app into a production ERPNext instance during Week 1. Local dev bench only.

## Useful References (for Claude Code, read on demand)

- `RGI_Migration_Rules.md` — full mapping rules library. NOT needed for parser work. Load when starting mapper (Week 2).
- ERPNext v16 JE import template — needed in Week 3 (generator). Not now.
- Tally XML DTD: Tally publishes a rough schema doc; real-world exports diverge, so trust the fixtures over the DTD.

## Definition of Done for Week 1

- [ ] `tally_xml_parser.py` parses both reference fixtures, produces correct `ParsedTallyTB`.
- [ ] `tally_excel_parser.py` parses both reference fixtures, produces the SAME `ParsedTallyTB` structure (same ledger count, same totals within ₹1).
- [ ] All hierarchy math and grand total checks pass on both fixtures.
- [ ] `pytest` green.
- [ ] CLI smoke test prints a clean summary (ledger count, group count, total Dr/Cr, warnings count).
- [ ] No Frappe import anywhere in the parsers package.
