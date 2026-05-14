import os
import sqlite3
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

def get_db_connection():
    db_url = os.environ.get('DATABASE_URL')
    if not db_url or not str(db_url).startswith('postgres'):
        conn = sqlite3.connect('dev_database.db')
        conn.row_factory = sqlite3.Row
        return conn, 'sqlite'
    return psycopg2.connect(db_url, cursor_factory=psycopg2.extras.DictCursor), 'postgres'

def get_cursor(conn, db_type):
    if db_type == 'sqlite':
        return conn.cursor()
    return conn.cursor()

def fix_membership_cards():
    try:
        conn, db_type = get_db_connection()
        c = get_cursor(conn, db_type)
        
        pk_type = "SERIAL PRIMARY KEY" if db_type == 'postgres' else "INTEGER PRIMARY KEY AUTOINCREMENT"
        
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS membership_cards (
                id {pk_type},
                tier_name VARCHAR(50) NOT NULL,
                price DECIMAL(10, 2) NOT NULL,
                features TEXT NOT NULL,
                image_path TEXT NOT NULL
            )
        """)
        
        # Check if they exist and update them
        cards = [
            ('Bronze Card', 100.00, 'Standard access to community features, Basic support, Regular member status', '/static/uploads/bronze_membership_card.png'),
            ('Silver Card', 250.00, 'Exclusive access to Silver events, Priority support, 10% discount on Star bookings', '/static/uploads/silver_membership_card.png'),
            ('Gold Executive Card', 750.00, 'All Silver benefits, VIP lounge access, 25% discount on Star bookings, Personal concierge', '/static/uploads/gold_membership_card.png'),
            ('Platinum Card', 2500.00, 'Ultimate executive access, Private jet concierge, 50% discount on Star bookings, Lifetime VIP status', '/static/uploads/platinum_membership_card.png')
        ]
        
        for tier, price, features, img in cards:
            if db_type == 'sqlite':
                c.execute("SELECT id FROM membership_cards WHERE tier_name = ?", (tier,))
            else:
                c.execute("SELECT id FROM membership_cards WHERE tier_name = %s", (tier,))
            
            row = c.fetchone()
            if row:
                if db_type == 'sqlite':
                    c.execute("UPDATE membership_cards SET price = ?, features = ?, image_path = ? WHERE tier_name = ?", (price, features, img, tier))
                else:
                    c.execute("UPDATE membership_cards SET price = %s, features = %s, image_path = %s WHERE tier_name = %s", (price, features, img, tier))
            else:
                if db_type == 'sqlite':
                    c.execute("INSERT INTO membership_cards (tier_name, price, features, image_path) VALUES (?, ?, ?, ?)", (tier, price, features, img))
                else:
                    c.execute("INSERT INTO membership_cards (tier_name, price, features, image_path) VALUES (%s, %s, %s, %s)", (tier, price, features, img))
        
        conn.commit()
        print(f"Successfully updated membership cards in {db_type} database.")
        
        c.execute("SELECT * FROM membership_cards")
        for row in c.fetchall():
            print(dict(row) if db_type == 'sqlite' else row)
            
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    fix_membership_cards()
