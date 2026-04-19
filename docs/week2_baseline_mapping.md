# Week 2 Baseline — Tier-1 Mapper Against Full CACSPU Tally Export

**Date:** 2026-04-19
**Input:** full 221 MB `<CompanyName>G H R College of Arts Commerce &Science</CompanyName>` Tally All Masters XML (stored outside the repo at `~/tally-exports/ghrcacs_masters.xml`; filename pre-dates the CACSPU rename).
**COA:** [cacspu_erpnext_coa_real.csv](../rgi_migration/tests/fixtures/cacspu_erpnext_coa_real.csv) — 698 ERP accounts exported from Desk.
**Rules:** [seed_plan.json](seed_plan.json) — 23 confirmed (20 positive + 3 anti-pattern).
**Mapper:** commit `eb6937d` (adds §3.3+derived P&L routing rule).

This is the honest first-run baseline for entity #1. The 35.8% sample-run
result was mechanics validation; these numbers are real signal.

---

## 1. Top-line numbers

| Bucket | Count | Notes |
|---|---:|---|
| Main ledgers (mapping input) | **1,965** | Non-student, non-P&L-closed-zero |
| Student ledgers (routed separately per §5.4) | 4,062 | `tb.student_ledgers` — Week-3 Phase-2 OIT |
| `tier1_exact` | 97 | Direct ERP leaf match by name |
| `tier1_rule` | 11 | Positive Mapping Rule fired (exact_ci) |
| `tier1_pattern` | 2 | Pattern-mode rule (regex) fired |
| `excluded_pnl` | 347 | Structural validator — correctly skipped |
| `group_refused` | 3 | Structural validator — group accounts |
| `pending_account_creation` | 2 | One anti-pattern + one conditional positive |
| `unmapped` | **1,503** | See §3 breakdown |
| Anti-pattern firings | **1** | §11 R3 Hostel Fee A/c |
| Account Creation Requests | 2 | Hostel Fee + Student Fee Outstanding |
| Raw `hit_rate` (mapper field) | 0.235 | `(total − unmapped) / total` |

## 2. The Tier-1-actionable view

Raw hit rate is misleading because 1,154 of the unmapped 1,503 are party
ledgers (Sundry Creditors / Sundry Debtors descendants) that Tier-1 is
not supposed to map — Week-3's OIT / party-wise JE workflow handles them
per §5.1–§5.3. Excluding them:

| Category | Count | Handled by |
|---|---:|---|
| Mapped by Tier-1 (`tier1_*` + `pending_account_creation`) | 113 | this sprint |
| Structural exclusions (`excluded_pnl` + `group_refused`) | 350 | validators (correct) |
| Party ledgers routed to Week-3 OIT | 349 | JE/OIT builder |
| **Non-party unmapped — real Tier-1 gaps** | **1,154** | Need new rules or fuzzy (Tier-2) |

Of those 1,154 non-party gaps, **1,131 have zero balance** (appear in the
Tally COA but carry no opening). Only **23 non-party unmapped ledgers
have non-zero balances** — those are the material gaps the rules library
should absorb before entity #2.

## 3. The 23 material gaps (non-party unmapped with non-zero balance)

### 3a. Inter-entity branch accounts (6 ledgers, ₹2.9 Cr combined)

| Tally ledger | Side | Amount | Candidate ERP target |
|---|---|---:|---|
| G H R Education & Medical Foundation Nagpur | Dr | 2,99,97,818.00 | Branch account — new rule needed |
| G H R College Of Engg & Management ( Engg) Pune | Dr | 95,83,415.65 | Branch → `GHRCEMPU`-named ERP account |
| G H R Edu & Medical Foundation ( Society) Pune | Cr | 75,62,544.04 | Society trust account |
| G H Raisoni Public School Pune | Dr | 69,14,023.85 | Branch → `GHRPSPU`-named ERP account |
| G H Raisoni Junior College Pune | Cr | 47,78,989.00 | Branch → `GHRJCPU`-named ERP account |
| G H R College Of Engg & Management ( M B A) Pune | Dr | 1,67,849.00 | Branch → `GHRCEMPUMBA`-named ERP account |

