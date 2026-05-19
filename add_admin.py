import sys
import os
from app import app, get_db_connection, get_cursor

with app.app_context():
    try:
        conn, db_type = get_db_connection()
        c = get_cursor(conn, db_type)
        
        c.execute("SELECT id FROM members WHERE username = 'AdminMaster'")
        if not c.fetchone():
            c.execute("""
                INSERT INTO members (username, email, mobile, fullname, age, gender, travel, income, password_hash, role)
                VALUES ('AdminMaster', 'admin@system.internal', '+0000000000', 'Administrator', 99, 'Other', 'None', 'None', 'SYSTEM_ACCOUNT', 'Admin')
            """)
            conn.commit()
            print("AdminMaster created successfully.")
        else:
            print("AdminMaster already exists.")
            
        conn.close()
    except Exception as e:
        print(f"Error: {e}")
