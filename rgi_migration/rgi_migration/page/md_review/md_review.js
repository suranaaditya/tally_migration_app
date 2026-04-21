/* ============================================================================
 * Mapping Decision Review — master-detail review page.
 *
 * Route: /app/md-review/<session-name>[#<decision-name>]
 *
 * Scope progression per docs/week4_review_ui_design.md:
 *   Commit 2: split-pane shell only (shipped).
 *   Commit 3: FilterBar + MasterPane below — this file.
 *   Commit 4: DetailPane (replaces placeholder).
 *   Commit 5: action handlers, save_decision, keyboard shortcuts for
 *             Approve/Reject/Defer/RequestCreation, Undo.
 *
 * Classes live in this file until the 2,500-line / Item-6-ranking
 * bundle threshold per docs/mapper_design_notes.md §10 is reached.
 * ============================================================================
 */


// ============================================================================
// CLASS: FilterBar
// ============================================================================

class FilterBar {
    // Filter preset: "pending" applies the review-action-is-blocking filter
    // per §1.3; "all" drops the filter for diagnostic inspection.
    static PENDING_REVIEW_ACTIONS = [
        "Pending",
        "Pending Account Creation",
        "Pending Group Account Resolution",
        "Pending Supplier Creation",
    ];

    static TIER_OPTIONS = [
        "tier1_exact",
        "tier1_rule",
        "tier1_pattern",
        "tier1_supplier_fuzzy",
        "tier2_fuzzy",
        "tier3_claude",
        "unmapped",
        "pending_account_creation",
        "pending_supplier_creation",
        "group_refused",
        "anti_pattern_blocked",
        "excluded_pnl",
        "excluded_zero_balance",
    ];

    static ROOT_TYPE_OPTIONS = ["Asset", "Liability", "Equity", "Income", "Expense"];

    constructor(container_el) {
        this.container = $(container_el);
        this.state = {
            preset: "pending",            // "pending" | "all"
            tiers: [],                    // empty = no filter, else subset of TIER_OPTIONS
            root_types: [],               // empty = no filter, else subset of ROOT_TYPE_OPTIONS
            search: "",                   // free-text, matches tally_name like "%text%"
        };
        this._change_callback = null;
        this._search_debounce_timer = null;
        this._render();
    }

    _render() {
        this.container.html(`
            <div class="filter-bar">
                <div class="filter-preset-group" role="tablist">
                    <button class="filter-preset-pill active" data-preset="pending">Pending</button>
                    <button class="filter-preset-pill" data-preset="all">All decisions</button>
                </div>
                <div class="filter-field filter-tier"></div>
                <div class="filter-field filter-root-type"></div>
                <div class="filter-field filter-search">
                    <input type="text" class="form-control filter-search-input"
                           placeholder="Search Tally name..." />
                </div>
            </div>
        `);

        this._wire_preset_toggle();
        this._wire_tier_select();
        this._wire_root_type_select();
        this._wire_search_input();
    }

    _wire_preset_toggle() {
        this.container.on("click", ".filter-preset-pill", (e) => {
            const preset = $(e.currentTarget).data("preset");
            if (preset === this.state.preset) return;
            this.container.find(".filter-preset-pill").removeClass("active");
            $(e.currentTarget).addClass("active");
            this.state.preset = preset;
            this._fire_change();
        });
    }

    _wire_tier_select() {
        // MultiSelectPills: lightweight multi-select via frappe.ui.FieldGroup.
        // Using MultiCheck would also work; Select is the spec per §1.3.
        const tier_container = this.container.find(".filter-tier")[0];
        this._tier_field = new frappe.ui.form.ControlMultiCheck({
            parent: tier_container,
            df: {
                label: "Tier",
                fieldname: "tier_filter",
                fieldtype: "MultiCheck",
                options: FilterBar.TIER_OPTIONS.map((v) => ({
                    label: v,
                    value: v,
                    checked: 0,
                })),
                on_change: () => {
                    this.state.tiers = this._tier_field.get_checked_options();
                    this._fire_change();
                },
            },
            render_input: true,
        });
    }

    _wire_root_type_select() {
        const rt_container = this.container.find(".filter-root-type")[0];
        this._root_type_field = new frappe.ui.form.ControlMultiCheck({
            parent: rt_container,
            df: {
                label: "Root Type",
                fieldname: "root_type_filter",
                fieldtype: "MultiCheck",
                options: FilterBar.ROOT_TYPE_OPTIONS.map((v) => ({
                    label: v,
                    value: v,
                    checked: 0,
                })),
                on_change: () => {
                    this.state.root_types = this._root_type_field.get_checked_options();
                    this._fire_change();
                },
            },
            render_input: true,
        });
    }

