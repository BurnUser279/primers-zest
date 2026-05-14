import sqlite3
conn = sqlite3.connect('dev_database.db')
c = conn.cursor()
c.execute("PRAGMA table_info(chatroom_messages)")
for row in c.fetchall():
    print(row)
conn.close()
