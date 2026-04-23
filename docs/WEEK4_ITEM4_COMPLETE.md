# Week 4 Item 4 — COMPLETE (2026-04-23)

**TL;DR**: Account Creation Request (ACR) workflow is live end-to-end.
Reviewer can map existing, create new, or reject account-tier decisions.
Generators respect all outcomes. Cascade-revert preserves audit trail
when Accounts/Suppliers are deleted externally. 8-step closing smoke
on real CACSPU data passes; test suite 385 passed, 2 skipped.

Read alongside [`SESSION_HANDOFF_2026_04_19.md`](SESSION_HANDOFF_2026_04_19.md)
for broader Week-4 context and [`mapper_design_notes.md`](mapper_design_notes.md)
for the durable design-decision trail (§5 has the gotcha log, §7 has
the leaf-only / cross-app boundary principles).

---

## 1. The four commits

| Commit | Hash | Scope |
|---|---|---|
| C1 | `c4405a2` | ACR schema + AccountResolutionDialog (UI shell only) |
| C2 | `8de4e4d` | ACR child row creation on dialog submit |
| C3 | `6cbde32` | ACR approval + Reset sweep + opening_je skip + cascade-revert |
| C4 | TBD | Closing smoke + this completion doc |

Branch: `claude/unruffled-hellman-c54c95` on `origin`, synced to worktree
`claude/epic-golick-6ad060` and the dev bench at `erp.jewonline.in`.

---

## 2. Cumulative architectural decisions landed in Item 4

### 2.1 ACR DocType + child-table workflow (parallel to SCR)

`Account Creation Request` is a Frappe child table on
`Tally Migration Session` (field: `account_creation_requests`),
structurally parallel to the `Supplier Creation Request` table from
Item 3. Commit 1 added four persisted fields on `Mapping Decision`
carrying the mapper's pre-parse suggestion
(`new_account_name`, `new_account_parent`, `new_account_root_type`,
`new_account_is_group`); Commit 2 added `account_type` on the ACR
child DocType (AMB C2-A).

### 2.2 AccountResolutionDialog — modal create-new path

UI shell in Commit 1; RPC wiring in Commit 2. Differs from
`SupplierResolutionDialog` in that there's no "Map to existing"
radio — Section 4's Final Account picker already handles that path.
Dialog is create-new only.

**Embedded tree picker** for parent_account selection (AMB-10 option
C, revisited mid-Commit-1 after reviewer feedback on the autocomplete
UX). Custom lightweight widget (`AccountTreePicker`) rather than
Frappe's `frappe.views.TreeView` (the latter is a full-page
controller; fragile to embed in a Dialog). ~230-node tree rendered
client-side with depth-indent, chevron expand/collapse, search with
substring highlight + auto-expand ancestors of matches, and
expand-all/collapse-all toolbar buttons. Auto-scroll to selected on
open.

### 2.3 Server-owned invariants via RPC signature exclusion

`create_account_creation_request`'s whitelist signature deliberately
omits `proposed_root_type` and `proposed_is_group`. Client cannot
smuggle these values — the backend derives root_type from
`decision.new_account_root_type or decision.tally_root_type` (AMB
C2-B) and forces `is_group = 0` (AMB C2-1). Structural tamper-
protection rather than defensive server-side checks.

### 2.4 Bulk ACR approve/reject (Commit 3 Phase D expansion)

`bulk_approve_acrs` / `bulk_reject_acrs` whitelists; panel dialog
Select-All + bulk action bar; per-row error isolation so a single
`DuplicateEntryError` doesn't block the rest. Parallel to
`bulk_approve_scrs` / `bulk_reject_scrs` added for Item 3.

### 2.5 Account/Supplier `on_trash` cascade-revert (Commit 3 Phase D expansion)

`doc_events.Account.on_trash` → `rgi_migration.hooks_impl.clear_account_links`;
same for `Supplier.on_trash` → `clear_supplier_links`. Before
Frappe's `check_if_doc_is_linked` runs (verified ordering in
`frappe/model/delete_doc.py:140-180`), the hook:

