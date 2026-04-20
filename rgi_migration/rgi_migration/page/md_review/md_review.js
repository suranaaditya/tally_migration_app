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

    // Commit 2 scope: log route parse for diagnostic only.
    // Commit 3+ will use these values to drive data loading.
    console.log("md-review loaded", {
        session_name: session_name,
        decision_hash: decision_hash
    });

    // Scaffold: 6/4 split-pane shell. Content lands in Commits 3-5.
    const container = $(`
        <div class="mapping-decision-review-app">
            <div class="decision-list-pane">
                <div class="pane-placeholder">Master pane — Commit 3</div>
            </div>
            <div class="decision-detail-pane">
                <div class="pane-placeholder">Detail pane — Commit 4</div>
            </div>
        </div>
    `).appendTo(page.body);

    // OQ5 resolution placeholder: no-session-name handling.
    // Commit 6 will add auto-redirect to user_settings.last_session + Switch Session dropdown.
    if (!session_name) {
        frappe.show_alert({
            message: "No session specified. Navigate from a Tally Migration Session form via the 'Review Decisions' button.",
            indicator: "orange"
        }, 7);
    }
};
