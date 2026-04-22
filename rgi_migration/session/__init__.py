"""Session-lifecycle package — parse/map/persist orchestration for a
``Tally Migration Session``.

Separated from the ``mapper`` package so that Frappe-free core logic
(parse + map + persist dict-building) lives here and the
``TallyMigrationSession`` DocType controller is the only Frappe wrapper.
"""
