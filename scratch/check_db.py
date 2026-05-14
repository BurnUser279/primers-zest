import sqlite3
import os

db_path = 'dev_database.db'
if not os.path.exists(db_path):
    print(f"Database {db_path} not found.")
else:
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = c.fetchall()
    print("Tables:", tables)
    for table in tables:
        t_name = table[0]
        c.execute(f"PRAGMA table_info({t_name})")
        print(f"\nSchema for {t_name}:")
        print(c.fetchall())
    conn.close()