* Deletes ACR/SCR child rows whose `created_account`/`created_supplier`
  pointed at the deleted doc.
* Reverts every Mapping Decision whose `final_account` /
  `proposed_account` / `final_supplier` / `proposed_supplier`
  referenced the deleted doc: nullifies the Link, flips
  `review_action` back to `Pending` if it was approved-like,
  restores the mapper-authoritative `tier` using the
  `new_account_name` / `new_supplier_name` heuristic (populated by
  mapper at parse time, never mutated post-parse — a reliable
  retroactive marker of original tier).
* Appends a chronology-header note to `reviewer_notes` so audit
  trail is preserved textually even after Links are gone.

Reviewer experience: delete Account from COA → source MD re-enters
the Pending filter → press `c` again, fresh ACR cycle works with no
duplicate refusal.

### 2.6 Reset Parse wipe-all-statuses sweep for SCR + ACR (AMB-8 closure)

`reset_parse` now sweeps both `supplier_creation_requests` and
`account_creation_requests` child tables alongside deleting Mapping
Decisions. Wipe-all-statuses semantic (AMB C3-7): Reset is a
"start over" action; Created/Skipped/Failed orphan rows are all
equally stale once their source MDs are deleted. Return shape
extended with `scr_swept` + `acr_swept` counts for toast visibility.
Closes AMB-8 — validated in Commit 4 smoke Step 8 (reset_parse
swept 5 ACR rows across all statuses cleanly).

### 2.7 `opening_je` silent-skip for Rejected refusal tiers

`opening_je._select_contributions` now skips (not refuses) rows
where `tier ∈ {unmapped, pending_account_creation}` AND
`review_action = Rejected`. Parallel to `oit_csv` / `advance_je`'s
Item 3 Commit 3 treatment for supplier-side Rejected rows. Scoped
to reviewer-actionable refusal tiers — `group_refused` /
`anti_pattern_blocked` still refuse even when Rejected (those are
mapper-structural, not reviewer-dismissible).

### 2.8 Refactors inside Item 4

* `_append_scr_error_log` generalized to `_append_error_log_block`
  (SCR alias preserved for backward compat). Both SCR and ACR
  approval paths share the helper.
* `_SCR_ACTION_ALLOWED_STATUSES` / `_TERMINAL_STATUSES` hoisted to
  generic `_APPROVAL_*` names (both SCR and ACR share lifecycle).

---

## 3. Line-count retrospective

Net changes across all four commits (per `git show --stat`):

| Commit | Files | Insertions | Deletions | Net |
|---|---:|---:|---:|---:|
| C1 `c4405a2` | 10 | 1,938 | 7 | +1,931 |
| C2 `8de4e4d` | 12 | 922 | 54 | +868 |
| C3 `6cbde32` | 11 | 2,004 | 25 | +1,979 |
| C4 (smoke+docs) | ~5 | ~1,050 | ~50 | ~+1,000 |
| **Total** | **~38** | **~5,914** | **~136** | **~+5,778** |

C3 was the heaviest — ACR approval workflow (approve_acr /
reject_acr / list_pending_acrs + bulk variants + panel dialog +
generator silent-skip + cascade-revert hooks + 500+ lines of
tests). C4 is mostly smoke + this doc.

Test suite: started Item 4 at 324 passed; ended at 385 passed
(+61 net across the 4 commits). Zero regressions introduced;
two failures caught and resolved in C2 (`[^}]*` regex collision
with JS comment containing `{...}`) and C3 (`_SCR_ACTION_ALLOWED_STATUSES`
renamed — test updated to match `_APPROVAL_*` hoist).

---

## 4. Closing smoke outcome matrix (Commit 4)

8 steps exercised end-to-end on the live CACSPU bench
(`TMS-CACSPU--00495`, session status = Reviewing):

