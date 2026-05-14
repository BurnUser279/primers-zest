import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv('REMOTE_DATABASE_URL')

def setup_cards():
    conn = psycopg2.connect(DATABASE_URL)
    c = conn.cursor()
    
    # Create table
    c.execute("""
        CREATE TABLE IF NOT EXISTS membership_cards (
            id SERIAL PRIMARY KEY,
            tier_name VARCHAR(50) NOT NULL,
            price DECIMAL(10, 2) NOT NULL,
            features TEXT NOT NULL,
            image_path TEXT NOT NULL
        )
    """)
    
    # Seed data
    cards = [
        ('Silver Card', 250.00, 'Exclusive access to Silver events, Priority support, 10% discount on Star bookings', 'silver_membership_card.png'),
        ('Gold Card', 750.00, 'All Silver benefits, VIP lounge access, 25% discount on Star bookings, Personal concierge', 'gold_membership_card.png'),
        ('Platinum Card', 2500.00, 'Ultimate executive access, Private jet concierge, 50% discount on Star bookings, Lifetime VIP status', 'platinum_membership_card.png')
    ]
    
    for tier, price, features, img in cards:
        c.execute("SELECT id FROM membership_cards WHERE tier_name = %s", (tier,))
        if not c.fetchone():
            c.execute("INSERT INTO membership_cards (tier_name, price, features, image_path) VALUES (%s, %s, %s, %s)",
                      (tier, price, features, f"/static/uploads/{img}"))
    
    conn.commit()
    conn.close()
    print("Membership cards table setup and seeded.")

if __name__ == "__main__":
    setup_cards()