    _wire_search_input() {
        this.container.on("input", ".filter-search-input", (e) => {
            const value = $(e.currentTarget).val();
            if (this._search_debounce_timer) clearTimeout(this._search_debounce_timer);
            this._search_debounce_timer = setTimeout(() => {
                this.state.search = (value || "").trim();
                this._fire_change();
            }, 250);
        });
    }

    _fire_change() {
        if (this._change_callback) this._change_callback(this.state);
    }

    onChange(callback) {
        this._change_callback = callback;
    }

    // Build the filter dict for the backend call per the §1.3 spec.
    getFilters() {
        const filters = {};

        if (this.state.preset === "pending") {
            filters.review_action = ["in", FilterBar.PENDING_REVIEW_ACTIONS];
        }
        // "all" preset → no review_action filter

        if (this.state.tiers && this.state.tiers.length > 0) {
            filters.tier = ["in", this.state.tiers];
        }
        if (this.state.root_types && this.state.root_types.length > 0) {
            filters.tally_root_type = ["in", this.state.root_types];
        }
        if (this.state.search) {
            filters.tally_name = ["like", "%" + this.state.search + "%"];
        }

        return filters;
    }
}


// ============================================================================
// CLASS: MasterPane
// ============================================================================

class MasterPane {
    // Indicator dot class lookup per §1.11 palette.
    // Values map to CSS classes .indicator-dot.{state} defined in md_review.css.
    static REVIEW_ACTION_STATE = {
        "Pending": "pending",
        "Pending Account Creation": "blocking",
        "Pending Supplier Creation": "blocking",
        "Pending Group Account Resolution": "blocking",
        "Approved": "done",
        "Manual Override": "done",
        "Rejected": "muted",
        "Deferred": "muted",
        "Skipped": "muted",
        "Excluded (P&L)": "muted",
    };

    // Tier chip color class lookup per §1.11.
    static TIER_CHIP_STATE = {
        "tier1_exact": "resolved",
        "tier1_rule": "resolved",
        "tier1_pattern": "resolved",
        "tier1_supplier_fuzzy": "resolved",
        "tier2_fuzzy": "fuzzy",
        "tier3_claude": "fuzzy",
        "unmapped": "pending",
        "pending_account_creation": "blocking",
        "pending_supplier_creation": "blocking",
        "group_refused": "blocking",
        "anti_pattern_blocked": "blocking",
        "excluded_pnl": "muted",
        "excluded_zero_balance": "muted",
    };

    static PAGE_LENGTH = 50;

    constructor(container_el, session_name, controller) {
        this.container = $(container_el);
        this.session_name = session_name;
        this.controller = controller;  // back-reference for URL-hash sync + future detail-pane coupling

        this.current_decisions = [];
        this.selected_index = null;
        this.total_count = 0;
        this.filtered_count = 0;
        this.pagination_start = 0;
        this.current_filters = {};

        this._render_shell();
    }

    _render_shell() {
        // Single-table structure per browser-verification feedback on initial
        // Phase-C screenshot: two-table layout (separate <thead> and <tbody>
        // tables) broke column alignment because each table sized its own
        // columns independently. Single table + sticky <thead> keeps them
        // synced and also gives us scroll-anchored headers for free.
        this.container.html(`
            <div class="master-pane">
                <div class="master-pane-body">
                    <table class="master-table">
                        <thead>
                            <tr>
                                <th class="col-indicator"></th>
                                <th class="col-tally-name">Tally Name</th>
                                <th class="col-parent-chain">Parent Chain</th>
                                <th class="col-net-amount">Net Amount</th>
                                <th class="col-side">Side</th>
                                <th class="col-tier">Tier</th>
                                <th class="col-proposed-account">Proposed Account</th>
                            </tr>
                        </thead>
                        <tbody class="master-rows"></tbody>
                    </table>
                </div>
                <div class="master-pane-footer">
                    <span class="master-count-label"></span>
                    <button class="btn btn-sm btn-default master-load-more">Load more</button>
                </div>
            </div>
        `);

        this._wire_row_clicks();
        this._wire_load_more();
    }

    _wire_row_clicks() {
        this.container.on("click", ".master-row", (e) => {
            const idx = $(e.currentTarget).data("index");
            if (idx === undefined || idx === null) return;
            this.selectRow(idx);
        });
    }

