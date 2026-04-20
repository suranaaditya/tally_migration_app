"""Source-level smoke test of the TallySlim GUI.

Drives the tkinter app programmatically by setting the input/output
tk.StringVar bindings directly (as though a user had Browsed to a file),
monkeypatches ``messagebox.showinfo`` / ``showerror`` to capture dialog
calls instead of blocking, and clicks the Convert button via a timed
``root.after`` callback after the real mainloop starts.  When the
dialog fires, the monkeypatched callback schedules ``root.quit`` so
the mainloop exits cleanly.

Covers three of the spec's smoke scenarios without requiring a human
click session:
  T1 — small committed sample through full GUI flow
  T2 — full 221 MB CACSPU through full GUI flow
  T3 — non-XML input (triggers error dialog)

NOTE: does NOT exercise PyInstaller-bundled code paths — that requires
running TallySlim.exe, which Windows Defender quarantined in this
session.  .exe-level smoke test requires admin Defender exclusion on
the build machine.
"""
from __future__ import annotations

import os
import sys
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from tools.tally_slim import gui as gui_mod   # noqa: E402

_REPO_ROOT = Path(__file__).parent.parent
SMALL = _REPO_ROOT / "rgi_migration" / "tests" / "fixtures" / "sample_cacspu_masters_sample.xml"
FULL = Path(r"C:\Users\adity\tally-exports\ghrcacs_masters.xml")

_WATCHDOG_MS = 180_000   # 3 minutes hard cap per scenario


def drive_gui(input_path: Path, output_path: Path, scenario: str) -> dict:
    """Run one GUI conversion pass, return captured dialog + progress state."""
    captured: dict = {
        "scenario": scenario,
        "info": None,
        "error": None,
        "status_sequence": [],
        "progress_sequence": [],
    }

    root = tk.Tk()
    root.withdraw()   # don't pop a real window during automated test

    # Monkeypatch dialogs — record, then quit mainloop so the scenario ends.
    # Delay the quit a tick so any trailing UI updates from the worker's
    # final _post() calls drain first.
    def _info(title, message):
        captured["info"] = (title, message)
        root.after(100, root.quit)
    def _error(title, message):
        captured["error"] = (title, message)
        root.after(100, root.quit)
    messagebox.showinfo = _info
    messagebox.showerror = _error

    app = gui_mod.TallySlimApp(root)
    app.input_var.set(str(input_path))
    app.output_var.set(str(output_path))
    root.update_idletasks()

    # Periodic sampler of status + progress (runs on main loop).
    # Try/except guards against residual callbacks firing after the root
    # is destroyed at end-of-scenario — otherwise tk logs noisy
    # "invalid command name" warnings into stderr.
    def _sample():
        try:
            captured["status_sequence"].append(app.status_var.get())
            captured["progress_sequence"].append(app.progress_var.get())
            root.after(250, _sample)
        except tk.TclError:
            pass
    root.after(250, _sample)

    # Fire the Convert click shortly after mainloop starts.
    root.after(50, app._on_convert)

    # Watchdog — quit no matter what after _WATCHDOG_MS.
    root.after(_WATCHDOG_MS, root.quit)

    import time
    started = time.monotonic()
    root.mainloop()
    captured["elapsed"] = time.monotonic() - started

    captured["final_status"] = app.status_var.get()
    captured["final_progress"] = app.progress_var.get()
    captured["progress_max"] = app.progress.cget("maximum")

    root.destroy()
    return captured


def main() -> int:
    bad = 0

    # T1 — small sample, expect success
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "slim.xml"
        cap = drive_gui(SMALL, out, "T1 small sample")
        print(f"\n=== T1 small sample ===")
        print(f"  elapsed: {cap['elapsed']:.2f}s")
        print(f"  progress max: {cap['progress_max']}")
        print(f"  final progress: {cap['final_progress']}")
        print(f"  final status: {cap['final_status']!r}")
        print(f"  status samples: {cap['status_sequence'][:8]}")
        print(f"  progress samples: {[f'{p:.0f}' for p in cap['progress_sequence'][:8]]}")
        print(f"  info dialog: {cap['info'][0] if cap['info'] else None}")
        if cap['info']:
            print("  info body:")
            for line in cap['info'][1].splitlines():
                print(f"    {line}")
        print(f"  error dialog: {cap['error']}")
        print(f"  output exists: {out.exists()}, size: {out.stat().st_size if out.exists() else 0}")
        log = Path(str(out) + ".log")
        print(f"  log exists: {log.exists()}, size: {log.stat().st_size if log.exists() else 0}")
        if cap["error"]:
            print("  FAIL: unexpected error dialog")
            bad += 1
        if not cap["info"]:
            print("  FAIL: no completion dialog")
            bad += 1
        if not out.exists():
            print("  FAIL: output file not written")
            bad += 1

    # T2 — full CACSPU, expect success + real progress
    if FULL.exists():
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "slim.xml"
            cap = drive_gui(FULL, out, "T2 full CACSPU")
            print(f"\n=== T2 full CACSPU ===")
            print(f"  elapsed: {cap['elapsed']:.2f}s")
            print(f"  progress max: {cap['progress_max']}")
            print(f"  final progress: {cap['final_progress']}")
            print(f"  final status: {cap['final_status']!r}")
            # Sample the sequence — skip first empty samples
            nonempty_statuses = [s for s in cap['status_sequence'] if s][::5]
            print(f"  status samples (every 5th non-empty): {nonempty_statuses[:10]}")
            nonzero_progress = [p for p in cap['progress_sequence'] if p > 0][::5]
            print(f"  progress samples (every 5th nonzero): {[f'{p:.0f}' for p in nonzero_progress[:10]]}")
            print(f"  info dialog title: {cap['info'][0] if cap['info'] else None}")
            if cap['info']:
                print("  info body:")
                for line in cap['info'][1].splitlines():
                    print(f"    {line}")
            print(f"  error dialog: {cap['error']}")
            if out.exists():
                size_mb = out.stat().st_size / 1024 / 1024
                print(f"  output size: {size_mb:.2f} MB")
            if cap["error"]:
                print("  FAIL: unexpected error dialog")
                bad += 1
            if not cap["info"]:
                print("  FAIL: no completion dialog")
                bad += 1
    else:
        print(f"\n=== T2 full CACSPU — SKIPPED (not at {FULL}) ===")

    # T3 — non-XML input, expect error dialog
    with tempfile.TemporaryDirectory() as td:
        bad_input = Path(td) / "not_xml.txt"
        bad_input.write_text("this is not Tally XML", encoding="utf-8")
        out = Path(td) / "slim.xml"
        cap = drive_gui(bad_input, out, "T3 error path")
        print(f"\n=== T3 error path (non-XML input) ===")
        print(f"  elapsed: {cap['elapsed']:.2f}s")
        print(f"  final status: {cap['final_status']!r}")
        print(f"  info dialog: {cap['info']}")
        print(f"  error dialog title: {cap['error'][0] if cap['error'] else None}")
        if cap['error']:
            print(f"  error message (first line): {cap['error'][1].splitlines()[0]}")
        log = Path(str(out) + ".log")
        print(f"  log exists: {log.exists()}, size: {log.stat().st_size if log.exists() else 0}")
        if not cap["error"]:
            print("  FAIL: no error dialog shown for non-XML input")
            bad += 1
        if cap["info"]:
            print("  FAIL: success dialog shown for non-XML input")
            bad += 1

    print(f"\n{'=' * 40}")
    print(f"{'PASS' if bad == 0 else f'FAIL ({bad} issues)'}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
