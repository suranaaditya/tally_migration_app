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

        // Header: proposed_supplier_name is the primary label (what the
        // Supplier will be called post-approval). tally_vendor_name is
        // the secondary subtitle (where the SCR came from). Updated
        // 2026-04-22 per reviewer feedback — reviewer thinks in terms
        // of the target Supplier, not the Tally source ledger.
        const headline = r.proposed_supplier_name || r.tally_vendor_name || "";
        const tally_sub = r.tally_vendor_id
            ? `${r.tally_vendor_name || ""} [tally_id=${frappe.utils.escape_html(r.tally_vendor_id)}]`
            : (r.tally_vendor_name || "");

        return `
            <div class="scr-row" data-row-name="${frappe.utils.escape_html(r.row_name)}">
                <div class="scr-row-head">
                    <input type="checkbox" class="scr-row-select" aria-label="${__("Select for bulk action")}" />
                    ${status_badge}
                    <div class="scr-row-vendor">
                        <div class="scr-row-vendor-name">${frappe.utils.escape_html(headline)}</div>
                        <div class="scr-row-tally-id">${__("from Tally")}: ${frappe.utils.escape_html(tally_sub)}</div>
                    </div>
                    <div class="scr-row-balance">${balance}</div>
                </div>
                <div class="scr-row-body">
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

    // Top bar — select-all + bulk actions. Updated 2026-04-22 for bulk
    // reviewer workflow (per CACSPU's ~18 SCR scale, per-row approval
    // becomes tedious).
    const bulk_bar = `
        <div class="scr-bulk-bar">
            <label class="scr-select-all-label">
                <input type="checkbox" class="scr-select-all" />
                <span>${__("Select all")} (${rows.length})</span>
            </label>
            <div class="scr-bulk-actions">
                <button class="btn btn-default btn-sm scr-bulk-reject" type="button" disabled>
                    ${__("Reject Selected")} (<span class="scr-bulk-count">0</span>)
                </button>
                <button class="btn btn-primary btn-sm scr-bulk-approve" type="button" disabled>
                    ${__("Approve Selected")} (<span class="scr-bulk-count">0</span>)
                </button>
            </div>
        </div>
    `;

    return `
        <div class="scr-processing-panel">
            ${bulk_bar}
            <div class="scr-row-list">${rows.map(rowHTML).join("")}</div>
        </div>
    `;
}

function attachSCRActions($body, frm, dialog) {
    // --- Per-row actions ---
    $body.find(".scr-btn-approve").on("click", (e) => {
        const $row = $(e.currentTarget).closest(".scr-row");
        const row_name = $row.data("row-name");
        const supplier = $row.find(".scr-row-vendor-name").text().trim();
        frappe.confirm(
            __("Create Supplier {0}? Source decision(s) will be Approved.", [supplier]),
            () => doSCRAction(frm, row_name, "approve_scr", "Approved", dialog),
        );
    });
    $body.find(".scr-btn-reject").on("click", (e) => {
        const $row = $(e.currentTarget).closest(".scr-row");
        const row_name = $row.data("row-name");
        frappe.confirm(
            __(
                "Reject this SCR? Source decision(s) will be marked Rejected. " +
                "Generators will silent-skip rejected rows (no refusal, no contribution)."
            ),
            () => doSCRAction(frm, row_name, "reject_scr", "Rejected", dialog),
        );
    });

    // --- Checkbox + bulk-action wiring ---
    const $select_all = $body.find(".scr-select-all");
    const $row_checks = $body.find(".scr-row-select");
    const $bulk_approve = $body.find(".scr-bulk-approve");
    const $bulk_reject = $body.find(".scr-bulk-reject");

    function refresh_bulk_bar() {
        const selected_count = $body.find(".scr-row-select:checked").length;
        $body.find(".scr-bulk-count").text(selected_count);
        $bulk_approve.prop("disabled", selected_count === 0);
        $bulk_reject.prop("disabled", selected_count === 0);
        // Sync "select all" indeterminate state
        const total = $row_checks.length;
        if (selected_count === 0) {
            $select_all.prop("checked", false).prop("indeterminate", false);
        } else if (selected_count === total) {
            $select_all.prop("checked", true).prop("indeterminate", false);
        } else {
            $select_all.prop("checked", false).prop("indeterminate", true);
        }
    }

    $row_checks.on("change", refresh_bulk_bar);

    $select_all.on("change", () => {
        const checked = $select_all.prop("checked");
        $row_checks.prop("checked", checked);
        refresh_bulk_bar();
    });

    function collect_selected_row_names() {
        return $body.find(".scr-row-select:checked")
            .map((_, cb) => $(cb).closest(".scr-row").data("row-name"))
            .get();
    }

    $bulk_approve.on("click", () => {
        const names = collect_selected_row_names();
        if (!names.length) return;
        frappe.confirm(
            __(
                "Approve {0} SCR(s)? A Supplier will be created for each and source decision(s) will be Approved.",
                [names.length],
            ),
            () => doBulkSCRAction(frm, names, "bulk_approve_scrs", dialog),
        );
    });

    $bulk_reject.on("click", () => {
        const names = collect_selected_row_names();
        if (!names.length) return;
        frappe.confirm(
            __(
                "Reject {0} SCR(s)? Source decision(s) will be marked Rejected. " +
                "Generators will silent-skip rejected rows.",
                [names.length],
            ),
            () => doBulkSCRAction(frm, names, "bulk_reject_scrs", dialog),
        );
    });
}


function doBulkSCRAction(frm, scr_row_names, method, dialog) {
    frappe.call({
        method: `rgi_migration.rgi_migration.page.md_review.md_review.${method}`,
        args: { session_name: frm.doc.name, scr_row_names: JSON.stringify(scr_row_names) },
        freeze: true,
        freeze_message: __("Processing {0} SCR(s)...", [scr_row_names.length]),
        callback: (r) => {
            const resp = (r && r.message) || {};
            const ok = (resp.ok || []).length;
            const failed = (resp.failed || []).length;
            if (failed === 0) {
                frappe.show_alert({
                    message: method === "bulk_approve_scrs"
                        ? __("Approved {0} SCR(s).", [ok])
                        : __("Rejected {0} SCR(s).", [ok]),
                    indicator: "green",
                }, 5);
            } else {
                // Show both counts + detail in a msgprint for visibility
                const detail = (resp.failed || [])
                    .map((f) => `<li><strong>${frappe.utils.escape_html(f.scr || "")}</strong>: ${frappe.utils.escape_html(f.error || "")}</li>`)
                    .join("");
                frappe.msgprint({
                    title: __("Bulk action partially completed"),
                    message: __("Succeeded: {0}. Failed: {1}.", [ok, failed]) +
                        `<ul style="margin-top:8px">${detail}</ul>`,
                    indicator: "orange",
                });
            }
            dialog.hide();
            frm.reload_doc().then(() => {
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