    _wire_load_more() {
        this.container.on("click", ".master-load-more", () => {
            this.pagination_start += MasterPane.PAGE_LENGTH;
            this.loadDecisions({ append: true });
        });
    }

    setFilters(filter_dict) {
        this.current_filters = filter_dict || {};
    }

    async loadDecisions({ append = false } = {}) {
        if (!this.session_name) {
            // No session → nothing to load. Shell + empty state only.
            this._render_rows([]);
            this._update_footer();
            return;
        }

        try {
            const r = await frappe.call({
                method: "rgi_migration.rgi_migration.page.md_review.md_review.get_session_decisions",
                args: {
                    session_name: this.session_name,
                    filters: this.current_filters,
                    start: this.pagination_start,
                    page_length: MasterPane.PAGE_LENGTH,
                },
            });

            const result = r.message || { decisions: [], total_count: 0, filtered_count: 0 };

            if (append) {
                this.current_decisions = this.current_decisions.concat(result.decisions);
            } else {
                this.current_decisions = result.decisions;
                this.selected_index = this.current_decisions.length > 0 ? 0 : null;
            }
            this.total_count = result.total_count;
            this.filtered_count = result.filtered_count;

            this._render_rows(this.current_decisions);
            this._update_footer();

            if (this.selected_index !== null) {
                this._apply_selection_highlight();
                // URL hash sync happens via controller on explicit selectRow;
                // initial-load hash restoration is handled by the controller
                // after load completes.
            }
        } catch (err) {
            console.error("md-review: get_session_decisions failed", err);
            frappe.show_alert({
                message: "Failed to load decisions — see console.",
                indicator: "red",
            }, 7);
        }
    }

    _render_rows(decisions) {
        const tbody = this.container.find(".master-rows");

        if (!decisions || decisions.length === 0) {
            tbody.html(`
                <tr class="master-empty-row">
                    <td colspan="7">
                        <div class="master-empty-state">
                            No decisions in this session match the current filters.
                        </div>
                    </td>
                </tr>
            `);
            return;
        }

        const rows_html = decisions.map((d, idx) => this._render_row(d, idx)).join("");
        tbody.html(rows_html);
    }

    _render_row(decision, idx) {
        const indicator_state = MasterPane.REVIEW_ACTION_STATE[decision.review_action] || "muted";
        const tier_state = MasterPane.TIER_CHIP_STATE[decision.tier] || "muted";
        const net_amount = MasterPane._format_amount(decision.net_amount);
        const net_side = decision.net_side || "—";
        const tally_name = decision.tally_name || "";
        const proposed_account = decision.proposed_account || "";
        const tier_label = decision.tier || "";

        // Prepend root type to parent chain for quick classification context
        // per reviewer feedback during Commit 3 browser verification. The
        // combined display anchors "this is a Liability" / "this is an
        // Asset" as the first signal, then the Tally group hierarchy
        // below it. Left-ellipsis keeps the innermost (rightmost) group
        // visible when the chain doesn't fit.
        const root_type = decision.tally_root_type || "";
        const chain = decision.tally_parent_chain || "";
        const parent_chain_display = root_type && chain
            ? root_type + " > " + chain
            : (root_type || chain);

        return `
            <tr class="master-row" data-index="${idx}" data-name="${frappe.utils.escape_html(decision.name)}">
                <td class="col-indicator">
                    <span class="indicator-dot ${indicator_state}"
                          title="${frappe.utils.escape_html(decision.review_action || "")}"></span>
                </td>
                <td class="col-tally-name" title="${frappe.utils.escape_html(tally_name)}">
                    ${frappe.utils.escape_html(tally_name)}
                </td>
                <td class="col-parent-chain" title="${frappe.utils.escape_html(parent_chain_display)}">
                    <span class="parent-chain-ellipsis"><bdo dir="ltr">${frappe.utils.escape_html(parent_chain_display)}</bdo></span>
                </td>
                <td class="col-net-amount">${net_amount}</td>
                <td class="col-side">${frappe.utils.escape_html(net_side)}</td>
                <td class="col-tier">
                    <span class="tier-chip ${tier_state}">${frappe.utils.escape_html(tier_label)}</span>
                </td>
                <td class="col-proposed-account" title="${frappe.utils.escape_html(proposed_account)}">
                    ${frappe.utils.escape_html(proposed_account)}
                </td>
            </tr>
        `;
    }

