"""Tally XML tag whitelist — the contract between parser and slim preprocessor.

This module is the single source of truth for "which XML tags from a Tally
All Masters export does rgi_migration's parser actually read?"  It exists
because the Windows slim preprocessor (``tools/tally_slim/preprocessor.py``)
needs to know what to keep and what to strip when compressing a 200+ MB
Tally export down to a 3-6 MB slim file, and the parser
(``rgi_migration/parsers/tally_xml_parser.py``) needs to keep reading the
same fields after the strip.

Contract
--------
1. The parser reads ONLY the tags listed below.
2. The preprocessor preserves ONLY the tags listed below.
3. If you teach the parser to read a new tag, add it here in the same
   commit.  Both sides stay synchronised.
4. Two tests enforce this contract:
   - ``test_tally_slim_roundtrip.py`` — parse(full) == parse(slim)
   - ``test_whitelist_covers_parser_reads.py`` — every tag the parser
     reads is either whitelisted or in the envelope-level allowlist.

Scope
-----
This whitelist covers the "All Masters" XML format only.  The legacy
display-report format (DSPACCNAME/DSPACCINFO flat pairs, see
``_parse_display`` in the parser) is out of scope: display-report exports
are small enough to upload directly without slimming, and they don't
share the LEDGER/GROUP/TALLYMESSAGE envelope structure.

Envelope-level reads (``SVCURRENTCOMPANY``, ``REPORTNAME``) are NOT
whitelisted here.  The preprocessor copies the envelope (``<ENVELOPE>``
through ``<REQUESTDESC>``) verbatim, so those elements survive without
per-tag filtering.  Keeping the whitelist scoped to element-level
children keeps the contract tight.

Versioning
----------
``WHITELIST_VERSION`` follows semver-lite: bump on any additive or
subtractive change to the keep sets.  The preprocessor embeds this
version in its sidecar log so a slim file can be traced to the
whitelist revision it was built against.

When bumping:
- MAJOR — removed a tag from a keep set (potentially breaks old slim files)
- MINOR — added a tag (old slim files still parse, new ones are richer)
- PATCH — docstring / comment fixes only, no set changes

See Also
--------
- ``rgi_migration/parsers/tally_xml_parser.py`` — the parser this
  whitelist mirrors.  ``.find*`` / ``.iter`` / ``.get`` call sites in
  ``_parse_masters``, ``_parse_group_element``, ``_parse_ledger_element``
  are the exhaustive list of reads.
- ``tools/tally_slim/preprocessor.py`` — consumes these sets.
- ``docs/slim_tally_export.md`` — architectural discussion that led
  to the preprocessor-over-TDL decision.
"""
from __future__ import annotations

WHITELIST_VERSION: str = "1.0"


# ---------------------------------------------------------------------------
# Top-level TALLYMESSAGE children (entity-type gate)
# ---------------------------------------------------------------------------
#
# The parser iterates ``root.iter("GROUP")`` and ``root.iter("LEDGER")``
# and reads nothing else at TALLYMESSAGE depth.  Everything else
# (STOCKITEM, STOCKGROUP, VOUCHERTYPE, COSTCENTRE, UNIT, GODOWN, CURRENCY,
# INCOMETAXCLASSIFICATION, INCOMETAXSLAB, SERIALNUMBER, CMPNOTE,
# STOCKCATEGORY, COSTCATEGORY, TAXUNIT, COMPANY, plus any future entity
# types Tally adds) is dropped wholesale by the preprocessor.
#
# On the CACSPU 221 MB reference export, STOCKITEM alone is 4,167 entities
# / 17.7 MB of content — by far the biggest non-LEDGER contributor.
TALLYMESSAGE_CHILDREN_KEEP: frozenset[str] = frozenset({
    "LEDGER",
    "GROUP",
})


