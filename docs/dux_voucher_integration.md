# dux_voucher integration — student-ledger handoff

**Target branch**: `dux_voucher` @ `feature/ex-student-module` (snapshot read on
2026-04-20; commit `72cdc4c` on erp.jewonline.in). This doc must be updated
in lockstep if that branch's CSV import contract changes.

**Scope**: this doc is the authoritative cross-app contract between
`rgi_migration` (producer) and `dux_voucher` (consumer) for migrating
student-opening-balance data out of Tally into ERPNext. Generator #4 in
`rgi_migration/generators/` is the producer; `Ex Student Opening Batch`
in dux_voucher is the consumer.

---

## 1. Context

Two sibling Frappe apps live on the same bench:

* **`rgi_migration`** — owns Tally-to-ERPNext opening-balance migration
  for *everything non-student*: chart of accounts, supplier payables and
  advances, P&L exclusions, cross-company postings, Temporary Opening
  balancer math.
* **`dux_voucher`** — owns the full ex-student lifecycle: `Ex Student`
  master, `Ex Student Opening Batch`, `Ex Student Receipt`,
  `Ex Student Writeoff`, `Ex Student Ledger Entry`, outstanding report.
  Student opening balances are one function of this app; it also runs
  ongoing receipting and write-offs after migration.

The boundary exists because student data has its own lifecycle after
migration (receipts, partial payments, write-offs, statement generation)
that is purpose-built into dux_voucher. Duplicating that workflow into
`rgi_migration` would strand student data in an app that has no
receipting or write-off semantics. Instead, `rgi_migration` performs
the one-time extraction and hands off to the app that owns the ongoing
workflow.

---

## 2. Architectural boundary

Per [`docs/mapper_design_notes.md`](mapper_design_notes.md) §7 (leaf-only
posting principle) and §9 (carry-over):

### `rgi_migration` owns:

* Parsing Tally XML/Excel (`rgi_migration/parsers/`)
* Flagging student ledgers via `is_student_ledger=True` — two routes
  (see §3 below)
* Routing flagged ledgers to a separate `tb.student_ledgers` list so
  they never reach the mapper
* Writing a CSV (generator #4) that matches dux_voucher's
  `import_from_csv` API contract

### `rgi_migration` does NOT:

* Create `Ex Student` master records — dux_voucher auto-creates on CSV
  import (see §4)
* Post any JE against student-receivable accounts — dux_voucher creates
  its own backend JE on Batch submission
* Validate that a student exists — dux_voucher handles at import time
* Know about the `Ex Student Ledger Entry` table or the recompute
  methods on `Ex Student`

### `dux_voucher` owns (for the handoff):

* Parsing the uploaded CSV via `import_from_csv`
* Upserting `Ex Student` records by `(student_name, company)`
* Populating `Ex Student Opening Batch.students_table` child rows
* Running Batch `validate()` + `on_submit()` to create the backend
  Opening Entry JE and `Ex Student Ledger Entry` rows
* Recomputing each student's `opening_balance` field post-submit

---

## 3. Student ledger identification

Two routes set `Ledger.is_student_ledger=True`. Both are parser-level
(`rgi_migration/parsers/tally_xml_parser.py`) — no Mapping Rules
involved.

### Route 1 — per-student leaves (Week 1)

A ledger's `parent_chain` contains any of a student-group marker set:
`STUDENT`, `STUDENTS`, `CYBERVIDYA`, `PASSOUT`, `GHRIMR STUDENT`, etc.
Matches the common Tally convention where each student has their own
ledger under a group named after the cohort or batch.

### Route 2 — aggregate control accounts (Week 3, commit `ce4d3ce`)

A ledger's cleaned name (suffix-stripped, whitespace-collapsed,
lowercased) is an exact match in
`AGGREGATE_STUDENT_ACCOUNT_NAMES` — a `frozenset` in
`rgi_migration/parsers/tally_xml_parser.py`. Currently contains
`"student fee outstanding"`. Adding aggregate names for future
entities is a code edit, not a Mapping Rule row.

Both routes produce the same output: the ledger is appended to
`ParsedTallyTB.student_ledgers` (not `ParsedTallyTB.ledgers`) and
carries `is_student_ledger=True`. See `docs/mapper_design_notes.md §7`
cross-app corollary.

---

## 4. CSV handoff format

### Producer contract (`rgi_migration` writes)

Generator #4 writes a UTF-8 CSV (no BOM, LF line endings, RFC 4180
quoting — same conventions as the OIT CSV per design notes §8.1)
matching the columns `dux_voucher.dux_voucher.api.ex_student_api.import_from_csv`
parses.

