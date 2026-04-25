# Week 4 — deferred items

Running list of items surfaced during Week 4 review-UI implementation
that were scoped out of the commit they were found in, but need to
land in a subsequent commit before Week 4 is declared complete.
Keep entries short; when a deferred item lands, delete its section
here and reference the landing commit in the change log.

---

## Future design discussions

### Fuzzy Review Workbench — DEFERRED (trigger: post-Item-9)

**Raised:** Item 6 Phase D browser verification (2026-04-24).

**Status:** Per-row `FuzzyMatchApprovalDialog` shipped in Item 6 as v1.
Reviewer sees one dialog per `tier2:fuzzy_classical` match on row
select — y/n/o Accept/Reject/Pick-different. Works; not optimized
for batch decisions.

**What's deferred:**
- Single "Review All Fuzzy Matches" view showing a table of all
  pending fuzzy matches in one session
- Per-row inline account picker (editable without closing the view)
- Checkbox-based bulk selection + bulk Accept / Reject actions
- Per-row state change (e.g., bulk-pivot selected rows to
  Request Creation workflow)
- Possibly: bulk Approve & Promote in one action

**Why deferred:**
- Genuinely new UX surface requiring its own Phase A design
  (inline picker architecture, bulk state-change semantics,
  Approve & Promote integration, navigation from md-review,
  keyboard flow across rows, scale handling at 15+ rows × ~700
  accounts per Company COA)
- Per-row dialog is usable v1 (~45 seconds reviewer time per
  entity for 15 matches at CACSPU scale); not blocking production
  readiness
- Scope discipline — pulling it into Item 6 mid-Phase-D would
  have unbound that item

**Trigger to open:**
- After Item 9 (CACSPU end-to-end) surfaces real reviewer
  experience at production scale.
- If Item 9 reviewer feedback says per-row dialog is meaningfully
  painful, open as Item 6.5 or later item with proper Phase A.
- If Item 9 reviewer feedback says per-row dialog is tolerable,
  stays deferred indefinitely — not required for 59-entity
  rollout.

**Estimated effort if tackled:** 5-8 hours (Phase A design
substantial; Phase B moderate; Phase C programmatic smoke
lighter; Phase D browser verification meaningful). Sizing
comparable to Item 6 total work.

Not a bug. Not blocking. Explicitly deferred by design.

---

### Item 8 timing gate — promoted rules stay inert until FrappeRuleSource lands

**Raised:** Item 5 Commit 1 Phase A (2026-04-23).
**Decision:** Phase A A-0 chose **α** (DocType-write only, inert until
Item 8).

**What this means:** reviewer promotions via the Item 5 Commit 1
"Approve & Promote" action write `Mapping Rule` rows to the DocType
but the live mapper continues reading from `docs/seed_plan.json`
through `JsonFileRuleSource`. Promoted rules are visible in Frappe
(browsable, linkable from the Mapping Decision detail pane) but do
NOT affect the next entity's `run_mapper` invocation until Item 8
replaces `JsonFileRuleSource` with a DocType-backed `FrappeRuleSource`.

The integration test `test_rule_promotion.py::
test_promoted_rule_does_not_appear_in_json_rule_source` codifies this
boundary. The test is future-forcing: when Item 8 lands, this test
SHOULD fail, and the Item 8 author should update it to codify γ
(DocType-backed live matching) rather than silencing.

**Tripwire:** if Item 8 has not shipped by the time CACSPU is
production-migrated (Item 9), **γ becomes mandatory before entity 2
starts**. Rolling out 59 entities while every reviewer's promotions
sit inert defeats the "rules library compounds" principle — the
second entity would be mapping with identical tier-1 coverage to the
first, and reviewers would compound the same corrections 59 times.

**Action at tripwire:** pull forward the `FrappeRuleSource`
implementation from Item 8 into the Item 9 migration scope. Minimum
viable γ is a read-path-only `Mapping Rule` DocType source (no
schema changes required — the fields already exist).

