# Week 4 — deferred items

Running list of items surfaced during Week 4 review-UI implementation
that were scoped out of the commit they were found in, but need to
land in a subsequent commit before Week 4 is declared complete.
Keep entries short; when a deferred item lands, delete its section
here and reference the landing commit in the change log.

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

## "Open in full form" button (Section 6) — bench URL routing quirk

**Raised:** Commit 4b (2026-04-21). **Re-deferred:** Commit 6 (2026-04-22) —
attempted fix in Commit 6 confirmed the issue is bench-level, not our
code.

**Symptom:** Clicking "Open in full form" in Section 6 Audit lands on
"Page mapping-decision not found" (Frappe's legacy desk-Page lookup
error). Reaching the form via the URL bar fails the same way.

**What Commit 6 tried:** Switched the link from `<a href="/app/...">`
to a scripted `window.open` (Path B), then to
`frappe.set_route("Form", "Mapping Decision", name)` (Path A —
canonical Frappe in-SPA navigation). Both fail with the same symptom.

**Root cause confirmed via curl probes:**

```
/app/mapping-decision/<name>      → 301 → /desk/mapping-decision/<name>
/desk/mapping-decision/<name>     → "Page mapping-decision not found"
/app/account (any DocType)        → 301 → /desk/account  (same redirect chain)
```

This bench redirects every `/app/...` URL to `/desk/...`, but the
desk router on this bench treats the first path segment after
`/desk/` as a Page name (legacy Frappe routing) and 404s on DocType
slugs. Frappe's own helper `frappe.utils.get_url_to_form('Mapping
Decision', '<name>')` returns the broken `/desk/...` URL too — so the
backend agrees the URL is canonical, but the frontend desk router
disagrees.

**Likely culprits to investigate (separate session):**

- nginx config doing the `/app/` → `/desk/` rewrite (probably from
  pre-v15 era when desk lived at `/desk/`)
- frappe `hooks.py` `website_redirects` doing the rewrite
- bench's Frappe version having a partial `/app/` router that fell
  back to legacy `/desk/` routing for non-Page URLs
- Custom `Page` records named after DocType slugs interfering

**Code currently shipped (Commit 6):** `_wire_audit_actions` in
`md_review.js` calls `frappe.set_route("Form", "Mapping Decision",
name)` on click. This is the *correct* canonical Frappe API — the
button will start working as soon as the bench routing is fixed.

**Not blocking Item 1** — full-form access is rare (audit / debug
use only). Reviewers do all editing in the detail pane.

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
