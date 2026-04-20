"""RGI Tally slim preprocessor — Windows utility for bookkeepers.

Converts a full Tally All Masters XML export (150-300 MB typical, UTF-16)
into a slim XML (2-5 MB typical, UTF-8) containing only the fields
``rgi_migration``'s parser reads.  Bookkeepers run this on their Windows
machine before uploading to the ERPNext Desk UI.

Sub-modules:
  preprocessor — CLI + streaming filter (pure Python, no Frappe dep)
  gui          — tkinter wrapper (added in Work Item 8 commit 5)
  build        — PyInstaller build script (added in Work Item 8 commit 5)
"""
from __future__ import annotations

__version__ = "0.1.0"
