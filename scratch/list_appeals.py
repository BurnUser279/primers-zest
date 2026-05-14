import sqlite3
conn = sqlite3.connect('dev_database.db')
c = conn.cursor()
c.execute("SELECT id, message, status FROM tickets WHERE category='APPEAL' AND status='Open'")
rows = c.fetchall()
for row in rows:
    print(row)
conn.close()
