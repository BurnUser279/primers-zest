import sqlite3
conn = sqlite3.connect('dev_database.db')
c = conn.cursor()
c.execute("SELECT message, is_read FROM admin_notifications")
for row in c.fetchall():
    print(f"Message: {row[0]}, is_read: {row[1]}")
conn.close()
