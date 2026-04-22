# Week 4 Review UI — Design Prose

Living document. Each numbered section is the prose design for one
item of the Week 4 migration-critical scope, authored before any
implementation and reviewed before any code lands. Implementation
commits reference the corresponding section number.

Scope call (2026-04-20, Aditya): *"Finish everything necessary for
RGI migration. Enhancements for broader use deferred to post-RGI."*
All sections in this doc must defend their design choices against
that bar — "does a reviewer need this to clear CACSPU's 39 decisions
and the 58 entities to follow?"

### Doc change log

| Date | Change |
|---|---|
| 2026-04-20 | Initial draft of §1 (list view) and §2 (detail view) as two separate Items — see commit `37831ca`. |
| 2026-04-20 | Revised §1 to unified **Mapping Decision Review Page (master-detail)**. Items 1+2 merge into a single deliverable; §2 is retired. Items 3-9 unchanged in numbering. Driver: Aditya's call after research phase showed POS-style custom Page is the right shape for reviewer throughput. Build budget 8-10 hrs across 3-4 sessions. |
| 2026-04-20 | Refinement pass on §1 — 7 tweaks from Aditya's review of `70241e5`, OQ1-6 resolutions embedded in relevant sections, Undo elevated from stretch to v1. Detail in §1.14. Prose now implementation-ready. |
| 2026-04-20 | Route renamed from `/app/mapping-decision-review/...` to `/app/md-review/...` due to Frappe Page controller's hardcoded 20-char runtime autoname truncation (see `mapper_design_notes.md §5`). Python module path similarly shortened to `page/md_review/`. CSS class `.mapping-decision-review-app` retained — describes component purpose, not URL identifier. Functional design unchanged. |
| 2026-04-21 | Pane split ratio tightened from 6/4 to 7/3 during Commit 3 browser verification. Master pane's 7-column text-dense layout benefits materially from the extra horizontal room; detail-pane form (Commit 4) still fits at 30% on typical Desk viewports. Prose §1.2 + §1.10 scope fence updated. One-line CSS change, reversible. |

---

## §1. Mapping Decision Review Page (master-detail)

*Revised 2026-04-20. Status: open for review. No code yet.*

A custom Frappe Page implementing a Gmail-inbox / Linear-issues
style split-pane reviewer workflow. Replaces the standalone-DocType
list + form pattern after Aditya rejected the form-pull-navigation
friction for 39-decision (CACSPU) review sessions scaling up to
~2,300 decisions across the 59-entity migration.

### §1.0 Reviewer narrative — concrete CACSPU session

Aditya has just run parse + map + generate against the CACSPU
`Tally Migration Session`. Generators #1–#4 produce four refusals:
21 unmapped blocking the main JE, 18 pending_supplier_creation
blocking the OIT CSV and party-advance JE. 39 decisions total.

1. Aditya opens the `Tally Migration Session` form for
   `CACSPU-2026-01` in Desk.
2. At the top of the session form, he clicks the **Review
   Decisions** button.
3. Browser navigates to
   `/app/md-review/CACSPU-2026-01`. Page loads in
   ~500 ms — empty page shell first, then master pane fills from
   one `frappe.desk.reportview.get` call, detail pane populates
   with the first row's full doc from `frappe.db.get_doc`.
4. Master pane (left, 60% width) shows the 39 pending rows by
   default — filter bar at top is pre-set to
   `review_action in (Pending, Pending Account Creation, Pending
   Group Account Resolution, Pending Supplier Creation)`. First
   row is highlighted — `Bank of Maharashtra (Cap) - 60451303968`,
   a Tier-1 unmapped bank account with a ₹3.2 lakh Dr balance.
5. Detail pane (right, 40% width) shows:
   - **Tally Context**: full name, parent chain, root type (Asset),
     opening Dr/Cr, signed net, flags.
   - **Mapper Resolution**: tier = `unmapped`, proposed account
     empty, confidence 0.00, no anti-pattern, no matched rule.
   - **Tier-2 Fuzzy Candidates**: empty state "Tier-2 fuzzy
     matching not yet active — placeholder for Item 6."
   - **Reviewer Action** form: `review_action` Select (defaulted
     to `Pending`), `final_account` Link (empty, autocompletes
     scoped to CACSPU accounts), `final_dr` / `final_cr`
     (auto-populated from `opening_dr` / `opening_cr`),
     `reviewer_notes` textarea.
   - **Assignment**: empty, "Assign to..." button.
   - **Audit**: created by Aditya 20 min ago, no modifications.
6. Aditya types `Bank of Maharashtra A/c` in the
   `final_account` field. Frappe's Link autocomplete surfaces
   `Bank of Maharashtra A/c No. - 60451303968 (Capital) - CACSPU`.
   He picks it. `review_action` auto-flips to `Approved` —
   the row was `unmapped` (empty `proposed_account`), so filling
   in a `final_account` is a straightforward approval, not an
   override. `Manual Override` is reserved for the distinct case
   where the mapper HAD a non-empty `proposed_account` and the
   reviewer picks a different `final_account`. See §1.4 Section
   4 for the full state-flip logic.
7. Presses **`a`** (approve + advance) OR clicks the
   **Approve & Next** button. Backend whitelist method validates
   (non-empty `final_account`, non-Pending `review_action`),
   persists the decision; master pane's row indicator flips from
   orange to green, optimistic-UI re-sorts so the next pending
   row takes the top slot; detail pane updates with the next
   decision's doc. Total click-to-next: ~300 ms. A 5-second Undo
   toast with a "Ctrl+Z to undo" hint appears in the page head.
8. Next row is `G H R Education & Medical Foundation Nagpur`
   under `Branch / Divisions`. Aditya recognises it as an
   inter-entity receivable with no existing COA target. Presses
   **`c`** (request account creation) — `review_action` flips to
   `Pending Account Creation`, a dialog asks for the new-account
   parent (defaulted from `tally_root_type`) and `is_group=0`.
   He confirms; save + advance.
9. He works through the list in ~30 min. Occasionally he uses
   `↓` / `↑` to scrub rows without acting (diagnostic skim), or
   clicks a row directly to jump. Mid-session, an
   `aditya@jewonline.in` coworker phone-pings asking about row
   #14 — he copies the URL
   `/app/md-review/CACSPU-2026-01#mapping-decision-0032`
   and shares it; coworker lands on the exact row.