# ---------------------------------------------------------------------------
# LEDGER element — child tags
# ---------------------------------------------------------------------------
#
# Parser reads (in ``_parse_ledger_element``):
#   - @NAME attribute (see LEDGER_ATTRIBUTES_KEEP below)
#   - <PARENT> text — empty/absent means "system account" (P&L A/c)
#   - <OPENINGBALANCE> text — falls back to sum of bill OBs if missing
#   - <BILLALLOCATIONS.LIST> — zero or more, recursed into
#
# Stripped by preprocessor:
#   GUID, ALTERID, ISDELETED, ISUPDATINGTARGETID, ISSECURITYONWHENENTERED,
#   ASORIGINAL, NAME.LIST, LANGUAGENAME.LIST, MAILINGNAME, ISBILLWISEON,
#   ISCOSTCENTRESON, ISCOSTTRACKINGON, ISRATEINCLUSIVEVAT, AFFECTSSTOCK,
#   SORTPOSITION, BILLCREDITPERIOD, TAXTYPE, TAXCLASSIFICATIONNAME,
#   FORPAYROLL, INTERESTCOLLECTION.LIST, GSTDETAILS.LIST,
#   GSTCLASSFNIGSTRATES.LIST, VATDETAILS.LIST, SERVICETAXDETAILS.LIST,
#   EXCISETARIFFDETAILS.LIST, EXTARIFFDUTYHEADDETAILS.LIST,
#   SCHVIDETAILS.LIST, SALESTAXCESSDETAILS.LIST, TCSCATEGORYDETAILS.LIST,
#   TDSCATEGORYDETAILS.LIST, OLDAUDITENTRIES.LIST, ACCOUNTAUDITENTRIES.LIST,
#   AUDITENTRIES.LIST, OLDAUDITENTRYIDS.LIST, EXCLUDEDTAXATIONS.LIST,
#   AUDITDETAILS.LIST, BANKALLOCATIONS.LIST, PAYMENTDETAILS.LIST,
#   XBRLDETAIL.LIST, ISOTHTERRITORYASSESSEE, ISCONDENSED, ...
LEDGER_CHILDREN_KEEP: frozenset[str] = frozenset({
    "PARENT",
    "OPENINGBALANCE",
    "BILLALLOCATIONS.LIST",
})


# ---------------------------------------------------------------------------
# LEDGER element — XML attributes
# ---------------------------------------------------------------------------
#
# Parser reads only @NAME via ``elem.get("NAME")``.  Real Tally exports
# sometimes carry @RESERVEDNAME or namespace-scoped attributes on LEDGER;
# these are harmless but add bytes, so we drop them.
LEDGER_ATTRIBUTES_KEEP: frozenset[str] = frozenset({
    "NAME",
})


# ---------------------------------------------------------------------------
# GROUP element — child tags
# ---------------------------------------------------------------------------
#
# Parser reads (in ``_parse_group_element``):
#   - @NAME attribute (see GROUP_ATTRIBUTES_KEEP below)
#   - <PARENT> text — empty/self-ref means root group
#   - <ISDEEMEDPOSITIVE> text — defaults "Yes" if absent
#
# Stripped by preprocessor: GUID, ALTERID, ISDELETED, ISCONDENSED,
# ISRESERVED, SORTPOSITION, AFFECTSSTOCK, NAME.LIST, LANGUAGENAME.LIST,
# plus all *DETAILS.LIST subtrees (GSTDETAILS.LIST et al. attach to
# GROUPs as well as LEDGERs in newer Tally versions).
GROUP_CHILDREN_KEEP: frozenset[str] = frozenset({
    "PARENT",
    "ISDEEMEDPOSITIVE",
})


# ---------------------------------------------------------------------------
# GROUP element — XML attributes
# ---------------------------------------------------------------------------
GROUP_ATTRIBUTES_KEEP: frozenset[str] = frozenset({
    "NAME",
})


# ---------------------------------------------------------------------------
# BILLALLOCATIONS.LIST child — per-bill subtree
# ---------------------------------------------------------------------------
#
# Parser reads (inline in ``_parse_ledger_element``'s bill loop):
#   - <NAME> text — bill reference (empty rows are skipped)
#   - <BILLDATE> text — YYYYMMDD, used for TB-date inference
#   - <OPENINGBALANCE> text — signed (negative = Dr, positive = Cr)
#
# Stripped by preprocessor: ISADVANCE, BILLCREDITPERIOD, BILLTYPE.
# Note: ISADVANCE is used downstream by the Generator (Work Item 7)
# but via the mapper's side-lookup, NOT via the parser.  The parser
# does not read ISADVANCE today.  If that changes, add it here.
BILLALLOC_CHILDREN_KEEP: frozenset[str] = frozenset({
    "NAME",
    "BILLDATE",
    "OPENINGBALANCE",
})


# ---------------------------------------------------------------------------
# Envelope-level tags NOT whitelisted here
# ---------------------------------------------------------------------------
#
# The following tags are read by the parser at envelope depth, not at
# LEDGER/GROUP child depth, and are preserved by the preprocessor via
# verbatim envelope copy rather than per-element filtering:
#
#   - SVCURRENTCOMPANY (read by ``_parse_masters`` via root.find)
#   - REPORTNAME (used by ``_detect_format`` to identify All Masters)
#   - TALLYMESSAGE (structural wrapper, not a "read" per se)
#
# The ``test_whitelist_covers_parser_reads.py`` test knows about this
# allowlist and exempts these tags from whitelist coverage.
ENVELOPE_READS: frozenset[str] = frozenset({
    "SVCURRENTCOMPANY",
    "REPORTNAME",
    "TALLYMESSAGE",
})