Not blocking Item 5 Commit 1 ship. Logged here so the pre-Item-9
checkpoint flags it explicitly.

---

### Loader-time tier auto-lift when `final_*` is set

**Raised:** Item 2 Commit 4.2 smoke-design (2026-04-22).

Potential enhancement: `decision_from_doc_row` could infer a lifted
`tier` when the reviewer has supplied a `final_*` field. Concretely,
when `final_account` is non-null and persisted `tier == "unmapped"`,
return `tier="tier1_exact"`. When `final_supplier` is non-null and
persisted `tier == "pending_supplier_creation"`, return
`tier="tier1_supplier_exact"`. Same for the other refusal tiers.

**Why it's interesting:** generator refusal counts today stay pinned
to mapper-authoritative tier even after reviewer picks a final
target. Auto-lift at read would make refusal counts drop in response
to reviewer edits without requiring the full Items 3/4 approval
workflow on the critical path.

**Trade-off:** `tier` becomes a **derived** field at read time rather
than a mapper-authoritative audit field. Two readers of the same
DocType row see different tiers. Breaks the invariant that the
persisted row is the source of truth.

**Scope status:** NOT in any current Item. Deferred pending explicit
design discussion. Items 3 (supplier) and 4 (account) are currently
expected to carry tier transitions as part of their approval
workflows — if those workflows land cleanly, loader-time auto-lift
may be moot.

---

## Pre-rollout bench COA hygiene audit

**Raised:** Item 4 Commit 4 closing smoke (2026-04-23).
**Target:** Before Item 9 (end-to-end production migration) /
before 59-entity rollout training.

**Problem:** The `scripts/item4_closing_smoke.py` picker
`_pick_existing_account` (used for Row A's Map-to-existing target)
selects the first alphabetically-sorted enabled leaf Account in the
session's Company + root_type branch. On CACSPU's dev bench this
landed on `234567891234566 - Trial 34 - CACSPU` — a literal scratch
/ test Account someone created during bench setup and never cleaned
up.

The smoke's correctness is unaffected (picker is deterministic,
assertions all pass), but the same first-alpha behavior surfaces
more broadly: the AccountResolutionDialog's parent-picker
autocomplete also orders by alpha at the top of each depth bucket,
so a test Account under "Application Of Funds(Assets)" would
appear as the FIRST option reviewers see. That's confusing in
training, and in the worst case a reviewer could mistakenly select
it as a parent.

**Fix shape:** pre-rollout audit of each entity's COA for scratch
/ test accounts. Candidates for cleanup:

- Accounts with non-word prefixes (digits-only, punctuation leading)
- Accounts with "Test", "Trial", "Temp", "XX" in the name
- Accounts with zero transactions and creation timestamps during
  bench-setup windows

Not a blocker for Item 4 closure. Flag for Item 9 scope and for
whoever runs the 59-entity rollout playbook.


## Phase C deployment playbook — HUP gunicorn workers after `bench build`

**Raised:** Item 4 Commit 1 Phase D (2026-04-23).

**Problem:** `bench build --app X` + `bench --site Y clear-cache` does
NOT refresh Python module imports held by preloaded gunicorn workers.
Any commit that adds a new whitelist method or modifies `.py` imports
will appear to deploy cleanly (pytest green on server, bench console
resolves names) but fail in the browser with `AttributeError: module
... has no attribute '<fn_name>'` on 500.

**Already documented** in `docs/mapper_design_notes.md` §5
"Gunicorn `--preload` + stale Python module cache" — the gotcha and
`kill -HUP <master-pid>` fix are canonical there. The deferred item
here is: **fold the HUP step into the standard Phase C deployment
recipe** so reviewers/operators don't rediscover it each time.

**Proposed recipe (update in session handoff docs + commit-2/3
Phase C checklists):**

```bash
scp <modified files> frappe@...:~/frappe-bench/apps/rgi_migration/...
ssh frappe@... "cd ~/frappe-bench && \
  bench execute rgi_migration.rgi_migration.setup.create_doctypes.<patch> && \
  bench build --app rgi_migration && \
  bench --site erp.jewonline.in clear-cache && \
  kill -HUP \$(pgrep -f 'gunicorn.*--preload' | head -1)"
```

