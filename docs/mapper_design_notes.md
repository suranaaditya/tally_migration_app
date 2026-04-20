# Mapper Design Notes

Canonical principles for the Tier-1 mapper and the Mapping Rule library.
Small document by design — add only decisions that survive a debugging cycle
or generalize beyond a single rule.

---

## 1. Anti-pattern seeding test

Before seeding any anti-pattern as a `Mapping Rule` row, ask:

> **Could the mapper propose the forbidden target on its own?**

If the answer is **no** — because the parser excludes the source ledger, the
JE builder filters it downstream, ERPNext rejects it at submission, or no ERP
COA entry with that name exists — the anti-pattern is noise. Do not seed.

Defensive rules that guard against impossible failure modes add maintenance
cost, clutter review UIs, and — worst — make the real anti-patterns harder
to notice among the noise. A closely-related failure: seeding as data what
is really a *structural* invariant belonging in Tier-1 code (see §2).

This test applies to every future anti-pattern added during reviewer
promotion, not just to the initial §11 seed pass.

### Worked example A — `Purchase Accounts` is NOT seeded (impossible failure mode)

An early seed plan included "refuse `Purchase Accounts` as an ERP asset
target" as a §11 anti-pattern. Removed on the test above:

1. The parser tags every ledger under `Purchase Accounts` with
   `root_type="Expense"` and `is_pnl_closed_zero=True`.
2. The Week-3 JE builder filters out every ledger with
   `root_type ∈ {Income, Expense}` before composing the JE.
3. ERPNext's `Opening Entry` journal type hard-rejects P&L accounts at
   submission anyway.

Given (1)–(3), Tier-1 cannot propose a P&L target. The anti-pattern would
guard a path that cannot be walked. Dead code.

### Worked example B — `FDR Canara Bank` group is NOT seeded (structural check)

