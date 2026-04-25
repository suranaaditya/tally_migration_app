# Item 8.5 Stage 3 Complete — Multi-pass partial generation

## TL;DR

Stage 3 of Item 8.5 ships multi-pass partial generation: reviewers
can defer decisions in Pass 1, mark Pass 1 Submitted with Deferred
rows present (Partial Submitted state), resolve them later, and run
Pass N (N ≥ 2) to emit only the newly-resolved decisions as a delta
JE / CSV — without re-emitting Pass 1 contributions or breaking
Pass 1's submitted artefacts. Architecturally bounded for arbitrary
N passes (Q-D Option B). Landed 2026-04-25 on branch
`claude/unruffled-hellman-c54c95`.

## Numbers

- **5 phases (A → B → C → D → E)** with full discipline
- **16 ambiguity-block questions** (Q-A through Q-P) resolved by Aditya before Phase B
- **4 new files** + **17 files modified** (Phase B + bug fixes + Phase D extension)
- **15-probe Phase C matrix** all PASS (after 3 inline bug fixes)
- **9-probe Phase D walkthrough** (B1-B9) all PASS (after 1 server-side affordance fix during walkthrough)
- **6 bugs caught + fixed in scope across Phases C+D**: 3 Stage 3 design issues, 1 Stage 1/2 latent bug exposed by Stage 3 paths, 1 missing client affordance against Q-J, 1 missing client affordance against Q-L
- **3 latent bugs parked** as Phase E follow-ups (zero-balancer guard, Approve & Next UX, lock icon polish)
- **Tests**: 712 (Stage 2 close) → 733 passed, 2 skipped (final Stage 3)
- **CACSPU `TMS-CACSPU--00495` untouched** throughout (verified Probe 15 + Probe 9 regression)

## Deliverables

| Artifact | Location | Purpose |
|---|---|---|
| Migration Pass child DocType | [rgi_migration/rgi_migration/doctype/migration_pass/](../rgi_migration/rgi_migration/doctype/migration_pass/) | Permanent per-pass audit row (15 fields per Q-A) |
| `generated_in_pass` MD field | [mapping_decision.json](../rgi_migration/rgi_migration/doctype/mapping_decision/mapping_decision.json) | Per-MD pass stamp; Q-B Option 1 |
| `Partial Submitted` status | [tally_migration_session.json](../rgi_migration/rgi_migration/doctype/tally_migration_session/tally_migration_session.json) | New non-terminal state per Q-D |
| Pass-tracking helpers | [rgi_migration/generators/pass_tracking.py](../rgi_migration/generators/pass_tracking.py) | Pure helpers: `determine_current_pass_number`, `build_reference_suffix`, `build_filename_suffix`, `is_fresh_pass_start`, `find_draft_pass_row` |
| Retroactive Pass 1 patch | [backfill_pass_1_for_submitted_sessions.py](../rgi_migration/patches/v1_0/backfill_pass_1_for_submitted_sessions.py) | One-time backfill per Q-C Option (a) |
| Pass-aware whitelists | [tally_migration_session.py](../rgi_migration/rgi_migration/doctype/tally_migration_session/tally_migration_session.py) | `generate_all` / `mark_submitted` / `reset_parse` extended |
| Pass-aware loader | [parse_and_map.py](../rgi_migration/session/parse_and_map.py) | `load_pass_pending_decisions_from_session(session, current_pass_number)` + `stamp_pass_on_decisions` |
| Pass-aware generators | [opening_je.py](../rgi_migration/generators/opening_je.py), [advance_je.py](../rgi_migration/generators/advance_je.py), [oit_csv.py](../rgi_migration/generators/oit_csv.py), [students_csv.py](../rgi_migration/generators/students_csv.py) | P{N} reference suffix, pass-pending loader, empty-payload guard |
| Decision lock helper | [md_review.py](../rgi_migration/rgi_migration/page/md_review/md_review.py) | `_decision_locked_pass` + `decision_lock_info` whitelist + hard refuse in `save_decision` (Q-L) |
| Generate Pass N button + intro | [tally_migration_session.js](../rgi_migration/rgi_migration/doctype/tally_migration_session/tally_migration_session.js) | Dynamic label, disabled-with-tooltip per Q-J, `renderPassHistoryIntro` per Q-K |
| Deferred preset + lock icon | [md_review.js](../rgi_migration/rgi_migration/page/md_review/md_review.js), [md_review.css](../rgi_migration/rgi_migration/page/md_review/md_review.css) | Q-I preset pill + count badge, Q-L lock decoration |
| Synthetic test helpers | [synthetic_sessions.py](../rgi_migration/session/synthetic_sessions.py) | `advance_session_to_submitted_pass_1`, `add_synthetic_resolved_deferred_decision` (Q-P) |
| Stage 3 tests | [test_stage3_multi_pass.py](../rgi_migration/tests/test_stage3_multi_pass.py) | 73 tests covering schema, helpers, generator wiring, patches, JS static checks, lock+disabled affordances |
| Empty-payload tests | [test_generator_empty_handling.py](../rgi_migration/tests/test_generator_empty_handling.py) | 9 tests covering both empty-payload paths |