10. Last pending row resolved. Master pane empties to "All
    decisions in filter resolved — regenerate artefacts?" with
    a **Back to Session** CTA. Aditya clicks it, lands on the
    session form, clicks **Regenerate Main JE** — the 4 generator
    buttons run in ~6 sec, produce the updated JE Draft + OIT CSV
    + party JE + students CSV.

That's the loop. 30 min from 39 decisions to 4 reviewable
artefacts. Everything below serves this loop.

### §1.1 Route and entry point

**Route**: `/app/md-review/<session-name>` — session
name as a path segment. On session `CACSPU-2026-01` that resolves
to `/app/md-review/CACSPU-2026-01`. Frappe's Page
framework routes this by registering a Page record with
`page_name = "md-review"` and reading `frappe.get_route()`
for the `<session-name>` suffix.

**Primary entry point**: a **Review Decisions** inner-button on the
`Tally Migration Session` form view. Wired via the existing
form-JS hook pattern (we'll add one in
`rgi_migration/rgi_migration/doctype/tally_migration_session/tally_migration_session.js`).
Click → `frappe.set_route("md-review", frm.doc.name)`.

**Secondary entry points** (no extra work, Desk provides):
- Direct URL paste (Aditya shares a URL to a coworker).
- Module sidebar under "Rgi Migration" — the Page's JSON record
  auto-registers it in the module. Useful for admin / diagnostic
  arrivals.

**Deep-link to a specific decision**: append
`#<decision-name>` to the URL — e.g.
`/app/md-review/CACSPU-2026-01#mapping-decision-0032`.
The page reads the hash on load and selects the matching row. See
§1.8 for state management details.

**No-session landing** (`/app/md-review` without a
path segment): auto-redirects to the user's last-viewed session
recorded in `frappe.model.user_settings["Mapping Decision Review"].last_session`.
If no recent session is tracked (fresh user or cleared settings),
shows the 404-shape empty state (§1.9 case 1). OQ5 resolved.

**Page header controls** (top bar of the custom page, above both
panes):

- **Title**: current session name + fiscal year chip.
- **Filter preset toggle**: *Pending* (default) / *All decisions*
  — duplicated from §1.3 so it's always reachable without
  scrolling the master pane.
- **Switch Session dropdown** — Select populated from the user's
  recent `Tally Migration Session` records (last 10, ordered by
  `modified`). Picking an entry `frappe.set_route`s to the
  equivalent URL for that session, preserving the `Review
  Decisions` mental model for cross-entity audit. Added per OQ5
  for reviewers auditing multiple entities in one sitting.
- **Regenerate link**: small right-aligned "Back to session →"
  link to `/app/tally-migration-session/<name>`.

