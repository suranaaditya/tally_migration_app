"""Conditional Frappe import shim for generator modules.

Generators are designed to be **importable without a bench** so pure-
core helpers (``build_je_payload``, ``_aggregate_per_student``, etc.)
can be unit-tested locally via pytest without Frappe installed in
the venv. Applying ``@frappe.whitelist()`` naively at module scope
would require Frappe at module-load time and break that property.

This shim exposes a ``whitelist`` callable that:

* On the bench (Frappe installed): resolves to ``frappe.whitelist``,
  so ``@whitelist()`` behaves identically to ``@frappe.whitelist()``.
* Off the bench (Frappe absent): resolves to a no-op passthrough
  decorator. The decorated function remains callable. Attempting to
  invoke it will still fail at the body's lazy ``import frappe`` —
  correct behavior, because the function genuinely needs Frappe to
  do anything useful.
"""

from __future__ import annotations

try:
    import frappe  # type: ignore[import]

    whitelist = frappe.whitelist
except ImportError:  # pragma: no cover — only hit on local pytest
    def whitelist(*_args, **_kwargs):  # type: ignore[no-redef]
        """No-op passthrough when Frappe isn't importable."""
        def decorator(fn):
            return fn
        return decorator
