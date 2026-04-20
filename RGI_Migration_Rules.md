# RGI Tally → ERPNext Opening Balance Migration Rules
## Machine-Readable Rules Document — Seed Data for `Mapping Rule` DocType
**Version:** 1.0 | **Date:** April 2026 | **Source:** Sessions across GHRF, CACSF, GHRCBM, GHRPSF, GHRPSJC, GHRCE, GHRLC, GHRCEMBA, GHRCEMNMBA, GHRCEMNMCA, GHRCEMN, GHRCEMNDIP, GHRILS, ASSGHS, ASSCOE, ASSHST

---

## SECTION 1 — ACCOUNT NAMING CONVENTIONS

### 1.1 ERP Account Suffix Rule
**Rule:** Every ERPNext account used in opening balance migration must carry the company abbreviation suffix.
**Pattern:** `{Account Name} - {ABBR}`
**Examples:**
- `Cash - GHRF`
- `Income Expenditure A/c - GHRCE`
- `Caution Money £ (Liability) - GHRILS`
**Status:** CONFIRMED | **Source:** All entities

### 1.2 Company Abbreviation Reference — All 59 Operating Companies

#### Ankush Shikshan Sanstha (ASS) — 16 companies
| # | ERPNext Company Name | Abbreviation |
|---|---|---|
| 1 | Ankush Shikshan Sanstha Society | ASS |
| 2 | GH Raisoni College Of Engineering | GHRCE |
| 3 | GHRCE — MBA | GHRCEMBA |
| 4 | Ankush Shikshan Sanstha COE | ASSCOE |
| 5 | Ankush Shikshan Sanstha Hostel | ASSHST |
| 6 | Ankush Shikshan Sanstha Admission | ASSADM |
| 7 | GH Raisoni College Of Engineering And Management Nagpur | GHRCEMN |
| 8 | GHRCEMN — MBA | GHRCEMNMBA |
| 9 | GHRCEMN — MCA | GHRCEMNMCA |
| 10 | GHRCEMN — Diploma | GHRCEMNDIP |
| 11 | GHRCEMN — Phase II | GHRCEMN2 |
| 12 | ASS Boys Hostel Shraddha Park | ASSBHS |
| 13 | ASS Girls Hostel Shraddha Park | ASSGHS |
| 14 | GH Raisoni Institute Of Life Science | GHRILS |
| 15 | ASS For GHRCEM | ASSGHRCEM |
| 16 | GH Raisoni Law College | GHRLC |

#### GH Raisoni Educational And Medical Foundation (GHREMF) — 8 companies
| # | ERPNext Company Name | Abbreviation |
|---|---|---|
| 17 | GH Raisoni College Of Engineering And Management Pune | GHRCEMPU |
| 18 | GHRCEM Pune — MBA | GHRCEMPUMBA |
| 19 | GHRCEM Pune — MCA | GHRCEMPUMCA |
| 20 | GHR CACS Pune | CACSPU |
| 21 | GH Raisoni Public School Pune | GHRPSPU |
| 22 | GH Raisoni Junior College Pune | GHRJCPU |
| 23 | GHREMF Society Pune | GHREMFP |
| 24 | GHREMF Society Nagpur | GHREMFN |

#### GH Raisoni Education Foundation Jalgaon (GHREF) — 7 companies
| # | ERPNext Company Name | Abbreviation |
|---|---|---|
| 25 | GH Raisoni College Of Engineering And Management Jalgaon | GHRCEMJ |
| 26 | GHRCEMJ — Phase II | GHRCEMJ2 |
| 27 | GH Raisoni Public School Jalgaon | GHRPSJ |
| 28 | GH Raisoni Junior College Jalgaon | GHRJCJ |
| 29 | GHREF Society Jalgaon | GHREFSJ |
| 30 | GHREF Society Nagpur | GHREFSN |
| 31 | Sadabai Raisoni Womens College | SRWC |

#### GH Raisoni Foundation (GHRF) — 5 companies
| # | ERPNext Company Name | Abbreviation |
|---|---|---|
| 32 | GH Raisoni College Of Business Management | GHRCBM |
| 33 | GHR CACS Foundation | CACSF |
| 34 | GH Raisoni Public School Foundation | GHRPSF |
| 35 | GH Raisoni Public School And Junior College | GHRPSJC |
| 36 | GH Raisoni Foundation Society | GHRF |

#### Chaitanya Bahuudeshiya Sanstha (CBS) — 3 companies
| # | ERPNext Company Name | Abbreviation |
|---|---|---|
| 37 | Chaitanya Bahuudeshiya Sanstha Society | CBS |
| 38 | GH Raisoni College Of Business Management Khaparkheda | GHRCBMK |
| 39 | GH Raisoni College Of Engineering And Management Amravati | GHRCEMA |

#### SGR Foundation Group — 2 companies
| # | ERPNext Company Name | Abbreviation |
|---|---|---|
| 40 | SGR Foundation | SGRF |
| 41 | SGR Education Foundation | SGREF |

#### GH Raisoni University Amravati (GHRUA) — 3 companies
| # | ERPNext Company Name | Abbreviation |
|---|---|---|
| 42 | GH Raisoni University Amravati | GHRUA |
| 43 | GHRUA — Nagpur Office | GHRUANG |
| 44 | GHRUA — Pune Office | GHRUAPU |

#### GH Raisoni Skill Tech University Nagpur (GHRSTU) — 1 company
| # | ERPNext Company Name | Abbreviation |
|---|---|---|
| 45 | GH Raisoni Skill Tech University Nagpur | GHRSTU |

