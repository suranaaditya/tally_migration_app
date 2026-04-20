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

---

## §1. Mapping Decision list view

*Draft 2026-04-20. Status: open for review. No code yet.*

### Reviewer narrative — what a reviewer actually does

A reviewer lands on their Desk home after migration runs overnight or
on-demand. Their workflow per entity:

1. Open the `Tally Migration Session` form for (say) `CACSPU-2026-01`.
2. See the session summary — total ledgers parsed, tier counts,
   number of decisions needing review.
3. Click a **"Review Decisions"** button on the session form. That's
   the primary entry point.
4. Land on a filtered list, default-scoped to this session, showing
   the ~39 decisions that need attention (not the ~1,899 auto-resolved
   or definitionally-excluded rows).
5. Scan the list — a few dozen rows, one per ledger, each showing the
   Tally name, what the mapper proposed (or didn't), why it's in the
   review surface, and the reviewer's current verdict (Pending on
   first visit, something else on return visits).
6. Click a row to enter the detail view (Item 2) to resolve it.
7. Return to the list after each decision; the row they just actioned
   disappears (they were filtering by Pending) or changes colour
   (they're filtering broader).
8. Repeat until the list is empty, the session flips to "Ready to
   Regenerate", and the reviewer returns to the Session form to
   regenerate the 4 artefacts.

That's the loop. Everything below serves it.

### 1. Entry point

**Primary**: a **"Review Decisions"** button on the Tally Migration
Session form view. Clicks navigate to the list view, pre-filtered to
`parent == <this session name>` plus the default review-surface
filter (see §1.2).

This is the expected reviewer flow for 95%+ of review activity —
reviewers work one entity at a time. The session is the natural
anchor.

**Secondary**: a standard Desk sidebar entry under "Rgi Migration"
module showing Mapping Decisions across all sessions. Useful for
cross-session diagnostics (e.g. "has this Tally ledger pattern ever
been rejected by a reviewer?") and for my own debugging. Not the
main reviewer path. Provided for free by Frappe if we go with the
standalone-DocType option in Open Question #1; costs custom work if
we go List Report.

**Not provided**: a module-level "dashboard" or cross-session review
inbox. The scope call defers multi-session bulk-review UI as an
enhancement. One entity at a time, through the session, is the
migration-critical path.

### 2. Default filter state

When the reviewer lands from the Session form's "Review Decisions"
button, the list is pre-filtered to show **only the review surface** —
everything that is neither auto-resolved nor definitionally excluded.
For CACSPU that's 39 rows out of 1,964.

Concretely, the default filter is:

```
parent == <this session>
review_action in ("Pending",
                  "Pending Account Creation",
                  "Pending Group Account Resolution",
                  "Pending Supplier Creation")
```

Rationale — the `review_action` field is the authoritative
"needs-reviewer-attention" signal per the DocType design. The other
terminal states (`Approved`, `Rejected`, `Manual Override`,
`Deferred`, `Skipped`, `Excluded (P&L)`) are all "reviewer is done
with this row, hide it by default" states.

Filtering on `review_action` rather than `tier` is deliberate — it
gives us a single clean discriminator for "attention needed" that
survives re-mapping (a previously-unmapped row re-resolved by Tier-2
fuzzy in a re-run keeps its reviewer-set action even if the tier
classification changes).

A **"Show All Decisions"** filter-preset toggle should be visible
above the list — one click to drop the default filter and see all
1,964 rows for this session (diagnostic, rarely used). Use Desk's
built-in saved-filter mechanism, not a custom widget.

### 3. Available filters

Listed in expected order of reviewer usefulness. All lean on Desk's
built-in funnel-icon filter UI unless noted.

| Filter | Field | Type | Notes |
|---|---|---|---|
| **Session** | `parent` | Link → Tally Migration Session | Defaulted on entry; clearable for cross-session views. |
| **Review action** | `review_action` | Select | Primary triage axis. Desk funnel filter. |
| **Tier** | `tier` | Select | Secondary axis — "show me only the pending_supplier_creation cases." Desk funnel filter. **See Open Question #2 — the Select options list on the deployed DocType is out of date.** |
| **Root type** | `tally_root_type` | Select | Lets reviewer slice by Asset / Liability / Equity — useful for "let me clear all the bank-account name-divergence cases together." |
| **Anti-pattern blocked** | `anti_pattern_blocked` | Check | Boolean toggle. Shows decisions that hit a §11 anti-pattern (e.g. `Hostel Fee A/c`). These need the paired Account Creation Request workflow. |
| **Flagged for email** | `flagged_for_email` | Check | Already-existing field on the DocType; represents decisions the reviewer has queued for client email. Separate from the review surface. |
| **Has issues** | *(derived)* | — | Not a single database field; constructed as "`review_action` in (Pending*) OR `anti_pattern_blocked == 1`". Offered as a saved-filter preset rather than a custom field. |
| **Full-text search over Tally name** | `tally_name` | Data | Desk's standard list-search box. Indexed by default. |

**Explicitly deferred** (not in Item 1):

- Filter by "assigned reviewer" — see §1.12 below. Out of migration-
  critical scope; Frappe's built-in assignment is adequate once we
  wire a minimal assignee column.
- Filter by confidence-score range — interesting for Tier-2 fuzzy
  (Item 6), not useful today since Tier-1 decisions are either 1.0
  (rule/exact) or 0.0 (unmapped).
- Filter by "decisions touched in last N hours" — nice-to-have for
  resuming mid-session work; lean on Desk's `modified` sort instead.

### 4. Columns shown in list

The goal is one-line-per-decision scanability. Proposed column order
and approximate widths (assuming a 1440px-wide Desk viewport with the
standard Frappe sidebar):

| # | Column | Source field | Width | Sortable | Inline filter | Purpose |
|---|---|---|---|---|---|---|
| 1 | Tally Name | `tally_name` | ~240px | Yes | Yes (search) | Primary identifier. Reviewer reads this first. |
| 2 | Net Amount | `net_amount` (signed) | ~120px, right-aligned | Yes | Range | Signed currency; one column, not separate Dr/Cr — reviewers read "is this a Dr or Cr case and how big" faster from one signed number. Formatted with sign and thousands separator. |
| 3 | Side | `net_side` (Dr/Cr/Zero) | ~50px | Yes | Yes | Colour-coded hint (see §1.9) for quick visual triage. |
| 4 | Tier | `tier` | ~180px | Yes | Yes | Why the decision is in the review surface. |
| 5 | Proposed Account | `proposed_account` | ~240px | Yes | Yes | What the mapper proposed (blank for unmapped). |
| 6 | Review Action | `review_action` | ~170px | Yes | Yes | Reviewer's current verdict. |
| 7 | Confidence | `confidence` | ~70px, right-aligned | Yes | Range | Shown as raw float for now (0.00–1.00). Tier-2 fuzzy (Item 6) may later surface as a progress-bar-style indicator; not today. |

Truncated from the default list view (available in detail view, not
here): `tally_id`, `tally_parent_chain`, `tally_root_type`,
`opening_dr`, `opening_cr`, `matched_rule`, `anti_pattern_*`,
`final_account`, `final_dr`, `final_cr`, `reviewer_notes`,
`excluded_reason`, `combine_*`. Parent chain is particularly
tempting to include — it's rich diagnostic context — but at typical
CACSPU lengths (`Current Assets > Sundry Debtors > STUDENTS > ...`)
it eats horizontal space and blows up list scannability. Keep it in
the detail view and tooltip.

The DocType already ships `in_list_view=1` on `tally_name`, `tier`,
`proposed_account`, `review_action`. Adding `net_amount`, `net_side`,
`confidence` to the list means either (a) editing the DocType JSON to
flip those flags, or (b) using a `list_view_settings` JS override to
set `add_columns` at runtime. Prefer (a) — single source of truth,
committed, no client JS.

### 5. Row actions from list view

Deliberately **minimal** for first pass. The detail view (Item 2) is
where reviewers do the real work; the list is for triage and
navigation.

- **Click row → detail view** — Desk default behaviour. No custom
  wiring.
- **Checkboxes + bulk menu** — Desk default. Meaningful bulk actions
  today: *"Assign to..."* (lean on Frappe's built-in assignment),
  *"Add comment"*, *"Export selection to Excel"* (for client
  review). Bulk Approve / Bulk Reject are **not** included in Item
  1 — bulk-mutation of review_action without the detail-view context
  is exactly the kind of "reviewer clicked through without thinking"
  risk we want to avoid at this stage. Revisit after CACSPU's first
  pass tells us what reviewers actually need.

Not in Item 1 (deferred to later items or explicitly out of scope):

- Quick-approve / quick-reject inline buttons on each row (Item 2/3
  concern — these actions need detail-view context).
- "Jump to matched rule" link (detail view enrichment, Item 2).
- "Promote to Mapping Rule" action (Item 5).

### 6. Navigation

- **Enter detail view**: click row. Desk default; opens the Mapping
  Decision form. Back-button returns to list with filter state
  preserved (Desk default via URL route params).
- **Return to parent Session**: Desk shows the parent Session as a
  "Linked Document" breadcrumb on the Mapping Decision form — one
  click to go back to the session dashboard.
- **Keyboard navigation**: Desk's default (`Ctrl+↑`/`Ctrl+↓` to move
  between rows in a list, `Enter` to open) is adequate. No custom
  keybindings in Item 1.

Open Question #1 below affects navigation: if we go with Option (b)
List Report, the "click row → detail" default is still available but
the "back to list preserves filters" UX is patchier. Call this out at
decision time.

### 7. Pagination

- Default: **50 per page** (overridden from Desk's default 20 via
  DocType `grid_page_length` — already set to 50 in the JSON, though
  that's a grid setting, not list — or via `list_view_settings.page_length`
  for the list).
- 50 balances (a) most CACSPU review work fits on 1 page (~39 rows),
  (b) largest realistic entity's review surface (~100 on a
  badly-scattered entity) fits in 2 pages, (c) server-side pagination
  stays fast at this grain.
- Frappe's standard "20 / 100 / 500 / Infinite" toggle in the list
  toolbar gives reviewers an escape hatch. Server-side pagination
  (Desk default for Doctypes) handles up-to-3,000-row entities without
  tuning.
- **No infinite scroll**. Explicit page controls preserve "I reviewed
  up to row 47, where was I" context across context switches.

### 8. Empty state

When the reviewer has cleared the review surface for a session and
the default filter returns zero rows:

> **No decisions need review.**
>
> Every mapping decision for this session is either Approved,
> Rejected, or excluded by rule. You can now regenerate the four
> output artefacts from the session's **Generate** button, or
> switch to **Show All Decisions** to review what was auto-resolved.

Two call-to-action buttons below the message:

- **Back to Session** (primary) — takes reviewer to the parent session
  form, where the regenerate buttons live. This is the session-
  complete path.
- **Show All Decisions** (secondary) — drops the default filter so
  the reviewer can audit the auto-resolved rows if desired.

Implementation note — Frappe's default empty list state is a terse
"No records found." We override with `list_view_settings.onload` or
`list_view_settings.get_indicator` in the client JS, or via a
`render_empty_state` pattern. Lean approach: one small JS file that
detects the filter state and swaps the message. Not a major build.

### 9. Visual cues

**Use Desk's built-in indicator ("coloured dot") pattern** keyed on
`review_action`. Frappe renders a coloured indicator next to one
designated column via `list_view_settings.get_indicator`. Mapping:

| review_action | Indicator colour | Why |
|---|---|---|
| `Pending` | orange | "Needs attention." |
| `Pending Account Creation` | red | Blocking — downstream generators can't proceed until resolved. |
| `Pending Supplier Creation` | red | Same reason. |
| `Pending Group Account Resolution` | red | Same reason. |
| `Approved` | green | Terminal, good. |
| `Manual Override` | green | Terminal, reviewer made an explicit choice. |
| `Rejected` | grey | Terminal, reviewer rejected mapping entirely. |
| `Deferred` / `Skipped` | grey | Terminal, kicked to next session. |
| `Excluded (P&L)` | grey | Definitional exclusion, not a reviewer decision. |

Rows themselves are **not** colour-backgrounded — only the indicator
dot. Row-level background colours fight the Desk theme and tire the
eye at 50 rows per page.

No progress-bar rendering of `confidence` in Item 1 — plain float.
Tier-2 fuzzy (Item 6) is where that becomes genuinely informative.

No custom icons. Desk defaults are fine.

### 10. Performance considerations

**Target shape**: 412 decisions on CACSPU post-Decision-1 (1,552
zero-balance + 347 P&L excluded = 1,899 hidden, 65 auto-resolved + 39
review surface = 104 relevant rows visible by default, 412 in
"Show All"). Largest realistic entity observed to date: unknown —
extrapolating from §4.10 student-ledger counts, we should plan for
up to ~3,000 decisions on a messy entity.

**Server-side filtering and pagination** from day one. Frappe's
default List controller does this; we don't need to opt in.

**Index on `parent`** — automatic for child-table DocTypes. Already
present.

**Potential new indexes** (not blocking Item 1, flag for DBA-aware
review):

- `(parent, review_action)` composite — the default filter touches
  both. At 3,000 rows per session × 59 sessions × 10 review actions,
  the cardinality doesn't warrant an index today, but an entity-
  deep-dive query pattern might benefit. Revisit after the first
  real reviewer session tells us which filter combinations reviewers
  actually hit.
- `(parent, tier)` — secondary triage axis. Same deferral logic.

**Column rendering** — the 7 proposed columns are all simple
field-pull operations. No JOINs, no computed columns, no async
loaders. List render time will be dominated by Desk's own overhead.

**List Report vs DocType list** performance (ties into Open Question
#1):

- DocType list (Option a, standalone): well-trodden Frappe path.
  Performant at 3,000 rows with pagination.
- List Report / Query Report (Option b): same backend query, but
  reports have historically been slower for reviewer-interactive
  list UX in v16 (no indicator-colour support, no inline filter UX
  as rich). Not a blocker; noted.

### 11. How filters compose

**AND composition** — Desk's default list filters are all ANDed
together. Filter by `tier = unmapped` AND `review_action = Pending`
returns the intersection. This is the expected reviewer mental model
and matches every other Frappe list view in the bench.

OR composition is not available inline; reviewers who need it today
(rare) use `review_action in ("Pending", "Pending Account Creation")`
which is Desk's built-in multi-select-per-field. That covers our
actual use cases (the default filter in §1.2 uses this exact
pattern).

No custom composition UI. No "advanced query builder" in Item 1.

### 12. Reviewer identity / audit

**First pass — lean on Frappe built-ins, no custom schema:**

- `creation`, `modified`, `owner`, `modified_by` — present on every
  Frappe DocType. Sufficient to answer "who last touched this
  decision and when."
- **Frappe Assignment** — the built-in `ToDo`-based assignment
  feature. Reviewer can `Assigned to...` a decision from the list
  bulk menu or the detail form. Adds a column automatically,
  filterable. Zero custom code.
- **Comments / Activity log** — Desk's built-in comment panel on
  the detail form. Reviewers leave notes; the activity log captures
  every field change. Sufficient audit trail for migration
  operations; we do not need a custom review-log child table in Week
  4.

**Add one cheap column** in the list view: `modified_by` (as "Last
Edited By"). Gives reviewers at-a-glance "someone else is working
this entity, don't stomp." ~50px.

**Deferred to a later item** (not Item 1):

- Field-level change history in the list (use detail view's Activity
  panel instead).
- "Approve/Reject with reviewer-specific notes" field — `reviewer_notes`
  already exists on the DocType; wiring its UX is Item 2's detail
  view concern.
- A separate "reviewer sign-off" state machine beyond `review_action`
  — not needed for the migration path; `review_action=Approved` is
  the terminal state.

---

### Open questions for Aditya (list view design)

Unresolved choices that block implementation. Prose does not improvise
these.

#### Open Question #1 — Standalone DocType vs List Report vs custom page

Mapping Decision is currently `istable=1` (child of Tally Migration
Session). Desk's free list view + filtering + indicator UX assumes
`istable=0`. Three paths forward:

**Option (a) — flip `istable=0` and add a `session` Link field.**

- Pros: Desk's full list view for free — funnel filters, indicator
  colours, bulk actions, Assignment, Comments, saved views, Export,
  Report view. Detail view is the standard DocType form — zero custom
  work. Navigation from list to detail to parent session is Desk
  default. Every piece of custom work in Items 2-9 builds on standard
  Frappe surfaces.
- Cons: breaks the parent-child containment model. Existing child-
  table child rows on sessions need migrating (one-off bench script
  iterating all sessions, copying rows into the standalone table,
  then clearing the child field). Generators change from
  `session.append("mapping_decisions", row)` to
  `frappe.get_doc({"doctype": "Mapping Decision", "session": session.name, ...}).insert()`.
  Parent session still needs a convenient "show all my decisions"
  link — a dashboard_fields hook or the existing `mapping_decisions`
  table gets re-purposed as a virtual display (not persisted).
- Migration risk: moderate. 170 existing tests reference the child-
  table path; need audit. Schema migration is a one-time bench
  script; reversible if we hate it.

**Option (b) — List Report (Script Report or Query Report).**

- Pros: no schema change. Keeps parent-child semantics intact. Can
  serve the list view without touching the DocType.
- Cons: List/Query Reports in v16 do NOT support `get_indicator`
  colour coding, do NOT support the same inline bulk actions, do NOT
  have the same filter UX richness, and routing from a report row to
  a detail form is clunkier (typically needs a `standard_filter`
  column wiring). Row-level "open detail view" needs custom JS.
  Item 2's approve/reject UI builds on the detail form anyway — the
  report doesn't help us there. The gap between "List Report as list
  view" and "full DocType list view" will show up as custom JS in
  Items 2-9.
- Migration risk: low up front, but the custom-JS debt accumulates.

**Option (c) — custom page / Frappe Portal page / bespoke route.**

- Pros: maximum control.
- Cons: maximum build cost. Reinvents every piece of Frappe
  infrastructure. Out of scope for migration-critical Week 4.

**My lean: Option (a).** The standalone-DocType schema migration is a
~2-hour job on the bench and in the tests; the payoff is every
subsequent review-UI item (Items 2-9) building on standard Frappe
surfaces instead of fighting them. The "break the parent-child
containment" concern is largely aesthetic — Mapping Decisions are
already keyed to their session logically via the parser/mapper
pipeline, and a `session` Link field preserves that link explicitly.

Your preliminary lean was Option (b). Two specific asks:

1. What's driving the (b) lean — the schema migration cost, or
   something about the parent-child containment we should preserve?
2. If Option (a) implementation reveals a gotcha (e.g. a test that
   relies on child-table idx ordering), is the fallback to (b) or do
   we pause and redesign?

#### Open Question #2 — `tier` Select options schema drift

The deployed DocType's `tier` field has these options:

```
tier1_exact
tier1_rule
tier1_pattern
tier2_fuzzy
tier3_claude
unmapped
excluded_pnl
group_refused
anti_pattern_blocked
pending_account_creation
```

The mapper / generators now write additional tier values that are
**not** in the Select enum:

- `excluded_zero_balance` (Decision 1, commit `f56bd5d`)
- `tier1_supplier_fuzzy` (Work Item 6)
- `pending_supplier_creation` (Work Item 6)

Frappe's Select field is permissive at write-time (values outside the
enum are stored), but the list-view filter dropdown only offers the
enum values — so a reviewer filtering by `tier = excluded_zero_balance`
wouldn't see the option in the UI. This blocks §1.3's "filter by tier"
design.

**Proposed resolution, included in Item 1 implementation scope**:
extend the `tier` Select options to the full set by editing the
DocType via the §5 schema-mutation recipe. One-line change. Re-run
`bench migrate`. Trivial.

Confirm: resolve as part of Item 1 implementation, or split into its
own precursor Work Item?

#### Open Question #3 — Multi-session cross-review inbox

Do any RGI reviewer workflows legitimately need a cross-session view
("show me everything pending across all 59 entities")? My read of
the scope call is **no** — one entity at a time, session-by-session,
through the session form. But the secondary entry point (§1.1) only
exists if that assumption holds. If you foresee a cross-session
inbox usage, we should design for it now in Item 1 (minor filter/URL
work) rather than retrofit.

#### Open Question #4 — Indicator colour palette

I've proposed orange/red/green/grey per §1.9. Any accessibility or
brand concerns? Frappe's indicator palette is `red|orange|yellow|green|blue|purple|grey|darkgrey|pink|lightblue` — we can re-map if
needed.

---

### Explicit non-goals for Item 1

Documented here so later items inherit the scope cleanly:

- **Detail view rendering of proposed candidates** — Item 2.
- **Approve / Reject / Override action wiring and downstream state
  mutation** — Item 3.
- **Creating a Supplier / Account Creation Request from a decision
  row** — Items 3 and 4.
- **Promoting a reviewer decision to a `Mapping Rule` or `Supplier
  Alias Rule`** — Item 5.
- **Generating Tier-2 fuzzy candidates for unmapped rows** — Item 6.
- **Session-lifecycle concerns** (reparse-and-remap, cache
  invalidation, session persistence) — deferred as enhancement per
  the scope call.
- **Mapping Decision Event / audit-log child DocType** — deferred as
  enhancement per the scope call.
