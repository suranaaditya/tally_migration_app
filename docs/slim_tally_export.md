# Slim Tally Export + Upload Architecture — Research & Recommendation

**Status:** Research doc. Nothing is built yet. This informs the Week-3
upload widget design and the per-entity Tally export procedure that goes
in the bookkeeper runbook.

**Problem:** the full Tally "All Masters" XML for CACSPU is **221 MB**;
Frappe's default file-upload limit is **10 MiB**. Multiply across 59
entities and this becomes operational pain: broken uploads, slow server
transfers, bookkeeper-facing errors, and engineering time retrofitting a
chunked-upload widget. The research below argues we should avoid the
chunked-upload rabbit hole entirely by producing a ~10–30 MB *slim*
Tally export that fits Frappe's stock upload path.

---

## 1. Frappe file-upload facts (v15)

| Concern | Default | Where to change | Notes |
|---|---|---|---|
| `max_file_size` | **10 MiB** | `sites/<site>/site_config.json` | Global cap on uploads via `/api/method/upload_file`. Setting it alone is not sufficient if nginx is in front. |
| nginx `client_max_body_size` | 1 MB (stock nginx) | `nginx.conf` or `sites-enabled/<site>.conf` | Must be bumped to ≥ `max_file_size`, else nginx rejects with 413 before Frappe ever sees the request. This is the most common "I set max_file_size and it still fails" trap on the Frappe forum. |
| HTTP request timeout (gunicorn) | 30 s | `common_site_config.json` → `http_timeout`, or `bench config http-timeout <sec>` | Large uploads can hit this even when size is OK. Raising to 120–300 s is the conventional fix for bulk imports. |
| Background-job time | unbounded (RQ) | N/A | Any work that happens *after* the upload (parse, map, generate) runs in a `frappe.enqueue`d RQ worker and has no inherent time cap. |
| Chunked upload API | present but undocumented for third parties | `/api/method/uploadfile` with `chunk_index` / `total_chunks` params | Available but not a stable contract. Introduces client-side coordination, resume logic, and cleanup of partial files. Most Frappe apps skip this in favour of bumping `max_file_size`. |
| `File` DocType storage | filesystem or S3 | `use_ssl`, `s3_*` keys | For our scale (59 entities × ~20 MB) even local disk is fine; ~1.2 GB total. |

**Operational implication.** With a one-time config change
(`max_file_size: 50000000` + `client_max_body_size 50M` + `http_timeout
120`), the standard Frappe upload path accommodates anything up to
~50 MB. That covers a slim Tally export with comfortable headroom, and
none of the 59-entity rollout needs a custom uploader.

---

## 2. Why "All Masters" is 221 MB — and what's actually in it

A plain "All Masters" export emits every master Tally knows about. On
the CACSPU fixture the XML breaks down roughly as:

| Master type | Approx share of file | Used by our parser? |
|---|---:|---|
| `STOCKITEM` | ~40–50% | **No** — inventory, irrelevant to opening-balance migration |
| `UNIT` (units of measurement) | small but many | **No** |
| `GODOWN` (warehouses) | small | **No** |
| `COSTCENTRE` / `COSTCATEGORY` | variable | **No** for now; may matter in Week 3 for cost-centre-tagged JE lines |
| `VOUCHERTYPE`, `BUDGET`, `NARRATIONS`, `EMPLOYEE` groups | small | **No** |
| `GROUP` (accounting groups) | ~1% | **Yes** — hierarchy |
| `LEDGER` (+ `OPENINGBALANCE`, `BILLALLOCATIONS.LIST`) | ~45–50% | **Yes** — the whole point |
| `CURRENCY` | trivial | Yes — envelope requirement |

