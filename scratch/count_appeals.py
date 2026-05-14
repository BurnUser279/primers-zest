import sqlite3
conn = sqlite3.connect('dev_database.db')
c = conn.cursor()
c.execute("SELECT COUNT(*) FROM tickets WHERE category='APPEAL' AND status='Open'")
count = c.fetchone()[0]
print(f"Open Appeals: {count}")
conn.close()
