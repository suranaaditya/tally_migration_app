# RGI Tally → ERPNext Opening Balance Migration

Custom Frappe app that automates opening balance migration from Tally
ERP 9 to ERPNext v16 for the Raisoni Group of Institutions (59 operating
companies). Installed on the Dux Digitech dev bench at `erp.jewonline.in`.

---

## For session-level context

**Read [`docs/SESSION_HANDOFF_2026_04_19.md`](docs/SESSION_HANDOFF_2026_04_19.md)
first.** It covers the current commit, Week 3 progress, immediate pending
tasks, and gotchas not captured here. This CLAUDE.md covers durable
project-wide decisions; the handoff doc covers what's currently in flight.

---

## Why this app exists

Opening-balance migration is otherwise manual: read Tally trial balance,
map each leaf to an ERPNext COA leaf, build Journal Entry + Opening
Invoice Tool files, get client confirmation on ambiguous items. Each
entity takes 2–3 hours. 59 entities × 2.5 hours is prohibitive. This app
turns each entity into a 15–20 minute review by automating deterministic
work and using Claude API (future Tier 3) only for judgment calls.

---

## High-level flow (per entity)

1. User uploads Tally XML export (primary) or Excel TB (fallback) and
   picks target ERPNext company.
2. **Parser** extracts ledgers, group hierarchy, opening balances, bill
   allocations → `ParsedTallyTB` normalized dataclass. Identifies
   student ledgers (per-student + aggregate) and routes them to a
   separate `student_ledgers` list.
3. **Mapper (Tier 1)** matches each non-student, non-P&L leaf ledger
   to an ERPNext account in three layers:
   - Layer 1: positive `Mapping Rule` hits (exact_ci mode)
   - Layer 2: positive `Mapping Rule` hits (regex / prefix / suffix / contains)
   - Layer 3: exact-name fallback against the target company's COA
   Anti-pattern rules and structural validators (P&L exclusion,
   group-account refusal) apply at each layer.
