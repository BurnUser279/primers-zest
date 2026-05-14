import sqlite3
import os

def check():
    db_path = 'dev_database.db'
    if not os.path.exists(db_path):
        return
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT fullname, is_active, membership_tier FROM members ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    print("Latest Member:", row)
    
    # Check for latest appeal/ticket
    c.execute("SELECT category, subject, message FROM tickets ORDER BY id DESC LIMIT 3")
    print("Latest Tickets:", c.fetchall())
    
    conn.close()

if __name__ == "__main__":
    check()