An early seed plan included §11 R10 ("Use `FDR Canara Bank` group for FDR
posting") as an anti-pattern. Removed for a different reason than Example A:
the failure mode IS reachable — exact-name match would happily propose a
group account — but the correct fix is structural, not data.

A data rule can only name *one* group account per row. The real concern is
"the mapper must never propose ANY group account, regardless of name match."
That's a single code-level invariant (§2(b)) that catches every group, not
just the one §11 R10 happened to mention.

Seeding R10 as data would be both narrowly scoped and redundant with the
structural check. Cut.

### The three §11 rows that DO survive the test

§11 rows 2, 3, 4 each pin a specific Tally→ERP name pair where the ERP COA
entry is miscategorised. They are NOT "refuse P&L" or "refuse groups"
rules — they are **"refuse exact-name match onto a wrongly-classified
account that the ERP COA happens to contain"** rules:

| §11 row | Tally pattern | Forbidden ERP target | Concrete failure mode prevented |
|---|---|---|---|
| R2 | `Summer Term Exam Fee Collection` | `Summer Term Exam Fee Collection - {ABBR}` (Indirect Incomes) | Exact-name match steers a BS liability onto a P&L income account. |
| R3 | `Hostel Fee A/c` | `Hostel Fee A/c - {ABBR}` (Direct Incomes) | Same shape — ERP COA places a same-name account under P&L while Tally source is BS. |
| R4 | `Caution Money £` | `Caution Money £ - {ABBR}` (Loans & Advances, Asset) | Exact-name match steers a student-deposit liability onto an Asset COA entry. Wrong BS side. |

These are data because the ERP COA is objectively wrong for that specific
named account — the anti-pattern is the bridge until the COA is cleaned up
(which may never happen; these miscategorisations are legacy across all 59
entities). The mapper is right to propose the exact-name match based on the
name alone.

---

## 2. Tier-1 structural checks

Checks run by Tier-1 code before a `Mapping Decision` is emitted. These are
code-level invariants, not data-driven rules — they don't live in the
`Mapping Rule` DocType because they apply regardless of rule content.

### (a) P&L root-type exclusion

Ledgers with `root_type ∈ {Income, Expense}` never receive a proposal. The
parser tags these during parse; the mapper skips them with
`review_action = "Excluded (P&L)"` and `excluded_reason = "Income/Expense
root type; handled by current-year P&L transfer, not opening JE"`.

Belt-and-braces downstream: ERPNext's `Opening Entry` journal type rejects
P&L accounts at submission (structural hard constraint), and the Week-3 JE
builder filters these out before composing the JE. Three layers of defense
mean the Tier-1 exclusion is diagnostic, not load-bearing.

Diagnostic: `Session.pnl_excluded_count`.

### (b) Group-account refusal

After Tier-1 resolves a proposed ERPNext account for a ledger — whether via
exact-name match, saved Mapping Rule, or canonical pattern — and **before**
emitting the `Mapping Decision`, the mapper checks `Account.is_group` on
the resolved name. If `is_group == 1`:

- Do not emit with the group as `proposed_account`.
- Emit the `Mapping Decision` with `review_action = "Pending Group Account
  Resolution"`, `proposed_account = NULL`, and `excluded_reason` populated
  with the group's resolved name and the resolution path ("reviewer selects
  a leaf under this group, or overrides with a different mapping").
- Increment `Session.group_account_refused_count`.

Why structural, not data: ERPNext rejects posting to group accounts at
submission regardless of name. No `Mapping Rule` row could make a group
proposal legitimate. The check belongs in code. §1 Worked Example B
expands on why seeding this as data was the wrong choice.

---

## 3. Account creation policy

**The mapper never creates `Account` records autonomously.** Every proposed
account creation routes through an `Account Creation Request` child row on
the session, pending reviewer approval and an explicit "Create Now" click.

Rationale:
- Protects the ERPNext COA from silent pollution during parse/map retries,
  failed runs, or re-seeds.
- Makes every account creation a first-class, traceable, reviewable event
  scoped to a migration session — auditable.
- Keeps Tier-1 deterministic: it proposes, reviewer disposes.

### Scope — three categories, one workflow

All three routes produce the same `Account Creation Request` +
`Mapping Decision.review_action = "Pending Account Creation"` shape. The
reviewer approves or skips; the mapper does not distinguish between the
three routes operationally.

1. **COA wrongly classified.** ERP has the same-name account under P&L;
   Tally source is a BS liability. Example: `Hostel Fee A/c` (§4.8 + §11
   R3), `Summer Term Exam Fee Collection` (§4.9 + §11 R2). Paired §11
   anti-pattern refuses the same-name match.
2. **COA under wrong-side BS.** ERP has the account on the opposite BS
   side. Example: `Student Fee Outstanding` under Liabilities when §4.10
   says it should be a Current Asset.
3. **Account absent entirely.** Typical — every entity has 5–15 Tally
   ledgers with no ERP target at all on first run.

### Which rows carry `creates_erpnext_account=1`

- **Anti-patterns that steer to a non-existent alternative** (§11 R2, R3)
  own the creation request. Full new-account fields populated.
- **Anti-patterns that steer to an existing alternative** (§11 R4 → the
  existing `Caution Money £ (Liability)`) carry
  `creates_erpnext_account=0`. Pure steers.
- **Paired §4 positive rules** for COA-miscategorised accounts (§4.8,
  §4.9) carry `creates_erpnext_account=0`. Their paired anti-pattern
  owns creation; the positive rule is the documented mapping. Avoids
  double-emission on the same Tally ledger.
- **Standalone positive rules with possible creation** (§4.10, §4.12,
  §4.17 — rule body explicitly says "new account"):
  `creates_erpnext_account=1`, **conditional**.

### Conditional creation on positive rules

`creates_erpnext_account=1` on a positive rule is a conditional signal, not
an unconditional trigger. At map time, Tier-1 resolves the
`erpnext_account_template` against `{ABBR}` and checks whether the
account exists in the target company's COA:

- Exists → propose it directly; no creation request.
- Doesn't exist → propose it AND emit an `Account Creation Request` with
  the rule's `new_account_parent`, `new_account_root_type`,
  `new_account_is_group`. Decision goes to `Pending Account Creation`.

Same seeded rule handles both the common case (account already in COA for
established entities) and the new-entity case (first run on a new company).

### Request lifecycle

```
Pending  →  Created  (reviewer clicks "Create Now"; Account insert succeeds)
         →  Skipped  (reviewer clicks "Skip" — all referenced decisions
                      go to review_action=Deferred with excluded_reason=
                      "Account creation declined by reviewer")
         →  Failed   (Account insert raised — error_log populated, decisions
                      stay Pending Account Creation, reviewer can retry)
```

### Deduplication and audit

Session-level dedupe on resolved `proposed_account_name` (post-`{ABBR}`
substitution). If two different rules target the same new account (e.g.
a §4 positive + a §11 anti-pattern both point at `Hostel Fee Advance
Payable - CACSPU`), one `Account Creation Request` row is created.

**Audit trail** for which rules contributed: the request carries a child
table `source_rules` (type `Account Creation Source`, one field `rule`
Link → Mapping Rule) — one row per contributing `Mapping Rule`. The
review UI surfaces all of them so reviewers see every rule that
converged on this creation. The original `Mapping Decision`s append
their `idx` to the request's `source_decisions` CSV.

---

## 4. Known data quirks

Oddities in the source data that are easy to misread as bugs but are in
fact legitimate. Future code must not "fix" them.

### Tally internal group/ledger names are independent of ERPNext abbreviations

The CACSPU Tally company data contains a Tally-side group literally named
`GHRCACS` (plus a child group `GHRCACS-Graduate`) — visible as
`<GROUP NAME="GHRCACS">` ~744 times in
`sample_cacspu_masters_sample.xml`. This is historical labelling chosen by
the original bookkeeper; it is **Tally source data**, not tied to the
ERPNext abbreviation (`CACSPU` per RGI_Migration_Rules.md §1.2 #20).

**Do not sanitize Tally-side names to match ERP conventions.** When a
parser, mapper, or diagnostic touches Tally ledger/group names, it treats
them as opaque source strings. The only name mutation we ever do is the
`-{digits}` suffix strip documented in CLAUDE.md (e.g.
`Income Expenditure A/c-2127` → `Income Expenditure A/c`), which is a
Tally-side data-hygiene step, not an ERP-alignment step.

Concretely, Tier-1 will encounter group/ledger names like `GHRCACS` or
`GHRCACS-Graduate` during mapping — they will not match any seeded
`Mapping Rule` (rules are keyed on known Tally ledger names from §4) and
will flow to `review_action="Pending"` as unmapped. That is correct
behaviour; the reviewer decides.

### P&L A/c routing — combine with I&E reserve at migration time

Tally distinguishes two accounts that ERPNext does not:

- **`Profit & Loss A/c`** — a Tally system accumulator holding current
  FY (2025-26) net surplus. Carries no `<PARENT>` tag in the XML export
  and is flagged `is_system_account=True` by the parser.
- **`Income Expenditure A/c`** — a user-created reserve holding
  accumulated prior-year surpluses. Ordinary Equity ledger, under
  `Reserves & Surplus` in Tally.

In Tally these are separate because Tally's year-end close moves the P&L
A/c balance into I&E at fiscal-year rollover. ERPNext has no equivalent
permanent ledger — it handles retained earnings via its own period-close
mechanism, not as a user-facing chart account. **At migration time, both
Tally accounts collapse to a single ERPNext account:** `Income
Expenditure A/c - {ABBR}`, with amounts combined.

This is encoded as a single seeded rule (`source_section = "§3.3 +
derived"`) with `tally_pattern="Profit & Loss A/c"`, one alternate
`"Income Expenditure A/c"`, `combine_amounts=1`, target template
`"Income Expenditure A/c - {ABBR}"`.

**Principle for future entities.** Different entities carry different
shapes of retained earnings — a pure-deficit entity with no reserve, an
entity with multiple named reserves, or an entity mid-way through an
unbooked close. The principle the rule encodes is: *ERPNext's retained-
earnings model uses one net account; collapse Tally's dual accumulators
into that one account at migration time.* When an entity's shape doesn't
match §4.12's two-account pattern (e.g. deficit entities where
I&E is Dr and there's no P&L carry), the reviewer promotes a per-entity
variant of this rule rather than trying to match this one structurally.

---

## 5. Schema mutation recipes (Frappe v16 / erp.jewonline.in)

Operational notes captured from the Week-3 DocType audit iteration.
Recording here so the next schema change doesn't rediscover these
painfully. All recipes tested on the bench at `erp.jewonline.in`
(Frappe 16.12 / ERPNext 16.10).

### DocField rename (fieldname change)

Modify the DocType.fields list in place, then `save()`:

```python
dt = frappe.get_doc("DocType", "<Some DocType>")
for f in dt.fields:
    if f.fieldname == "old_name":
        f.fieldname = "new_name"
dt.save(ignore_permissions=True)
```

`save()` adds the new column to `tab<DocType>`. It does **not** drop the
old column — the old column becomes an orphan and has to be cleaned up
separately (next recipe). On a table with existing data, this means
the data lands only in the NEW column; the old column keeps its old
values until dropped. Plan migration accordingly.

### Orphan SQL column cleanup

Raw DDL (`ALTER TABLE … DROP COLUMN`) inside a transaction with pending
writes raises `ImplicitCommitError` in v16. Flank the DDL with explicit
commits:

```python
frappe.db.commit()                        # flush pending transaction writes
frappe.db.sql(
    "ALTER TABLE `tab<DocType>` DROP COLUMN `orphan_column_name`"
)
frappe.db.commit()
```

Same pattern for any DDL: `ADD COLUMN`, `CHANGE COLUMN`, etc.

### `idx` is NOT an orphan to drop when flipping `istable` from 1 → 0

Despite being a child-table column in some readings, `idx` is
**framework-universal** — Frappe's generic `INSERT INTO` statement
includes `idx` as a column on every DocType, child or standalone. It
backs the default-ordering semantics (`ORDER BY idx` in Desk list
queries) and isn't exposed as a declared field in the DocType JSON;
it's auto-added by Frappe at table-creation time.

Dropping `idx` from a standalone DocType's SQL table breaks every
subsequent insert with:

```
OperationalError: (1054, "Unknown column 'idx' in 'INSERT INTO'")
```

The orphan-drop list after an `istable = 1 → 0` flip is strictly
the three child-only columns:

- `parent`
- `parenttype`
- `parentfield`

`idx` stays. If you already dropped it, restore with
`ALTER TABLE \`tab<DocType>\` ADD COLUMN \`idx\` INT NOT NULL DEFAULT 0`
(commit-flanked per the recipe above).

Recorded after Week 4 Item 1 Commit 1 mis-dropped `idx` during the
Mapping Decision istable flip and had to restore it in the same
session.

### `autoname` mode string requires a trailing colon for naming_series

Frappe's naming dispatcher
(`apps/frappe/frappe/model/naming.py:226` in v16.12) pattern-matches
`autoname` string prefixes:

```python
if _autoname.startswith("field:"):
    ...
elif _autoname.startswith("naming_series:"):   # ← note trailing colon
    ...
elif _autoname.startswith("prompt"):
    ...
elif _autoname.startswith("format:"):
    ...
```

Setting `DocType.autoname = "naming_series"` (no colon) fails every
branch and falls through to the hash-name fallback — docs insert with
10-character random names like `3sj54k3dp0` even when the
`naming_series` field is populated with the correct pattern
(`MD-.YYYY.-.#####` in our case) and the field default is set.

Canonical `autoname` values:

| Autoname mode | Correct string | Effect |
|---|---|---|
| naming_series-driven | `"naming_series:"` | reads the `naming_series` field on each doc and applies the pattern |
| field-driven | `"field:<fieldname>"` | uses the literal value of `<fieldname>` as the doc name |
| format-driven | `"format:<pattern>"` | uses a computed pattern against the doc |
| user-prompted | `"prompt"` | UI prompts for name at insert |
| random hash | empty or `"hash"` | 10-char random name fallback |

Also recorded after Week 4 Item 1 Commit 1 — the trailing-colon
requirement isn't obvious from the user-facing DocType form, and I
missed it in the schema-flip script. Fix: single `dt.save()` with
the colon appended, no DDL needed.

### Frappe Page name length limit — 20 characters at runtime insert

Frappe's Page controller (`frappe/core/doctype/page/page.py:39-49`)
hardcodes `[:20]` truncation in `autoname()`, despite the docstring
claiming 30 characters. Pages created via
`frappe.get_doc({"doctype": "Page", ...}).insert()` have their names
silently truncated at 20 characters.

Passing `name` explicitly alongside `page_name` does NOT bypass the
truncation — the condition in `autoname()` still triggers during the
insert pipeline.

Pages with longer names on the bench (e.g. 26-char
`warehouse-capacity-summary`) reached their full length via
fixture-loading (`bench migrate` reading a pre-existing `.json`),
which bypasses runtime `autoname()` entirely.

**Practical guidance for this project**:

- Keep Page names ≤ 20 characters, chosen intentionally.
- Prefer short mnemonics matching existing naming conventions
  (e.g. `md-review` matches the `MD-YYYY-NNNNN` `naming_series`
  prefix for Mapping Decision records — the "MD" appears in both
  the page route and the record names).
- Don't rely on `rename_doc` workarounds — they introduce hidden
  coupling on fresh-bench deploys (the rename has to run every
  time someone installs the app on a new bench).
- CSS class names and other internal identifiers can retain a
  descriptive longer form (e.g. `.mapping-decision-review-app`) —
  they're not constrained by the 20-char cap and they describe
  the component's purpose rather than the URL identifier.

Recorded after Week 4 Item 1 Commit 2 attempted to create
`mapping-decision-review` (23 chars) and got silently truncated to
`mapping-decision-rev` twice (once with explicit `name`, once
without). Final landing: `md-review` (9 chars). Two-attempt sequence
+ investigation ≈ 45 min; a single sentence in this guardrail would
have saved that time.

### Pre-existing bench Socket.IO 404s on all Desk pages

As of 2026-04-20, the `erp.jewonline.in` bench emits Socket.IO
connection errors on every Desk page load:

```
GET /socket.io/?EIO=4&transport=polling  → 404 (Not Found)
Error connecting to socket.io: xhr poll error
```

This affects real-time features (live comment pushes, assignment
notifications, form-change broadcasts) but NOT normal page
rendering or data operations. Our Week 4 review page uses
`frappe.call` + `frappe.db.get_doc` exclusively (standard XHR),
which is unaffected.

Fix deferred to bench-ops work (likely requires `bench setup
socketio` + `bench setup supervisor` reconfiguration on the
Ubuntu host, or a `supervisorctl restart` of the frappe-web /
frappe-socketio processes). Logged here rather than in the Week 4
design doc because it's infrastructure state, not design.

If browser verification of any custom Desk page surfaces only
these Socket.IO errors + no other errors, treat as green.

### Section Break / Data field fieldname collision

When two fields on the same DocType both claim the same fieldname (e.g.
a Section Break at `source_section` and a Data field also wanting
`source_section`), Frappe silently renames the later one with a suffix
(we saw `_ref` appended). Rename the Section Break out of the way
*first*, save, then rename the Data field in. Two sequential saves.

### APIs that are **not** available in v16

- `frappe.model.rename_doc.rename_field` — removed. Use
  DocType.fields-in-place edit + `save()` instead.
- `frappe.db.rename_field` — does not exist on this bench.
- Raw `ALTER CHANGE COLUMN` inside a transaction — blocked by
  `ImplicitCommitError`; use commit-flanked DDL.

### Execution surface — prefer bench console heredoc

```bash
bench --site erp.jewonline.in console <<EOF
import frappe
# ... your patch code ...
EOF
```

`bench console` with a Python heredoc is the most reliable execution
surface on this bench. Clear output, full interactive-Python error
messages, reliable module imports.

**Avoid** `bench --site X execute <dotted.path.to.func>` for anything
non-trivial. It has a fallback-to-`eval()` path (see
`frappe/commands/utils.py:288`) that triggers a spurious
`NameError: name '<app>' is not defined` when the primary
`frappe.get_attr()` import fails for any reason. Seen on this bench
with the setup module's `run_audit_patches` function despite the
module importing cleanly via `python -c`. Reproduced and worked
around; kept as a known quirk rather than chased to root cause.

### Testing protocol for schema patches

1. **Probe** current DocField metadata and SQL column state via
   `bench --site X console` heredoc before writing the patch.
2. **Idempotent patch** — always check-before-change (`frappe.db.exists`,
   `frappe.db.has_column`, existing-fieldname lookups).
3. **Run patch** via console heredoc. Re-probe. Confirm.
4. **`bench migrate`**. No rgi_migration-related errors.
5. **Bridge-commit** the generated JSON changes from server to GitHub
   via the local-as-bridge pattern
   (`git fetch frappe@…:frappe-bench/apps/rgi_migration …`, then
   `git push origin`).

---

## 6. Supplier matching (Tier-1)

Operational notes on vendor/party-ledger routing and supplier resolution.
Applies to any ledger whose Tally parent chain indicates Sundry-Creditors
lineage, subject to the exclusions in §6.2 and §6.3 below.

### 6.1 Rule-first ordering

**Principle.** Positive §4 rules are the primary classification signal.
Parent-chain-based routing (like party-ledger detection) is a **fallback**
for unclassified ledgers, never a pre-empt of rule matching.

`Mapper.resolve()` evaluates positive rules **before** the party branch.
If any positive rule fires, the rule's account proposal wins — regardless
of parent chain. The party branch is only reached for ledgers that no
rule matched.

**Why it matters.** §4 includes rules whose targets live under Sundry
Creditors in Tally: §4.6 `Unpaid Expenditure Account → Unpaid Expenditure
Provision - {ABBR}`, §4.7 `Payable A/c → Payable Account - {ABBR}`.
Without rule-first ordering, the party branch short-circuits these
rules to `pending_supplier_creation` and the Work-Item-3 JE builder
emits incorrect output (account-level balance attempted as party-wise
JE with no matching supplier).

**Regression finding (Week 3, Work Item 6).** The initial Work Item 6
implementation ran the party branch before rule matching. Between the
Week-2 baseline and the initial Work Item 6 run on the full 221 MB
CACSPU export, `tier1_rule` dropped from 11 to 10 — exactly the §4.6
Unpaid Expenditure Account match being diverted. Caught at prose-review
time before the fix shipped; corrected in commit `8449ee5` by swapping
the party branch to run AFTER the rule lookup.

### 6.2 Party-ledger detection

A ledger is treated as a Sundry-Creditors vendor (and routed through
supplier resolution) when **both** conditions hold:

1. Its parent chain (case-insensitive substring match) contains
   `sundry creditors`.
2. Its cleaned name does NOT match any control-account pattern (§6.3).

Sundry **Debtors** descendants are NOT routed here — those are students
on RGI entities, handled by the dux_voucher ex-student workflow per
`docs/dux_voucher_integration.md`.

### 6.3 Control-account pattern list

Names matching any of these 8 hardcoded patterns stay in the main JE
flow (account-level balances), even when they live under Sundry
Creditors. They are pseudo-accounts for Tally bookkeeping mechanics,
not vendors, and should never produce a Supplier Creation Request.

All patterns are case-insensitive and anchored (`^`) at the start of
the cleaned ledger name. Patterns live in
`rgi_migration/mapper/tier1_supplier.py`:

| Pattern | Rationale | Example matches |
|---|---|---|
| `^Advances?\s+Received\b` | Accrual bucket under Sundry Creditors; no vendor counterparty | `Advances Received For Expenses`, `Advance Received` |
| `^TDS\s+Payable\b` | Statutory liability; Government is not a Supplier master entry | `TDS Payable 194C`, `TDS Payable On Rent` |
| `^(GST\s+)?Tax\s+Collected\b` | GST output liability; anchored to avoid matching vendor names containing "Tax" | `Tax Collected at Source`, `GST Tax Collected` |
| `^Provision\s+for\b` | Period-end accrual, not vendor-linked | `Provision for Expenses`, `Provision for Audit Fees` |
| `^Suspense\s+A/c\b` | Clearing account; slash required to avoid ambiguity with `Ac` as a prefix | `Suspense A/c` |
| `^Unadjusted\b` | Reconciliation-pending items, not vendor obligations | `Unadjusted Receipts`, `Unadjusted Advance from Customer` |
| `^GST\s+(Payable\|Input\|Output)\b` | GST control accounts common in Indian ERP Tally setups | `GST Payable`, `GST Input`, `GST Output` |
| `^Round(ing)?\s+off\b` | Mechanical rounding pseudo-account (often classified P&L anyway, but defend-in-depth) | `Round off`, `Rounding off` |

**Observed on full 221 MB CACSPU export (post-fix verification):** 5
ledgers matched control patterns. Two of those were previously in the
party branch; the rest were already excluded by the P&L validator or
sat outside Sundry Creditors. Party routing dropped from 345 to 343.

**Adding patterns.** For Week 3 scope, patterns are hardcoded here and
edited via a code commit. See §6.4 for deferred work.

### 6.4 Fuzzy false-positive pattern against sparse supplier masters

**Principle.** Dev-bench testing with a small, generic supplier master
will produce fuzzy-layer false positives. This is expected mechanism
behaviour, not a resolver bug. Against production-scale supplier
masters (hundreds of longer, more distinctive names), coincidental
prefix/token matches are outscored by the real match.

**The rule**: threshold 85% stays as designed. Do not tighten based on
dev-bench results. If future dev-bench testing consistently produces
false positives, the right response is to **seed negative alias rules**
(status=`paused` with a rejection reason via the `Supplier Alias Rule`
table's Layer 2 workflow, once reviewer promotion is live in Week 4+),
not to adjust the fuzzy threshold globally.

**Why**: tightening the threshold hides legitimate near-miss matches on
the eventual real supplier masters. Negative rules mark the specific
"this Tally vendor is NOT that Supplier" pair without degrading the
signal elsewhere.

**Worked example — jewonline dev-bench baseline (Work Item 6).**

Against the 10-entry `jewonline_suppliers_real.csv` + full 221 MB
CACSPU Tally export, the fuzzy layer produced 7 matches at 85–90%.
**All 7 are false positives** driven by the dev-bench master's short
generic names (`Amit`, `Nilesh Traders`, `Gulab Hardware`) and common
English tokens (`Hardware`, `Traders`):

```
Tally ledger                                Matched supplier    Score   Why it's a FP
------------------------------------------  ------------------  ------  -----------------------
Amita Marble & Granites-VA0044              Amit                0.900   4-char prefix coincidence
Nilesh Travels                              Nilesh Traders      0.857   different business, same first word
Bharat Hardware Store-SB0083                Gulab Hardware      0.855   shared "Hardware" token
Mahavir Hardware And Electronics            Gulab Hardware      0.855   shared "Hardware" token
Mahesh Hardware And Electricals             Gulab Hardware      0.855   shared "Hardware" token
Om Electrical & Hardware-VO0006             Gulab Hardware      0.855   shared "Hardware" token
Rajshree Hardware & Electricals-SR0167      Gulab Hardware      0.855   shared "Hardware" token
```

**Future debugging note.** If future runs against production supplier
masters produce similar low-50s-score coincidences, DO NOT chase them
as resolver bugs. Verify the WRatio output against real token overlap;
in ~every case the explanation will be short generic supplier names in
the master colliding with longer Tally vendor names. Mitigation is
via reviewer-promoted negative rules or enriched supplier-name data
(including registered business names, address fragments, GSTIN — all
currently out of scope).

### 6.5 Deferred — Control Account Pattern DocType (Week 4+)

Operational staff will eventually need to add control-account patterns
without a code edit. Scope for Week 4+:

- New DocType `Control Account Pattern` with fields `pattern` (Data,
  regex), `description` (Small Text), `is_active` (Check), `source`
  (Select: seed | manual | session_review).
- `is_control_account(ledger)` reads the DocType at runtime instead
  of from the hardcoded `_CONTROL_ACCOUNT_PATTERNS` list, with the
  same compile-once cache semantics.
- Seed the 8 Week-3 patterns on first migrate.

Not doing this today because (a) the 8 patterns cover observed real
data adequately, (b) Week 3 is already large, (c) pattern additions
during Week 3 are done via a code commit + push cycle that's
acceptable for the small number of edits we expect.

---

## 7. Leaf-only posting principle

All accounting postings in both Tally and ERPNext happen at **leaf** level.
Group accounts are containers whose balance equals the sum of their
children — they are not postable in their own right. When `rgi_migration`
migrates opening balances, it proposes **leaf-to-leaf** mappings only.

The group-account refusal validator (§2(b)) enforces this at the mapper
layer. It is not a defensive safety net but a **fundamental correctness
requirement**: posting to a group account would create a split-brain
between the group's children-summed balance and its manually-posted
balance. Tally and ERPNext both forbid this structurally for the same
reason.

### Corollary for the rule library

Rules targeting Tally names that represent **groups** in the source data
can never fire — Tally never emits groups as `<LEDGER>` elements in its
All Masters XML export, only leaves. Such rules are marked
`status="paused"` in the library (not deprecated — they remain live for
entities where the same name happens to be used as a leaf ledger).

### Cross-app architectural boundary

Some Tally ledger categories are handled by **sibling apps**, not
`rgi_migration`. Student receivables flow through `dux_voucher`'s
`Ex Student Opening Batch` per `docs/dux_voucher_integration.md`.

The parser's `is_student_ledger=True` flag is the routing mechanism:

- **Per-student leaves** — `parent_chain` contains a student-group
  marker (`STUDENT`, `STUDENTS`, `CYBERVIDYA`, `PASSOUT`, `GHRIMR
  STUDENT`, etc.). Existing behavior since Week 1.
- **Aggregate control accounts** — cleaned name is an exact
  (case-insensitive, whitespace-collapsed) member of
  `AGGREGATE_STUDENT_ACCOUNT_NAMES` in
  `rgi_migration/parsers/tally_xml_parser.py`. Added 2026-04 to
  catch `Student Fee Outstanding` and any future same-shape names.

Both routes set the same flag; both result in the ledger being placed
on `ParsedTallyTB.student_ledgers` and excluded from the main
`ledgers` list. The downstream mapper never sees them.

These do NOT appear in the main `Mapping Rule` library because the
routing is an architectural boundary, not per-entity business logic.
The parser is the right layer — it owns the source-data
classification.

**Original §4.10 Student Fee Outstanding — deprecated 2026-04.**
Initially seeded as a confirmed rule; paused in a first pass, then
fully removed from the seed library once the parser-level routing
landed. Historical record lives in `RGI_Migration_Rules.md` §4.10 for
audit-trail completeness. The rule was the wrong shape: a cross-app
boundary can't be expressed as a per-entity `Mapping Rule` because
the routing is uniform across all 59 entities.

### How to identify future leaf-paused / parser-routed candidates

When seeding or promoting a rule, ask two questions in order:

1. *"Does this ledger belong to rgi_migration at all, or does a sibling
   app (dux_voucher, future ones) own it?"* If sibling-owned, extend
   the parser's classification (e.g. add to
   `AGGREGATE_STUDENT_ACCOUNT_NAMES`); do NOT add a Mapping Rule.
2. *"In the Tally XML export, will this name appear as a `<LEDGER>`
   element or a `<GROUP>` element?"* If the answer is "group in nearly
   all entities we care about", the rule would never fire. Ship it
   as `status="paused"` with a note, or skip entirely if the routing
   is architectural (answer 1 covers it).

Seeding as confirmed when either answer points elsewhere creates dead
rules that look active in review UIs and mask real gaps.

---

## 8. Generator conventions (Work Item 7)

These are post-mapper concerns — shared across generators #1-#4 so the
four artefacts (main JE, OIT CSV, party-Dr JE, students CSV) stay
consistent when a reviewer compares them or aggregates across entities.

### 8.1 CSV format conventions

- **Encoding**: UTF-8, no BOM.
- **Line endings**: `\n` (LF). ERPNext's Import Data tool and most Indian
  accounting workflows handle either, but LF keeps diffs clean in git if
  a CSV ever gets committed for regression testing.
- **Quoting**: RFC 4180 — only wrap fields in double-quotes when the
  value contains `,`, `"`, or a newline. Escape embedded `"` as `""`.
- **Date columns**: always ISO 8601 (`YYYY-MM-DD`) regardless of how the
  RGI rules doc examples display dates. Rationale: RGI examples show
  localised DD-MM-YYYY (`01-04-2026`), but that's UI presentation, not a
  storage contract. ERPNext's Import Data tool parses ISO natively and
  without locale ambiguity; the Desk UI re-renders per user preference
  after import. Keeping storage/interchange ISO avoids a class of
  import-tool bugs where DD-MM vs MM-DD is ambiguous.
- **Currency / amounts**: 2 decimal places, no thousands separator, no
  currency symbol. E.g. `125000.00`, not `₹1,25,000.00` and not `125000`.
- **Integer-like columns that are stored as Data in the target DocType**
  (e.g. OICT Item's `qty` field) write as string literal `"1"`, not `1`.
  Reflects the DocType's actual type so Import Data doesn't coerce
  unpredictably.

### 8.2 Generator performance — parse-and-map caching

Week 3 generators reparse the Tally XML and re-run the full Tier-1
mapper on every regeneration. Acceptable for dev cycle (2-3s on
CACSPU's 221 MB file) and removes a persistence layer that would
otherwise need its own schema + invalidation logic.

**Week 4+ optimisation** (deferred): persist `ParsedTallyTB` and the
`list[MappedDecision]` to the Tally Migration Session on first parse
(JSON attach or pickle blob). Subsequent regenerations read from
cache; cache invalidates on source-file SHA-256 change or explicit
reviewer "reset parse" action. Do not do this inside a generator
module — the cache is a session-lifecycle concern, not a
generator-lifecycle concern. The review UI workflow is the natural
owner.

### 8.3 Supplier re-check ordering (Order A)

Generators #2 (OIT CSV) and #3 (party-advance JE) must re-check every
resolved supplier against the live ERPNext Supplier master at
generation time — between mapping and generation the master can have
disabled, deleted, or renamed rows.

**Ordering**: **all-suppliers-first, batch error**. The generator
iterates the supplier-resolving decisions once, collects every
missing/disabled supplier into a list, then refuses the whole run
with a single error message that cites ALL problems at once. Not
first-encountered-refusal, not one-by-one.

Rationale: reviewer sees the complete set of issues in a single trip.
First-encountered refusal forces N re-runs for N problems. The
single-pass implementation is also cheaper — one `frappe.get_all`
batch lookup instead of N individual `frappe.get_doc` calls.

Example refusal message:

```
Cannot generate OIT CSV for session {name}: 3 supplier resolution issues:
  - Nilesh Traders: deleted from Supplier master
  - Abhi Tria: disabled
  - Gulab Hardware: deleted from Supplier master
Re-run mapping or restore/enable suppliers, then regenerate.
```

Applies identically to generator #3's supplier re-check step.

### 8.4 Artefact filename convention

CSV / attachment filenames produced by generators use the pattern:

```
{artefact}-{ABBR}-{fiscal_year}-{YYYYMMDDHHMMSS}.{ext}
```

e.g. `oit-CACSPU-2026-2027-20260420091530.csv`,
`students-CACSPU-2026-2027-20260420091530.csv`.

Note: uses `fiscal_year` in **full form** (`2026-2027`), not
`fiscal_year_short` (`26-27`). Rationale: `fiscal_year_short` is a
hidden, read-only field on `Tally Migration Session` with no
auto-population hook (confirmed 2026-04-20 during the autoname
diagnostic). Week-4 UI workflows that create sessions through Desk
would leave it blank, breaking any filename template that depends on
it. `fiscal_year` is always populated (required field) and fully
self-documenting in the filename.

The trailing `YYYYMMDDHHMMSS` timestamp preserves an audit trail in
the File Manager when a session regenerates — the old file is
deleted from the session attachment link, but prior versions remain
distinguishable by timestamp for forensics.

---

## 9. Week-3 → Week-4 carry-over

Explicit list of items generated during Work Item 7 (Week 3) that are
correctly deferred to Week 4. Not bugs, not technical debt — known
scope partitioning.

| Item | Why it's Week 4 |
|---|---|
| Residual ~21 unmapped on CACSPU | Name-divergence cases (Bank of Maharashtra (Cap) vs Bank of Maharashtra A/c No. ...). Tier-2 fuzzy + Account Creation Request workflow is exactly designed for these. Writing per-case Mapping Rules would be churn. |
| 18 `pending_supplier_creation` on CACSPU | Real vendors needing ERPNext Supplier master rows created. Review UI workflow drives this, not generator code. |
| `fiscal_year_short` auto-pop hook | Needed for Desk-created sessions. Can be a tiny `before_insert` hook in `tally_migration_session.py` (`"{YY}-{YY+1}"` from `fiscal_year`). Trivial when ready. |
| Generator #1 full-file smoke on real data | Blocked by refusal until reviewers clear the 21 unmapped. Synthetic-fixture unit tests cover the happy path. Acceptance criterion for gen #1 in Week 3 is "code + refusal contract + unit tests", not "Draft JE visible in ERPNext UI". |
| `ParsedTallyTB` + decisions persistence | Per §8.2 — session-lifecycle concern. Review UI is the natural owner. Generators reparse-and-remap until then. |
| `Tally Migration Session Event` child table | Prose-design mentioned it; doesn't exist. Generators log deletion/warning events to `session.error_log` (Long Text) as timestamped blocks in the interim. Clean schema migration when Week-4 UI wants structured events. |
| Stale pre-Decision-1 mappings with `pending_supplier_creation` on now-zero-balance ledgers | Currently moot (no persisted sessions). Week-4 review UI should re-run mapping on load for any session whose source-file SHA-256 matches the stored parse — cheap and always consistent with current mapper behaviour. |
| ~~CACSPU Company's `Stock Received But Not Billed` default~~ | **Withdrawn 2026-04-20** — post-commit follow-up confirmed the default IS set on CACSPU (`Stock Received But Not Billed - CACSPU`, also set on the two peer companies on the bench). The Q5 Path B failure that surfaced this item was a false alarm: the synthetic `Purchase Invoice` test fixture used `expense_account="Temporary Opening - CACSPU"` (an Equity account, not Expense), which triggered ERPNext's fallback-to-default cascade with a misleading SRBNB error. The real OICT `make_invoices()` production path is unaffected. |

---

## 10. Custom-page bundle-refactor threshold

Recorded here (rather than per-page) so subsequent custom pages
across the app inherit the rule without re-litigating it each
time. Origin: Week 4 Item 1+2 (Mapping Decision Review Page)
prose refinement, see `docs/week4_review_ui_design.md §1.12`.

**Default**: a custom Frappe Page's JS entrypoint ships inline
— all panel classes in the single `<page_name>.js` file, no
`<page_name>.bundle.js` wrapper, no `hooks.py` `app_include_js`
entries for the page. This keeps the asset graph simple and
avoids `bench build` churn during development.

**Refactor to a bundle when EITHER holds**:

1. The inline file crosses **2,500 lines** — at that size,
   reading end-to-end in one session stops being practical; the
   page is better split into 3-5 focused modules (e.g. a list
   pane, a detail pane, a controller, a shared utilities file)
   that each read cleanly.
2. **Item 6 (Tier-2 fuzzy) or any future Item** adds meaningful
   ranking / algorithm logic that warrants its own testable
   module. Algorithmic code should not live inline with DOM
   rendering — the moment ranking logic appears, it splits out
   regardless of line count.

Both triggers are "refactor now" not "refactor soon." If the
line-count trigger fires mid-implementation, pause, refactor,
then resume.

**Precedent**: ERPNext Point of Sale (~4,400 JS lines + ~1,400
SCSS) uses a bundle with 8 modules. Worth looking at
`erpnext/public/js/point-of-sale.bundle.js` for the split
pattern if we cross the threshold on any page.

**Not in scope for this rule**: SCSS / CSS. Page-specific
stylesheets can stay inline up to any size; CSS doesn't suffer
from readability-at-scale the way JS does.

---

## 11. Change log

| Date | Change |
|------|--------|
| 2026-04-19 | Initial draft. Anti-pattern seeding test + account creation policy. |
| 2026-04-19 | Added §2 Tier-1 structural checks (P&L exclusion + group-account refusal). Moved §11 R5/R6/R7/R10 out of the seed plan into structural/process categories. Worked Example B (FDR group) added to §1. `Account Creation Request` audit trail switched from single Link to `source_rules` child table. |
| 2026-04-19 | §4 Known data quirks section added. Documents the CACSPU-vs-GHRCACS Tally-internal-name quirk — Tally source names are opaque and must not be sanitised to match ERP conventions. |
| 2026-04-19 | §4 P&L A/c routing added. Tally's Profit & Loss A/c + Income Expenditure A/c collapse to one ERP account (`Income Expenditure A/c - {ABBR}`) with combine_amounts=1. Seeded as "§3.3 + derived" positive rule. |
| 2026-04-19 | §5 Schema mutation recipes added. Captures operational lessons from the Week-3 DocType audit iteration: DocField rename pattern, orphan column cleanup, v16 API gotchas, bench-console-heredoc as the preferred execution surface. |
| 2026-04-19 | §6 Supplier matching added. Documents rule-first ordering, the 8-pattern control-account exclusion list, and the §4.6 regression finding caught at prose-review time during Work Item 6. Control Account Pattern DocType deferred to Week 4+. |
| 2026-04-19 | §6.4 Fuzzy false-positive pattern added. Documents the 7 jewonline-dev-bench false positives from Work Item 6, the rule that threshold 85% stays as designed, and negative-alias-rule mitigation over threshold tightening. |
| 2026-04-19 | §7 Leaf-only posting principle added. Articulates that all Tally/ERPNext postings happen at leaf level; group-account refusal in §2(b) is a correctness requirement, not a defensive check. First recorded example: §4.10 Student Fee Outstanding paused because the name is a group in Tally on educational entities, routing through dux_voucher's per-student CSV instead. |
| 2026-04-20 | §7 extended with "Cross-app architectural boundary" subsection. §4.10 fully deprecated (removed from seed library; DocType row deleted). Parser extended with AGGREGATE_STUDENT_ACCOUNT_NAMES frozenset — aggregate student control accounts now flagged `is_student_ledger=True` alongside per-student leaves. Total seed count: 23 → 22. |
| 2026-04-20 | §8 Generator conventions added — CSV format (ISO 8601, UTF-8, RFC 4180), parse/map caching deferred to Week 4, all-suppliers-first batch refusal (Order A), and the artefact filename convention using `fiscal_year` in full form (avoids the `fiscal_year_short` auto-pop gap). §9 Week-3 → Week-4 carry-over added — explicit list of deferred items (residual unmapped, supplier creations, session-event child table, parse cache, `fiscal_year_short` hook, CACSPU `Stock Received But Not Billed` default). Change log renumbered §8 → §10. |
| 2026-04-20 | §9 CACSPU `Stock Received But Not Billed` row withdrawn — post-gen-#2 bench check confirmed the default IS already set on CACSPU + both peer companies. The Q5 Path B failure that flagged it was a false alarm (synthetic PI fixture used an Equity account where Expense was required, cascading into a misleading SRBNB default-check error). Real OICT production path unaffected. |
| 2026-04-20 | **Company configuration corrections (CACSPU)** applied via `frappe.db.set_value` on erp.jewonline.in: `default_payable_account` changed from `Unsecured Loans Payable - CACSPU` → `Sundry Creditors - CACSPU`. The previous value was inconsistent with RGI §5.3 ("Dr to Sundry Creditors") and would have caused reviewer-edited Purchase Invoices on CACSPU to default-populate the wrong payable account, contaminating reporting. Surfaced during gen #3 research. `stock_received_but_not_billed` verified unchanged (already correctly set to `Stock Received But Not Billed - CACSPU`, same as peer companies JEWIPL + DD). No DocType schema change; Company-record setting only. |
| 2026-04-20 | **`Tally Migration Session` schema** — added `generated_advance_je` (Link → Journal Entry, read-only) + `generated_advance_je_reference` (Data) fields adjacent to the existing `generated_je_draft` / `generated_je_reference` pair in the Output Artifacts section. Enables gen #3's idempotency pattern + Week-4 UI to cleanly display all four generator artifacts per session. Applied via `dt.save()` on bench + `bench migrate` + scp JSON bridge. |
| 2026-04-20 | **Generator #3 landed** — Party-wise Dr JE (`advance_je.py`). Per RGI §5.3, one Opening Entry JE per session carrying every net-Dr supplier balance with `is_advance="Yes"`, balanced by `Temporary Opening - {ABBR}`, reference `OB-{ABBR}-2026-02`. 11 unit tests, strict scope. Draft only, never auto-submit. Full-file smoke deferred to end-of-Week-3 integration across all 4 generators. |
| 2026-04-22 | §10 Custom-page bundle-refactor threshold added. Rule: inline by default; refactor to a bundle when the page's JS file exceeds 2,500 lines OR when Item 6 (Tier-2 fuzzy) adds meaningful ranking logic. Change log renumbered §10 → §11. Origin: Week 4 Item 1+2 (Mapping Decision Review Page) prose refinement 7, see `docs/week4_review_ui_design.md §1.12`. |
| 2026-04-22 | §5 expanded with two schema-mutation clarifications captured during Week 4 Item 1 Commit 1 schema-migration work: (a) `idx` is framework-universal and must NOT be dropped as a child-table orphan; (b) autoname mode string requires a trailing colon (`"naming_series:"`, not `"naming_series"`) per the Frappe naming dispatcher. Both learned the hard way; both now load-bearing guardrails for the next `istable` flip. |
| 2026-04-22 | §5 expanded further during Week 4 Item 1 Commit 2 page-scaffolding work: (a) Frappe Page names are truncated to 20 characters at runtime autoname regardless of how `name` / `page_name` are passed — fixture-loading is the only path to longer names; (b) pre-existing Socket.IO 404s on this bench are orthogonal to our work and can be ignored during Desk-page browser verification. Guardrail for the next custom Page we create. |
