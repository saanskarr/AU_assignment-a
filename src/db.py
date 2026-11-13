import sqlite3

DEFAULT_DB_PATH = "pipeline.db"


def get_db(db_path=DEFAULT_DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def setup_tables(conn):
    cur = conn.cursor()


    cur.execute("""
        CREATE TABLE IF NOT EXISTS processed_files (
            file_key TEXT PRIMARY KEY,
            processed_at TEXT
        )
    """)


    cur.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            workflow_id TEXT,
            phone_hash TEXT,
            phone_masked TEXT,
            customer_name_hash TEXT,
            external_ref TEXT,
            batch_id TEXT,
            extra_fields TEXT,
            PRIMARY KEY (workflow_id, phone_hash)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS rejections (
            workflow_id TEXT,
            batch_id TEXT,
            row_number INTEGER,
            reason TEXT,
            PRIMARY KEY (workflow_id, batch_id, row_number)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS events_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT UNIQUE,
            event_type TEXT,
            workflow_id TEXT,
            call_id TEXT,
            occurred_at_iso TEXT,
            occurred_at_date TEXT,
            phone_hash TEXT,
            payload TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS stats (
            name TEXT PRIMARY KEY,
            count INTEGER
        )
    """)

    stat_names = [
        "lead_rows_total",
        "lead_rows_rejected",
        "duplicate_lead_rows_merged",
        "field_warnings",
        "events_total",
        "malformed_events",
        "duplicate_events_dropped"
    ]
    for name in stat_names:
        cur.execute("INSERT OR IGNORE INTO stats (name, count) VALUES (?, 0)", (name,))

    conn.commit()


def increment_stat(cur, name, amount=1):
    cur.execute("UPDATE stats SET count = count + ? WHERE name = ?", (amount, name))