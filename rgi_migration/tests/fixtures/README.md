# Test fixtures

This directory holds the reference Tally exports used by the regression
test suite (`test_xml_vs_excel_reference.py`) and by the diagnostic
scripts in `scripts/`.

## Files

| File | Committed? | Size | Purpose |
|---|---|---|---|
| `sample_ghrcacs_masters_sample.xml`      | **yes** | ~10 MB | Default XML fixture — 587-ledger curated sample from the full export |
| `sample_ghrcacs_opening_tb.xlsx`         | **yes** | 29 KB  | Reference opening-TB Excel with explicit Dr/Cr columns; used by the Excel parser and the XML↔Excel cross-check |
| `sample_ghrcem.xml`, `sample_ghrcem.xlsx`| **yes** | ~200 KB total | GHRCEM files: preserved for Week 2-3 mapper testing, not referenced by the current test suite |

## Full Tally exports live **outside** the repo

The full 221 MB `ghrcacs_masters.xml` is **not** stored in this directory
and is **not** version-controlled. By convention it lives under
`~/tally-exports/` on each developer's machine:

```
~/tally-exports/ghrcacs_masters.xml   (221 MB)
```

The location is a convention, not a hard requirement — anywhere outside
the repo root works. The path is referenced via an environment variable
(`RGI_FULL_XML_PATH`), not hardcoded, so different developers can store
it wherever suits them.

**Why outside the repo?** The file is 4× over GitHub's 50 MB soft limit.
Keeping it outside the repo makes an accidental commit mechanically
impossible: there is no file to `git add`.

## How the sample was built

`sample_ghrcacs_masters_sample.xml` is produced by
`scripts/build_sample_masters_xml.py` from the full export. The sample
keeps:

- All `<GROUP>` elements (~403 — structurally small)
- All `<CURRENCY>` elements (envelope needs them)
- First 500 `<LEDGER>` elements in document order
- Plus 13 explicitly-named diagnostic ledgers (the `MUST_INCLUDE` set in
  the build script), whose presence is required by assertions in the
  regression test — without them, the sign-flip regression test would
  pass trivially

All STOCKITEM, UNIT, COSTCENTRE, VOUCHERTYPE, etc. are dropped.

## Regenerating the full export and running the full-file tests

1. Open Tally with the **GHRCACS** company loaded.
2. Follow the export procedure in **`docs/tally_sign_convention.md §3`**
   exactly. The non-obvious-but-critical setting is
   **"Export closing balances as opening balance": Yes** — without this
   the `<OPENINGBALANCE>` values become a year stale and the regression
   test's balance assertion fails.
3. Save the output to **any location outside this repo**. The convention
   is `~/tally-exports/ghrcacs_masters.xml` but anywhere works.
4. Set the env var pointing at the absolute path:
   ```
   export RGI_FULL_XML_PATH=~/tally-exports/ghrcacs_masters.xml
   ```
   (On Windows: `$env:RGI_FULL_XML_PATH = "$env:USERPROFILE\tally-exports\ghrcacs_masters.xml"`)
5. Verify nothing has been accidentally copied into `fixtures/`:
   ```
   python scripts/check_no_big_fixtures.py
   ```
6. Run the full-file regression suite:
   ```
   RGI_FULL_XML_PATH=~/tally-exports/ghrcacs_masters.xml \
       pytest rgi_migration/tests/
   ```
   All 9 assertions should pass.

If you want to regenerate the committed sample after you've re-exported:

```
# Temporarily copy the full file in so the build script finds it
cp ~/tally-exports/ghrcacs_masters.xml \
   rgi_migration/tests/fixtures/sample_ghrcacs_masters.xml
python scripts/build_sample_masters_xml.py
# Then move the full file BACK out before committing:
mv rgi_migration/tests/fixtures/sample_ghrcacs_masters.xml \
   ~/tally-exports/ghrcacs_masters.xml
git diff rgi_migration/tests/fixtures/sample_ghrcacs_masters_sample.xml
```

*(A future improvement: teach `build_sample_masters_xml.py` to read the
source path from `RGI_FULL_XML_PATH` directly so this shuffle isn't
needed.)*

## Before any commit touching fixtures

Run the size guard — it fails hard if any file in `fixtures/` exceeds
50 MB:

```
python scripts/check_no_big_fixtures.py
```

CI runs the same check. A red build on this step means a large file is
sitting in the fixtures directory and must be moved out.

## Why a sample + external full file, not just a sample?

The sample is enough to catch every regression we know to test for
(see the `MUST_INCLUDE` commentary at the top of
`test_xml_vs_excel_reference.py`). The full file is still occasionally
useful for end-to-end verification — specifically the
balance-to-within-1% assertion, which only makes sense on a complete
TB. Running `RGI_FULL_XML_PATH=... pytest` gives the strongest
confidence but needs the 221 MB export; normal development runs use
just the committed sample and complete in ~1 second.
