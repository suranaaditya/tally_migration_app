"""Fail if ANY file in tests/fixtures/ exceeds 50 MB.

By repo policy, large Tally exports are stored **outside** the repo
(typically ``~/tally-exports/``) and referenced via the
``RGI_FULL_XML_PATH`` environment variable.  This script enforces that:
no file inside ``rgi_migration/tests/fixtures/`` may be over 50 MB,
period.  There is no gitignore-based exception -- if a big file sits in
the fixtures directory at all, even temporarily, accidental commits
become possible and the policy is violated.

Run before any commit that touches fixtures, and wire into CI.

Usage:
    python scripts/check_no_big_fixtures.py

Exit 0 if clean, 1 if any offender found.
"""
from __future__ import annotations

import sys
from pathlib import Path

LIMIT_MB = 50
FIXTURES = Path("rgi_migration/tests/fixtures")


def main() -> int:
    if not FIXTURES.exists():
        print(f"(No fixtures dir at {FIXTURES}; nothing to check.)")
        return 0

    offenders: list[tuple[Path, float]] = []
    for f in FIXTURES.rglob("*"):
        if not f.is_file():
            continue
        size_mb = f.stat().st_size / (1024 * 1024)
        if size_mb > LIMIT_MB:
            offenders.append((f, size_mb))

    if offenders:
        print(f"ERROR: fixtures exceeding {LIMIT_MB} MB found:")
        for f, mb in offenders:
            print(f"  {f}: {mb:.1f} MB")
        print()
        print("This file must not live inside the repo.  Move it outside")
        print("(e.g., ~/tally-exports/) and reference it via the")
        print("RGI_FULL_XML_PATH env var.")
        print()
        print("See rgi_migration/tests/fixtures/README.md for the full")
        print("convention and the regenerate-the-full-export procedure.")
        return 1

    print(f"OK: no fixtures over {LIMIT_MB} MB.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