    _apply_selection_highlight() {
        this.container.find(".master-row").removeClass("selected");
        if (this.selected_index === null) return;
        const $selected = this.container.find(
            `.master-row[data-index="${this.selected_index}"]`
        );
        $selected.addClass("selected");
        // Scroll into view so keyboard nav works when the selected row is
        // offscreen. ``block: "nearest"`` avoids unnecessary jumping when
        // the row is already visible.
        const el = $selected.get(0);
        if (el && el.scrollIntoView) {
            el.scrollIntoView({ block: "nearest" });
        }
    }

    _update_footer() {
        const shown = this.current_decisions.length;
        const total = this.filtered_count;
        this.container
            .find(".master-count-label")
            .text(`Showing ${shown} of ${total}`);

        const more_available = shown < total;
        this.container
            .find(".master-load-more")
            .toggle(more_available);
    }

    selectRow(index) {
        if (index < 0 || index >= this.current_decisions.length) return;
        this.selected_index = index;
        this._apply_selection_highlight();

        const decision = this.current_decisions[index];
        if (decision && this.controller && this.controller.on_selection_change) {
            this.controller.on_selection_change(decision);
        }
    }

    getSelectedDecisionName() {
        if (this.selected_index === null) return null;
        const decision = this.current_decisions[this.selected_index];
        return decision ? decision.name : null;
    }

    selectByName(decision_name) {
        if (!decision_name) return false;
        const idx = this.current_decisions.findIndex((d) => d.name === decision_name);
        if (idx === -1) {
            console.log(`md-review: decision '${decision_name}' not in current result set`);
            return false;
        }
        this.selectRow(idx);
        return true;
    }

    static _format_amount(amount) {
        if (amount === null || amount === undefined) return "";
        try {
            // Indian-style grouping matches the existing ERPNext formatting
            // elsewhere on the bench.
            return amount.toLocaleString("en-IN", {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2,
            });
        } catch (e) {
            return String(amount);
        }
    }
}


// ============================================================================
// CLASS: AssignmentWidget
// ============================================================================

class AssignmentWidget {
    // Stable-hash avatar palette. Same user always gets same color across
    // sessions / browsers; deterministic, not random. Per §1.4 Section 5
    // refinement — reviewers quickly recognise "oh that's Priya's red"
    // without a photo lookup.
    static AVATAR_PALETTE = [
        "#4a90e2", "#7ed321", "#f5a623", "#bd10e0",
        "#50e3c2", "#e94e77", "#b8e986",
    ];

    constructor(opts) {
        this.parent = $(opts.parent);
        this.decision_name = opts.decision_name;
        this.assignments = opts.assignments || [];
        this.on_change = opts.on_change;
    }

    render() {
        const assignee = this._current_assignee();
        if (assignee) {
            const color = AssignmentWidget._avatar_color(assignee.owner);
            const initial = (assignee.owner[0] || "?").toUpperCase();
            this.parent.html(`
                <div class="assignment-chip">
                    <span class="assignment-avatar" style="background: ${color};">${frappe.utils.escape_html(initial)}</span>
                    <span class="assignment-name" title="${frappe.utils.escape_html(assignee.description || "")}">${frappe.utils.escape_html(assignee.owner)}</span>
                    <button class="assignment-remove" title="Remove assignment">×</button>
                </div>
                <button class="btn btn-default btn-xs assignment-reassign">Reassign...</button>
            `);
        } else {
            this.parent.html(`
                <div class="assignment-empty">Unassigned</div>
                <button class="btn btn-default btn-xs assignment-assign">Assign to...</button>
            `);
        }
        this._wire();
    }

    _wire() {
        this.parent.on("click", ".assignment-assign, .assignment-reassign", () => this._open_dialog());
        this.parent.on("click", ".assignment-remove", () => this._remove_assignment());
    }

    _current_assignee() {
        // get_assignments filters out Cancelled/Closed already, but be
        // defensive in case the shape ever changes. Take the first (rare
        // multi-assignment case: still single chip in v1 — first wins).
        return (this.assignments || []).find(
            (a) => a && a.owner && a.status !== "Cancelled" && a.status !== "Closed"
        );
    }

