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
   `/app/mapping-decision-review/CACSPU-2026-01`. Page loads in
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
   He picks it. `review_action` auto-flips to `Manual Override`
   (because `final_account` no longer equals the empty
   `proposed_account`).
7. Presses **`a`** (approve — save + advance) OR clicks the
   **Save & Next** button. Backend whitelist method persists the
   decision; master pane's row indicator flips from orange to
   green, optimistic-UI re-sorts so the next pending row takes
   the top slot; detail pane updates with the next decision's
   doc. Total click-to-next: ~300 ms.
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
   `/app/mapping-decision-review/CACSPU-2026-01#mapping-decision-0032`
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

**Route**: `/app/mapping-decision-review/<session-name>` — session
name as a path segment. On session `CACSPU-2026-01` that resolves
to `/app/mapping-decision-review/CACSPU-2026-01`. Frappe's Page
framework routes this by registering a Page record with
`page_name = "mapping-decision-review"` and reading `frappe.get_route()`
for the `<session-name>` suffix.

**Primary entry point**: a **Review Decisions** inner-button on the
`Tally Migration Session` form view. Wired via the existing
form-JS hook pattern (we'll add one in
`rgi_migration/rgi_migration/doctype/tally_migration_session/tally_migration_session.js`).
Click → `frappe.set_route("mapping-decision-review", frm.doc.name)`.

**Secondary entry points** (no extra work, Desk provides):
- Direct URL paste (Aditya shares a URL to a coworker).
- Module sidebar under "Rgi Migration" — the Page's JSON record
  auto-registers it in the module. Useful for admin / diagnostic
  arrivals.

**Deep-link to a specific decision**: append
`#<decision-name>` to the URL — e.g.
`/app/mapping-decision-review/CACSPU-2026-01#mapping-decision-0032`.
The page reads the hash on load and selects the matching row. See
§1.8 for state management details.

