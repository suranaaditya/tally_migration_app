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

        // Process Supplier Creation Requests — panel dialog listing all
        // Pending / Failed SCRs on this session with per-row Approve /
        // Reject actions. Per Item 3 Commit 3 AMB Q1 (panel-dialog
        // pattern). Hidden when the child table has no open rows.
        const scr_count = (frm.doc.supplier_creation_requests || []).filter(
            (r) => r.status === "Pending" || r.status === "Failed"
        ).length;
        if (scr_count > 0) {
            frm.add_custom_button(
                __("Process Supplier Creation Requests ({0})", [scr_count]),
                () => openSCRProcessingDialog(frm),
            );
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


// ===========================================================================
// SCR processing dialog (Item 3 Commit 3)
// ===========================================================================
//
// Opened by the "Process Supplier Creation Requests" button on the
// Session form. Lists Pending + Failed SCRs with per-row Approve /
// Reject. Approve calls approve_scr (creates Supplier via Frappe ORM,
// writes back to source decisions); Reject calls reject_scr (marks
// decisions Rejected; generators silent-skip).

function openSCRProcessingDialog(frm) {
    frappe.call({
        method:
            "rgi_migration.rgi_migration.page.md_review.md_review.list_pending_scrs",
        args: { session_name: frm.doc.name },
        callback: (r) => {
            const rows = (r && r.message) || [];
            if (!rows.length) {
                frappe.show_alert({
                    message: __("No pending or failed SCRs on this session."),
                    indicator: "blue",
                }, 3);
                return;
            }
            renderSCRDialog(frm, rows);
        },
    });
}

function renderSCRDialog(frm, rows) {
    const dialog = new frappe.ui.Dialog({
        title: __("Supplier Creation Requests — {0}", [rows.length]),
        size: "large",
        fields: [
            {
                fieldtype: "HTML",
                fieldname: "scr_grid",
            },
        ],
    });

    const $body = dialog.get_field("scr_grid").$wrapper;
    $body.html(renderSCRGridHTML(rows));
    attachSCRActions($body, frm, dialog);

    dialog.show();
}

function renderSCRGridHTML(rows) {
    const rowHTML = (r) => {
        const balance = frappe.format(
            r.detected_balance || 0,
            { fieldtype: "Currency" }
        );
        const status_badge = r.status === "Failed"
            ? `<span class="scr-status-badge scr-failed">${__("Failed")}</span>`
            : `<span class="scr-status-badge scr-pending">${__("Pending")}</span>`;
        const notes = (r.reviewer_notes || "").slice(0, 140);
        const error_preview = r.error_log
            ? `<div class="scr-error-preview"><strong>${__("Error")}:</strong> ${frappe.utils.escape_html(
                (r.error_log || "").split("\n").slice(-4).join("\n")
              )}</div>`
            : "";

        return `
            <div class="scr-row" data-row-name="${frappe.utils.escape_html(r.row_name)}">
                <div class="scr-row-head">
                    ${status_badge}
                    <div class="scr-row-vendor">
                        <div class="scr-row-vendor-name">${frappe.utils.escape_html(r.tally_vendor_name || "")}</div>
                        <div class="scr-row-tally-id">${r.tally_vendor_id ? `tally_id=${frappe.utils.escape_html(r.tally_vendor_id)}` : ""}</div>
                    </div>
                    <div class="scr-row-balance">${balance}</div>
                </div>
                <div class="scr-row-body">
                    <div class="scr-kv">
                        <span class="scr-k">${__("Supplier")}:</span>
                        <span class="scr-v">${frappe.utils.escape_html(r.proposed_supplier_name || "")}</span>
                    </div>
                    <div class="scr-kv">
                        <span class="scr-k">${__("Group")}:</span>
                        <span class="scr-v">${frappe.utils.escape_html(r.proposed_supplier_group || "(none)")}</span>
                    </div>
                    ${notes ? `
                        <div class="scr-kv">
                            <span class="scr-k">${__("Notes")}:</span>
                            <span class="scr-v scr-notes">${frappe.utils.escape_html(notes)}</span>
                        </div>
                    ` : ""}
                    ${error_preview}
                </div>
                <div class="scr-row-actions">
                    <button class="btn btn-default btn-sm scr-btn-reject" type="button">${__("Reject")}</button>
                    <button class="btn btn-primary btn-sm scr-btn-approve" type="button">
                        ${r.status === "Failed" ? __("Retry Approve") : __("Approve")}
                    </button>
                </div>
            </div>
        `;
    };

    return `<div class="scr-processing-panel">${rows.map(rowHTML).join("")}</div>`;
}

function attachSCRActions($body, frm, dialog) {
    $body.find(".scr-btn-approve").on("click", (e) => {
        const $row = $(e.currentTarget).closest(".scr-row");
        const row_name = $row.data("row-name");
        const supplier = $row.find(".scr-v").first().text().trim();
        frappe.confirm(
            __("Create Supplier {0}? Source decision(s) will be Approved.", [supplier]),
            () => doSCRAction(frm, row_name, "approve_scr", "Approved", dialog),
        );
    });
    $body.find(".scr-btn-reject").on("click", (e) => {
        const $row = $(e.currentTarget).closest(".scr-row");
        const row_name = $row.data("row-name");
        const supplier = $row.find(".scr-v").first().text().trim();
        frappe.confirm(
            __(
                "Reject this SCR? Source decision(s) will be marked Rejected. " +
                "Generators will silent-skip rejected rows (no refusal, no contribution)."
            ),
            () => doSCRAction(frm, row_name, "reject_scr", "Rejected", dialog),
        );
    });
}

function doSCRAction(frm, row_name, method, label, dialog) {
    frappe.call({
        method: `rgi_migration.rgi_migration.page.md_review.md_review.${method}`,
        args: { session_name: frm.doc.name, scr_row_name: row_name },
        freeze: true,
        freeze_message: __("Processing..."),
        callback: (r) => {
            const resp = (r && r.message) || {};
            if (resp.status === "ok") {
                let msg;
                if (method === "approve_scr") {
                    msg = __(
                        "Supplier {0} created, {1} decision(s) approved.",
                        [resp.created_supplier, (resp.updated_decisions || []).length]
                    );
                } else {
                    msg = __(
                        "{0} decision(s) marked Rejected.",
                        [(resp.updated_decisions || []).length]
                    );
                }
                frappe.show_alert({ message: msg, indicator: "green" }, 5);
            } else if (resp.status === "failed") {
                frappe.msgprint({
                    title: __("Supplier creation failed"),
                    message: __("SCR marked Failed. Error: {0}", [resp.error || "unknown"]),
                    indicator: "red",
                });
            }
            dialog.hide();
            frm.reload_doc().then(() => {
                // Re-open dialog if more SCRs remain — reviewer stays in flow
                const remaining = (frm.doc.supplier_creation_requests || []).filter(
                    (r) => r.status === "Pending" || r.status === "Failed"
                ).length;
                if (remaining > 0) {
                    openSCRProcessingDialog(frm);
                }
            });
        },
    });
}
