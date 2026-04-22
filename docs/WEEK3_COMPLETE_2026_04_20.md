# Week 3 complete — 2026-04-20

**TL;DR**: All 4 generators (Main JE, OIT CSV, Party-Dr JE, Students CSV)
are scaffolded, unit-tested, and end-to-end integration-smoke-tested
against the full 221 MB CACSPU export. Refusal contracts hold; cleanup
leaks zero artefacts; gen #4 produces a 369-row CSV. Week 4 starts on a
tractable **39-decision review surface** (21 unmapped + 18
pending_supplier_creation) plus the review-UI work itself.

Read this doc together with
[`docs/SESSION_HANDOFF_2026_04_19.md`](SESSION_HANDOFF_2026_04_19.md)
for Week 1-3 context and the §9 Week-3 → Week-4 carry-over list in
[`docs/mapper_design_notes.md`](mapper_design_notes.md).

---

## 1. What shipped in Week 3

### Infrastructure (Work Items 1-6)

* GitHub remote + `week1-complete` / `week2-complete` tags
* Prereq check: Frappe 16.12 / ERPNext 16.10 / Python 3.14 on `erp.jewonline.in`
* Frappe app scaffold installed at `~/frappe-bench/apps/rgi_migration`
* **9 DocTypes** (4 standalone + 5 child tables) with schema-drift audit
  patches — field-name drift from `source_section_ref` → `source_section`
  normalised; v16 schema-mutation recipes captured in
  [`docs/mapper_design_notes.md §5`](mapper_design_notes.md)
* **22 Mapping Rules seeded** (19 positive + 3 anti-pattern; §4.10 deleted
  post-leaf-only architectural decision)
* **Tier-1 supplier resolution**: 3-layer (`exact_ci` → alias stub → rapidfuzz
  85%) + **8 hardcoded control-account patterns** short-circuiting party
  routing; rule-first ordering in `Mapper.resolve()`
* **Decision 1**: zero-balance exclusion at mapper boundary — new
  `excluded_zero_balance` tier short-circuits Tally definitional noise
  before any resolver runs (1,552 of 1,964 CACSPU ledgers caught)
* **Case-insensitive + whitespace-tolerant exact-match** (bug surfaced on
  `STUDENT PAYABLE CYBERVIDYA` title-case mismatch; CYBERVIDYA ×2 now
  resolve correctly)
* **Tally Migration Session DocType** complete with all generator-artefact
  fields: `generated_je_draft`, `generated_advance_je`,
  `generated_oit_file`, `student_ledger_file`, `generated_je_reference`,
  `generated_advance_je_reference`, `temp_opening_amount`, `error_log`
* **Autoname fix** (`.###` → `{#####}`) and two Company-configuration
  corrections on CACSPU (`default_payable_account` pointed at wrong leaf)

### Generators (Work Item 7)

All four follow the same pure-core + Frappe-entry split pattern:

| Generator | File | Tests | Output |
|---|---|---:|---|
| #1 Main Opening JE | [rgi_migration/generators/opening_je.py](../rgi_migration/generators/opening_je.py) | 21 | Draft `Journal Entry` (voucher_type=Opening Entry) |
| #2 OIT CSV | [rgi_migration/generators/oit_csv.py](../rgi_migration/generators/oit_csv.py) | 16 | File attachment per RGI §5.2 schema |
| #3 Party-wise Dr JE | [rgi_migration/generators/advance_je.py](../rgi_migration/generators/advance_je.py) | 11 | Draft `Journal Entry` (is_advance=Yes per line) |
| #4 Students CSV | [rgi_migration/generators/students_csv.py](../rgi_migration/generators/students_csv.py) | 15 | File attachment per dux_voucher `import_from_csv` contract |