**Fallback — Desk list view**: the standalone Mapping Decision
DocType (Option (a) from Open Question #1, confirmed 2026-04-20)
retains its default Desk list view at `/app/mapping-decision`.
Reviewers don't use it; admins use it for cross-session
diagnostics and as a safety net if the custom page breaks.

### §1.2 Layout — split-pane, 6/4, fixed-ratio v1

CSS grid matching POS's pattern (`erpnext/public/scss/point-of-sale.scss`):

```scss
.mapping-decision-review-app {
  display: grid;
  grid-template-columns: repeat(10, minmax(0, 1fr));
  gap: var(--margin-md);
  padding: 1%;
  height: calc(100vh - 5rem);

  > .decision-list-pane   { grid-column: span 6 / span 6; }
  > .decision-detail-pane { grid-column: span 4 / span 4; }
}
```

Both panes have fixed height `calc(100vh - 5rem)` (matching POS);
each pane internally scrolls on overflow.

**Ratio rationale**: 6/4 prioritises list scannability. Seven
columns of text-dense list content (see §1.3) read cleanly at
60% viewport; the detail pane at 40% fits all six sections (see
§1.4) without wasted whitespace on a 1440px Desk viewport. Mirrors
POS's own ratio and every reviewer on this bench will already have
the eye-model from their dux_daybook / dux_cashbook use.

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
  tier values. Composed AND with the preset filter. Open Question
  §1.11 #1 — the `tier` DocType enum needs extending to include
  `excluded_zero_balance`, `tier1_supplier_fuzzy`,
  `pending_supplier_creation` before this is useful.
- **Root-type multi-select**: Select from
  Asset / Liability / Equity / Income / Expense. Useful for
  "clear all the bank cases together" workflows.
- **Search box**: text input, binds to `tally_name` `like` filter.
  Debounced 250 ms.

**Table** (scrollable, below filter bar):

| # | Column | Source | Width | Notes |
|---|---|---|---|---|
| 1 | Indicator | derived from `review_action` | 8px | coloured dot (§1.9 palette) |
| 2 | Tally Name | `tally_name` | ~32% | primary identifier, ellipsised at width |
| 3 | Net Amount | `net_amount` (signed) | ~14%, right-align | formatted with sign + thousands sep |
| 4 | Side | `net_side` | ~6% | `Dr` / `Cr` / `—` |
| 5 | Tier | `tier` | ~16% | mapper-classification chip |
| 6 | Proposed Account | `proposed_account` | ~24% | ellipsised; blank for unmapped |
| 7 | Review Action | `review_action` | ~16% | current state chip |

Parent chain is deliberately absent — too long for list width,
present in detail pane tooltip-free.

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
  per deployed DocType enum. Auto-mutates based on other field
  changes: if reviewer picks a `final_account` that differs
  from `proposed_account`, flips to `Manual Override`; if
  reviewer clears `final_account`, flips back to `Pending`.
- `final_account` Link → Account — **scoped to the session's
  Company via `get_query`** (critical: CACSPU reviewers see
  only CACSPU accounts, not Dux Digitech / JEWIPL accounts).
  See §1.11 #2 for the implementation-risk flag on standalone
  `make_control` widgets.
- `final_dr`, `final_cr` Currency — auto-populated from
  `opening_dr` / `opening_cr` on initial render; editable for
  Manual Override cases (e.g. reviewer splits a Tally balance
  across two ERPNext accounts via linked sibling decisions —
  handled via `requires_combine` / `combine_with` fields,
  already in schema).
- `reviewer_notes` Small Text — free-text audit trail. Used for
  "why" documentation that doesn't fit the structured fields.
  **Serves as the v1 comments replacement** (Open Question A
  resolved: skip comments, lean on this field).

**Section 5 — Assignment** *(custom minimal widget)*

Two-element widget:
- **Current assignee chip**: displays `<user> (avatar + name)`
  if the decision has an open ToDo row; empty state otherwise.
- **Assign to... button**: opens `frappe.ui.Dialog` with a User
  Link field and description textarea. On Submit, POSTs to
  `frappe.desk.form.assign_to.add` with
  `{doctype: "Mapping Decision", name: <decision>, assign_to: [<user>], description}`.
  Reloads the chip.

No Remove-assignment in v1 — reviewer navigates to the ToDo
itself (standard Frappe) for removal. Good enough until Week 4
usage says otherwise.

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
| **Request Creation** | `c` | `review_action = Pending Account Creation` *(or Pending Supplier Creation for vendor rows)* | opens an account-creation sub-dialog (Item 4 / Item 3) |
| **Save & Next** (primary) | `a` / `Ctrl+S` | uses current field values — `review_action` is whatever the Select shows | default "Approve" flow |

All four save the decision, then auto-advance (§1.7).

Additional shortcuts:

| Shortcut | Action |
|---|---|
| `↑` / `↓` | move selection in list (no save; pure navigation) |
| `Home` / `End` | first / last row |
| `Enter` (focus in list) | move focus to detail's `final_account` field |
| `Esc` (focus in detail) | discard unsaved field changes, keep row selection |
| `?` | show shortcut reference (Frappe built-in dialog) |

Ctrl+S is reused via `page.set_primary_action("Save & Next",
handler, ...)` — Frappe's global Ctrl+S shortcut triggers the
page's primary action automatically (`frappe/public/js/frappe/ui/keyboard.js:188`).
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
   `rgi_migration.rgi_migration.page.mapping_decision_review.mapping_decision_review.save_decision`
   (custom whitelist method; see §1.10).
2. Show a **5-second Undo** toast (Gmail-style) in the page
   head. Click → revert `review_action`, `final_account`,
   `final_dr`, `final_cr`, `reviewer_notes` to pre-save values,
   stay on current row. **Stretch for v1**; if implementation
   budget runs tight, land without Undo and defer (§1.9 scope
   fence).
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
`/app/mapping-decision-review/<session-name>[#<decision-name>]`

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
string: `/app/mapping-decision-review/CACSPU-2026-01?filter=all#mapping-decision-0032`.
Default filter is the absence of query param. Per-reviewer sort
preferences persist via `frappe.model.user_settings`.

**Session-switching**: if a reviewer manually edits the URL to a
different session, the page reloads end-to-end (different
master-pane filter). No client-side session hot-swap in v1.

### §1.9 Empty states

Four distinct empty states, each with its own messaging and CTA:

**1. Session name missing or 404** —
`/app/mapping-decision-review/` or
`/app/mapping-decision-review/<invalid>`:

> **Session not found.**
>
> The session `<invalid>` doesn't exist on this bench. Pick a
> session from the list below, or go back to the Session form
> view.
>
> [Go to Session list] — `frappe.set_route("List", "Tally Migration Session")`

Rendered via `page.get_empty_state()` (Frappe's built-in).

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
| Drag-to-resize panes | One-line CSS change, zero reviewer value at 6/4 ratio | v2+ post-RGI |
| Collapsible detail sections | Six sections all fit at 40% width × calc(100vh - 5rem); collapsing is premature | v2+ post-RGI |
| Full comment threads with @-mention | `reviewer_notes` textarea covers the audit trail; synthetic-frm scaffolding is fragile | v2+ post-RGI, if reviewers ask |
| Mobile responsive | Desk-only per Aditya's call — all reviewers use laptop-class screens | post-RGI |
| Tier-2 fuzzy candidate list | Backend + ranking logic doesn't exist yet | Week 4 Item 6 |
| Tier-3 Claude API candidates | Scope-gated until Tier-2 validates against 3-5 entities | Week 5+, evaluate after Tier-2 |
| Bulk approve / bulk reject | Deferred from original §1 — "reviewer clicked through without thinking" risk not resolved | Re-evaluate after CACSPU first pass; defer until real evidence |
| Account Creation sub-dialog full build | Rough dialog in v1 (row-action `c` flips state + saves stub request); full create-now button wiring | Week 4 Item 4 |
| Supplier Creation sub-dialog | Same shape as Item 4 | Week 4 Item 3 |
| Reviewer-promotion (confirmed fuzzy → Mapping Rule) | Requires Tier-2 plus promotion workflow | Week 4 Item 5 |
| 5-second Undo toast on save | Stretch — ships if v1 build budget allows, else defers | v2 or late v1 |
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

Tier chips in the list (column 5) and detail (§1.4 section 2):

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
indicator dot and the Tier chip carry colour. Matches the "don't
fatigue the eye at 50-row pages" call.

### §1.12 Build architecture

**Layer map**:

| Layer | File | Expected LOC |
|---|---|---|
| Page registration | `rgi_migration/rgi_migration/page/mapping_decision_review/mapping_decision_review.json` | ~20 (JSON DocType record) |
| Page entrypoint | `rgi_migration/rgi_migration/page/mapping_decision_review/mapping_decision_review.js` | ~1,500–2,000 (all panel classes inline, no bundle) |
| Page styles | `rgi_migration/rgi_migration/page/mapping_decision_review/mapping_decision_review.css` | ~250–400 |
| Backend mutations | `rgi_migration/rgi_migration/page/mapping_decision_review/mapping_decision_review.py` | ~200–400 (whitelist methods: `save_decision`, optionally `bulk_save_decisions`) |
| Session form JS | `rgi_migration/rgi_migration/doctype/tally_migration_session/tally_migration_session.js` | +~15 lines (Review Decisions button wiring) |

**No bundle**. Unlike POS which splits 8 controller files via
`point-of-sale.bundle.js`, we inline all panel classes in the
single page JS file. Expected total ~1,800 lines is within
read-in-one-session range; avoids `hooks.py` `app_include_js`
entries and `bench build` complications. Refactor to a bundle if
the file crosses 3,000 lines.

**Backend methods** (under
`rgi_migration/rgi_migration/page/mapping_decision_review/mapping_decision_review.py`):

- `get_session_decisions(session_name, filters, start, page_length, order_by)`
  — thin wrapper around `frappe.get_list` that validates the
  reviewer has read access to the session's company, and
  filters to `parent=<session_name>` implicitly. Used instead of
  raw `frappe.desk.reportview.get` to enforce the company scope.
- `save_decision(decision_name, review_action, final_account,
  final_dr, final_cr, reviewer_notes)` — atomic update, returns
  the refreshed doc.
- `get_next_pending(session_name, after_decision_name, filters)`
  — server-computed "what's next" for auto-advance; avoids
  client-state-desync when two reviewers work the same session.

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
User presses `a` / Ctrl+S / Save & Next
  → JS: collect field values from section 4 controls
  → JS: frappe.call("…save_decision", {args})
  → Server: update doc, commit, return refreshed doc
  → JS: optimistic toast "Saved" with Undo
  → JS: frappe.call("…get_session_decisions", {refresh}) OR
     reuse cached list by splicing in the updated row
  → JS: resort + reselect next row
  → JS: frappe.db.get_doc(next_row) → update detail pane
```

### §1.13 Open questions for Aditya

Unresolved before implementation kickoff. Listed in order of
blocking-severity.

#### Open Question 1 — `tier` Select enum drift (carried over from prior §1)

Still unresolved. The deployed `Mapping Decision.tier` Select
options are missing `excluded_zero_balance`, `tier1_supplier_fuzzy`,
`pending_supplier_creation`. Blocks the §1.3 tier filter widget.

**Proposed resolution**: extend the Select options as the first
commit of Item 1 implementation (alongside the `istable=0` flip).
Captured in one `bench console` heredoc per the §5 schema-mutation
recipe. Sign-off?

#### Open Question 2 — `get_query` on standalone `make_control` (implementation risk)

The §1.4 Reviewer Action section requires company-scoped Account
autocomplete. `frappe.ui.form.make_control(df, parent)` accepts a
`get_query` function at form-level typically; verified behaviour
on **standalone** (no `frm`) widgets is not yet confirmed.

**Proposed resolution**: Commit-1 of implementation should include
a 15-min spike against a throwaway test page to verify
`make_control` + `get_query` + Link field works standalone. If it
doesn't, fall back to one of:

- (a) Custom `frappe.ui.form.ControlLink` subclass with
  overridden `get_query`.
- (b) Server-side filtered autocomplete via a custom whitelist
  method (`search_accounts(session_name, txt)`) + a
  `frappe.ui.form.ControlAutocomplete`.

Both fallbacks are ~30–45 min of work; (a) is preferred.

Flag here so if the spike reveals blockage, we don't lose scope
time chasing the Ideal path.

#### Open Question 3 — Account Creation Request sub-dialog in §1.6 `c` shortcut

When reviewer presses `c`, v1 options are:

- **Minimal** — flip `review_action = Pending Account Creation`,
  save, advance. Account Creation Request row is created as a
  stub with just the link back to the decision; Item 4 builds
  out the "fill in parent / is_group / type and Create Now"
  fields and the approval UI.
- **Inline dialog** — open a small dialog asking for
  `new_account_parent` + `new_account_root_type` + `is_group`
  before saving. Creates a fully-populated Account Creation
  Request in one flow. Item 4 then just builds the
  review-and-approve side.

Item 1+2 merged scope suggests **Minimal** is correct — less
coupling, respects item boundaries. Confirm?

#### Open Question 4 — Supplier Creation Request: same question

Identical shape to OQ3 for `pending_supplier_creation`. Presume
same resolution (minimal stub in Item 1+2; Item 3 builds the
Supplier Creation Request workflow). Confirm?

#### Open Question 5 — What happens if session-name path segment is `None` / URL has no segment?

`/app/mapping-decision-review` (no session) — three options:

- **a** — 404 empty state per §1.9.
- **b** — auto-redirect to most-recently-viewed session
  (Frappe `user_settings` supports this cleanly).
- **c** — render a session picker as the landing state.

Lean: **(b)** with a fallback to **(a)** if no recent session
exists. Cheap via `frappe.model.user_settings.Mapping Decision
Review.last_session`. Confirm?

#### Open Question 6 — Undo toast stretch item

Per §1.7: implementation budget scope. I'd like to hold 30 min at
end of v1 build to implement a Gmail-style 5-second Undo toast;
if budget runs tight, defer to v2. Explicit OK to treat as
stretch?

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
