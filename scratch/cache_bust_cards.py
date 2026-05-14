import psycopg2
import os
from dotenv import load_dotenv
import time

load_dotenv()
DATABASE_URL = os.getenv('REMOTE_DATABASE_URL')

def apply_cache_busting():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        c = conn.cursor()
        
        # Add a timestamp as a query parameter to force browser to reload
        ts = int(time.time())
        
        c.execute("UPDATE membership_cards SET image_path = %s WHERE tier_name = 'Bronze Card'", 
                  (f'/static/uploads/bronze_membership_card.png?v={ts}',))
        c.execute("UPDATE membership_cards SET image_path = %s WHERE tier_name = 'Silver Card'", 
                  (f'/static/uploads/silver_membership_card.png?v={ts}',))
        c.execute("UPDATE membership_cards SET image_path = %s WHERE tier_name = 'Gold Card'", 
                  (f'/static/uploads/gold_membership_card.png?v={ts}',))
        c.execute("UPDATE membership_cards SET image_path = %s WHERE tier_name = 'Platinum Card'", 
                  (f'/static/uploads/platinum_membership_card.png?v={ts}',))
        
        print(f"Cache busting applied with v={ts}")
        
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    apply_cache_busting()
