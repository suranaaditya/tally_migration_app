// Copyright (c) 2026, Dux Digitech and contributors
// For license information, please see license.txt

frappe.ui.form.on("Tally Migration Session", {
    refresh(frm) {
        // Review Decisions — routes to the custom master-detail review page.
        // Per docs/week4_review_ui_design.md §1.1 entry point.
        //
        // Page name is "md-review" (9 chars) due to Frappe Page controller's
        // 20-char runtime autoname truncation; see mapper_design_notes.md §5.
        if (!frm.is_new() && frm.doc.name) {
            frm.add_custom_button("Review Decisions", () => {
                frappe.set_route("md-review", frm.doc.name);
            });
        }
    },
});
