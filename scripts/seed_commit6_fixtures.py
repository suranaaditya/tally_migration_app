"""Commit 6 verification fixtures — TMS-CACSPU--00495.

Substitutes Aditya's fictional account names with real CACSPU accounts
that exist on the dev bench (proposed_account / final_account are Link
fields and Frappe rejects unknown Links). The semantic intent
(eligible vs skipped buckets) is preserved.
"""
import frappe

session = "TMS-CACSPU--00495"

print("=== Cleanup existing decisions ===")
existing = frappe.get_all("Mapping Decision", filters={"session": session}, pluck="name")
for d in existing:
    frappe.delete_doc("Mapping Decision", d, force=True)
frappe.db.commit()
print(f"Deleted {len(existing)} existing decisions")

print("=== Insert Commit 6 fixtures ===")
fixtures = [
    # 5 tier-1 ELIGIBLE for bulk approve (Pending + proposed + no final)
    {"tally_name": "Cash in Hand", "tally_parent_chain": "Asset > Capital Account > Cash",
     "tally_root_type": "Asset", "opening_dr": 85420.00, "opening_cr": 0.0,
     "net_amount": 85420.00, "net_side": "Dr", "tier": "tier1_exact",
     "proposed_account": "Advance For Festival - CACSPU",
     "review_action": "Pending", "confidence": 1.0,
     "proposed_dr": 85420.00, "proposed_cr": 0.0},

    {"tally_name": "Bank of Maharashtra A/c No. 60451301234",
     "tally_parent_chain": "Asset > Capital Account > Bank Accounts > Savings > Branch Banking",
     "tally_root_type": "Asset", "opening_dr": 320145.23, "opening_cr": 0.0,
     "net_amount": 320145.23, "net_side": "Dr", "tier": "tier1_exact",
     "proposed_account": "3455667812334 - Trail bank - CACSPU",
     "review_action": "Pending", "confidence": 1.0,
     "proposed_dr": 320145.23, "proposed_cr": 0.0},

    {"tally_name": "Petty Cash", "tally_parent_chain": "Asset > Capital Account > Cash",
     "tally_root_type": "Asset", "opening_dr": 12500.00, "opening_cr": 0.0,
     "net_amount": 12500.00, "net_side": "Dr", "tier": "tier1_rule",
     "proposed_account": "Advance For Affiliation - CACSPU",
     "review_action": "Pending", "confidence": 0.95,
     "proposed_dr": 12500.00, "proposed_cr": 0.0},

    {"tally_name": "Salary Payable", "tally_parent_chain": "Liability > Current > Salary",
     "tally_root_type": "Liability", "opening_dr": 0.0, "opening_cr": 450000.00,
     "net_amount": 450000.00, "net_side": "Cr", "tier": "tier1_pattern",
     "proposed_account": "1st Level Ph.d Registration Fees - CACSPU",
     "review_action": "Pending", "confidence": 0.92,
     "proposed_dr": 0.0, "proposed_cr": 450000.00},

    {"tally_name": "TDS Payable", "tally_parent_chain": "Liability > Current > Statutory",
     "tally_root_type": "Liability", "opening_dr": 0.0, "opening_cr": 75000.00,
     "net_amount": 75000.00, "net_side": "Cr", "tier": "tier1_exact",
     "proposed_account": "Advance Tax & TDS - CACSPU",
     "review_action": "Pending", "confidence": 1.0,
     "proposed_dr": 0.0, "proposed_cr": 75000.00},

    # 2 tier-1 with manual final_account (should be SKIPPED)
    {"tally_name": "Office Rent Advance", "tally_parent_chain": "Asset > Current > Advances",
     "tally_root_type": "Asset", "opening_dr": 50000.00, "opening_cr": 0.0,
     "net_amount": 50000.00, "net_side": "Dr", "tier": "tier1_exact",
     "proposed_account": "Abdul H A Aziz Khan - CACSPU",
     "final_account": "Accounts Written Off - CACSPU",
     "review_action": "Pending", "confidence": 1.0,
     "proposed_dr": 50000.00, "proposed_cr": 0.0},

    {"tally_name": "Utility Deposit", "tally_parent_chain": "Asset > Current > Deposits",
     "tally_root_type": "Asset", "opening_dr": 25000.00, "opening_cr": 0.0,
     "net_amount": 25000.00, "net_side": "Dr", "tier": "tier1_rule",
     "proposed_account": "Achamma Varghese Thomas - CACSPU",
     "final_account": "Accumulated Depreciation - CACSPU",
     "review_action": "Pending", "confidence": 0.88,
     "proposed_dr": 25000.00, "proposed_cr": 0.0},

    # 3 unmapped / supplier / anti-pattern Pending
    {"tally_name": "Opening Balance Equity", "tally_parent_chain": "Equity > Equity > System",
     "tally_root_type": "Equity", "opening_dr": 0.0, "opening_cr": 2500000.00,
     "net_amount": 2500000.00, "net_side": "Cr", "tier": "unmapped",
     "review_action": "Pending", "confidence": 0.0},

    {"tally_name": "Suspense Account", "tally_parent_chain": "Asset > Suspense > Temporary",
     "tally_root_type": "Asset", "opening_dr": 5000.00, "opening_cr": 0.0,
     "net_amount": 5000.00, "net_side": "Dr", "tier": "anti_pattern_blocked",
     "review_action": "Pending", "confidence": 0.0},

    {"tally_name": "AARNA SOLUTION-VA0290",
     "tally_parent_chain": "Liability > Sundry Creditors > Vendors > IT Services",
     "tally_root_type": "Liability", "opening_dr": 0.0, "opening_cr": 48500.00,
     "net_amount": 48500.00, "net_side": "Cr", "tier": "pending_supplier_creation",
     "review_action": "Pending", "confidence": 0.0},

    # 1 excluded P&L
    {"tally_name": "Sales Revenue", "tally_parent_chain": "Income > P&L > Revenue > Operating",
     "tally_root_type": "Income", "opening_dr": 0.0, "opening_cr": 500000.00,
     "net_amount": 500000.00, "net_side": "Cr", "tier": "excluded_pnl",
     "review_action": "Excluded (P&L)", "confidence": 1.0},
]

inserted = 0
errors = []
for f in fixtures:
    try:
        md = frappe.get_doc({"doctype": "Mapping Decision", "session": session, **f})
        md.insert()
        inserted += 1
    except Exception as exc:
        errors.append((f.get("tally_name"), str(exc)[:200]))

frappe.db.commit()
print(f"INSERTED {inserted} of {len(fixtures)}")
if errors:
    print("=== ERRORS ===")
    for name, err in errors:
        print(f"  {name!r}: {err}")
else:
    print("No errors.")
print(f"Tier-1 eligible for bulk approve: 5")
print(f"Tier-1 with manual final_account (skip): 2")
print(f"Unmapped/supplier/anti-pattern Pending: 3")
print(f"Excluded P&L: 1")
