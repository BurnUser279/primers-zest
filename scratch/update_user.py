import sqlite3

db_path = 'dev_database.db'
conn = sqlite3.connect(db_path)
c = conn.cursor()

# Generate secure password hash for 'password'
pw_hash = 'scrypt:32768:8:1$iMa4Sb6w86VP5WKG$cdac06823c60388646b67fd369e6e21f9dde7f7e3d476c40c66748e44fc157e6d96d5048e0131a3310880cfb740d1522149e0d97092252f5f6d87969ddb2f65c'

c.execute("""
    UPDATE members 
    SET password_hash = ?, membership_tier = 'VIP', is_active = 1, is_locked = 0 
    WHERE email = 'test@example.com'
""", (pw_hash,))

c.execute("""
    UPDATE members 
    SET password_hash = ?, membership_tier = 'VIP', is_active = 1, is_locked = 0, role = 'Admin'
    WHERE username = 'AdminMaster'
""", (pw_hash,))

conn.commit()
print("Updated rows:", c.rowcount)
conn.close()
