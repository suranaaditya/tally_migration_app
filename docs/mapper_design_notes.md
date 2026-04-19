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

---

## 5. Change log

| Date | Change |
|------|--------|
| 2026-04-19 | Initial draft. Anti-pattern seeding test + account creation policy. |
| 2026-04-19 | Added §2 Tier-1 structural checks (P&L exclusion + group-account refusal). Moved §11 R5/R6/R7/R10 out of the seed plan into structural/process categories. Worked Example B (FDR group) added to §1. `Account Creation Request` audit trail switched from single Link to `source_rules` child table. |
| 2026-04-19 | §4 Known data quirks section added. Documents the CACSPU-vs-GHRCACS Tally-internal-name quirk — Tally source names are opaque and must not be sanitised to match ERP conventions. |
