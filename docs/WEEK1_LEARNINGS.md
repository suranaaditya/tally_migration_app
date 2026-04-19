# Week 1 — Lessons the Parser Taught Us

Brief log of the non-obvious things we learned building the Tally → ERPNext
opening-balance parser. Each item survived a debugging cycle worth
remembering next time a Tally export arrives.

**Sign convention is the opposite of bookkeeping intuition.** Tally's
All Masters XML encodes a ledger's opening balance as a signed decimal
where **negative = Dr, positive = Cr** — independent of account type.
An asset with a Dr book value of ₹1 M is stored as `-1000000.00`. The
group's `ISDEEMEDPOSITIVE` flag is a red herring for this decision; it's
UI/reporting metadata that does not affect how balances are stored. We
spent a real session following the `deemed_positive` lead before
recognising that it inverts for Fixed Assets sub-groups like
`Immovable Properties` (Tally sets the flag to `No` even though the
group holds Dr-nature ledgers). The storage-sign rule is uniform; the
metadata is local. Full derivation and citation to Tally's TDL docs
in `docs/tally_sign_convention.md §1`.

**"Export closing balances as opening balance" is a per-export switch
we MUST enable for every entity.** Tally keeps `$OpeningBalance` and
`$ClosingBalance` as two distinct fields internally. By default, the
XML export emits `$OpeningBalance` — which is the start-of-year value,
not the end-of-year close we want for the next fiscal year's ERPNext
opening. Setting the flag at export time makes `<OPENINGBALANCE>` carry
the closing snapshot. We caught this on CACSPU purely because we had
a reference TB Excel with explicit Opening and Closing columns and
could write a diagnostic that voted closing with 108:0 margin on the
discriminating ledgers. Without that Excel we would have shipped a
year-stale migration. Export procedure documented verbatim in
`docs/tally_sign_convention.md §3`; defensive 1%-imbalance warning in
the parser catches future wrong-setting exports.

**Name collisions on distinct Tally IDs are legitimate — always key
by (name, tally_id).** CACSPU contains two real ledgers both named
`Sachin  Gawande` — `-52926` under `Personal Advance` (a staff cash
advance) and `-30` under `STUDENTS` (a student ledger). The cleaned
display name is identical; only the numeric tally_id disambiguates.
An early version of the regression test used `name` alone for the
disjoint-partition check and failed with a false positive. Every
cross-ledger identity comparison uses the `(name, tally_id)` tuple now.
This also matters for the Excel parser's `known_ledger_parents` dict
keyed on the full raw name (including the `-{ID}` suffix). See
`docs/tally_sign_convention.md §5`.

**Raw Tally data carries historical residuals; that's a data signal,
not a parser bug.** CACSPU's trial balance is internally imbalanced
by ₹342,358 (0.2% of grand total). The signed sum of every
`<OPENINGBALANCE>` in the file is `-342,358.48`, not zero. We verified
this is baked into the source Tally company — long-running books
accumulate tiny residuals from hand-corrected vouchers, rounding, and
similar — and is faithfully preserved through the
"closing as opening" mechanism. The Week-3 JE builder will absorb this
residual into the `Temporary Opening - {ABBR}` balancer entry per
`RGI_Migration_Rules.md §3.7`, so it becomes a reviewable line item,
not lost data. A large Temp Opening value for any entity is a
diagnostic — it tells us the source data has historical inconsistencies
worth investigating independently of the migration itself. See
`docs/tally_sign_convention.md §6.1`.

**Large fixtures live outside the repo.** The full 221 MB CACSPU
All Masters export is 4× over GitHub's soft file limit and makes
accidental commits trivially easy. We moved it to `~/tally-exports/`
(convention — any path outside the repo works) and reference it via
the `RGI_FULL_XML_PATH` environment variable. A 10 MB curated sample
(`sample_cacspu_masters_sample.xml` — first 500 ledgers + 13
diagnostic ledgers via allow-list + all groups) lives in
`rgi_migration/tests/fixtures/` and is sufficient to catch every
regression we know to test for. Full details and the regenerate
procedure in `rgi_migration/tests/fixtures/README.md`;
`scripts/check_no_big_fixtures.py` enforces a 50 MB cap as a CI gate.
