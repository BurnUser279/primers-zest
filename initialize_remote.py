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
