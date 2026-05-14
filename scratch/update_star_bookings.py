import app
import sqlite3

def setup():
    conn, db_type = app.get_db_connection()
    c = app.get_cursor(conn, db_type)
    
    # Add columns if they don't exist
    cols_to_add = [
        ('occasion', 'TEXT'),
        ('timeframe', 'TEXT'),
        ('start_time', 'TEXT'),
        ('address', 'TEXT'),
        ('recipient', 'TEXT'),
        ('arrival_time', 'TEXT')
    ]
    
    for col_name, col_type in cols_to_add:
        try:
            c.execute(f"ALTER TABLE star_bookings ADD COLUMN {col_name} {col_type}")
            print(f"Added column {col_name}")
        except Exception as e:
            print(f"Column {col_name} might already exist: {e}")
            
    # Add setting
    try:
        c.execute("INSERT OR IGNORE INTO site_settings (setting_key, setting_value) VALUES (?, ?)", 
                  ('star_occasions', 'Birthday, Anniversary, Expert Advice, Personal Shoutout, Business Endorsement, Other'))
        print("Initialized star_occasions setting")
    except Exception as e:
        print(f"Error initializing setting: {e}")
        
    conn.commit()
    conn.close()

if __name__ == "__main__":
    setup()