| Step | Status | Result |
|---|---|---|
| 1 Baseline | ✅ | `reset_parse` + `run_mapper` → 1,939 decisions, 21 unmapped baseline |
| 2 Seed 4 rows | ✅ | A=Map / B=ACR-approve / C=ACR-reject / D=direct-reject |
| 3 Bulk ACR | ✅ | 3 ACRs, `bulk_approve_acrs` → ok=3/failed=0 |
| 4 Cascade-revert | ✅ | Account delete → MD reverted + fresh ACR succeeded; Supplier delete → final_supplier nullified with chronology note |
| 5 State verification | ✅ | All 4 seeded rows match expected tier + review_action + final_account |
| 6 opening_je refusal | ✅ | 15 ledgers unmapped (= baseline 21 − 6); Row A invariant held per deferred tier-auto-lift |
| 7 Supplier generator regression | ✅ | `oit_csv` + `advance_je` both refuse with "12 suppliers pending creation" — `_append_error_log_block` refactor did NOT regress SCR path |
| 8 Idempotency | ✅ | `reset_parse` swept 5 ACR rows (wipe-all-statuses); re-run baseline → 21 unmapped (no drift) |

Full output captured in Commit 4 PR / session transcript.

---

## 5. Carry-forward deferred items

Still open in [`WEEK4_DEFERRED_ITEMS.md`](WEEK4_DEFERRED_ITEMS.md)
after Item 4:

* **Loader-time tier auto-lift when `final_*` is set.**
  Pending explicit design discussion. Currently, `save_decision`
  (Section 4 Map-to-existing path) writes `final_account` +
  `review_action = Approved` but does NOT lift `tier`. Row A in the
  closing smoke exercises this and stays in the refusal bucket.
  Closing this item would flip the Item 4 smoke's Step 6 arithmetic
  from `baseline - 6` to `baseline - 7`; invariant probe + remedy
  note already in place in the smoke script.
* **Phase C deployment playbook — HUP gunicorn.** QoL; gotcha
  canonical in `mapper_design_notes.md` §5, deferred item is about
  folding HUP into the standard deploy recipe.
* **`create_doctypes.py` scaffolder drift.** Pre-Item-9
  fresh-bench bootstrap test.
* **Session DocType `erpnext_company` backfill.** One-line default
  on insert; pre-rollout checkpoint.
* **Pre-rollout bench COA hygiene audit** (new, raised in C4).
  Scratch / test accounts on CACSPU surfaced by first-alpha picker;
  flag for Item 9 rollout playbook.

---

## 6. What's NOT in Item 4 (deferred to Items 5-9)

* **Item 5** — Reviewer-promotion to `Mapping Rule` / `Supplier Alias
  Rule`. Lets an approved decision's mapping crystallise into the
  rule library, so the next entity's mapper auto-resolves the same
  pattern. "Rules library compounds" architectural principle.
* **Item 6** — Tier-2 fuzzy matching via `rapidfuzz`. Currently
  supplier resolution uses rapidfuzz at Tier-1 layer 3; Tier-2
  would add fuzzy matching for account-side names.
* **Item 7** — `fiscal_year_short` `before_insert` hook +
  `erpnext_company` auto-populate from `company_abbr`.
* **Item 8** — `FrappeRuleSource` + `FrappeSupplierSource`. Runtime
  dependency-injection so mapper reads rules directly from Frappe
  DocTypes rather than a JSON file.
* **Item 9** — End-to-end CACSPU production migration. First real
  entity run of the entire pipeline against clean production
  COA + Supplier masters.

---

## 7. Next-session handoff

Fresh session recommended for Item 5. Starting state:

* Branch `claude/unruffled-hellman-c54c95` at Commit 4 hash
  (see commit log for latest).
* Bench `erp.jewonline.in` synced; `TMS-CACSPU--00495` in Reviewing
  status with 21 unmapped + 12 pending_supplier_creation as
  baseline.
* Test suite: 385 passed, 2 skipped.
* Item 5 opens new design space: promotion flow from approved
  decision → new `Mapping Rule` or `Supplier Alias Rule` row,
  with reviewer-facing UI for confirming the generalisation
  pattern (exact vs regex vs contains, which entity types it
  applies to, etc.). Phase A should scope carefully — this has
  more design-surface than Item 4 had.
