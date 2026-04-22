# Week 4 — deferred items

Running list of items surfaced during Week 4 review-UI implementation
that were scoped out of the commit they were found in, but need to
land in a subsequent commit before Week 4 is declared complete.
Keep entries short; when a deferred item lands, delete its section
here and reference the landing commit in the change log.

---

## Auto-approve tier-1 matched decisions

**Raised:** Commit 4b Phase D.3 browser verification (2026-04-22).

**Problem:** CACSPU has ~115 tier-1 matches (`tier1_exact` +
`tier1_rule` + `tier1_pattern`) with high-confidence
`proposed_account` values. Requiring the reviewer to click through
each one and press Save is tedious for zero decision value — the
reviewer's time is better spent on the ~39 actually-pending
decisions that require judgement. 59 entities × 115 tier-1 matches
× manual save = ~6,800 unnecessary clicks across the project.

**Options:**

- **Path A — server-side auto-resolution in the mapper.** Tier-1
  matches with `proposed_account` set would be written into
  `Mapping Decision` with `review_action = "Approved"` and
  `final_account = proposed_account` during the mapper run. The
  reviewer never sees these rows in the Pending filter. Pro: no
  reviewer action needed. Con: loses the reviewer's opportunity to
  catch a systematically-wrong tier-1 rule before it's treated as
  approved.
- **Path B — frontend bulk-approve button.** "Approve all tier-1
  matches" action on the review page. Reviewer clicks once,
  backend bulk-saves all Pending tier-1 rows that have a
  `proposed_account`. Pro: reviewer still has eyeball-level
  oversight; can spot-check before committing. Con: still one-click
  per session rather than zero.
- **Path C — bulk-actions UX.** Full row-select + bulk-action
  pattern (checkbox column in the master pane, footer "approve
  selected" button). Deferred in §1.10 scope fence — broader scope
  than we need for this specific problem.

**Recommendation:** Path B for speed-to-ship. Can land in Commit 5
or as a separate Commit 4c depending on Commit 5's scope weight.

**Not blocking Commit 4b** — reviewer can still save tier-1 matches
one by one. Just tedious.

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

## "Open in full form" button (Section 6) — broken link

**Raised:** Commit 4b Phase C walk-through (2026-04-21).

**Problem:** The "Open in full form" link in Section 6 Audit
points to `/app/mapping-decision/<name>` but does not actually
navigate when clicked (observed by Aditya). Possibly a missing
`target` attribute handling or event-delegation intercept.

**Fix scope:** Single-file investigation on
`rgi_migration/rgi_migration/page/md_review/md_review.js` (the
`_render_audit` method renders the `<a>` tag). Likely one-line fix
once reproduced.

**Not blocking Commit 4b** — cosmetic; full form is reachable via
the URL directly.

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