## Architectural decisions captured

### Q-D — `Partial Submitted` status (non-terminal)

Status flow:
- Pass 1: `Reviewing → Generating → Generated → Submitted` (deferred=0) OR `Partial Submitted` (deferred>0)
- Pass N (from Partial Submitted): `Partial Submitted → Generating → Generated → Submitted | Partial Submitted` (same fork)
- Final terminal state: `Submitted` (only when deferred_count=0 at mark_submitted time)

`mark_submitted` validates **only the current pass's JEs** per Q-E,
not cumulative across passes. Each pass is independently submitted;
Pass 1 was already verified at its own mark_submitted.

### Q-H — Session-level artefact field semantic

`session.generated_je_draft` (and the parallel advance/OIT/students
fields) **always mirror the most recent pass's** Draft/Submitted
artefact. Pass 1's artefacts are preserved permanently on the
Migration Pass row; the session-level fields get overwritten when
Pass 2 generates.

Implementation:
- Fresh Pass 2+ entry: `is_fresh_pass_start` is True →
  `generate_all` clears session-level artefact fields before
  generators run, so each generator's idempotency check sees a
  clean slate.
- Intra-pass regen: clears the in-progress Migration Pass row's
  artefact link fields up-front so Frappe's session.save() doesn't
  trip on stale Link references during intermediate saves (the
  "Bug #2" caught in Phase C Probe 13).

### Q-L — Post-submit immutability

Decision is locked iff (a) `generated_in_pass IS NOT NULL` AND (b)
the corresponding Migration Pass row's `pass_status == 'Submitted'`.
Pass-N-Draft decisions are NOT locked — reviewer can still edit
them within the open pass window.

Server-side: `save_decision` calls `_decision_locked_pass` first
and `frappe.throw`s if locked. Client-side: master pane row
renders a 🔒 prefix on the tally_name cell + muted background +
hover tooltip "Locked — included in Pass {N}, submitted {date}.
Use ERPNext Amend for corrections." (`decision_lock_info`
whitelist supports the client lookup.)

### Q-M — Reset Parse refusal

Hard refuse if any Migration Pass row has `pass_status = Submitted`.
Server-side throw lists the offending pass numbers; UI hides the
button when the guard would fail. Cancel-and-restart is the
operational escape hatch when a session needs to be redone.

## Six bugs caught + fixed in scope

### Phase C — 3 design issues + 1 Stage 1/2 latent bug

1. **Empty-payload guard** ([opening_je.py](../rgi_migration/generators/opening_je.py), [advance_je.py](../rgi_migration/generators/advance_je.py)). Pre-existing latent crash for sessions where one side (account or supplier) had zero eligible MDs. Stage 3 Pass 2+ commonly drains one side, exposing the bug. Fix: `build_*_payload` returns `None` on empty; Frappe wrappers detect None, clear session fields, return `""` sentinel. See `mapper_design_notes.md §5 → Generator empty-payload guard`.

2. **Migration Pass stale-link validation** ([tally_migration_session.py:generate_all](../rgi_migration/rgi_migration/doctype/tally_migration_session/tally_migration_session.py)). Intra-pass regen: Main JE deleted prior Pass-N Draft JE and created a new one, but the Migration Pass row's `Link → Journal Entry` field still pointed to the deleted JE during intermediate `session.save()` calls. Frappe LinkValidationError. Fix: clear the in-progress Migration Pass row's artefact link fields up-front when intra-pass regen detected; `_upsert_migration_pass_row` repopulates at end.

3. **Regen-aware loader** ([parse_and_map.py:load_pass_pending_decisions_from_session](../rgi_migration/session/parse_and_map.py)). Strict `generated_in_pass IS NULL` filter excluded MDs already stamped with the current pass during intra-pass regen, producing empty payloads. Fix: extend filter to `(generated_in_pass IS NULL OR generated_in_pass = current_pass_number)`. Cross-pass isolation preserved (prior-pass MDs still excluded). Stamping remains idempotent.

### Phase D — 2 missing client affordances + 1 data-layer gap

4. **S7 — Generate Pass N disabled-with-tooltip** ([tally_migration_session.js](../rgi_migration/rgi_migration/doctype/tally_migration_session/tally_migration_session.js)). Server-side enforcement was in `generate_all` (Phase B); client-side disabled state per Q-J was missing. Fix: pre-flight resolvable-count fetch on form refresh; if 0 in Partial Submitted state, button gets `prop("disabled", true)` + `attr("title", ...)` + `opacity 0.5`.

