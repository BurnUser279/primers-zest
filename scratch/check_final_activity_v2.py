import sqlite3
import os

def check():
    db_path = 'dev_database.db'
    if not os.path.exists(db_path):
        return
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT fullname, email, is_active FROM members ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    print("Latest Member:", row)
    
    # Check for latest tickets
    c.execute("SELECT category, message FROM tickets ORDER BY id DESC LIMIT 5")
    print("Latest Tickets:", c.fetchall())
    
    # Check for latest chat messages (concierge/star)
    try:
        c.execute("SELECT message FROM chat_messages ORDER BY id DESC LIMIT 5")
        print("Latest Chat Messages:", c.fetchall())
    except:
        pass
        
    conn.close()

if __name__ == "__main__":
    check()
