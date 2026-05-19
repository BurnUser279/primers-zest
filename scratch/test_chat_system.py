import os
import sys
import unittest
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app import app, get_db_connection, get_cursor

class ChatSystemTests(unittest.TestCase):
    def setUp(self):
        # Configure app for testing
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'testsecret'
        self.client = app.test_client()
        
        # Open connection to clean up or set up test records
        self.conn, self.db_type = get_db_connection()
        self.c = get_cursor(self.conn, self.db_type)
        
        # Ensure a test member exists
        self.c.execute("DELETE FROM members WHERE email = 'testchatuser@example.com'")
        if self.db_type == 'postgres':
            self.c.execute("""
                INSERT INTO members (username, email, password_hash, fullname, role, membership_tier) 
                VALUES ('testchatuser', 'testchatuser@example.com', 'hashedpassword', 'Test Chat Member', 'Member', 'VIP') 
                RETURNING id
            """)
            self.member_id = self.c.fetchone()[0]
        else:
            self.c.execute("""
                INSERT INTO members (username, email, password_hash, fullname, role, membership_tier) 
                VALUES ('testchatuser', 'testchatuser@example.com', 'hashedpassword', 'Test Chat Member', 'Member', 'VIP')
            """)
            self.member_id = self.c.lastrowid
        self.conn.commit()

    def tearDown(self):
        # Clean up database records
        self.c.execute("DELETE FROM vip_pre_payment_chats WHERE member_id = %s", (self.member_id,))
        self.c.execute("DELETE FROM chatroom_messages WHERE sender_id = %s", (self.member_id,))
        self.c.execute("DELETE FROM members WHERE id = %s", (self.member_id,))
        self.conn.commit()
        self.conn.close()

    def test_vip_concierge_chat_route(self):
        print("\n--- Testing VIP Concierge Chat ---")
        # Log in the simulated member via session variables in Flask
        with self.client.session_transaction() as sess:
            sess['member_id'] = self.member_id
            sess['member_fullname'] = 'Test Chat Member'
            sess['membership_tier'] = 'VIP'

        # 1. Send message to payment concierge
        response = self.client.post(f'/vip_chat/send/{self.member_id}', data={
            'message': 'Hello payment concierge!'
        })
        self.assertEqual(response.status_code, 302) # Redirects back as expected

        # 2. Check if message was saved to database
        self.c.execute("SELECT * FROM vip_pre_payment_chats WHERE member_id = %s ORDER BY timestamp DESC LIMIT 1", (self.member_id,))
        chat = self.c.fetchone()
        self.assertIsNotNone(chat)
        
        # Handles both DictRow / Row and tuple indexing
        msg_text = chat['message'] if hasattr(chat, 'keys') or isinstance(chat, dict) else chat[4]
        sender = chat['sender_id'] if hasattr(chat, 'keys') or isinstance(chat, dict) else chat[3]
        
        self.assertEqual(msg_text, 'Hello payment concierge!')
        self.assertEqual(sender, self.member_id)
        print("-> VIP Concierge Message successfully delivered & stored in database!")

    def test_vip_lounge_chat_route(self):
        print("\n--- Testing VIP Lounge Chat ---")
        # Log in user
        with self.client.session_transaction() as sess:
            sess['member_id'] = self.member_id
            sess['member_fullname'] = 'Test Chat Member'
            sess['membership_tier'] = 'VIP'

        # Get room_id of 'VIP Lounge'
        self.c.execute("SELECT id FROM chatrooms WHERE room_name = 'VIP Lounge' LIMIT 1")
        row = self.c.fetchone()
        room_id = row[0] if row else None
        
        if not room_id:
            if self.db_type == 'postgres':
                self.c.execute("INSERT INTO chatrooms (room_name) VALUES ('VIP Lounge') RETURNING id")
                room_id = self.c.fetchone()[0]
            else:
                self.c.execute("INSERT INTO chatrooms (room_name) VALUES ('VIP Lounge')")
                room_id = self.c.lastrowid
            self.conn.commit()

        # Send a VIP Lounge Message
        response = self.client.post('/vip_lounge', data={
            'message_text': 'Hello Lounge Chat!',
            'channel_id': 'main'
        })
        self.assertEqual(response.status_code, 302)

        # Verify saved message in database
        self.c.execute("SELECT * FROM chatroom_messages WHERE room_id = %s AND sender_id = %s ORDER BY created_at DESC LIMIT 1", (room_id, self.member_id))
        msg = self.c.fetchone()
        self.assertIsNotNone(msg)
        
        text = msg['message_text'] if hasattr(msg, 'keys') or isinstance(msg, dict) else msg[3]
        self.assertEqual(text, 'Hello Lounge Chat!')
        print("-> VIP Lounge Message successfully delivered & stored in database!")

if __name__ == '__main__':
    unittest.main()
