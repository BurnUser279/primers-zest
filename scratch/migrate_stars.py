import sqlite3
import os

def migrate():
    db_path = 'dev_database.db'
    if not os.path.exists(db_path):
        print("Database not found.")
        return

    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # Create stars table
    c.execute('''
        CREATE TABLE IF NOT EXISTS stars (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT,
            bio TEXT,
            image_path TEXT,
            price TEXT,
            is_active BOOLEAN DEFAULT 1
        )
    ''')

    # Create star_bookings table
    c.execute('''
        CREATE TABLE IF NOT EXISTS star_bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id INTEGER NOT NULL,
            star_id INTEGER NOT NULL,
            status TEXT DEFAULT 'Pending',
            request_details TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            chatroom_id INTEGER,
            FOREIGN KEY (member_id) REFERENCES members(id),
            FOREIGN KEY (star_id) REFERENCES stars(id),
            FOREIGN KEY (chatroom_id) REFERENCES chatrooms(id)
        )
    ''')

    conn.commit()
    conn.close()
    print("Star booking tables created successfully.")

if __name__ == "__main__":
    migrate()