4. **Supplier resolution (Tier 1, parallel)** for vendor party ledgers
   (Sundry Creditors descendants that aren't control accounts): three
   layers — exact_ci → saved `Supplier Alias Rule` → fuzzy 85%.
5. **Tier 2 fuzzy (rapidfuzz)** — Week 4+, not yet built.
6. **Tier 3 Claude API** — Week 5+, not yet built.
7. **Review UI** — Week 4+, not yet built.
8. **Generators (Work Item 7, about to start)** build four artefacts:
   - Main Opening Journal Entry (Draft)
   - OIT CSV for net-Cr vendors
   - Party-wise Dr JE for net-Dr vendors (with `is_advance=Yes`)
   - Students CSV for dux_voucher handoff (see below)
9. Human previews, submits in ERPNext UI. Approved mappings can be
   promoted to new `Mapping Rule` rows (learning loop).

---

## Architecture decisions (already made — do not relitigate)

- **In-app, not external.** Installed as a Frappe custom app on the
  same bench as sibling apps. Direct ORM access. No REST API, no
  external auth.
- **Tally XML is primary source**, Excel TB is fallback. XML gives
  group hierarchy, opening balances, and party bill allocations
  natively.
- **Three-tier mapper** (rules → fuzzy → LLM), not pure LLM. Cost,
  speed, auditability.
- **Rules library compounds.** Every approved mapping becomes a
  `Mapping Rule` DocType row. Target: Tier 1 auto-maps 70%+ by
  entity 10.
- **Never auto-submit.** Draft JE + OIT, human previews in ERPNext,
  human submits.
- **Leaf-only posting principle.** All postings (Tally side and
  ERPNext side) happen at leaf level. Group accounts are containers
  whose balance equals the sum of their children — they are never
  postable. See `docs/mapper_design_notes.md §7`.
- **Cross-app architectural boundary with dux_voucher.** Student
  receivables are handled entirely by `dux_voucher`'s Ex Student
  Opening Batch, **not** by rgi_migration. The parser flags student
  ledgers (per-student leaves via parent-chain marker, plus aggregate
  control accounts via the `AGGREGATE_STUDENT_ACCOUNT_NAMES`
  frozenset in `rgi_migration/parsers/tally_xml_parser.py`); these
  flow to a 4-column CSV consumed by dux_voucher. Do NOT add Mapping
  Rules for student-shaped names. See
  `docs/mapper_design_notes.md §7`.
- **Rule-first ordering.** `Mapper.resolve()` runs positive-rule
  matching BEFORE any parent-chain-based routing (like party-ledger
  detection). Positive §4 rules are the primary classification
  signal; parent-chain routing is a fallback. See
  `docs/mapper_design_notes.md §6.1`.
- **Test entity first.** CACSPU (GHR CACS Pune, per RGI rules §1.2
  entry #20) is the reference entity for all Week 1-3 work. Real
  Week-4+ data tests are preceded by a test-company run.
- **`bench console` over `bench execute`** for Frappe-side scripts.
  See `docs/mapper_design_notes.md §5`.

---

## Tech stack

- **Python 3.14** (Frappe 16.12 / ERPNext 16.10 on this bench; `env`
  venv at `~/frappe-bench/env/bin/python`)
- **`lxml`** for Tally XML (not `xml.etree` — need XPath, better
  UTF-8, survives malformed real-world Tally XML)
- **`openpyxl`** for Excel TB (cell-level access for outline level +
  merged cells; not `pandas.read_excel`)
- **`rapidfuzz`** for supplier fuzzy matching (Tier-1 supplier Layer 3)
- **Frappe ORM** for all ERPNext access (`frappe.get_all`, `frappe.get_doc`)
- **Anthropic SDK** for future Tier-3 Claude API calls (Week 5+)

---

## Frappe app structure (current)

```
rgi_migration/                          # git repo root
├── rgi_migration/                      # Python package / Frappe app
│   ├── __init__.py                     # __version__ = "0.1.0"
│   ├── hooks.py                        # Frappe app metadata
│   ├── modules.txt                     # "Rgi Migration"
│   ├── parsers/                        # Week 1 — Frappe-free parsers
│   │   ├── normalized_schema.py        # Ledger / Group / ParsedTallyTB
│   │   ├── tally_xml_parser.py         # XML parse + AGGREGATE_STUDENT_ACCOUNT_NAMES
│   │   └── tally_excel_parser.py       # Excel parse, reuses xml parser helpers
│   ├── mapper/                         # Week 2-3 — Tier-1 mapper
│   │   ├── mapper.py                   # Mapper orchestrator + MappedDecision
│   │   ├── rule_source.py              # RuleSource protocol + JSON / InMemory / Frappe
│   │   ├── tier1_rules.py              # positive-rule resolution + anti-pattern lookup
│   │   ├── tier1_supplier.py           # Week 6 — supplier resolution + control accounts
│   │   ├── supplier_source.py          # SupplierSource protocol
│   │   └── validators.py               # P&L exclusion + group-account refusal
│   ├── tests/                          # pytest suite
│   │   ├── fixtures/                   # sample_cacspu_*.xml / .xlsx / .csv + README
│   │   └── test_*.py                   # 86 tests as of commit ce4d3ce
│   └── rgi_migration/                  # Frappe "module" dir (where DocType JSONs live)
│       ├── doctype/                    # 9 DocTypes (4 standalone + 5 child)
│       └── setup/                      # create_doctypes.py + seed_workflow.py
├── scripts/                            # standalone ops scripts
│   ├── seed_mapping_rules.py           # canonical rule data + seed orchestration
│   ├── build_sample_masters_xml.py     # regenerate committed 10 MB sample
│   └── check_*.py                      # diagnostics
├── docs/                               # architectural docs
│   ├── SESSION_HANDOFF_2026_04_19.md   # ★ latest session state — read first
│   ├── mapper_design_notes.md          # §1-§7 canonical design principles
│   ├── tally_sign_convention.md        # negative=Dr, closing-as-opening rules
│   ├── WEEK1_LEARNINGS.md              # non-obvious parser lessons
│   ├── week2_baseline_mapping.md       # real-file mapper baseline on CACSPU
│   ├── slim_tally_export.md            # upload architecture decision
│   ├── seed_plan.json                  # regeneratable 22-row seed plan
│   └── dux_voucher_integration.md      # NOT YET CREATED — loose end, create before students-CSV
├── RGI_Migration_Rules.md              # canonical rule library source
├── TENTATIVE_RULES.md                  # skipped / deferred rule items
├── pyproject.toml                      # flit_core, deps, pytest, ruff
├── setup.py                            # bench-scaffolded
└── CLAUDE.md                           # this file
```

Tests also ship a standalone 10 MB XML fixture
(`sample_cacspu_masters_sample.xml`) at the fixtures dir. The full
221 MB Tally export lives OUTSIDE the repo — conventionally at
`~/tally-exports/ghrcacs_masters.xml` on the local dev machine (old
filename, not yet renamed).

---

## Current phase: Week 3 — mapper polish + generator prep

Weeks 1-2 shipped (parser + Tier-1 mapper + 22-rule library). Week 3
Work Items 1-6 complete: GitHub remote, Frappe app scaffolded on
bench, 9 DocTypes created, seed inserted, supplier matching built,
control-account + leaf-only routing hardened.

**Work Item 7 (generators) is about to start, paused on pre-flight
inputs.** See `docs/SESSION_HANDOFF_2026_04_19.md §3` for what's
blocking.

**Week 2's "out of scope" list is now partially in scope.** DocTypes
exist. Mapper runs end-to-end. JE / OIT generators are Work Item 7.
Review UI is Week 4. Claude API is Week 5.

---

## Normalized output schema (unchanged since Week 1)

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
    source_row: int | None         # Excel row number for debugging
    is_system_account: bool        # Tally internal (no <PARENT>); e.g. P&L A/c
    is_student_ledger: bool        # routes to students CSV (dux_voucher handoff). True via
                                   #   (a) parent_chain contains STUDENTS / CYBERVIDYA-* /
                                   #       PASSOUT-* / GHRIMR STUDENT marker, OR
                                   #   (b) cleaned name in AGGREGATE_STUDENT_ACCOUNT_NAMES
    is_pnl_closed_zero: bool       # diagnostic; see tally_sign_convention.md §4
```

See `rgi_migration/parsers/normalized_schema.py` for the authoritative
definitions and `docs/tally_sign_convention.md` for the sign-handling
rules.

---

## Code conventions

- Type hints on every function signature.
  `from __future__ import annotations` at top of every file.
- Dataclasses for all structured data. `frozen=True` where possible.
- No `print()` for diagnostics — use `logging.getLogger(__name__)`.
  Parsers must be silent by default.
- `parse_warnings` list on the output object for non-fatal issues.
  Raise exceptions only for unparseable files.
- Every parser rule that handles a Tally quirk must comment the
  quirk (example: `# Tally exports negative OPENINGBALANCE for Cr`).
- ASCII-only in DocType field names, labels, and Select option
  lists. Runtime VALUES can be UTF-8 (e.g. `source_section="§4.1"`).

---

## Key Tally quirks (already handled in parsers)

- **Account-name `-{ID}` suffix**: `Income Expenditure A/c-2127`.
  Stripped to `name`; raw ID retained in `tally_id`.
- **UTF-8 in names**: `₹`, `£` (e.g. `Caution Money £`). Force
  UTF-8, never transcode.
- **Sign convention**: `<OPENINGBALANCE>` stores signed — negative
  → Dr, positive → Cr. See `docs/tally_sign_convention.md §1`.
- **"Closing as opening" export flag**: must be enabled for Tally
  XML export. See `docs/tally_sign_convention.md §2-§3`.
- **Both-sided accounts**: preserved as `opening_dr` and
  `opening_cr`; netting is mapper's concern.
- **Excel grand-total row**: detected and excluded; used for
  verification.
- **Name collisions by tally_id**: identity is always
  `(name, tally_id)` tuple, never name alone.

---

## Testing

- `pytest` for all tests. 86 pass + 1 pre-existing skip on the
  committed fixtures.
- Primary reference: **CACSPU** (GH Raisoni College of Arts Commerce
  & Science, Pune). Previous references to GHRCE / ASSHST in the
  original Week-1 CLAUDE.md are stale.
- Committed fixtures: `sample_cacspu_masters_sample.xml` (10 MB,
  curated subset + 13 must-include diagnostic ledgers),
  `sample_cacspu_opening_tb.xlsx` (29 KB),
  `cacspu_erpnext_coa_real.csv` (698-row real COA export),
  `jewonline_suppliers_real.csv` (10-row dev-bench supplier export),
  `sample_cacspu_erpnext_coa.csv` (hand-built stub, superseded by the
  real COA).
- Full-file tests: set `RGI_FULL_XML_PATH` env var to the absolute
  path of the 221 MB XML (kept outside the repo per
  `rgi_migration/tests/fixtures/README.md`).
- CI: `pytest` must pass before any merge.

---

## Things NOT to do

- **Do not use `pandas.read_excel`** for the primary parse — cell-
  level access required.
- **Do not use `xml.etree`** — use `lxml`.
- **Do not hardcode entity-specific logic in parsers.** Entity quirks
  belong in the `Mapping Rule` library + `tier1_rules.py`.
- **Do not call Claude API from the parser or mapper today.** Tier 3
  is Week 5+.
- **Do not route student ledgers through `Mapping Rule`.** Extend
  `AGGREGATE_STUDENT_ACCOUNT_NAMES` in the parser for new aggregate
  names. See `docs/mapper_design_notes.md §7`.
- **Do not auto-submit** any Journal Entry. Always Draft.
- **Do not use `bench execute` for multi-line scripts.** Use
  `bench --site X console` with a Python heredoc. See
  `docs/mapper_design_notes.md §5`.
- **Do not use `source_section_ref` as a Mapping Rule filter key.**
  The deployed field is `source_section` (Work Item 4 audit).
- **Do not push to `main` on origin.** Main is local-only
  (Week-1 snapshot). All work goes on
  `claude/unruffled-hellman-c54c95`.
- **Do not skip checkpoints during Work Item 7.** 9 review points
  across 4 generators. Prose design → pause → implement → pause.
  See `docs/SESSION_HANDOFF_2026_04_19.md §4`.

---

## Key reference docs

- [`docs/SESSION_HANDOFF_2026_04_19.md`](docs/SESSION_HANDOFF_2026_04_19.md)
  — **current session state**, latest commit, pending tasks.
- [`docs/mapper_design_notes.md`](docs/mapper_design_notes.md) —
  design principles §1-§7. Read before proposing architectural
  changes. Includes schema-mutation recipes (§5), supplier-matching
  and control-account policies (§6), leaf-only posting + cross-app
  boundary (§7).
- [`RGI_Migration_Rules.md`](RGI_Migration_Rules.md) — canonical
  rule library source (human-readable). §4 positive rules, §11
  anti-patterns. §4.10 deprecated 2026-04.
- [`docs/tally_sign_convention.md`](docs/tally_sign_convention.md) —
  why negative=Dr; "closing as opening" flag; defensive imbalance
  warnings.
- [`docs/week2_baseline_mapping.md`](docs/week2_baseline_mapping.md)
  — first-real-signal mapper baseline on the full 221 MB CACSPU
  export.
- [`docs/slim_tally_export.md`](docs/slim_tally_export.md) — upload
  architecture (three lanes, no custom uploader). Implementation
  deferred to Work Item 8.
- `docs/dux_voucher_integration.md` — **NOT YET CREATED.** Loose
  end from Week 3 kickoff; should be the first sub-task of Work
  Item 7 generator #4 (Students CSV). Referenced by multiple
  commits and docs but doesn't exist in the repo.
