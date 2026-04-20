"""Static-analysis test — every tag the parser reads is covered.

AST-walks ``tally_xml_parser.py`` and extracts every string literal
passed to ``.find``, ``.findall``, ``.findtext``, ``.iter``, or
``.iterfind`` method calls.  Asserts each extracted tag is in either
(a) one of the ``*_CHILDREN_KEEP`` / ``TALLYMESSAGE_CHILDREN_KEEP``
frozensets in ``tally_tag_whitelist.py``, or (b) the
``ENVELOPE_READS`` allowlist defined below (for envelope-level reads
the preprocessor preserves via verbatim envelope copy, and for
display-report format tags which are out of scope for Work Item 8).

Belt-level safety net.  The braces-level net is
``test_tally_slim_roundtrip.py`` — same drift would also fail a
runtime round-trip, but that test requires a working XML fixture
while this one is pure AST walking and runs in under 100 ms.

If this test fails with "uncovered tags", classify each:

- **New All Masters tag the parser reads** — add to the appropriate
  KEEP set in ``tally_tag_whitelist.py``, bump WHITELIST_VERSION.
- **Envelope-level read** (SVCURRENTCOMPANY, REPORTNAME etc.) — add
  to ``ENVELOPE_READS`` in this file with a one-line comment.
- **Display-report format** (DSP* tags) — add to ``DISPLAY_REPORT_READS``
  below; Work Item 8 scope is All Masters only.

Do NOT blanket-allowlist by adding to ENVELOPE_READS without thought —
the whole point of the test is that unclassified reads surface here.
"""
from __future__ import annotations

import ast
from pathlib import Path

from rgi_migration.parsers import tally_tag_whitelist as wl

_PARSER_PATH = (
    Path(__file__).parent.parent / "parsers" / "tally_xml_parser.py"
)

# Tag-accepting ElementTree / lxml methods.  ``.get`` is intentionally
# excluded — it reads attributes (covered by ``*_ATTRIBUTES_KEEP``) and
# is overloaded with dict.get, making static detection unreliable.
_XPATH_METHODS = frozenset({
    "find",
    "findall",
    "findtext",
    "iter",
    "iterfind",
})

# Envelope-level reads.  Preserved by the preprocessor via verbatim
# envelope copy (``<ENVELOPE>`` through ``<REQUESTDESC>``), so they
# don't need per-element whitelisting.
ENVELOPE_READS: frozenset[str] = frozenset({
    # Envelope structure
    "ENVELOPE",
    "HEADER",
    "BODY",
    "IMPORTDATA",
    "REQUESTDESC",
    "REQUESTDATA",
    "REPORTNAME",
    "STATICVARIABLES",
    "SVCURRENTCOMPANY",
    "TALLYMESSAGE",
})

# Display-report format tags.  Out of scope for Work Item 8 — legacy
# DSPACCNAME/DSPACCINFO flat-pair format is handled by a separate parser
# path (``_parse_display``) and slim preprocessing doesn't apply.
DISPLAY_REPORT_READS: frozenset[str] = frozenset({
    "DSPACCNAME",
    "DSPDISPNAME",
    "DSPCLDRAMT",
    "DSPCLDRAMTA",
    "DSPCLCRAMT",
    "DSPCLCRAMTA",
})


# ---------------------------------------------------------------------------
# AST extraction
# ---------------------------------------------------------------------------

def _extract_xpath_literals(source: str) -> set[str]:
    """Return every string literal passed as first positional arg to
    ``.find`` / ``.findall`` / ``.findtext`` / ``.iter`` / ``.iterfind``.
    """
    tree = ast.parse(source)
    literals: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in _XPATH_METHODS:
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            literals.add(first.value)
    return literals


