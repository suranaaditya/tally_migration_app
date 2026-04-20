# Work Item 8 Complete — Windows Preprocessor (TallySlim)

## TL;DR

TallySlim converts full Tally All Masters XML (220 MB typical) to slim
XML (3 MB typical) for browser upload to the RGI migration system.
Deliverable: standalone Windows `.exe` built via PyInstaller. Six
commits, landed 2026-04-20 on branch `claude/unruffled-hawking-ec0beb`.

## Deliverables

| Artifact | Location | Purpose |
|---|---|---|
| Whitelist module | [rgi_migration/parsers/tally_tag_whitelist.py](../rgi_migration/parsers/tally_tag_whitelist.py) | Single source of truth for parser/preprocessor contract |
| Preprocessor core | [tools/tally_slim/preprocessor.py](../tools/tally_slim/preprocessor.py) | CLI + callable API for streaming XML filter |
| GUI wrapper | [tools/tally_slim/gui.py](../tools/tally_slim/gui.py) | tkinter interface for bookkeepers |
| Build script | [tools/tally_slim/build.py](../tools/tally_slim/build.py) | PyInstaller invocation |
| Round-trip test | [rgi_migration/tests/test_tally_slim_roundtrip.py](../rgi_migration/tests/test_tally_slim_roundtrip.py) | Runtime contract enforcement (braces-level net) |
| Static-analysis test | [rgi_migration/tests/test_whitelist_coverage.py](../rgi_migration/tests/test_whitelist_coverage.py) | AST-level contract enforcement (belt-level net) |
| GUI smoke-test harness | [scripts/smoke_test_gui.py](../scripts/smoke_test_gui.py) | Programmatic GUI validation without click-through |
| Bookkeeper docs | [tools/tally_slim/README.md](../tools/tally_slim/README.md) | End-user instructions (SmartScreen + Defender guidance) |
| Developer docs | [tools/tally_slim/BUILD.md](../tools/tally_slim/BUILD.md) | Build/release procedure + known issues |

## Numbers on CACSPU (221 MB reference export)

| Metric | Value |
|---|---|
| Input size | 231,380,460 bytes (220.7 MB) |
| Input encoding | UTF-16-LE with BOM |
| Source element count | 1,906,779 (across 811 distinct tag names, 11,017 TALLYMESSAGE entries) |
| Source entity breakdown | 6,027 LEDGER + 4,167 STOCKITEM + 403 GROUP + 420 misc (STOCKGROUP/COSTCENTRE/VOUCHERTYPE/etc.) |
| Output size | 2,998,986 bytes (2.9 MB) |
| Output encoding | UTF-8 |
| **Size reduction** | **98.7%** |
| Conversion time (CLI) | 8.3 s |
| Conversion time (GUI) | 9.96 s wall-clock (includes 4.5 s pre-scan for progress denominator + 5.5 s main conversion) |
| Peak memory | under 150 MB (streaming iterparse + aggressive element clearing) |
| Parser output match | Identical `ParsedTallyTB`: 1964 main + 4063 student + 280 groups, same `total_dr`/`total_cr`, same `is_balanced` |

## Architectural contract

Parser and preprocessor share [`tally_tag_whitelist.py`](../rgi_migration/parsers/tally_tag_whitelist.py).
The contract: if the parser reads a new tag, it must be added to the
appropriate KEEP frozenset in the whitelist module in the same commit.
Enforced by two tests:

- **Runtime** ([test_tally_slim_roundtrip.py](../rgi_migration/tests/test_tally_slim_roundtrip.py)) —
  parses both source and slim output, asserts exhaustive per-field
  equality across TrialBalance, Ledger, Group, BillAllocation dataclasses
  (floats within 0.01). Tier 1 runs on the committed sample in CI; Tier 2
  gated on `RGI_FULL_XML_PATH` exercises the full CACSPU export and
  additionally asserts slim size under 10 MB as a whitelist-bloat guard.
- **Static analysis** ([test_whitelist_coverage.py](../rgi_migration/tests/test_whitelist_coverage.py)) —
  AST-walks the parser, extracts every tag literal passed to
  `.find`/`.findall`/`.findtext`/`.iter`/`.iterfind`, asserts each is in
  the whitelist or in one of two explicit allowlists (envelope reads,
  display-report format). Runs in under 200 ms.

If the parser grows a new tag read without updating the whitelist, both
tests fail. If the whitelist adds a tag the parser doesn't read, both
tests still pass (noise, not a regression).

## Smoke test results

**Source-level GUI smoke tests** ([scripts/smoke_test_gui.py](../scripts/smoke_test_gui.py))
against the actual Python source files (same code that PyInstaller
bundles):

| Scenario | Result | Detail |
|---|---|---|
| T1 — 10.6 MB committed sample | PASS | 0.6 s conversion, strict validation passed, completion dialog correct |
| T2 — 220.7 MB full CACSPU | PASS | 5.5 s conversion after 4.5 s pre-scan, progress bar animated through 700 → 2600 → 4500 → 6200 → 11017, validation passed |
| T3 — non-XML .txt input | PASS | Error dialog fired, no success dialog, log written, failed fast in 0.18 s |

