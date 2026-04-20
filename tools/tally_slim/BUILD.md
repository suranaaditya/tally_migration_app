# Building TallySlim

## Requirements

- Windows 10 or 11 (PyInstaller produces a native Windows executable
  regardless of the build host, but bookkeepers run on Windows so the
  test matrix is Windows-only)
- Python 3.11 or later (3.14 is what this repo is built against)
- `pip install pyinstaller` (tested with 6.19.0)
- `pip install lxml` (tested with 6.1.0)

## Build

From the repo root:

    python tools/tally_slim/build.py

Output: `tools/tally_slim/dist/TallySlim.exe` (~10-15 MB).

`build.py` sets up `--distpath`, `--workpath`, and `--specpath` all
under `tools/tally_slim/` so no build artefacts leak into the repo root.
The `.spec` file, `build/`, and `dist/` folders are all gitignored.

## Version bumps

Before releasing a new build:

1. Update `tools/tally_slim/__init__.py`'s `__version__`
2. Update `rgi_migration/parsers/tally_tag_whitelist.py`'s
   `WHITELIST_VERSION` **if** any of the KEEP frozensets changed
   (additive changes: bump minor; removals: bump major; docstring
   fixes: bump patch)
3. Rebuild

Both versions show in the window title and the sidecar log, so a
bookkeeper reporting an issue can tell us exactly which build they
have.

## Known issues

### PyInstaller misses `lxml.etree` submodules

If running the exe raises `ImportError: No module named lxml.etree`,
ensure `--collect-submodules lxml` is in the build command. Already
handled in `build.py`. If it ever breaks again, try `--collect-all
lxml` as a heavier fallback.

### First launch slow

PyInstaller `--onefile` extracts bundled libraries to a temp directory
on each launch. First launch takes 2-3 seconds. Subsequent launches in
the same user session are faster because the temp directory persists.

### SmartScreen warning on new machines

Unsigned executables trigger Windows Defender SmartScreen when first
run on a machine that hasn't seen the binary before. Users click
**More info** -> **Run anyway** once per machine. A Microsoft
code-signing certificate would eliminate this (~$200-500/year, out of
scope for Work Item 8).

### Antivirus blocks CreateProcess ("contains a virus...")

More severe than SmartScreen: on some machines, the installed
antivirus's real-time protection actively blocks `CreateProcess` on
the built exe with error `WinError 225` / "Operation did not complete
successfully because the file contains a virus or potentially unwanted
software."

This is a false positive on the PyInstaller `runw.exe` bootloader
template, not a real malware detection. Most major AV products trip
on unsigned PyInstaller exes periodically — their heuristics rotate.
Confirmed on this project so far: **McAfee LiveSafe** (silent quarantine
on launch), **Windows Defender** (per the original spec). Norton,
Kaspersky, ESET are all known to exhibit the same pattern on related
PyInstaller-built tools.

Fix depends on which AV is installed. The pattern is always the same:
turn off real-time scanning briefly, build/restore the exe, add the
exe (or its parent folder) to the exclusion list, turn real-time
scanning back on.

**Windows Defender** (requires admin PowerShell):

    Add-MpPreference -ExclusionPath 'C:\path\to\rgi-migration-app\tools\tally_slim\dist'

If `Add-MpPreference` fails with `0x800106ba`, a third-party AV has
taken over real-time protection and the Defender cmdlet is disabled.
Check which AV is actually active.

**McAfee LiveSafe**:

1. Open McAfee -> **My Protection** -> **Real-Time Scanning**
2. Click **Turn off**, select a short duration (15 minutes is enough)
3. Rebuild the exe (or restore it from Quarantine if already quarantined)
4. McAfee -> **Excluded Files** -> **Add file** -> browse to
   `TallySlim.exe`
5. Turn Real-Time Scanning back on (or wait for the 15-min timer)
6. The exclusion persists after real-time protection resumes

**Alternative path if exe already quarantined**: open McAfee ->
**Security History** (or **Quarantined Items**), find the entry,
**Restore** and whitelist in one step.

Bookkeepers without local admin rights must escalate to their IT team.
Code-signing would eliminate this alongside SmartScreen across all AV
products — same mitigation, same out-of-scope cost. Until signing
exists, expect this issue to surface on ~1 in 5 Windows machines,
with the exact symptom varying by AV vendor.

### Smoke-testing the GUI without the exe

`scripts/smoke_test_gui.py` drives the tkinter app programmatically
against the source files (bypassing PyInstaller). Exercises the three
spec scenarios (small sample, full CACSPU, non-XML error). Run this
to validate GUI logic changes without rebuilding the exe:

    python scripts/smoke_test_gui.py

Does NOT validate PyInstaller bundle correctness (for that, run
`TallySlim.exe` directly).

### UPX compression disabled intentionally

UPX would cut the exe size by 30-40%, but UPX-packed binaries trigger
false-positive malware signatures on several Windows AV engines. The
bookkeeper support cost of "my AV quarantined TallySlim.exe" exceeds
the download-size savings.

## Testing a build

After `build.py` completes:

1. Close any running TallySlim instances
2. Navigate to `tools/tally_slim/dist/`
3. Double-click `TallySlim.exe`
4. Smoke-test with a small Tally XML
   (`rgi_migration/tests/fixtures/sample_cacspu_masters_sample.xml`
   works — copy it somewhere the exe can see)
5. Smoke-test with the full 220 MB CACSPU XML if available
6. Verify the `.log` file is written next to the output
7. Open the slim output file and eyeball for sanity (should have
   `<ENVELOPE>`, `<LEDGER>`, `<GROUP>` elements and no `<STOCKITEM>`)

If the smoke test fails, check the `.log` file for error details and
open an issue with the log attached.
