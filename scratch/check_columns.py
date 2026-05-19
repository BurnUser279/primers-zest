import os
import sys
import psycopg2
from dotenv import load_dotenv
load_dotenv()

try:
    db_url = os.environ.get('REMOTE_DATABASE_URL')
    if not db_url:
        print("REMOTE_DATABASE_URL not found in .env!")
        sys.exit(1)
        
    print(f"Connecting to remote Postgres database...")
    conn = psycopg2.connect(db_url)
    c = conn.cursor()
    
    # Audit vip_pre_payment_chats table
    c.execute("""
        SELECT column_name, data_type 
        FROM information_schema.columns 
        WHERE table_name = 'vip_pre_payment_chats'
    """)
    print("\nLive Postgres 'vip_pre_payment_chats' columns:")
    rows = c.fetchall()
    if not rows:
        print("Table 'vip_pre_payment_chats' does not exist in the remote database!")
    for row in rows:
        print(row)
        
    conn.close()
except Exception as e:
    print(f"Error checking remote columns: {e}")