The HUP step is a no-op on non-preload gunicorn configs; safe to
always include. Not a blocker — just QoL for deployment ergonomics.

---

## `create_doctypes.py` scaffolder drift

**Raised:** Item 2 Commit 1 (2026-04-22). **Target:** before Item 9
fresh-bench bootstrap test.

The Week-3 `rgi_migration/rgi_migration/setup/create_doctypes.py`
scaffolder is stale vs. the live DocType JSON. Drift known to include
(at least): Mapping Decision still lists `istable=1`, misses session
Link + naming_series + requires_combine + combine_with + the 4 Item-2
supplier fields; Tally Migration Session shell excludes post-Week-3
fields added via direct JSON edit. JSON is authoritative under
`bench migrate`, so no runtime impact today — but a fresh-bench
bootstrap run from this scaffolder would produce a stale schema.
Reconcile (or deprecate the scaffolder in favour of fixture-based
bootstrap) before Item 9 end-to-end test on a clean bench.

**Landed in Commit 6 (Item 1 polish):**

- Path-B bulk auto-approve tier-1 matches (`bulk_approve_tier1`
  whitelist + `Bulk-Approve Tier-1` inner button + dry-run preview
  + summary toast / partial-failure dialog).
- Switch Session dropdown + last_session redirect on no-segment
  route (§1.1 OQ5 resolution).
- `?` keyboard shortcut → custom shortcut-reference dialog
  grouping bindings by §1.6 Action / Navigation / Editing.
- §1.9 distinct empty states — case 1 (session not found),
  case 2 (zero parsed decisions), case 3 (all resolved on default
  preset), case 4 (filter eliminated everything) each get their own
  copy + CTA.
- §1.11 prose fix — "column 5" → "column 6" for the Tier chip.

---

## Session DocType: populate `erpnext_company` from `company_abbr`

**Raised:** Commit 4b Phase C browser verification (2026-04-22).

**Problem:** Existing test session `TMS-CACSPU--00495` had
`erpnext_company` NULL (only `company_abbr = "CACSPU"` populated).
Section 4's `final_account` Link needed the full Company name to
filter autocomplete, so it failed with an empty-company error until
the session was manually patched. Verify the session-insert path
(form creation, setup scripts, test fixtures) always populates
`erpnext_company` via a `Company` lookup on `abbr`.

**Not blocking Commit 4b** — the one test session was patched in
Phase C sync; future session inserts need verification, not code
changes yet.

---

## Supplier and Customer mapping UI — Items 3-4 scope

**Raised:** Commit 5 Phase D verification (2026-04-22).

**Observation:** The review page currently exposes only a `final_account`
Link (Chart of Accounts) in Section 4. But Mapping Decisions target
three distinct ERPNext entities based on category:

1. **Account ledgers** → map to ERPNext Account from COA. Current UI
   handles this correctly.
2. **Supplier ledgers** (tier `pending_supplier_creation`,
   `tier1_supplier_fuzzy`, Sundry-Creditors descendants) → map to
   ERPNext Supplier records. Account head is auto-derived from the
   Supplier's default payable (Sundry Creditors) at transaction time.
3. **Customer ledgers** (student deposits, Sundry-Debtors descendants)
   → map to ERPNext Customer records. Account head is auto-derived
   from the Customer's default receivable (Sundry Debtors).

**Current v1 behavior:** supplier / customer rows can only be handled
via **Request Creation** (flags as `Pending Supplier Creation` /
`Pending Account Creation`). No path to resolve to an *existing*
Supplier or Customer record.

**Design intent confirmed by Aditya:**

- Vendors are mapped via Supplier record; head auto-derives from
  Sundry Creditors on the vendor's default-payable setting.
- Customers are mapped via Customer record; head auto-derives from
  Sundry Debtors on the customer's default-receivable setting.