**Fallback — Desk list view**: the standalone Mapping Decision
DocType (Option (a) from Open Question #1, confirmed 2026-04-20)
retains its default Desk list view at `/app/mapping-decision`.
Reviewers don't use it; admins use it for cross-session
diagnostics and as a safety net if the custom page breaks.

### §1.2 Layout — split-pane, 7/3, fixed-ratio v1

CSS grid matching POS's pattern (`erpnext/public/scss/point-of-sale.scss`):

```scss
.mapping-decision-review-app {
  display: grid;
  grid-template-columns: repeat(10, minmax(0, 1fr));
  gap: var(--margin-md);
  padding: 1%;
  height: calc(100vh - 5rem);

  > .decision-list-pane   { grid-column: span 7 / span 7; }
  > .decision-detail-pane { grid-column: span 3 / span 3; }
}
```

Both panes have fixed height `calc(100vh - 5rem)` (matching POS);
each pane internally scrolls on overflow.

**Ratio rationale**: 7/3 prioritises list scannability. The seven
columns of text-dense list content (see §1.3) benefit materially
from the extra horizontal room at 70%; the detail pane at 30%
fits the six sections (see §1.4) adequately at 1200px+ viewports —
Link autocomplete and Currency fields stay readable, textarea
wraps naturally.

Originally designed at 6/4 (mirroring POS), tightened to 7/3
during Commit 3 browser verification after reviewer feedback that
the master pane was the attention-anchor and deserved the budget.
One-line CSS change if we revisit.

**Fixed ratio in v1**. Drag-to-resize is explicitly a v2 concern
(§1.9 scope fence). One-line CSS change to add it later; not
worth the event-handler work today.

### §1.3 Master pane — list

**Filter bar** (top of master pane, single row):

- **Filter preset toggle**: two pills — *Pending* (default) /
  *All decisions*. One click to flip. The default preset applies
  `review_action in (Pending, Pending Account Creation, Pending
  Group Account Resolution, Pending Supplier Creation)` (the
  migration-critical "needs attention" bucket). *All decisions*
  drops the filter for diagnostic inspection.
- **Tier multi-select**: `frappe.ui.FieldGroup` select with all
  tier values. Composed AND with the preset filter. The `tier`
  DocType Select enum is extended in Commit 1 of implementation
  (alongside the `istable=0` flip) to include the three missing
  values `excluded_zero_balance`, `tier1_supplier_fuzzy`,
  `pending_supplier_creation`. OQ1 resolved.
- **Root-type multi-select**: Select from
  Asset / Liability / Equity / Income / Expense. Useful for
  "clear all the bank cases together" workflows.
- **Search box**: text input, binds to `tally_name` `like` filter.
  Debounced 250 ms.

**Table** (scrollable, below filter bar):

| # | Column | Source | Width | Notes |
|---|---|---|---|---|
| 1 | Indicator | derived from `review_action` | 8px | coloured dot (§1.11 palette); carries the row's review-state signal |
| 2 | Tally Name | `tally_name` | ~26% | primary identifier, ellipsised at width |
| 3 | Parent Chain | `tally_parent_chain` | ~20%, truncated | last ~100 chars, ellipsised LEFT (so rightmost-specific parent is visible); tooltip shows full chain |
| 4 | Net Amount | `net_amount` (signed) | ~14%, right-align | formatted with sign + thousands sep |
| 5 | Side | `net_side` | ~6% | `Dr` / `Cr` / `—` |
| 6 | Tier | `tier` | ~14% | mapper-classification chip |
| 7 | Proposed Account | `proposed_account` | ~20% | ellipsised; blank for unmapped |

**Column 7 rationale — Review Action dropped (refinement 6).**
The prior design had a text column for `review_action`; re-read
confirms it's redundant with the Indicator dot (column 1), which
colour-codes the same state via the §1.11 palette. Granular
within-colour distinctions (Pending Account Creation vs Pending
Supplier Creation vs Pending Group Account Resolution; Approved
vs Manual Override) are rarely decision-relevant at list-scan
time — reviewer clicks through to the detail pane for that.
Removing the column buys horizontal budget for a parent-chain
snippet, which IS decision-relevant at scan time (e.g. "is this
under Sundry Debtors or Bank Accounts?"). Parent-chain truncation
ellipsises the LEFT so the innermost parent group — the one with
the most classification signal — stays visible at narrow widths.

**Default sort**: `review_action` ascending (Pending sorts
first against the alphabetised Select enum), then `net_amount`
descending (largest balances first). Persisted per reviewer via
`frappe.model.user_settings`.

**Row interactions**:
- **Click** → select + populate detail pane. No navigation.
- **Arrow keys** (`↑` / `↓`) → move selection ± 1, scroll into
  view if off-screen, refresh detail pane. Per-page scoped via
  `frappe.ui.keys.add_shortcut({page: this.page, ...})`.
- **Home / End** → jump to first / last row.
- **Enter** with focus in list → move focus to detail pane's
  first interactive control (`final_account` field). Reviewer
  can then type immediately.

**Data source**: `frappe.call("frappe.desk.reportview.get",
{doctype: "Mapping Decision", fields: [...], filters: [...],
order_by: "...", start: 0, page_length: 50})`. Same path Frappe's
own list view uses — permission filters, ordering, indexing all
free. Page-length 50 default; pagination via **Load More** button
at list footer (not infinite scroll — explicit control preserves
mid-review context).

**Real-time update after save**: after a save mutation (§1.6),
re-issue the reportview.get call with same filters to refresh
the list in-place. Cheap at 50 rows / 2-3 KB payload. Selected
row auto-advances to the next (§1.7).

### §1.4 Detail pane — sections

Six sections, top-to-bottom, each a collapsible-ready container
(collapsibility itself is §1.9 scope fence — v1 shows all six
always-expanded):

**Section 1 — Tally Context** *(read-only HTML)*

Displays the source-data facts the reviewer needs to make a
mapping decision. Pulled from the Mapping Decision doc:

- `tally_name` (heading, bold, monospace-serif)
- `tally_id` (small, muted)
- `tally_parent_chain` (full chain, `>`-joined)
- `tally_root_type` (coloured chip: Asset = blue,
  Liability = red, Equity = purple, Income/Expense = grey-muted)
- `opening_dr`, `opening_cr` (currency, both shown for transparency)
- `net_amount` (signed, large, right-align)
- `net_side` (Dr/Cr chip)
- `is_pnl_closed_zero`, `is_system_account` (small icons if set)

Rendered as a flat HTML table with label-value rows. No edit
controls — this is source-of-truth from Tally, reviewer-immutable.

**Section 2 — Mapper Resolution** *(read-only HTML)*

What the Tier-1 mapper proposed (or why it refused):

- `tier` chip (same colour as list column)
- `proposed_account` (if set, as Link display with navigate
  button to the Account form in a new tab)
- `matched_rule` (if set, Link to the Mapping Rule; blank
  otherwise)
- `confidence` (0.00–1.00, two decimals)
- `proposed_dr` / `proposed_cr` (currency, diagnostic)
- `anti_pattern_blocked` + `anti_pattern_rule` +
  `anti_pattern_message` (conditional block, red-bordered if
  present — this is the reviewer's "don't exact-match this"
  signal)
- `excluded_reason` (conditional; populated for `excluded_pnl`,
  `excluded_zero_balance`, `pending_group_account_resolution`)

**Section 3 — Tier-2 Fuzzy Candidates** *(v1 placeholder)*

Empty state for v1: *"Tier-2 fuzzy matching not yet active. Top
candidate matches will appear here after Item 6 ships."* Included
now so the layout doesn't shift when Item 6 lands — reviewers
will have the visual location pre-learned.

**Section 4 — Reviewer Action** *(writable form controls)*

The work surface. Uses `frappe.ui.form.make_control` per field:

- `review_action` Select — the primary state knob. 10 options
  per deployed DocType enum. Auto-mutates on `final_account`
  change per this truth table (refinement 1):

  | Starting state | Reviewer action | `review_action` flips to |
  |---|---|---|
  | `proposed_account` empty + `Pending` | picks a `final_account` | `Approved` |
  | `proposed_account` empty + `Pending` | clears `final_account` | back to `Pending` |
  | `proposed_account` non-empty + `Pending` | picks same as `proposed_account` | `Approved` |
  | `proposed_account` non-empty + `Pending` | picks different `final_account` | `Manual Override` |
  | `proposed_account` non-empty + `Pending` | clears `final_account` | back to `Pending` |
  | Any | manually picks `Rejected` / `Deferred` / `Pending *` | Select value wins (no auto-flip) |

  Key point: `Manual Override` is reserved for "reviewer picked
  something DIFFERENT than the mapper's proposal." Unmapped +
  reviewer picks = `Approved` (the reviewer agreed with "there
  should be a mapping here" and supplied it; not an override of
  an existing proposal).

- `final_account` Link → Account — **scoped to the session's
  Company via `get_query`** (critical: CACSPU reviewers see
  only CACSPU accounts, not Dux Digitech / JEWIPL accounts).
  OQ2 resolved: implementation Commit 1 includes a 15-min spike
  verifying `frappe.ui.form.make_control` + `get_query` on
  Link fields works on a standalone (no-form) widget. If the
  spike fails, fall back to fallback (a) — custom
  `frappe.ui.form.ControlLink` subclass with overridden
  `get_query`. Fallback (b) reserved as a last resort.

- `final_dr`, `final_cr` Currency — **read-only in v1**
  (refinement 2). Derived from `opening_dr` / `opening_cr` and
  always equal to them. The splitting workflow — one Tally
  balance → two or more ERPNext accounts via
  `requires_combine` / `combine_with` — is deferred to v2 once
  the DocType schema for split linkage matures coherently
  (currently the `combine_with` CSV field is a schema stub
  with no enforced semantics). See §1.10 scope fence.

- `reviewer_notes` Small Text — free-text audit trail. Used for
  "why" documentation that doesn't fit the structured fields.
  **Serves as the v1 comments replacement** (per earlier resolution:
  skip comment threads, lean on this field for audit chronology).

  **Chronology header** (refinement 3): on save, if
  `reviewer_notes` is being appended to (non-empty incoming
  value AND existing non-empty stored value), the backend
  `save_decision` method prepends `[<user>, <YYYY-MM-DD HH:MM>]`
  to the new content before concatenating with a blank line
  separator. Example:

  ```
  [aditya@jewonline.in, 2026-04-22 10:14] Confirmed as inter-entity
  receivable — GHR Education is a sibling RGI company; target ledger
  will be created in Item 4.

  [priya@jewonline.in, 2026-04-20 15:47] First-pass: tier unmapped,
  Tally parent chain suggests Branch / Divisions.
  ```

  Preserves review chronology without a comment-thread UI.
  Implementation is server-side (in `save_decision`); the UI
  textarea simply shows the current full value and the new
  input is concatenated on submit. See §1.12 for the backend
  contract.

**Section 5 — Assignment** *(custom minimal widget)*

Two-element widget:
- **Current assignee chip**: displays `<user> (avatar + name)`
  if the decision has an open ToDo row; empty state otherwise.
  **Click-to-remove** (refinement 5): clicking the chip's `×`
  icon calls `frappe.desk.form.assign_to.remove` with
  `{doctype: "Mapping Decision", name: <decision>, assign_to: <user>}`.
  Confirms via `frappe.confirm`, then repaints the chip as empty
  on success. Removes forced navigation to the ToDo form for
  assignment changes — reviewer never leaves the review page.
- **Assign to... button** (shown when chip is empty, or as a
  secondary "Reassign..." when chip is populated): opens
  `frappe.ui.Dialog` with a User Link field and description
  textarea. On Submit, POSTs to `frappe.desk.form.assign_to.add`
  with `{doctype: "Mapping Decision", name: <decision>, assign_to: [<user>], description}`.
  Reloads the chip.

**Section 6 — Audit** *(read-only)*

- `owner` (label "Created by"), `creation` (timestamp).
- `modified_by` (label "Last edited by"), `modified` (timestamp).
- `promoted_to_rule` Link → Mapping Rule, if a Week-5
  reviewer-promotion workflow has already converted this
  decision into a rule. Blank in Week 4.

Plus a small **"Open in full form"** button → navigates to the
standalone DocType form at `/app/mapping-decision/<name>` for
deep diagnostic cases (every DocType field editable without the
curated layout).

### §1.5 (reserved — merged into §1.4)

Old §2 detail-view section numbering folded into §1.4 sections
1–6. Section preserved here as a placeholder so Items 3-9 keep
their numbering in the rest of this doc.

### §1.6 Actions and keyboard shortcuts

Four action buttons in the detail pane footer (left-to-right,
primary on the right per Frappe convention):

| Button | Shortcut | Sets | Primary use |
|---|---|---|---|
| **Defer** | `d` | `review_action = Deferred` | "kick to next session" |
| **Reject** | `r` | `review_action = Rejected` | "no mapping appropriate" |
| **Request Creation** | `c` | `review_action = Pending Account Creation` *(or Pending Supplier Creation for vendor rows)* | opens a minimal-stub dialog with placeholder required fields (`parent = tally_root_type`, `is_group = 0`). Full Account / Supplier Creation Request workflow lands in Item 4 / Item 3. OQ3+OQ4 resolved. |
| **Approve & Next** (primary) | `a` / `Ctrl+S` | **validates first** (see below), sets `review_action = Approved` (or `Manual Override` per §1.4 Section 4 truth table), saves, advances | default approval flow |
| **Save Without Advance** (Actions menu) | `Ctrl+Shift+S` | saves current field state verbatim — whatever `review_action` the Select shows, no validation, stays on row | deliberate state preservation (e.g. save a partial `reviewer_notes` mid-thinking) |

Approve / Defer / Reject / Request Creation all auto-advance
(§1.7); Save Without Advance stays on the current row.

**`a` validation semantics** (refinement 4). Before save, the
handler checks:

1. `final_account` is non-empty (the reviewer has committed to
   a target account).
2. `review_action` is not `Pending` (a state transition has
   actually occurred — guards against fat-fingered `a` while the
   auto-flip logic hasn't run because the account picker was
   abandoned).

If either fails, the handler shows an **inline validation
message** below the detail-pane footer ("Pick an account before
approving" / "Review action is still Pending — confirm via the
Select above"), does NOT save, does NOT advance. The reviewer
fixes the input and retries.

`Ctrl+Shift+S` (and the **Save Without Advance** Actions menu item)
is the "save what I have, warts and all" escape hatch — useful when
a reviewer wants to persist partial `reviewer_notes` while still
thinking about the right `final_account`. Discoverability:
mentioned in the `?` shortcut dialog. Ctrl+S deliberately does NOT
take this path — it's bound to the validating primary action per
Frappe convention.

Additional shortcuts:

| Shortcut | Action |
|---|---|
| `↑` / `↓` | move selection in list (no save; pure navigation) |
| `Home` / `End` | first / last row |
| `Enter` (focus in list) | move focus to detail's `final_account` field |
| `Esc` (focus in detail) | discard unsaved field changes, keep row selection |
| `Ctrl+Shift+S` | save verbatim (no validation), stay on row — also via Actions menu |
| `Ctrl+Z` (focus anywhere on page, within 5 s of save) | trigger Undo toast action (§1.7) |
| `?` | show shortcut reference (Frappe built-in dialog) |

`page.set_primary_action("Approve & Next", handler, ...)` wires
the `a` button + Frappe's global Ctrl+S to the validating
approve-and-advance handler. Ctrl+S in v1 calls the SAME handler
as the `a` button — meaning Ctrl+S also validates. The
"save-verbatim-no-validation" escape hatch described above is
bound to a separate shortcut (`Ctrl+Shift+S`) and also exposed
via a **Save Without Advance** menu item in the page's Actions
dropdown. This keeps Ctrl+S aligned with the primary-action
convention (non-surprising for Frappe users) while still offering
the save-without-validation path discoverably. Reviewers who
habitually Ctrl+S will get validation; the escape hatch is
opt-in via the menu or the less-common shortcut.

Single-letter shortcuts (`a`, `r`, `c`, `d`) register via
`frappe.ui.keys.add_shortcut({page: this.page, ignore_inputs: false, ...})`
— they only fire when focus is NOT in a text input, so they
don't collide with typing in the `reviewer_notes` textarea or
the `final_account` autocomplete.

Shortcut conflict check (performed against
`frappe/public/js/frappe/ui/keyboard.js:188-246`): Frappe reserves
`Ctrl+S`, `Ctrl+K`, `Ctrl+G`, `Alt+S`, `Shift+/`, `Alt+H`,
`Escape`, `Enter`, `Ctrl+↑`, `Ctrl+↓`. Our plain-letter and plain-arrow
bindings don't collide.

### §1.7 Auto-advance semantics

On any successful save:

1. Persist the decision via `frappe.call` →
   `rgi_migration.rgi_migration.page.md_review.md_review.save_decision`
   (custom whitelist method; see §1.10).
2. Show a **5-second Undo** toast (Gmail-style) in the page
   head. Click (or `Ctrl+Z`) → revert `review_action`,
   `final_account`, `reviewer_notes` to pre-save values, stay
   on current row (undoes the advance as well). **V1 scope
   commit** per OQ6 resolution — Aditya's explicit call for
   reviewer cognitive load. If the build budget tightens, cut
   scope elsewhere (e.g. Switch Session dropdown polish) before
   cutting Undo.

   Implementation: client-side `frappe.show_alert` with a
   custom action handler that calls a separate
   `rgi_migration.rgi_migration.page.md_review.md_review.undo_decision`
   whitelist method. The backend method uses a per-session
   in-memory `frappe.cache()` keyed by `<user>:<decision_name>`
   holding the pre-save doc snapshot with a 10-second TTL
   (covers clock skew). After 10 s the snapshot is gone and
   Undo becomes a no-op with a "Too late to undo" alert.
   Chronology header on `reviewer_notes` is NOT prepended on
   undo — undo means "never happened."
3. Re-fetch the master-pane list (same filter state).
4. Compute the next row:
   - Find current selection in the refreshed result set.
   - If the current row is still in the result set (e.g. reviewer
     marked as `Pending Account Creation` which is still in the
     default filter), move one down from its position.
   - If the current row dropped out (e.g. reviewer marked as
     `Approved` on the Pending filter), stay at the same index
     (which now points to what was the next row before).
   - If no rows remain in the result set, show empty state
     (§1.8).
5. Select the next row. Update detail pane. Update URL hash
   (§1.8).

**Save-without-advance** is not a v1 action. If a reviewer wants
to stay on a row (rare — e.g. "save my partial notes and think"),
they can save via Ctrl+S then press `↑` to go back. Good enough.

### §1.8 State management and URL

**URL shape on load**:
`/app/md-review/<session-name>[#<decision-name>]`

- **Without hash**: page loads, selects the first row in the
  default filter.
- **With hash**: page loads, selects the matching decision IF it's
  in the default filter's result set. If the hash refers to a
  decision NOT in the filter, page auto-flips to "All decisions"
  preset and selects; shows a one-line notice ("Showing all
  decisions because the linked row is not in the default filter").

**URL hash updates on navigation**: arrow-key movement, clicks,
auto-advance all `history.replaceState` the hash. No full
navigation → browser back/forward works across rows within the
page; browser back from row #14 to session form works via the
breadcrumb (Frappe default).

**Refresh behaviour**: reload the URL → re-selects the same row
(hash is intact). Filter preset state is URL-encoded via query
string: `/app/md-review/CACSPU-2026-01?filter=all#mapping-decision-0032`.
Default filter is the absence of query param. Per-reviewer sort
preferences persist via `frappe.model.user_settings`.

**Session-switching**: if a reviewer manually edits the URL to a
different session, the page reloads end-to-end (different
master-pane filter). No client-side session hot-swap in v1.

### §1.9 Empty states

Four distinct empty states, each with its own messaging and CTA:

**1. Session name 404 (segment present but invalid)** —
`/app/md-review/<invalid>`:

> **Session not found.**
>
> The session `<invalid>` doesn't exist on this bench. Pick a
> session from the list below, or go back to the Session form
> view.
>
> [Go to Session list] — `frappe.set_route("List", "Tally Migration Session")`

Rendered via `page.get_empty_state()` (Frappe's built-in).

**1a. No session segment** — `/app/md-review`:
auto-redirects to the user's last-viewed session via
`frappe.model.user_settings["Mapping Decision Review"].last_session`
(OQ5). If `last_session` is absent or the referenced session
no longer exists, falls through to case 1 above. No empty state
is shown for the redirecting path — the transition is
transparent to the reviewer.

**2. Session has zero mapping decisions yet** — reviewer opened
a Session that hasn't been parsed/mapped:

> **No decisions for this session.**
>
> Run parse + map on the session before reviewing. Generators
> can't run without mapped decisions.
>
> [Back to Session] → `/app/tally-migration-session/<name>`

**3. Filter matches zero rows (default filter cleared)** — all
pending decisions resolved:

> **All decisions in the current filter are resolved.** 🎉
>
> The session is ready to regenerate its four output artefacts.
>
> [Back to Session] (primary) |
> [Show All Decisions] (secondary, flips to all-decisions preset)

**4. Filter matches zero rows (non-default filter)** — reviewer
filtered to a subset that happens to be empty:

> **No decisions match this filter.**
>
> Try clearing filters or switching back to the Pending preset.
>
> [Clear filters]

All empty states hide the detail pane.

### §1.10 Scope fence — v1 non-goals

Explicit list. Each item lands in one of: a later Week-4 Item,
a Week-5+ scope, or post-RGI enhancement.

| Non-goal | Why not v1 | Lands in |
|---|---|---|
| Drag-to-resize panes | One-line CSS change, zero reviewer value at 7/3 ratio | v2+ post-RGI |
| Collapsible detail sections | Six sections all fit at 40% width × calc(100vh - 5rem); collapsing is premature | v2+ post-RGI |
| Full comment threads with @-mention | `reviewer_notes` textarea covers the audit trail; synthetic-frm scaffolding is fragile | v2+ post-RGI, if reviewers ask |
| Mobile responsive | Desk-only per Aditya's call — all reviewers use laptop-class screens | post-RGI |
| Tier-2 fuzzy candidate list | Backend + ranking logic doesn't exist yet | Week 4 Item 6 |
| Tier-3 Claude API candidates | Scope-gated until Tier-2 validates against 3-5 entities | Week 5+, evaluate after Tier-2 |
| Bulk approve / bulk reject | Deferred from original §1 — "reviewer clicked through without thinking" risk not resolved | Re-evaluate after CACSPU first pass; defer until real evidence |
| Account Creation sub-dialog full build | Rough dialog in v1 (row-action `c` flips state + saves stub request); full create-now button wiring | Week 4 Item 4 |
| Supplier Creation sub-dialog | Same shape as Item 4 | Week 4 Item 3 |
| Reviewer-promotion (confirmed fuzzy → Mapping Rule) | Requires Tier-2 plus promotion workflow | Week 4 Item 5 |
| Editable `final_dr` / `final_cr` + balance-splitting workflow | `requires_combine` / `combine_with` schema exists as stub but has no enforced semantics; splitting one Tally balance across multiple ERPNext accounts is non-trivial (ordering, rounding, reconciliation). Deferred until schema matures coherently. | v2+ post-RGI |
| Cross-session review inbox | Explicitly rejected per Aditya's scope call (one entity at a time) | Not planned |
| Per-reviewer "my work queue" view | Same reason | Not planned |
| Comment / email attachments on a decision | Out of workflow scope | Not planned |
| Animations / transitions | Unnecessary | Not planned |
| Custom theme | Use Desk's native theme | Not planned |

### §1.11 Visual cues

**Indicator palette** (red/orange/green/grey, per earlier resolution):

| `review_action` | Indicator dot | Row text |
|---|---|---|
| `Pending` | orange | default |
| `Pending Account Creation` | red | default |
| `Pending Supplier Creation` | red | default |
| `Pending Group Account Resolution` | red | default |
| `Approved` / `Manual Override` | green | default |
| `Rejected` | grey | muted |
| `Deferred` / `Skipped` | grey | muted |
| `Excluded (P&L)` | grey | muted |

Tier chips in the list (column 6) and detail (§1.4 section 2):

| `tier` | Chip colour |
|---|---|
| `tier1_exact` / `tier1_rule` / `tier1_pattern` | blue (resolved) |
| `tier1_supplier_fuzzy` | blue |
| `tier2_fuzzy` | purple (Week-4+) |
| `tier3_claude` | purple |
| `unmapped` | orange |
| `pending_supplier_creation` / `pending_account_creation` | red |
| `group_refused` / `anti_pattern_blocked` | red |
| `excluded_pnl` / `excluded_zero_balance` | grey |

Root-type chip in detail §1.4 section 1:

| `tally_root_type` | Chip colour |
|---|---|
| `Asset` | blue |
| `Liability` | red |
| `Equity` | purple |
| `Income` / `Expense` | grey-muted (should be rare given §2(a) exclusion) |

Rows themselves are not background-colour-coded; only the
indicator dot (column 1) and the Tier chip (column 6) carry
colour. Matches the "don't fatigue the eye at 50-row pages"
call. The indicator dot is the sole representation of
`review_action` in the list now that the Review Action text
column has been dropped (§1.3 refinement 6); granular state
distinctions are surfaced in the detail pane.

### §1.12 Build architecture

**Layer map**:

| Layer | File | Expected LOC |
|---|---|---|
| Page registration | `rgi_migration/rgi_migration/page/md_review/md_review.json` | ~20 (JSON DocType record) |
| Page entrypoint | `rgi_migration/rgi_migration/page/md_review/md_review.js` | ~1,500–2,000 (all panel classes inline, no bundle) |
| Page styles | `rgi_migration/rgi_migration/page/md_review/md_review.css` | ~250–400 |
| Backend mutations | `rgi_migration/rgi_migration/page/md_review/md_review.py` | ~200–400 (whitelist methods: `save_decision`, optionally `bulk_save_decisions`) |
| Session form JS | `rgi_migration/rgi_migration/doctype/tally_migration_session/tally_migration_session.js` | +~15 lines (Review Decisions button wiring) |

**No bundle in v1**. Unlike POS which splits 8 controller files
via `point-of-sale.bundle.js`, we inline all panel classes in
the single page JS file. Expected total ~1,800 lines is within
read-in-one-session range; avoids `hooks.py` `app_include_js`
entries and `bench build` complications. The bundle-refactor
trigger is recorded in `docs/mapper_design_notes.md` per
refinement 7: refactor when the file exceeds 2,500 lines OR
when Item 6 (Tier-2 fuzzy) adds meaningful ranking logic that
warrants its own module.

**Backend methods** (under
`rgi_migration/rgi_migration/page/md_review/md_review.py`):

- `get_session_decisions(session_name, filters, start, page_length, order_by)`
  — thin wrapper around `frappe.get_list` that validates the
  reviewer has read access to the session's company, and
  filters to `parent=<session_name>` implicitly (this filter
  becomes `session=<session_name>` once the `istable=0` flip
  adds the Link field). Used instead of raw
  `frappe.desk.reportview.get` to enforce the company scope.
- `save_decision(decision_name, review_action, final_account,
  reviewer_notes)` — atomic update, returns the refreshed doc.
  `final_dr` / `final_cr` are NOT accepted as inputs in v1
  (refinement 2 — they're derived server-side from
  `opening_dr` / `opening_cr`). The method:
  1. Loads the current doc, captures a snapshot for Undo.
  2. Applies `review_action` and `final_account` from inputs;
     re-derives `final_dr` / `final_cr` from
     `opening_dr` / `opening_cr`.
  3. Applies the chronology header to `reviewer_notes`
     (refinement 3): if the incoming value is non-empty AND
     differs from the stored value AND stored value is non-empty,
     prepends `[<frappe.session.user>, <frappe.utils.now_datetime().strftime("%Y-%m-%d %H:%M")>]\n`
     to the new content, then concatenates
     `<new_content>\n\n<stored_value>`. If the stored value
     is empty, no header is prepended (first-note case — the
     `owner` + `creation` fields on the doc are the audit
     anchor).
  4. Validates, saves, stashes the pre-save snapshot in
     `frappe.cache().hset("mdr_undo:<user>", decision_name, snapshot)`
     with a 10 s TTL via `expire_on`.
  5. Returns the refreshed doc.
- `undo_decision(decision_name)` — reverts a decision to its
  pre-save snapshot if one exists in `frappe.cache()` under
  `mdr_undo:<frappe.session.user>` and is within the 10 s
  TTL. Otherwise returns `{status: "expired"}` and the client
  shows "Too late to undo." No chronology header is added on
  undo (per §1.7).
- `get_next_pending(session_name, after_decision_name, filters)`
  — server-computed "what's next" for auto-advance; avoids
  client-state-desync when two reviewers work the same session.
- `get_session_scope(session_name)` — returns `{company, fiscal_year, recent_sessions}`
  for the page header (title chip + Switch Session dropdown per
  OQ5). `recent_sessions` is the current user's 10 most-recently-
  modified Tally Migration Sessions filtered to ones the user
  has read access to.
- Company-scoped Account autocomplete:
  - **Preferred path (OQ2 spike succeeds)**: no backend method
    needed — `frappe.ui.form.make_control` with `get_query`
    handles it client-side against the standard
    `frappe.client.validated_get_list`.
  - **Fallback (a) path**: still no backend method — a custom
    `ControlLink` subclass overrides `get_query` and continues
    to hit `frappe.client.validated_get_list`.
  - **Fallback (b) path**, deprecated: a
    `search_company_accounts(session_name, txt)` whitelist
    method would be added. Listed only for completeness;
    unlikely to be reached.

**Data flow on row-click**:

```
User clicks row (or arrow key)
  → JS: set selected_name
  → JS: frappe.db.get_doc("Mapping Decision", selected_name)
     (returns full doc in one network call)
  → JS: render Tally Context, Mapper Resolution sections
     from doc (pure template substitution)
  → JS: frappe.ui.form.make_control() × 4 for the writable
     section 4 fields, wire value-change handlers
  → JS: frappe.desk.form.load.get_docinfo() for the Assignment
     chip
  → JS: focus list row (keep keyboard focus; don't steal to
     detail pane until Enter pressed)
```

**Data flow on save**:

```
User presses `a` / Ctrl+S (Approve & Next — validates)
  OR Ctrl+Shift+S (Save Without Advance — no validation)
  → JS: (if Approve path) validate final_account non-empty +
        review_action != Pending; abort + inline error on fail
  → JS: collect field values from section 4 controls
  → JS: frappe.call("…save_decision", {args})
  → Server: re-derive final_dr/final_cr, apply chronology header
     to reviewer_notes, save, stash Undo snapshot in cache
  → JS: toast "Saved" with Undo button (5 s, Ctrl+Z also fires)
  → JS: (auto-advance paths only — Approve/Defer/Reject/Request
        Creation; NOT Save Without Advance) refresh list + resort
     + advance to next row via get_next_pending
  → JS: frappe.db.get_doc(next_row) → update detail pane
```

### §1.13 Open-question resolutions (all resolved 2026-04-22)

Every OQ from the `70241e5` prose has been resolved and embedded
in the relevant section. Kept here as a pointer index, not as
open work.

| OQ | Topic | Resolution | Landed in |
|---|---|---|---|
| 1 | `tier` Select enum drift | Extend enum as part of Commit 1 (alongside `istable=0` flip). | §1.3 filter bar note; §1.12 build architecture |
| 2 | `get_query` on standalone `make_control` | 15-min spike in Commit 1; fallback (a) custom `ControlLink` subclass if spike fails. Fallback (b) deprecated. | §1.4 Section 4 `final_account` bullet; §1.12 backend |
| 3 | Account Creation Request sub-dialog on `c` | Minimal stub with placeholder required fields (`parent = tally_root_type`, `is_group = 0`). Full CRUD in Item 4. | §1.6 action-buttons table |
| 4 | Supplier Creation Request sub-dialog | Same shape as OQ3 for `pending_supplier_creation`. Full CRUD in Item 3. | §1.6 action-buttons table |
| 5 | Session-less URL behaviour | Auto-redirect to `user_settings.last_session` with 404 fallback. Plus **Switch Session dropdown** in page header for mid-session context switching. | §1.1 page header controls; §1.9 case 1a |
| 6 | Undo toast | **Elevated to v1 scope** per Aditya's explicit call for reviewer cognitive load. Cut scope elsewhere if budget tightens, not Undo. | §1.7 auto-advance step 2; §1.12 `undo_decision` backend |

No open questions remain. Prose is implementation-ready.

### §1.14 Prose refinement change log

Detail of what changed between commit `70241e5` (initial §1
master-detail prose) and this commit. For each refinement:
what it fixes, which sections moved, and why it matters.

#### Refinement 1 — `review_action` auto-flip semantics

**Before**: §1.0 step 6 and §1.4 Section 4 said that picking any
`final_account` different from `proposed_account` flips
`review_action` to `Manual Override`.

**After**: a 6-row truth table in §1.4 Section 4 distinguishes:
- unmapped → picks account → `Approved` (reviewer agreed there
  should be a mapping and supplied it).
- mapped (non-empty proposal) → picks same → `Approved`.
- mapped → picks different → `Manual Override`.
- mapped → clears → `Pending`.
- Any → manually picks terminal state → Select value wins.

**Why it matters**: `Manual Override` carried a specific
semantic ("reviewer disagreed with mapper's proposal") that was
being diluted when reviewers simply filled in unmapped rows.
Downstream (reviewer-promotion to Mapping Rule in Item 5) wants
to distinguish "new rule needed" (Approved on formerly unmapped)
from "existing rule is wrong for this case" (Manual Override).

§1.0 step 6 narrative updated to reflect the new flip. Cross-
reference added from §1.0 to §1.4 Section 4.

#### Refinement 2 — `final_dr` / `final_cr` read-only in v1

**Before**: §1.4 Section 4 showed these fields as editable, with
a note that Manual Override could split a Tally balance across
ERPNext accounts via `requires_combine` / `combine_with`.

**After**: both fields are strictly read-only in v1, derived
server-side from `opening_dr` / `opening_cr`. The splitting
workflow is explicitly punted to v2 in §1.10 scope fence. §1.12
`save_decision` drops them from the input schema and re-derives
on save.

**Why it matters**: `requires_combine` + `combine_with` is
currently a schema stub with no enforced semantics — letting
reviewers edit these fields in v1 would produce unvalidated data
that we'd then have to migrate when splitting actually lands.
Keeping them read-only means the v1 data model is guaranteed to
match `opening_*` on save, so v2 can design the splitting
schema cleanly without cleaning up v1 garbage first.

#### Refinement 3 — `reviewer_notes` chronology header

**Before**: §1.4 Section 4 treated `reviewer_notes` as a single
text field; multiple reviewers writing to the same decision over
time would stomp each other's notes or accumulate unattributed
concatenation.

**After**: §1.12 `save_decision` backend auto-prepends
`[<user>, <YYYY-MM-DD HH:MM>]` to new content on append,
preserving chronology without a comment-thread UI. First-note
case (empty stored value) skips the header — `owner` + `creation`
cover the attribution. §1.4 Section 4 documents the client-side
UX (reviewer just sees/edits the current full value; concatenation
is server-side).

**Why it matters**: comment threads were deferred per the earlier
"skip comments" resolution, but audit chronology IS
migration-critical — the client will want to see "priya did X at
time Y, aditya disagreed and did Z at time W" when reconciling.
This is the lightest-weight way to preserve that signal.

#### Refinement 4 — `a` shortcut validates before save

**Before**: `a` and Ctrl+S both saved unconditionally.

**After**: `a` (now "Approve & Next") validates
`final_account != empty` and `review_action != Pending` before
saving; failure shows an inline message without advancing.
Ctrl+S is now bound to the SAME validating handler (non-surprising
for Frappe users). The escape hatch is bound to `Ctrl+Shift+S`
and exposed via an Actions menu "Save Without Advance" item.

**Why it matters**: the common failure mode was fat-fingering
`a` immediately after a row-click before the reviewer had
actually decided; without validation this created `Approved`
rows with empty `final_account`, which then broke generator #1
at regenerate time. Now `a` is reliably "I mean it" — Ctrl+Shift+S
is the escape hatch for "save my thinking mid-stream."

#### Refinement 5 — Assignment chip clickable to remove

**Before**: §1.4 Section 5 required reviewers to navigate to the
ToDo form to remove an assignment.

**After**: clicking the chip's `×` icon calls
`frappe.desk.form.assign_to.remove` via `frappe.confirm`; reviewer
stays on the review page. Assign-to button morphs into a
"Reassign..." secondary button when the chip is populated.

**Why it matters**: the review page is the reviewer's primary
workspace for the 30-min per-entity loop; forcing a navigation
away for assignment changes broke flow. This makes assignment a
first-class in-page action.

#### Refinement 6 — Review Action column dropped in favour of Parent Chain snippet

**Before**: master pane column 7 was a text chip for `review_action`.

**After**: column 7 dropped; column 3 is a new Parent Chain
snippet (truncated at ~100 chars, **left-ellipsised** so the
innermost parent group stays visible); column widths
re-balanced. The indicator dot (column 1) remains the sole
list-level representation of `review_action` via the §1.11
colour palette. Granular state distinctions move to the detail
pane.

**Why it matters**: `review_action` chip was redundant with the
coloured dot at list-scan time — reviewers rarely need to
disambiguate "Pending Account Creation vs Pending Supplier
Creation" without clicking through. Parent chain, in contrast,
is scan-relevant: "is this a Sundry Debtors ledger?" "is this
under Bank Accounts?" are triage-relevant questions the prior
list couldn't answer without entering detail.

Left-ellipsising the parent chain preserves the most-specific
ancestor (the one that differentiates between sibling ledgers),
which is the part carrying actual classification signal.

#### Refinement 7 — Bundle-threshold rule documented elsewhere

**Before**: §1.12 carried a "refactor to bundle if file crosses
3,000 lines" rule inline.

**After**: rule moved to `docs/mapper_design_notes.md` (§10 in
that doc) with updated thresholds (2,500 lines **or** Item 6
adds meaningful ranking logic). §1.12 references it.

**Why it matters**: the threshold is a cross-item concern (Item
6 will contribute to the same file) and should live in the
shared architectural-principles doc, not inside Item 1's prose.
Avoids every subsequent item re-litigating "when do we bundle?"

#### Undo elevation — OQ6 resolved at v1 scope

Moved from "stretch if budget allows" to v1-committed scope per
Aditya's explicit call for reviewer cognitive load. §1.7
auto-advance step 2 is now unconditional; §1.10 scope fence no
longer lists Undo as deferred; §1.12 backend adds an
`undo_decision` whitelist method + `frappe.cache()`-backed 10 s
TTL snapshot storage per user+decision. Ctrl+Z bound as an
alternative trigger for the Undo toast.

**Why it matters**: reviewers working through 2,300 decisions
over 59 entities will fat-finger. The cognitive cost of "did I
just approve something I shouldn't have?" uncertainty compounds
across the migration. Undo is the cheapest possible mitigation
and it matters most on a bulk-throughput workflow.

---

### Explicit non-goals for Items 3-9 (carried forward for scope hygiene)

- Item 3 (Supplier Creation Request workflow) — approve/reject
  UI lives on the SCR DocType form, not on the review page.
- Item 4 (Account Creation Request workflow) — same.
- Item 5 (Reviewer-promotion to Mapping Rule) — dialog launched
  from the review page detail pane, but the promotion logic is
  Item 5 scope.
- Item 6 (Tier-2 fuzzy candidates) — backend + ranking;
  renders into §1.4 Section 3 placeholder.
- Item 7 (`fiscal_year_short` before_insert hook) — session
  controller work; no review-page coupling.
- Item 8 (FrappeRuleSource / FrappeSupplierSource) — mapper
  infrastructure; no review-page coupling.
- Item 9 (First end-to-end CACSPU production migration test) —
  exercises the whole pipeline including this page.