#### GH Raisoni International Skill Tech University Pune (GHRISTU) — 1 company
| # | ERPNext Company Name | Abbreviation |
|---|---|---|
| 46 | GH Raisoni International Skill Tech University Pune | GHRISTU |

#### GH Raisoni University Saikheda (GHRUS) — 13 companies
| # | ERPNext Company Name | Abbreviation |
|---|---|---|
| 47 | GH Raisoni University Saikheda | GHRUS |
| 48 | GH Raisoni Hospital And Research Centre | GHRHRC |
| 49 | GH Raisoni University Construction Account | GHRUSCON |
| 50 | GH Raisoni University Hostel | GHRUSHST |
| 51 | GH Raisoni University TBIF | GHRUTBIF |
| 52 | School of Nursing Saikheda | SONNRS |
| 53 | School of Paramedical Sciences Saikheda | SOPMS |
| 54 | School of Pharmacy Saikheda | SOPHS |
| 55 | School of Agriculture Saikheda | SOAS |
| 56 | School of Commerce And Management Saikheda | SOCMS |
| 57 | School of Engineering And Technology Saikheda | SOETS |
| 58 | School of Law Saikheda | SOLS |
| 59 | School of Science Saikheda | SOSS |

#### Trust Group Summary
| Trust Group | Count |
|---|---|
| Ankush Shikshan Sanstha (ASS) | 16 |
| GH Raisoni Educational And Medical Foundation (GHREMF) | 8 |
| GH Raisoni University Saikheda (GHRUS) | 13 |
| GH Raisoni Education Foundation Jalgaon (GHREF) | 7 |
| GH Raisoni Foundation (GHRF) | 5 |
| Chaitanya Bahuudeshiya Sanstha (CBS) | 3 |
| GH Raisoni University Amravati (GHRUA) | 3 |
| SGR Foundation Group | 2 |
| GH Raisoni Skill Tech University Nagpur (GHRSTU) | 1 |
| GH Raisoni International Skill Tech University Pune (GHRISTU) | 1 |
| **TOTAL** | **59** |

### 1.3 Tally Company ID Format
**Pattern:** Tally uses `{Account Name}-{ID}` (e.g., `Income Expenditure A/c-2127`, `Caution Money £-2010`)
**Rule:** Strip the numeric suffix (after last hyphen) when matching to ERP account names.
**Anti-pattern:** Do NOT include the numeric ID in ERP account name.
**Status:** CONFIRMED | **Source:** All entities

---

## SECTION 2 — CLASSIFICATION RULES

### 2.1 Leaf vs Group Account Detection
**Rule:** ERPNext only allows posting to leaf (non-group) accounts. Before mapping any account, verify it has zero children.
**Check method:**
```
children = COA[COA['Parent Account'].str.contains(account_name)]
is_leaf = len(children) == 0
```
**Anti-pattern:** Never post to group accounts even if name matches perfectly.
**Status:** CONFIRMED | **Source:** All entities

### 2.2 P&L Account Exclusion
**Rule:** Any account under `Income` or `Expense` root type in ERP is P&L — exclude from opening balance JE.
**Detection:** Trace parent chain. If chain contains `Income` or `Expense` root → P&L → EXCLUDE.
**Common P&L accounts that appear in Tally BS:**
- `Purchase Accounts` group and all children → P&L EXCLUDE
- `Direct Incomes` and all children → P&L EXCLUDE
- `Indirect Incomes` and all children → P&L EXCLUDE
- `Indirect Expenses` and all children → P&L EXCLUDE
**Important exception:** Some accounts are placed under P&L in ERP but carry BS balances in Tally:
- `Hostel Fee A/c` → ERP under Direct Incomes (P&L) but Tally has under Current Liabilities (BS)
- `Summer Term Exam Fee Collection` → ERP under Indirect Incomes (P&L) but Tally has under Sundry Creditors (BS)
- **Action:** Do NOT post to these ERP P&L accounts. Create new liability leaf account instead.
**Status:** CONFIRMED | **Source:** GHRF, GHRCE, GHRCEMBA, ASSCOE, ASSHST

### 2.3 Asset vs Liability Root Type Check
**Rule:** Verify ERP account is on correct BS side before posting.
**Detection:**
- Chain contains `Application Of Funds(Assets)` → ASSET
- Chain contains `Sources Of Funds(Liabilities)` → LIABILITY
- Chain contains `Equity` or `Reserves & Surplus` → EQUITY
**Flag if:** Tally group says LIABILITY but ERP maps to ASSET, or vice versa.
**Status:** CONFIRMED | **Source:** All entities

### 2.4 "Purchase A/c" Suffix = Capitalised Asset
**Rule:** In Tally, fixed assets are sometimes named with "Purchase A/c" or "Purchase" suffix under Fixed Assets group. This is a Tally naming convention — it is a capitalised asset, NOT an expense.
**Pattern:** `{Asset Name} Purchase A/c-{ID}` or `{Asset Name} Purchase-{ID}` under Tally Fixed Assets
**Mapping:** → Same asset account in ERP under Movable/Immovable Properties
**Examples confirmed:**
- `Office Equipment Purchase-1030` → `Office equipment - ASSGHS` (CONFIRMED, ASSGHS)
- `Computer & Accessories Purchase A/c-1006` → `Computer & Accessories - ASSGHS` (CONFIRMED, ASSGHS)
- `Furniture & Fixture Purchase-1016` → `Furniture & fixture - ASSHST` (CONFIRMED, ASSHST)
- `Sports Equipment Purchase-1047` → `Sports equipment - GHRF` (CONFIRMED, GHRF)
- `Equipment Electrical Purchase-1227` → `Electrical equipment - ASSGHS` (CONFIRMED, ASSGHS)
**Anti-pattern:** Do NOT exclude these as P&L purchase accounts.
**Status:** CONFIRMED | **Source:** GHRF, ASSGHS, ASSHST

