import csv
import json
import os
from datetime import datetime
from src.db import get_db, setup_tables, increment_stat
from src.validators import (
    IST,
    clean_header,
    hash_value,
    mask_phone,
    parse_phone,
    parse_date,
    parse_decimal,
    parse_enum,
    parse_event_timestamp
)


def get_schema_file(schemas_dir, filename):
    paths_to_check = [
        os.path.join(schemas_dir, filename),
        os.path.join(schemas_dir, "schemas", filename),
        os.path.join("assignment-a-data", "schemas", filename),
        filename
    ]
    for p in paths_to_check:
        if os.path.exists(p):
            return p
    raise FileNotFoundError(f"Cannot find schema file: {filename}")


def load_workflow_schema(schemas_dir, workflow_id):
    path = get_schema_file(schemas_dir, f"{workflow_id}.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def process_lead_csv(conn, schemas_dir, workflow_id, batch_id, csv_path, file_key):
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM processed_files WHERE file_key = ?", (file_key,))
    if cur.fetchone():
        return

    schema = load_workflow_schema(schemas_dir, workflow_id)
    fields = schema["fields"]

    header_map = {}
    for f_def in fields:
        header_map[clean_header(f_def["name"])] = f_def["name"]
        for alias in f_def.get("aliases", []):
            header_map[clean_header(alias)] = f_def["name"]

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        try:
            raw_headers = next(reader)
        except StopIteration:
            return

        col_idx_to_field = {}
        for idx, h in enumerate(raw_headers):
            norm_h = clean_header(h)
            if norm_h in header_map:
                col_idx_to_field[idx] = header_map[norm_h]

        row_num = 0
        for row in reader:
            if not row or all(cell.strip() == "" for cell in row):
                continue

            row_num += 1
            increment_stat(cur, "lead_rows_total", 1)

            row_data = {}
            for idx, cell_val in enumerate(row):
                if idx in col_idx_to_field:
                    row_data[col_idx_to_field[idx]] = cell_val.strip()

            reject_reason = None
            warnings_in_row = 0
            cleaned = {}

            for f_def in fields:
                fname = f_def["name"]
                ftype = f_def["type"]
                is_req = f_def.get("required", False)
                raw_val = row_data.get(fname, "")

                if raw_val == "":
                    if is_req:
                        reject_reason = f"missing_required:{fname}"
                        break
                    else:
                        cleaned[fname] = None
                        continue

                val = None
                if ftype == "string":
                    val = raw_val
                elif ftype == "phone":
                    val = parse_phone(raw_val)
                    if val is None:
                        reject_reason = "invalid_phone"
                        break
                elif ftype == "date":
                    val = parse_date(raw_val)
                elif ftype == "decimal":
                    val = parse_decimal(raw_val)
                elif ftype == "enum":
                    val = parse_enum(raw_val, f_def.get("values", []))

                if val is None:
                    if is_req:
                        reject_reason = f"missing_required:{fname}"
                        break
                    else:
                        cleaned[fname] = None
                        warnings_in_row += 1
                else:
                    cleaned[fname] = val

            if reject_reason:
                increment_stat(cur, "lead_rows_rejected", 1)
                cur.execute("""
                    INSERT OR IGNORE INTO rejections (workflow_id, batch_id, row_number, reason)
                    VALUES (?, ?, ?, ?)
                """, (workflow_id, batch_id, row_num, reject_reason))
                continue

            if warnings_in_row > 0:
                increment_stat(cur, "field_warnings", warnings_in_row)

            canon_phone = cleaned["phone"]
            p_hash = hash_value(canon_phone)
            p_masked = mask_phone(canon_phone)
            name_hash = hash_value(cleaned.get("customer_name") or "")

            ext_ref = ""
            for f_def in fields:
                if f_def.get("reference"):
                    ext_ref = cleaned.get(f_def["name"]) or ""

            extra = {}
            for f_def in fields:
                fname = f_def["name"]
                if not f_def.get("pii", False):
                    extra[fname] = cleaned.get(fname)

            cur.execute("""
                SELECT external_ref, customer_name_hash, extra_fields
                FROM leads
                WHERE workflow_id = ? AND phone_hash = ?
            """, (workflow_id, p_hash))
            existing_lead = cur.fetchone()

            if existing_lead is None:
                for f_def in fields:
                    fname = f_def["name"]
                    if not f_def.get("pii", False):
                        if extra.get(fname) is None and "default" in f_def:
                            extra[fname] = f_def["default"]

                cur.execute("""
                    INSERT INTO leads (
                        workflow_id, phone_hash, phone_masked,
                        customer_name_hash, external_ref, batch_id, extra_fields
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (workflow_id, p_hash, p_masked, name_hash, ext_ref, batch_id, json.dumps(extra)))


            else:
                increment_stat(cur, "duplicate_lead_rows_merged", 1)
                merged_extra = json.loads(existing_lead["extra_fields"])

                for k, v in extra.items():
                    if v is not None:
                        merged_extra[k] = v

                for f_def in fields:
                    fname = f_def["name"]
                    if not f_def.get("pii", False):
                        if merged_extra.get(fname) is None and "default" in f_def:
                            merged_extra[fname] = f_def["default"]

                updated_ref = ext_ref if ext_ref else existing_lead["external_ref"]
                updated_name_hash = name_hash if cleaned.get("customer_name") else existing_lead["customer_name_hash"]

                cur.execute("""
                    UPDATE leads
                    SET customer_name_hash = ?, external_ref = ?, extra_fields = ?
                    WHERE workflow_id = ? AND phone_hash = ?
                """, (updated_name_hash, updated_ref, json.dumps(merged_extra), workflow_id, p_hash))

    cur.execute("INSERT INTO processed_files (file_key, processed_at) VALUES (?, ?)",
                (file_key, datetime.now(IST).isoformat()))
    conn.commit()


def process_events_jsonl(conn, jsonl_path, file_key):
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM processed_files WHERE file_key = ?", (file_key,))
    if cur.fetchone():
        return

    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            increment_stat(cur, "events_total", 1)


            try:
                ev = json.loads(line)
                if not isinstance(ev, dict):
                    increment_stat(cur, "malformed_events", 1)
                    continue
            except json.JSONDecodeError:
                increment_stat(cur, "malformed_events", 1)
                continue

            event_id = ev.get("event_id")
            if not event_id or not isinstance(event_id, str) or not event_id.strip():
                increment_stat(cur, "malformed_events", 1)
                continue
            event_id = event_id.strip()

            ts_parsed = parse_event_timestamp(ev.get("occurred_at", ""))
            if not ts_parsed:
                increment_stat(cur, "malformed_events", 1)
                continue
            iso_ist, date_ist = ts_parsed


            cur.execute("SELECT 1 FROM events_ledger WHERE event_id = ?", (event_id,))
            if cur.fetchone():
                increment_stat(cur, "duplicate_events_dropped", 1)
                continue

            
            raw_phone = ev.get("phone")
            p_hash = None
            if raw_phone is not None:
                norm_p = parse_phone(raw_phone)
                p_hash = hash_value(norm_p) if norm_p else "INVALID_PHONE"

            cur.execute("""
                INSERT INTO events_ledger (
                    event_id, event_type, workflow_id, call_id,
                    occurred_at_iso, occurred_at_date, phone_hash, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event_id,
                ev.get("event_type", ""),
                ev.get("workflow_id", ""),
                ev.get("call_id", ""),
                iso_ist,
                date_ist,
                p_hash,
                json.dumps(ev.get("payload", {}))
            ))

    cur.execute("INSERT INTO processed_files (file_key, processed_at) VALUES (?, ?)",
                (file_key, datetime.now(IST).isoformat()))
    conn.commit()


def ingest_drop(drop_dir, db_path, schemas_dir):
    conn = get_db(db_path)
    setup_tables(conn)

    drop_name = os.path.basename(os.path.normpath(drop_dir))


    leads_dir = os.path.join(drop_dir, "leads")
    if os.path.exists(leads_dir):
        for wf in sorted(os.listdir(leads_dir)):
            wf_path = os.path.join(leads_dir, wf)
            if not os.path.isdir(wf_path):
                continue
            csv_files = sorted(f for f in os.listdir(wf_path) if f.endswith(".csv"))
            for fname in csv_files:
                batch_id = os.path.splitext(fname)[0]
                full_path = os.path.join(wf_path, fname)
                file_key = f"leads/{drop_name}/{wf}/{fname}"
                process_lead_csv(conn, schemas_dir, wf, batch_id, full_path, file_key)


    events_dir = os.path.join(drop_dir, "events")
    if os.path.exists(events_dir):
        jsonl_files = sorted(f for f in os.listdir(events_dir) if f.endswith(".jsonl"))
        for fname in jsonl_files:
            full_path = os.path.join(events_dir, fname)
            file_key = f"events/{drop_name}/{fname}"
            process_events_jsonl(conn, full_path, file_key)

    conn.close()