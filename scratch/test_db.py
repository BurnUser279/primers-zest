import sqlite3
import os

db_path = 'dev_database.db'
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    # 1. Check tables
    c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [t[0] for t in c.fetchall()]
    print(f"Tables: {tables}")
    
    if 'chatrooms' in tables:
        c.execute("SELECT id, room_name FROM chatrooms")
        rooms = c.fetchall()
        print(f"Rooms: {rooms}")
    
    if 'members' in tables:
        # Check columns
        c.execute("PRAGMA table_info(members)")
        cols = [col[1] for col in c.fetchall()]
        print(f"Member columns: {cols}")
        
        # Check if new columns exist
        print(f"can_write_news exists: {'can_write_news' in cols}")
        print(f"can_write_insights exists: {'can_write_insights' in cols}")
    
    conn.close()
else:
    print("Database not found")
