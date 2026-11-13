# Submission

## Candidate
- **Name:** Sanskar
- **Assignment chosen:** A (Data)
- **Time spent (honest estimate):** ~6.5 hours

## How to run
```bash
# 1. Optional: set up a virtual env
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. Generate the test data
python3 assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data

# 3. Run ingest on both drops (creates pipeline.db in the root folder)
rm -f pipeline.db
python3 pipeline.py ingest data/drop_1
python3 pipeline.py ingest data/drop_2

# 4. Generate the reports
python3 pipeline.py report --out reports/

# 5. Run the unit tests
python3 -m unittest discover -s tests -v
```

## Design
I split the code into four small modules under `src/` (`db.py`, `validators.py`, `ingest.py`, and `report.py`) with `pipeline.py` acting as a thin CLI wrapper. I went with SQLite (`sqlite3`) instead of Postgres so anyone grading this can run it straight from a clean clone without spinning up Docker containers.

```text
CSV Batches  --> validate & hash PII --> leads / rejections tables
JSONL Events --> dedupe & hash phone --> events_ledger table (append-only)
                                               |
                                               v
report CLI   <-- write 5 output files <-- link events to leads (2 passes)
```

For the database schema (`pipeline.db`), I used five tables:
- **`processed_files` (`file_key PRIMARY KEY, processed_at`)**: Stores the relative path of each file we ingest (like `leads/drop_1/motor_renewal/batch_001.csv`). If someone runs `ingest` twice on the same drop, we check this table and skip the file so we don't double-count rows or warnings.
- **`leads` (`workflow_id, phone_hash, phone_masked, customer_name_hash, external_ref, batch_id, extra_fields, PRIMARY KEY (workflow_id, phone_hash)`)**: Holds one row per unique lead. Since PII can't be stored in plaintext, I hash the normalised `+91XXXXXXXXXX` phone number and customer name using SHA-256 (`phone_hash`, `customer_name_hash`). I also save `phone_masked` (`+91******XXXX`) right on the row because `lead_report.csv` needs the last 4 digits. Workflow-specific columns (like `premium_amount` or `product`) are stored as a JSON string in `extra_fields` so we don't need separate tables for every workflow.
- **`rejections` (`workflow_id, batch_id, row_number, reason, PRIMARY KEY (workflow_id, batch_id, row_number)`)**: Keeps track of bad CSV rows and why they failed, without storing any PII.
- **`events_ledger` (`id INTEGER PRIMARY KEY AUTOINCREMENT, event_id UNIQUE, event_type, workflow_id, call_id, occurred_at_iso, occurred_at_date, phone_hash, payload`)**: Our append-only event log. On ingest, I convert all timestamps to IST (`+05:30`) and hash the phone number so no raw phone numbers sit on disk.
- **`stats` (`name PRIMARY KEY, count`)**: A simple key-value counter table to track cumulative totals (`lead_rows_total`, `field_warnings`, `events_total`, `malformed_events`, etc.) across drops.

## Decisions & assumptions

| Decision | Why | Alternative considered |
|---|---|---|
| One-way SHA-256 hash for `phone` and `customer_name` + storing `phone_masked` | Keeps plaintext PII off the disk while still letting us join events to leads across drops and print `+91******XXXX` in the lead report. | Encrypting with AES (felt like overkill since none of the reports ask for the full unmasked name or phone number back). |
| Save unlinked events in `events_ledger` during ingest, and count orphans at report time | The spec mentions events in `drop_1` can arrive before their lead shows up in `drop_2`. If we dropped orphans during `ingest`, we'd lose those events forever. | Filtering out orphan events during `ingest` (breaks as soon as a lead arrives in a later drop). |
| Two-pass linking over `events_ledger` during reporting | `disposition.override` events only have `call_id` (no `phone`), and the JSONL lines are shuffled so an override can appear above `call.initiated` in the file. Pass 1 maps `call_id -> phone_hash`, and Pass 2 links everything. | Single-pass linking in file order (missed overrides that appeared earlier in the file than `call.initiated`). |
| Only count a call if `call.initiated` is present and linked | Followed the spec rule literally: a call is only counted if its `call.initiated` event is present. | Counting any `call_id` that had a `call.connected` or `call.ended` event. |
| Treat a required field with an invalid type (e.g. bad date) as `missing_required:<field>` | Walking fields in schema order: if a required field is unparseable, it doesn't have a valid value. Warnings are only counted for optional fields on valid rows. | Counting bad required fields as warnings instead of rejecting the row. |
| Track ingested files in `processed_files` | Easy way to make `ingest` idempotent without messing up counters like `malformed_events` or `lead_rows_total` on re-runs. | Relying only on SQLite `UNIQUE` constraints (would still increment `events_total` and `malformed_events` every time you re-ran `ingest`). |

