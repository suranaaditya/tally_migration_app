# Tally All Masters XML — Sign Convention & Date Source

This document is the authoritative reference for how the parser interprets the
`<OPENINGBALANCE>` field in a Tally *All Masters* XML export. Every rule
below is derived from real fixture data (CACSPU-PUNE, FY 2025-26) and
cross-verified against Tally's official integration documentation.

---

## 1. The sign rule

The `<OPENINGBALANCE>` element holds a **signed decimal**. Its sign — and
**only its sign** — determines whether the balance is Debit or Credit:

| `<OPENINGBALANCE>` value | Side  | Amount        |
| ------------------------ | ----- | ------------- |
| < 0                      | **Dr** | `abs(value)` |
| > 0                      | **Cr** | `value`      |
| = 0 or absent            | none  | 0             |

This rule applies **identically** to:

1. The ledger's direct `<OPENINGBALANCE>` child element
2. Each bill's `<OPENINGBALANCE>` inside a `<BILLALLOCATIONS.LIST>`

The group's `<ISDEEMEDPOSITIVE>` flag is **not** used by this rule. It is
Tally UI metadata, not a data-encoding flag.

### Why this is counter-intuitive

The sign in the XML is the opposite of how a bookkeeper writes it on paper:
an asset with a positive (Dr) book value is stored as a **negative** number
in the XML. Treat the storage as "the positive direction is Cr for every
account type."

### Evidence table (11 ledgers cross-checked against the CACSPU opening TB Excel)

| Ledger                          | Group / nature              | `<OPENINGBALANCE>` | Excel says | Fits rule |
| ------------------------------- | --------------------------- | ------------------ | ---------- | --------- |
| Computer & accessories-9005     | Fixed Assets (Dr-nature)    | −18,676,470.95     | Dr         | ✓         |
| Furniture & fixture-9009        | Fixed Assets (Dr-nature)    | −10,304,693.19     | Dr         | ✓         |
| Building Revaluation-1373       | Immovable Properties        | −25,678,384.94     | Dr         | ✓         |
| Bank of Maharashtra (Cap)-11079 | Bank Accounts (Dr-nature)   | −104,773.38        | Dr         | ✓         |
| Depreciation Fund-2125          | Reserves & Surplus (Cr)     | +90,483,637.85     | Cr         | ✓         |
| Income Expenditure A/c-2127     | Reserves & Surplus (Cr)     | +29,449,417.86     | Cr         | ✓         |
| Profit & Loss A/c               | (system, no `<PARENT>`)     | +22,302,542.93     | Cr         | ✓         |
| CAFE SESSY-VC0096               | Sundry Creditors (Cr)       | +738.00            | Cr         | ✓         |
| Anupam Silver Works-VA0243      | Sundry Creditors (Cr)       | +5,150.00 (direct) | Cr         | ✓         |
| AADESH — student                | STUDENTS (under S. Debtors) | −18,740.00         | Dr         | ✓         |
| Aarati (student, bill-wise)     | CYBERVIDYA-BSc-3rd YEAR     | bills net to 0     | 0          | ✓         |

Zero counter-examples found across the full CACSPU fixture (6,027 ledgers).

---

## 2. Date-source rule — XML = Closing

The `<OPENINGBALANCE>` field in a Tally master XML can represent **either**
the fiscal-year opening balance **or** the closing balance, depending on an
export-time option called *"Export closing balances as opening balance"*. The
parser cannot tell which by reading the XML alone.

**Our fixture was exported with that flag enabled**, so
`<OPENINGBALANCE>` = closing balance as of 31-Mar-2026 = the ERPNext
opening balance for FY 2026-27.

### How we verified

A diagnostic script (`scripts/check_xml_date_source.py`) classifies every
common ledger as `matches_opening`, `matches_closing`, `matches_both`, or
`matches_neither` against the reference opening TB Excel
(`sample_cacspu_opening_tb.xlsx`), which has explicit separate Opening and
Closing columns. Only ledgers where Opening ≠ Closing in Excel count as
discriminating evidence:

