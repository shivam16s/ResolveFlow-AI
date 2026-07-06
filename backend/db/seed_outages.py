from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

try:
    from .init_db import DEFAULT_DB_PATH
    from .seed_customers import seed_customers
except ImportError:  # pragma: no cover - keeps direct script execution working.
    from init_db import DEFAULT_DB_PATH
    from seed_customers import seed_customers


OUTAGES = [
    {
        "outage_id": "OUT-CHN-04-20260520",
        "location": "Chennai Zone-04",
        "start_time": "2026-05-20T08:00:00",
        "end_time": "2026-05-20T15:00:00",
        "duration_hours": 7.0,
        "verified": 1,
        "affected_customers": ["CUST-1001", "CUST-1020"],
    },
    {
        "outage_id": "OUT-CHN-02-20260518",
        "location": "Chennai Zone-02",
        "start_time": "2026-05-18T21:30:00",
        "end_time": "2026-05-18T23:00:00",
        "duration_hours": 1.5,
        "verified": 1,
        "affected_customers": ["CUST-1002"],
    },
    {
        "outage_id": "OUT-MUM-W01-20260512",
        "location": "Mumbai West-01",
        "start_time": "2026-05-12T02:15:00",
        "end_time": "2026-05-12T04:45:00",
        "duration_hours": 2.5,
        "verified": 1,
        "affected_customers": ["CUST-1003"],
    },
    {
        "outage_id": "OUT-KOC-C03-20260509",
        "location": "Kochi Central-03",
        "start_time": "2026-05-09T17:00:00",
        "end_time": None,
        "duration_hours": None,
        "verified": 0,
        "affected_customers": ["CUST-1004"],
    },
    {
        "outage_id": "OUT-HYD-N02-20260514",
        "location": "Hyderabad North-02",
        "start_time": "2026-05-14T11:20:00",
        "end_time": "2026-05-14T19:50:00",
        "duration_hours": 8.5,
        "verified": 1,
        "affected_customers": ["CUST-1005"],
    },
    {
        "outage_id": "OUT-DEL-S07-20260516",
        "location": "Delhi South-07",
        "start_time": "2026-05-16T06:00:00",
        "end_time": "2026-05-16T06:40:00",
        "duration_hours": 0.67,
        "verified": 0,
        "affected_customers": ["CUST-1006"],
    },
    {
        "outage_id": "OUT-PUN-E05-20260517",
        "location": "Pune East-05",
        "start_time": "2026-05-17T13:00:00",
        "end_time": "2026-05-17T18:30:00",
        "duration_hours": 5.5,
        "verified": 1,
        "affected_customers": ["CUST-1007"],
    },
    {
        "outage_id": "OUT-NOI-62-20260519",
        "location": "Noida Sector-62",
        "start_time": "2026-05-19T09:10:00",
        "end_time": "2026-05-19T17:40:00",
        "duration_hours": 8.5,
        "verified": 1,
        "affected_customers": ["CUST-1008"],
    },
    {
        "outage_id": "OUT-BLR-WF-20260515",
        "location": "Bengaluru Whitefield",
        "start_time": "2026-05-15T20:30:00",
        "end_time": None,
        "duration_hours": None,
        "verified": 0,
        "affected_customers": ["CUST-1009"],
    },
    {
        "outage_id": "OUT-GGN-DLF-20260513",
        "location": "Gurgaon DLF-03",
        "start_time": "2026-05-13T10:00:00",
        "end_time": "2026-05-13T12:20:00",
        "duration_hours": 2.33,
        "verified": 0,
        "affected_customers": ["CUST-1013"],
    },
]


def seed_outages(db_path: Path = DEFAULT_DB_PATH) -> None:
    seed_customers(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executemany(
            """
            INSERT INTO outages (
                outage_id,
                location,
                start_time,
                end_time,
                duration_hours,
                verified,
                affected_customers
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(outage_id) DO UPDATE SET
                location = excluded.location,
                start_time = excluded.start_time,
                end_time = excluded.end_time,
                duration_hours = excluded.duration_hours,
                verified = excluded.verified,
                affected_customers = excluded.affected_customers
            """,
            [
                (
                    outage["outage_id"],
                    outage["location"],
                    outage["start_time"],
                    outage["end_time"],
                    outage["duration_hours"],
                    outage["verified"],
                    json.dumps(outage["affected_customers"]),
                )
                for outage in OUTAGES
            ],
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed ResolveFlow outage records.")
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"SQLite database path. Defaults to {DEFAULT_DB_PATH}",
    )
    args = parser.parse_args()

    seed_outages(args.db_path)
    print(f"Seeded {len(OUTAGES)} outage records at {args.db_path}")


if __name__ == "__main__":
    main()
