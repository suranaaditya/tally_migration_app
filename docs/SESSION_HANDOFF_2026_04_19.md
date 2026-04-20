# Session Handoff — 2026-04-19 / 2026-04-20

**For:** next Claude Code session picking up rgi_migration Week 3.
**Reading time:** 10-15 min. Read end-to-end before touching code.

---

## TL;DR

Week 3 is mid-flight. Work Items 1-6 are done; Work Item 7 (JE + OIT +
party-advance JE + Students CSV generators) is about to start but is
blocked on three pre-flight answers from Aditya.

The repo is on `claude/unruffled-hellman-c54c95`, latest commit
`ce4d3ce`, fully pushed to GitHub. The working tree is clean across
both the main repo and the Claude Code worktree. 86 pytest tests pass
(1 pre-existing skip). 22 Mapping Rules are seeded on the live dev
bench (`erp.jewonline.in`).

Before doing anything, read:
- `CLAUDE.md` — project-wide architecture decisions
- `docs/mapper_design_notes.md` — canonical design principles (§1-§7)
- `docs/week2_baseline_mapping.md` — full-file hit-rate baseline
- `RGI_Migration_Rules.md` — the canonical rule library source

---

## 1. Current state snapshot

### Git

| Tree | Branch | HEAD | Status |
|---|---|---|---|
| Main repo | `main` | `02ed6f7` (Week-1 state, 2 commits total) | Clean w.r.t. tracked files. Two untracked paths (`.claude/`, `cacspu_erpnext_coa_real.csv`) are NOT WIP — they're artefacts of main sitting at Week-1 while real work lives on the claude branch. |
| Worktree | `claude/unruffled-hellman-c54c95` | `ce4d3ce` | Clean, in sync with `origin`. |

**Tags**: `week1-complete` (02ed6f7), `week2-complete` (6097024). No
`week3-complete` yet — tag only when Work Item 7 lands and all four
generators produce reviewed output.

**Remote**: `https://github.com/suranaaditya/tally_migration_app.git`
(GitHub, private). Only `claude/unruffled-hellman-c54c95` is pushed —
main is local-only by design (Week-1 snapshot), never push it.

### Recent 10 commits (worktree)

```
ce4d3ce fix: Student Fee Outstanding routes to dux_voucher via parser flag
439346e fix: pause sec 4.10 Student Fee Outstanding — leaf-only posting principle
03d5673 docs: fuzzy false-positive pattern from jewonline dev-bench baseline
96aaaf1 docs: control-account routing principles + pattern list
8449ee5 fix: control account routing — rule-first ordering + pattern list
7cd5136 feat: Work Item 6 — Tier-1 supplier resolution for vendor party ledgers
fc60ffd data: jewonline supplier master export for Work Item 6 testing
be64356 feat: SupplierSource abstraction scaffold (stub for Work Item 6)
6fbd410 fix: seed_workflow sys.path uses one .parent not two
16b94ad feat: Work Item 5 seed orchestration module
```

### Week 3 progress

| # | Work Item | Status |
|---|---|---|
| 1 | Push to GitHub (branch + tags) | ✅ done |
| 2 | Prerequisite check (Frappe 16.12, site erp.jewonline.in, ORM alive) | ✅ done |
| 3 | Frappe app scaffold (bench new-app + our code + install + migrate) | ✅ done |
| 4 | 9 DocTypes via bench console (+ schema drift audit patches) | ✅ done |
| 5 | Seed 22 Mapping Rules to production DocType | ✅ done |
| 6 | Supplier matching (party-ledger detection + three-layer resolver) | ✅ done |
| 7 | JE + OIT + Party-advance JE + Students CSV | ⏸ **about to start; blocked on pre-flight** |
| 8 | Slim TDL (bookkeeper-facing export template) | ⏸ deferred to end of Week 3 |

### Numbers

- **22 Mapping Rules** on erp.jewonline.in (19 positive + 3 anti-pattern; §4.10 deleted).
- **9 DocTypes** in the Rgi Migration module (4 standalone + 5 child tables).
- **4 rules** with `creates_erpnext_account=1` (was 5 before §4.10 deletion).
- **86 pytest tests pass** + 1 pre-existing skip (full-file balance assertion — opt-in via `RGI_FULL_XML_PATH`).

### Full-file mapper baseline (post-§4.10 deletion, CACSPU 221 MB)

