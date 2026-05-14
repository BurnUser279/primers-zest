import os
import psycopg2

def run_migrations():
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        print("Error: DATABASE_URL environment variable not found.")
        return

    try:
        conn = psycopg2.connect(db_url)
        c = conn.cursor()
        
        # 0. members (Standard table)
        c.execute("""
            CREATE TABLE IF NOT EXISTS members (
                id SERIAL PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                mobile TEXT NOT NULL UNIQUE,
                fullname TEXT NOT NULL,
                username TEXT NOT NULL,
                age INTEGER NOT NULL,
                gender TEXT NOT NULL,
                travel TEXT NOT NULL,
                income TEXT NOT NULL,
                medical TEXT,
                password_hash TEXT NOT NULL,
                membership_tier TEXT DEFAULT 'Regular',
                vip_admin_reply TEXT,
                vip_user_proof TEXT,
                is_verified BOOLEAN DEFAULT FALSE,
                is_active BOOLEAN DEFAULT TRUE,
                failed_attempts INTEGER DEFAULT 0,
                is_locked BOOLEAN DEFAULT FALSE,
                country VARCHAR(100),
                state VARCHAR(100),
                industry VARCHAR(255),
                net_worth VARCHAR(255),
                can_write_news BOOLEAN DEFAULT FALSE,
                can_write_insights BOOLEAN DEFAULT FALSE,
                bio TEXT,
                profile_photo TEXT,
                role VARCHAR(20) DEFAULT 'Member',
                vip_since TIMESTAMP
            )
        """)

        # 1. admin_audit_logs
        c.execute("""
            CREATE TABLE IF NOT EXISTS admin_audit_logs (
                id SERIAL PRIMARY KEY,
                admin_id INTEGER REFERENCES members(id),
                action VARCHAR(100) NOT NULL,
                target_type VARCHAR(50),
                target_id INTEGER,
                details TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 2. stars
        c.execute("""
            CREATE TABLE IF NOT EXISTS stars (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                category VARCHAR(255),
                bio TEXT,
                price VARCHAR(100),
                image_path TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 3. star_bookings
        c.execute("""
            CREATE TABLE IF NOT EXISTS star_bookings (
                id SERIAL PRIMARY KEY,
                member_id INTEGER REFERENCES members(id),
                star_id INTEGER REFERENCES stars(id),
                request_details TEXT,
                chatroom_id INTEGER REFERENCES chatrooms(id),
                status VARCHAR(50) DEFAULT 'Pending',
                occasion VARCHAR(255),
                timeframe VARCHAR(255),
                start_time VARCHAR(255),
                address TEXT,
                recipient VARCHAR(255),
                arrival_time VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 4. email_logs
        c.execute("""
            CREATE TABLE IF NOT EXISTS email_logs (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES members(id),
                subject TEXT,
                body TEXT,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 5. crypto_wallets
        c.execute("""
            CREATE TABLE IF NOT EXISTS crypto_wallets (
                id SERIAL PRIMARY KEY,
                currency VARCHAR(50),
                network VARCHAR(50),
                address TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 6. chatroom_reactions
        c.execute("""
            CREATE TABLE IF NOT EXISTS chatroom_reactions (
                id SERIAL PRIMARY KEY,
                message_id INTEGER REFERENCES chatroom_messages(id),
                member_id INTEGER REFERENCES members(id),
                emoji VARCHAR(50),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 7. message_reactions
        c.execute("""
            CREATE TABLE IF NOT EXISTS message_reactions (
                id SERIAL PRIMARY KEY,
                message_id INTEGER REFERENCES chatroom_messages(id),
                reaction_type VARCHAR(20),
                count INTEGER DEFAULT 1,
                is_artificial BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 8. admin_notifications
        c.execute("""
            CREATE TABLE IF NOT EXISTS admin_notifications (
                id SERIAL PRIMARY KEY,
                member_id INTEGER REFERENCES members(id),
                action_type VARCHAR(100),
                message TEXT,
                target_url TEXT,
                is_read BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 9. member_notifications
        c.execute("""
            CREATE TABLE IF NOT EXISTS member_notifications (
                id SERIAL PRIMARY KEY,
                member_id INTEGER REFERENCES members(id),
                action_type VARCHAR(100),
                message TEXT,
                target_url TEXT,
                is_read BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 10. tickets
        c.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES members(id),
                category TEXT NOT NULL,
                message TEXT NOT NULL,
                attachment TEXT,
                status TEXT DEFAULT 'Open',
                admin_reply TEXT,
                admin_media TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 11. attachments
        c.execute("""
            CREATE TABLE IF NOT EXISTS attachments (
                id SERIAL PRIMARY KEY,
                ticket_id INTEGER REFERENCES tickets(id),
                file_path TEXT NOT NULL,
                uploaded_by_admin BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 12. donations
        c.execute("""
            CREATE TABLE IF NOT EXISTS donations (
                id SERIAL PRIMARY KEY,
                member_id INTEGER REFERENCES members(id),
                amount REAL NOT NULL,
                visibility_preference TEXT NOT NULL,
                status TEXT DEFAULT 'Pending',
                admin_reply TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 13. subscription_plans
        c.execute("""
            CREATE TABLE IF NOT EXISTS subscription_plans (
                id SERIAL PRIMARY KEY,
                plan_name TEXT NOT NULL,
                price REAL NOT NULL,
                features TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 14. vip_submissions
        c.execute("""
            CREATE TABLE IF NOT EXISTS vip_submissions (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES members(id),
                plan_id INTEGER REFERENCES subscription_plans(id),
                payment_method VARCHAR(50),
                transaction_hash TEXT,
                wire_reference TEXT,
                giftcard_code TEXT,
                status VARCHAR(50) DEFAULT 'Pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 15. club_slideshows
        c.execute("""
            CREATE TABLE IF NOT EXISTS club_slideshows (
                id SERIAL PRIMARY KEY,
                image_path VARCHAR(255) NOT NULL,
                info_text TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 16. site_settings
        c.execute("""
            CREATE TABLE IF NOT EXISTS site_settings (
                id SERIAL PRIMARY KEY,
                setting_key VARCHAR(50) UNIQUE,
                setting_value TEXT
            )
        """)

        # 17. system_settings
        c.execute("""
            CREATE TABLE IF NOT EXISTS system_settings (
                id SERIAL PRIMARY KEY,
                support_email TEXT
            )
        """)

        # 18. vip_periods
        c.execute("""
            CREATE TABLE IF NOT EXISTS vip_periods (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES members(id),
                start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                end_time TIMESTAMP
            )
        """)

        # 19. email_templates
        c.execute("""
            CREATE TABLE IF NOT EXISTS email_templates (
                id SERIAL PRIMARY KEY,
                event_type TEXT NOT NULL UNIQUE,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                trigger_event TEXT DEFAULT 'Manual',
                plan_id INTEGER
            )
        """)

        # --- Schema Patching (For existing tables) ---
        c.execute("ALTER TABLE donations ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        c.execute("ALTER TABLE members ADD COLUMN IF NOT EXISTS kyc_status VARCHAR(20) DEFAULT 'Unverified'")
        
        conn.commit()
        conn.close()
        print("Secure database migration completed and committed successfully.")
    except Exception as e:
        print(f"Migration Error: {e}")

if __name__ == "__main__":
    run_migrations()