def _bare_tags(xpath: str) -> set[str]:
    """Split a (possibly compound) xpath literal into bare tag names.

    Handles:
      - ``"LEDGER"``                           → {"LEDGER"}
      - ``".//SVCURRENTCOMPANY"``              → {"SVCURRENTCOMPANY"}
      - ``"DSPCLDRAMT/DSPCLDRAMTA"``           → {"DSPCLDRAMT", "DSPCLDRAMTA"}
      - ``"LEDGER[@NAME='foo']"``              → {"LEDGER"}
      - ``"BILLALLOCATIONS.LIST"``             → {"BILLALLOCATIONS.LIST"}
      - ``"@NAME"``                            → {"NAME"}   (attribute axis)

    Wildcards (``*``), empty segments, and bare dots are dropped.
    """
    out: set[str] = set()
    for part in xpath.split("/"):
        seg = part.strip()
        if not seg or seg == ".":
            continue
        seg = seg.split("[", 1)[0].strip()      # drop predicates
        seg = seg.lstrip("@")                    # drop attribute axis
        if seg and seg != "*":
            out.add(seg)
    return out


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_every_parser_tag_is_whitelisted() -> None:
    """The AST-level coverage contract — see module docstring."""
    source = _PARSER_PATH.read_text(encoding="utf-8")
    raw_xpaths = _extract_xpath_literals(source)

    bare: set[str] = set()
    for xp in raw_xpaths:
        bare |= _bare_tags(xp)

    covered = (
        wl.TALLYMESSAGE_CHILDREN_KEEP
        | wl.LEDGER_CHILDREN_KEEP
        | wl.GROUP_CHILDREN_KEEP
        | wl.BILLALLOC_CHILDREN_KEEP
        | ENVELOPE_READS
        | DISPLAY_REPORT_READS
    )

    uncovered = bare - covered
    assert not uncovered, (
        f"Parser reads tags not covered by whitelist or allowlists: "
        f"{sorted(uncovered)}.\n"
        f"Classify each: new All Masters tag -> add to appropriate KEEP set "
        f"in tally_tag_whitelist.py; envelope-level read -> add to "
        f"ENVELOPE_READS in this test; display-report format -> add to "
        f"DISPLAY_REPORT_READS in this test."
    )


def test_whitelist_entries_look_like_tally_tags() -> None:
    """Typo sanity check — Tally tags are ALLCAPS (optionally with ``.LIST``)."""
    all_whitelisted = (
        wl.TALLYMESSAGE_CHILDREN_KEEP
        | wl.LEDGER_CHILDREN_KEEP
        | wl.GROUP_CHILDREN_KEEP
        | wl.BILLALLOC_CHILDREN_KEEP
    )
    for tag in all_whitelisted:
        normalized = tag.replace(".", "").replace("_", "")
        assert normalized.isupper() and normalized.isalnum(), (
            f"Whitelist entry {tag!r} doesn't match ALLCAPS Tally tag "
            f"convention — likely a typo"
        )


def test_whitelist_version_is_semver() -> None:
    """``WHITELIST_VERSION`` must be numeric semver so the sidecar log's
    version line stays machine-parseable if tooling needs it later."""
    version = wl.WHITELIST_VERSION
    parts = version.split(".")
    assert len(parts) in (2, 3), (
        f"Expected semver like '1.0' or '1.0.0', got {version!r}"
    )
    for part in parts:
        assert part.isdigit(), f"Semver part {part!r} is not numeric"


def test_envelope_and_display_allowlists_disjoint_from_whitelist() -> None:
    """ENVELOPE_READS / DISPLAY_REPORT_READS should not re-cover tags
    already in the whitelist — that would hide whitelist drift.

    Exception: TALLYMESSAGE is a structural tag that appears at envelope
    depth as a wrapper AND is implicitly part of TALLYMESSAGE_CHILDREN_KEEP's
    role (keep sets gate its CHILDREN).  Allowed to appear in both.
    """
    whitelist_tags = (
        wl.TALLYMESSAGE_CHILDREN_KEEP
        | wl.LEDGER_CHILDREN_KEEP
        | wl.GROUP_CHILDREN_KEEP
        | wl.BILLALLOC_CHILDREN_KEEP
    )
    allowed_overlap: frozenset[str] = frozenset()  # none currently
    overlap_env = (ENVELOPE_READS & whitelist_tags) - allowed_overlap
    overlap_disp = (DISPLAY_REPORT_READS & whitelist_tags) - allowed_overlap
    assert not overlap_env, (
        f"ENVELOPE_READS overlaps whitelist: {sorted(overlap_env)} — "
        f"drop from ENVELOPE_READS so coverage traces through the "
        f"whitelist set instead"
    )
    assert not overlap_disp, (
        f"DISPLAY_REPORT_READS overlaps whitelist: {sorted(overlap_disp)}"
    )
