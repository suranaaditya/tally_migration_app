"""Build TallySlim.exe via PyInstaller.

Run from repo root:

    python tools/tally_slim/build.py

Output: ``tools/tally_slim/dist/TallySlim.exe``.

Tested on Windows 10/11 with Python 3.11+ and PyInstaller 6.x.  See
``BUILD.md`` for troubleshooting (SmartScreen handling, lxml submodule
collection, first-launch performance).

Skips UPX compression intentionally — UPX-packed binaries trigger
false-positive AV flags on several Windows engines, and the 3-5 MB
reduction isn't worth the support load.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_TOOL_DIR = Path(__file__).parent.resolve()
_REPO_ROOT = _TOOL_DIR.parent.parent


def build() -> None:
    # PyInstaller's --add-data separator is ';' on Windows, ':' elsewhere.
    sep = ";" if sys.platform == "win32" else ":"
    whitelist_src = _REPO_ROOT / "rgi_migration" / "parsers" / "tally_tag_whitelist.py"

    args = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--windowed",
        "--name", "TallySlim",
        "--distpath", str(_TOOL_DIR / "dist"),
        "--workpath", str(_TOOL_DIR / "build"),
        "--specpath", str(_TOOL_DIR),
        "--noconfirm",
        # lxml's dynamic loads aren't fully picked up by PyInstaller's
        # static analysis without an explicit submodule sweep.
        "--collect-submodules", "lxml",
        # Ship the whitelist module as data so the exe can import it even
        # though the rgi_migration package isn't bundled.  Path inside the
        # exe mirrors the source layout so the lazy import in
        # preprocessor.py (strict-mode parser re-import) continues to work
        # if rgi_migration ever ships into the bundle too.
        "--add-data", f"{whitelist_src}{sep}rgi_migration/parsers/",
        str(_TOOL_DIR / "gui.py"),
    ]

    icon = _TOOL_DIR / "icon.ico"
    if icon.exists():
        args.extend(["--icon", str(icon)])

    print("Building TallySlim.exe...")
    print(f"Command: {' '.join(args)}")
    print()
    result = subprocess.run(args, cwd=_REPO_ROOT)
    if result.returncode != 0:
        print(f"\nBuild failed with exit code {result.returncode}")
        sys.exit(result.returncode)

    exe = _TOOL_DIR / "dist" / "TallySlim.exe"
    if not exe.exists():
        print(f"\nBuild reported success but {exe} not found")
        sys.exit(1)

    size_mb = exe.stat().st_size / 1024 / 1024
    print(f"\nSuccess: {exe} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    build()
