
import argparse
from src.db import DEFAULT_DB_PATH
from src.ingest import ingest_drop
from src.report import generate_reports


def main():
    parser = argparse.ArgumentParser(description="Outbound Campaign Interaction Ledger & Reporting CLI")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite database path")
    parser.add_argument("--schemas", default=".", help="Path to schemas directory")

    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_cmd = subparsers.add_parser("ingest", help="Ingest a drop directory")
    ingest_cmd.add_argument("drop_dir", help="Path to drop folder (e.g. data/drop_1)")

    report_cmd = subparsers.add_parser("report", help="Generate CSV and JSON reports")
    report_cmd.add_argument("--out", required=True, help="Directory to write report files")

    args = parser.parse_args()

    if args.command == "ingest":
        ingest_drop(args.drop_dir, args.db, args.schemas)
    elif args.command == "report":
        generate_reports(args.out, args.db, args.schemas)


if __name__ == "__main__":
    main()