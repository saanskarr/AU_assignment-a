# Assignment A: Interaction Ledger & Reporting Pipeline

A modular, idempotent data ingestion and reporting pipeline built for Agentic Universe's outbound voice campaigns (`motor_renewal` and `policy_feedback`).

Built using Python's standard library (`sqlite3`, `csv`, `json`, `hashlib`, `decimal`, `argparse`, `unittest`) so it runs out-of-the-box without external database servers.

---

## Project Structure

    assignment-a/
    ├── assignment-a-data/
    │   ├── schemas/
    │   │   ├── dispositions.csv
    │   │   ├── motor_renewal.json
    │   │   └── policy_feedback.json
    │   └── tools/
    │       └── generate_data.py
    ├── docs/
    │   └── GLOSSARY.md
    ├── src/
    │   ├── __init__.py
    │   ├── db.py               # SQLite connection, table schema, and stat counters
    │   ├── validators.py       # Header, phone, date, decimal, enum, and IST timestamp parsers
    │   ├── ingest.py           # Lead CSV & Event JSONL ingestion logic
    │   └── report.py           # Two-pass event linking, bucket logic, and CSV/JSON writers
    ├── tests/
    │   └── test_pipeline.py    # Unit tests for validation and normalisation rules
    ├── pipeline.py             # CLI entry point (ingest & report commands)
    ├── requirements.txt        # Python dependencies
    ├── SUBMISSION.md           # Design explanation, engineering decisions, and timings
    ├── HOW_IT_WORKS.md         # Step-by-step walkthrough of the pipeline flow
    ├── AI_USAGE.md             # AI usage disclosure
    └── README.md               # Project documentation and run guide

---

## How to Run the Whole Assignment (End-to-End)

Follow these exact steps from the root of the repository to set up the environment, generate the synthetic data, ingest both drops, generate the final reports, and run the test suite.

### Step 1: Set Up Environment & Install Dependencies
Requires **Python 3.9+**. Create a virtual environment (optional) and install `requirements.txt`:

    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt

### Step 2: Generate the Synthetic Data Drops
Run the synthetic data generator to create `data/drop_1` and `data/drop_2`:

    python3 assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data

*(Optional)* To test performance on the large dataset (~140k leads, ~600k events):

    python3 assignment-a-data/tools/generate_data.py --seed 42 --scale large --out data_large

### Step 3: Ingest Data Drops Incrementally
Ingest `drop_1` followed by `drop_2`. By default, the pipeline creates a local SQLite database (`pipeline.db`) and automatically loads workflow schemas from `assignment-a-data/schemas/`:

    # Start with a clean database if re-running from scratch
    rm -f pipeline.db

    # Ingest Drop 1 (Day 1 delivery)
    python3 pipeline.py ingest data/drop_1

    # Ingest Drop 2 (Day 2 delivery)
    python3 pipeline.py ingest data/drop_2

*(Optional Idempotency Check)* Re-running `ingest` on an already processed drop is a safe no-op and changes nothing:

    python3 pipeline.py ingest data/drop_1

### Step 4: Generate the Five Client Reports
Generate all required CSV and JSON reports into the `reports/` directory:

    python3 pipeline.py report --out reports/

### Step 5: Inspect Generated Output Files
Verify that all 5 output files are created inside `reports/`:

    ls -la reports/
    cat reports/data_quality.json
    head -n 10 reports/batch_summary.csv
    head -n 10 reports/daily_overview.csv
    head -n 10 reports/lead_report.csv
    head -n 10 reports/rejections.csv

### Step 6: Run Unit Tests
Run the unit test suite to verify phone normalisation, date parsing, currency rounding, header/enum cleaning, and IST timestamp conversion:

    python3 -m unittest discover -s tests -v

---

## Custom CLI Flags

If you want to specify a custom SQLite database file or a custom schema folder path, pass `--db` and `--schemas` before the subcommand:

    python3 pipeline.py --db my_custom.db --schemas assignment-a-data/schemas ingest data/drop_1
    python3 pipeline.py --db my_custom.db --schemas assignment-a-data/schemas ingest data/drop_2
    python3 pipeline.py --db my_custom.db --schemas assignment-a-data/schemas report --out custom_reports/

---

## Output Contract Summary

Running `python3 pipeline.py report --out reports/` produces:

1. **`batch_summary.csv`** — Sorted by `workflow_id, batch_id`. Shows `total_leads`, `yet_to_reach_out`, `in_progress_telephony_failed`, `in_progress_dialed`, and `completed`.
2. **`daily_overview.csv`** — Sorted by `workflow_id, date` (IST). Shows `total_calls`, `connected_calls`, `connectivity_rate` (4 decimal places), and `success_leads`.
3. **`lead_report.csv`** — Sorted by `workflow_id, external_ref`. Shows per-lead status, `phone_masked` (`+91******XXXX`), call counts, `last_call_at` (ISO-8601 IST), `current_disposition`, and `ever_success`.
4. **`rejections.csv`** — Sorted by `workflow_id, batch_id, row_number`. Lists rejected CSV rows and their first schema failure reason (`invalid_phone` or `missing_required:<field>`). Contains zero PII.
5. **`data_quality.json`** — Summary counts of total/rejected/duplicate lead rows, field warnings, and total/malformed/duplicate/orphan/unknown-disposition events.

---

## Architecture Highlights

- **PII Protection at Rest:** Plaintext phone numbers and customer names are never stored in `pipeline.db`. Canonical phones (`+91XXXXXXXXXX`) and customer names are hashed with `SHA-256` (`phone_hash`, `customer_name_hash`) for deterministic matching across drops, while `phone_masked` (`+91******XXXX`) is stored for reporting.
- **Append-Only Interaction Ledger:** All valid events are recorded immutably in `events_ledger`. Supervisor `disposition.override` events never delete or overwrite prior `disposition.set` events.
- **Cross-Drop Event Linking:** Events that arrive in `drop_1` before their corresponding lead arrives in `drop_2` are preserved in the ledger and automatically link when `report` runs after `drop_2`.