    _open_dialog() {
        const dialog = new frappe.ui.Dialog({
            title: __("Assign Decision"),
            fields: [
                {
                    fieldname: "assign_to",
                    fieldtype: "Link",
                    options: "User",
                    label: __("Assign To"),
                    reqd: 1,
                },
                {
                    fieldname: "description",
                    fieldtype: "Small Text",
                    label: __("Description"),
                },
            ],
            primary_action_label: __("Assign"),
            primary_action: (values) => {
                frappe.call({
                    method: "frappe.desk.form.assign_to.add",
                    args: {
                        assign_to: [values.assign_to],
                        doctype: "Mapping Decision",
                        name: this.decision_name,
                        description: values.description || "",
                    },
                }).then(() => {
                    dialog.hide();
                    frappe.show_alert({
                        message: __("Assigned to {0}", [values.assign_to]),
                        indicator: "green",
                    }, 3);
                    if (this.on_change) this.on_change();
                }).catch((err) => {
                    console.error("md-review: assign_to.add failed", err);
                    frappe.show_alert({
                        message: __("Failed to assign — see console."),
                        indicator: "red",
                    }, 7);
                });
            },
        });
        dialog.show();
    }

    _remove_assignment() {
        const assignee = this._current_assignee();
        if (!assignee) return;

        frappe.confirm(
            __("Remove assignment from {0}?", [assignee.owner]),
            () => {
                frappe.call({
                    method: "frappe.desk.form.assign_to.remove",
                    args: {
                        doctype: "Mapping Decision",
                        name: this.decision_name,
                        assign_to: assignee.owner,
                    },
                }).then(() => {
                    frappe.show_alert({
                        message: __("Unassigned"),
                        indicator: "blue",
                    }, 3);
                    if (this.on_change) this.on_change();
                }).catch((err) => {
                    console.error("md-review: assign_to.remove failed", err);
                    frappe.show_alert({
                        message: __("Failed to remove assignment — see console."),
                        indicator: "red",
                    }, 7);
                });
            }
        );
    }

    static _avatar_color(email) {
        if (!email) return AssignmentWidget.AVATAR_PALETTE[0];
        const hash = email.split("").reduce((sum, c) => sum + c.charCodeAt(0), 0);
        return AssignmentWidget.AVATAR_PALETTE[hash % AssignmentWidget.AVATAR_PALETTE.length];
    }
}


// ============================================================================
// CLASS: DetailPane
// ============================================================================

class DetailPane {
    // Root-type CSS class map for chip styling per §1.11.
    static ROOT_TYPE_CLASS = {
        "Asset": "asset",
        "Liability": "liability",
        "Equity": "equity",
        "Income": "income",
        "Expense": "expense",
    };

    constructor(container_el) {
        this.container = $(container_el);
        this.current_decision_name = null;
        this.current_decision = null;
        this.current_docinfo = null;
        this.session_company_abbr = null;
        this.assignment_widget = null;
        this._render_empty();
    }

    _render_empty() {
        this.container.html(`
            <div class="pane-placeholder">Select a decision to view details</div>
        `);
    }

    clear() {
        this.current_decision_name = null;
        this.current_decision = null;
        this.current_docinfo = null;
        this.session_company_abbr = null;
        this.assignment_widget = null;
        this._render_empty();
    }

    async loadDecision(decision_name) {
        if (!decision_name) {
            this.clear();
            return;
        }
        // Idempotent — avoids double-fetch when selection callback + initial
        // hash-restore both trigger the same decision.
        if (decision_name === this.current_decision_name) return;

        try {
            const r = await frappe.call({
                method: "rgi_migration.rgi_migration.page.md_review.md_review.get_decision_detail",
                args: { decision_name: decision_name },
            });
            const msg = r.message || {};
            this.current_decision_name = decision_name;
            this.current_decision = msg.decision || {};
            this.current_docinfo = msg.docinfo || {};
            this.session_company_abbr = msg.session_company_abbr || "";
            this._render();
        } catch (err) {
            console.error("md-review: get_decision_detail failed", err);
            frappe.show_alert({
                message: __("Failed to load decision detail — see console."),
                indicator: "red",
            }, 7);
        }
    }

    async refreshAssignments() {
        // Assignment widget invokes on_change after add/remove. Re-fetch the
        // full detail so docinfo.assignments is fresh. Cheap (one call).
        const name = this.current_decision_name;
        if (!name) return;
        this.current_decision_name = null;  // force re-fetch despite idempotency guard
        await this.loadDecision(name);
    }

    _render() {
        const d = this.current_decision;
        if (!d || !d.name) {
            this._render_empty();
            return;
        }

        this.container.html(`
            <div class="detail-pane-content">
                ${this._render_tally_context(d)}
                ${this._render_mapper_resolution(d)}
                ${this._render_tier2_placeholder()}
                ${this._render_assignment_shell()}
                ${this._render_audit(d)}
            </div>
        `);

        // AssignmentWidget mounts into the shell after HTML is in DOM
        this._mount_assignment();
    }

