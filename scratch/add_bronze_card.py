import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv('REMOTE_DATABASE_URL')

def add_bronze_card():
    conn = psycopg2.connect(DATABASE_URL)
    c = conn.cursor()
    
    # Check if Bronze exists
    c.execute("SELECT COUNT(*) FROM membership_cards WHERE tier_name = 'Bronze Card'")
    if c.fetchone()[0] == 0:
        c.execute("""
            INSERT INTO membership_cards (tier_name, price, features, image_path) 
            VALUES (%s, %s, %s, %s)
        """, ('Bronze Card', 100.00, 'Standard access to community features, Basic support, Regular member status', '/static/uploads/bronze_membership_card.png'))
        print("Bronze Card added.")
    else:
        print("Bronze Card already exists.")
        
    conn.commit()
    conn.close()

if __name__ == "__main__":
    add_bronze_card()
