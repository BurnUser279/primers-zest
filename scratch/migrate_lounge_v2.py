import sqlite3
import os

def migrate():
    db_path = 'dev_database.db'
    if not os.path.exists(db_path):
        print("Database not found.")
        return

    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # 1. Update chatroom_messages for Pinning and Channels
    try:
        c.execute("ALTER TABLE chatroom_messages ADD COLUMN is_pinned BOOLEAN DEFAULT FALSE")
        print("Added is_pinned to chatroom_messages")
    except sqlite3.OperationalError:
        print("is_pinned already exists")

    try:
        c.execute("ALTER TABLE chatroom_messages ADD COLUMN channel_id VARCHAR(50) DEFAULT 'main'")
        print("Added channel_id to chatroom_messages")
    except sqlite3.OperationalError:
        print("channel_id already exists")

    # 2. Update members for Online Status
    try:
        c.execute("ALTER TABLE members ADD COLUMN last_active TIMESTAMP")
        print("Added last_active to members")
    except sqlite3.OperationalError:
        print("last_active already exists")

    # 3. Create reactions table (Zero cost storage)
    c.execute('''CREATE TABLE IF NOT EXISTS chatroom_reactions
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  message_id INTEGER NOT NULL,
                  member_id INTEGER NOT NULL,
                  emoji TEXT NOT NULL,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY(message_id) REFERENCES chatroom_messages(id),
                  FOREIGN KEY(member_id) REFERENCES members(id),
                  UNIQUE(message_id, member_id, emoji))''')
    print("Ensured chatroom_reactions table exists")

    conn.commit()
    conn.close()

if __name__ == "__main__":
    migrate()