All share `Temporary Opening - {ABBR}` as the balancer account; net to
zero when all four artefacts are submitted/imported (including
dux_voucher's Ex Student Opening Batch JE downstream of #4).

### Documentation

* [`docs/mapper_design_notes.md`](mapper_design_notes.md) — §1-§10 design
  principles, schema-mutation recipes, generator conventions (§8),
  Week-3 → Week-4 carry-over list (§9)
* [`docs/dux_voucher_integration.md`](dux_voucher_integration.md) —
  authoritative cross-app contract for generator #4 → dux_voucher's
  `Ex Student Opening Batch`
* [`docs/SESSION_HANDOFF_2026_04_19.md`](SESSION_HANDOFF_2026_04_19.md)
  — Week 1-3 context carry-over for new sessions
* [`scripts/analyze_unmapped.py`](../scripts/analyze_unmapped.py) —
  reusable diagnostic for per-tier unmapped-bucket analysis
* [`scripts/week3_integration_smoke.py`](../scripts/week3_integration_smoke.py)
  — reusable 4-generator smoke test with side-effect integrity +
  cleanup-leak verification

### Testing

* **170 unit tests + 1 pre-existing skip** — suite passes in ~2s locally
* Integration smoke validated end-to-end on the 221 MB CACSPU export
  (5.9s wall-clock for 4 generators serially; cleanup verified zero-leak)

---

## 2. CACSPU current-state numbers

Post-all-fixes tier breakdown on the full 221 MB CACSPU XML, from the
integration smoke run on commit `412de4d`:

| Bucket | Count | Note |
|---|---:|---|
| `excluded_zero_balance` | **1,552** | Architectural exclusion — Tally definitional noise |
| `excluded_pnl` | 347 | P&L closes to zero on opening |
| `tier1_exact` | 102 | Auto-resolved for main JE |
| `tier1_rule` | 11 | Auto-resolved for main JE |
| `tier1_pattern` | 2 | Auto-resolved for main JE |
| `tier1_supplier_fuzzy` | 7 | Auto-resolved for OIT CSV |
| `pending_account_creation` | **0** | (was 1 pre-Decision-1; collapsed into zero-balance) |
| `group_refused` | **0** | (was 4 pre-Decision-1; collapsed into zero-balance) |
| `pending_supplier_creation` | 18 | **Needs Supplier Creation Requests in Week 4** |
| `unmapped` | 21 | **Needs rules / review / Tier-2 fuzzy in Week 4** |
| **Total** | **1,964** | |

Non-noise fraction: 412 of 1,964 (1,552 zero-balance + 347 P&L = 1,899
excluded; 65 eligible + 347 P&L excluded after decisions = 412 carrying
real balances).

### Student handoff (generator #4)

| Metric | Value |
|---|---:|
| Raw student ledgers in parser | 4,063 |
| Unique student base names | 4,059 |
| Zero-net after aggregation (skipped) | 3,694 (91%) |
| CSV rows emitted | **369** (313 net-Dr + 56 net-Cr) |
| Total debit on CSV | ₹45.55 lakh |
| Total credit on CSV | ₹7.46 lakh |
| Net Dr | **₹38.09 lakh** |

Aggregation total-preservation invariant verified: raw
`Σ opening_dr − Σ opening_cr` == emitted
`Σ debit_amount − Σ credit_amount` to the paisa.

---

## 3. Week 4 review surface — 39 decisions

### 21 unmapped (non-zero, non-leaked)

From the analyze_unmapped diagnostic + post-fix residual. Clusters
(per the §4 sample in the analyze_unmapped run):

* **Inter-entity receivables** under `Branch / Divisions > G H Raisoni
  Group Of Institution` (~6 cases, large balances — ₹2.99 crore Dr on
  `G H R Education & Medical Foundation Nagpur`, etc.)
* **Bank accounts** with name-divergence from COA — Tally
  `Bank of Maharashtra (Cap) - 60451303968` vs COA
  `Bank of Maharashtra A/c No. - 60451303968 (Capital) - CACSPU`.
  Same account number, different framing. **Tier-2 fuzzy territory**
  per Week 4-5.
* **TDS Payable variants** under `Current Liabilities > Duties & Taxes`
  — smaller balances, need either Mapping Rules or Account Creation
  Requests.
* **Fixed Asset leaves** (`Library Books`, `Laboratory equipment`,
  `Electrical Fitting`) — some exist in COA under different names
  (`Library Books Maintenance - CACSPU`), others don't exist at all
  (`Electrical Fitting`) — mix of Mapping Rules + Account Creation.
* **Miscellaneous receivables** — personal advances, inventory
  consumables, Ph.D fee ledgers.

### 18 pending_supplier_creation

Real vendors with non-zero balances needing ERPNext `Supplier` doc
creation via the Week-4 Supplier Creation Request workflow. Once
created, re-run mapping → these cases resolve to
`tier1_supplier_exact`, and generators #2 / #3 unblock.

### Total

**39 decisions** for Week 4's first-pass review cycle. Tractable in a
single reviewer session. Extrapolated to 59 entities: ~2,300 decisions
total — feasible for the RGI accounting team.

---

## 4. Blocking issues for Week 4

**None on the generator side.** All 4 generators are production-ready
and waiting for the review UI to consume their output.

Week 4's first task is the review UI itself — decisions flow through it,
and the auto-regenerate pattern from gens #1-#4 means a review action
directly unblocks the next refusal path.

---

## 5. Architectural decisions locked in Week 3

| # | Decision | Anchor |
|---|---|---|
| 1 | Zero-balance exclusion at mapper boundary (`excluded_zero_balance`) | [design notes §2.zero_balance_exclusion](mapper_design_notes.md) |
| 2 | Leaf-only posting principle — no postings to group accounts | [design notes §7](mapper_design_notes.md) |
| 3 | Cross-app boundary with dux_voucher via student CSV | [dux_voucher_integration.md](dux_voucher_integration.md) |
| 4 | Case-insensitive + whitespace-tolerant exact-match (Layer 3) | `rgi_migration/mapper/tier1_rules.py` |
| 5 | Per-supplier aggregation (gens #2/#3) + per-student aggregation (gen #4) | Generator modules |
| 6 | Rule-first ordering in `Mapper.resolve()` — positive rules before party-routing | [design notes §6.1](mapper_design_notes.md) |
| 7 | Draft-only JE generation — never auto-submit | CLAUDE.md, generator modules |
| 8 | Refusal contracts with Order A (all-problems-at-once, single message) | [design notes §8.3](mapper_design_notes.md) |
| 9 | Shared `Temporary Opening - {ABBR}` balancer across all 4 generators + dux_voucher's Ex Student JE | All generator modules |
| 10 | Reparse-and-remap per generation (Week-4 deferral of persistence) | [design notes §8.2](mapper_design_notes.md) |
| 11 | 4-column CSV filename convention `{artefact}-{ABBR}-{fiscal_year}-{timestamp}.{ext}` | [design notes §8.4](mapper_design_notes.md) |
| 12 | Student-ledger parser-level routing (parent-chain markers + `AGGREGATE_STUDENT_ACCOUNT_NAMES`) | `rgi_migration/parsers/tally_xml_parser.py` |

---

## 6. Carry-overs from design-notes §9 (status update)

| # | Item | Status |
|---|---|---|
| 1 | `Tally Migration Session Event` child DocType | **Deferred** — generators route events through `session.error_log` as timestamped blocks. Week-4 UI may want structured events later; clean schema migration when it does. |
| 2 | `FrappeRuleSource` / `FrappeSupplierSource` Frappe-native implementations | **Deferred** — generators use `JsonFileRuleSource(docs/seed_plan.json)` + inline `frappe.get_all("Supplier")` wrapped in `InMemorySupplierSource`. Current seed is source of truth; DocType rows were seeded from it. |
| 3 | `fiscal_year_short` auto-populate hook | **Needed for Week 4** — Desk-created sessions will leave it blank (the field is hidden + read_only with no fetch_from / hook). Trivial `before_insert` on `tally_migration_session.py` computes `"{YY}-{YY+1}"` from `fiscal_year`. |
| 4 | Bank-account / Library Books / Electrical Fitting name divergence | **Tier-2 fuzzy territory** — Week 4+ work. 6-8 of the 21 unmapped fall here. |
| 5 | Reparse-and-remap on every generation | **In flight — Week 4 Item 2.** Framing revised per mapper_design_notes §9.1: Mapping Decision DocType is the persistence layer; mapper persists on explicit `Run Mapper` trigger; generators pivot to read `final_*` over `proposed_*` (Commit 3). No `ParsedTallyTB` cache — full re-parse on explicit `Reset Parse`. |
| 6 | `Control Account Pattern` DocType | **Deferred** — 8 patterns are hardcoded in `tier1_supplier.py`. Week-4+ ops can edit via DocType instead of code. |
| 7 | Automated CSV-to-dux_voucher handoff | **Deferred intentionally** — matches "never auto-submit" principle. Reviewer downloads → attaches → invokes `import_from_csv` via JS button. Week 5+ could chain via API. |
| 8 | Cross-app state sync | **Out of scope** — no protocol defined. `rgi_migration` doesn't know if dux_voucher has imported the CSV. Reviewer tracks in dux_voucher's Batch. |

Withdrawn from earlier §9 list:
* **CACSPU `Stock Received But Not Billed` default** — verified correctly
  set; the Q5 Path B failure that flagged it was a false-alarm from a
  synthetic test fixture (withdrawal logged in design notes §10
  change log).

Applied during Week 3:
* **CACSPU `default_payable_account`** — changed from
  `Unsecured Loans Payable - CACSPU` to `Sundry Creditors - CACSPU` on
  2026-04-20 via bench console. Company configuration only; no code change.

---

## 7. How to resume in Week 4

Recipe for a new Claude Code session picking up Week 4:

1. Open a fresh Claude Code session in the rgi_migration worktree.
2. Read [`CLAUDE.md`](../CLAUDE.md) first.
3. Read [`docs/SESSION_HANDOFF_2026_04_19.md`](SESSION_HANDOFF_2026_04_19.md)
   for Week 1-3 context carry-over.
4. Read **this doc** for precise Week-3-close state + Week-4 starting point.
5. Run `git log --oneline week3-complete..HEAD` + `git status` to confirm
   current state matches this doc's assertions.
6. Summarize understanding back to Aditya.
7. **Spot-check question**: "What's the refusal count on CACSPU's main
   JE and why?" Correct answer: **21 unmapped** (post-Decision-1;
   pending_account_creation and group_refused collapsed into
   `excluded_zero_balance`). Separately, 18 `pending_supplier_creation`
   for gens #2/#3. Total Week-4 review surface: **39 decisions**.
8. Authorize Week 4 review UI work.

### Week 4 first-task prompt template

```
Week 4 starts. Generator layer is complete (see
docs/WEEK3_COMPLETE_2026_04_20.md). Propose a review UI scaffold:
prose design only, no code. Structure: one page per pending-bucket
(unmapped + pending_account_creation + pending_supplier_creation +
anti_pattern_blocked), reviewer actions that mutate Mapping Decisions
and optionally promote to new Mapping Rule / Supplier Alias Rule /
Account Creation Request / Supplier Creation Request.
```

---

## 8. Test counts and verification

| Layer | Count |
|---|---:|
| Total unit tests | **170** |
| Pre-existing skip (full-file balance assertion; opt-in via `RGI_FULL_XML_PATH`) | 1 |
| Integration smoke script | 1 (scripts/week3_integration_smoke.py) |

Per-generator breakdown:

| Generator | Tests |
|---|---:|
| Generator #1 Main Opening JE | 21 (test_generator_opening_je.py) |
| Generator #2 OIT CSV | 16 (test_generator_oit_csv.py) |
| Generator #3 Advance JE | 11 (test_generator_advance_je.py) |
| Generator #4 Students CSV | **15** (test_generator_students_csv.py) |

Other test modules (tier1 mapper, supplier source, resolve_exact_name,
bucketing, aggregate student parser, xml-vs-excel reference) cover the
infrastructure layer — see `rgi_migration/tests/` for the full list.

All test suites pass on `claude/unruffled-hellman-c54c95` HEAD.

Server bench (`erp.jewonline.in`) is in sync at HEAD — code pulled via
`git pull --ff-only` on `~/frappe-bench/apps/rgi_migration`.

GitHub push target: `origin/claude/unruffled-hellman-c54c95` + the
`week3-complete` tag.

---

## 9. Commit chain summary (week2-complete..week3-complete)

Roughly in order:

### Work Items 3-4 (Frappe app scaffold + DocTypes)

* `59859c6` feat: Frappe v16 app scaffold (bench new-app integration)
* `37a9a79` feat: Batch 1 DocTypes (Company Abbreviation + Mapping Rule Alternate Pattern)
* `7d25616` feat: Batch 2 DocTypes (Mapping Rule + Supplier Alias Rule)
* `615393e` feat: setup module for autonomous DocType creation
* `a194eb4` fix: drop 'import': 1 from default permissions
* `a368b60` feat: scaffold 5 DocTypes via setup module (phase 1 + phase 2)
* `9182539` fix: schema drift corrections from post-Batch-4 audit
* `062f5d5` / `a9a20c8` / `6567a74` fix: v16 schema-mutation recipes captured
* `a7bba93` docs: v16 schema mutation recipes captured from audit patch iteration

### Work Item 5 (seed 22 Mapping Rules)

* `ffdf70d` feat: Work Item 5 — idempotent real-seed + schema preflight
* `16b94ad` feat: Work Item 5 seed orchestration module
* `6fbd410` fix: seed_workflow sys.path uses one .parent not two

### Work Item 6 (supplier resolution)

* `be64356` feat: SupplierSource abstraction scaffold
* `fc60ffd` data: jewonline supplier master export
* `7cd5136` feat: Work Item 6 — Tier-1 supplier resolution for vendor party ledgers
* `8449ee5` fix: control account routing — rule-first ordering + pattern list
* `96aaaf1` docs: control-account routing principles + pattern list
* `03d5673` docs: fuzzy false-positive pattern from jewonline dev-bench baseline

### §4.10 pause → delete → parser-level routing

* `439346e` fix: pause sec 4.10 Student Fee Outstanding — leaf-only posting principle
* `ce4d3ce` fix: Student Fee Outstanding routes to dux_voucher via parser flag

### Session handoff + CLAUDE.md refresh

* `c04268a` docs: session handoff 2026-04-19 for new Claude Code session
* `3c4c3c7` docs: CLAUDE.md refresh reflecting Week 3 state

### Work Item 7 — the four generators

* `b8071f6` feat: Work Item 7 generator #1 — Main Opening JE (Draft)
* `66c585c` fix: Tally Migration Session autoname — {#####} counter syntax for v16
* `6606eb7` diag: analyze unmapped ledgers on CACSPU
* `c1497fe` diag: analyze_unmapped — move imports inside run() for IPython exec
* `cf23828` fix: exact-match resolver — case-insensitive + whitespace-tolerant
* `f56bd5d` feat: `excluded_zero_balance` tier — skip zero-balance ledgers at mapper boundary (**Decision 1**)
* `2696b1d` docs: generator conventions + Week-3 to Week-4 carry-over
* `79a8dae` feat: Work Item 7 Generator #2 — OIT CSV for net-Cr vendors
* `325825c` docs: withdraw sec 9 CACSPU SRBNB carry-over row — false alarm
* `25227d5` feat: Tally Migration Session — add generated_advance_je link + reference fields
* `e089f90` feat: Work Item 7 Generator #3 — Party-wise Dr JE for vendor advances
* `b6209bc` docs: Q7 CACSPU default_payable_account correction + gen #3 change-log entries
* `0f22b59` docs: dux_voucher integration spec — handoff format and architectural boundary
* `fe2004c` feat: Work Item 7 Generator #4 — Students CSV for dux_voucher handoff
* `00d5a13` test: generator #4 — explicit coverage for all-None tally_ids in remarks
* `412de4d` test: Week 3 integration smoke — all 4 generators end-to-end on CACSPU

40 commits from `week2-complete` to `week3-complete`.

---

## 10. Week 4 first-week scope (indicative, not binding)

Out of Week 3 scope, noted here for Week 4 planning continuity:

1. **Review UI scaffolding** — per-bucket reviewer pages, reviewer actions
   mutating Mapping Decisions
2. **Reviewer-promotion workflows** — confirm a fuzzy match → promote
   to `Supplier Alias Rule`; confirm an unmapped decision with new rule →
   promote to `Mapping Rule`; confirm account-creation proposal →
   create `Account Creation Request` → auto-create account
3. **Session persistence** — cache `ParsedTallyTB` + decisions on session
   after first parse; invalidate on `source_file_sha256` change (design
   notes §8.2). *Status 2026-04-22: framing revised in §9.1 — Mapping
   Decision DocType is the persistence layer (not a cache), populated
   by explicit `Run Mapper` trigger. In flight as Week 4 Item 2;
   Commit 1 (schema + doc) shipped.*
4. **Tier-2 fuzzy account matching** — rapidfuzz-based account resolution
   for the name-divergence cases (banks, library books, etc.)
5. **First CACSPU end-to-end submission test** — reviewer clears all
   39 decisions, all 4 generators succeed, reviewer submits all 4
   artefacts, Temporary Opening nets to zero across ERPNext + dux_voucher
6. **`fiscal_year_short` auto-populate hook** — small `before_insert` on
   the session controller

Then 58 more entities to migrate using the same workflow — real volume
testing of the 3-tier architecture.

---

*Written 2026-04-20 at commit `412de4d`, tagged `week3-complete`.*