## Failure modes considered
- **Re-running `ingest` on the same drop:** Checked via `processed_files`. It exits cleanly and leaves the database and counters untouched.
- **Process crash / `kill -9` mid-file:** Each file is wrapped in a single SQLite transaction (`conn.commit()` happens at the very end of the file right after inserting into `processed_files`). If it crashes halfway through a file, SQLite rolls back the partial changes automatically.
- **Corrupted JSON lines & missing `event_id`:** Handled in a `try/except` block during JSONL reading; increments `malformed_events` and moves to the next line.
- **Cross-drop duplicate events:** Handled by the `UNIQUE` index on `event_id` in `events_ledger` and counted under `duplicate_events_dropped`.
- **What is NOT handled:** Running multiple `ingest` processes in parallel against the same SQLite file (SQLite locks the whole database file on writes), and memory usage during `report` if the dataset grows to tens of millions of rows since I currently load the ledger into Python dicts to do the two-pass linking.

## Testing
- **What I unit tested (`tests/test_pipeline.py`):**
  - All 6 valid phone formats from the generator, plus bad phones (9 digits, starting with `5`, `022` landlines, `"N/A"`, US `+1` numbers).
  - All 4 date formats (`YYYY-MM-DD`, `DD/MM/YYYY`, `DD-MM-YYYY`, `DD-Mon-YYYY`) and impossible dates like `31/02/2026` and `2026-13-01`.
  - Currency strings (`₹`, `Rs.`, `INR`, commas) and rounding to 2 decimal places.
  - Header cleaning (`Insured__Name` -> `insured name`) and enum matching.
  - Timestamp conversion from UTC (`Z`) and naive strings to IST (`+05:30`).
- **What I tested manually / end-to-end:**
  - Ran `ingest` on `drop_1` and `drop_2` with `--seed 42`, then ran `ingest data/drop_1` a second time to verify `data_quality.json` and the 5 CSVs didn't change at all.

## Results
- **Timings:** On my MacBook (Python 3.12), ingesting `drop_1` + `drop_2` and generating all 5 reports for `--scale small` takes about **0.25 seconds** total.
- **Numbers on `--seed 42` (`--scale small`):**
  - `lead_rows_total`: 2,057 (`108` rejected: `42` invalid phone, `25` missing name, `14` missing phone, `14` missing policy number, `13` missing order id).
  - `duplicate_lead_rows_merged`: 57, `field_warnings`: 122.
  - `events_total`: 8,745 (`8` malformed truncated JSON lines, `237` duplicate events, `462` orphans from calls to rejected/un-uploaded leads, and `18` unknown dispositions like `"RENEWD"` and `"call_back"`).
  - Also noticed `batch_003.csv` in `drop_2` switched all the column headers to aliases (`Insured Name`, `Mobile No`, etc.), added an extra `agent_notes` column, and threw in blank `,,,,` lines—all of which got handled cleanly.

## If I had another day
1. **Use HMAC-SHA256 with a secret salt for phone hashes:** Plain SHA-256 on 10-digit Indian mobile numbers is vulnerable to a precomputed rainbow table since there are only ~4 billion possible numbers starting with 6-9. Adding a secret key via HMAC fixes that.
2. **Move report aggregation into SQL queries:** Right now `report.py` pulls `events_ledger` into memory in Python. Rewriting the effective disposition and bucket logic using SQL window functions (`ROW_NUMBER() OVER (...)`) would scale much better for huge datasets.
3. **Batch inserts with `executemany`:** Ingesting row-by-row is fast enough for small scale, but batching inserts in chunks of 5,000 would speed up `--scale large` significantly.