**Required columns** (header row, lowercased at import time):

| Column | Semantic | Notes |
|---|---|---|
| `student_name` | Student identifier as it appears in Tally ledger name | Used by dux_voucher as the upsert key against `Ex Student.student_name` within the target Company. If no matching record exists, dux_voucher creates one. |
| `debit_amount` | Opening balance — student owes institution | Must be `> 0` or `0`. Not both debit and credit non-zero on the same row (dux_voucher rejects). |
| `credit_amount` | Opening balance — institution owes student (advance received) | Same non-negative + mutual-exclusion rule. |

**Optional column** actively used in round-trip audit:

| Column | Semantic |
|---|---|
| `remarks` | Free text; rgi_migration writes Tally provenance here (ledger tally_id, parent chain, etc.) so the reviewer can trace each row back to its Tally source. |

**Optional columns** dux_voucher understands but `rgi_migration` does
NOT populate (no Tally-side source data): `father_name`, `course`,
`batch_year`, `admission_session`, `student_id`, `mobile`, `email`.
Left out of the CSV entirely; reviewers edit the `Ex Student` masters
after import if they want these fields.

**Column `amount`** — dux_voucher accepts this for backwards
compatibility (treated as `debit_amount` if no explicit debit/credit).
`rgi_migration` does NOT use it; always writes the explicit
`debit_amount`/`credit_amount` split.

### Per-student aggregation (critical)

Tally may emit multiple ledgers per student — one per fee bucket, one
per year, or one per course. The parser flags each as
`is_student_ledger=True` independently. But dux_voucher's
`Ex Student Opening Batch.validate()` **rejects duplicate
`ex_student` links** on Batch save:

> *"Row X: Ex Student Y is repeated. Combine into one row."*

Because `import_from_csv` upserts one `Ex Student` per unique
(`student_name`, `company`) and appends one child row per CSV row,
duplicate `student_name` in the CSV → duplicate `ex_student` on the
batch → validation failure.

**Therefore**: `rgi_migration` must aggregate multiple Tally ledgers
per student into a single CSV row **before writing**:

1. Group `Ledger` objects in `tb.student_ledgers` by cleaned
   `Ledger.name`.
2. Per group: `sum_dr = Σ opening_dr`, `sum_cr = Σ opening_cr`.
3. Net: `net_dr = sum_dr - sum_cr`. dux_voucher does not accept
   both-sided rows, so one side must be zeroed.
4. Emit:
   * `net_dr > 0` → `debit_amount=net_dr, credit_amount=0`
   * `net_dr < 0` → `debit_amount=0, credit_amount=abs(net_dr)`
   * `net_dr == 0` → **skip the row** (dux_voucher rejects rows with
     both `debit_amount=0` AND `credit_amount=0`)

Decision 1's zero-balance exclusion runs at the mapper level, but
student ledgers never reach the mapper — so Decision 1 does NOT apply
to `tb.student_ledgers`. Generator #4 must apply its own
zero-balance-per-student skip during aggregation.

### Filename convention

`students-{abbr}-{fiscal_year}-{YYYYMMDDHHMMSS}.csv` — same convention
as OIT CSV per design notes §8.4 (full `fiscal_year`, not
`fiscal_year_short`; trailing timestamp preserves File Manager audit
trail on regeneration). Attached to the Tally Migration Session via
the existing `student_ledger_file` Attach field.

---

## 5. Shared accounts

Both apps post against these accounts in the target ERPNext Company.
They must exist before either generator runs.

| Account | Owner | Role |
|---|---|---|
| `Temporary Opening - {ABBR}` (Equity, Temporary, leaf) | Shared | Balancer on all 4 generators' output. Main JE + OIT + Party-Dr JE + dux_voucher's Ex Student Opening JE all post here; net to zero once all four submit. |
| `Ex-Students Receivable - {ABBR}` (Asset, Receivable, leaf) | dux_voucher | Student receivable side of dux_voucher's backend JE. `rgi_migration` never writes to it directly. |

`dux_voucher.dux_voucher.api.utils._get_ex_student_accounts()` hardcodes
both names by company-abbr convention. If either is missing on the
target Company's COA, `dux_voucher` throws at Batch save:

> *"Missing account(s) in Chart of Accounts for {company}: ... . Please
> create these accounts before posting Ex Student opening balances."*

