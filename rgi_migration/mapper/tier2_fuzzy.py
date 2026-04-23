"""Tier-2 composite matcher pipeline.

Three sub-matchers run in precision-descending order; first match wins:

    Sub-matcher 1 — normalization-strong exact match
        Handler for letter-spacing / punctuation / case variance
        (``T D S On Salary 193`` vs ``TDS On Salary -193``). Pure
        equality after a 7-step normalization; zero false-positive
        risk.

    Sub-matcher 2 — account-number identifier match
        Handler for shared-digit-suffix ledgers (Bank accounts, GST
        IDs — ``\\b\\d{6,}\\b`` strong IDs). Intersection semantic:
        every strong ID from the ledger must appear in the candidate
        account.

    Sub-matcher 3 — classical fuzzy (``partial_ratio``)
        Handler for residual near-duplicate / token-variant cases
        (``Electrical Fitting`` vs ``Electric Fitting``; ``Library
        Books`` vs ``Library Book``). Length-ratio guard L=0.5
        blocks short-account-into-long-ledger noise. Default
        threshold 85.0, tunable via Mapper ctor.

Per CACSPU Phase A empirical research (docs/WEEK4_* / Phase A
research probe): the three sub-matchers address the three distinct
failure families surfaced by calibration. WRatio was discarded after
Phase B v1 — it produced wrong-answer matches on TDS / BoM families.

All three sub-matchers filter candidates by leaf (is_group=0) +
root_type match to prevent cross-type false positives. The mapper's
step 7.5 delegates to :func:`find_tier2_match` which runs the three
sub-matchers in order and returns a terminal result.

Abbreviation-expansion family (``Edu`` → ``Educational``) is
DEFERRED per Phase A Q1 to Item 5 reviewer-promoted rules. Two
CACSPU cases; revisit priority at Item 10 (Tier-3 Claude API)
opening.

Subtier is communicated via the ``matched_rule`` field on the
resulting ``MappedDecision`` (colon-delimited prefix ``tier2:``):

    ``tier2:norm_strong``     — Sub-matcher 1
    ``tier2:acct_num``        — Sub-matcher 2
    ``tier2:fuzzy_classical`` — Sub-matcher 3

Tie handling consistent across all three matchers: ambiguous ties
(multiple equally-ranked candidates) produce a refusal with
``excluded_reason`` populated rather than an arbitrary pick. The
mapper routes the refusal as ``tier=unmapped``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from rapidfuzz import fuzz, process

from rgi_migration.mapper.tier1_supplier import _clean

LOG = logging.getLogger(__name__)

# Subtier label prefix — exposed as module constants so call sites and
# tests can reference them symbolically without hardcoding literals.
SUBTIER_NORM = "tier2:norm_strong"
SUBTIER_ACCT_NUM = "tier2:acct_num"
SUBTIER_FUZZY = "tier2:fuzzy_classical"

# Regex: COA entity-abbr suffix (" - CACSPU", " - GHRIMR"). Strip
# before normalizing so ``Cash - CACSPU`` compares to ``Cash``.
_ABBR_SUFFIX_RE = re.compile(r"\s*-\s*[A-Z]{3,10}\s*$")

# Regex: non-alphanumeric runs. Collapse to a single space in step 4
# of the normalization pipeline.
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

# Regex: strong digit identifiers (>=6 contiguous digits). Weak IDs
# (3-5 digits, e.g. section numbers) are NOT matched — deferred per
# Phase A Q4.
_STRONG_ID_RE = re.compile(r"\b\d{6,}\b")

# Length-ratio guard for Sub-matcher 3. Refuses matches where one
# side is less than L times the other's length. L=0.5 per Phase A Q8.
_LENGTH_GUARD_L = 0.5


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Tier2Match:
    """Outcome of a Tier-2 composite match attempt.

    Four terminal states encoded in one dataclass:
        1. matched:         account_name set, subtier set
        2. ambiguous tie:   account_name None, subtier set, tie_reason
                            populated
        3. length-guard refused: account_name None, subtier set,
                                 tie_reason explains "length-ratio
                                 guard refused"
        4. no match:        account_name None, subtier None
    """

    account_name: str | None
    subtier: str | None = None
    score: float = 0.0
    tie_reason: str | None = None

    @property
    def is_match(self) -> bool:
        return self.account_name is not None

    @property
    def is_refusal(self) -> bool:
        """True when a sub-matcher looked at this ledger and refused
        (tie or length-guard) rather than producing no-signal. Mapper
        surfaces refusals as tier=unmapped with excluded_reason."""
        return self.account_name is None and self.tie_reason is not None


# ---------------------------------------------------------------------------
# Normalization pipeline (Sub-matcher 1 primitives; Phase A Q2)
# ---------------------------------------------------------------------------


def _strip_abbr_suffix(name: str) -> str:
    """Step 1 — strip the " - CACSPU" style entity-abbr suffix from
    COA names. Tally ledgers don't carry this suffix, so the same
    pipeline applied to Tally names is a no-op."""
    return _ABBR_SUFFIX_RE.sub("", name or "")


def normalize(name: str) -> str:
    """Full normalization pipeline (spaces preserved).

    1. Strip entity-abbr suffix (COA-side only)
    2. tier1_supplier._clean (party-ID strip)
    3. Lowercase
    4. Non-alphanumeric runs → single space
    5. Collapse multiple spaces → single space
    6. Strip leading/trailing whitespace
    """
    s = _strip_abbr_suffix(name or "")
    s = _clean(s)
    s = s.lower()
    s = _NON_ALNUM_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s)
    s = s.strip()
    return s


def normalize_strong(name: str) -> str:
    """Step-7 strong variant: drop ALL remaining whitespace from
    :func:`normalize`'s output. Equal strings after this are the
    Sub-matcher 1 equivalence class."""
    return normalize(name).replace(" ", "")


# ---------------------------------------------------------------------------
# Candidate-pool helper (shared across matchers)
# ---------------------------------------------------------------------------


def _candidate_pool_leaf_root(
    coa: dict[str, Any], root_type: str,
) -> list[Any]:
    """Return COA accounts that are leaf (is_group=0) AND match the
    ledger's root_type. Q6 / Q7 — primary correctness lever applied
    uniformly across all three sub-matchers."""
    if not root_type:
        return []
    return [
        acct for acct in coa.values()
        if (not acct.is_group) and acct.root_type == root_type
    ]


# ---------------------------------------------------------------------------
# Sub-matcher 1 — normalization-strong equality
# ---------------------------------------------------------------------------


def find_by_normalization(
    ledger: Any, coa: dict[str, Any],
) -> Tier2Match:
    """Sub-matcher 1. Build the normalized-strong key for the Tally
    ledger, scan the root-type-filtered COA for equality, return.

    Tie handling: if two COA accounts normalize to the same key,
    refuse with excluded_reason citing both. Zero-false-positive
    invariant of equality matching holds only when keys are unique.
    """
    root_type = getattr(ledger, "root_type", "") or ""
    if not root_type:
        return Tier2Match(account_name=None)

    target_key = normalize_strong(ledger.name)
    if not target_key:
        return Tier2Match(account_name=None)

    pool = _candidate_pool_leaf_root(coa, root_type)
    hits = [a for a in pool if normalize_strong(a.name) == target_key]

    if len(hits) == 1:
        return Tier2Match(
            account_name=hits[0].name,
            subtier=SUBTIER_NORM,
            score=1.0,
        )

    if len(hits) >= 2:
        names = ", ".join(sorted(h.name for h in hits[:3]))
        reason = (
            f"Tier-2 norm_strong: ambiguous — {len(hits)} candidates "
            f"share normalized key {target_key!r}: {names}"
        )
        LOG.info("tier2:norm_strong ambiguous on %r: %s", ledger.name, reason)
        return Tier2Match(
            account_name=None, subtier=SUBTIER_NORM, tie_reason=reason,
        )

    return Tier2Match(account_name=None)


# ---------------------------------------------------------------------------
# Sub-matcher 2 — strong account-number intersection
# ---------------------------------------------------------------------------


def _extract_strong_ids(name: str) -> list[str]:
    """Return the list of ≥6-digit contiguous runs in ``name``,
    preserving order. Phase A Q4."""
    return _STRONG_ID_RE.findall(name or "")


def find_by_account_number(
    ledger: Any, coa: dict[str, Any],
) -> Tier2Match:
    """Sub-matcher 2. Extract strong IDs from the Tally ledger. For
    a candidate to match: every strong ID from the ledger must also
    appear in the candidate account's name (intersection semantic,
    Phase A Q5). Root-type filter applied.

    Ties across multiple candidates satisfying the intersection →
    refuse with excluded_reason (Q6).
    """
    ledger_ids = _extract_strong_ids(ledger.name or "")
    if not ledger_ids:
        return Tier2Match(account_name=None)

    root_type = getattr(ledger, "root_type", "") or ""
    if not root_type:
        return Tier2Match(account_name=None)

    ledger_ids_set = set(ledger_ids)
    pool = _candidate_pool_leaf_root(coa, root_type)
    hits = [
        a for a in pool
        if ledger_ids_set.issubset(set(_extract_strong_ids(a.name)))
    ]

    if len(hits) == 1:
        return Tier2Match(
            account_name=hits[0].name,
            subtier=SUBTIER_ACCT_NUM,
            score=1.0,
        )

    if len(hits) >= 2:
        names = ", ".join(sorted(h.name for h in hits[:3]))
        reason = (
            f"Tier-2 acct_num: ambiguous — {len(hits)} candidates "
            f"share strong IDs {sorted(ledger_ids_set)}: {names}"
        )
        LOG.info("tier2:acct_num ambiguous on %r: %s", ledger.name, reason)
        return Tier2Match(
            account_name=None, subtier=SUBTIER_ACCT_NUM, tie_reason=reason,
        )

    return Tier2Match(account_name=None)


# ---------------------------------------------------------------------------
# Sub-matcher 3 — classical fuzzy (partial_ratio + length guard)
# ---------------------------------------------------------------------------


def _passes_length_guard(a: str, b: str, L: float = _LENGTH_GUARD_L) -> bool:
    """Phase A Q8 — refuse matches where one side is less than L times
    the other's length. Blocks short-account-into-long-ledger partial
    matches."""
    la, lb = len(a), len(b)
    if la == 0 or lb == 0:
        return False
    return min(la, lb) / max(la, lb) >= L


def find_by_classical_fuzzy(
    ledger: Any, coa: dict[str, Any], threshold: float = 80.0,
) -> Tier2Match:
    """Sub-matcher 3. Classical fuzzy via rapidfuzz.partial_ratio.
    Length-ratio guard applied per-candidate BEFORE scoring. Tie
    handling refuses 2-decimal-equal top-1 / top-2. Root-type
    filter via candidate-pool helper.
    """
    cleaned_ledger = _clean(ledger.name or "")
    if not cleaned_ledger:
        return Tier2Match(account_name=None)

    root_type = getattr(ledger, "root_type", "") or ""
    if not root_type:
        return Tier2Match(account_name=None)

    pool = _candidate_pool_leaf_root(coa, root_type)

    # Build cleaned-name choices, honoring the length-ratio guard.
    # A candidate that fails the guard is silently dropped from the
    # pool — not a refusal, since the guard is a noise filter not a
    # correctness signal.
    choices: dict[str, str] = {}
    for a in pool:
        cleaned_a = _clean(_strip_abbr_suffix(a.name))
        if not cleaned_a:
            continue
        if not _passes_length_guard(cleaned_ledger, cleaned_a):
            continue
        choices[a.name] = cleaned_a

    if not choices:
        return Tier2Match(account_name=None)

    hits = process.extract(
        cleaned_ledger, choices,
        scorer=fuzz.partial_ratio,
        limit=2,
        score_cutoff=threshold,
    )
    if not hits:
        return Tier2Match(account_name=None)

    _, top_score, top_key = hits[0]
    top_score_f = float(top_score)

    if len(hits) >= 2:
        _, runner_score, runner_key = hits[1]
        if round(top_score_f, 2) == round(float(runner_score), 2):
            reason = (
                f"Tier-2 fuzzy_classical: ambiguous tie at score "
                f"{top_score_f:.2f} between {top_key!r} and {runner_key!r}"
            )
            LOG.info(
                "tier2:fuzzy_classical ambiguous tie on %r: %r vs %r at %.2f",
                ledger.name, top_key, runner_key, top_score_f,
            )
            return Tier2Match(
                account_name=None, subtier=SUBTIER_FUZZY,
                score=top_score_f / 100.0, tie_reason=reason,
            )

    return Tier2Match(
        account_name=top_key,
        subtier=SUBTIER_FUZZY,
        score=top_score_f / 100.0,
    )


# ---------------------------------------------------------------------------
# Composite orchestrator
# ---------------------------------------------------------------------------


def find_tier2_match(
    ledger: Any, coa: dict[str, Any], fuzzy_threshold: float = 80.0,
) -> Tier2Match:
    """Run sub-matchers 1 → 2 → 3. First match (or refusal with
    excluded_reason) wins.
    excluded_reason) wins. A sub-matcher returning a pure no-signal
    result (account_name None AND tie_reason None) falls through to
    the next matcher.

    Mapper's step 7.5 calls this function; when the result is a
    match, build a MappedDecision at tier=tier2_fuzzy with
    matched_rule=<subtier>. When the result is a refusal, build a
    MappedDecision at tier=unmapped with excluded_reason set.
    """
    m1 = find_by_normalization(ledger, coa)
    if m1.is_match or m1.is_refusal:
        return m1

    m2 = find_by_account_number(ledger, coa)
    if m2.is_match or m2.is_refusal:
        return m2

    m3 = find_by_classical_fuzzy(ledger, coa, threshold=fuzzy_threshold)
    return m3
