import csv
import json
import os
from src.db import get_db, setup_tables
from src.ingest import get_schema_file


def load_dispositions_catalogue(schemas_dir):
    path = get_schema_file(schemas_dir, "dispositions.csv")
    catalogue = {}
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            wf = row["workflow_id"].strip()
            code = row["code"].strip()
            if wf not in catalogue:
                catalogue[wf] = {}
            catalogue[wf][code] = {
                "is_terminal": row["is_terminal"].strip().lower() == "true",
                "is_success": row["is_success"].strip().lower() == "true"
            }
    return catalogue


def generate_reports(out_dir, db_path, schemas_dir):
    os.makedirs(out_dir, exist_ok=True)
    conn = get_db(db_path)
    setup_tables(conn)
    cur = conn.cursor()

    dispositions = load_dispositions_catalogue(schemas_dir)

    # Fetch all valid leads
    cur.execute("SELECT workflow_id, phone_hash, phone_masked, external_ref, batch_id FROM leads")
    leads = {}
    for row in cur.fetchall():
        key = (row["workflow_id"], row["phone_hash"])
        leads[key] = {
            "workflow_id": row["workflow_id"],
            "phone_hash": row["phone_hash"],
            "phone_masked": row["phone_masked"],
            "external_ref": row["external_ref"],
            "batch_id": row["batch_id"],
            "calls": {}
        }

    # Fetch all events from the ledger
    cur.execute("""
        SELECT id, event_id, event_type, workflow_id, call_id,
               occurred_at_iso, occurred_at_date, phone_hash, payload
        FROM events_ledger
        ORDER BY id ASC
    """)
    all_events = cur.fetchall()

    # Pass 1: Map (workflow_id, call_id) -> phone_hash using regular events
    # We do this in two passes because events are shuffled and disposition.override has no phone!
    call_to_phone = {}
    for ev in all_events:
        if ev["event_type"] != "disposition.override":
            wf = ev["workflow_id"]
            ph = ev["phone_hash"]
            if ph and ph != "INVALID_PHONE" and (wf, ph) in leads:
                call_to_phone[(wf, ev["call_id"])] = ph

    # Pass 2: Link events to leads, count orphans and unknown dispositions
    orphan_count = 0
    unknown_disp_count = 0

    for ev in all_events:
        etype = ev["event_type"]
        wf = ev["workflow_id"]
        cid = ev["call_id"]
        ph = ev["phone_hash"]
        payload = json.loads(ev["payload"])

        # Step 3: Check if event is an orphan
        linked_phone_hash = None
        if etype == "disposition.override":
            linked_phone_hash = call_to_phone.get((wf, cid))
        else:
            if ph and ph != "INVALID_PHONE" and (wf, ph) in leads:
                linked_phone_hash = ph

        if linked_phone_hash is None:
            orphan_count += 1
            continue

        # Step 4: Check if disposition code is in dispositions.csv
        if etype in ("disposition.set", "disposition.override"):
            code = payload.get("disposition_code", "")
            if wf not in dispositions or code not in dispositions[wf]:
                unknown_disp_count += 1
                continue

        # Group valid events under their lead and call_id
        lead_calls = leads[(wf, linked_phone_hash)]["calls"]
        if cid not in lead_calls:
            lead_calls[cid] = {
                "call_id": cid,
                "initiated_at": None,
                "initiated_date": None,
                "connected": False,
                "telephony_failed": False,
                "sets": [],
                "overrides": []
            }

        c_info = lead_calls[cid]
        if etype == "call.initiated":
            c_info["initiated_at"] = ev["occurred_at_iso"]
            c_info["initiated_date"] = ev["occurred_at_date"]
        elif etype == "call.connected":
            c_info["connected"] = True
        elif etype == "call.telephony_failed":
            c_info["telephony_failed"] = True
        elif etype == "disposition.set":
            c_info["sets"].append((ev["occurred_at_iso"], ev["id"], payload["disposition_code"]))
        elif etype == "disposition.override":
            c_info["overrides"].append((ev["occurred_at_iso"], ev["id"], payload["disposition_code"]))

    # Compute derived metrics for reports
    batch_summary = {}
    daily_overview = {}
    lead_report_rows = []

    for (wf, ph), lead in leads.items():
        b_key = (wf, lead["batch_id"])
        if b_key not in batch_summary:
            batch_summary[b_key] = {
                "total_leads": 0,
                "yet_to_reach_out": 0,
                "in_progress_telephony_failed": 0,
                "in_progress_dialed": 0,
                "completed": 0
            }

        # Only count calls that have a linked call.initiated event
        valid_calls = []
        for cid, c_info in lead["calls"].items():
            if c_info["initiated_at"] is None:
                continue

            # Effective disposition: latest override by occurred_at, else latest set
            eff_code = None
            if c_info["overrides"]:
                c_info["overrides"].sort(key=lambda x: (x[0], x[1]))
                eff_code = c_info["overrides"][-1][2]
            elif c_info["sets"]:
                c_info["sets"].sort(key=lambda x: (x[0], x[1]))
                eff_code = c_info["sets"][-1][2]

            is_term = False
            is_succ = False
            if eff_code and wf in dispositions and eff_code in dispositions[wf]:
                is_term = dispositions[wf][eff_code]["is_terminal"]
                is_succ = dispositions[wf][eff_code]["is_success"]

            valid_calls.append({
                "call_id": cid,
                "initiated_at": c_info["initiated_at"],
                "initiated_date": c_info["initiated_date"],
                "connected": c_info["connected"],
                "telephony_failed": c_info["telephony_failed"],
                "eff_code": eff_code,
                "is_terminal": is_term,
                "is_success": is_succ
            })

        valid_calls.sort(key=lambda c: (c["initiated_at"], c["call_id"]))

        total_calls = len(valid_calls)
        connected_calls = sum(1 for c in valid_calls if c["connected"])
        last_call_at = valid_calls[-1]["initiated_at"] if valid_calls else ""

        # Current disposition: most recent call (by initiated time) that has an effective disposition
        current_disp = ""
        for c in reversed(valid_calls):
            if c["eff_code"]:
                current_disp = c["eff_code"]
                break

        ever_success = any(c["is_success"] for c in valid_calls)

        # Lead bucket logic
        if total_calls == 0:
            bucket = "yet_to_reach_out"
        elif any(c["is_terminal"] for c in valid_calls):
            bucket = "completed"
        elif all(c["telephony_failed"] for c in valid_calls):
            bucket = "in_progress_telephony_failed"
        else:
            bucket = "in_progress_dialed"

        batch_summary[b_key]["total_leads"] += 1
        batch_summary[b_key][bucket] += 1

        for c in valid_calls:
            d_key = (wf, c["initiated_date"])
            if d_key not in daily_overview:
                daily_overview[d_key] = {
                    "total_calls": 0,
                    "connected_calls": 0,
                    "success_leads": set()
                }
            daily_overview[d_key]["total_calls"] += 1
            if c["connected"]:
                daily_overview[d_key]["connected_calls"] += 1
            if c["is_success"]:
                daily_overview[d_key]["success_leads"].add(ph)

        lead_report_rows.append({
            "workflow_id": wf,
            "external_ref": lead["external_ref"],
            "batch_id": lead["batch_id"],
            "phone_masked": lead["phone_masked"],
            "bucket": bucket,
            "total_calls": total_calls,
            "connected_calls": connected_calls,
            "last_call_at": last_call_at,
            "current_disposition": current_disp,
            "ever_success": "true" if ever_success else "false"
        })

    # 1. Write batch_summary.csv
    with open(os.path.join(out_dir, "batch_summary.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "workflow_id", "batch_id", "total_leads",
            "yet_to_reach_out", "in_progress_telephony_failed",
            "in_progress_dialed", "completed"
        ])
        for (wf, bid) in sorted(batch_summary.keys()):
            s = batch_summary[(wf, bid)]
            w.writerow([
                wf, bid, s["total_leads"],
                s["yet_to_reach_out"], s["in_progress_telephony_failed"],
                s["in_progress_dialed"], s["completed"]
            ])

    # 2. Write daily_overview.csv
    with open(os.path.join(out_dir, "daily_overview.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "workflow_id", "date", "total_calls",
            "connected_calls", "connectivity_rate", "success_leads"
        ])
        for (wf, dt) in sorted(daily_overview.keys()):
            d = daily_overview[(wf, dt)]
            tot = d["total_calls"]
            conn_c = d["connected_calls"]
            rate = f"{(conn_c / tot):.4f}" if tot > 0 else "0.0000"
            w.writerow([wf, dt, tot, conn_c, rate, len(d["success_leads"])])

    # 3. Write lead_report.csv
    lead_report_rows.sort(key=lambda r: (r["workflow_id"], r["external_ref"]))
    with open(os.path.join(out_dir, "lead_report.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "workflow_id", "external_ref", "batch_id", "phone_masked",
            "bucket", "total_calls", "connected_calls", "last_call_at",
            "current_disposition", "ever_success"
        ])
        for r in lead_report_rows:
            w.writerow([
                r["workflow_id"], r["external_ref"], r["batch_id"], r["phone_masked"],
                r["bucket"], r["total_calls"], r["connected_calls"], r["last_call_at"],
                r["current_disposition"], r["ever_success"]
            ])

    # 4. Write rejections.csv
    cur.execute("""
        SELECT workflow_id, batch_id, row_number, reason
        FROM rejections
        ORDER BY workflow_id ASC, batch_id ASC, row_number ASC
    """)
    with open(os.path.join(out_dir, "rejections.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["workflow_id", "batch_id", "row_number", "reason"])
        for r in cur.fetchall():
            w.writerow([r["workflow_id"], r["batch_id"], r["row_number"], r["reason"]])

    # 5. Write data_quality.json
    cur.execute("SELECT name, count FROM stats")
    stats_map = {row["name"]: row["count"] for row in cur.fetchall()}

    cur.execute("SELECT reason, COUNT(*) as cnt FROM rejections GROUP BY reason ORDER BY reason ASC")
    rej_reasons = {row["reason"]: row["cnt"] for row in cur.fetchall()}

    dq_output = {
        "lead_rows_total": stats_map.get("lead_rows_total", 0),
        "lead_rows_rejected": stats_map.get("lead_rows_rejected", 0),
        "rejections_by_reason": rej_reasons,
        "duplicate_lead_rows_merged": stats_map.get("duplicate_lead_rows_merged", 0),
        "field_warnings": stats_map.get("field_warnings", 0),
        "events_total": stats_map.get("events_total", 0),
        "malformed_events": stats_map.get("malformed_events", 0),
        "duplicate_events_dropped": stats_map.get("duplicate_events_dropped", 0),
        "orphan_events": orphan_count,
        "unknown_disposition_events": unknown_disp_count
    }

    with open(os.path.join(out_dir, "data_quality.json"), "w", encoding="utf-8") as f:
        json.dump(dq_output, f, indent=2)
        f.write("\n")

    conn.close()