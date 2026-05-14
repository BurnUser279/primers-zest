import sqlite3
import os

def check():
    db_path = 'dev_database.db'
    if not os.path.exists(db_path):
        return
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT id, fullname, email FROM members WHERE email = 'audit_user_001@example.com'")
    row = c.fetchone()
    print(row)
    
    # Also check for latest messages
    c.execute("SELECT category, message FROM tickets ORDER BY created_at DESC LIMIT 5")
    print("Latest tickets:", c.fetchall())
    
    conn.close()

if __name__ == "__main__":
    check()
