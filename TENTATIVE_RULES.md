# Tentative / Skipped Rules

Items from `RGI_Migration_Rules.md` that `scripts/seed_mapping_rules.py`
did NOT insert into the `Mapping Rule` DocType. Each carries a documented
reason (structural check, process rule, or genuinely tentative). Promote
manually during reviewer sign-off in a session if needed.

Companion: [`docs/mapper_design_notes.md`](docs/mapper_design_notes.md) —
the anti-pattern seeding test and Tier-1 structural-check list explain
why several §11 rows are code, not data.

## Structural checks (encoded in Tier-1 code, not data)

### §11 R5 — Post group account as opening balance target

Encoded as the Tier-1 group-account refusal validator. See docs/mapper_design_notes.md §2(b).

### §11 R6 — Include P&L accounts in opening JE

Encoded as the Tier-1 P&L root-type exclusion + JE-builder filter + ERPNext Opening Entry submission check (three layers). See docs/mapper_design_notes.md §2(a).

### §11 R7 — Post Purchase Accounts (Tally group) as asset

Parser tags every Purchase Accounts leaf as root_type=Expense; JE builder filters it out; ERPNext Opening Entry rejects P&L at submission. The anti-pattern would guard an impossible failure mode. See docs/mapper_design_notes.md §1 Worked Example A.

### §11 R10 — Use FDR Canara Bank group for FDR posting

Encoded as the Tier-1 group-account refusal validator — the same code path that catches any group-account proposal, not just the FDR case. See docs/mapper_design_notes.md §1 Worked Example B and §2(b).

## Process rules (OIT/JE builder invariants, not mapping data)

### §11 R1 — Post to Sundry Creditors directly via JE without party tagging

Not a Tally→ERP name mapping; governed by the OIT/JE builder per §5.1–§5.3 (net-Cr → OIT, net-Dr → party-wise JE).

### §11 R8 — Net Dr vendor in OIT

OIT builder invariant per §5.3 (net-Dr vendors go through party-wise JE, not OIT). Not mapping data.

### §11 R9 — Post parent group total AND child rows (double count)

JE builder emits only leaves per §7.4; group totals are never posted. Not mapping data.

## Tentative / out-of-scope for §4 seed

### §5.6 — Cash Purchases in Sundry Creditors

Status=TENTATIVE. §5 is OIT/party process rules, not a Mapping Rule data source. Revisit during reviewer promotion.