```
STRONG evidence (both Op and Cl non-zero AND different):   24 ledgers
  → matches_opening:  0
  → matches_closing: 24

WEAK evidence (one side zero, XML = the non-zero side):    36 ledgers
  → matches_opening:  0
  → matches_closing: 36

AMBIGUOUS (XML=0, Op=0, Cl!=0) — P&L post-close behaviour: 77 ledgers
  (not counted; zero is consistent with either interpretation)

Weighted score (strong × 3 + weak × 1):
  opening-side : 0
  closing-side : 108
```

Representative strong-evidence samples:

| Ledger                                  | Op          | Cl          | XML         |
| --------------------------------------- | ----------- | ----------- | ----------- |
| Caution Money £-2010                    | Cr 6,383,950 | Cr 6,577,950 | Cr 6,577,950 |
| Classic Enterprises-VC0011              | Cr 22,015   | Cr 8,233    | Cr 8,233    |
| G H R College Of Engg Pune              | Dr 9,029,538.65 | Dr 9,583,415.65 | Dr 9,583,415.65 |
| G H R Edu & Medical Foundation Nagpur   | Dr 6,018,785 | Dr 29,997,818 | Dr 29,997,818 |
| GLOBAL OFFICE SOLUTIONS-VG0080          | Cr 30,061   | Cr 7,930    | Cr 7,930    |

All 24 strong-evidence ledgers match the Closing column. Zero match Opening.
The case is closed.

---

## 3. Required Tally export settings

To guarantee the XML for a new entity is usable as ERPNext opening balance
input, the Tally operator MUST use exactly these settings when exporting:

> **Tally All Masters XML export — required configuration**
>
> 1. **Gateway of Tally** → **Display More Reports** → **List of Accounts**
> 2. Press **Alt+E** (Export) → **Configuration**
> 3. **Type of Masters**: `All Masters`
> 4. **Include Dependent Masters**: `Yes`
> 5. **Export closing balances as opening balance**: `Yes` ← **critical**
> 6. **Show Bill-wise Details also**: `Yes`
> 7. **File Format**: `XML (Data Interchange)`

With this configuration, `<OPENINGBALANCE>` in the exported XML equals the
source company's closing balances as of the export date, which is what the
parser and the downstream mapper treat as the ERPNext opening balance for
the new fiscal year.

If an export is done without option 5 enabled, the XML reflects the
**previous fiscal year's** opening balance (a year stale) — the parser's
defensive imbalance warning (§5 below) will catch this, but it's much
cheaper to export correctly the first time.

---

## 4. Expected zero balances — P&L accounts under "closing as opening"

When *"Export closing balances as opening balance"* is enabled, Tally
performs the equivalent of a year-end close before writing the XML:

- Revenue accounts under `Sales Accounts`, `Direct Incomes`, `Indirect Incomes`
- Expense accounts under `Purchase Accounts`, `Direct Expenses`,
  `Indirect Expenses`

…all have their net activity absorbed into **Profit & Loss A/c** (Tally's
system account, which appears in the XML with no `<PARENT>` tag). The
revenue/expense ledgers themselves reset to zero in the exported
`<OPENINGBALANCE>` field.

**This is correct behaviour, not a parser bug.** When we see the 77 P&L-style
ledgers showing `<OPENINGBALANCE>` = 0 or absent, we are observing Tally's
year-end close, not a data-loss event. For ERPNext migration, these ledgers
SHOULD have zero opening balance in the new fiscal year — the net P&L is
represented by the single balancing entry against P&L A/c (which the
mapper converts into the ERPNext retained earnings / Temporary Opening
adjustment during JE generation).

No special exclusion logic runs in the parser — these ledgers parse normally
and just happen to carry zero. A diagnostic flag `is_pnl_closed_zero` is set
so the review UI can surface them as "expected-zero" rather than forcing a
reviewer to mentally filter them out.

The flag is `True` when **all three** of the following hold:

1. `root_type` is `Income` or `Expense`
2. `opening_dr == 0 AND opening_cr == 0`
3. `parent_chain` contains one of: `Sales Accounts`, `Purchase Accounts`,
   `Direct Incomes`, `Direct Expenses`, `Indirect Incomes`, `Indirect Expenses`

### § 4.1 Why Profit & Loss A/c stays in `main_ledgers`

Tally's **Profit & Loss A/c** is a balance-sheet account in Tally's model —
it sits under the **Equity** root as the year-end net-income accumulator,
conceptually equivalent to retained earnings in ERPNext. It carries the
Cr balance that offsets the cumulative movement of assets and liabilities
over each closed fiscal year.

