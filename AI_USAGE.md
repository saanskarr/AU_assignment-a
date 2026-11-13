# AI Usage Disclosure

## Tools used

* Gemini and VS Code's built-in AI assistant

## What I used them for

* Writing the regex patterns in `src/validators.py` to strip currency symbols (`₹`, `Rs.`, `INR`) and clean up messy CSV headers.
* Getting a quick first draft of the script working, which I later broke up into separate files under `src/`.
* Generating the repetitive unit test cases in `tests/test_pipeline.py` for all the different phone and date formats.
* Cleaning up the formatting and tables in `README.md` and `SUBMISSION.md`.

## What I wrote or decided myself

* Splitting the code into `db.py`, `validators.py`, `ingest.py`, and `report.py` inside `src/` instead of leaving everything in one giant file.
* Using SQLite with SHA-256 hashes for `phone` and `customer_name`, plus saving the masked phone (`+91******XXXX`) directly on the lead row so I wouldn't have to store raw PII or deal with encryption keys just to generate `lead_report.csv`.
* Keeping unlinked events in `events_ledger` during `ingest` and only counting orphans at `report` time. Since events in `drop_1` can show up before their lead is uploaded in `drop_2`, dropping them during ingest would have broken cross-drop linking.
* Adding the `processed_files` table so re-running `ingest` on the same folder doesn't double-count rows or warnings.

## Where the AI was wrong or unhelpful

* **Dumping everything into one file:** The first version it gave me was a 500-line `pipeline.py` file with all the SQL, validation, and CSV writing mixed together. It worked, but it was messy to read, so I refactored it into `src/`.
* **Single-pass override linking:** The initial reporting logic tried to link `disposition.override` events in one pass while looping through the events. When I checked `generate_data.py`, I saw `r.shuffle(lines)` shuffles all the JSONL lines before saving them. Because overrides only have a `call_id` and no phone number, an override can show up earlier in the file than `call.initiated`. I had to rewrite `src/report.py` to do two passes—first mapping `call_id` to `phone_hash`, and then linking the events.
* **Messed up imports when moving files:** While moving files around in VS Code, the auto-import feature changed `from src.ingest` to `from counters.ingest` (from an old folder name I deleted) and broke the CLI with a `ModuleNotFoundError`. Caught that right away in the terminal and fixed the import paths.

## How I verified AI-generated code

* Ran `generate_data.py` with `--seed 42 --scale small`, ingested both drops, and manually checked the generated CSVs and `data_quality.json` to make sure the counts made sense.
* Ran `python3 pipeline.py ingest data/drop_1` twice in a row and checked `reports/data_quality.json` afterward to make sure none of the numbers (`2057` lead rows, `108` rejected, `8745` events) went up on the second run.
* Ran the unit tests (`python3 -m unittest discover -s tests -v`) to make sure tricky cases like `31/02/2026`, `022` landlines, and UTC timestamps crossing midnight into IST were handled properly.