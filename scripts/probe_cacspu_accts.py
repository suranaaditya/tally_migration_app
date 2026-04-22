"""List CACSPU leaf accounts via correct company name."""
import frappe

accts = frappe.get_all(
    "Account",
    filters={"company": "GHR CACS Pune", "is_group": 0, "disabled": 0},
    fields=["name"],
    limit=20,
    order_by="name asc",
)
for a in accts:
    print("ACCT:", a.name)
print(f"TOTAL: {len(accts)}")