```
total:                     1964
tier1_rule:                  11
tier1_exact:                 97
tier1_pattern:                2
tier1_supplier_fuzzy:         7   (all dev-bench false positives — see §2c)
group_refused:                3
pending_account_creation:     1
pending_supplier_creation:  336
excluded_pnl:               347
unmapped:                  1160
hit_rate:                   0.409
```

`student_ledgers: 4063` (includes the aggregate `Student Fee
Outstanding` row as of commit `ce4d3ce`).

---

## 2. Recent architectural decisions (and why)

### a) §4.10 journey — pause → delete → parser-level routing

**Why it changed**: §4.10 (`Student Fee Outstanding → Student Fee
Outstanding (Receivable) - {ABBR}`) was originally seeded as a
confirmed positive rule. Three iterations:

1. **Pause** (commit `439346e`): per the leaf-only posting principle,
   `Student Fee Outstanding` is usually a Tally GROUP on educational
   entities and Tally never emits groups as `<LEDGER>` elements. Paused
   to avoid dead code.
2. **Full-file run revealed** it WAS matching on CACSPU — as a
   zero-balance LEAF ledger. Previously produced an Account Creation
   Request for a redundant `... (Receivable)` variant; post-pause,
   fell through to exact-name match on `Student Fee Outstanding -
   CACSPU` (already in COA). Net effect: −1 pending_account_creation,
   +1 tier1_exact.
3. **Delete** (commit `ce4d3ce`): realized the routing is an
   **architectural boundary** (student receivables belong to
   dux_voucher), not per-entity business logic. Deleted from the live
   DocType, removed from `docs/seed_plan.json` and
   `scripts/seed_mapping_rules.py`. Routing now happens at **parser
   level** via `AGGREGATE_STUDENT_ACCOUNT_NAMES` frozenset in
   `rgi_migration/parsers/tally_xml_parser.py`.

**Bottom line**: student ledgers (per-student leaves AND aggregate
control accounts) are routed by the parser to `student_ledgers`.
They never reach the mapper. The student CSV is handed off to
dux_voucher's Ex Student Opening Batch.

**DO NOT** re-seed §4.10. **DO NOT** add rules for student-related
names to the Mapping Rule library. **ADD** new aggregate names to
`AGGREGATE_STUDENT_ACCOUNT_NAMES` in
`rgi_migration/parsers/tally_xml_parser.py` (code edit).

### b) Leaf-only posting principle — docs/mapper_design_notes.md §7

Both Tally and ERPNext forbid posting to group accounts; all
postings are at leaf level. This is a **correctness requirement**,
not a defensive check.

