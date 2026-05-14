import sqlite3
conn = sqlite3.connect('dev_database.db')
c = conn.cursor()
c.execute("SELECT is_read FROM admin_notifications WHERE message = 'Test Unread Notification'")
row = c.fetchone()
if row:
    print(f"is_read: {row[0]}")
else:
    print("Notification not found")
conn.close()