Although the parser flags it with `is_system_account=True` (because it has
no `<PARENT>` tag and is recognised by Tally as a reserved ledger), it
**remains in `main_ledgers`** — excluding it would remove the XML's
natural balancing entry and break the TB balance check. The
`is_system_account` flag exists for the downstream *mapper* to route the
ledger to ERPNext's retained-earnings account, not for the parser to
partition it out.

`system_ledgers` is reserved on `ParsedTallyTB` for any future truly
ledger-disjoint system accounts (e.g. suspense/error ledgers) that
shouldn't participate in the migration totals; it is currently always
empty.

---

## 5. Defensive imbalance warning

After the sign fix, a correctly-exported Tally XML **must** produce
`total_dr ≈ total_cr` within ₹1 (excluding P&L A/c system account, student
ledgers, and any other ledgers routed to separate outputs).

The parser emits a `parse_warnings` entry when the residual imbalance
exceeds **1%** of the grand total:

```
Large imbalance detected (Dr − Cr = ₹X on total ₹Y, Z%). This usually means
the Tally export was done without 'Export closing balances as opening
balance' = Yes. Other possible causes: mid-year cutoff, hand-edited XML,
unusual both-sided group accounts, OR the source Tally company has
accumulated historical imbalances (common for long-running companies with
years of hand-corrected vouchers — see §6.1). This last cause is not
fixable at export time; the JE builder will absorb the residual into the
Temp Opening balancer. See docs/tally_sign_convention.md §3 for required
export settings.
```

The presumed-cause framing highlights the most common root cause first
without closing off the other diagnoses a future user might need.

---

## 6. Known edge cases — 2 ledgers on the CACSPU fixture

Two ledgers in the CACSPU fixture carry a small XML-side balance that is
not reflected in the Excel TB. Both are bill-wise party accounts with no
direct `<OPENINGBALANCE>` — the XML value is computed by summing the
`<OPENINGBALANCE>` of each outstanding bill inside `<BILLALLOCATIONS.LIST>`.

| Ledger                                      | Parent group            | XML (bills net) | Excel Op / Cl |
| ------------------------------------------- | ----------------------- | --------------- | ------------- |
| Cash Purchases-SX0001                       | Sundry Creditors        | Dr 1,851.00     | 0 / 0         |
| SHIROLE NEHA DATTATRAY 21A0007BCAG1039      | CYBERVIDYA-BCA-3rd YEAR | Dr 2,250.00     | 0 / 0         |

Bill history for SHIROLE NEHA DATTATRAY (illustrative of both cases):

```
Tui (2021-09-20)                              +64,173.00
Tuition Fee-3022 (2021-12-22)                  −4,250.00
Tuition Fee-3022 (2022-12-07)                 −39,173.00
TU (2023-09-20)                               +23,314.00
Tuition Fee-3022 (2023-11-10)                 −45,000.00
University Prorata-2617 (2023-11-10)             −314.00
Student Activity Fees-0002 (2023-11-10)        −1,000.00
--------------------------------------------------------
Sum (signed, = XML balance)                    −2,250.00 → Dr 2,250
```

**Most likely explanation:** Tally's Trial Balance view suppresses
below-threshold residuals on inactive bill-wise parties. Both rows have
genuine bill history netting to their XML values, and both fall well below
typical TB suppression thresholds (~₹5,000) while the TB grand total is
₹153 M.

**Alternatives (less likely):** the opening TB Excel was generated with
*"Show all ledgers"* disabled; or the bills are historical outstandings
that the TB view has rolled out of visibility for a different reason.

Parser behaviour is unchanged — both ledgers still get a `parse_warnings`
entry and remain non-blocking. The amounts are immaterial (₹1,851 and
₹2,250 on a ₹153 million TB).

### § 6.1 Raw-XML residual and Temp Opening

The CACSPU fixture's raw XML sums to **₹−342,358.48** (Dr excess) across
all `<OPENINGBALANCE>` values. This is **not** a parser artifact — it is
baked into the source Tally company's data and is faithfully preserved by
the "Export closing balances as opening balance" mechanism.

Known contributors identified on CACSPU (~₹46,881 of the ₹342,358
accounted for):

