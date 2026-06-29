import sqlite3; conn = sqlite3.connect('data/resolveflow.db'); print(conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='customers'").fetchone()[0])