### 2.5 Tally Immovable Properties Misclassification
**Rule:** Tally sometimes places non-immovable assets under `Immovable Properties` group due to naming error. Always check substance over Tally placement.
**Example:** `Computer & Accessories` placed under `Immovable Properties` in ASSCOE Tally TB → map to `Computer & Accessories - ASSCOE` under Movable Properties in ERP.
**Status:** CONFIRMED | **Source:** ASSCOE

---

## SECTION 3 — BALANCE TREATMENT RULES

### 3.1 Both-Sided Accounts — Net Treatment
**Rule:** When a single Tally account appears with BOTH Dr and Cr amounts in the TB export, compute the NET and post one entry in that direction.
**Formula:** `net = Cr_amount - Dr_amount`. If net > 0 → Cr entry. If net < 0 → Dr entry (use abs value).
**Examples:**
- `Unpaid Expenditure Account` Dr ₹70,800 | Cr ₹14,69,471 → Net Cr ₹13,98,671 → post Cr (ASSHST)
- `Payable A/c-2114` Dr ₹59,637 | Cr ₹28,077 → Net Dr ₹31,560 → post Dr (ASSCOE)
- `Furniture & Fixture` Dr ₹54,83,372.41 | Cr ₹8,41,050 → Net Dr ₹46,42,322.41 → post Dr (ASSHST)
**Status:** CONFIRMED | **Source:** Multiple entities

### 3.2 Net Zero Accounts — Skip
**Rule:** If a Tally account has equal Dr and Cr (net = 0), skip it entirely. Do not post a zero entry.
**Examples confirmed:**
- `Unpaid Expenditure Account-2103` Dr ₹3,09,860 = Cr ₹3,09,860 → SKIP (ASSCOE)
- `Global Education Ltd (Publication Div)` Dr ₹572 = Cr ₹572 → SKIP (ASSHST vendor)
- `Panipat Carpet` Dr ₹9,000 = Cr ₹9,000 → SKIP (ASSHST vendor)
- `Purchase Bills to Come` Dr = Cr → SKIP (GHRF)
**Status:** CONFIRMED | **Source:** ASSCOE, ASSHST, GHRF

### 3.3 I&E A/c Direction Convention
**Rule:** `Income Expenditure A/c` direction indicates surplus or deficit.
- I&E **Cr** = Surplus entity (accumulated profits > losses)
- I&E **Dr** = Deficit entity (accumulated losses > profits)
**Examples:**
- GHRF: I&E Cr ₹3,25,68,379.92 → surplus
- GHRCE: I&E Cr → surplus
- GHRCEMNDIP: I&E Dr → deficit
- GHRILS: I&E Dr ₹1,04,47,916.64 → deficit
- ASSGHS: I&E Dr → deficit
- ASSCOE: I&E Dr ₹39,73,99,701.45 → deficit
- ASSHST: I&E Dr ₹7,82,47,282.12 → deficit
**Status:** CONFIRMED | **Source:** All entities

### 3.4 Depreciation Fund Direction
**Rule:** `Depreciation Fund` is an equity/reserve account.
- Depreciation Fund **Cr** = normal (accumulated depreciation reserve)
- Depreciation Fund **Dr** = abnormal (deficit entity where fund is exhausted)
**Examples:**
- ASSHST: Depreciation Fund Dr ₹3,84,62,910.33 → deficit entity
- ASSGHS: Depreciation Fund Cr ₹8,52,92,594.16 → normal surplus
**Status:** CONFIRMED | **Source:** ASSGHS, ASSHST

### 3.5 Abnormal Balance Treatment
**Rule:** When an account shows a balance in the unexpected direction (e.g., asset with Cr balance, liability with Dr balance), post as per Tally and flag in client email. Do NOT reclassify without client confirmation.
**Examples:**
- `ICICI Bank` (asset account) with Cr balance → post Cr as Tally, flag as possible OD (GHRILS, ASSGHS, ASSCOE)
- `Scholarship & Freeship [2024-25]` (asset) with Cr balance → post Cr as Tally, flag (GHRCEMN, GHRILS)
- `Student Receivable Cybervidya` (asset) with Cr balance → post Cr as Tally, flag (ASSCOE)
- `Equipment Electrical` (asset) with net Cr balance → DEFER, ask client (ASSHST)
**Status:** CONFIRMED | **Source:** Multiple entities

### 3.6 Branch / Inter-Entity Account Treatment
**Rule:** Branch & Division accounts in ERP are always on the **Asset side** regardless of whether Tally shows Dr or Cr.
**Mapping:** Post Dr or Cr as per Tally direction — ERPNext accommodates both for these accounts.
- Branch account Dr in Tally = amount owed TO this entity FROM the branch
- Branch account Cr in Tally = amount owed BY this entity TO the branch
**Status:** CONFIRMED | **Source:** All entities

### 3.7 Temp Opening Balance Calculation
**Rule:** Temp Opening is a balancing account to make JE1 Dr = Cr.
**Formula:**
```
total_dr = sum of all Dr entries in JE
total_cr = sum of all Cr entries in JE
diff = total_dr - total_cr
if diff > 0: Temp Opening = Cr (diff)   # Dr heavy → need Cr balancer
if diff < 0: Temp Opening = Dr (abs(diff))  # Cr heavy → need Dr balancer
```
**Temp Opening parent:** Under Equity in ERP COA (leaf account: `Temporary Opening - {ABBR}`)
**What it represents:** All deferred/pending items not yet posted (vendor OIT, Phase 2 student debtors, deferred branch items, etc.)
**Status:** CONFIRMED | **Source:** All entities