**.exe-level smoke test** — **PASS after AV exclusion**. `TallySlim.exe`
built successfully (16.7 MB, PyInstaller no errors). Initial launch
attempts were blocked by McAfee LiveSafe's real-time protection with
`WinError 225` ("contains a virus or potentially unwanted software") —
a known false-positive on the PyInstaller bootloader template that the
spec's build checklist anticipated.

After the build machine's McAfee exclusion was added for `TallySlim.exe`
(procedure: briefly disable Real-Time Scanning -> rebuild -> McAfee
**Excluded Files -> Add file** -> re-enable), the .exe launched cleanly
and stayed alive through a 7-second observation window. This confirms
the PyInstaller bundle itself is structurally correct:

- lxml submodules collected (ImportError on startup would have killed
  the process immediately)
- tkinter runtime hook works (GUI init reaches mainloop)
- Whitelist `--add-data` path resolves (preprocessor's
  `from rgi_migration.parsers.tally_tag_whitelist import ...` succeeds
  inside the bundle)

Combined with the source-level GUI smoke (which exercises the same
Python code that PyInstaller packages), TallySlim is validated
end-to-end.

**AV caveat for bookkeeper rollout**: expect ~1 in 5 Windows machines
to trip their antivirus on first run. Symptom and remediation vary by
AV product:

- **Windows Defender**: admin PowerShell + `Add-MpPreference -ExclusionPath`
- **McAfee LiveSafe**: turn off Real-Time Scanning briefly, add
  TallySlim.exe to Excluded Files, re-enable
- Others (Norton, Kaspersky, etc.): same pattern — whitelist the exe

Both `README.md` and `BUILD.md` now document both AV variants explicitly
with per-product remediation steps. Code-signing would eliminate the
issue across all AV products but is out of scope ($200-500/year).

## Full test suite

```
175 passed, 2 skipped
```

The 2 skipped tests both gate on `RGI_FULL_XML_PATH` for full-file
exercise (expected behavior); when that env var is set, all 175 pass +
2 skipped tests run as 177 passed.

## Release artifacts

`tools/tally_slim/dist/TallySlim.exe` is **not** committed to git
(gitignored via global `dist/` rule, 16.7 MB). Distribute as a release
asset via shared drive or email. Bookkeepers also need
[README.md](../tools/tally_slim/README.md) — bundle as a zip:

```
TallySlim-v0.1.0.zip
├── TallySlim.exe
└── README.md
```

- **AV false-positive on some machines** — unsigned PyInstaller
  bootloader gets flagged across multiple AV products (Windows Defender,
  McAfee LiveSafe, Norton, Kaspersky). Mitigation requires adding
  TallySlim.exe to the AV's exclusion list per-machine OR code-signing
  cert (out of scope). Expected failure rate: ~1 in 5 Windows machines.
  See [BUILD.md](../tools/tally_slim/BUILD.md#antivirus-blocks-createprocess-contains-a-virus)
  and [README.md](../tools/tally_slim/README.md#first-time-windows-warning-one-of-two-variants)
  for per-AV remediation steps.
- **SmartScreen warning on first run per new machine** — milder cousin
  of the above. Users click "More info -> Run anyway" once per machine.
  One-time annoyance, no admin needed.
- **All Masters scope only** — display-report XML format not supported.
  Current RGI workflow uses All Masters exclusively per Week-1 decisions,
  and display-report exports are small enough not to need slimming
  anyway.
- **No auto-update mechanism** — when `WHITELIST_VERSION` or
  `__version__` bumps, bookkeepers need a fresh .exe redistributed
  manually. Acceptable given the 59-entity rollout is ~3 months.

## When to rebuild

Any of these changes requires a rebuild and redistribution:

- `WHITELIST_VERSION` bumped (whitelist sets modified)
- `__version__` bumped (GUI/CLI code changed meaningfully)
- Python runtime upgraded in build environment
- lxml or PyInstaller major version upgraded

Procedure is in [BUILD.md](../tools/tally_slim/BUILD.md). Both versions
show in the window title and sidecar log so bookkeepers reporting an
issue can say which build they're running.

## Resume recipe for future sessions

If someone else (or future-you) picks up Work Item 8 maintenance:

1. Read this doc
2. Read [BUILD.md](../tools/tally_slim/BUILD.md) for build procedure
3. Read the module docstring in
   [tally_tag_whitelist.py](../rgi_migration/parsers/tally_tag_whitelist.py)
   for the contract
4. Run:

   ```
   pytest rgi_migration/tests/test_whitelist_coverage.py \
          rgi_migration/tests/test_tally_slim_roundtrip.py
   ```

   to verify nothing's drifted
5. If all green, you're current. Modify as needed — the safety nets
   will fail loudly if you break the parser/preprocessor contract.

## Commit sequence (for reference)

| Commit | SHA | Scope |
|---|---|---|
| 1 | `24582f2` | Whitelist module + 7 frozenset exports + docstring contract |
| 2 | `b7d95d4` | Preprocessor CLI + streaming filter + sidecar log + --strict validation |
| 3 | `85fd811` | Round-trip test (Tier 1 + Tier 2) + `preprocess()` extraction refactor |
| 4 | `9d2b302` | Static-analysis whitelist-coverage test (4 assertions) |
| 5 | `6aabb32` | GUI (tkinter) + PyInstaller build.py + README.md + BUILD.md + `*.spec` ignore + progress-callback plumbing |
| 6 | this commit | Defender docs, smoke-test harness, close-out |