- Reviewers do NOT need to pick account heads for supplier/customer
  decisions — that's computed at generator time.

**For Items 3-4 scope:**

- **Item 3 (Supplier Creation Request workflow)** extends the review
  page to support supplier-target decisions:
  - Detect target category from `tier` (supplier-tier set already
    lives in `DetailPane.SUPPLIER_TIERS`).
  - Render a **Final Supplier** Link picker (scoped to session's
    Company) in place of Final Account for supplier rows.
  - Approve & Next on a supplier row saves `final_supplier` instead
    of `final_account`.
  - Backend `save_decision` accepts either field, validates per
    category.
- **Item 4 (Account Creation Request workflow)** handles the Chart of
  Accounts side — full Account Creation Request DocType + stub dialog
  replacement for the current `frappe.confirm`.
- **Customer workflow** isn't currently scoped to an Item. Either (a)
  fold into Item 4 as Item 4b, or (b) new Item. Raise when CACSPU
  reviews surface a customer row that needs live resolution.

**Schema implications:**

- `Mapping Decision` needs a `target_doctype` discriminator field
  (Select: `Account` / `Supplier` / `Customer`) derived from `tier`.
- New Link fields alongside existing `final_account`:
  `final_supplier` Link → Supplier, `final_customer` Link → Customer.
- Generator code reads the correct `final_*` field based on
  `target_doctype`.
- Schema migration for existing Mapping Decisions: backfill
  `target_doctype` from `tier`.

**Not blocking Commit 5** — supplier rows can still be Deferred or
Request-Creation'd through the current UI. Items 3-4 build the full
resolution workflow; Commit 5's action suite applies to all three
categories uniformly (Defer / Reject / Request Creation / Save
Without Advance all work regardless of target type).

---

## Backlog beyond Item 9

Captured 2026-04-23 during Item 6 kickoff ordering discussion. One-line
entries only; proper Phase A for each when its turn comes. Ordering and
final scope TBD per-item when each opens.

### Item 10 — Tier-3 Claude API (AI matching)

Originally Week 5+ scope per CLAUDE.md three-tier architecture +
`docs/WEEK3_COMPLETE_2026_04_20.md` §10. DocType schema already reserves
the `tier3_claude` enum value on Mapping Decision `tier` field and the
`tier3_claude_count` counter on Tally Migration Session
(`rgi_migration/rgi_migration/setup/create_doctypes.py:270`, `:435`).
Triggers when Tier-2 fuzzy can't resolve — judgment-call residuals.

### Item 11 — Customer workflow (Sundry Debtors)