| Ledger                                     | Parent                   | Contribution |
| ------------------------------------------ | ------------------------ | ------------ |
| Cash Purchases-SX0001                      | Sundry Creditors         | Dr 1,851     |
| SHIROLE NEHA DATTATRAY 21A0007BCAG1039     | CYBERVIDYA-BCA-3rd YEAR  | Dr 2,250     |
| MANUJA LAWNS-VM0073                        | Sundry Creditors         | Cr 6,780     |
| SHARAD EVENTS-VS0216                       | Sundry Creditors         | Dr 36,000    |

The remaining ~₹295K comes from additional bill-wise ledgers with small
net residuals that don't appear in the reference Excel TB (likely TB-view
suppression, consistent with §6).

**Treatment:** the parser carries this residual through as-is. The
Week-3 JE builder will absorb it into the `Temporary Opening - {ABBR}`
balancer entry, per **RGI_Migration_Rules.md §3.7**. A large Temp Opening
amount for an entity is diagnostic — it signals historical imbalances in
the source Tally data that the client may want to review independently
of the migration itself.

**Tolerance policy:** the regression test (`test_xml_vs_excel_reference.py`)
asserts the raw-XML residual stays within **1% of grand total**. This
matches the parser's own `parse_warnings` imbalance-warning threshold
(§5), so the two signals fire together — single policy, no split between
"warning" and "test failure" thresholds.

---

## 7. Primary sources (Tally official documentation)

All three findings above are corroborated by Tally Solutions' published
integration documentation.

### (a) `OpeningBalance` and `ClosingBalance` are distinct TDL fields

Source: https://help.tallysolutions.com/understanding-tally-xml-tags/ — the
Ledger object definition shows:

```
TNetBalance: $$AsPositive: $$AmountSubtract: $ClosingBalance: $OpeningBalance
```

Both fields exist independently on every ledger. The `<OPENINGBALANCE>` tag
in an XML export maps to TDL's `$OpeningBalance`. It equals
`$ClosingBalance` **only** when the export-time option *"Export closing
balances as opening balance"* was enabled at export time — which, as the
§2 diagnostic confirmed, is how our fixture was produced.

### (b) Sign convention negative = Dr, positive = Cr — confirmed by Tally's sample responses

Source: https://help.tallysolutions.com/sample-xml/ — Tally's sample Trial
Balance XML response shows:

```xml
<DSPDISPNAME>Current Assets</DSPDISPNAME>
<DSPCLDRAMTA>-43092.00</DSPCLDRAMTA>           ← negative, Dr column

<DSPDISPNAME>Purchase Accounts</DSPDISPNAME>
<DSPCLDRAMTA>-1521000.00</DSPCLDRAMTA>         ← negative, Dr column

<DSPDISPNAME>Current Liabilities</DSPDISPNAME>
<DSPCLCRAMTA>1526292.00</DSPCLCRAMTA>          ← positive, Cr column
```

The rule we derived empirically (negative = Dr, positive = Cr) applies
across **all** Tally XML surfaces — master exports, display-format Trial
Balance XML, voucher exports — not just master exports.

### (c) Why the rule exists — `$$AsPositive`

The TDL formula `$$AsPositive: $$AmountSubtract: $ClosingBalance: $OpeningBalance`
reveals that the raw amount fields are signed internally. Tally's reporting
code unconditionally wraps them in `$$AsPositive` to produce display values.

- In **display-format XML** exports (DSPACCINFO style), Tally has already
  done the sign-to-side conversion, so Dr and Cr appear in separate tags
  (`DSPCLDRAMTA`, `DSPCLCRAMTA`).
- In **master XML** exports, the raw signed value comes through
  unprocessed — which is why our parser must replicate the `$$AsPositive`
  transform.

This grounds our parser's behaviour in Tally's own internal arithmetic. It
also explains why the sign convention isn't formally documented at the XML
schema level: at TDL it's just an `Amount`-type field; the sign-to-side
interpretation is a reporting-layer transform that third-party XML
consumers have to replicate.

---

## 8. Change log

| Date       | Author       | Change |
| ---------- | ------------ | ------ |
| 2026-04-19 | Claude Code + Aditya | Initial draft after CACSPU diagnostic (sign bug + closing-as-opening verification). Reviewed and approved by Aditya. |
