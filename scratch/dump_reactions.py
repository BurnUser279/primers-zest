import sqlite3
import json

conn = sqlite3.connect('dev_database.db')
c = conn.cursor()

def get_table_data(table_name):
    try:
        c.execute(f"SELECT * FROM {table_name}")
        return [list(row) for row in c.fetchall()]
    except Exception as e:
        return str(e)

data = {
    'lounge_message_reactions': get_table_data('lounge_message_reactions'),
    'chatroom_reactions': get_table_data('chatroom_reactions')
}

with open('reaction_data.json', 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

conn.close()
print("Data written to reaction_data.json")