**Pattern recommendation for Week 3.** These follow §4.1's shape (Tally
inter-entity ledger → ERP branch account) but the naming doesn't match
any seeded §4.1 alternate. Adding a family of GHREMF-Pune inter-entity
rules — one per sibling abbreviation from §1.2 #17–24 — would auto-map
these on every entity in the group. Candidate for reviewer promotion
once the ERP-side branch accounts are confirmed present on CACSPU's COA
(spot-check suggests not — none of these six names match any leaf in
`cacspu_erpnext_coa_real.csv`; would need creation requests too).

### 3b. Fixed assets (2 ledgers, ₹35.3 L combined)

| Tally ledger | Side | Amount | Notes |
|---|---|---:|---|
| Electrical Fitting | Dr | 17,92,994.65 | ERP has `Electrical equipment - CACSPU`? Name mismatch |
| Library Books | Dr | 17,36,180.00 | Likely needs its own COA entry |

**Tier-2 fuzzy territory.** `Electrical Fitting` vs ERP's
`Electrical equipment` is exactly the kind of close-but-not-exact match
rapidfuzz is for. Week-4 addition.

### 3c. Bank accounts with account-number suffix (3 ledgers)

| Tally ledger | Side | Amount |
|---|---|---:|
| Bank of Maharashtra ( N S S Camp 60041022214) | Dr | 3,07,517.92 |
| Bank of Maharashtra (Cap) - 60451303968 | Dr | 1,04,773.38 |
| ICICI BANK - 624205021153 | Cr | 1,93,787.61 |

The parser strips a trailing `-{digits}` as Tally ID but only the last
one — `Bank of Maharashtra (Cap) - 60451303968-11079` cleans to
`Bank of Maharashtra (Cap) - 60451303968`, still containing the
account-number suffix. ERP has `Bank of Maharashtra (Cap) - CACSPU`
without the account number, so exact-name misses.

