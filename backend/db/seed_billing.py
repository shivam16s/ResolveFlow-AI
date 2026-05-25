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


PAYMENTS = [
    ("PAY-1001-A", "CUST-1001", 1199, "2026-05-18T09:10:00", "upi", 1),
    ("PAY-1001-B", "CUST-1001", 1199, "2026-05-18T09:12:00", "upi", 1),
    ("PAY-1002", "CUST-1002", 1499, "2026-05-05T13:25:00", "credit_card", 0),
    ("PAY-1003", "CUST-1003", 1999, "2026-05-07T08:45:00", "netbanking", 0),
    ("PAY-1004", "CUST-1004", 799, "2026-05-09T20:14:00", "upi", 0),
    ("PAY-1005", "CUST-1005", 999, "2026-05-11T18:40:00", "auto_debit", 0),
    ("PAY-1006", "CUST-1006", 1199, "2026-05-02T10:05:00", "credit_card", 0),
    ("PAY-1007", "CUST-1007", 1499, "2026-05-15T16:30:00", "upi", 0),
    ("PAY-1008", "CUST-1008", 1999, "2026-05-06T11:11:00", "auto_debit", 0),
    ("PAY-1009", "CUST-1009", 1199, "2026-05-08T19:52:00", "upi", 0),
    ("PAY-1010", "CUST-1010", 799, "2026-05-12T07:36:00", "wallet", 0),
    ("PAY-1011", "CUST-1011", 999, "2026-05-10T15:18:00", "netbanking", 0),
    ("PAY-1012", "CUST-1012", 1499, "2026-05-14T09:27:00", "credit_card", 0),
    ("PAY-1013", "CUST-1013", 1999, "2026-05-16T21:04:00", "auto_debit", 0),
    ("PAY-1014", "CUST-1014", 799, "2026-04-29T17:43:00", "upi", 0),
    ("PAY-1015", "CUST-1015", 1199, "2026-05-03T12:20:00", "wallet", 0),
    ("PAY-1016", "CUST-1016", 999, "2026-05-13T10:40:00", "upi", 0),
    ("PAY-1017", "CUST-1017", 1499, "2026-05-17T22:15:00", "credit_card", 0),
    ("PAY-1018", "CUST-1018", 799, "2026-05-04T06:55:00", "netbanking", 0),
    ("PAY-1019", "CUST-1019", 1999, "2026-05-19T14:02:00", "auto_debit", 0),
    ("PAY-1020", "CUST-1020", 1199, "2026-05-18T09:50:00", "upi", 0),
]


INVOICES = [
    ("INV-8821", "CUST-1001", 1199, "2026-05-18", "disputed", "PAY-1001-A"),
    ("INV-1002", "CUST-1002", 1499, "2026-05-05", "paid", "PAY-1002"),
    ("INV-1003", "CUST-1003", 1999, "2026-05-07", "paid", "PAY-1003"),
    ("INV-1004", "CUST-1004", 799, "2026-05-09", "paid", "PAY-1004"),
    ("INV-1005", "CUST-1005", 999, "2026-05-11", "paid", "PAY-1005"),
    ("INV-1006", "CUST-1006", 1199, "2026-05-02", "paid", "PAY-1006"),
    ("INV-1007", "CUST-1007", 1499, "2026-05-15", "paid", "PAY-1007"),
    ("INV-1008", "CUST-1008", 1999, "2026-05-06", "paid", "PAY-1008"),
    ("INV-1009", "CUST-1009", 1199, "2026-05-08", "paid", "PAY-1009"),
    ("INV-1010", "CUST-1010", 799, "2026-05-12", "paid", "PAY-1010"),
    ("INV-1011", "CUST-1011", 999, "2026-05-10", "paid", "PAY-1011"),
    ("INV-1012", "CUST-1012", 1499, "2026-05-14", "paid", "PAY-1012"),
    ("INV-1013", "CUST-1013", 1999, "2026-05-16", "paid", "PAY-1013"),
    ("INV-1014", "CUST-1014", 799, "2026-04-29", "pending", "PAY-1014"),
    ("INV-1015", "CUST-1015", 1199, "2026-05-03", "paid", "PAY-1015"),
    ("INV-1016", "CUST-1016", 999, "2026-05-13", "paid", "PAY-1016"),
    ("INV-1017", "CUST-1017", 1499, "2026-05-17", "paid", "PAY-1017"),
    ("INV-1018", "CUST-1018", 799, "2026-05-04", "paid", "PAY-1018"),
    ("INV-1019", "CUST-1019", 1999, "2026-05-19", "paid", "PAY-1019"),
    ("INV-1020", "CUST-1020", 1199, "2026-05-18", "paid", "PAY-1020"),
]


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
    parser = argparse.ArgumentParser(description="Seed ResolveFlow invoices and payments.")
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"SQLite database path. Defaults to {DEFAULT_DB_PATH}",
    )
    args = parser.parse_args()

    seed_billing(args.db_path)
    print(f"Seeded {len(INVOICES)} invoices and {len(PAYMENTS)} payments at {args.db_path}")


if __name__ == "__main__":
    main()
