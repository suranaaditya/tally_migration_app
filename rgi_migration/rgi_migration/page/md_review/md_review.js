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

    /**
     * Programmatically flip the preset pill. Used by the all-resolved
     * empty state's "Show All Decisions" button so the reviewer can
     * exit the empty state without the hunt-and-click to the preset
     * pill at the top of the page.
     *
     * @param {"pending"|"all"} preset
     */
    setPreset(preset) {
        if (preset === this.state.preset) return;
        if (preset !== "pending" && preset !== "all") return;
        this.container.find(".filter-preset-pill").removeClass("active");
        this.container.find(`.filter-preset-pill[data-preset="${preset}"]`).addClass("active");
        this.state.preset = preset;
        this._fire_change();
    }

    /**
     * Reset all filter state to defaults: Pending preset, no tier /
     * root-type / search filters. Used by the master-pane's §1.9
     * case 4 empty-state "Clear filters" CTA.
     *
     * Updates DOM and state in lock-step, then fires a single change
     * event so the master pane reloads once (not four times).
     */
    reset() {
        this.state.preset = "pending";
        this.state.tiers = [];
        this.state.root_types = [];
        this.state.search = "";

        // Sync DOM — preset pills, multi-checks, search input.
        this.container.find(".filter-preset-pill").removeClass("active");
        this.container.find(`.filter-preset-pill[data-preset="pending"]`).addClass("active");
        // ControlMultiCheck doesn't expose a public "uncheck all" — the
        // simplest portable path is to uncheck the underlying input
        // boxes directly. The MultiCheck reads back via DOM scan, so
        // the next get_checked_options() call returns the empty list.
        this.container
            .find(".filter-tier input[type=checkbox]:checked")
            .prop("checked", false);
        this.container
            .find(".filter-root-type input[type=checkbox]:checked")
            .prop("checked", false);
        this.container.find(".filter-search-input").val("");

        this._fire_change();
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
    // Supplier tiers extended 2026-04-22 per Item 3 Commit 1a — Item 2's
    // enum extension added tier1_supplier_exact / _alias; without entries
    // here they'd fall back to "muted" (grey) which reads as excluded.
    static TIER_CHIP_STATE = {
        "tier1_exact": "resolved",
        "tier1_rule": "resolved",
        "tier1_pattern": "resolved",
        "tier1_supplier_exact": "resolved",
        "tier1_supplier_alias": "resolved",
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
            this.session_missing = false;

            this._render_rows(this.current_decisions);
            this._update_footer();

            if (this.selected_index !== null) {
                this._apply_selection_highlight();
                // URL hash sync happens via controller on explicit selectRow;
                // initial-load hash restoration is handled by the controller
                // after load completes.
            }
        } catch (err) {
            // §1.9 case 1: invalid session segment. The Frappe wrapper
            // raises DoesNotExistError when the session can't be loaded;
            // surface that as the "Session not found" empty state inside
            // the master pane (detail pane stays cleared by the
            // controller). Other errors stay as red toast — most likely
            // permission or transport.
            const exc_type = (err && err._server_messages) || "";
            const is_missing = (
                err && err.exc_type === "DoesNotExistError"
            ) || /DoesNotExistError|does not exist|not found/i.test(
                String(err && (err.message || exc_type))
            );

            if (is_missing) {
                this.session_missing = true;
                this.current_decisions = [];
                this.total_count = 0;
                this.filtered_count = 0;
                this._render_rows([]);
                this._update_footer();
                return;
            }

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
            tbody.html(this._render_empty_state_row());
            this._wire_empty_state_actions();
            return;
        }

        const rows_html = decisions.map((d, idx) => this._render_row(d, idx)).join("");
        tbody.html(rows_html);
    }

    /**
     * Pick the right §1.9 empty-state for the current load context.
     *
     * Branches:
     *   - session_missing → case 1 ("Session not found")
     *   - total_count == 0 → case 2 (zero parsed decisions)
     *   - filtered_count == 0 + has extra non-default filters → case 4
     *   - filtered_count == 0 + only default Pending filter → case 3
     *     ("All decisions resolved" — duplicates the detail-pane
     *     showAllResolvedEmpty for the initial-load case where no save
     *     is in flight)
     *
     * Returns the full <tr> HTML; ``_wire_empty_state_actions`` binds
     * the buttons after insertion.
     */
    _render_empty_state_row() {
        if (this.session_missing) {
            return `
                <tr class="master-empty-row">
                    <td colspan="7">
                        <div class="master-empty-state master-empty-session-missing">
                            <h5>${__("Session not found.")}</h5>
                            <p class="text-muted">
                                ${__("The session {0} doesn't exist on this bench.",
                                    [`<code>${frappe.utils.escape_html(this.session_name || "")}</code>`])}
                            </p>
                            <button class="btn btn-default btn-sm master-empty-go-list">
                                ${__("Go to Session list")}
                            </button>
                        </div>
                    </td>
                </tr>
            `;
        }

        if (this.total_count === 0) {
            return `
                <tr class="master-empty-row">
                    <td colspan="7">
                        <div class="master-empty-state master-empty-no-decisions">
                            <h5>${__("No decisions for this session.")}</h5>
                            <p class="text-muted">
                                ${__("Run parse + map on the session before reviewing. Generators can't run without mapped decisions.")}
                            </p>
                            <button class="btn btn-default btn-sm master-empty-back-session">
                                ${__("Back to Session")}
                            </button>
                        </div>
                    </td>
                </tr>
            `;
        }

        if (MasterPane._hasExtraFilters(this.current_filters)) {
            // Case 4 — non-default filter eliminated everything.
            return `
                <tr class="master-empty-row">
                    <td colspan="7">
                        <div class="master-empty-state master-empty-filtered-out">
                            <h5>${__("No decisions match this filter.")}</h5>
                            <p class="text-muted">
                                ${__("Try clearing filters or switching back to the Pending preset.")}
                            </p>
                            <button class="btn btn-default btn-sm master-empty-clear-filters">
                                ${__("Clear filters")}
                            </button>
                        </div>
                    </td>
                </tr>
            `;
        }

        // Case 3 — default Pending preset matches zero rows = all
        // decisions resolved. Detail-pane already renders a richer
        // version when triggered post-save (showAllResolvedEmpty);
        // master-pane variant is for the initial-load case.
        return `
            <tr class="master-empty-row">
                <td colspan="7">
                    <div class="master-empty-state master-empty-all-resolved">
                        <h5>${__("All decisions in the current filter are resolved.")} \u{1F389}</h5>
                        <p class="text-muted">
                            ${__("The session is ready to regenerate its four output artefacts.")}
                        </p>
                        <button class="btn btn-default btn-sm master-empty-show-all">
                            ${__("Show All Decisions")}
                        </button>
                    </div>
                </td>
            </tr>
        `;
    }

    _wire_empty_state_actions() {
        // Click handlers for the empty-state CTAs. Bound after each
        // render because the buttons are recreated each time.
        this.container.find(".master-empty-go-list").off("click").on("click", () => {
            frappe.set_route("List", "Tally Migration Session");
        });
        this.container.find(".master-empty-back-session").off("click").on("click", () => {
            frappe.set_route("Form", "Tally Migration Session", this.session_name);
        });
        this.container.find(".master-empty-clear-filters").off("click").on("click", () => {
            // Defer to the FilterBar via the controller's empty-state hook.
            // Falls back to a route reload if the hook isn't wired.
            if (this.controller && typeof this.controller.on_clear_filters === "function") {
                this.controller.on_clear_filters();
            } else {
                window.location.reload();
            }
        });
        this.container.find(".master-empty-show-all").off("click").on("click", () => {
            if (this.controller && typeof this.controller.on_show_all === "function") {
                this.controller.on_show_all();
            }
        });
    }

    /**
     * "Default Pending preset" check for empty-state branching. Anything
     * beyond a single ``review_action`` filter — tier multi-select,
     * root-type multi-select, search box — counts as an "extra" filter
     * and routes to §1.9 case 4 ("clear filters"). The "all decisions"
     * preset (no review_action key at all) ALSO counts as extra so the
     * filtered-out copy fires when the reviewer dropped the Pending
     * filter manually.
     */
    static _hasExtraFilters(filters) {
        const keys = Object.keys(filters || {});
        if (keys.length === 0) return true;  // "all" preset, no other filters
        return keys.some(k => k !== "review_action");
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
        // Optional callback — invoked by saveDecision() when the
        // save path advances (Approve & Next / Defer / Reject /
        // Request Creation). Controller calls get_next_pending and
        // selects the next row. Signature: (decision_name) => void.
        this.on_advance_requested = opts.on_advance_requested || null;
        // Optional callback — invoked by undoLastSave() on success so
        // the controller can re-select the reverted row in the master
        // pane (important when the save had auto-advanced the
        // reviewer away from it). Signature: (decision_name) => void.
        this.on_undo_success = opts.on_undo_success || null;
        // Optional callback — fires when the "Show All Decisions"
        // link is clicked from the all-resolved empty state.
        // Controller flips the filter preset. Signature: () => void.
        this.on_show_all_requested = opts.on_show_all_requested || null;
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
        // Undo-window tracking: set on every successful save, cleared
        // on timeout / successful undo / second-undo attempt. The
        // 5-second client window is enforced in undoLastSave() (the
        // backend has a 10-second TTL as defence against clock skew).
        this._last_saved_decision_name = null;
        this._last_saved_timestamp = 0;
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
        this._last_saved_decision_name = null;
        this._last_saved_timestamp = 0;
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
        this._wire_audit_actions();
    }

    /**
     * Wire the Section 6 "Open in full form" button.
     *
     * History: this bench redirects ``/app/<doctype>/<name>`` →
     * ``/desk/<doctype>/<name>``, but the desk router on this bench
     * treats the first path segment after ``/desk/`` as a Page name
     * (legacy Frappe convention) and 404s on DocType slugs.
     * ``frappe.utils.get_url_to_form`` returns the broken URL too —
     * it's a bench-level routing quirk, not a code bug.
     *
     * Workaround: ``frappe.set_route("Form", ...)`` uses Frappe's
     * internal in-SPA navigation, which works regardless of the URL
     * the address bar settles on. Trade-off: same-tab navigation —
     * the reviewer loses the new-tab UX but the link actually works.
     * Commit 6 fix.
     */
    _wire_audit_actions() {
        this.container.find(".open-full-form").off("click").on("click", (e) => {
            e.preventDefault();
            e.stopPropagation();
            const name = $(e.currentTarget).data("decision-name");
            if (name) frappe.set_route("Form", "Mapping Decision", name);
        });
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
        const matched = d.matched_rule || "";

        const matched_html = matched
            ? `<a href="/app/mapping-rule/${encodeURIComponent(matched)}" target="_blank" rel="noopener">${frappe.utils.escape_html(matched)} <span class="nav-icon">↗</span></a>`
            : `<span class="muted">(none)</span>`;

        // Supplier vs account polymorphism per Item 3 Commit 1a.
        // Supplier-tier rows (tier ∈ SUPPLIER_TIERS) show the proposed
        // Supplier + match score; account-tier rows show proposed Account
        // + confidence (original behaviour).
        const is_supplier = DetailPane.SUPPLIER_TIERS.has(tier);
        const proposed_rows_html = is_supplier
            ? DetailPane._render_supplier_proposal_rows(d, tier)
            : DetailPane._render_account_proposal_rows(d);

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

        // Proposed Dr/Cr row only shown when at least one is non-zero (mapper diagnostic).
        // Skipped on supplier-tier rows — amounts are aggregated per-supplier
        // at generator time (OIT / Advance JE), not emitted as proposed_dr/cr
        // on individual decisions.
        const proposed_amounts_html = !is_supplier && (d.proposed_dr || d.proposed_cr) ? `
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
                    ${proposed_rows_html}
                    <div class="kv-label">Matched Rule</div>
                    <div class="kv-value">${matched_html}</div>
                    ${proposed_amounts_html}
                </div>
                ${anti_pattern_html}
                ${excluded_html}
            </section>
        `;
    }

    /** Account-tier rows: Proposed Account + Confidence (pre-Item-3 behaviour). */
    static _render_account_proposal_rows(d) {
        const proposed = d.proposed_account || "";
        const confidence = typeof d.confidence === "number" ? d.confidence.toFixed(2) : "";
        const proposed_html = proposed
            ? `<a href="/app/account/${encodeURIComponent(proposed)}" target="_blank" rel="noopener">${frappe.utils.escape_html(proposed)} <span class="nav-icon">↗</span></a>`
            : `<span class="muted">(none)</span>`;
        return `
            <div class="kv-label">Proposed Account</div>
            <div class="kv-value">${proposed_html}</div>
            <div class="kv-label">Confidence</div>
            <div class="kv-value">${confidence}</div>
        `;
    }

    /** Supplier-tier rows: Proposed Supplier + match score chip, plus
     *  `new_supplier_name` hint for pending_supplier_creation rows.
     *
     *  Match-score chip colour thresholds (per Item 3 AMB-6):
     *    ≥ 0.95  green   "good"
     *    0.85 – 0.94  amber   "warn"
     *    <  0.85  grey    "muted"   (defensive — mapper threshold is 0.85)
     *
     *  For pending_supplier_creation (confidence == 0.0 by design) the
     *  match-score row is suppressed — "0%" on an unmatched row is
     *  misleading.
     */
    static _render_supplier_proposal_rows(d, tier) {
        const proposed = d.proposed_supplier || "";
        const score = typeof d.supplier_match_score === "number"
            ? d.supplier_match_score
            : 0.0;
        const new_name = d.new_supplier_name || "";

        const proposed_html = proposed
            ? `<a href="/app/supplier/${encodeURIComponent(proposed)}" target="_blank" rel="noopener">${frappe.utils.escape_html(proposed)} <span class="nav-icon">↗</span></a>`
            : `<span class="muted">(none)</span>`;

        const match_score_html = tier === "pending_supplier_creation"
            ? ""  // suppressed — confidence is 0.0 by design, not meaningful
            : `
                <div class="kv-label">Match Score</div>
                <div class="kv-value">
                    <span class="match-score ${DetailPane._match_score_class(score)}">${Math.round(score * 100)}%</span>
                </div>
            `;

        // For pending_supplier_creation rows, show the mapper's cleaned-name
        // suggestion so the reviewer has a starting point before opening the
        // Create-new dialog (Commit 1b / Commit 2).
        const new_name_html = (tier === "pending_supplier_creation" && new_name)
            ? `
                <div class="kv-label">Suggested Name</div>
                <div class="kv-value"><em>${frappe.utils.escape_html(new_name)}</em></div>
            `
            : "";

        return `
            <div class="kv-label">Proposed Supplier</div>
            <div class="kv-value">${proposed_html}</div>
            ${match_score_html}
            ${new_name_html}
        `;
    }

    /** Threshold bucket for supplier_match_score display. */
    static _match_score_class(score) {
        if (score >= 0.95) return "good";
        if (score >= 0.85) return "warn";
        return "muted";
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
                    <div class="action-validation-message" role="alert"></div>
                    <div class="action-button-row">
                        <button class="btn btn-default btn-sm action-defer-btn" type="button">
                            Defer
                        </button>
                        <button class="btn btn-default btn-sm action-reject-btn" type="button">
                            Reject
                        </button>
                        <button class="btn btn-default btn-sm action-request-creation-btn" type="button">
                            Request Creation
                        </button>
                        <button class="btn btn-primary btn-sm action-approve-btn" type="button" disabled>
                            Approve &amp; Next
                        </button>
                    </div>
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
                this._update_action_button_states();
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

        // Wire the four action button click handlers. Event-bound
        // directly to the button elements (not delegated) — _render()
        // replaces innerHTML between rows, so handlers die with the
        // DOM naturally. .off+on per click namespace defends against
        // any future code path that mutates without a full _render.
        this.container.find(".action-defer-btn")
            .off("click.mdr-defer").on("click.mdr-defer", () => this._onDefer());
        this.container.find(".action-reject-btn")
            .off("click.mdr-reject").on("click.mdr-reject", () => this._onReject());
        this.container.find(".action-request-creation-btn")
            .off("click.mdr-request").on("click.mdr-request", () => this._onRequestCreation());
        this.container.find(".action-approve-btn")
            .off("click.mdr-approve").on("click.mdr-approve", () => this._onApprove());

        // Button text polymorphism per row type (Item 3 Commit 1b, AMB-12).
        // Supplier rows → "Resolve Supplier" (opens rich dialog); account
        // rows → "Request Creation" (opens simple confirm, replaced by
        // Item 4 Commit).
        const is_vendor = this._isVendorRow();
        this.container.find(".action-request-creation-btn").text(
            is_vendor ? __("Resolve Supplier") : __("Request Creation")
        );

        // Initial button state — freshly loaded row is clean (Approve &
        // Next disabled until reviewer makes an edit; Defer/Reject/
        // Request Creation enabled regardless of dirty state).
        this._update_action_button_states();
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

    _update_action_button_states() {
        // Approve & Next mirrors the Commit-4b Save semantics: only
        // enabled when the form is dirty, since an "approve" with no
        // edits would either re-save the same Pending state (no-op)
        // or auto-flip an already-clean row into a fresh Approved
        // state without any reviewer signal that they reviewed it.
        // The dirty gate forces a meaningful interaction first.
        const approve = this.container.find(".action-approve-btn");
        if (approve.length) {
            approve.prop("disabled", this._saving || !this.isDirty());
        }
        // Defer / Reject / Request Creation are intentionally NOT
        // gated by isDirty(). Reviewers commonly want to reject or
        // defer a row they haven't touched — e.g. "this Tally ledger
        // is junk, no analysis needed". Only the in-flight save guard
        // applies, to block button-spam during the await.
        const others = this.container.find(
            ".action-defer-btn, .action-reject-btn, .action-request-creation-btn"
        );
        others.prop("disabled", this._saving);
    }

    /**
     * Persist Section 4 inputs via the save_decision whitelist method.
     *
     * Flow:
     *   1. Collect values, send to backend
     *   2. On success:
     *      - Stash Undo-window state (decision name + timestamp)
     *      - Show Undo toast with clickable link (5s window)
     *      - Notify controller for master-pane optimistic row update
     *      - Either: advance to next decision (opts.advance=true,
     *        via controller callback) OR reload current row (advance=false)
     *   3. On error: Frappe surfaces whitelist-method errors via its
     *      error dialog; we log and preserve state for retry
     *
     * The _saving guard blocks re-entry between click and await
     * resolution. opts.advance controls the post-save navigation —
     * Approve & Next / Defer / Reject / Request Creation all advance,
     * Save Without Advance stays put.
     *
     * @param {object} opts
     * @param {boolean} [opts.advance=false] - if true, fire
     *   on_advance_requested after save to move to next decision.
     */
    async saveDecision(opts = {}) {
        const { advance = false } = opts;
        if (this._saving) return;
        if (!this.isDirty()) return;  // button should be disabled; defensive.
        if (!this.current_decision_name) return;

        this._saving = true;
        this._update_action_button_states();

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

            // Stash Undo state BEFORE showing the toast — if the
            // reviewer slams Ctrl+Z immediately after the save
            // resolves, we want the state in place to handle it.
            this._last_saved_decision_name = decision_name;
            this._last_saved_timestamp = Date.now();

            // Notify the controller so it can update the master pane
            // row in place (optimistic UX — no list refetch).
            if (this.on_save_success) {
                this.on_save_success(decision_name, saved);
            }

            this._showSavedToastWithUndo();

            if (advance && this.on_advance_requested) {
                // Controller walks get_next_pending + selects the next
                // row via master_pane.selectByName, which re-fires
                // on_selection_change → this.loadDecision. We don't
                // reload current row because we're moving away from it.
                this.on_advance_requested(decision_name);
            } else {
                // Stay on current row — reload so the chronology block,
                // modified_by / modified timestamps, and any server-
                // derived value transforms are visible to the reviewer.
                // Force re-fetch by clearing the idempotency guard.
                this.current_decision_name = null;
                await this.loadDecision(decision_name);
            }
        } catch (err) {
            // Frappe surfaces whitelist-method errors automatically via
            // its error dialog (frappe.call handles the response
            // shape). All we do here is log for dev debugging and
            // preserve dirty state so the reviewer can retry.
            console.error("md-review: save_decision failed", err);
        } finally {
            this._saving = false;
            this._update_action_button_states();
        }
    }

    /**
     * Post-save Undo toast. Green "Saved" alert with a clickable
     * "Undo" link that triggers undoLastSave() for the 5-second
     * window per §1.7. Ctrl+Z is bound separately (in on_page_load)
     * and calls undoLastSave directly — both paths converge on the
     * same method.
     *
     * Uses frappe.show_alert's return value (a jQuery element) to
     * bind the click handler post-render. The toast auto-dismisses
     * at 5s; if the reviewer clicks Undo, we also explicitly remove
     * the toast (so it doesn't sit around showing a now-stale link).
     */
    _showSavedToastWithUndo() {
        const undo_html = `<a class="mdr-undo-link" style="cursor:pointer;text-decoration:underline;margin-left:8px;color:inherit">${__("Undo")}</a>`;
        const alert_el = frappe.show_alert(
            {
                message: __("Saved") + undo_html,
                indicator: "green",
            },
            5,
        );
        if (alert_el && alert_el.find) {
            alert_el.find(".mdr-undo-link").on("click", (e) => {
                e.preventDefault();
                e.stopPropagation();
                this.undoLastSave();
                alert_el.remove();
            });
        }
    }

    /**
     * Revert the most recent save via the undo_decision backend.
     *
     * Client-side 5-second window check (vs the 10-second backend TTL
     * — the backend buffer covers clock skew). On success:
     *   - Reloads the decision so the pre-save values re-render
     *   - Notifies master pane so the row indicator reverts
     *   - Fires on_undo_success so the controller can re-select the
     *     reverted row (important: if the save triggered an advance,
     *     the reviewer is now on the next row — undo should bring
     *     them back to the original one)
     *
     * Single-use: _last_saved_decision_name is cleared after the
     * attempt, so a second Ctrl+Z / Undo-click is a no-op.
     */
    async undoLastSave() {
        if (!this._last_saved_decision_name) return;
        const age = Date.now() - (this._last_saved_timestamp || 0);
        if (age > 5000) {
            frappe.show_alert(
                { message: __("Too late to undo"), indicator: "orange" },
                3,
            );
            this._last_saved_decision_name = null;
            return;
        }

        const decision_name = this._last_saved_decision_name;
        // Clear stash BEFORE the RPC so parallel Ctrl+Z spam can't
        // double-fire the undo against a cache entry that's already
        // been consumed (backend would return "Too late to undo"
        // but a client-side guard is cleaner).
        this._last_saved_decision_name = null;

        try {
            const r = await frappe.call({
                method: "rgi_migration.rgi_migration.page.md_review.md_review.undo_decision",
                args: { decision_name: decision_name },
            });
            const undone = r.message || {};

            frappe.show_alert({ message: __("Undone"), indicator: "blue" }, 3);

            if (this.on_save_success) {
                this.on_save_success(decision_name, undone);
            }
            if (this.on_undo_success) {
                // Controller re-selects the row in the master pane so
                // the reviewer sees their revert. If they'd already
                // auto-advanced, this brings them back.
                this.on_undo_success(decision_name);
            }

            // Reload detail to show the reverted values. If
            // on_undo_success already triggered a selectByName that
            // re-fired loadDecision, this is a no-op (idempotent).
            if (this.current_decision_name !== decision_name) {
                this.current_decision_name = null;
                await this.loadDecision(decision_name);
            }
        } catch (err) {
            console.error("md-review: undo_decision failed", err);
        }
    }

    // ---- Validation (Approve & Next only per §1.6) -----------------------

    _validateApprove() {
        const fa = this.section4_controls.final_account.get_value() || "";
        const ra = this.section4_controls.review_action.get_value() || "";

        // Order matters — final_account missing is the more actionable
        // message, surface it first.
        if (!fa) {
            return {
                ok: false,
                message: __("Pick a Final Account before approving."),
            };
        }
        if (DetailPane.PENDING_REVIEW_STATES.has(ra)) {
            return {
                ok: false,
                message: __(
                    "Review Action is still Pending — pick a terminal state (or trigger auto-flip by changing Final Account).",
                ),
            };
        }
        return { ok: true };
    }

    _showValidationMessage(msg) {
        // Inline under the button row (§1.6). Phase C reserved the
        // slot; this fills it. :empty CSS collapses the slot when
        // cleared, avoiding layout-shift.
        this.container.find(".action-validation-message").text(msg);
    }

    _clearValidationMessage() {
        this.container.find(".action-validation-message").empty();
    }

    // ---- Focus + discard helpers (for Enter / Esc shortcuts) -------------

    /**
     * Move focus to the Final Account input — the Enter shortcut's
     * landing target when focus is in the master pane list.
     * §1.6 Additional shortcuts table.
     */
    focusFinalAccount() {
        const control = this.section4_controls.final_account;
        if (!control) return;
        const $input = control.$input;
        if ($input && $input.length) $input.focus();
    }

    /**
     * Revert all three writable fields to their row-load originals.
     * Bound to Esc per §1.6 ("discard unsaved field changes, keep row
     * selection"). The _loading_controls guard prevents the
     * programmatic set_value calls from triggering auto-flip or
     * dirty-state-change chatter.
     */
    discardChanges() {
        if (!this.section4_controls.review_action) return;
        this._loading_controls = true;
        try {
            this.section4_controls.review_action.set_value(
                this._original_review_action || "Pending",
            );
            this.section4_controls.final_account.set_value(
                this._original_final_account || "",
            );
            this.section4_controls.reviewer_notes.set_value("");
        } finally {
            this._loading_controls = false;
        }
        this._clearValidationMessage();
        this._update_action_button_states();
    }

    // ---- All-resolved empty state (§1.9 case 3) --------------------------

    /**
     * Render the detail pane's "all resolved" empty state. Triggered
     * by the controller when get_next_pending returns null AND
     * filtered_count is 0 — reviewer has exhausted the filter.
     * Master pane stays visible (showing its own "no decisions match"
     * row); detail pane shows celebration + actions.
     */
    showAllResolvedEmpty() {
        // Clear tracked state — subsequent row selection will
        // re-populate via loadDecision.
        this.current_decision_name = null;
        this.current_decision = null;
        this.section4_controls = {};
        this._last_saved_decision_name = null;

        this.container.html(`
            <div class="detail-all-resolved">
                <div class="detail-all-resolved-icon">\u{1F389}</div>
                <div class="detail-all-resolved-title">${__("All decisions in the current filter are resolved.")}</div>
                <div class="detail-all-resolved-subtitle">${__("The session is ready to regenerate its four output artefacts.")}</div>
                <div class="detail-all-resolved-actions">
                    <button class="btn btn-default btn-sm detail-all-resolved-show-all">${__("Show All Decisions")}</button>
                </div>
            </div>
        `);

        // Wire the "Show All Decisions" click — fires the
        // on_show_all_requested callback so the controller can flip
        // the filter preset. Back-to-Session is intentionally
        // omitted in v1; a reviewer can use the breadcrumb.
        this.container.find(".detail-all-resolved-show-all")
            .off("click.mdr-showall")
            .on("click.mdr-showall", () => {
                if (this.on_show_all_requested) this.on_show_all_requested();
            });
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
        // A stale validation message (from a prior failed Approve) is
        // cleared on any edit — the error is now potentially resolved,
        // and a lingering red line is confusing.
        this._clearValidationMessage();
        this._update_action_button_states();
    }

    // ---- Action button handlers -----------------------------------------
    //
    // Five distinct save paths land in Phase D with diverging behaviour:
    //  • Approve & Next — validates first (final_account non-empty,
    //    review_action != Pending), then saves + advances
    //  • Defer / Reject — fires save with a forced review_action, then
    //    advances. No validation (reviewer is moving the row out of the
    //    way regardless of its prior state)
    //  • Request Creation — confirm dialog, then save with a tier-derived
    //    Pending* state (Account Creation vs Supplier Creation per A2)
    //  • Save Without Advance — verbatim save of current control values,
    //    no validation, no advance (the "preserve partial state" escape
    //    hatch reachable via the page Menu and Ctrl+Shift+S in Phase D)
    //
    // In Phase C the save call is identical for all paths (plain
    // saveDecision), with each handler differing only in (1) whether it
    // sets review_action first, and (2) Request Creation's confirm step.
    // Phase D adds validation + auto-advance + Undo toast on top.

    /** Vendor vs account derivation for the Request Creation button.
     *
     * §1.6 calls for Pending Account Creation OR Pending Supplier Creation
     * depending on whether the row is a vendor ledger. Per the §A2
     * default, we derive purely from the mapper's tier classification:
     * any tier that the supplier-resolution path produced means vendor.
     *
     * This avoids a parent-chain heuristic that would false-positive on
     * Asset ledgers under a "Sundry Creditors" group accidentally
     * (rare but real).
     */
    // Extended 2026-04-22 per Item 3 Commit 1a — tier1_supplier_exact and
    // tier1_supplier_alias were missing since Item 2's tier enum extension.
    // Without them, _isVendorRow() returned false for these rows and the
    // `c` / Request Creation path incorrectly routed them through the
    // account workflow.
    static SUPPLIER_TIERS = new Set([
        "pending_supplier_creation",
        "tier1_supplier_fuzzy",
        "tier1_supplier_exact",
        "tier1_supplier_alias",
    ]);

    _isVendorRow() {
        const tier = (this.current_decision && this.current_decision.tier) || "";
        return DetailPane.SUPPLIER_TIERS.has(tier);
    }

    /**
     * Programmatically set review_action and persist. Used by
     * Defer / Reject / Request Creation — they each force a specific
     * Pending or terminal state regardless of what the Select currently
     * shows. The set_value is wrapped in the _loading_controls guard so
     * the auto-flip onchange branch doesn't re-enter and overwrite the
     * forced value (auto-flip only triggers on final_account changes,
     * not review_action changes — but defensive against future drift).
     */
    async _saveWithAction(target_review_action) {
        if (!this.current_decision_name) return;
        if (this._saving) return;

        this._loading_controls = true;
        try {
            this.section4_controls.review_action.set_value(target_review_action);
        } finally {
            this._loading_controls = false;
        }
        // saveDecision reads the just-set review_action from the control.
        // It also requires isDirty() to be true — which it is now, since
        // review_action just changed from its original. Defer / Reject /
        // Request Creation all auto-advance per §1.6.
        await this.saveDecision({ advance: true });
    }

    async _onApprove() {
        // Supplier-row dispatch (Item 3 Commit 1b, AMB-9): the rich
        // dialog IS the approval path for supplier rows — final_account
        // is irrelevant on these, and the dialog performs its own
        // validation. The a-shortcut / Approve button on supplier rows
        // opens the dialog instead of going through the account-side
        // validate-and-save path.
        if (this._isVendorRow()) {
            this._openSupplierResolutionDialog();
            return;
        }
        // Validate first per §1.6 refinement 4. Short-circuit on
        // either failure — no save, no advance. Reviewer fixes input
        // and retries (validation message clears on next edit via
        // _on_section4_change).
        const validation = this._validateApprove();
        if (!validation.ok) {
            this._showValidationMessage(validation.message);
            return;
        }
        this._clearValidationMessage();
        await this.saveDecision({ advance: true });
    }

    async _onDefer() {
        await this._saveWithAction("Deferred");
    }

    async _onReject() {
        await this._saveWithAction("Rejected");
    }

    async _onRequestCreation() {
        // Supplier-row dispatch (Item 3 Commit 1b, AMB-8): rich dialog
        // on supplier rows; simple confirm on account rows (Item 4
        // replaces with a real Account Creation Request workflow).
        if (this._isVendorRow()) {
            this._openSupplierResolutionDialog();
            return;
        }
        const target_state = "Pending Account Creation";
        const label = "account creation request";
        frappe.confirm(
            __(`Flag this decision as a ${label}? The full creation workflow will be added in a later commit; for now this only marks the state.`),
            () => this._saveWithAction(target_state),
        );
    }

    /**
     * Open the rich supplier resolution dialog on the current
     * supplier-tier row (Item 3 Commit 1b).
     *
     * Caller is responsible for checking `_isVendorRow()` first — the
     * dialog makes no sense on account-tier rows. The dialog handles
     * its own save via save_supplier_resolution; on success, calls back
     * into this pane's auto-advance via `_onSupplierResolutionSaved`.
     */
    _openSupplierResolutionDialog() {
        if (this._saving) return;
        const d = this.current_decision;
        if (!d) return;

        const decision_name = this.current_decision_name;
        const dialog = new SupplierResolutionDialog({
            decision: d,
            onSaved: async (saved_doc, opts) =>
                this._onSupplierResolutionSaved(decision_name, saved_doc, opts),
        });
        dialog.show();
    }

    /**
     * Post-save callback from the supplier dialog. Parallel to
     * saveDecision's tail — updates master pane, shows the appropriate
     * toast, and auto-advances.
     *
     * Undo behaviour split by path (Item 3 Commit 2):
     * - Map-to-existing (``undoable=true``): cache snapshot was
     *   written server-side by save_supplier_resolution; shows the
     *   standard "Saved + Undo" toast; Ctrl+Z routes through the
     *   shape-agnostic undo_decision endpoint.
     * - Create-new (``undoable=false``): no cache snapshot was
     *   written (SCR creation has side effects that make clean Undo
     *   expensive); shows a plain "Saved" toast without Undo. Reviewer
     *   recovery is manual per the 2026-04-22 design decision.
     */
    async _onSupplierResolutionSaved(saved_decision_name, saved_doc, opts = {}) {
        const { undoable = true } = opts;

        if (undoable) {
            // Stash Undo state only when the save path actually cached
            // a snapshot. Otherwise Ctrl+Z / the toast's Undo would
            // either find a stale entry from an earlier save (wrong
            // decision) or hit the expired-window guard.
            this._last_saved_decision_name = saved_decision_name;
            this._last_saved_timestamp = Date.now();
        } else {
            // Defensive: clear any stale Undo state so Ctrl+Z on this
            // row doesn't revert a prior decision's save.
            this._last_saved_decision_name = null;
            this._last_saved_timestamp = 0;
        }

        if (this.on_save_success) {
            this.on_save_success(saved_decision_name, saved_doc || {});
        }

        if (undoable) {
            this._showSavedToastWithUndo();
        } else {
            this._showSavedToast();
        }

        if (this.on_advance_requested) {
            this.on_advance_requested(saved_decision_name);
        }
    }

    /** Plain "Saved" toast without Undo — used by Create-new path
     *  (Item 3 Commit 2). Intentionally no Undo button so the absence
     *  teaches reviewers that this save is non-reversible. */
    _showSavedToast() {
        frappe.show_alert(
            {
                message: __("Saved"),
                indicator: "green",
            },
            3,
        );
    }

    /**
     * Verbatim save of the current control values — no auto-flip
     * override (so whatever review_action the Select shows is what
     * gets saved). Phase D adds the Ctrl+Shift+S binding + Menu item;
     * here it's a method ready for those wirings.
     */
    async saveWithoutAdvance() {
        // Functionally equivalent to saveDecision in Phase C — the
        // distinction emerges in Phase D when Approve & Next gains
        // validation (which this method intentionally bypasses).
        await this.saveDecision();
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

        const decision_name = d.name || "";

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
                    <button class="btn btn-default btn-xs open-full-form" data-decision-name="${frappe.utils.escape_html(decision_name)}" type="button">
                        Open in full form <span class="nav-icon">↗</span>
                    </button>
                </div>
            </section>
        `;
    }
}


// ============================================================================
// Shortcut reference dialog — Commit 6
// ============================================================================
//
// Custom dialog rather than Frappe's built-in
// ``show_keyboard_shortcut_dialog`` because the built-in flattens all
// page-scoped shortcuts into one alphabetical list — useless for
// scanning. This mirrors §1.6's three-group structure (Action /
// Navigation / Editing) so reviewers see the row-action group
// together.
//
// If reviewers later want the built-in's broader coverage (Frappe-
// global shortcuts too), expose it via a "Show all Frappe shortcuts"
// link in the footer.

const SHORTCUT_REFERENCE = [
    {
        group: "Actions",
        bindings: [
            ["A",            "Approve & Next — on supplier rows, opens Resolve Supplier dialog"],
            ["R",            "Reject"],
            ["C",            "Request Creation — on supplier rows, opens Resolve Supplier dialog"],
            ["D",            "Defer"],
            ["Ctrl+S",       "Approve & Next (validating; works from notes)"],
            ["Ctrl+Shift+S", "Save Without Advance"],
            ["Ctrl+Z",       "Undo last save (10s window)"],
        ],
    },
    {
        group: "Navigation",
        bindings: [
            ["↑",        "Previous decision"],
            ["↓",        "Next decision"],
            ["Home",     "First decision"],
            ["End",      "Last decision"],
            ["Enter",    "Focus Final Account picker"],
        ],
    },
    {
        group: "Editing",
        bindings: [
            ["Esc",  "Discard unsaved changes"],
            ["?",    "Show this shortcut reference"],
        ],
    },
];

function showShortcutReferenceDialog() {
    const groups_html = SHORTCUT_REFERENCE.map(group => {
        const rows = group.bindings.map(([keys, desc]) => `
            <tr>
                <td style="width: 35%; white-space: nowrap;">
                    <kbd>${frappe.utils.escape_html(keys)}</kbd>
                </td>
                <td>${frappe.utils.escape_html(desc)}</td>
            </tr>
        `).join("");
        return `
            <h6 style="margin-top: 1rem;">${frappe.utils.escape_html(group.group)}</h6>
            <table class="table table-sm" style="margin-bottom: 0;">
                <tbody>${rows}</tbody>
            </table>
        `;
    }).join("");

    const dialog = new frappe.ui.Dialog({
        title: __("Keyboard shortcuts"),
        size: "small",
    });
    dialog.$body.html(`<div class="mdr-shortcut-reference">${groups_html}</div>`);
    dialog.show();
}


// ============================================================================
// Bulk-Approve Tier-1 handler — Commit 6 (Path B per WEEK4_DEFERRED_ITEMS.md)
// ============================================================================

/**
 * Two-phase bulk-approve flow: dry_run preview → confirm → live → toast.
 *
 * Why dry_run instead of a separate count endpoint: the eligibility
 * filter is non-trivial (4 checks across tier / review_action /
 * proposed_account / final_account) and lives server-side in
 * ``query.is_bulk_approve_eligible``. A separate count would risk
 * preview/execute drift if the predicate changed. Same code path,
 * different output shape.
 */
async function bulkApproveTier1Handler(session_name, master_pane, detail_pane) {
    let preview;
    try {
        const r = await frappe.call({
            method: "rgi_migration.rgi_migration.page.md_review.md_review.bulk_approve_tier1",
            args: { session_name: session_name, dry_run: true },
        });
        preview = r.message || {};
    } catch (err) {
        console.error("md-review: bulk_approve_tier1 dry_run failed", err);
        frappe.show_alert({
            message: __("Could not preview bulk approval — see console."),
            indicator: "red",
        }, 7);
        return;
    }

    const eligible_count = preview.eligible_count || 0;
    if (eligible_count === 0) {
        frappe.msgprint({
            title: __("Nothing to bulk-approve"),
            message: __(
                "No eligible tier-1 matches in this session. Bulk approval " +
                "only applies to tier-1 (exact / rule / pattern) matches " +
                "that are still pending and have a proposed account."
            ),
            indicator: "blue",
        });
        return;
    }

    const message = __(
        "Approve {0} tier-1 matches in {1}?",
        [`<strong>${eligible_count}</strong>`, `<strong>${frappe.utils.escape_html(session_name)}</strong>`]
    );
    const detail_message = __(
        "This sets <code>review_action = Approved</code> and " +
        "<code>final_account = proposed_account</code> on each eligible row. " +
        "Reviewer notes are left untouched. Skips any row where the reviewer " +
        "has already typed a manual Final Account or marked the row resolved."
    );

    frappe.confirm(
        `${message}<br><br><span class="text-muted">${detail_message}</span>`,
        async () => {
            // Live execute. Re-runs the eligibility filter server-side,
            // so any rows the reviewer touched between preview and confirm
            // are correctly skipped (count may differ from preview).
            let result;
            try {
                const r = await frappe.call({
                    method: "rgi_migration.rgi_migration.page.md_review.md_review.bulk_approve_tier1",
                    args: { session_name: session_name },
                    freeze: true,
                    freeze_message: __("Bulk-approving tier-1 matches…"),
                });
                result = r.message || {};
            } catch (err) {
                console.error("md-review: bulk_approve_tier1 failed", err);
                frappe.show_alert({
                    message: __("Bulk approval failed — see console."),
                    indicator: "red",
                }, 7);
                return;
            }

            const approved_count = (result.approved || []).length;
            const failed = result.failed || [];
            const failed_count = failed.length;

            // Refresh master pane — approved rows drop out of the
            // Pending filter, indicator dots flip to green under "all"
            // preset. Detail pane reloads via the standard selection
            // change path.
            master_pane.pagination_start = 0;
            await master_pane.loadDecisions();
            const selected = master_pane.getSelectedDecisionName();
            if (selected) {
                detail_pane.current_decision_name = null;
                await detail_pane.loadDecision(selected);
            } else {
                detail_pane.clear();
            }

            if (failed_count === 0) {
                frappe.show_alert({
                    message: __("Approved {0} tier-1 matches.", [approved_count]),
                    indicator: "green",
                }, 6);
                return;
            }

            // Partial failure — surface the failed names + reasons in a
            // follow-up dialog so the reviewer can hand-fix each one.
            const failed_rows = failed.map(f => `
                <tr>
                    <td><a href="/app/mapping-decision/${encodeURIComponent(f.name)}" target="_blank">${frappe.utils.escape_html(f.name)}</a></td>
                    <td>${frappe.utils.escape_html(f.reason || "")}</td>
                </tr>
            `).join("");
            frappe.msgprint({
                title: __("Bulk approval partial — {0} approved, {1} failed",
                    [approved_count, failed_count]),
                message: `
                    <p>${__("These rows could not be saved and need manual attention:")}</p>
                    <table class="table table-bordered" style="font-size: 12px">
                        <thead><tr><th>${__("Decision")}</th><th>${__("Reason")}</th></tr></thead>
                        <tbody>${failed_rows}</tbody>
                    </table>
                `,
                indicator: "orange",
                wide: true,
            });
        }
    );
}


// ============================================================================
// last_session helpers — Commit 6 (§1.1 OQ5 resolution)
// ============================================================================
//
// Reviewers auditing across multiple entities want to land back on the
// session they were last reviewing rather than a blank /app/md-review.
// We persist last_session into Frappe's user_settings store, which
// round-trips to the server on save and reloads from frappe.boot on
// next login. Namespace string ("Mapping Decision Review") matches the
// design-doc spec verbatim.

const MDR_USER_SETTINGS_KEY = "Mapping Decision Review";

function readLastSessionFromUserSettings() {
    const us = frappe.model.user_settings[MDR_USER_SETTINGS_KEY];
    return (us && us.last_session) || null;
}

function writeLastSessionToUserSettings(session_name) {
    if (!session_name) return;
    frappe.model.user_settings.save(
        MDR_USER_SETTINGS_KEY,
        "last_session",
        session_name
    );
}

/**
 * Render §1.9 case 1 — "Session not found." Mounts into the page body
 * (replacing the split-pane shell). Used for both invalid-segment and
 * fell-through-from-no-segment cases.
 */
function renderSessionMissingEmptyState(page, session_name_attempted) {
    const message = session_name_attempted
        ? __("The session {0} doesn't exist on this bench.",
             [`<code>${frappe.utils.escape_html(session_name_attempted)}</code>`])
        : __("No session selected and no recent session found.");
    page.body.empty().append(`
        <div class="mdr-empty-state mdr-empty-state-session-missing">
            <h4>${__("Session not found.")}</h4>
            <p class="text-muted">${message}</p>
            <p class="text-muted">${__("Pick a session from the list, or go back to the Session form view.")}</p>
            <button class="btn btn-primary mdr-empty-state-go-list">
                ${__("Go to Session list")}
            </button>
        </div>
    `);
    page.body.find(".mdr-empty-state-go-list").on("click", () => {
        frappe.set_route("List", "Tally Migration Session");
    });
}


// ============================================================================
// SupplierResolutionDialog — Item 3 Commit 1b rich dialog
// ============================================================================
//
// Modal dialog for supplier-tier decisions. Two radio paths:
//
// 1. "Map to existing supplier" — supplier autocomplete picker wired
//    to supplier_query backend. Submit calls save_supplier_resolution,
//    which sets final_supplier + tier=tier1_supplier_exact +
//    review_action=Approved + reviewer_notes (chronology-header).
//    Caller's onSaved callback handles Undo + auto-advance.
//
// 2. "Create new supplier" — disabled in Commit 1b (per Sub-AMB-8 β).
//    Helper text redirects reviewer to Defer on un-resolvable rows
//    until Commit 2 wires the SCR child row insertion.
//
// Radio default based on current-state (Sub-AMB-6):
//   final_supplier set    → Map-to-existing, picker pre-filled
//   proposed_supplier set → Map-to-existing, picker pre-filled
//   else                  → Create-new (which is disabled; reviewer
//                           reads the banner and cancels + Defers)

class SupplierResolutionDialog {
    static MAP_RADIO = "Map to existing supplier";
    static CREATE_RADIO = "Create new supplier";

    constructor({ decision, onSaved }) {
        this.decision = decision;
        this.onSaved = onSaved || (() => {});
        this._dialog = null;
    }

    /** Compute initial radio selection per Sub-AMB-6 current-state defaults. */
    _initial_radio() {
        const d = this.decision || {};
        if (d.final_supplier || d.proposed_supplier) {
            return SupplierResolutionDialog.MAP_RADIO;
        }
        return SupplierResolutionDialog.CREATE_RADIO;
    }

    /** Compute initial picker value — reviewer's prior pick wins over mapper. */
    _initial_supplier() {
        const d = this.decision || {};
        return d.final_supplier || d.proposed_supplier || "";
    }

    show() {
        const d = this.decision || {};
        const initial_radio = this._initial_radio();
        const initial_supplier = this._initial_supplier();

        // Context line — tally name + parent chain + opening balance
        // for reviewer orientation; matches the detail-pane Section 1
        // information without duplicating the whole section.
        const parent_chain = d.tally_parent_chain || "";
        const opening = d.opening_cr || d.opening_dr || 0;
        const context_html = `
            <div class="supplier-dialog-context">
                <div class="sdc-label">Tally ledger</div>
                <div class="sdc-value">${frappe.utils.escape_html(d.tally_name || "")}${
                    d.tally_id ? ` <span class="sdc-id">[tally_id=${frappe.utils.escape_html(d.tally_id)}]</span>` : ""
                }</div>
                ${parent_chain ? `
                    <div class="sdc-label">Parent chain</div>
                    <div class="sdc-value">${frappe.utils.escape_html(parent_chain)}</div>
                ` : ""}
                <div class="sdc-label">Opening</div>
                <div class="sdc-value">${frappe.format(opening, { fieldtype: "Currency" })} ${d.net_side || ""}</div>
            </div>
        `;

        this._dialog = new frappe.ui.Dialog({
            title: __("Resolve Supplier"),
            fields: [
                {
                    fieldtype: "HTML",
                    fieldname: "context_block",
                    options: context_html,
                },
                {
                    fieldtype: "Section Break",
                },
                {
                    fieldtype: "Select",
                    fieldname: "resolution_path",
                    label: __("Resolution"),
                    options: [
                        SupplierResolutionDialog.MAP_RADIO,
                        SupplierResolutionDialog.CREATE_RADIO,
                    ].join("\n"),
                    default: initial_radio,
                    reqd: 1,
                    change: () => this._on_radio_change(),
                },
                {
                    fieldtype: "Link",
                    fieldname: "final_supplier",
                    label: __("Supplier"),
                    options: "Supplier",
                    default: initial_supplier,
                    get_query: () => ({
                        query: "rgi_migration.rgi_migration.page.md_review.md_review.supplier_query",
                    }),
                    depends_on: `eval:doc.resolution_path === "${SupplierResolutionDialog.MAP_RADIO}"`,
                },
                {
                    fieldtype: "Data",
                    fieldname: "proposed_supplier_name",
                    label: __("Proposed Supplier Name"),
                    default: d.new_supplier_name || d.tally_name || "",
                    depends_on: `eval:doc.resolution_path === "${SupplierResolutionDialog.CREATE_RADIO}"`,
                    mandatory_depends_on: `eval:doc.resolution_path === "${SupplierResolutionDialog.CREATE_RADIO}"`,
                    description: __("Used as the Supplier's display name once the SCR is approved."),
                },
                {
                    fieldtype: "Data",
                    fieldname: "supplier_group",
                    label: __("Supplier Group"),
                    default: "",
                    depends_on: `eval:doc.resolution_path === "${SupplierResolutionDialog.CREATE_RADIO}"`,
                    description: __("Optional here — SCR approval will require it before the Supplier record can be created."),
                },
                {
                    fieldtype: "HTML",
                    fieldname: "create_new_note",
                    options: `
                        <div class="supplier-dialog-note">
                            ${__("A Supplier Creation Request will be added to this session. The decision stays Pending Supplier Creation until an approver resolves the request (Item 3 Commit 3). This action does NOT participate in Undo.")}
                        </div>
                    `,
                    depends_on: `eval:doc.resolution_path === "${SupplierResolutionDialog.CREATE_RADIO}"`,
                },
                {
                    fieldtype: "Section Break",
                },
                {
                    fieldtype: "Small Text",
                    fieldname: "reviewer_notes",
                    label: __("Reviewer notes"),
                    description: __("Appended to chronology — prior notes preserved above the new entry."),
                },
            ],
            primary_action_label: __("Save"),
            primary_action: () => this._on_submit(),
            secondary_action_label: __("Cancel"),
        });

        this._dialog.show();
        // Hide Save button initially if Create-new is the default (disabled).
        this._update_submit_state();
    }

    _on_radio_change() {
        this._update_submit_state();
    }

    _update_submit_state() {
        // Item 3 Commit 2: both Map-to-existing and Create-new paths
        // submit; the previous Commit 1b disabled-state on Create-new
        // is removed. Primary button always enabled (field-level
        // mandatory_depends_on on proposed_supplier_name catches the
        // empty-name case for Create-new).
        if (!this._dialog) return;
        const btn = this._dialog.get_primary_btn();
        if (!btn || !btn.length) return;
        btn.prop("disabled", false);
        btn.removeAttr("title");
    }

    async _on_submit() {
        const values = this._dialog.get_values();
        if (!values) return;  // Dialog's own validation rejected (required fields)

        if (values.resolution_path === SupplierResolutionDialog.CREATE_RADIO) {
            await this._submit_create_new(values);
        } else {
            await this._submit_map_to_existing(values);
        }
    }

    async _submit_map_to_existing(values) {
        if (!values.final_supplier) {
            frappe.msgprint({
                title: __("Pick a supplier"),
                message: __("Select a Supplier from the dropdown."),
                indicator: "orange",
            });
            return;
        }
        try {
            const r = await frappe.call({
                method: "rgi_migration.rgi_migration.page.md_review.md_review.save_supplier_resolution",
                args: {
                    decision_name: this.decision.name,
                    final_supplier: values.final_supplier,
                    reviewer_notes: values.reviewer_notes || "",
                },
            });
            const saved_doc = r.message || {};
            this._dialog.hide();
            // Item 3 Commit 1b: Map-to-existing participates in Undo.
            await this.onSaved(saved_doc, { undoable: true });
        } catch (err) {
            // eslint-disable-next-line no-console
            console.error("save_supplier_resolution failed", err);
        }
    }

    async _submit_create_new(values) {
        // Item 3 Commit 2: Create-new path. Submit creates an SCR
        // child row on the session and flags the decision as
        // Pending Supplier Creation. Explicitly NOT undoable per the
        // 2026-04-22 design decision — child-row insert has side
        // effects that make clean Undo expensive without proportional
        // UX benefit. The caller's onSaved hook is invoked with
        // `undoable: false` so the toast path skips the Undo button.
        const proposed_name = (values.proposed_supplier_name || "").trim();
        if (!proposed_name) {
            frappe.msgprint({
                title: __("Proposed name required"),
                message: __("Type the supplier's name before submitting Create-new."),
                indicator: "orange",
            });
            return;
        }
        try {
            const r = await frappe.call({
                method: "rgi_migration.rgi_migration.page.md_review.md_review.create_supplier_creation_request",
                args: {
                    decision_name: this.decision.name,
                    proposed_supplier_name: proposed_name,
                    supplier_group: (values.supplier_group || "").trim(),
                    reviewer_notes: values.reviewer_notes || "",
                },
            });
            const resp = r.message || {};
            this._dialog.hide();
            await this.onSaved(resp.decision || {}, { undoable: false });
        } catch (err) {
            // eslint-disable-next-line no-console
            console.error("create_supplier_creation_request failed", err);
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

    // No-segment route handling per §1.1 / §1.9 case 1a:
    //   1. Read last_session from user_settings.
    //   2. If present + still exists → frappe.set_route to it (which
    //      re-fires this on_page_load with the segment present).
    //   3. Otherwise → render §1.9 case 1 empty state ("session not
    //      found"), so the reviewer has a click-through to pick.
    // Done before any DOM scaffold so the empty state owns page.body.
    if (!session_name) {
        const last = readLastSessionFromUserSettings();
        if (last) {
            frappe.db.exists("Tally Migration Session", last).then(exists => {
                if (exists) {
                    frappe.set_route("md-review", last);
                } else {
                    renderSessionMissingEmptyState(page, last);
                }
            });
        } else {
            renderSessionMissingEmptyState(page, null);
        }
        return;
    }

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

    // Persist this session as the last-viewed for the current user.
    // Fire-and-forget — server roundtrip; not awaited because the
    // value is only consumed by future page loads.
    writeLastSessionToUserSettings(session_name);

    // Switch Session dropdown — Link picker in the page form area
    // (above the split pane) per §1.1. Link autocomplete naturally
    // sorts by modified desc and respects read permissions, which
    // covers the "recent sessions" intent without a custom query.
    const switch_session_field = page.add_field({
        fieldname: "switch_session",
        label: __("Switch Session"),
        fieldtype: "Link",
        options: "Tally Migration Session",
        change() {
            const target = switch_session_field.get_value();
            if (target && target !== session_name) {
                // Clear before navigating so the field doesn't briefly
                // show the old value if the reviewer hits Back.
                switch_session_field.set_value("");
                frappe.set_route("md-review", target);
            }
        },
    });

    // DetailPane instantiates FIRST so the controller's on_selection_change
    // callback can close over it. The detail-pane container is already in
    // the DOM (from the shell above); DetailPane replaces its contents with
    // the empty-state placeholder, then with rendered sections on row
    // selection.
    //
    // Four callbacks bridge DetailPane → MasterPane + FilterBar without
    // the DetailPane holding direct references (keeps it independently
    // constructible / testable). Declared as forward references (let)
    // because master_pane + filter_bar aren't in scope until a few
    // lines later; the closures capture the outer bindings which are
    // filled in before any callback fires.
    let master_pane;
    let filter_bar;
    const detail_pane = new DetailPane(
        container.find(".decision-detail-pane"),
        {
            // After save — update the master-pane row's indicator dot
            // + proposed-account cell in place (optimistic, no list
            // refetch). Also used by undoLastSave with the reverted
            // decision dict.
            on_save_success: (name, saved) => {
                if (master_pane) master_pane.updateRow(name, saved);
            },

            // After a save that should advance — call get_next_pending
            // server-side with the current filter + position, select
            // the next row (triggers loadDecision via
            // on_selection_change). If no next exists AND the filter
            // is now empty → render the all-resolved empty state;
            // otherwise reload the current row.
            on_advance_requested: async (name) => {
                if (!master_pane) return;
                const position = master_pane.selected_index;
                const filters = filter_bar ? filter_bar.getFilters() : {};
                try {
                    const r = await frappe.call({
                        method: "rgi_migration.rgi_migration.page.md_review.md_review.get_next_pending",
                        args: {
                            session_name: session_name,
                            after_decision_name: name,
                            after_position: position,
                            filters: filters,
                        },
                    });
                    const { next_decision_name, filtered_count } = r.message || {};
                    if (next_decision_name) {
                        // Advance. selectByName fires on_selection_change
                        // → detail_pane.loadDecision(next_decision_name).
                        master_pane.selectByName(next_decision_name);
                    } else if ((filtered_count || 0) === 0) {
                        // All resolved under the current filter.
                        detail_pane.showAllResolvedEmpty();
                    } else {
                        // End-of-list but rows remain (e.g. reviewer
                        // was already on the last Pending row and
                        // moved it out of filter). Stay on the current
                        // row — reload to show the just-saved state.
                        detail_pane.current_decision_name = null;
                        await detail_pane.loadDecision(name);
                    }
                } catch (err) {
                    console.error("md-review: get_next_pending failed", err);
                    // Fallback: reload current row so the reviewer at
                    // least sees the save landed. Better than a silent
                    // freeze after a network blip.
                    detail_pane.current_decision_name = null;
                    await detail_pane.loadDecision(name);
                }
            },

            // Undo re-selects the row the reviewer was on before auto-
            // advance (so the revert is visible on the same row they
            // just came from). selectByName → loadDecision reload.
            on_undo_success: (name) => {
                if (master_pane) master_pane.selectByName(name);
            },

            // "Show All Decisions" link on the all-resolved empty
            // state. Flips the filter preset to "all" and reloads.
            on_show_all_requested: () => {
                if (filter_bar) filter_bar.setPreset("all");
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

        // §1.9 case 4 — "Clear filters" CTA from the master-pane
        // empty-state. Resets FilterBar to its default Pending preset
        // and reloads. Wired here (not in MasterPane) because only
        // on_page_load has the FilterBar reference.
        on_clear_filters: () => {
            if (!filter_bar) return;
            filter_bar.reset();
        },

        // §1.9 case 3 — "Show All Decisions" CTA from the master-pane
        // all-resolved empty-state. Mirrors the DetailPane variant
        // (on_show_all_requested) so both entry points behave the
        // same.
        on_show_all: () => {
            if (filter_bar) filter_bar.setPreset("all");
        },
    };

    filter_bar = new FilterBar(container.find(".filter-bar-container"));
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

    // Action-bar keyboard shortcuts per §1.6. Split into two groups
    // by ignore_inputs behaviour:
    //
    //   - Ctrl-combos (ctrl+s, ctrl+shift+s, ctrl+z) use
    //     ignore_inputs: true — reviewers spend most of their time
    //     with focus in the reviewer_notes textarea, and these must
    //     fire from there without requiring a blur.
    //   - Single letters (a, r, c, d) use ignore_inputs: false so
    //     typing letters in the textarea doesn't trigger them.
    //
    // Ctrl+S now routes through the validating Approve & Next handler
    // per §1.6 ("Ctrl+S in v1 calls the SAME handler as the `a`
    // button — meaning Ctrl+S also validates"). Commit 4b's plain
    // save binding is replaced here.

    frappe.ui.keys.add_shortcut({
        shortcut: "ctrl+s",
        action: () => {
            if (!detail_pane._saving) detail_pane._onApprove();
        },
        description: __("Approve & Next"),
        page: page,
        ignore_inputs: true,
    });

    frappe.ui.keys.add_shortcut({
        shortcut: "ctrl+shift+s",
        action: () => {
            if (!detail_pane.isDirty() || detail_pane._saving) return;
            detail_pane.saveWithoutAdvance();
        },
        description: __("Save Without Advance"),
        page: page,
        ignore_inputs: true,
    });

    frappe.ui.keys.add_shortcut({
        shortcut: "ctrl+z",
        action: () => detail_pane.undoLastSave(),
        description: __("Undo last save"),
        page: page,
        ignore_inputs: true,
    });

    frappe.ui.keys.add_shortcut({
        shortcut: "a",
        action: () => {
            if (!detail_pane._saving) detail_pane._onApprove();
        },
        description: __("Approve & Next"),
        page: page,
        ignore_inputs: false,
    });

    frappe.ui.keys.add_shortcut({
        shortcut: "r",
        action: () => {
            if (!detail_pane._saving) detail_pane._onReject();
        },
        description: __("Reject"),
        page: page,
        ignore_inputs: false,
    });

    frappe.ui.keys.add_shortcut({
        shortcut: "c",
        action: () => {
            if (!detail_pane._saving) detail_pane._onRequestCreation();
        },
        description: __("Request Creation"),
        page: page,
        ignore_inputs: false,
    });

    frappe.ui.keys.add_shortcut({
        shortcut: "d",
        action: () => {
            if (!detail_pane._saving) detail_pane._onDefer();
        },
        description: __("Defer"),
        page: page,
        ignore_inputs: false,
    });

    // ? — keyboard-shortcut reference dialog. Renders the canonical
    // list of shortcuts registered on this page so reviewers don't
    // have to dig through the spec doc. Frappe's built-in
    // ``frappe.ui.keys.show_keyboard_shortcut_dialog`` picks up
    // standard-shortcut registrations but groups them generically;
    // our custom modal mirrors §1.6 grouping (Action / Navigation /
    // Editing) so it's actually useful at the keyboard. Bound under
    // ignore_inputs: false so typing literal "?" in a textarea
    // doesn't fire it.
    frappe.ui.keys.add_shortcut({
        shortcut: "shift+/",
        action: () => showShortcutReferenceDialog(),
        description: __("Show keyboard shortcuts"),
        page: page,
        ignore_inputs: false,
    });

    // Esc — discard unsaved field changes per §1.6. Only fires when
    // focus is inside the detail pane (not elsewhere on the page) so
    // it doesn't hijack Esc from, say, a Frappe dialog.
    frappe.ui.keys.add_shortcut({
        shortcut: "escape",
        action: () => {
            const detail_el = container.find(".decision-detail-pane")[0];
            if (detail_el && detail_el.contains(document.activeElement)) {
                detail_pane.discardChanges();
            }
        },
        description: __("Discard unsaved changes"),
        page: page,
        ignore_inputs: true,
    });

    // Save Without Advance — also exposed via the page's ⋮ Menu
    // dropdown per §1.6 ("exposed via a Save Without Advance menu
    // item"). Clicking is the mouse-equivalent of the Ctrl+Shift+S
    // binding above; same isDirty() + _saving guards.
    page.add_menu_item(__("Save Without Advance"), () => {
        if (!detail_pane.isDirty() || detail_pane._saving) return;
        detail_pane.saveWithoutAdvance();
    });

    // Bulk-Approve Tier-1 — Path B per docs/WEEK4_DEFERRED_ITEMS.md.
    // One-click action for the ~115 obvious tier-1 matches that would
    // otherwise eat ~6,800 unnecessary clicks across the 59-entity
    // project. Server-side eligibility filter excludes:
    //   - tier1_supplier_fuzzy (Items 3-4 supplier UX scope)
    //   - already-resolved rows
    //   - rows with no proposed_account
    //   - rows where the reviewer typed a manual final_account
    // Two-step UX: dry_run preview → confirm dialog with count +
    // session name → live execute → toast summary. Refreshes the
    // master pane on success so approved rows leave the Pending
    // filter and the indicator dots flip to green.
    page.add_inner_button(__("Bulk-Approve Tier-1"), () => {
        bulkApproveTier1Handler(session_name, master_pane, detail_pane);
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
            // Only useful when focus is in the master pane list — a
            // reviewer pressing Enter there wants to jump into the
            // detail pane's primary input (the Final Account picker)
            // so they can start typing an account name. If focus is
            // already in the detail pane (e.g. in reviewer_notes),
            // Enter is the natural newline character; let the input
            // keep it.
            if (container.find(".decision-detail-pane")[0].contains(document.activeElement)) {
                return;
            }
            detail_pane.focusFinalAccount();
        },
        description: __("Focus Final Account"),
        page: page,
    });
};