**Options:**
- Tier-2 fuzzy (Week 4) would absorb these.
- A Tier-1 regex rule stripping `(?: - \d{6,})$` from bank names before
  matching would be even cleaner, but the pattern is bank-specific
  (doesn't generalise to, say, `ICICI BANK - 624205021153`).
- Simplest: let reviewer promote per-bank mapping decisions into rules
  once approved. Three rules, one-time cost.

### 3d. Student-side liability (1 ledger, ₹15.6 L)

| Tally ledger | Side | Amount |
|---|---|---:|
| STUDENT PAYABLE CYBERVIDYA | Cr | 15,56,689.50 |

§4.18 already covers `STUDENT RECEIVABLE CYBERVIDYA` → asset receivable.
This is the liability-side counterpart (student paid in excess). Needs
a symmetric rule — new §4.20 candidate: `STUDENT PAYABLE CYBERVIDYA` →
`Student Payable Cybervidya - CACSPU` (liability under Liability For
Students).

### 3e. TDS / compliance accounts (4 ledgers, ₹17,421 combined)

| Tally ledger | Side | Amount |
|---|---|---:|
| T D S On Salary - 193 | Cr | 10,500.00 |
| T D S On Contractors Payment - 194 C | Cr | 2,651.00 |
| T D S On Rent - 194 I | Cr | 1,620.00 |
| TCS_11210 | Dr | 213.00 |

ERPNext has a standard TDS account template. Add a set of TDS-section-
code rules (`193`, `194 C`, `194 I`, `194 H`, `195`, etc.) pointing at
the relevant ERPNext COA entries when available, or flagging for
creation.

### 3f. One-offs (7 ledgers, ₹2 L combined)

| Tally ledger | Side | Amount | Probable shape |
|---|---|---:|---|
| N S S Account | Cr | 82,750.00 | Trust-fund sub-account; per-entity |
| Priyanka Deshmukh - Ph D Fees | Cr | 60,000.00 | Individual — reviewer decision |
| Printing & Stationary Material Inventory | Dr | 29,244.51 | New Asset leaf needed |
| Vishal Waghole - Advance | Dr | 12,000.00 | Staff advance — Sundry Creditor? |
| University Medical Fee @ | Cr | 3,210.00 | University remittance payable |
| General Store Inventory | Dr | 2,152.95 | New Asset leaf needed |
| Laboratory equipment | Dr | 1,10,645.47 | ERP has sim-named asset? |

## 4. Anti-pattern firings

| # | Rule | Tally ledger | Outcome |
|---|---|---|---|
| 1 | §11 R3 Hostel Fee A/c | `Hostel Fee A/c` (Liability) | Forbidden `Hostel Fee A/c - CACSPU` (Income in ERP) refused; creation request for `Hostel Fee Advance Payable - CACSPU` under `Liability For Students - CACSPU`. **Exactly the designed behaviour.** |

The other two seeded anti-patterns (§11 R2 Summer Term Exam Fee, §11 R4
Caution Money £ asset version) did not fire because CACSPU's Tally data
doesn't contain ledgers with those names. Both target COA entries DO
exist on the ERP side — if a future Tally export for this entity
introduces either name, the anti-pattern fires automatically.

## 5. Account Creation Requests

Two requests, both correctly generated:

| # | Triggered by | Tally ledger | New account | Parent |
|---|---|---|---|---|
| 1 | §11 R3 anti-pattern | `Hostel Fee A/c` | `Hostel Fee Advance Payable - CACSPU` | `Liability For Students - CACSPU` (Liability) |
| 2 | §4.10 positive (conditional) | `Student Fee Outstanding` | `Student Fee Outstanding (Receivable) - CACSPU` | `Loans & Advances - CACSPU` (Asset) |

Both parents exist in the COA (`cacspu_erpnext_coa_real.csv`), so the
creation step would succeed at reviewer "Create Now" time. Lifecycle
(docs/mapper_design_notes.md §3) unbroken.

## 6. Group-account refusals

Three refusals, all zero-balance — reviewer dismisses or reclassifies:

| Tally ledger | Parent chain ending | Plausible reviewer action |
|---|---|---|
| Advance To Staff | (Current Assets) | Staff advance roll-up; dismiss if no balance, else pick a specific staff leaf |
| Reserves & Surplus | (Capital Account) | Equity roll-up; dismiss |
| Tirupati Urban Co Op Loan A/c | (Loans Liability) | Loan account — confirm if this Tally group has any under-children to post |

## 7. Net assessment

**Week-2 target hit.** Tier-1 delivered:
- 113 mapped (main + creation requests)
- 3 structural refusals (group accounts — exactly the Flag-4 design)
- 1 anti-pattern firing that landed on the designed creation workflow
- 2 creation requests with parent-validation passing

**Real Tier-1 gaps: 23 ledgers.** Clear roadmap for the rule library's
next expansion — 5 of those families (branch accounts, fixed-asset name
drift, bank-number-suffix, student-payable Cybervidya, TDS codes) would
plausibly absorb ~18 of the 23 gaps with one-time rule work. Tier-2
fuzzy (Week 4) is only needed for close-but-not-exact matches — 3 bank
accounts and the `Electrical Fitting` / `Library Books` style name
drifts, roughly 5 of 23.

**Party ledger volume confirms the OIT architecture was right.** 349 of
1,503 unmapped are vendor / supplier party ledgers. Trying to map these
one-by-one via §4-style rules would explode the rule library. Keeping
them in Week-3's JE/OIT process rules (§5.1–§5.3) keeps the Mapping
Rule table focused on name-to-name transformations.

**Hit-rate trajectory** per CLAUDE.md ("by entity 10, Tier 1 auto-maps
70%+"):

- Entity 1 (CACSPU, this baseline): 115 / (1965 − 347) = **7.1% of
  non-P&L main ledgers mapped by Tier-1** — low but expected on entity 1
  with 23 seeded rules.
- Entity 10 target ≈ 70% = ~1,135 of 1,618 mapped. Gap = ~1,020
  ledgers. If reviewer promotes rules at a rate of ~100 per entity
  (plausible for the first entities where whole families of gaps
  resolve together), the 70% line is reachable around entity 7–10.

The non-party unmapped-with-balance count (23 on this entity) is the
metric to watch. When that drops below ~5 on a new entity, Tier-1 is
doing its job and reviewer work collapses to new-account-creation
sign-off plus party-side decisions.

## 8. Reproduce

```
.venv/Scripts/python -m rgi_migration.mapper.mapper \
  --coa rgi_migration/tests/fixtures/cacspu_erpnext_coa_real.csv \
  --rules docs/seed_plan.json \
  --abbr CACSPU \
  --xml ~/tally-exports/ghrcacs_masters.xml
```

Full runtime ~40 seconds on the reference workstation (parse 35s, map 2s,
report 1s).