Supplier workflow (Item 3) + Account workflow (Item 4) exist; Customer
equivalent is unscoped to any current Item (see "Supplier and Customer
mapping UI" section above). Student-deposit / debtor rows today can
only be Deferred or Request-Creation-able. Parallel architecture to
Item 3 expected — `final_customer` Link field, Sundry-Debtors default-
receivable auto-derivation at generator time.

### Item 12 — Slim TDL / bookkeeper-facing Tally export template

Originally Week 3 Work Item 8 per
`docs/SESSION_HANDOFF_2026_04_19.md:71`. Design in
`docs/slim_tally_export.md`. Deferred end-of-Week-3, never built.
**Numbering collision**: this is the Week-3 "Item 8," NOT the current
Item 8 (FrappeRuleSource / FrappeSupplierSource). Disambiguate as
"Item 12 (née Week-3 Item 8)" in future references.

### Item 13 — Pre-rollout hygiene

Bundle required before 59-entity production rollout:
- `create_doctypes.py` scaffolder reconcile (stale vs. live DocType JSON
  — see "`create_doctypes.py` scaffolder drift" section above)
- Bench COA hygiene audit (scratch / test accounts cleanup — see
  "Pre-rollout bench COA hygiene audit" section above)
- HUP-gunicorn deployment playbook formalization (fold into standard
  recipe — see "Phase C deployment playbook" section above)


### Approve & Next button semantic — saves dropdown value, doesn't hardcode Approved

**Surfaced:** Item 8.5 Stage 3 Phase D walkthrough B6 (2026-04-25).
Reviewer clicked "Approve & Next" on a Deferred row expecting the
review_action to flip to Approved. Instead the saved value matched
the dropdown's current value (still "Deferred"), updating only the
modified timestamp without semantic effect.

**Existing behavior (intentional, Stage 1+2):**
`md_review.js:_onApprove` validates then calls `saveDecision`, which
reads `review_action` from the dropdown's current value
(`section4_controls.review_action.get_value()`). The reviewer is
expected to manually set the dropdown to "Approved" / "Manual
Override" / "Rejected" first, then click the button. This allows
flexibility (a Manual Override decision still uses the same button).

**UX gap:** The button label "Approve & Next" promises a specific
action; a reviewer with intuition that the click implies Approved
gets a silent no-op. Friction during 59-entity rollout where
reviewers will routinely transition Deferred → Approved.

**Trigger to revisit:** Reviewer feedback during real CACSPU
migration, or 59-entity rollout discipline review.

**Fix options (decide later):**
1. Hardcode `review_action = "Approved"` inside `_onApprove` before
   `saveDecision` (mirrors fuzzy-match accept handler at
   `md_review.js:1225`). Lose the "use the button to save Manual
   Override" affordance.
2. Rename the button to "Save & Next" and add a separate "Mark
   Approved" shortcut that hardcodes the value.
3. Keep current behavior; add a tooltip or inline note clarifying
   that the dropdown drives the save.

No work today; out of Stage 3 scope.


### md-review lock icon — Unicode emoji polish

**Surfaced:** Item 8.5 Stage 3 Phase D extension S3 (2026-04-25).
Lock icon for Pass-Submitted-stamped MDs uses the Unicode glyph 🔒.
Renders correctly on Frappe's default theme on modern browsers.

**Trigger to revisit:** If reviewer reports rendering issues
(grayscale/unsupported emoji rendering on certain browsers/themes)
or wants a more uniform icon style (e.g., FontAwesome `fa-lock` to
match other Frappe UI icons).

**Fix shape:** swap the inline `<span class="md-row-lock">🔒</span>`
for `<span class="md-row-lock"><i class="fa fa-lock"></i></span>` and
update CSS accordingly. CSS `.md-row-lock` already scoped to the
`.master-row.locked` selector, so the swap is local.

**Estimated work:** ~30 min (visual swap + CSS adjustment).


### `requires_combine` / `combine_with` — sibling-completion semantics

**Surfaced:** Item 8.5 Stage 3 Phase A research (R1).

**Status:** Mapping Decision DocType has `requires_combine` (Check)
and `combine_with` (Small Text — CSV of sibling decision idxs)
fields, persisted today but **inert** — no generator reads or
enforces them. `_group_by_account` in `opening_je.py` aggregates by
ERPNext account name only; `combine_with` relationships are
invisible to the JE construction.

**Why it's deferred (not a bug today):** Real CACSPU data hasn't
exercised the sibling-coupling case at scale. Stage 3 multi-pass
adds a new failure mode — a partial Pass 2 resolving one sibling
while another remains Deferred — that the generators handle by
emitting independently (no structural assertion fails). For Pass 1
single-pass migrations, the inertness is a no-op.

**Trigger to revisit:** Real reviewer feedback during 59-entity
rollout where two Tally ledgers MUST be combined into one ERPNext
JE row for a correctness reason that can't be expressed as
"same proposed_account." If that scenario lands, design surface is:
- Generator-side enforcement: refuse generation if any
  `combine_with` link points to a Mapping Decision in a different
  pass (Pass 1 stamped vs. Pass 2 NULL → mismatch)
- md-review surface: indicate "this row is part of a combine group
  with N other rows; resolve all together."
- Semantics question: does combine_with imply same review_action?
  Same proposed_account? Both?

**No work today.** Inert until a real case exercises it.

