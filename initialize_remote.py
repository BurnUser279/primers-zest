import os
import psycopg2
from dotenv import load_dotenv

# Load environment logic
load_dotenv()

def run_migrations():
    """Live PostgreSQL schema migration utility."""
    try:
        # Establish connection
        db_url = os.environ.get('DATABASE_URL')
        if not db_url:
            print("DATABASE_URL not found. Skipping migrations.")
            return

        conn = psycopg2.connect(db_url)
        c = conn.cursor()
        
        # 1. Base Tables (via app.py logic)
        try:
            init_db()
        except Exception as e:
            print(f"init_db base failed: {e}")

        # 2. Schema Hardening & Migrations
        migrations = [
            # Tickets expansions
            "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS admin_reply TEXT;",
            "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS admin_media TEXT;",
            
            # Members expansions (Email Verification Fix)
            "ALTER TABLE members ADD COLUMN IF NOT EXISTS vip_since TIMESTAMP;",
            "ALTER TABLE members ADD COLUMN IF NOT EXISTS is_verified BOOLEAN DEFAULT FALSE;",
            "ALTER TABLE members ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;",
            
            # Chatroom expansions
            "ALTER TABLE subscription_plans ADD COLUMN IF NOT EXISTS features TEXT;"
        ]

        for sql in migrations:
            try:
                c.execute(sql)
                conn.commit()
            except Exception as e:
                conn.rollback()

        # 3. New Table Definitions
        # Attachments
        c.execute('''CREATE TABLE IF NOT EXISTS attachments
                     (id SERIAL PRIMARY KEY,
                      ticket_id INTEGER NOT NULL,
                      file_path TEXT NOT NULL,
                      uploaded_by_admin BOOLEAN DEFAULT FALSE,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      FOREIGN KEY(ticket_id) REFERENCES tickets(id))''')
        
        # Secure Tokens
        c.execute('''CREATE TABLE IF NOT EXISTS secure_tokens
                     (id SERIAL PRIMARY KEY,
                      attachment_id INTEGER NOT NULL,
                      token_string TEXT UNIQUE NOT NULL,
                      expires_at TIMESTAMP NOT NULL,
                      is_used BOOLEAN DEFAULT FALSE,
                      FOREIGN KEY(attachment_id) REFERENCES attachments(id))''')

        # Chatrooms
        c.execute('''CREATE TABLE IF NOT EXISTS chatrooms
                     (id SERIAL PRIMARY KEY,
                      room_name TEXT NOT NULL,
                      created_by_admin_id INTEGER,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        
        c.execute("INSERT INTO chatrooms (room_name) SELECT 'VIP Lounge' WHERE NOT EXISTS (SELECT 1 FROM chatrooms WHERE room_name = 'VIP Lounge');")

        # Chatroom Attachments
        c.execute('''CREATE TABLE IF NOT EXISTS chatroom_attachments
                     (id SERIAL PRIMARY KEY,
                      message_id INTEGER NOT NULL,
                      file_path TEXT NOT NULL,
                      file_size BIGINT,
                      FOREIGN KEY(message_id) REFERENCES chatroom_messages(id))''')

        # Chatroom Messages
        c.execute('''CREATE TABLE IF NOT EXISTS chatroom_messages
                     (id SERIAL PRIMARY KEY,
                      room_id INTEGER NOT NULL,
                      sender_id INTEGER NOT NULL,
                      message_text TEXT NOT NULL,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      FOREIGN KEY(room_id) REFERENCES chatrooms(id),
                      FOREIGN KEY(sender_id) REFERENCES members(id))''')

        # Subscription Plans
        c.execute('''CREATE TABLE IF NOT EXISTS subscription_plans
                     (id SERIAL PRIMARY KEY,
                      plan_name TEXT NOT NULL,
                      price REAL NOT NULL)''')

        # Email Templates
        c.execute('''CREATE TABLE IF NOT EXISTS email_templates
                     (id SERIAL PRIMARY KEY,
                      event_type TEXT UNIQUE NOT NULL,
                      subject TEXT NOT NULL,
                      body TEXT NOT NULL)''')

        # Step 3: Verify email_logs table creation SQL
        c.execute('''CREATE TABLE IF NOT EXISTS email_logs
                     (id SERIAL PRIMARY KEY,
                      user_id INTEGER,
                      subject TEXT NOT NULL,
                      body TEXT NOT NULL,
                      sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      FOREIGN KEY(user_id) REFERENCES members(id))''')

        # Step 2: Ensure verification_tokens targets "members" table
        c.execute('''CREATE TABLE IF NOT EXISTS verification_tokens
                     (id SERIAL PRIMARY KEY,
                      user_id INTEGER NOT NULL,
                      token_string TEXT UNIQUE NOT NULL,
                      is_used BOOLEAN DEFAULT FALSE,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      FOREIGN KEY(user_id) REFERENCES members(id))''')

        # VIP Periods
        c.execute('''CREATE TABLE IF NOT EXISTS vip_periods
                     (id SERIAL PRIMARY KEY,
                      user_id INTEGER NOT NULL,
                      start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      end_time TIMESTAMP,
                      FOREIGN KEY(user_id) REFERENCES members(id))''')

        conn.commit()

        # 4. Seeding & Data Migration
        # Legacy VIP Migration
        c.execute("""
            INSERT INTO vip_periods (user_id, start_time)
            SELECT id, vip_since FROM members 
            WHERE membership_tier = 'VIP' AND vip_since IS NOT NULL
            AND NOT EXISTS (SELECT 1 FROM vip_periods WHERE user_id = members.id)
        """)

        # Email Template Seeding
        c.execute("SELECT COUNT(*) FROM email_templates")
        if c.fetchone()[0] == 0:
            templates = [
                ('Registration', 'Welcome to Primer\'s Zest, {{name}}!', 'Hello {{name}},\n\nThank you for registering with Primer\'s Zest. Your account is now active.\n\nBest regards,\nAdministration'),
                ('VIP_Welcome', 'Congratulations! You are now a VIP Member', 'Hello {{name}},\n\nWe are excited to inform you that your VIP status has been granted! You now have full access to the VIP Lounge and exclusive features.\n\nEnjoy your stay,\nAdministration'),
                ('Admin_Reply', 'New Administrative Response', 'Hello {{name}},\n\nAn administrator has responded to your inquiry:\n\n---\n{{admin_text}}\n---\n\nPlease check your dashboard for more details.\n\nBest regards,\nAdministration'),
                ('Subscription_Success', 'Payment Verified - Access Granted', 'Hello {{name}},\n\nYour payment has been successfully verified for your chosen plan. Welcome to the elite tier of Primer\'s Zest.\n\nBest regards,\nAdministration'),
                ('VIP_Removal', 'VIP Membership Status Update', 'Hello {{name}},\n\nThis is to notify you that your VIP membership status has been removed. You will still have access to your regular account features.\n\nIf you believe this is an error, please contact support.\n\nBest regards,\nAdministration')
            ]
            c.executemany("INSERT INTO email_templates (event_type, subject, body) VALUES (%s, %s, %s)", templates)

        # Subscription Plans Seeding
        c.execute("SELECT COUNT(*) FROM subscription_plans")
        if c.fetchone()[0] == 0:
            default_monthly = "Immediate VIP Lounge access\nStandard resolution media previews\nBasic historical chat access"
            default_quarterly = "Priority Admin support\nFull resolution media previews\nExtended historical chat access"
            default_annual = "All VIP Lounge features\nElite 'Founding Member' status\nFull lifetime history synchronization"
            c.execute("INSERT INTO subscription_plans (plan_name, price, features) VALUES (%s, %s, %s), (%s, %s, %s), (%s, %s, %s)",
                      ('Monthly', 9.99, default_monthly, 
                       'Quarterly', 24.99, default_quarterly, 
                       'Annual', 89.99, default_annual))

        conn.commit()
        conn.close()
        print("Database migrations completed successfully.")
    except Exception as e:
        print(f"MIGRATION FAILED: {e}")

if __name__ == '__main__':
    run_migrations()