Ledgers plus groups are the only masters our parser touches today
(see [sample_cacspu_masters_sample.xml](../rgi_migration/tests/fixtures/sample_cacspu_masters_sample.xml)
— our 10 MB committed sample is effectively "Groups + first 500 Ledgers
+ 13 diagnostic Ledgers" and passes every regression assertion).

**A TDL-driven export that emits only the three used collections should
land at 20–35 MB for a large entity** (CACSPU has ~6,000 ledgers; a
back-of-envelope from the sample's size-per-ledger ratio gives ~28 MB
for the full ledger universe). Well within the 50 MB Frappe budget.

---

## 3. Slim TDL — concept sketch (not a working template)

Tally's TDL (Tally Definition Language) lets us define a custom Report
that writes XML containing only specified Collections. The structural
shape is approximately:

```tdl
[#Menu: Gateway of Tally]
    Add: Item: CACSPU Slim Export : Display : CACSPU Slim Export Report

[Report: CACSPU Slim Export Report]
    Form   : CACSPU Slim Export Form
    Object : Company
    Export : Yes
    XML Tag: "ENVELOPE"

[Form: CACSPU Slim Export Form]
    Parts: CACSPU Slim Export Part

[Part: CACSPU Slim Export Part]
    Lines  : CACSPU Slim Export Line
    Scroll : Vertical
    Repeat : CACSPU Slim Export Line : Accounting Groups, Accounting Ledgers

[Collection: Accounting Groups]
    Type   : Group
    Fetch  : Name, Parent, IsDeemedPositive, ReservedName, PrimaryGroup

[Collection: Accounting Ledgers]
    Type   : Ledger
    Fetch  : Name, Parent, OpeningBalance, IsBillWiseOn, ReservedName, BillAllocations.*
    ; BillAllocations.* pulls BillAllocations.Name / Amount / OpeningBalance
    ; — exactly the fields our parser already consumes.

[Line: CACSPU Slim Export Line]
    Fields : Name Field
    XMLTag : "$$MasterType"
```

**Properties of the approach:**

- **Deterministic output shape.** Same `<ENVELOPE><BODY>...<TALLYMESSAGE>`
  structure as stock "All Masters" export — our parser already handles
  it, no parser changes needed.
- **Naturally excludes** STOCKITEM, UNIT, GODOWN, COSTCENTRE,
  VOUCHERTYPE, EMPLOYEE, etc. — the ~50%+ of the file we throw away.
- **Works with "Export closing balances as opening balance" setting.**
  That's an export-time option on the Report; it operates on whatever
  Collections the Report enumerates.
- **Bill-wise allocations preserved.** The `BillAllocations.*` fetch
  keeps the parser's `BillAllocation` dataclass populated — essential
  for the raw-XML residual diagnostic (§6.1 in sign-convention doc).

**What's NOT in this doc:** the actual tested-working TDL. Writing and
debugging TDL requires hands-on Tally — it's a two-hour job for Aditya
or a Tally-familiar developer with a test entity, not a dry exercise
from specs. Recommend Week-3 deliverable.

**Estimated output size** (extrapolating from the committed 10 MB,
587-ledger sample which is itself a subset of the full 221 MB file):
~28–35 MB for the full CACSPU universe. At the upper end, still under
the 50 MB Frappe budget.

---

## 4. Recommended upload architecture

### Three lanes, keyed on who's uploading and what they have

**Lane A — internal migration team (Aditya + engineering): full XML via
server-side drop.**
- Full 221 MB XML is copied to the server's
  `~/tally-exports/<abbr>_masters.xml` (convention) — outside Frappe's
  `sites/` tree, outside any repo, no web upload at all.
- `Tally Migration Session.source_file_server_path` holds the absolute
  path; the parser reads from disk directly.
- Zero upload risk; always works.
- Target use: the first 3–5 entities where we're still calibrating
  rules, and any entity where the slim TDL export fails for reasons we
  haven't diagnosed yet.

**Lane B — bookkeeper at the entity site: slim TDL export via Frappe
upload.**
- Bookkeeper runs the (installed-once) slim TDL export. Output is
  ~20–30 MB XML.
- Uploads via the standard Frappe file widget attached to the
  `Tally Migration Session` DocType. Upload takes 5–15 s on a typical
  office connection.
- `site_config.json` configured with `max_file_size: 50000000` + nginx
  `client_max_body_size 50M` + `http_timeout 120`.
- No chunking, no resume logic, no custom uploader. Standard Frappe
  attach-file UX the bookkeepers already know.
- Target use: the 50+ entities where we've converged on a rule library
  and parse-and-map is largely routine.

**Lane C — fallback: any entity where slim TDL export is broken or
bookkeeper can't install the TDL.**
- Bookkeeper exports stock "All Masters" (221 MB → local disk), sends
  via internal file-sharing (Teams, SFTP, shared drive).
- Migration team drops the file on the server and runs via Lane A.
- Documented procedure, never the *first* option but the *always works*
  option.

### Why this split rather than a chunked-upload widget

| Option | Eng cost | Fails how | Good for |
|---|---|---|---|
| Chunked upload, resume, retry | weeks to build+test | Hangs, partial uploads, server disk fills with abandoned chunks, browser timeouts | Files you can't split; bookkeepers with flaky internet |
| **Slim TDL + stock upload (Lane B)** | days (TDL) + 30 min (config) | TDL operator error, rare | 90% of entities |
| Server-side drop (Lane A / fallback) | zero | Never | First entities, edge cases, whenever slim TDL breaks |

Chunked upload is the most expensive option with the worst failure
mode. The slim TDL + Lane A fallback covers the same ground with no
custom upload code and a well-trodden Frappe config path. Further,
the slim TDL is *reusable* per entity for free; every future
entity-year opening migration uses it.

### Field-level change on `Tally Migration Session`

Today the schema (docs/mapper_design_notes.md equivalent) carries:

- `source_file` — Frappe `Attach` field, used by Lane B
- `source_file_server_path` — Data field, used by Lane A
- `source_file_size_mb`, `source_file_sha256` — populated by whichever
  lane loaded the file

Validation at Session-create time: exactly one of the two must be
populated. If neither, the form can't advance past `Draft`.

---

## 5. Recommended Frappe site-config deltas

```json
// sites/<sitename>/site_config.json
{
    "max_file_size": 50000000,
    "http_timeout": 120
}
```

```nginx
# conf/nginx.conf or sites-enabled/<site>.conf
client_max_body_size 50M;
```

These three changes together are the full set required to support Lane
B. None of them affect Lane A (server-side drop bypasses the upload
path entirely). No custom app code.

---

## 6. Decisions to land before Week 3 builds this

1. **Do we invest the ~2 hours to write + test the slim TDL this
   sprint**, or defer to Week 3 and rely on Lane A for the first
   entities? My recommendation: **defer** — rules library + JE builder
   are the higher-leverage work, and Lane A trivially handles entities
   1–3 while we're still calibrating.
2. **Bookkeeper training plan for Lane B.** One-page runbook, screen
   recording of the slim TDL export procedure, written by Aditya once
   the TDL is verified. Not engineering work.
3. **What happens if a bookkeeper uploads stock "All Masters" by
   mistake** (221 MB into a 50 MB field)? Clean rejection with a link
   to the runbook is fine; no engineering work.

## 7. What this doc explicitly does NOT recommend

- A custom chunked-upload widget. Reasons above.
- Client-side XML compression (gzip) prior to upload. Adds complexity
  for ~3× size reduction that the slim TDL already achieves at higher
  fidelity (and in human-readable XML the server can diff).
- A Flask/Frappe-side streaming parser that runs while the upload is
  in flight. Tempting for the 221 MB case, but our parser uses
  `lxml.etree.parse` which needs the complete file; refactoring to
  iterparse is real work with no upside once Lane B is in place.
- Moving the whole migration to a sidecar service outside Frappe.
  CLAUDE.md's "In-app, not external" architecture decision is
  re-validated by this research.

---

## 8. Sources

Primary references consulted for this document:

- [Frappe Framework v15 Configuration docs — `max_file_size` default, related keys](https://docs.frappe.io/framework/v15/user/en/basics/site_config) — official source for the 10 MiB default.
- [Frappe Forum — file-upload size / nginx `client_max_body_size` interaction](https://discuss.frappe.io/t/how-to-upload-large-file-size-more-than-200mb/98344) — community confirmation that both Frappe and nginx caps must be raised together.
- [Frappe GitHub Issue #26397 — 25 MB default mismatch discussion](https://github.com/frappe/frappe/issues/26397) — background on how the limit is enforced.
- [Tally Integration Capabilities — Case Study 3 (Ledger Master export via TDL)](https://help.tallysolutions.com/article/DeveloperReference/integration-capabilities/case_study_3.htm) — conceptual foundation for the TDL approach.
- [Tally TDL Reference — Objects and Collections](https://help.tallysolutions.com/article/DeveloperReference/tdlreference/objects_and_collections.htm) — Collection syntax used in §3 sketch.
- [Tally Help — Sample XML / envelope structure](https://help.tallysolutions.com/sample-xml/) — confirms the output shape matches what our parser already consumes.
- [AAPL Automation: Exporting Ledgers & Stock Items of Selected Group in XML (PDF)](https://www.aaplautomation.com/tally_img/1616499479Export%20Ledgers%20&%20Stock%20Items%20of%20Selected%20Group%20in%20XML.pdf) — a third-party TDL example the bookkeeper-facing runbook can crib structure from.
