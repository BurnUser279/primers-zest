import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv('REMOTE_DATABASE_URL')

def check_stars():
    conn = psycopg2.connect(DATABASE_URL)
    c = conn.cursor()
    c.execute("""
        SELECT column_name, data_type 
        FROM information_schema.columns 
        WHERE table_name = 'stars'
    """)
    for row in c.fetchall():
        print(row)
    conn.close()

if __name__ == "__main__":
    check_stars()