Corollary: a rule targeting a Tally name that's typically a GROUP can
never fire (Tally doesn't emit groups as `<LEDGER>`). The
group-account refusal validator (`docs/mapper_design_notes.md §2(b)`)
catches any residual group proposal at map time.

Cross-app corollary: some names represent sibling-app territory
(dux_voucher for students). Parser routing is the mechanism; Mapping
Rules are the wrong shape.

### c) Supplier matching — fuzzy false positives are expected on dev bench

Work Item 6 introduced three-layer supplier resolution:

1. Layer 1: `exact_ci` on `supplier_name` (case/whitespace tolerant)
2. Layer 2: saved `Supplier Alias Rule` hits — STUB until reviewer
   promotion workflow lands (Week 4+)
3. Layer 3: rapidfuzz WRatio fuzzy match, threshold 85%

On the dev bench's 10-supplier master (jewonline), Layer 3 produces
**7 false positives** — all driven by short generic names
(`Amit`, `Hardware`, `Traders`). Documented in
`docs/mapper_design_notes.md §6.4`. **DO NOT** tighten the threshold
based on dev-bench results — against production supplier masters
(hundreds of longer, more distinctive names), these coincidences are
outscored. Future mitigation is negative alias rules, not threshold
changes.

### d) Rule-first ordering + control-account pattern list — commit 8449ee5

`Mapper.resolve()` runs positive-rule matching BEFORE party-ledger
routing, because §4 rules sometimes target ledgers that live under
Sundry Creditors (§4.6 `Unpaid Expenditure Account`, §4.7
`Payable A/c`). Caught as a regression at prose-review time:
`tier1_rule` dropped from 11 to 10 between Week-2 baseline and initial
Work Item 6.

Plus **8 hardcoded control-account patterns** in
`rgi_migration/mapper/tier1_supplier.py` (Advances Received, TDS
Payable, Tax Collected, Provision for, Suspense A/c, Unadjusted, GST
Payable/Input/Output, Round off) that short-circuit party routing
even when the ledger sits under Sundry Creditors. Full pattern list +
rationale in `docs/mapper_design_notes.md §6.3`.

Future: `Control Account Pattern` DocType so ops staff add patterns
without code edits. Deferred to Week 4+.

### e) Schema-mutation recipes for v16 — docs/mapper_design_notes.md §5

Painfully earned during the Work Item 4 audit. Key findings:

- **DocField rename**: edit `DocType.fields` in place and `.save()`.
  `save()` adds new columns but does NOT drop old ones (orphan
  cleanup is a separate step).
- **Orphan SQL column cleanup**: raw `ALTER TABLE … DROP COLUMN`
  flanked by `frappe.db.commit()` on both sides. Without commits
  you hit v16's `ImplicitCommitError` guard.
- **APIs that don't exist in v16**: `rename_field` (in any module).
- **`bench execute`** has a buggy fallback-to-`eval()` path that
  throws spurious `NameError` for complex module paths. Prefer
  `bench --site X console` with a Python heredoc for anything
  non-trivial.
- **DocField filter key is `source_section`**, NOT
  `source_section_ref`. The latter name existed briefly during
  Batch-2 (Aditya's first attempt) and was renamed via the Work
  Item 4 audit (commit `9182539`). Any snippet that filters
  `{"source_section_ref": ...}` is stale.

### f) Discipline contract — prose design before code on Work Item 7

After I ignored the step-by-step discipline during Work Item 6 and
"got lucky" with a clean outcome, Aditya called it out clearly: good
outcomes from skipping checkpoints reinforce the wrong instinct. Work
Item 7 runs with **strict** prose → pause → implement → pause per
generator. See §4 below for the contract.

---

## 3. Immediate pending tasks (what to do first when resuming)

### Pre-flight — needs Aditya's inputs

Five items blocking Work Item 7 kickoff:

1. **Company Abbreviation target on the dev bench.** `erp.jewonline.in`
   has `Dux Digitech (DD)` and `Jain Engineering Works (JEWIPL)` but
   no `CACSPU` / `GHR CACS Pune` Company record. Does Work Item 7
   create one, or generate the JE Draft targeting an existing dev
   company as a stand-in?
2. **Fiscal Year** for the opening balance — likely `2026-2027` per
   RGI rules §6.1, but confirm.
3. **Fresh pre-Work-Item-7 backup path** on the server. I want a
   restore point committed before any Journal Entry DocType records
   get written.
4. **Student ledger structure check** in the Tally XML — Week 1's
   flagging accuracy. **Partially green**: post-commit `ce4d3ce`
   verified `Student Fee Outstanding` now routes to student_ledgers.
   Broader sweep still welcome.
5. **Parser-extension pickup** — **already green**. Verified in the
   `ce4d3ce` commit message; `student_ledgers` went from 4062 to
   4063.

Items 4 and 5 are substantively done; items 1, 2, 3 are yours to
answer.

### Loose end — `docs/dux_voucher_integration.md` does NOT exist yet

Multiple commits and doc sections reference `docs/dux_voucher_integration.md`
(DocType field descriptions, `RGI_Migration_Rules.md §4.10`,
`docs/mapper_design_notes.md §7`, `scripts/seed_mapping_rules.py`
docstring). **The file does not exist in the repo.** Create it
**before** writing the Students-CSV generator (Work Item 7 generator
#4) — the Week-3 kickoff explicitly ordered this. Contents per
earlier Aditya spec:

- 4-column CSV schema (`student_name`, `tally_id`, `debit_amount`, `credit_amount`)
- One row per Ledger where `is_student_ledger=True`
- Consumed by dux_voucher's Ex Student Opening Batch DocType
- Shared `Temporary Opening - {ABBR}` account between rgi_migration's
  main JE and dux_voucher's Ex Student JE
- dux_voucher lives on bench at `apps/dux_voucher`, branch
  `feature/ex-student-module`

---

## 4. Work Item 7 — scope, discipline, checkpoint structure

### Scope: four generators

1. **Main Opening Journal Entry (Draft)** — Frappe `Journal Entry`
   DocType, `voucher_type=Opening Entry`, `is_opening=Yes`,
   `docstatus=0`. Reference `OB-CACSPU-2026-01` per RGI rules §6.2.
   Account lines from approved Mapping Decisions, Temp Opening
   balancer (`Temporary Opening - CACSPU`) per §3.7. Validate Dr = Cr
   within ₹1 before creating Draft. **NEVER auto-submit**.
2. **OIT CSV** for net-Cr vendors per §5.1/§5.2. Refuses to generate
   if any Supplier Creation Request still has `status=Pending`.
3. **Party-wise Dr JE** for net-Dr vendors per §5.3 — `is_advance=Yes`.
   Separate JE reference `OB-CACSPU-2026-02`, Draft.
4. **Students CSV** — 4 columns for dux_voucher handoff. Uses
   `tb.student_ledgers` (the parser's output). Attached to the Session.

All four artefacts attach to the `Tally Migration Session` DocType
(fields `generated_je_draft`, `generated_oit_file`,
`student_ledger_file`, etc. — already scaffolded in Work Item 4).

### Discipline contract

**9 review checkpoints across Work Item 7.** Aditya's explicit ask
after the Work Item 6 discipline slip:

- Prose design of generator #1 → **pause**, wait for sign-off
- Implementation of generator #1 → **pause**
- Same for generators #2, #3, #4 (separately, each with prose +
  implementation pause)
- Integration into session workflow → prose + implementation pauses
- Full-file run with sample JE Draft for eyeball verification → final
  review

That's 8-9 pauses. Slow and reviewed is correct. **If you propose
a multi-step sequence and say "starting now" without each step's
signoff, Aditya will flag it.** Don't chain.

### Why Work Item 7 is paused right now

Three hard blockers:
1. Pre-flight items 1-3 unanswered (Company target, FY, backup).
2. `docs/dux_voucher_integration.md` doesn't exist yet (should be
   the first sub-task of Work Item 7, before generator #4).
3. Generator #1 needs a target ERPNext Company to link JE lines
   against — depends on pre-flight item #1.

---

## 5. Infrastructure facts

### Remote dev bench

- **Host**: `frappe@187.127.132.58` (hostname `srv1505157`)
- **Bench**: `/home/frappe/frappe-bench/`
- **Site**: `erp.jewonline.in`
- **Frappe**: 16.12.0
- **ERPNext**: 16.10.0
- **Python**: 3.14.3 (in the bench's venv at `~/frappe-bench/env/bin/python`)
- **App**: `rgi_migration` at `~/frappe-bench/apps/rgi_migration`
  (installed, on branch `claude/unruffled-hellman-c54c95`)
- **SSH**: key-based; works from the local Windows machine via
  `ssh frappe@187.127.132.58` without prompting. Config file at
  `C:/Users/adity/.ssh/config` has the host entry.

### Other apps on the shared bench (14 total)

`frappe`, `erpnext`, `frappe_er_generator`, `raven`,
`hsc_master_inhouse`, `dux_voucher` (branch
`feature/ex-student-module` — ex-student workflow lives here),
`purchase_register`, `vehicle_inhouse`, `bank_statement_importer`,
`india_compliance` (16.2.1), `dux_portal`, plus `rgi_migration`.

### CACSPU Tally XML (the 221 MB reference fixture)

- **Local**: `C:/Users/adity/tally-exports/ghrcacs_masters.xml` (old
  filename, pre-dates the CACSPU rename; 221 MB; NEVER committed).
- **Server**: **NOT PRESENT**. No `~/tally-exports/` dir on the
  server. Work Item 7's full-file integration tests either (a) run
  locally against the local copy, or (b) require Aditya to scp the
  XML to the server.
- **Sample**: `rgi_migration/tests/fixtures/sample_cacspu_masters_sample.xml`
  is a 10 MB committed subset (500 first-in-document-order ledgers +
  13 diagnostic must-include ledgers). Enough for regression tests
  and mechanism validation; NOT a full-file hit-rate measurement.

### Deployment pattern

**Code changes flow**: local worktree → `git push origin` → server
`git pull --ff-only` → `bench migrate` if schema changed.

**DocType data edits (single row updates)**: via `bench --site X
console` heredoc on the server. Code-level DocType edits need to
round-trip through git (edit setup module or patch script locally,
push, server pull, run the setup function).

**Bridging generated DocType JSONs** (e.g. after `bench migrate`
regenerates JSON files on the server): commit on server, then fetch
from local via SSH and push to origin. I've used the local-as-bridge
pattern several times (see commits `a368b60`, `6567a74` for
examples). The server's git has no GitHub auth, so direct push from
server fails.

### Local environment

- **Main repo**: `C:/Users/adity/Desktop/claude code/rgi-migration-app/`
  (currently on branch `main` at `02ed6f7`)
- **Worktree**: `.claude/worktrees/unruffled-hellman-c54c95/` (all
  active development)
- **Python venv**: `C:/Users/adity/Desktop/claude code/rgi-migration-app/.venv/`
  — has pytest, rapidfuzz, lxml, openpyxl. Activated via
  `.venv/Scripts/python`.

---

## 6. Known gotchas

### Branch / migrate trap (dux_voucher's problem, not ours directly)

The dux_voucher CLAUDE.md notes: *"Single shared DB across branches.
`bench migrate` deletes any DocType / Page / Report / Workspace whose
JSON isn't on the current branch's disk."* This applies **within an
app**, not across apps. `rgi_migration` is a sibling app with its
own `/apps/rgi_migration/` directory; our DocType JSONs are
independent of dux_voucher's branch state.

Mitigation for us: keep `rgi_migration` on a **single integration
branch** (`claude/unruffled-hellman-c54c95`). Don't split into
feature branches that might get migrated separately and wipe each
other's DocType metadata.

### v16 schema-mutation recipes — see docs/mapper_design_notes.md §5

Summary of what works and what doesn't:

| Task | Works | Doesn't |
|---|---|---|
| Rename DocField | Edit `DocType.fields` list + `save()` | `frappe.model.rename_doc.rename_field` (doesn't exist in v16) |
| Drop orphan SQL column | `frappe.db.commit(); frappe.db.sql("ALTER TABLE …"); frappe.db.commit()` | Raw DDL inside transaction (`ImplicitCommitError`) |
| Add new field | Edit `DocType.fields` list + `save()` | (same) |
| Run ad-hoc schema code | `bench --site X console <<EOF … EOF` | `bench --site X execute dotted.path` (flaky fallback) |

### Git auth on the server

Server has no GitHub PAT or outbound SSH key. HTTPS push from server
fails. Use the local-as-bridge pattern — commit on server, fetch
from local worktree via SSH, push from local to GitHub.

### `bench execute` NameError fallback

`bench --site X execute <dotted.path.to.func>` has a fallback path
(`frappe/commands/utils.py:288`) that does `eval(code)` if the
primary `frappe.get_attr` import fails — producing a spurious
`NameError: name '<app>' is not defined`. Hit twice in this session.
Workaround: `bench --site X console` with a heredoc. The console
path reliably imports modules fresh (after clearing `__pycache__`).

### Shell escaping in heredocs

When running Python through `ssh user@host 'bench ... console <<EOF
...'`, f-strings with dict access (`f"…{d['key']}…"`) get mangled by
the shell's quote-handling. Workaround: use `+ str(x)` concatenation
or put the workflow code in a committed module that the heredoc just
imports and calls. See `rgi_migration/rgi_migration/setup/seed_workflow.py`
for the pattern.

### Mapping Rule field name drift

Aditya's Batch 2 put a Section Break at fieldname `source_section`
and the data field at `source_section_ref`. Work Item 4 audit
(commit `9182539`) renamed to `source_section` + `sb_source`. Any
snippet or tool that filters by `source_section_ref` is stale —
use `source_section`. The seed script (`scripts/seed_mapping_rules.py`)
has a comment at the top about this.

### `~/tally-exports/ghrcacs_masters.xml` filename is stale

The full 221 MB Tally export is locally named `ghrcacs_masters.xml`,
but the company is CACSPU (per the Week 2 rename that swept the
repo). Aditya was supposed to rename the local file to
`cacspu_masters.xml` but hasn't. Code paths referencing the file
use whatever filename is on disk — `~/tally-exports/ghrcacs_masters.xml`
still works locally. When writing CLI examples or test-path
references, use the actual filename on disk, not the logically-correct
one.

---

## 7. Don't do

- **Don't re-seed §4.10.** Deleted deliberately. Student routing is
  parser-level via `AGGREGATE_STUDENT_ACCOUNT_NAMES`.
- **Don't add Mapping Rules for student-related names.** Extend
  `AGGREGATE_STUDENT_ACCOUNT_NAMES` (code edit) instead.
- **Don't use `source_section_ref` as a field filter.** Use
  `source_section`. See audit commit `9182539`.
- **Don't tighten the fuzzy threshold** based on dev-bench results.
  85% stays; use negative alias rules (when Week-4 reviewer workflow
  lands) for production false positives.
- **Don't chain steps in Work Item 7.** One prose design per
  generator, pause for review, one implementation, pause. Nine
  checkpoints total. Aditya will flag any "starting now" multi-step
  sequences.
- **Don't push to the local `main` branch on origin.** Main is
  local-only by design (Week-1 snapshot). All work goes on
  `claude/unruffled-hellman-c54c95`.
- **Don't use `bench execute` for complex module calls.** Use
  `bench --site X console <<EOF … EOF` instead.
- **Don't force-push.** Ever. If a push fails, rebase or ask.
- **Don't assume Frappe ORM is available outside bench.** Always
  wrap in `bench --site X console`, `bench --site X execute` (for
  trivial calls), or `~/frappe-bench/env/bin/python -c "..."`
  (which usually fails without the site context).
- **Don't re-invent decisions documented in
  `docs/mapper_design_notes.md`.** Anti-pattern seeding test (§1),
  structural checks (§2), account creation policy (§3), known data
  quirks (§4), schema recipes (§5), supplier matching including
  rule-first ordering and fuzzy false-positive expectations (§6),
  leaf-only posting principle with cross-app boundary (§7). Read
  before proposing architectural changes.

---

## 8. Quick-reference file map

| File | Purpose |
|---|---|
| `CLAUDE.md` | Project-wide architecture + tech stack. Stale on some Week-1 details (GHRCE/ASSHST references); CACSPU is the real reference entity. |
| `RGI_Migration_Rules.md` | Rule-library source of truth. §4.10 is Deprecated (see §2a above). |
| `docs/mapper_design_notes.md` | Design principles §1-§7. **Read this before proposing architecture changes.** |
| `docs/tally_sign_convention.md` | Why negative = Dr, why "closing as opening" matters. |
| `docs/WEEK1_LEARNINGS.md` | Non-obvious things the parser taught us. Still relevant. |
| `docs/week2_baseline_mapping.md` | First-real-signal mapper baseline on CACSPU full file. |
| `docs/slim_tally_export.md` | Upload architecture decision (three-lane, no custom uploader). Work Item 8 builds the TDL. |
| `docs/seed_plan.json` | Machine-readable current rule set. 22 rows. Regenerated via `python scripts/seed_mapping_rules.py --dry-run`. |
| `docs/dux_voucher_integration.md` | **DOES NOT EXIST YET.** Create as first sub-task of Work Item 7 before generator #4 (Students CSV). |
| `scripts/seed_mapping_rules.py` | Rule library source + seed workflow. |
| `rgi_migration/parsers/` | Week 1 parsers (XML, Excel). Work Item 6 extended with `AGGREGATE_STUDENT_ACCOUNT_NAMES`. |
| `rgi_migration/mapper/` | Tier-1 mapper (Week 2) + supplier resolution (Week 6) + control-account patterns. |
| `rgi_migration/rgi_migration/` | Frappe app package (Work Item 3-4 scaffold). |
| `rgi_migration/rgi_migration/setup/` | `create_doctypes.py` (schema), `seed_workflow.py` (seed-insert orchestration). |
| `rgi_migration/tests/` | pytest suite, 86 tests. |

---

## 9. Resuming — suggested first 10 minutes

1. Read this doc (you just did).
2. `git log --oneline -10` on the worktree — confirm HEAD is `ce4d3ce`.
3. Run `python -m pytest rgi_migration/tests/` — should see 86 passed, 1 skipped.
4. Skim `docs/mapper_design_notes.md` §7 — the most recent architectural
   decision and the one most likely to come up.
5. Ask Aditya for the three pre-flight answers (Company Abbreviation,
   Fiscal Year, backup). Don't move on Work Item 7 without them.
6. When ready: prose-design generator #1 (main Opening JE), pause for
   signoff, implement, pause, repeat.

Good luck. Don't chain steps.

— Handoff written 2026-04-20, state as of commit `ce4d3ce`.
