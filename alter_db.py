import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
c = conn.cursor()
try:
    c.execute('ALTER TABLE tickets ADD COLUMN admin_reply TEXT')
    print("Added admin_reply column to tickets table")
except Exception as e:
    print(f"Error: {e}")
conn.commit()
conn.close()
