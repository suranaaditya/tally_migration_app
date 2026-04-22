// Copyright (c) 2026, Dux Digitech and contributors
// For license information, please see license.txt

frappe.ui.form.on("Tally Migration Session", {
    refresh(frm) {
        if (frm.is_new() || !frm.doc.name) {
            return;
        }

        // Review Decisions — navigates to the custom master-detail review page.
        // Per docs/week4_review_ui_design.md §1.1 entry point.
        // Page name is "md-review" (9 chars) due to Frappe Page controller's
        // 20-char runtime autoname truncation; see mapper_design_notes.md §5.
        frm.add_custom_button("Review Decisions", () => {
            frappe.set_route("md-review", frm.doc.name);
        });

        // Run Mapper — visible when the session is in a state that permits a
        // fresh parse-and-map (Draft or Failed) AND a source file is attached.
        // See mapper_design_notes §9.1 for the persistence architecture and
        // tally_migration_session.py:run_mapper() for the guards.
        const status = frm.doc.status;
        const can_run =
            (status === "Draft" || status === "Failed") &&
            (frm.doc.source_file || frm.doc.source_file_server_path);
        if (can_run) {
            frm.add_custom_button("Run Mapper", () => {
                frappe.call({
                    method:
                        "rgi_migration.rgi_migration.doctype.tally_migration_session" +
                        ".tally_migration_session.run_mapper",
                    args: { session_name: frm.doc.name },
                    freeze: true,
                    freeze_message: __("Parsing source + running Tier-1 mapper..."),
                    callback: (r) => {
                        if (r.message && r.message.status === "ok") {
                            frappe.show_alert({
                                message: __(
                                    "Mapped {0} decisions — status now Reviewing.",
                                    [r.message.decision_count]
                                ),
                                indicator: "green",
                            }, 7);
                            frm.reload_doc();
                        }
                    },
                });
            }).addClass("btn-primary");
        }

        // Reset Parse — visible when the session has moved past Draft (and
        // is not Submitted). Drops all Mapping Decisions for the session
        // and resets snapshot fields. error_log is preserved for audit.
        const reset_allowed_from = new Set([
            "Parsing", "Parsed", "Mapping", "Reviewing",
            "Generating", "Generated", "Failed", "Cancelled",
        ]);
        if (reset_allowed_from.has(status)) {
            frm.add_custom_button("Reset Parse", () => {
                frappe.confirm(
                    __(
                        "This will delete every Mapping Decision for this session " +
                        "and reset status to Draft. Reviewer work on these " +
                        "decisions will be lost. Continue?"
                    ),
                    () => {
                        frappe.call({
                            method:
                                "rgi_migration.rgi_migration.doctype.tally_migration_session" +
                                ".tally_migration_session.reset_parse",
                            args: { session_name: frm.doc.name },
                            freeze: true,
                            freeze_message: __("Clearing decisions..."),
                            callback: (r) => {
                                if (r.message && r.message.status === "ok") {
                                    frappe.show_alert({
                                        message: __(
                                            "Deleted {0} decisions — status now Draft.",
                                            [r.message.deleted_count]
                                        ),
                                        indicator: "orange",
                                    }, 7);
                                    frm.reload_doc();
                                }
                            },
                        });
                    }
                );
            });
        }
    },
});
