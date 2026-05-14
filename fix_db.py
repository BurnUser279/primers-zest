import sqlite3
import os

db_path = "dev_database.db"
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    try:
        c.execute("ALTER TABLE vip_pre_payment_chats ADD COLUMN media_path TEXT")
        print("Column media_path added to vip_pre_payment_chats.")
    except Exception as e:
        print(f"Error or already exists: {e}")
    conn.commit()
    conn.close()
else:
    print(f"DB {db_path} not found.")