### 3.8 Unpaid Expenditure Both-Sided
**Rule:** `Unpaid Expenditure Account` often appears with both Dr and Cr in Tally TB. Always post net Cr. If net is zero, skip.
**Pattern:** Dr side = reversals/payments; Cr side = new accruals. Net Cr = outstanding liability.
**Status:** CONFIRMED | **Source:** Multiple entities

---

## SECTION 4 — ACCOUNT MAPPING RULES

### 4.1 GHRIET → GHRCE Mapping
**Rule:** All of the following Tally inter-entity accounts map to `GH Raisoni College Of Engineering - {ABBR}` in ERP:
- `G H Raisoni Inst Of Engg & Tech For Women-2330`
- `G H Raisoni Inst Of Engg & Tech For Women` (any variant)
- `GHRIET (For Women)`
- `G H R College Of Engineering-2117`
- `GH Raisoni College Of Engineering` (direct)
**Confirmed across:** GHRCEMNDIP, GHRILS, ASSGHS, ASSCOE, ASSHST
**Note:** When multiple Tally accounts (e.g., GHRIET + GHR College Of Engineering) BOTH map to the same ERP account, COMBINE the amounts.
**Example:** ASSGHS — GHRIET Dr ₹44,01,962 + GHR College Dr ₹19,68,000 = Combined Dr ₹63,69,962 → `GH Raisoni College Of Engineering - ASSGHS`
**Status:** CONFIRMED | **Source:** Multiple entities

