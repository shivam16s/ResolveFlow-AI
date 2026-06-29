from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

try:
    from .init_db import DEFAULT_DB_PATH, initialize_database
except ImportError:  # pragma: no cover - keeps direct script execution working.
    from init_db import DEFAULT_DB_PATH, initialize_database


PLANS = [
    {
        "plan_id": "fiber_starter_100",
        "plan_name": "Fiber Starter 100",
        "monthly_price": 799,
        "speed_mbps": 100,
        "benefits": ["Unlimited data", "Basic router support"],
        "cancellation_fee": 0,
    },
    {
        "plan_id": "fiber_plus_200",
        "plan_name": "Fiber Plus 200",
        "monthly_price": 1199,
        "speed_mbps": 200,
        "benefits": ["Unlimited data", "Priority diagnostics", "OTT bundle"],
        "cancellation_fee": 499,
    },
    {
        "plan_id": "fiber_family_300",
        "plan_name": "Fiber Family 300",
        "monthly_price": 1499,
        "speed_mbps": 300,
        "benefits": ["Unlimited data", "Family controls", "Free router replacement"],
        "cancellation_fee": 799,
    },
    {
        "plan_id": "fiber_work_500",
        "plan_name": "Fiber Work 500",
        "monthly_price": 1999,
        "speed_mbps": 500,
        "benefits": ["Static IP", "Priority support", "4-hour issue response"],
        "cancellation_fee": 999,
    },
    {
        "plan_id": "mobile_broadband_bundle",
        "plan_name": "Mobile Broadband Bundle",
        "monthly_price": 999,
        "speed_mbps": 150,
        "benefits": ["Broadband", "Two mobile add-ons", "Weekend data boost"],
        "cancellation_fee": 299,
    },
]


CUSTOMERS = [
    ("CUST-1001", "Rahul Sharma", "rahul.sharma@example.com",
     "Chennai Zone-04", "fiber_plus_200", "high", "en", "active", 0.78),
    ("CUST-1002", "Ananya Iyer", "ananya.iyer@example.com",
     "Chennai Zone-02", "fiber_family_300", "medium", "ta", "active", 0.42),
    ("CUST-1003", "Vikram Mehta", "vikram.mehta@example.com",
     "Mumbai West-01", "fiber_work_500", "low", "en", "active", 0.18),
    ("CUST-1004", "Priya Nair", "priya.nair@example.com", "Kochi Central-03",
     "fiber_starter_100", "medium", "ml", "active", 0.36),
    ("CUST-1005", "Arjun Reddy", "arjun.reddy@example.com", "Hyderabad North-02",
     "mobile_broadband_bundle", "high", "te", "pending_cancellation", 0.84),
    ("CUST-1006", "Meera Kapoor", "meera.kapoor@example.com",
     "Delhi South-07", "fiber_plus_200", "low", "hi", "active", 0.21),
    ("CUST-1007", "Sanjay Kulkarni", "sanjay.kulkarni@example.com",
     "Pune East-05", "fiber_family_300", "medium", "mr", "active", 0.49),
    ("CUST-1008", "Neha Gupta", "neha.gupta@example.com",
     "Noida Sector-62", "fiber_work_500", "critical", "hi", "active", 0.91),
    ("CUST-1009", "Karthik Subramanian", "karthik.subramanian@example.com",
     "Bengaluru Whitefield", "fiber_plus_200", "medium", "kn", "active", 0.55),
    ("CUST-1010", "Aisha Khan", "aisha.khan@example.com",
     "Lucknow Central-01", "fiber_starter_100", "low", "hi", "active", 0.16),
    ("CUST-1011", "Rohan Das", "rohan.das@example.com", "Kolkata Salt Lake",
     "mobile_broadband_bundle", "medium", "bn", "active", 0.47),
    ("CUST-1012", "Divya Menon", "divya.menon@example.com",
     "Trivandrum North-02", "fiber_family_300", "low", "ml", "active", 0.24),
    ("CUST-1013", "Kabir Malhotra", "kabir.malhotra@example.com",
     "Gurgaon DLF-03", "fiber_work_500", "high", "en", "active", 0.73),
    ("CUST-1014", "Sneha Patil", "sneha.patil@example.com", "Nagpur West-04",
     "fiber_starter_100", "medium", "mr", "suspended", 0.62),
    ("CUST-1015", "Ishaan Bose", "ishaan.bose@example.com",
     "Kolkata Park Street", "fiber_plus_200", "low", "bn", "active", 0.28),
    ("CUST-1016", "Nisha Verma", "nisha.verma@example.com", "Jaipur East-06",
     "mobile_broadband_bundle", "medium", "hi", "active", 0.51),
    ("CUST-1017", "Aditya Rao", "aditya.rao@example.com", "Bengaluru Indiranagar",
     "fiber_family_300", "high", "kn", "pending_cancellation", 0.81),
    ("CUST-1018", "Farah Qureshi", "farah.qureshi@example.com",
     "Ahmedabad SG Highway", "fiber_starter_100", "low", "gu", "active", 0.19),
    ("CUST-1019", "Manav Singh", "manav.singh@example.com",
     "Chandigarh Sector-17", "fiber_work_500", "critical", "en", "active", 0.89),
    ("CUST-1020", "Lakshmi Narayanan", "lakshmi.narayanan@example.com",
     "Chennai Zone-04", "fiber_plus_200", "high", "ta", "active", 0.76),
]


def seed_plans(connection: sqlite3.Connection) -> None:
    connection.executemany(
        """
        INSERT INTO plans (
            plan_id,
            plan_name,
            monthly_price,
            speed_mbps,
            benefits,
            cancellation_fee
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(plan_id) DO UPDATE SET
            plan_name = excluded.plan_name,
            monthly_price = excluded.monthly_price,
            speed_mbps = excluded.speed_mbps,
            benefits = excluded.benefits,
            cancellation_fee = excluded.cancellation_fee
        """,
        [
            (
                plan["plan_id"],
                plan["plan_name"],
                plan["monthly_price"],
                plan["speed_mbps"],
                json.dumps(plan["benefits"]),
                plan["cancellation_fee"],
            )
            for plan in PLANS
        ],
    )


def seed_customers(db_path: Path = DEFAULT_DB_PATH) -> None:
    initialize_database(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = MEMORY")
        seed_plans(connection)
        connection.executemany(
            """
            INSERT INTO customers (
                customer_id,
                name,
                email,
                location,
                plan_id,
                risk_level,
                preferred_language,
                account_status,
                churn_score
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(customer_id) DO UPDATE SET
                name = excluded.name,
                email = excluded.email,
                location = excluded.location,
                plan_id = excluded.plan_id,
                risk_level = excluded.risk_level,
                preferred_language = excluded.preferred_language,
                account_status = excluded.account_status,
                churn_score = excluded.churn_score
            """,
            CUSTOMERS,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed ResolveFlow telecom customers.")
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"SQLite database path. Defaults to {DEFAULT_DB_PATH}",
    )
    args = parser.parse_args()

    seed_customers(args.db_path)
    print(
        f"Seeded {len(CUSTOMERS)} customers and {len(PLANS)} plans at {args.db_path}")


if __name__ == "__main__":
    main()