    // --- Section renderers ---------------------------------------------------

    _render_tally_context(d) {
        const root_type = d.tally_root_type || "";
        const root_type_class = DetailPane.ROOT_TYPE_CLASS[root_type] || "muted";
        const chain = d.tally_parent_chain || "";

        // Conditional flag badges
        const flags = [];
        if (d.is_pnl_closed_zero) flags.push(`<span class="flag-badge">PnL Closed Zero</span>`);
        if (d.is_system_account) flags.push(`<span class="flag-badge">System Account</span>`);
        const flags_html = flags.length
            ? `<div class="flag-badges">${flags.join("")}</div>`
            : "";

        const tally_id = d.tally_id || "";

        return `
            <section class="detail-section detail-tally-context">
                <div class="detail-section-header">Tally Context</div>
                <div class="detail-heading">
                    <div class="tally-name-heading">${frappe.utils.escape_html(d.tally_name || "")}</div>
                    ${tally_id ? `<div class="tally-id-sub">ID ${frappe.utils.escape_html(tally_id)}</div>` : ""}
                </div>
                <div class="kv-grid">
                    ${root_type ? `
                        <div class="kv-label">Root Type</div>
                        <div class="kv-value">
                            <span class="root-type-chip ${root_type_class}">${frappe.utils.escape_html(root_type)}</span>
                        </div>
                    ` : ""}
                    ${chain ? `
                        <div class="kv-label">Parent Chain</div>
                        <div class="kv-value detail-parent-chain">${frappe.utils.escape_html(chain)}</div>
                    ` : ""}
                    <div class="kv-label">Opening Dr</div>
                    <div class="kv-value">${MasterPane._format_amount(d.opening_dr)}</div>
                    <div class="kv-label">Opening Cr</div>
                    <div class="kv-value">${MasterPane._format_amount(d.opening_cr)}</div>
                    <div class="kv-label">Net Amount</div>
                    <div class="kv-value kv-net-amount">
                        ${MasterPane._format_amount(d.net_amount)}
                        <span class="net-side-inline">${frappe.utils.escape_html(d.net_side || "—")}</span>
                    </div>
                </div>
                ${flags_html}
            </section>
        `;
    }

    _render_mapper_resolution(d) {
        const tier = d.tier || "";
        const tier_state = MasterPane.TIER_CHIP_STATE[tier] || "muted";
        const proposed = d.proposed_account || "";
        const matched = d.matched_rule || "";
        const confidence = typeof d.confidence === "number"
            ? d.confidence.toFixed(2)
            : "";

        const proposed_html = proposed
            ? `<a href="/app/account/${encodeURIComponent(proposed)}" target="_blank" rel="noopener">${frappe.utils.escape_html(proposed)} <span class="nav-icon">↗</span></a>`
            : `<span class="muted">(none)</span>`;

        const matched_html = matched
            ? `<a href="/app/mapping-rule/${encodeURIComponent(matched)}" target="_blank" rel="noopener">${frappe.utils.escape_html(matched)} <span class="nav-icon">↗</span></a>`
            : `<span class="muted">(none)</span>`;

        // Anti-pattern block (conditional, red-bordered)
        let anti_pattern_html = "";
        if (d.anti_pattern_blocked) {
            anti_pattern_html = `
                <div class="anti-pattern-block">
                    <div class="anti-pattern-label">⚠ Anti-pattern blocked</div>
                    ${d.anti_pattern_rule ? `<div class="anti-pattern-rule">Rule: ${frappe.utils.escape_html(d.anti_pattern_rule)}</div>` : ""}
                    ${d.anti_pattern_message ? `<div class="anti-pattern-message">${frappe.utils.escape_html(d.anti_pattern_message)}</div>` : ""}
                </div>
            `;
        }

        // Excluded reason (conditional — for excluded_pnl / excluded_zero_balance / group_refused)
        const excluded_html = d.excluded_reason
            ? `<div class="excluded-reason-block">${frappe.utils.escape_html(d.excluded_reason)}</div>`
            : "";

        // Proposed Dr/Cr row only shown when at least one is non-zero (mapper diagnostic)
        const proposed_amounts_html = (d.proposed_dr || d.proposed_cr) ? `
            <div class="kv-label">Proposed Dr / Cr</div>
            <div class="kv-value">${MasterPane._format_amount(d.proposed_dr)} / ${MasterPane._format_amount(d.proposed_cr)}</div>
        ` : "";

        return `
            <section class="detail-section detail-mapper-resolution">
                <div class="detail-section-header">Mapper Resolution</div>
                <div class="kv-grid">
                    <div class="kv-label">Tier</div>
                    <div class="kv-value">
                        <span class="tier-chip ${tier_state}">${frappe.utils.escape_html(tier)}</span>
                    </div>
                    <div class="kv-label">Proposed Account</div>
                    <div class="kv-value">${proposed_html}</div>
                    <div class="kv-label">Matched Rule</div>
                    <div class="kv-value">${matched_html}</div>
                    <div class="kv-label">Confidence</div>
                    <div class="kv-value">${confidence}</div>
                    ${proposed_amounts_html}
                </div>
                ${anti_pattern_html}
                ${excluded_html}
            </section>
        `;
    }