### 4.2 Caution Money Mapping
**Rule:** Always use `Caution Money £ (Liability) - {ABBR}` (LIABILITY side, under `Liability For Students`).
**Anti-patterns — DO NOT USE:**
- `Caution Money £ - {ABBR}` → ASSET side (wrong for security deposits collected)
- `Hostel Caution Money - {ABBR}` → LIABILITY but under Other Liabilities (use this only if Caution Money £ (Liability) doesn't exist)
**Condition:** When Tally account is under Current Liabilities and represents student security deposits
**Exception:** If net Dr in liability → post net Dr, flag as abnormal
**Status:** CONFIRMED | **Source:** GHRCE, GHRILS, ASSGHS, GHRCEMBA, GHRCEMNMBA, ASSHST

### 4.3 Kitchen Crockery Mapping
**Rule:** `Kitchen Crockery` or `Kitchen Croceery` in Tally → `Kitchen Equipment (Club House) - {ABBR}` in ERP
**Status:** CONFIRMED | **Source:** ASSHST

### 4.4 Security Deposit MSEB Mapping
**Rule:** `Security Dep With M S E B[{Con No}]` or `Deposit To M S E B` → `Security Deposit MSEB - {ABBR}`
**Note:** Ignore the connection number in the name; it's just an identifier.
**Status:** CONFIRMED | **Source:** GHRF, ASSHST

### 4.5 FDR / Fixed Deposit Naming Pattern
**Rule:** `Canara Bank FDR - {ABBR}` is a GROUP in ERP. The postable LEAF is `FDR Canara Bank - {ABBR}`.
**Pattern:** `{Bank Name} FDR` = Group | `FDR {Bank Name}` = Leaf
**Verification required:** Always check leaf/group status before mapping.
**Status:** CONFIRMED | **Source:** GHRCEMNDIP, GHRILS
**Note:** For GHRILS specifically: leaf `FDR Canara Bank - GHRILS` is under group `Canara Bank FDR - GHRILS`

### 4.6 Unpaid Expenditure Account Mapping
**Rule:** `Unpaid Expenditure Account-2103` in Tally → `Unpaid Expenditure Provision - {ABBR}` in ERP
**Note:** Name differs — match by substance (accrued liabilities not yet paid)
**Status:** CONFIRMED | **Source:** Multiple entities

### 4.7 Payable A/c Mapping
**Rule:** `Payable A/c-2114` in Tally → `Payable Account - {ABBR}` in ERP
**Note:** Tally uses "A/c" abbreviation; ERP uses full "Account"
**Status:** CONFIRMED | **Source:** Multiple entities

### 4.8 Hostel Fee A/c — ERP COA Error
**Rule:** `Hostel Fee A/c` in Tally is under Current Liabilities. In ERP, `Hostel Fee A/c` is WRONGLY placed under `Direct Incomes` (P&L).
**Action:** Do NOT post to `Hostel Fee A/c - {ABBR}` in ERP. Create a new liability leaf account (e.g., `Hostel Fee Advance Payable - {ABBR}` under Other Liabilities or Liability For Students).
**Status:** CONFIRMED | **Source:** ASSHST

### 4.9 Summer Term Exam Fee Collection — ERP COA Error
**Rule:** `Summer Term Exam Fee Collection` in Tally is under Sundry Creditors (Current Liabilities). In ERP, `Summer Term Exam Fee Collection` is WRONGLY under `Indirect Incomes` (P&L).
**Action:** Do NOT post to the ERP P&L version. Use `ASS COE Collection / Payment A/c - {ABBR}` (correctly under Other Liabilities) OR create a new account `Summer Term Exam Fee Payable - {ABBR}`.
**Status:** CONFIRMED | **Source:** ASSCOE

### 4.10 Student Fee Outstanding Classification
**Status:** **Deprecated 2026-04.** `Student Fee Outstanding` and per-student ledgers route through `dux_voucher`'s Ex Student Opening Batch, not through `rgi_migration` rules. The parser flags these ledgers with `is_student_ledger=True` (via parent-chain marker for per-student leaves, or via the `AGGREGATE_STUDENT_ACCOUNT_NAMES` frozenset for aggregate control accounts); they flow to the students CSV and are consumed by `dux_voucher`. See `docs/dux_voucher_integration.md` and `docs/mapper_design_notes.md` §7 "Leaf-only posting principle".

**Historical rule (superseded, kept for audit trail):** `Student Fee Outstanding` in Tally = Current Asset (receivable from students). ERP sometimes placed it under `Liability For Students` (wrong classification); the original rule created `Student Fee Outstanding (Receivable) - {ABBR}` under Current Assets instead. **This rule is no longer seeded into `Mapping Rule` — parser handling supersedes it.**

**Original source:** GHRCEMNMBA, GHRILS (observed when rule was active).

### 4.11 Computer & Accessories
**Rule:** Both `Computer & Accessories` and `Computer & Accessories Purchase A/c` Tally accounts map to `Computer & Accessories - {ABBR}` in ERP.
**Status:** CONFIRMED | **Source:** Multiple entities

### 4.12 Building Revaluation Reserve
**Rule:** `Building Revaluation Reserve (Old)-2161` → `Building Revaluation Reserve ( Old ) - {ABBR}` (note spaces around parentheses in ERP)
**Anti-pattern:** Do NOT map to `Building Revaluation` (plain) — different account.
**Status:** CONFIRMED | **Source:** GHRCE, ASSHST, ASSGHS

### 4.13 Office Equipment Combined
**Rule:** When Tally has BOTH `Office equipment-9014` AND `Office Equipment Purchase-1030`, combine both Dr amounts into single ERP account `Office equipment - {ABBR}`.
**Example:** ASSGHS — ₹2,85,855 + ₹36,27,314.73 = ₹38,13,169.73 → `Office equipment - ASSGHS`
**Status:** CONFIRMED | **Source:** ASSGHS

### 4.14 ASS Boys Hostel
**Rule:** `ASS BOYS HOSTEL-10728` in Tally → `ASS Boys Hostel - {ABBR}` in ERP
**Status:** CONFIRMED | **Source:** ASSGHS

### 4.15 GHR Institute Of Life Science
**Rule:** `G H R Institute Of Life Science-2121` → `GHR Institute Of Life Science - {ABBR}`
**Status:** CONFIRMED | **Source:** ASSGHS, ASSHST

### 4.16 Badminton Court
**Rule:** `Badminton Court-11033` → `Badminton Court (Club House) - {ABBR}`
**Note:** ERP name includes "(Club House)" qualifier
**Status:** CONFIRMED | **Source:** ASSGHS

### 4.17 FD Autonomous Exam Fund
**Rule:** `Fixed Deposit [Autonomous Exam Fund]` in Tally → `FD [Autonomous Exam Fund] - {ABBR}` (new account under Fixed Deposits with Bank)
**Note:** Post as Tally (net Cr if Cr > Dr), flag in email re: escrow vs asset classification
**Status:** CONFIRMED | **Source:** ASSCOE

### 4.18 Student Receivable Cybervidya
**Rule:** `STUDENT RECEIVABLE CYBERVIDYA-11275` → `Student Receivable Cybervidya - {ABBR}`
**Note:** If Cr balance in Tally (asset with Cr = excess collected), post Cr as Tally, flag in email
**Status:** CONFIRMED | **Source:** ASSCOE

### 4.19 Receivable Account Others
**Rule:** `Receivable Account (Others)-1567` → `Receivable Account ( Others ) - {ABBR}` (note spaces around parentheses)
**Status:** CONFIRMED | **Source:** ASSHST

---

## SECTION 5 — PARTY & SUB-LEDGER RULES

### 5.1 Sundry Creditors — OIT vs JE Split
**Rule:** For each vendor under Sundry Creditors:
- Compute net: `net = Cr_amount - Dr_amount`
- If net > 0 (net Cr) → vendor has outstanding payable → **OIT (Opening Invoice Tool)**
- If net < 0 (net Dr) → vendor has advance paid → **Party-wise JE** (Dr to Sundry Creditors with Is Advance = Yes)
- If net = 0 → SKIP
**Status:** CONFIRMED | **Source:** GHRF, GHRCE, ASSGHS, ASSHST

### 5.2 OIT CSV Format
**Columns:** `Invoice Number, Party Type, Party ID, Party Name, Temporary Opening Account, Posting Date, Due Date, Supplier Invoice Date, Item Name, Outstanding Amount, Quantity, Cost Center`
**Party Type:** `Supplier` (for all vendor payables)
**Party ID:** Must exactly match supplier ID in ERP supplier master
**Temporary Opening Account:** `Temporary Opening - {ABBR}`
**Posting Date / Due Date:** `01-04-2026`
**Item Name:** `Opening Invoice Item`
**Outstanding Amount:** Net Cr amount per vendor (positive)
**Quantity:** `1`
**Status:** CONFIRMED | **Source:** All entities

### 5.3 Vendor Advance (Dr Balance in Sundry Creditors) — Party-wise JE
**Rule:** Net Dr vendors go into JE (not OIT) against Sundry Creditors with party tagging.
**JE columns to set:** Party Type = Supplier, Party = vendor name, Is Advance = Yes
**Examples:**
- GHRF: Atharva Tyres Dr ₹25,700, Go Digital Dr ₹1,94,911.73
- ASSHST: S S FOODS Dr ₹25,000, YOCO Stays Dr ₹93,962
**Status:** CONFIRMED | **Source:** GHRF, ASSHST

### 5.4 Sundry Debtors — Always Phase 2
**Rule:** Sundry Debtors (student balances) are NEVER included in JE1. Always deferred to Phase 2 once student masters are confirmed in ERP.
**Exception:** If ALL student balances are NIL (as in GHRILS), no Phase 2 needed.
**Status:** CONFIRMED | **Source:** All entities

### 5.5 Supplier Master Check
**Rule:** Before building OIT, verify every Cr vendor exists in ERP supplier master (`Supplier_List_114.xlsx` or equivalent).
**If missing:** Flag in email — create supplier first, then run supplementary OIT.
**Status:** CONFIRMED | **Source:** GHRCE (3 suppliers missing initially), GHRF

### 5.6 Cash Purchases in Sundry Creditors
**Rule:** `Cash Purchases-SX0001` appearing in Sundry Creditors with Cr balance = unposted cash purchase liability. Include in OIT like any other Cr vendor.
**Status:** TENTATIVE | **Source:** ASSHST

---

## SECTION 6 — OUTPUT FORMAT RULES

### 6.1 JE Import Template Columns (ERPNext v16)
```
Company | Entry Type | Posting Date | Reference Number | Reference Date |
User Remark | Pay To / Recd From | ID (Accounting Entries) |
Account (Accounting Entries) | Account Type (Accounting Entries) |
Against Account (Accounting Entries) | Bank Account (Accounting Entries) |
Credit (Accounting Entries) | Debit (Accounting Entries) |
Is Advance (Accounting Entries) | Party (Accounting Entries) |
Party Type (Accounting Entries)
```
**Key rules:**
- `Company`, `Entry Type`, `Posting Date`, `Reference Number`, `Reference Date`, `User Remark` → only on FIRST row
- `Entry Type` = `Opening Entry`
- `Posting Date` / `Reference Date` = `2026-04-01`
- `Is Advance` = `No` for all rows (except vendor Dr advances = `Yes`)
- Leave empty cells as `None` (not empty string)
- `Credit`/`Debit` = amount if non-zero, `None` if zero
**Status:** CONFIRMED | **Source:** All entities

### 6.2 Reference Number Format
**Pattern:** `OB-{ABBR}-2026-{sequence}`
**Examples:** `OB-GHRF-2026-01`, `OB-GHRCE-2026-01`, `OB-GHRILS-2026-01`
**Sequence:** Starts at 01, increments if multiple JEs needed (JE2 = `-02`, etc.)
**Status:** CONFIRMED | **Source:** All entities

### 6.3 User Remark Format
**Pattern:** `Opening Balance Migration — {Full Company Name} FY 2025-26`
**Status:** CONFIRMED | **Source:** All entities

### 6.4 Temp Opening Row
**Rule:** Always add Temp Opening as the LAST row of the JE.
**Account:** `Temporary Opening - {ABBR}`
**Amount:** Whatever is needed to balance Dr = Cr
**Status:** CONFIRMED | **Source:** All entities

---

## SECTION 7 — VALIDATION RULES

### 7.1 Hierarchy Math Check
**Rule:** Before mapping, verify all Tally parent totals match sum of their children.
- `parent_total == sum(children)` → OK
- `parent_total != sum(children)` → flag for investigation (hidden sub-accounts, export cut issue, rounding)
**Status:** CONFIRMED | **Source:** All entities

### 7.2 JE Balance Check
**Rule:** Final JE must have Dr = Cr to 2 decimal places.
`assert abs(total_dr - total_cr) < 0.01`
**Status:** CONFIRMED | **Source:** All entities

### 7.3 Grand Total Cross-Check
**Rule:** Tally TB Grand Total (both sides equal) should equal sum of all visible rows. If it doesn't, there are hidden accounts or export truncation.
**Status:** CONFIRMED | **Source:** GHRCE (large entity with 355 leaf accounts)

### 7.4 Double Count Risk Checks
**Patterns that risk double counting:**
1. Same amount appears in BOTH Sundry Debtors AND Branch account (same ₹11.5L in ASSHST)
2. Parent group total and all children both visible → only use children
3. Structural subtotals (e.g., `Immovable Properties`, `Movable Properties`) → never post directly
4. Branch total + individual branch rows both showing → only use individual rows
**Status:** CONFIRMED | **Source:** ASSHST (critical Q1)

### 7.5 Red Flags Triggering Manual Review
| Flag | Action |
|---|---|
| Asset account with Cr balance | Post as Tally + flag in email |
| Liability account with Dr balance | Post net Dr as Tally + flag OR defer |
| Fixed asset with net Cr balance | DEFER, ask client |
| Bank account with Cr balance (non-OD) | Post as Tally + flag, ask if OD |
| SC Dr total ≠ sum of visible vendor Dr amounts | Flag discrepancy before proceeding |
| Both sides present in equity/reserve account | Investigate before posting |
| Same amount in two different account groups | Double-count risk — confirm before posting |

---

## SECTION 8 — DEFERRED ITEMS PATTERNS

### 8.1 Always-Deferred Items
| Item Type | Why Deferred | Resolution Path |
|---|---|---|
| Student Debtors (Sundry Debtors) | Need student-by-student breakup | Phase 2 OIT after student master confirmed |
| Missing ERP accounts | Account doesn't exist yet | Client creates account, then JE2 |
| Unreconciled Balance A/c | Tally cleanup item, no clear ERP mapping | Client confirms treatment |
| SBI/SSBL CA with large Cr balance | OD vs asset classification unclear | Client confirms OD or not |
| FD with Cr balance | Escrow vs asset classification unclear | Client confirms nature |
| GHRIET-2 (small entity) | No ERP account exists | New account creation needed |
| GHRCE-ANKUSH SHIKSHAN SANSTHA | No ERP account match | Client confirms mapping |
| Building Revaluation Dr (Fixed Asset) | Only one ERP account (Equity) exists | New Fixed Asset account needed |
| Caution Money net Dr in liability | Abnormal direction | Client confirms |
| Hostel Fee A/c (Cr balance) | ERP account is P&L (wrong) | New liability account needed |

### 8.2 Email Items per Entity Type
**Always include in email:**
- Any ICICI bank account with Cr balance (confirm OD or credit balance)
- Any vendor or branch not matching ERP (confirm mapping)
- Phase 2 student debtors (provide Tally export instructions)
- Any Unreconciled Balance accounts

---

## SECTION 9 — ENTITY-SPECIFIC QUIRKS

### GHRF (GH Raisoni Foundation Society)
- Has `Chaitanya Bahudeshiya Sanstha` — in Tally under Unsecured Loans (Liability) but ERP has under Branch & Division (Asset) — classification conflict
- `G H R Vidyaniketan` — missing from ERP entirely (large balance ₹17.66 Cr)
- `ICICI Bank A/c 624201034837` has Cr balance (OD)
- `Purchase Bills to Come` = Dr and Cr equal → net zero, skip
- `Corpus Fund` equity account present (most entities don't have it)

### CACSF (GHR CACS Foundation)
- Has `Canara Bank FDR` accounts (multiple FDR numbers)
- OIT contribution creates additional Dr on Temp Opening (by design — each OIT PI debits Temp Opening)
- Sundry Debtors has both Dr and Cr (student advances mixed with fee receivables)

### GHRPSF (GH Raisoni Public School Foundation)
- Building Revaluation appears TWICE — both as Equity Cr AND Fixed Asset Dr
- Only ONE ERP account `Building Revaluation Reserve (Old)` exists
- Resolution: Create `Building Revaluation (Asset) - GHRPSF` under Immovable Properties

### GHRCE (GH Raisoni College Of Engineering) — Largest entity
- 355 Tally leaf accounts, 122 ERP leaf accounts after JE1
- Required JE1 through JE5 + multiple supplementary OITs
- `Caution Money £ (Liability)` Cr ₹4,18,55,000 — confirmed as standard Liability version
- `Equipment Electrical` Dr ₹9,82,097.88 — exists in ERP as `Electrical equipment`
- EBC Receivable accounts with GHRIET suffix: `EBC Receivable 2021-22(GHRIET)` etc. — separate accounts, not the GHRIET branch
- Scholarship & Freeship has multiple year-wise sub-accounts
- `Ankush Shikshan Sanstha (Society Only)` shows ₹72.9 Cr Dr — entity is major inter-company balance
- Had XML vs XLS format discrepancy (₹1.25 Cr difference in ASS Society Only) — use XLS version

### GHRCEMN (GH Raisoni College Of Engineering N)
- GHRIET (For Women) Dr ₹2,09,67,665.65 → `GH Raisoni College Of Engineering - GHRCEMN` ✅
- In GHRCEMNDIP (Diploma), GHRIET Dr shows as mirror image (reverse direction — inter-entity balance mirrors)

### GHRCEMNDIP (GH Raisoni College Of Engineering — Diploma)
- `V K Surana & Company` OIT Cr ₹2,43,000 (confirmed)
- RAC Exam Cr ₹62,540 deferred
- G H Raisoni Polytechnic Dr ₹2,27,388.46 deferred
- Unreconciled Balance Dr ₹5,000 deferred
- Student Fee Outstanding Dr ₹61,92,369 — ERP places `Student Fee Outstanding` under Liability (wrong) → create new asset account
- FDR DTE Dr ₹53,60,687 → `Fixed Deposit With [DTE] - GHRCEMNDIP`
- GHRIET-2 Dr ₹3,000 — no ERP account

### GHRILS (GH Raisoni Institute Of Life Science)
- ALL Sundry Debtors NIL → no Phase 2 needed
- `Canara Bank FDR - GHRILS` is GROUP; leaf = `FDR Canara Bank - GHRILS`
- ICICI CA 624205020623 Cr ₹4,59,741.76 → posted Cr, flagged
- GHRIET (For Women) Cr ₹13,360 → `GH Raisoni College Of Engineering - GHRILS`
- Library Deposit Dr ₹500 in liability → post Dr as Tally (abnormal)
- University Enrollment Fee Dr ₹6,120 in liability → post Dr as Tally (abnormal)
- Unreconciled Balance Cr ₹50,000 → deferred, email
- 6 vendors for OIT — all confirmed in global supplier master

### ASSGHS (ASS Girls Hostel Shraddha Park)
- Two Tally accounts map to ONE ERP account: `Office equipment-9014` + `Office Equipment Purchase-1030` → `Office equipment - ASSGHS` combined Dr ₹38,13,169.73
- GHRIET (For Women) Dr ₹44,01,962 + GHR College Of Engineering Dr ₹19,68,000 → combined `GH Raisoni College Of Engineering - ASSGHS` Dr ₹63,69,962
- ICICI 624205016711 Cr → posted Cr, flagged
- 9 vendors for OIT — all in supplier master
- GHRCE-ANKUSH SHIKSHAN SANSTHA Dr ₹80,000 → deferred, no ERP account
- GHRIET-2-10732 Cr ₹9,000 → deferred, no ERP account

### ASSCOE (Ankush Shikshan Sanstha COE)
- PURE DEFICIT — I&E Dr ₹39.74 Cr, zero Cr reserves
- `Summer Term Exam Fee Collection` Cr ₹26,67,000 → new account `Summer Term Exam Fee Payable - ASSCOE` created under Current Liabilities
- `Payable A/c-2114` net Dr ₹31,560 → posted Dr to `Payable Account - ASSCOE` (liability with Dr = abnormal)
- `Computer & Accessories` under Immovable in Tally (naming error) → `Computer & Accessories - ASSCOE` under Movable in ERP
- `FD [Autonomous Exam Fund]` Net Cr ₹7,15,19,000 → new account created under assets, posted Cr, flagged
- `SBI CA 3329555815` Net Cr ₹14,43,28,157.53 → DEFERRED (OD vs asset)
- `SSBL CA 101` Net Cr ₹53,97,140 → DEFERRED (OD vs asset)
- `Student Receivable Cybervidya` Cr ₹89,14,871 → posted Cr, flagged
- `Unpaid Expenditure` Dr = Cr = ₹3,09,860 → net zero, SKIP
- No OIT needed (no Cr vendors in Sundry Creditors)
- `ASS COE Collection / Payment A/c` exists correctly under Other Liabilities

### ASSHST (Ankush Shikshan Sanstha Hostel) — PENDING
- ALL 3 equity accounts on Dr side (Building Revaluation Reserve, Depreciation Fund, I&E) — full deficit
- SC Dr discrepancy ₹50,000 (children sum ₹26,18,479 vs stated ₹25,68,479)
- Equipment Electrical net Cr ₹1,16,215 — abnormal (Dr ₹49,472 / Cr ₹1,65,687)
- Caution Money net Dr ₹12,12,582 (Dr ₹14,42,582 / Cr ₹2,30,000) in liability
- Hostel Fee A/c Cr ₹19,000 — ERP under P&L (wrong)
- `Ankush Shikshan Sanstha Hostel-1060` Cr ₹11,50,000 appears in BOTH Sundry Debtors AND ASS Society Only — potential double count
- 27 net Cr vendors via OIT, 2 net Dr vendors via party JE (S S FOODS ₹25,000, YOCO Stays ₹93,962)
- 2 net zero vendors — Global Ed (Pub Div), Panipat Carpet — skip

---

## SECTION 10 — OPEN QUESTIONS / RULES NOT YET ESTABLISHED

1. **GHRIET-2 mapping** — Small Cr balance (₹3,000–₹9,000) appears across GHRCEMNDIP, ASSGHS, ASSHST. No ERP account. Should a standard mapping be created? Or always defer?

2. **GHRCE-ANKUSH SHIKSHAN SANSTHA mapping** — Dr ₹80,000 in ASSGHS. Which ERP account? (`Ass [Controller Of Examination (GHRCE)]` or `ASS COE GHRCE Exam A/c`?)

3. **Bank OD classification** — SBI CA and SSBL CA with large Cr balances. Standard rule needed: if bank CA shows net Cr > certain threshold (say ₹1L), always flag as possible OD?

4. **Building Revaluation Dr under Fixed Assets** — Tally sometimes puts Building Revaluation as Fixed Asset Dr. ERP only has it as Equity. Need standard rule: create new Fixed Asset account or add to Building A/c?

5. **Scholarship abnormal Cr** — Scholarship & Freeship year-wise accounts with Cr balance (asset with Cr = govt refund or excess receipt). Post as Tally or reclassify?

6. **P&L transfer handling** — Current year P&L (Indirect Income/Expenses) needs to be transferred to I&E A/c via separate entry. Is this in scope for migration or separate process?

7. **Student Fee Outstanding** — Multiple entities have it under wrong ERP parent. Need standard: always create `Student Fee Outstanding (Receivable) - {ABBR}` under Current Assets if Tally has it under Current Assets.

8. **University Enrollment Fee** — Appears as Dr in Liability group. Always post as Tally Dr (abnormal) or check if it should be in Current Assets?

9. **Phase 2 student debtors OIT** — What is the correct ERP process? Sales Invoice OIT per student? Or Sundry Debtors JE with party?

10. **Hostel Fee A/c standard** — Should `Hostel Fee A/c` ERP COA be fixed (moved to Liabilities) globally? Or always create a new account per entity?

---

## SECTION 11 — ANTI-PATTERNS (Explicitly Rejected Mappings)

| Anti-Pattern | Why Rejected | Correct Approach |
|---|---|---|
| Post to `Sundry Creditors - {ABBR}` directly via JE without party tagging | Creates control account imbalance | Use OIT for Cr vendors, party-wise JE for Dr vendors |
| Post `Summer Term Exam Fee Collection` to ERP account of same name | ERP account is under P&L (Indirect Incomes) | Use liability account instead |
| Post `Hostel Fee A/c` to ERP account of same name | ERP account is under P&L (Direct Incomes) | Create new liability account |
| Use `Caution Money £` (Asset version) for student deposits | Wrong side — deposits received should be liability | Use `Caution Money £ (Liability)` |
| Post group account as opening balance target | ERPNext rejects posting to group accounts | Always verify leaf status first |
| Include P&L accounts in opening JE | ERPNext rejects P&L accounts in Opening Entry | Exclude and note current year P&L is separate |
| Post Purchase Accounts (Tally group) as asset | These are P&L expenses | Exclude entirely |
| Net Dr vendor in OIT | OIT only accepts positive (Cr) outstanding amounts | Use party-wise JE with Is Advance = Yes |
| Post parent group total AND child rows | Double counting | Post only leaf account rows |
| Use `FDR Canara Bank` group for FDR posting | Group account — not postable | Use leaf `FDR Canara Bank - {ABBR}` |

---

*Document end. Total rules: 60+ confirmed, 10 open questions, 10 anti-patterns.*
*Next update: After ASSHST Q1-Q6 confirmations and JE build.*
