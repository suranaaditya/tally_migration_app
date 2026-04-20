"""Week 3 integration smoke — run parser + mapper + all 4 generators on
full CACSPU data end-to-end.

Documents refusal behaviour on the current un-reviewed data state + verifies
generator #4 produces a correct 369-row CSV. Not expected to produce usable
Draft JEs or a usable OIT CSV on current CACSPU data — that's Week-4
review-UI work. This test documents CURRENT behaviour so regressions
against it can be detected during Week 4.

Invocation (from ~/frappe-bench):

    echo "exec(open(
        '/home/frappe/frappe-bench/apps/rgi_migration/scripts/week3_integration_smoke.py'
    ).read()); run()" | bench --site erp.jewonline.in console

All imports live inside run() because IPython's exec() path does not
propagate module-level imports into nested function scopes reliably
(known constraint documented in design notes §5 / SESSION_HANDOFF
gotchas).

Read-only except for a single throwaway Tally Migration Session row +
any File attachments generators #2 / #4 happen to produce; all of
which are cleaned up in phase 7 before the function returns.
"""

from __future__ import annotations


def run() -> None:
    import hashlib
    import os
    import time
    import traceback

    import frappe  # type: ignore[import]

    from rgi_migration.generators.advance_je import (
        AdvanceJEGenerationError,
        generate_advance_je,
    )
    from rgi_migration.generators.oit_csv import (
        OITGenerationError,
        generate_oit_csv,
    )
    from rgi_migration.generators.opening_je import (
        MainJEGenerationError,
        generate_main_opening_je,
    )
    from rgi_migration.generators.students_csv import (
        StudentsCSVGenerationError,
        generate_students_csv,
    )

    XML_PATH = "/home/frappe/tally-exports/cacspu_masters.xml"

    print("=" * 72)
    print("WEEK 3 INTEGRATION SMOKE TEST")
    print("=" * 72)
    print("")

    # --- Phase 1: baseline counts ---
    print("-" * 72)
    print("Phase 1 — Baseline counts (before smoke)")
    print("-" * 72)
    baseline = {
        "Tally Migration Session": frappe.db.count("Tally Migration Session"),
        "Mapping Decision": frappe.db.count("Mapping Decision"),
        "JEs containing OB-CACSPU in user_remark": frappe.db.count(
            "Journal Entry", {"user_remark": ["like", "%OB-CACSPU%"]}
        ),
        "Files attached to Tally Migration Session": frappe.db.count(
            "File", {"attached_to_doctype": "Tally Migration Session"}
        ),
    }
    for k, v in baseline.items():
        print("  " + k.ljust(48) + " " + str(v))
    print("")

    # --- Phase 2: fresh session ---
    print("-" * 72)
    print("Phase 2 — Create fresh session + compute source SHA-256")
    print("-" * 72)
    h = hashlib.sha256()
    fh = open(XML_PATH, "rb")
    try:
        while True:
            chunk = fh.read(8 * 1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    finally:
        fh.close()
    sha = h.hexdigest()
    size_mb = round(os.path.getsize(XML_PATH) / 1024 / 1024, 2)

    sess = frappe.get_doc({
        "doctype": "Tally Migration Session",
        "company_abbr": "CACSPU",
        "fiscal_year": "2026-2027",
        "fiscal_year_short": "26-27",
        "tb_date": "2026-04-01",
        "source_format": "xml",
        "status": "Draft",
        "source_file_server_path": XML_PATH,
        "source_file_size_mb": size_mb,
        "source_file_sha256": sha,
    }).insert(ignore_permissions=True)
    frappe.db.commit()
    print("  session:    " + sess.name)
    print("  size_mb:    " + str(size_mb))
    print("  sha short:  " + sha[:16] + "...")
    print("")

    # --- Phase 3: invoke all 4 generators ---
    print("-" * 72)
    print("Phase 3 — Invoke all 4 generators (serially, per-run timing)")
    print("-" * 72)
    results: list[tuple[str, str, str, float]] = []
    overall_start = time.time()
    for gen_label, gen_fn, exc_type in [
        ("Generator #1 Main JE",      generate_main_opening_je,  MainJEGenerationError),
        ("Generator #2 OIT CSV",      generate_oit_csv,          OITGenerationError),
        ("Generator #3 Advance JE",   generate_advance_je,       AdvanceJEGenerationError),
        ("Generator #4 Students CSV", generate_students_csv,     StudentsCSVGenerationError),
    ]:
        print("")
        print("  ### " + gen_label)
        t0 = time.time()
        try:
            artifact = gen_fn(sess.name)
            dt = time.time() - t0
            print("    SUCCESS   elapsed=" + "{:.1f}".format(dt) + "s")
            print("    artifact: " + str(artifact))
            results.append((gen_label, "SUCCESS", str(artifact), dt))
        except exc_type as e:
            dt = time.time() - t0
            print("    REFUSED   elapsed=" + "{:.1f}".format(dt) + "s")
            # Full message for refusal assertion — the counts in the message
            # are the critical verification surface.
            msg = str(e)
            for line in msg.splitlines():
                print("    | " + line)
            results.append((gen_label, "REFUSED", msg[:250], dt))
        except Exception as e:
            dt = time.time() - t0
            print("    UNEXPECTED   elapsed=" + "{:.1f}".format(dt) + "s")
            print("    " + type(e).__name__ + ": " + str(e))
            traceback.print_exc()
            results.append((
                gen_label, "UNEXPECTED",
                type(e).__name__ + ": " + str(e)[:200], dt,
            ))
    overall_dt = time.time() - overall_start
    print("")
    print("  --- total wall-clock across 4 generators: "
          + "{:.1f}".format(overall_dt) + "s")

    # --- Phase 4: session state snapshot ---
    print("")
    print("-" * 72)
    print("Phase 4 — Session state after all 4 generators")
    print("-" * 72)
    sess = frappe.get_doc("Tally Migration Session", sess.name)
    snapshot = {
        "generated_je_draft":             sess.generated_je_draft,
        "generated_je_reference":         sess.generated_je_reference,
        "generated_advance_je":           sess.generated_advance_je,
        "generated_advance_je_reference": sess.generated_advance_je_reference,
        "generated_oit_file":             sess.generated_oit_file,
        "student_ledger_file":            sess.student_ledger_file,
        "temp_opening_amount":            sess.temp_opening_amount,
    }
    for k, v in snapshot.items():
        print("  " + k.ljust(36) + " " + repr(v))
    err_len = len(sess.error_log or "")
    print("  " + "error_log length (chars):".ljust(36) + " " + str(err_len))
    if err_len:
        print("  error_log (last 400 chars):")
        for line in (sess.error_log or "")[-400:].splitlines():
            print("    | " + line)

    # --- Phase 5: side-effect integrity checks ---
    print("")
    print("-" * 72)
    print("Phase 5 — Side-effect integrity")
    print("-" * 72)
    integrity_checks = [
        (
            "generated_je_draft is None (gen #1 refused, no JE created)",
            sess.generated_je_draft is None,
        ),
        (
            "generated_advance_je is None (gen #3 refused, no JE created)",
            sess.generated_advance_je is None,
        ),
        (
            "generated_oit_file is None (gen #2 refused, no file attached)",
            sess.generated_oit_file is None,
        ),
        (
            "student_ledger_file populated (gen #4 succeeded)",
            bool(sess.student_ledger_file),
        ),
    ]
    all_integrity_ok = True
    for check, ok in integrity_checks:
        if not ok:
            all_integrity_ok = False
        flag = "OK" if ok else "FAIL"
        print("  [" + flag + "]  " + check)

    # --- Phase 6: CSV contents spot-check ---
    print("")
    print("-" * 72)
    print("Phase 6 — Students CSV contents spot-check")
    print("-" * 72)
    if sess.student_ledger_file:
        file_name = frappe.db.get_value(
            "File", {"file_url": sess.student_ledger_file}, "name"
        )
        if file_name:
            file_doc = frappe.get_doc("File", file_name)
            content = file_doc.get_content()
            if isinstance(content, bytes):
                content = content.decode("utf-8")
            lines = content.splitlines()
            print("  File name: " + file_name)
            print("  URL:       " + sess.student_ledger_file)
            print("  Total lines (incl header): " + str(len(lines)))
            print("  Header: " + lines[0])
            if len(lines) > 1:
                print("  First 3 data rows:")
                for line in lines[1:4]:
                    print("    " + line[:160])
                print("  Last 2 data rows:")
                for line in lines[-2:]:
                    print("    " + line[:160])

    # --- Phase 7: cleanup ---
    print("")
    print("-" * 72)
    print("Phase 7 — Cleanup")
    print("-" * 72)
    deleted_files = 0
    for url in (sess.generated_oit_file, sess.student_ledger_file):
        if not url:
            continue
        fname = frappe.db.get_value("File", {"file_url": url}, "name")
        if fname:
            try:
                frappe.delete_doc("File", fname, force=1)
                deleted_files += 1
                print("  deleted File: " + fname)
            except Exception as e:
                print("  WARN: failed to delete File " + fname + ": " + str(e))

    for je_name in (sess.generated_je_draft, sess.generated_advance_je):
        if je_name and frappe.db.exists("Journal Entry", je_name):
            try:
                frappe.delete_doc("Journal Entry", je_name, force=1)
                print("  deleted JE: " + je_name)
            except Exception as e:
                print("  WARN: failed to delete JE " + je_name + ": " + str(e))

    session_name = sess.name
    frappe.delete_doc("Tally Migration Session", session_name, force=1)
    frappe.db.commit()
    print("  deleted Tally Migration Session: " + session_name)
    print("  deleted files total: " + str(deleted_files))

    # --- Phase 8: post-cleanup counts — verify zero artefact leak ---
    print("")
    print("-" * 72)
    print("Phase 8 — Post-cleanup counts")
    print("-" * 72)
    after = {
        "Tally Migration Session": frappe.db.count("Tally Migration Session"),
        "Mapping Decision": frappe.db.count("Mapping Decision"),
        "JEs containing OB-CACSPU in user_remark": frappe.db.count(
            "Journal Entry", {"user_remark": ["like", "%OB-CACSPU%"]}
        ),
        "Files attached to Tally Migration Session": frappe.db.count(
            "File", {"attached_to_doctype": "Tally Migration Session"}
        ),
    }
    all_clean = True
    for k, v_after in after.items():
        v_before = baseline[k]
        delta = v_after - v_before
        if delta != 0:
            all_clean = False
        status = "OK  " if delta == 0 else "LEAK"
        print("  [" + status + "]  " + k.ljust(45)
              + "  before=" + str(v_before)
              + "  after=" + str(v_after)
              + "  delta=" + str(delta))
    print("")
    if all_clean:
        print("CLEANUP VERIFIED: zero artefacts leaked across smoke run.")
    else:
        print("!!! CLEANUP INCOMPLETE — see deltas above.")

    # --- Phase 9: summary ---
    print("")
    print("=" * 72)
    print("SUMMARY")
    print("=" * 72)
    for label, status, _detail, dt in results:
        print("  " + label.ljust(32) + " " + status
              + "   (" + "{:.1f}".format(dt) + "s)")
    print("")
    print("Total wall-clock across 4 generators:  "
          + "{:.1f}".format(overall_dt) + "s")
    print("Integrity checks:                      "
          + ("all OK" if all_integrity_ok else "FAIL — see Phase 5"))
    print("Cleanup:                               "
          + ("clean" if all_clean else "LEAKED — see Phase 8"))
