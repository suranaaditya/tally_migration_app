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

    // Pending-like review states. Mirror of DetailPane.PENDING_REVIEW_STATES
    // and query.PENDING_REVIEW_STATES (Python). When review_action is in
    // one of these, the tier chip remains meaningful — it tells the
    // reviewer "here's what the mapper thought". Once the reviewer has
    // resolved the decision (Approved, Rejected, etc.), the tier chip
    // is historical noise that conflicts with the indicator dot; hide
    // it (or swap for an "excluded" chip on Excluded (P&L)).
    static PENDING_REVIEW_STATES = new Set([
        "Pending",
        "Pending Account Creation",
        "Pending Group Account Resolution",
        "Pending Supplier Creation",
    ]);

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

        // Tier chip display rule: the mapper's tier classification is
        // historical — it describes what the mapper initially proposed.
        // Once the reviewer has resolved the decision (Approved,
        // Rejected, Deferred, Skipped, Manual Override), the tier chip
        // conflicts with the indicator dot (e.g. "unmapped" tier chip
        // next to a green done-state dot). Hide it for resolved states,
        // keep it visible for the four Pending* variants so the
        // reviewer sees the mapper signal while the decision is still
        // open. Excluded (P&L) gets a muted "excluded" chip — an
        // indicator-independent signal that the ledger is routed out
        // of the migration entirely.
        const review_action = decision.review_action || "";
        let tier_chip_html = "";
        if (MasterPane.PENDING_REVIEW_STATES.has(review_action)) {
            tier_chip_html = `<span class="tier-chip ${tier_state}">${frappe.utils.escape_html(tier_label)}</span>`;
        } else if (review_action === "Excluded (P&L)") {
            tier_chip_html = `<span class="tier-chip muted">excluded</span>`;
        }
        // else: resolved state. Empty cell, indicator dot carries the
        // state signal.

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
                    ${tier_chip_html}
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

    /**
     * Optimistically patch a single row in-place after a successful
     * save — no list refetch, no re-sort. The page.controller invokes
     * this from DetailPane.on_save_success so the indicator dot and
     * proposed-account cell reflect the just-saved state without the
     * round-trip + re-render cost.
     *
     * The underlying ``current_decisions`` entry is mutated so that
     * subsequent filter/refetch cycles start from the new values, and
     * the row's DOM is re-rendered in place to pick up the indicator-
     * state + tier class changes.
     *
     * If ``name`` isn't currently in the list (e.g. a filter excluded
     * it), silently no-ops — the next list refresh will pick up the
     * change. Save already succeeded on the server.
     *
     * @param {string} name - Mapping Decision name
     * @param {object} patch - saved decision dict from save_decision
     *   response. Only the fields present in _render_row's output are
     *   consumed (review_action, final_account, tier,
     *   proposed_account).
     */
    updateRow(name, patch) {
        if (!name || !patch) return;
        const idx = this.current_decisions.findIndex((d) => d.name === name);
        if (idx < 0) return;

        // Merge patch onto the cached decision. Keep fields the server
        // didn't return (e.g. tally_name, net_amount — immutable from
        // this endpoint's perspective) intact.
        const merged = { ...this.current_decisions[idx], ...patch };
        this.current_decisions[idx] = merged;

        // Re-render only the affected row. _render_row is pure; find
        // the <tr> by data-name and replaceWith.
        const new_html = this._render_row(merged, idx);
        const $row = this.container.find(
            `.master-row[data-name="${$.escapeSelector(name)}"]`
        );
        if ($row.length) {
            $row.replaceWith(new_html);
            // Preserve selection highlight if this was the selected row.
            if (this.selected_index === idx) {
                this._apply_selection_highlight();
            }
        }
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

    // Mirror of rgi_migration.rgi_migration.page.md_review.query.PENDING_REVIEW_STATES.
    // Kept in sync with the Python side — see test_auto_flip_logic.py's
    // test_pending_subvariants_all_flip for the cross-language invariant
    // guard. If the Select enum grows a new Pending-like state, add it to
    // BOTH this JS frozenset and the Python frozenset.
    static PENDING_REVIEW_STATES = new Set([
        "Pending",
        "Pending Account Creation",
        "Pending Group Account Resolution",
        "Pending Supplier Creation",
    ]);

    /**
     * Pure function: compute what review_action should become, given the
     * current control values and the row-load-time proposed_account.
     * Mirror of query.compute_auto_flip (Python); see that docstring and
     * test_auto_flip_logic.py for the authoritative truth table. Both
     * versions must stay behaviourally identical.
     *
     * @param {object} params
     * @param {string|null} params.originalProposedAccount - proposed_account at row load
     * @param {string} params.currentReviewAction - current Select value
     * @param {string|null} params.currentFinalAccount - current Link value (empty = cleared)
     * @returns {string} The review_action Select value to display.
     */
    static _computeAutoFlip({
        originalProposedAccount,
        currentReviewAction,
        currentFinalAccount,
    }) {
        // Row 5 — reviewer chose a terminal state, respect it.
        if (!DetailPane.PENDING_REVIEW_STATES.has(currentReviewAction)) {
            return currentReviewAction;
        }
        // Row 4 — final_account cleared, back to Pending.
        if (!currentFinalAccount) {
            return "Pending";
        }
        // Row 1 — unmapped + picked = Approved (not override).
        if (!originalProposedAccount) {
            return "Approved";
        }
        // Row 2 — mapped + picked same = Approved.
        if (currentFinalAccount === originalProposedAccount) {
            return "Approved";
        }
        // Row 3 — mapped + picked different = Manual Override.
        return "Manual Override";
    }

    constructor(container_el, opts = {}) {
        this.container = $(container_el);
        // Optional callback — invoked by saveDecision() after a
        // successful save so the controller can update the master
        // pane row in place (optimistic UX — no full list refetch).
        // Signature: (decision_name, saved_decision_dict) => void.
        this.on_save_success = opts.on_save_success || null;
        this.current_decision_name = null;
        this.current_decision = null;
        this.current_docinfo = null;
        this.session_company_abbr = null;
        this.session_erpnext_company = null;
        this.assignment_widget = null;
        // Section 4 control instances, indexed by fieldname. Re-created
        // on every _render() call (the previous DOM is replaced, so the
        // old instances are orphaned and GC'd once this map is replaced).
        // Populated by _mount_reviewer_action_controls().
        this.section4_controls = {};
        // Auto-flip needs the pre-reviewer proposed_account (pinned at
        // row load time). Kept on ``this`` so the onchange handler
        // can read it without re-fetching current_decision.
        this._original_proposed_account = null;
        // Dirty-detection originals — snapshotted in
        // _mount_reviewer_action_controls after the three writable
        // controls are populated. Comparing current control values
        // against these determines whether the Save button is
        // enabled. reviewer_notes's original is always "" (textarea
        // always starts empty per the chronology-separation UX).
        this._original_review_action = null;
        this._original_final_account = null;
        // True during initial ``set_value`` calls that populate the
        // controls. Blocks the onchange handler from firing the auto-
        // flip + dirty-check logic on controlled state changes.
        // Cleared after initial population.
        this._loading_controls = false;
        // In-flight save guard — blocks double-click / Enter-spam
        // while a save_decision round-trip is pending.
        this._saving = false;
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
        this.session_erpnext_company = null;
        this.assignment_widget = null;
        this.section4_controls = {};
        this._original_proposed_account = null;
        this._original_review_action = null;
        this._original_final_account = null;
        this._loading_controls = false;
        this._saving = false;
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
            this.session_erpnext_company = msg.session_erpnext_company || "";
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
                ${this._render_reviewer_action_shell(d)}
                ${this._render_assignment_shell()}
                ${this._render_audit(d)}
            </div>
        `);

        // Controls that need interactive state mount AFTER HTML is in
        // the DOM. Each mount helper finds its own shell and attaches.
        // Previous control instances (if any) are abandoned here —
        // their DOM parents are gone, so listeners are effectively
        // detached; the JS objects are GC'd once the old maps fall
        // out of scope.
        this.section4_controls = {};
        this._mount_reviewer_action_controls();
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

    _render_reviewer_action_shell(d) {
        // Section 4 — the writable work surface. The shell is pure HTML
        // (labels + mount divs + read-only display values for final_dr/cr
        // + a stored-notes chronology block). The four writable controls
        // (Select, Link, read-only Currency fields were folded to plain
        // HTML since they're v1-read-only per refinement 2, Small Text)
        // are instantiated after render via make_control in
        // _mount_reviewer_action_controls.
        //
        // final_dr / final_cr render as plain read-only currency text
        // (not make_control-backed) since refinement 2 locks them to
        // mirror opening_dr / opening_cr until the splitting workflow
        // lands in v2 — no control machinery needed for a display-only
        // field that will never carry reviewer edits in this version.
        const final_dr_val = (d.final_dr != null && d.final_dr !== 0)
            ? d.final_dr
            : (d.opening_dr || 0);
        const final_cr_val = (d.final_cr != null && d.final_cr !== 0)
            ? d.final_cr
            : (d.opening_cr || 0);

        // Stored reviewer_notes render as a read-only chronology block
        // ABOVE the input textarea. The textarea itself starts empty —
        // the user types only the new note, and the server (via
        // apply_decision_save) prepends the chronology header and
        // concatenates with stored on save. Separating display from
        // input keeps the "what's new in this save" unambiguous.
        const stored_notes = d.reviewer_notes || "";
        const stored_notes_html = stored_notes
            ? `<div class="reviewer-notes-stored" title="Prior notes — read-only">${frappe.utils.escape_html(stored_notes)}</div>`
            : "";

        return `
            <section class="detail-section detail-reviewer-action">
                <div class="detail-section-header">Reviewer Action</div>
                <div class="reviewer-action-grid">
                    <div class="ra-field ra-review-action">
                        <label class="ra-label">Review Action</label>
                        <div class="review-action-mount"></div>
                    </div>
                    <div class="ra-field ra-final-account">
                        <label class="ra-label">Final Account</label>
                        <div class="final-account-mount"></div>
                    </div>
                    <div class="ra-field ra-final-dr">
                        <label class="ra-label">Final Dr</label>
                        <div class="readonly-currency">${MasterPane._format_amount(final_dr_val)}</div>
                    </div>
                    <div class="ra-field ra-final-cr">
                        <label class="ra-label">Final Cr</label>
                        <div class="readonly-currency">${MasterPane._format_amount(final_cr_val)}</div>
                    </div>
                    <div class="ra-field ra-reviewer-notes">
                        <label class="ra-label">Reviewer Notes</label>
                        ${stored_notes_html}
                        <div class="reviewer-notes-mount"></div>
                    </div>
                </div>
                <div class="reviewer-action-footer">
                    <button class="btn btn-primary btn-sm save-decision-btn" disabled>
                        Save
                    </button>
                </div>
            </section>
        `;
    }

    _mount_reviewer_action_controls() {
        const d = this.current_decision || {};
        const company = this.session_erpnext_company || "";

        const review_action_mount = this.container.find(".review-action-mount");
        const final_account_mount = this.container.find(".final-account-mount");
        const reviewer_notes_mount = this.container.find(".reviewer-notes-mount");

        if (!review_action_mount.length) {
            // Section 4 shell didn't render (defensive). Nothing to mount.
            return;
        }

        // Pin the row-load-time proposed_account. The auto-flip truth
        // table distinguishes "unmapped + reviewer supplied mapping"
        // from "mapped + reviewer picked different" — both cases can
        // have the same current_final_account, so we need to remember
        // what the mapper proposed BEFORE the reviewer touched
        // anything. Captured here, read by _handle_final_account_change
        // on every change event.
        this._original_proposed_account = d.proposed_account || null;

        // Block the onchange handlers below during initial set_value
        // population — the handler would otherwise auto-flip
        // review_action to "Pending" the moment we clear final_account
        // (or to "Approved" when we set it). Cleared at the end of this
        // method once all three controls are populated.
        this._loading_controls = true;

        // review_action Select — 10 enum options per Mapping Decision
        // DocType. Options string uses the exact spelling the DocType
        // expects (Frappe compares literally on save).
        // onchange handler: a MANUAL change to the Select doesn't
        // trigger auto-flip (auto-flip runs only on final_account
        // changes); it just marks the state dirty. The handler is a
        // thin wrapper around _on_section4_change — see that method
        // for the dirty-tracking logic.
        this.section4_controls.review_action = frappe.ui.form.make_control({
            parent: review_action_mount[0],
            df: {
                fieldtype: "Select",
                fieldname: "review_action",
                options: [
                    "Pending",
                    "Approved",
                    "Rejected",
                    "Manual Override",
                    "Deferred",
                    "Skipped",
                    "Excluded (P&L)",
                    "Pending Account Creation",
                    "Pending Group Account Resolution",
                    "Pending Supplier Creation",
                ].join("\n"),
                onchange: () => this._on_section4_change("review_action"),
            },
            render_input: true,
        });
        this.section4_controls.review_action.set_value(d.review_action || "Pending");

        // final_account Link — scoped to session's Company via the
        // custom account_query_with_parent whitelist method.
        // onchange handler: applies the auto-flip truth table (§1.4
        // refinement 1) — when final_account changes, review_action
        // may auto-mutate per the rules in
        // DetailPane._computeAutoFlip. Also marks state dirty.
        this.section4_controls.final_account = frappe.ui.form.make_control({
            parent: final_account_mount[0],
            df: {
                fieldtype: "Link",
                fieldname: "final_account",
                options: "Account",
                get_query: () => ({
                    query: "rgi_migration.rgi_migration.page.md_review.md_review.account_query_with_parent",
                    filters: {
                        company: company,
                        is_group: 0,
                    },
                }),
                onchange: () => this._on_section4_change("final_account"),
            },
            render_input: true,
        });
        this.section4_controls.final_account.set_value(d.final_account || "");

        // reviewer_notes — Small Text. Always mounts empty; stored
        // chronology is shown separately above via the read-only
        // block in _render_reviewer_action_shell.
        this.section4_controls.reviewer_notes = frappe.ui.form.make_control({
            parent: reviewer_notes_mount[0],
            df: {
                fieldtype: "Small Text",
                fieldname: "reviewer_notes",
                placeholder: "Add a note — chronology header is added automatically on save",
                onchange: () => this._on_section4_change("reviewer_notes"),
            },
            render_input: true,
        });
        this.section4_controls.reviewer_notes.set_value("");

        // Small Text's onchange fires on blur, not on every keystroke —
        // without this `input` listener the Save button wouldn't enable
        // until the reviewer clicked outside the textarea. Bind
        // directly to the DOM element so we react as the user types.
        const notes_input = this.section4_controls.reviewer_notes.$input;
        if (notes_input && notes_input.length) {
            notes_input.off("input.mdr-notes").on("input.mdr-notes", () => {
                if (this._loading_controls) return;
                this._update_save_button_state();
            });
        }

        // Snapshot originals for dirty detection. Captured AFTER the
        // initial set_value calls so we record the post-population
        // state, not the pre-population blanks. reviewer_notes's
        // original is always "" — the textarea represents the "new
        // note for this save" increment, never the stored chronology.
        this._original_review_action =
            this.section4_controls.review_action.get_value() || "";
        this._original_final_account =
            this.section4_controls.final_account.get_value() || "";

        // Population complete — release the onchange guard. From here
        // on, any value change is reviewer-initiated and should run
        // the handler.
        this._loading_controls = false;

        // Wire the Save button click handler. Event-delegated on the
        // Section 4 container so it survives the shell-re-render
        // between row switches. jQuery .on("click") is idempotent per
        // selector+handler, but we use .off+on to guarantee no stale
        // handler from a prior mount lingers (belt-and-braces — in
        // practice _render() replaces innerHTML so handlers die with
        // the DOM, but explicit off defends against any code path
        // that mutates without a full _render).
        const btn = this.container.find(".save-decision-btn");
        btn.off("click.mdr-save").on("click.mdr-save", () => this.saveDecision());

        // Initial button state — freshly loaded row is clean.
        this._update_save_button_state();
    }

    /**
     * Compute whether Section 4 has unsaved changes.
     *
     * Dirty if ANY of:
     *   - review_action differs from original (manual pick OR auto-flip
     *     from a final_account change both count)
     *   - final_account differs from original
     *   - reviewer_notes textarea is non-empty (always starts empty;
     *     any content is a new note to persist)
     *
     * Pure read — no side effects. Safe to call from onchange handlers,
     * render paths, and anywhere else.
     *
     * @returns {boolean}
     */
    isDirty() {
        if (!this.section4_controls.review_action) return false;  // pre-render

        const current_ra = this.section4_controls.review_action.get_value() || "";
        const current_fa = this.section4_controls.final_account.get_value() || "";
        const current_notes = this.section4_controls.reviewer_notes.get_value() || "";

        return (
            current_ra !== this._original_review_action ||
            current_fa !== this._original_final_account ||
            current_notes !== ""
        );
    }

    _update_save_button_state() {
        const btn = this.container.find(".save-decision-btn");
        if (!btn.length) return;
        // During an in-flight save, button stays disabled regardless of
        // dirty state — prevents double-submit before the await resolves.
        btn.prop("disabled", this._saving || !this.isDirty());
    }

    /**
     * Persist Section 4 inputs via the save_decision whitelist method.
     *
     * Flow:
     *   1. Collect values, send to backend
     *   2. On success: reload decision (chronology header is added
     *      server-side, so the new stored notes block reflects the
     *      concatenated value immediately), reset dirty snapshots,
     *      fire on_save_success callback for the master-pane
     *      optimistic row update
     *   3. On error: Frappe's default dialog surfaces the message;
     *      we just log and keep the current dirty state so the
     *      reviewer can retry without retyping
     *
     * The _saving guard blocks re-entry between click and await
     * resolution.
     */
    async saveDecision() {
        if (this._saving) return;
        if (!this.isDirty()) return;  // button should be disabled; defensive.
        if (!this.current_decision_name) return;

        this._saving = true;
        this._update_save_button_state();

        const decision_name = this.current_decision_name;
        const review_action = this.section4_controls.review_action.get_value() || "";
        const final_account = this.section4_controls.final_account.get_value() || "";
        const reviewer_notes = this.section4_controls.reviewer_notes.get_value() || "";

        try {
            const r = await frappe.call({
                method: "rgi_migration.rgi_migration.page.md_review.md_review.save_decision",
                args: {
                    decision_name: decision_name,
                    review_action: review_action,
                    final_account: final_account,
                    reviewer_notes: reviewer_notes,
                },
            });

            const saved = r.message || {};
            frappe.show_alert({ message: __("Saved"), indicator: "green" }, 3);

            // Notify the controller so it can update the master pane
            // row in place (optimistic UX — no list refetch).
            if (this.on_save_success) {
                this.on_save_success(decision_name, saved);
            }

            // Reload the decision so the chronology block, updated
            // modified/modified_by timestamps, and any server-side
            // value transformations are visible to the reviewer.
            // Force re-fetch by clearing the idempotency guard.
            this.current_decision_name = null;
            await this.loadDecision(decision_name);
            // loadDecision → _render → _mount_reviewer_action_controls
            // re-snapshots originals, so dirty state is naturally
            // clean on the reloaded row.
        } catch (err) {
            // Frappe surfaces whitelist-method errors automatically via
            // its error dialog (frappe.call handles the response
            // shape). All we do here is log for dev debugging and
            // preserve dirty state so the reviewer can retry.
            console.error("md-review: save_decision failed", err);
        } finally {
            this._saving = false;
            this._update_save_button_state();
        }
    }

    /**
     * Central change handler for Section 4 controls. Called on every
     * onchange event from the three writable controls. Responsibilities:
     *
     *  - On final_account change: compute auto-flip (§1.4 refinement 1)
     *    and programmatically set review_action. The programmatic
     *    set_value re-enters this handler (Frappe fires onchange on
     *    set_value too), so the _loading_controls flag is raised around
     *    the set_value call to prevent recursion.
     *
     *  - Dirty-state tracking lands in Phase D.3. For now, this handler
     *    is auto-flip-only.
     *
     * @param {string} field_that_changed - "review_action" / "final_account" / "reviewer_notes"
     */
    _on_section4_change(field_that_changed) {
        // Skip the handler entirely during initial row-load population.
        // Also skip when we're mid-programmatic-flip (see below).
        if (this._loading_controls) return;

        if (field_that_changed === "final_account") {
            const current_final = this.section4_controls.final_account.get_value() || "";
            const current_action = this.section4_controls.review_action.get_value() || "Pending";

            const new_action = DetailPane._computeAutoFlip({
                originalProposedAccount: this._original_proposed_account,
                currentReviewAction: current_action,
                currentFinalAccount: current_final,
            });

            if (new_action !== current_action) {
                // Programmatic set_value would re-enter this handler via
                // onchange and potentially cause a flip-feedback loop if
                // the review_action branch ever grows logic. Raise the
                // guard for the duration of the flip to keep the chain
                // single-pass.
                this._loading_controls = true;
                try {
                    this.section4_controls.review_action.set_value(new_action);
                } finally {
                    this._loading_controls = false;
                }
            }
        }
        // review_action manual change and reviewer_notes edits: no
        // auto-flip, but the Save button state still needs to update.
        // All three field changes funnel into this terminal step.
        this._update_save_button_state();
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
    //
    // on_save_success bridges DetailPane → MasterPane.updateRow so the
    // list row reflects the just-saved state without a full list
    // refetch. Declared here (rather than in the controller object
    // below) because master_pane isn't in scope until after
    // DetailPane is constructed — the closure captures the outer
    // binding which is filled in a few lines later; by the time
    // on_save_success actually fires, master_pane is populated.
    let master_pane;
    const detail_pane = new DetailPane(
        container.find(".decision-detail-pane"),
        {
            on_save_success: (name, saved) => {
                if (master_pane) master_pane.updateRow(name, saved);
            },
        }
    );

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
    master_pane = new MasterPane(
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

    // Ctrl+S keyboard shortcut — saves the current decision when
    // Section 4 is dirty. First shortcut in the page; the fuller
    // suite (a/r/c/d/Ctrl+Shift+S/Ctrl+Z) lands in Commit 5 alongside
    // the opinionated Approve & Next / Reject / Defer flow and their
    // validation semantics. Ctrl+S is the one shortcut whose meaning
    // ("save current form") is unambiguous enough to ship standalone.
    //
    // ignore_inputs: true is load-bearing — reviewers spend most of
    // their time with focus in the reviewer_notes textarea, and the
    // shortcut MUST fire from there without requiring a blur-first.
    // The _saving guard inside saveDecision() handles the double-
    // fire case (click + Ctrl+S in quick succession).
    frappe.ui.keys.add_shortcut({
        shortcut: "ctrl+s",
        action: () => {
            if (detail_pane.isDirty() && !detail_pane._saving) {
                detail_pane.saveDecision();
            }
            // If clean, swallow the Ctrl+S silently — no toast, no
            // error. Reviewers will habitually Ctrl+S to confirm
            // "yes, this row is good as-is"; we don't want to nag.
        },
        description: __("Save current decision"),
        page: page,
        ignore_inputs: true,
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
