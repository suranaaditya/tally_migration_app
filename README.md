# rgi_migration

Custom Frappe app that automates Tally ERP 9 → ERPNext v16 opening balance migration for the Raisoni Group of Institutions (59 entities).

See [CLAUDE.md](CLAUDE.md) for the full project spec.

## Current phase

**Week 1 — parser layer only.** Runnable as a plain Python module, no Frappe dependency yet.

```
rgi_migration/
├── parsers/
│   ├── normalized_schema.py    # shared output dataclasses
│   ├── tally_xml_parser.py     # primary source
│   └── tally_excel_parser.py   # fallback source
└── tests/
    ├── fixtures/               # anonymized real-entity samples
    └── test_parsers.py
```

## Setup

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# POSIX:
source .venv/bin/activate

pip install -e ".[dev]"
```

## Run tests

```bash
pytest
```

## CLI smoke test (once parsers are implemented)

```bash
python -m rgi_migration.parsers.tally_xml_parser <path-to-tally.xml>
python -m rgi_migration.parsers.tally_excel_parser <path-to-tb.xlsx>
```
