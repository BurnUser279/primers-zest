import os
import psycopg2
from dotenv import load_dotenv

# Load environment logic
load_dotenv()

# Initialize the db using app's function
from app import init_db

try:
    init_db()
    
    # Establish connection
    db_url = os.environ.get('DATABASE_URL')
    conn = psycopg2.connect(db_url)
    c = conn.cursor()
    
    try:
        c.execute("ALTER TABLE tickets ADD COLUMN admin_reply TEXT;")
        conn.commit()
    except Exception as e:
        conn.rollback()

    try:
        c.execute("ALTER TABLE tickets ADD COLUMN admin_media TEXT;")
        conn.commit()
    except Exception as e:
        conn.rollback()

    try:
        c.execute('''CREATE TABLE IF NOT EXISTS attachments
                     (id SERIAL PRIMARY KEY,
                      ticket_id INTEGER NOT NULL,
                      file_path TEXT NOT NULL,
                      uploaded_by_admin BOOLEAN DEFAULT FALSE,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      FOREIGN KEY(ticket_id) REFERENCES tickets(id))''')
        conn.commit()
    except Exception as e:
        conn.rollback()

    try:
        c.execute('''CREATE TABLE IF NOT EXISTS secure_tokens
                     (id SERIAL PRIMARY KEY,
                      attachment_id INTEGER NOT NULL,
                      token_string TEXT UNIQUE NOT NULL,
                      expires_at TIMESTAMP NOT NULL,
                      is_used BOOLEAN DEFAULT FALSE,
                      FOREIGN KEY(attachment_id) REFERENCES attachments(id))''')
        conn.commit()
    except Exception as e:
        conn.rollback()

    try:
        c.execute('''CREATE TABLE IF NOT EXISTS chatrooms
                     (id SERIAL PRIMARY KEY,
                      room_name TEXT NOT NULL,
                      created_by_admin_id INTEGER,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        c.execute("INSERT INTO chatrooms (room_name) SELECT 'VIP Lounge' WHERE NOT EXISTS (SELECT 1 FROM chatrooms WHERE room_name = 'VIP Lounge');")
        conn.commit()
    except Exception as e:
        conn.rollback()

    try:
        c.execute("ALTER TABLE members ADD COLUMN IF NOT EXISTS vip_since TIMESTAMP;")
        conn.commit()
    except Exception as e:
        conn.rollback()

    try:
        c.execute('''CREATE TABLE IF NOT EXISTS chatroom_attachments
                     (id SERIAL PRIMARY KEY,
                      message_id INTEGER NOT NULL,
                      file_path TEXT NOT NULL,
                      file_size BIGINT,
                      FOREIGN KEY(message_id) REFERENCES chatroom_messages(id))''')
        conn.commit()
    except Exception as e:
        conn.rollback()

    try:
        c.execute('''CREATE TABLE IF NOT EXISTS chatroom_messages
                     (id SERIAL PRIMARY KEY,
                      room_id INTEGER NOT NULL,
                      sender_id INTEGER NOT NULL,
                      message_text TEXT NOT NULL,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      FOREIGN KEY(room_id) REFERENCES chatrooms(id),
                      FOREIGN KEY(sender_id) REFERENCES members(id))''')
        conn.commit()
    except Exception as e:
        conn.rollback()

    try:
        # Create table if not exists
        c.execute('''CREATE TABLE IF NOT EXISTS subscription_plans
                     (id SERIAL PRIMARY KEY,
                      plan_name TEXT NOT NULL,
                      price REAL NOT NULL)''')
        
        # safely add features column if it doesn't exist
        c.execute("ALTER TABLE subscription_plans ADD COLUMN IF NOT EXISTS features TEXT")
        
        # Strict Seeding Check
        c.execute("SELECT COUNT(*) FROM subscription_plans")
        count = c.fetchone()[0]
        if count == 0:
            print("Detected empty subscription plans. Seeding defaults...")
            default_monthly = "Immediate VIP Lounge access\nStandard resolution media previews\nBasic historical chat access"
            default_quarterly = "Priority Admin support\nFull resolution media previews\nExtended historical chat access"
            default_annual = "All VIP Lounge features\nElite 'Founding Member' status\nFull lifetime history synchronization"
            
            c.execute("INSERT INTO subscription_plans (plan_name, price, features) VALUES (%s, %s, %s), (%s, %s, %s), (%s, %s, %s)",
                      ('Monthly', 9.99, default_monthly, 
                       'Quarterly', 24.99, default_quarterly, 
                       'Annual', 89.99, default_annual))
        conn.commit()
        print(f"Subscription plans table ready (Current Count: {count if count > 0 else 3}).")
    except Exception as e:
        conn.rollback()
        print(f"Error seeding subscription plans: {e}")

    try:
        c.execute('''CREATE TABLE IF NOT EXISTS vip_periods
                     (id SERIAL PRIMARY KEY,
                      user_id INTEGER NOT NULL,
                      start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      end_time TIMESTAMP,
                      FOREIGN KEY(user_id) REFERENCES members(id))''')
        
        # Migration: Create periods for legacy VIPs
        c.execute("""
            INSERT INTO vip_periods (user_id, start_time)
            SELECT id, vip_since FROM members 
            WHERE membership_tier = 'VIP' AND vip_since IS NOT NULL
            AND NOT EXISTS (SELECT 1 FROM vip_periods WHERE user_id = members.id)
        """)
        conn.commit()
        print("vip_periods table ready and legacy data migrated.")
    except Exception as e:
        print(f"Error initializing vip_periods: {e}")
        conn.rollback()

    c.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public';")
    tables = c.fetchall()
    conn.close()
    
    print("Tables found:")
    for table in tables:
        print(table[0])
except Exception as e:
    import sys
    print(f"FAILED: {e}")
    sys.exit(1)