For CACSPU, both accounts were verified present during Week-3
pre-flight (Aditya's pre-flight report 2026-04-20).

---

## 6. Workflow

End-to-end, once per migrated entity:

1. Reviewer runs the full rgi_migration pipeline against a Tally
   Migration Session (parse, map, generate).
2. Parser flags student ledgers via `is_student_ledger=True`
   (§3 above).
3. Mapper skips `tb.student_ledgers`; they never reach Mapping Rule
   resolution.
4. Generator #4 per-student aggregates and writes
   `students-{abbr}-...csv`; attaches to
   `session.student_ledger_file`.
5. Reviewer downloads the CSV from the Session attachment.
6. Reviewer creates a new `Ex Student Opening Batch` in the ERPNext
   Desk, sets `company` + `posting_date`.
7. Reviewer attaches the CSV to the Batch (Frappe's Attach mechanism
   on the Batch doc).
8. Reviewer clicks the "Import from CSV" button in the Batch JS, which
   calls `dux_voucher.dux_voucher.api.ex_student_api.import_from_csv(batch_name, file_url)`.
9. `import_from_csv` parses the CSV, upserts `Ex Student` masters per
   row (auto-creating where needed), appends child rows to
   `batch.students_table`, saves the batch as Draft. Returns a summary
   `{created, appended, errors}` for the JS to display.
10. Reviewer reviews the populated batch in Desk, fixes any error rows
    flagged by the summary.
11. Reviewer clicks Submit. `validate()` runs (duplicate-student check,
    non-negative, mutual-exclusion of Dr/Cr); `on_submit()` creates
    the backend Opening Entry JE (Dr `Ex-Students Receivable` / Cr
    `Temporary Opening` for net-receivable; reversed for net-credit)
    and `Ex Student Ledger Entry` rows per student.
12. Once `rgi_migration`'s 3 JEs + 1 OIT CSV import and dux_voucher's
    Ex Student JE all submit, the shared `Temporary Opening - {ABBR}`
    nets to zero.

---

## 7. Prerequisites

Before running generator #4, verify:

1. Target ERPNext Company exists with COA imported (same as gens #1-3).
2. `Temporary Opening - {ABBR}` exists as Equity leaf, account_type
   `Temporary`. Verified for CACSPU — `parent=Equity - CACSPU`.
3. `Ex-Students Receivable - {ABBR}` exists as Asset leaf, account_type
   `Receivable` preferred (but `_get_ex_student_accounts` doesn't check
   the type — just existence). Verified for CACSPU during pre-flight.
4. `dux_voucher` app is installed on the bench and on
   `feature/ex-student-module` or its successor branch.
5. `Ex Student` master may be empty on first run — dux_voucher's
   `import_from_csv` auto-creates per row. Reviewer should NOT
   pre-populate Ex Student masters from elsewhere unless they want to
   avoid the auto-create path (the upsert keys on
   `(student_name, company)`, so pre-existing records with matching
   names will be reused).

Ex Student Opening Batch uses naming series `ESO-.YYYY.-` →
`ESO-2026-00001`, etc.

---

## 8. Error handling

### Producer side (`rgi_migration` generator #4)

* **Zero-balance student after aggregation**: skipped silently. Does
  not count as a failure.
* **Both-sided per-ledger ledger** (`opening_dr > 0` and
  `opening_cr > 0` on a single Tally ledger): summed into the student's
  totals and netted normally. Aggregation produces a single-sided
  output row.
* **Malformed ledger** (e.g. missing `name`): parser-side diagnostic,
  captured in `ParsedTallyTB.parse_warnings` during parse. Generator
  #4 does not re-validate — trusts the parser.
* **Duplicate `student_name` within `tb.student_ledgers`**: aggregated
  by per-group summing (§4 above); never emitted as duplicate rows.

### Consumer side (`dux_voucher` import + submit)

Surfaced to the reviewer through the JS summary + batch validation:

* **Row missing `student_name`**: `import_from_csv` records an error,
  skips the row, reports in summary. Batch still saves with the rows
  that succeeded.
* **Row with both debit and credit > 0**: same — error recorded, row
  skipped.
* **Row with debit == 0 and credit == 0**: same.
* **Unknown student** (`student_name` not in Ex Student master for
  this Company): **auto-created** via `_get_or_create_ex_student` on
  the fly. Not an error.
* **Duplicate `ex_student`** on batch save (two rows resolving to the
  same Ex Student doc after upsert): Batch `validate()` throws. Happens
  only if CSV has two rows with identical `student_name` — which
  generator #4 prevents by aggregating.
* **Missing shared accounts** (`Temporary Opening - {ABBR}` or
  `Ex-Students Receivable - {ABBR}`): Batch `validate()` throws.
* **Cross-company row**: if an Ex Student resolved by the upsert
  belongs to a different Company than the Batch's, Batch `validate()`
  throws. Not expected in the migration path — dux_voucher matches
  `(student_name, company)` so it would upsert into the Batch's own
  company by default.

### Reconciliation across apps

After Batch submit, reviewer cross-checks:

* Sum of `students_table.debit_amount` − `students_table.credit_amount`
  on the Batch equals what generator #4 produced in the CSV.
* Ex Student JE's Temporary Opening leg matches the difference (JE is
  skipped entirely if gross-zero — check `Ex Student Ledger Entry`
  rows instead for zero-net-nonzero-row cases).

No automated cross-app reconciliation — this is a manual reviewer
step.

---

## 9. Limitations / known gaps

1. **No automated CSV handoff.** Reviewer downloads from Session, uploads
   to Batch. Two manual UI steps. Not a defect — matches the
   "never auto-submit" principle. Future optimisation could attach the
   CSV directly via Frappe API + invoke `import_from_csv` in one call,
   but out of scope for Week 3.
2. **No cross-app state sync.** `rgi_migration` does not know whether
   `dux_voucher` has successfully imported or submitted the batch for a
   given session. The Session's `student_ledger_file` attachment is
   the only on-rgi_migration record that the CSV was produced; the
   reviewer tracks import status in dux_voucher's Batch.
3. **No `student_id` or external-ID carry-through.** Tally's
   `tally_id` numeric suffix (stripped from `Ledger.name` into
   `Ledger.tally_id`) is preserved in the per-row `remarks` for audit
   only. If a future workflow needs Tally's ID as a lookup key on the
   Ex Student record itself, the CSV schema will need a new column
   mapped to the `student_id` optional input on
   `_get_or_create_ex_student`.
4. **Student-name collisions across Tally ledgers** — if two different
   students in Tally happen to have the same cleaned `student_name`,
   the aggregator collapses them into one CSV row. Mitigation: the
   parser's `(name, tally_id)` identity is preserved in the
   `Ledger` dataclass; if generator #4 encounters duplicate
   `student_name` with distinct `tally_id`, it emits a
   `parse_warnings`-equivalent and the reviewer must resolve manually
   (splitting or renaming in Tally, or matching on a stronger key).
   To be formalised in generator #4's prose design.
5. **`dux_voucher`'s `import_from_csv` has no `force=True` flag.**
   Re-running against the same batch appends more rows (doesn't
   replace). Regeneration on the `rgi_migration` side deletes and
   re-attaches the File, but the reviewer is responsible for clearing
   Batch rows before re-importing if they're iterating.
6. **Excel format not supported** — dux_voucher's importer accepts
   CSV/TSV (delimiter-sniffed). Generator #4 writes CSV only. Future
   multi-format support would require dux_voucher-side changes.

---

## 10. Version boundary

This spec targets `dux_voucher` on branch `feature/ex-student-module`
at commit `72cdc4c` (2026-04-20). Relevant consumer surface:

| File | Purpose | Relevant on |
|---|---|---|
| `dux_voucher/dux_voucher/doctype/ex_student_opening_batch/ex_student_opening_batch.json` | Parent DocType | Fields we assume: `company`, `posting_date`, `students_table`, `remarks`, `is_posted`, `backend_je` |
| `dux_voucher/dux_voucher/doctype/ex_student_opening_batch/ex_student_opening_batch.py` | Validation + submit logic | `validate()` duplicate check, `on_submit()` JE creation |
| `dux_voucher/dux_voucher/doctype/ex_student_opening_row/ex_student_opening_row.json` | Child row schema | Fields: `ex_student` (Link), `student_name` (fetch), `debit_amount`, `credit_amount`, `remarks` |
| `dux_voucher/dux_voucher/api/ex_student_api.py` | `import_from_csv` CSV parser | Column names, upsert semantics, error reporting |
| `dux_voucher/dux_voucher/api/utils.py` `_get_ex_student_accounts` | Hardcoded account name convention | `Ex-Students Receivable - {ABBR}` + `Temporary Opening - {ABBR}` |

If any of the above changes on dux_voucher's side, this doc must be
updated in lockstep and generator #4's CSV writer re-verified.
Particularly sensitive surfaces: the `import_from_csv` column vocabulary
(currently case-insensitive + order-flexible but name-sensitive), and
the Opening Row's required `ex_student` Link field (changing from Link
to Data would break the upsert path).

The CSV format version implicitly tracks dux_voucher's branch — we do
not emit a version header in the CSV itself. If formats diverge in the
future, adding a `# version: 1` comment line at the top of the CSV is
the cheapest forward-compatibility mechanism; dux_voucher's current
parser ignores blank lines but not comments, so that change would need
to land in dux_voucher first.