    _render_tier2_placeholder() {
        return `
            <section class="detail-section detail-tier2-placeholder">
                <div class="detail-section-header">Tier-2 Fuzzy Candidates</div>
                <div class="placeholder-text">
                    Tier-2 fuzzy matching not yet active. Top candidate matches
                    will appear here after Item 6 ships.
                </div>
            </section>
        `;
    }

    _render_assignment_shell() {
        return `
            <section class="detail-section detail-assignment">
                <div class="detail-section-header">Assignment</div>
                <div class="assignment-widget-mount"></div>
            </section>
        `;
    }

    _mount_assignment() {
        const mount = this.container.find(".assignment-widget-mount");
        if (!mount.length) return;

        const assignments = (this.current_docinfo && this.current_docinfo.assignments) || [];

        this.assignment_widget = new AssignmentWidget({
            parent: mount,
            decision_name: this.current_decision_name,
            assignments: assignments,
            on_change: () => this.refreshAssignments(),
        });
        this.assignment_widget.render();
    }

    _render_audit(d) {
        const creation = d.creation || "";
        const modified = d.modified || "";
        const owner = d.owner || "";
        const modified_by = d.modified_by || "";
        const promoted = d.promoted_to_rule || "";

        // frappe.datetime.comment_when is "2 hours ago"-style pretty format.
        // Full ISO in the title= tooltip for reviewer who needs exact time.
        const creation_display = creation
            ? `<span title="${frappe.utils.escape_html(creation)}">${frappe.datetime.comment_when(creation)}</span>`
            : "—";
        const modified_display = modified
            ? `<span title="${frappe.utils.escape_html(modified)}">${frappe.datetime.comment_when(modified)}</span>`
            : "—";

        const promoted_html = promoted
            ? `<a href="/app/mapping-rule/${encodeURIComponent(promoted)}" target="_blank" rel="noopener">${frappe.utils.escape_html(promoted)} <span class="nav-icon">↗</span></a>`
            : `<span class="muted">—</span>`;

        const open_form_url = `/app/mapping-decision/${encodeURIComponent(d.name || "")}`;

        return `
            <section class="detail-section detail-audit">
                <div class="detail-section-header">Audit</div>
                <div class="kv-grid">
                    <div class="kv-label">Created by</div>
                    <div class="kv-value">${frappe.utils.escape_html(owner)} · ${creation_display}</div>
                    <div class="kv-label">Last edited by</div>
                    <div class="kv-value">${frappe.utils.escape_html(modified_by)} · ${modified_display}</div>
                    <div class="kv-label">Promoted to rule</div>
                    <div class="kv-value">${promoted_html}</div>
                </div>
                <div class="audit-actions">
                    <a class="btn btn-default btn-xs open-full-form" href="${open_form_url}" target="_blank" rel="noopener">
                        Open in full form <span class="nav-icon">↗</span>
                    </a>
                </div>
            </section>
        `;
    }
}


// ============================================================================
// MAIN: on_page_load
// ============================================================================

