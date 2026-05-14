import os
import psycopg2
from werkzeug.security import generate_password_hash
from dotenv import load_dotenv

load_dotenv()

conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
c = conn.cursor()

admin_recovery_hash = generate_password_hash('admin123')
c.execute("UPDATE members SET password_hash = %s WHERE username = 'AdminMaster'", (admin_recovery_hash,))
conn.commit()
conn.close()

print("AdminMaster password has been reset to 'admin123'")
