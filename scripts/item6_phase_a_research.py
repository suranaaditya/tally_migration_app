"""Item 6 — re-scoped Phase A research probe.

Runs on the bench. Gathers empirical evidence for:

    R1 — enumerate failure families on CACSPU's unmapped-with-balance
         ledgers (pre-tier2_fuzzy baseline)
    R2 — test a proposed normalization spec against the TDS / letter-
         spacing family
    R3 — test an account-number regex spec against the Bank-of-
         Maharashtra family + scan for other digit-run matches in
         the COA

Run:

    bench --site erp.jewonline.in console
    >>> import sys
    >>> sys.path.insert(0,
    ...     '/home/frappe/frappe-bench/apps/rgi_migration/scripts')
    >>> import item6_phase_a_research
    >>> item6_phase_a_research.main()
"""
from __future__ import annotations

import re
from collections import Counter

import frappe

from rgi_migration.mapper.tier2_fuzzy import _clean

SESSION = "TMS-CACSPU--00495"


def _hr(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


# ---------------------------------------------------------------------------
# Normalization spec candidate for R2
# ---------------------------------------------------------------------------


_ABBR_SUFFIX_RE = re.compile(r"\s*-\s*[A-Z]{3,10}\s*$")   # " - CACSPU"
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def normalize(name: str, abbr: str = "") -> str:
    """Proposed normalization for Sub-matcher 1.

    1. Strip the entity-abbr suffix (" - CACSPU") from COA names.
    2. Use tier1_supplier._clean to strip tally-party-ID suffixes.
    3. Lowercase.
    4. Collapse non-alphanumeric runs to a single space.
    5. Strip surrounding whitespace.

    Result: a "skeleton" string that preserves word and digit identity
    but drops all spacing/punctuation variance. `T D S On Salary 193`
    and `TDS On Salary -193` both collapse to `t d s on salary 193`
    vs `tds on salary 193` — still NOT equal after step 4 because
    `T D S` vs `TDS` remains.
    """
    s = _clean(name or "")
    s = _ABBR_SUFFIX_RE.sub("", s)
    s = s.lower()
    s = _NON_ALNUM_RE.sub(" ", s)
    s = s.strip()
    return s


def normalize_strong(name: str, abbr: str = "") -> str:
    """Stronger normalization — additionally collapses letter-spacing.

    After the base normalization, removes ALL whitespace. This forces
    `t d s on salary 193` → `tdsonsalary193`, matching
    `tds on salary 193` → `tdsonsalary193`. True equality.
    """
    base = normalize(name, abbr=abbr)
    return base.replace(" ", "")


# ---------------------------------------------------------------------------
# Account-number regex spec for R3
# ---------------------------------------------------------------------------


_STRONG_ID_RE = re.compile(r"\b\d{6,}\b")     # ≥6 contiguous digits
_WEAK_ID_RE = re.compile(r"\b\d{3,5}\b")      # 3-5 digits (section / year)


def extract_ids(name: str) -> tuple[list[str], list[str]]:
    """Returns (strong_ids, weak_ids)."""
    return _STRONG_ID_RE.findall(name or ""), _WEAK_ID_RE.findall(name or "")


# ---------------------------------------------------------------------------
# R1 — unmapped enumeration
# ---------------------------------------------------------------------------


def _load_coa(company: str) -> list[dict]:
    return frappe.get_all(
        "Account",
        filters={"company": company, "disabled": 0, "is_group": 0},
        fields=["name", "root_type"],
        limit_page_length=0,
    )


def _get_unmapped_ledgers() -> list[dict]:
    return frappe.get_all(
        "Mapping Decision",
        filters={
            "session": SESSION,
            "tier": ["in", ["unmapped", "tier2_fuzzy"]],
        },
        fields=["name", "tally_name", "tally_root_type",
                "opening_dr", "opening_cr", "tier"],
        limit_page_length=0,
        order_by="tally_name",
    )


def _candidate_targets_by_substring(
    tally_name: str, coa: list[dict], root_type: str, max_out: int = 5,
) -> list[str]:
    """Heuristic: find COA leaves (same root_type) that share a
    distinctive token (>=5 chars) with the Tally name. Used for R1
    manual-audit column only — not a matcher."""
    tokens = [t.lower() for t in re.split(r"[^A-Za-z0-9]+", tally_name or "")
              if len(t) >= 5]
    if not tokens:
        return []
    hits: list[tuple[int, str]] = []
    for a in coa:
        if a["root_type"] != root_type:
            continue
        aln = a["name"].lower()
        score = sum(1 for t in tokens if t in aln)
        if score:
            hits.append((score, a["name"]))
    hits.sort(key=lambda x: (-x[0], x[1]))
    return [h[1] for h in hits[:max_out]]


def r1_enumerate_families(coa: list[dict]) -> list[dict]:
    _hr("R1 — Unmapped ledger enumeration (family classification)")
    ledgers = _get_unmapped_ledgers()
    print(f"  Pulled {len(ledgers)} ledgers (tier IN unmapped OR tier2_fuzzy)")

    rows: list[dict] = []
    for lg in ledgers:
        candidates = _candidate_targets_by_substring(
            lg.tally_name or "", coa, lg.tally_root_type or "",
        )
        # Family heuristics (initial classification; refine manually)
        name = lg.tally_name or ""
        name_lc = name.lower()
        family: list[str] = []
        # Letter-spacing variance — tokens of length 1
        single_letter_tokens = sum(
            1 for t in re.split(r"\s+", name.strip()) if len(t) == 1
        )
        if single_letter_tokens >= 2:
            family.append("letter-spacing")
        # Strong account-number identifier
        strong, weak = extract_ids(name)
        if strong:
            family.append(f"acct-num({'/'.join(strong)})")
        elif weak:
            family.append(f"weak-id({'/'.join(weak)})")
        # Parenthetical content
        if "(" in name and ")" in name:
            family.append("parenthetical")
        # Vendor-looking party ledger (person name - Advance / Fees)
        if re.search(r"-\s*(Advance|Fees|Ph D)", name, re.IGNORECASE):
            family.append("person-suffix")

        rows.append({
            "tally": name,
            "root": lg.tally_root_type,
            "dr": lg.opening_dr, "cr": lg.opening_cr,
            "current_tier": lg.tier,
            "family": ", ".join(family) or "none",
            "candidates": candidates,
        })

    # Print structured
    print()
    print(f"{'Tally':<52} | {'Root':<10} | {'Tier':<15} | Family")
    print("-" * 110)
    for r in rows:
        tally = (r["tally"] or "")[:51]
        tier = (r["current_tier"] or "")[:15]
        print(f"{tally:<52} | {r['root']:<10} | {tier:<15} | {r['family']}")

    print()
    print("--- Candidate correct targets (heuristic — substring overlap) ---")
    for r in rows:
        print(f"\n  Tally: {r['tally']!r}  [{r['family']}]")
        if not r["candidates"]:
            print("    (no substring-overlap candidates)")
        for c in r["candidates"]:
            print(f"    → {c}")

    return rows


# ---------------------------------------------------------------------------
# R2 — normalization spec test
# ---------------------------------------------------------------------------


def r2_test_normalization(rows: list[dict], coa: list[dict]) -> None:
    _hr("R2 — Normalization spec test on unmapped ledgers")
    # Build COA normalized index
    coa_by_root: dict[str, dict[str, str]] = {}
    coa_by_root_strong: dict[str, dict[str, str]] = {}
    for a in coa:
        rt = a["root_type"] or ""
        coa_by_root.setdefault(rt, {})[normalize(a["name"])] = a["name"]
        coa_by_root_strong.setdefault(rt, {})[normalize_strong(a["name"])] = a["name"]

    print(f"{'Tally (orig)':<48} | {'Norm':<40} | Norm+ | {'Matches norm':<40} | Matches norm+")
    print("-" * 150)
    n_norm_hit = 0
    n_strong_hit = 0
    for r in rows:
        orig = r["tally"]
        norm = normalize(orig)
        strong = normalize_strong(orig)
        root = r["root"] or ""
        hit_norm = coa_by_root.get(root, {}).get(norm)
        hit_strong = coa_by_root_strong.get(root, {}).get(strong)
        if hit_norm:
            n_norm_hit += 1
        if hit_strong:
            n_strong_hit += 1
        o = orig[:47]; n = norm[:39]; s = strong[:6]
        hn = (hit_norm or "-")[:39]; hs = "YES" if hit_strong else "-"
        print(f"{o:<48} | {n:<40} | {s:<6} | {hn:<40} | {hs}")

    print()
    print(f"  normalize() exact hits against COA: {n_norm_hit} / {len(rows)}")
    print(f"  normalize_strong() exact hits:      {n_strong_hit} / {len(rows)}")


# ---------------------------------------------------------------------------
# R3 — account-number regex test
# ---------------------------------------------------------------------------


def r3_test_account_numbers(rows: list[dict], coa: list[dict]) -> None:
    _hr("R3 — Account-number regex test")
    # Index COA accounts by the strong IDs (>=6 digits) they contain
    coa_by_strong_id: dict[str, list[str]] = {}
    for a in coa:
        strong, _ = extract_ids(a["name"])
        for s in strong:
            coa_by_strong_id.setdefault(s, []).append(a["name"])

    print(f"  COA: {len(coa_by_strong_id)} distinct >=6-digit IDs across "
          f"{sum(len(v) for v in coa_by_strong_id.values())} accounts")
    print()

    # For each unmapped ledger, try to extract an ID and match
    print(f"{'Tally':<48} | {'Strong IDs':<25} | {'Weak IDs':<15} | Match in COA?")
    print("-" * 130)
    n_strong_unique = 0
    n_strong_ambig = 0
    for r in rows:
        strong, weak = extract_ids(r["tally"])
        if strong:
            hits_union: list[str] = []
            for s in strong:
                hits_union.extend(coa_by_strong_id.get(s, []))
            # Uniq preserving order
            seen = set()
            uniq = [x for x in hits_union if not (x in seen or seen.add(x))]
            if len(uniq) == 1:
                n_strong_unique += 1
                match_str = f"UNIQUE → {uniq[0]}"
            elif len(uniq) > 1:
                n_strong_ambig += 1
                match_str = f"AMBIG ({len(uniq)} cands)"
            else:
                match_str = "no match"
        else:
            match_str = "(no strong ID)"
        print(f"{(r['tally'] or '')[:47]:<48} | "
              f"{('/'.join(strong))[:24]:<25} | "
              f"{('/'.join(weak))[:14]:<15} | {match_str}")

    print()
    print(f"  Unmapped ledgers with unique strong-ID match: {n_strong_unique}")
    print(f"  Unmapped ledgers with ambiguous strong-ID match: {n_strong_ambig}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    _hr("ITEM 6 RE-SCOPED — Phase A research probe on CACSPU")
    session = frappe.get_doc("Tally Migration Session", SESSION)
    erpnext_company = frappe.get_doc(
        "Company Abbreviation", session.company_abbr
    ).erpnext_company
    coa = _load_coa(erpnext_company)
    print(f"  Company: {erpnext_company!r}   COA leaves: {len(coa)}")

    rows = r1_enumerate_families(coa)
    r2_test_normalization(rows, coa)
    r3_test_account_numbers(rows, coa)