frappe.pages["md-review"].on_page_load = function(wrapper) {
    const page = frappe.ui.make_app_page({
        parent: wrapper,
        title: "Mapping Decision Review",
        single_column: true
    });

    // Parse route: /app/md-review/<session-name>[#<decision-name>]
    const route = frappe.get_route();
    const session_name = route[1] || null;
    const decision_hash = window.location.hash ? window.location.hash.substring(1) : null;

    console.log("md-review loaded", {
        session_name: session_name,
        decision_hash: decision_hash
    });

    // 6/4 split-pane shell per §1.2. Master pane now hosts FilterBar +
    // MasterPane; detail pane stays as placeholder until Commit 4.
    const container = $(`
        <div class="mapping-decision-review-app">
            <div class="decision-list-pane">
                <div class="filter-bar-container"></div>
                <div class="master-pane-container"></div>
            </div>
            <div class="decision-detail-pane">
                <div class="pane-placeholder">Detail pane — Commit 4</div>
            </div>
        </div>
    `).appendTo(page.body);

    if (!session_name) {
        frappe.show_alert({
            message: "No session specified. Navigate from a Tally Migration Session form via the 'Review Decisions' button.",
            indicator: "orange"
        }, 7);
        return;
    }

    // DetailPane instantiates FIRST so the controller's on_selection_change
    // callback can close over it. The detail-pane container is already in
    // the DOM (from the shell above); DetailPane replaces its contents with
    // the empty-state placeholder, then with rendered sections on row
    // selection.
    const detail_pane = new DetailPane(container.find(".decision-detail-pane"));

    // Controller: thin orchestration layer between FilterBar + MasterPane +
    // DetailPane. Selection in the master pane drives URL hash sync AND
    // detail pane refresh; filter changes drive a master-pane reload AND
    // a detail-pane refresh for the new selection (or clear if empty).
    const controller = {
        on_selection_change: (decision) => {
            if (decision && decision.name) {
                history.replaceState(null, "", "#" + decision.name);
                detail_pane.loadDecision(decision.name);
            }
        },
    };

    const filter_bar = new FilterBar(container.find(".filter-bar-container"));
    const master_pane = new MasterPane(
        container.find(".master-pane-container"),
        session_name,
        controller
    );

    filter_bar.onChange(() => {
        master_pane.setFilters(filter_bar.getFilters());
        master_pane.pagination_start = 0;
        master_pane.loadDecisions().then(() => {
            // After filter change, the master pane reselects row 0. Sync
            // the detail pane to that new selection, or clear if empty.
            const selected = master_pane.getSelectedDecisionName();
            if (selected) {
                detail_pane.loadDecision(selected);
            } else {
                detail_pane.clear();
            }
        });
    });

    // Initial load
    master_pane.setFilters(filter_bar.getFilters());
    master_pane.loadDecisions().then(() => {
        // If URL had a #<decision-name>, restore selection to that row if
        // present in the result set; otherwise the default (first row)
        // remains selected.
        if (decision_hash) {
            master_pane.selectByName(decision_hash);
        }
        // Trigger initial detail-pane load for whatever row ended up
        // selected. selectByName already fires on_selection_change (→
        // detail_pane.loadDecision), which is idempotent, so this explicit
        // call covers the "no hash, default row 0" case without double-
        // fetching the hash case.
        const selected = master_pane.getSelectedDecisionName();
        if (selected) {
            detail_pane.loadDecision(selected);
        }
    });

    // --------------------------------------------------------------------
    // Keyboard shortcuts — page-scoped per §1.3 "Row interactions"
    // --------------------------------------------------------------------
    // All shortcuts are registered with ``page: page`` so they only fire on
    // this page (not other Desk pages). Default ``ignore_inputs: false``
    // means they don't fire while focus is in a text input — so typing in
    // the filter search box doesn't get eaten by arrow keys.

    frappe.ui.keys.add_shortcut({
        shortcut: "arrowup",
        action: () => {
            const idx = master_pane.selected_index;
            if (idx === null || idx <= 0) return;
            master_pane.selectRow(idx - 1);
        },
        description: __("Previous decision"),
        page: page,
    });

    frappe.ui.keys.add_shortcut({
        shortcut: "arrowdown",
        action: () => {
            const idx = master_pane.selected_index;
            const last = master_pane.current_decisions.length - 1;
            if (last < 0) return;
            if (idx === null) {
                master_pane.selectRow(0);
                return;
            }
            if (idx >= last) return;
            master_pane.selectRow(idx + 1);
        },
        description: __("Next decision"),
        page: page,
    });

    frappe.ui.keys.add_shortcut({
        shortcut: "home",
        action: () => {
            if (master_pane.current_decisions.length === 0) return;
            master_pane.selectRow(0);
        },
        description: __("First decision"),
        page: page,
    });

    frappe.ui.keys.add_shortcut({
        shortcut: "end",
        action: () => {
            const last = master_pane.current_decisions.length - 1;
            if (last < 0) return;
            master_pane.selectRow(last);
        },
        description: __("Last decision"),
        page: page,
    });

    frappe.ui.keys.add_shortcut({
        shortcut: "enter",
        action: () => {
            // TODO Commit 4: focus detail.final_account control
        },
        description: __("Focus detail pane (Commit 4+)"),
        page: page,
    });
};
