from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

try:
    from .init_db import DEFAULT_DB_PATH
    from .seed_customers import seed_customers
except ImportError:  # pragma: no cover - keeps direct script execution working.
    from init_db import DEFAULT_DB_PATH
    from seed_customers import seed_customers


import datetime
import random

PAYMENTS = []
INVOICES = []

customer_amounts = {
    "CUST-1001": 1199, "CUST-1002": 1499, "CUST-1003": 1999, "CUST-1004": 799,
    "CUST-1005": 999,  "CUST-1006": 1199, "CUST-1007": 1499, "CUST-1008": 1999,
    "CUST-1009": 1199, "CUST-1010": 799,  "CUST-1011": 999,  "CUST-1012": 1499,
    "CUST-1013": 1999, "CUST-1014": 799,  "CUST-1015": 1199, "CUST-1016": 999,
    "CUST-1017": 1499, "CUST-1018": 799,  "CUST-1019": 1999, "CUST-1020": 1199,
}
methods = ["upi", "credit_card", "netbanking", "wallet", "auto_debit"]

# Generate 12 months of historical invoices (June 2025 - May 2026)
for cust_id, amount in customer_amounts.items():
    for month_offset in range(12):
        # 11 = May 2026, 0 = June 2025
        year = 2025 if month_offset < 7 else 2026
        month = (month_offset + 5) % 12 + 1 # June (6) to May (5)
        
        day = random.randint(1, 15)
        date_str = f"{year}-{month:02d}-{day:02d}"
        
        inv_id = f"INV-{cust_id.split('-')[1]}-{year}-{month:02d}"
        pay_id = f"PAY-{cust_id.split('-')[1]}-{year}-{month:02d}"
        method = random.choice(methods)
        
        # CUST-1001 gets a duplicate payment in May 2026 to preserve the test scenario
        if cust_id == "CUST-1001" and month == 5 and year == 2026:
            INVOICES.append((inv_id, cust_id, amount, date_str, "disputed", f"{pay_id}-A"))
            PAYMENTS.append((f"{pay_id}-A", cust_id, amount, f"{date_str}T09:10:00", method, 1))
            PAYMENTS.append((f"{pay_id}-B", cust_id, amount, f"{date_str}T09:12:00", method, 1))
        else:
            INVOICES.append((inv_id, cust_id, amount, date_str, "paid", pay_id))
            PAYMENTS.append((pay_id, cust_id, amount, f"{date_str}T10:00:00", method, 0))



def seed_payments(connection: sqlite3.Connection) -> None:
    connection.executemany(
        """
        INSERT INTO payments (
            payment_id,
            customer_id,
            amount,
            date,
            method,
            duplicate_flag
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(payment_id) DO UPDATE SET
            customer_id = excluded.customer_id,
            amount = excluded.amount,
            date = excluded.date,
            method = excluded.method,
            duplicate_flag = excluded.duplicate_flag
        """,
        PAYMENTS,
    )


def seed_invoices(connection: sqlite3.Connection) -> None:
    connection.executemany(
        """
        INSERT INTO invoices (
            invoice_id,
            customer_id,
            amount,
            date,
            status,
            payment_id
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(invoice_id) DO UPDATE SET
            customer_id = excluded.customer_id,
            amount = excluded.amount,
            date = excluded.date,
            status = excluded.status,
            payment_id = excluded.payment_id
        """,
        INVOICES,
    )


def seed_billing(db_path: Path = DEFAULT_DB_PATH) -> None:
    seed_customers(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = MEMORY")
        seed_payments(connection)
        seed_invoices(connection)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed ResolveFlow invoices and payments.")
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"SQLite database path. Defaults to {DEFAULT_DB_PATH}",
    )
    args = parser.parse_args()

    seed_billing(args.db_path)
    print(
        f"Seeded {len(INVOICES)} invoices and {len(PAYMENTS)} payments at {args.db_path}")


if __name__ == "__main__":
    main()