5. **S3 — md-review lock icon** ([md_review.js](../rgi_migration/rgi_migration/page/md_review/md_review.js), [md_review.css](../rgi_migration/rgi_migration/page/md_review/md_review.css)). Server refuse was in `save_decision` (Phase B); client-side visual lock per Q-L was missing. Fix: `MasterPane.setPassStatusMap` lookup populated via `controller.refresh_pass_status_map`; `_render_row` prepends 🔒 glyph + sets `.locked` row class for stamped+Submitted MDs; CSS rules in md_review.css.

6. **B5 data-layer gap** ([query.py](../rgi_migration/rgi_migration/page/md_review/query.py)). Master pane's `DEFAULT_DECISION_FIELDS` did NOT include `generated_in_pass`, so `decision.generated_in_pass` was `undefined` in the JS render path → `stamped_pass = 0` → the lock-icon branch never fired. Static tests didn't catch this — they verified JS source strings, not the data-flow contract. Fix: add `generated_in_pass` to `DEFAULT_DECISION_FIELDS` + a static test enforcing membership.

## Cross-pass integrity validators (Probes 6, 8, 11)

The most critical Stage 3 invariants — verified across all 15
Phase C probes + 9 Phase D probes:

- **Probe 6** — MD count integrity across passes: `stamped_p1`
  unchanged after Pass 2 generation; no double-stamping; Rejected
  + Deferred MDs stay unstamped (correct Q-F semantic).
- **Probe 8** — artefact non-interference: Pass 1 JE/file docs
  unchanged after Pass 2 lifecycle; Pass 1 Migration Pass row
  fields stable.
- **Probe 11** — server-side lock enforcement: `save_decision`
  refuses on stamped MDs whose pass is Submitted; Pass-N-Draft
  stamped MDs correctly NOT locked.

## Latent bugs parked for Phase E follow-ups

Documented in [WEEK4_DEFERRED_ITEMS.md](WEEK4_DEFERRED_ITEMS.md):

- **Generator zero-balancer guard** — exactly-balanced contributions
  produce a 0/0 balancer row Frappe rejects. Same class as the
  empty-payload guard; missed because the existing fix only catches
  empty-list. Real CACSPU rarely hits (Tally rounding leaves a
  residual) but bites synthetic data with even Dr/Cr distributions.
  ~1 hour fix.
- **Approve & Next button semantic** — saves the dropdown's current
  value rather than hardcoding `review_action="Approved"`. Confusing
  UX on Deferred → Approved transitions. Existing Stage 1+2
  intentional behavior; UX gap surfaces only with Stage 3's Deferred
  transition pattern.
- **Lock icon Unicode polish** — uses 🔒 emoji glyph; consider
  FontAwesome `fa-lock` for visual consistency with rest of Frappe
  UI. ~30 min cosmetic.

## Discipline patterns codified in §5

Stage 3 contributed two new entries to `mapper_design_notes.md §5`:

1. **Generator empty-payload guard** (added during Phase C bug fix).
   Pattern: any generator constructing a balancer row must guard
   against empty input.
2. **Phase rhythm — Phase B clean is necessary but not sufficient**
   (added during Phase E closure). Pattern: stage closure requires
   all of Phase B + C + D to validate. Phase B reaching "all tests
   green, zero pause-and-surface triggers" is necessary but not
   sufficient — Phase C surfaces integration bugs in code paths
   unit tests don't reach; Phase D surfaces affordance gaps that
   server-side correctness alone doesn't catch.

## Operational notes

- **No new generators added** — Stage 3 extends the existing 4
  (Main JE, OIT CSV, Advance JE, Students CSV) with pass-awareness.
- **No new tier values** — pass-tracking is orthogonal to the
  mapper's tier classification.
- **No CACSPU rule changes** — Stage 3 is generator+session
  orchestration; mapper rules are unchanged.
- **dux_voucher integration unchanged** — Students CSV still
  bypasses Mapping Decisions; Stage 3 only changes its filename
  to add `_p{N}` suffix on Pass 2+.
- **Retroactive backfill patch ran 0/0/0 on the dev bench** — no
  pre-Stage-3 Submitted sessions existed. Patch is registered for
  forward-compatibility against backup-restore scenarios.

## What's next

Item 8.5 is now complete (Stages 1, 2, 3 all shipped). The
operational backlog beyond Item 9:

- **Item 9**: CACSPU production migration. The first real entity
  exercising the full pipeline end-to-end. Stage 3 unblocks
  multi-pass scenarios where some decisions need external
  resolution (supplier paperwork, accountant disputes).
- **Item 10**: Tier-3 Claude API (Week 5+).
- **Item 11**: Customer workflow (Sundry Debtors).
- **Item 12**: Slim TDL bookkeeper-facing export template.
- **Item 13**: Pre-rollout hygiene bundle (scaffolder reconcile,
  COA hygiene, deployment playbook).

3 latent bugs parked + `requires_combine` semantics deferred —
none blocking Item 9; revisit during real-data testing or 59-entity
rollout discipline review.
