# Week 4 — deferred items

Running list of items surfaced during Week 4 review-UI implementation
that were scoped out of the commit they were found in, but need to
land in a subsequent commit before Week 4 is declared complete.
Keep entries short; when a deferred item lands, delete its section
here and reference the landing commit in the change log.

---

## Future design discussions

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

## `reset_parse` does not sweep the SCR / ACR child tables

**Raised:** Item 3 Commit 4 closing smoke (2026-04-22).
**Scope extended:** Item 4 Commit 1 Phase C (2026-04-23) — confirmed
ACR child table has the identical gap.
**Target:** Item 4 Commit 3 (combined fix, both child tables in one
patch).

**Problem:** `rgi_migration.rgi_migration.doctype.tally_migration_session.tally_migration_session.reset_parse`
deletes every `Mapping Decision` row for the session and clears the
parse/map snapshot fields, but it does NOT clear the
`supplier_creation_requests` OR `account_creation_requests` child
tables on the session doc. Any SCR / ACR rows from a prior run
persist; their `source_decisions` CSV tokens now reference deleted
MD names (orphaned). On re-run of `run_mapper`, the child tables
remain populated with stale history while the underlying decisions
are freshly minted under new MD-YYYY-##### autonames.

**Current smoke workaround:** Item 3 closing smoke
(`scripts/item3_closing_smoke.py`) calls a helper
`_clear_scr_child_table(session_name)` that writes
`session.supplier_creation_requests = []` and saves. Reviewers doing
Reset Parse from the UI do not get this sweep today. No equivalent
helper exists yet for ACR rows — Item 4 Commit 4 smoke should either
add a parallel helper or (preferably) rely on the in-product fix
landing in Commit 3.

**Observed symptoms:** none yet in reviewer flow — orphan SCRs/ACRs
are inert (no references point to them from live decisions, and the
Process-* dialogs filter to `status=Pending/Failed` which may still
display stale rows). Worth clearing to avoid confusing the reviewer.

**Fix shape (Item 4 Commit 3):** add both child-table clears inside
`reset_parse` between the Mapping Decision delete and the
`session.save()`:

```python
session.set("supplier_creation_requests", [])
session.set("account_creation_requests", [])
```

Write a unit test that seeds decisions + one SCR + one ACR, calls
`reset_parse`, and asserts both child-table lengths are 0.


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
