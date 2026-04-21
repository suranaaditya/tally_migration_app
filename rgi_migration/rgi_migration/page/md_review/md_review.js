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

    // Controller: thin orchestration layer so FilterBar + MasterPane stay
    // cohesive classes. Grows into the keyboard-shortcut + detail-pane
    // coupling home in Commits 4 and 5.
    const controller = {
        on_selection_change: (decision) => {
            // URL hash sync on selection. Detail pane update lands Commit 4.
            if (decision && decision.name) {
                history.replaceState(null, "", "#" + decision.name);
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
        master_pane.loadDecisions();
    });

    // Initial load
    master_pane.setFilters(filter_bar.getFilters());
    master_pane.loadDecisions().then(() => {
        // If URL had a #<decision-name>, restore selection to that row if
        // present in the result set; otherwise leave the default (first row)
        // selected and log for diagnostic.
        if (decision_hash) {
            master_pane.selectByName(decision_hash);
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
