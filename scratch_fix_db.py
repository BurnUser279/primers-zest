import sqlite3
with sqlite3.connect('prototype.db') as conn:
    c = conn.cursor()
    c.execute("UPDATE members SET membership_tier = 'Regular' WHERE membership_tier = 'Pending' OR membership_tier IS NULL")
    conn.commit()
print("Fixed existing db.")
