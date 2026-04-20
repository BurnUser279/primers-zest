import os
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
load_dotenv()
from flask import Flask, render_template, request, session, redirect, url_for, flash
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = 'super_secret_prototype_key' 
app.config['UPLOAD_FOLDER'] = 'static/uploads'

# Ensure the upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True) 

def init_db():
    conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS members
                 (id SERIAL PRIMARY KEY,
                  email TEXT NOT NULL UNIQUE,
                  mobile TEXT NOT NULL UNIQUE,
                  fullname TEXT NOT NULL,
                  username TEXT NOT NULL,
                  age INTEGER NOT NULL,
                  gender TEXT NOT NULL,
                  travel TEXT NOT NULL,
                  income TEXT NOT NULL,
                  medical TEXT,
                  password_hash TEXT NOT NULL,
                  membership_tier TEXT DEFAULT 'Regular',
                  vip_admin_reply TEXT,
                  vip_user_proof TEXT)''')
                  
    c.execute('''CREATE TABLE IF NOT EXISTS donations
                 (id SERIAL PRIMARY KEY,
                  member_id INTEGER NOT NULL,
                  amount REAL NOT NULL,
                  visibility_preference TEXT NOT NULL,
                  status TEXT DEFAULT 'Pending',
                  admin_reply TEXT,
                  FOREIGN KEY(member_id) REFERENCES members(id))''')
                  
    c.execute('''CREATE TABLE IF NOT EXISTS tickets
                 (id SERIAL PRIMARY KEY,
                  user_id INTEGER NOT NULL,
                  category TEXT NOT NULL,
                  message TEXT NOT NULL,
                  attachment TEXT,
                  status TEXT DEFAULT 'Open',
                  admin_reply TEXT,
                  admin_media TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY(user_id) REFERENCES members(id))''')

    c.execute('''CREATE TABLE IF NOT EXISTS attachments
                 (id SERIAL PRIMARY KEY,
                  ticket_id INTEGER NOT NULL,
                  file_path TEXT NOT NULL,
                  uploaded_by_admin BOOLEAN DEFAULT FALSE,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY(ticket_id) REFERENCES tickets(id))''')
    conn.commit()
    conn.close()

@app.route('/')
def landing():
    return render_template('landing.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        try:
            final_fullname = request.form['fullname'].strip()
            if len(final_fullname.split()) < 2:
                return "System Crash Report: You must enter both a First and Last name."

            final_gender = request.form['gender']
            if final_gender == 'Other':
                final_gender = request.form.get('gender_other', 'Other')

            # Check passwords before hashing
            raw_password = request.form['password']
            confirm_password = request.form['confirm_password']
            
            if raw_password != confirm_password:
                return "System Crash Report: Passwords do not match. Try again."
                
            hashed_pw = generate_password_hash(raw_password)

            conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
            c = conn.cursor()
            c.execute("INSERT INTO members (email, mobile, fullname, username, age, gender, travel, income, medical, password_hash, membership_tier) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'Regular')",
                      (request.form['email'], request.form['mobile'], final_fullname, request.form['username'], request.form['age'], final_gender, request.form['travel'], request.form['income'], request.form['medical'], hashed_pw))
            conn.commit()
            conn.close()
            return redirect(url_for('member_login'))
        except Exception as e:
            return f"System Crash Report: {str(e)}"
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def member_login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
        c = conn.cursor()
        c.execute("SELECT id, fullname, password_hash FROM members WHERE email = %s", (email,))
        member = c.fetchone()
        conn.close()
            
        # member[2] is the scrambled hash. We compare the raw password to the hash.
        if member and check_password_hash(member[2], password):
            session['member_id'] = member[0]
            session['member_fullname'] = member[1]
            return redirect(url_for('member_dashboard'))
        else:
            return "Invalid Email or Password."
            
    return render_template('member_login.html')

@app.route('/dashboard', methods=['GET', 'POST'])
def member_dashboard():
    if 'member_id' not in session:
        return redirect(url_for('member_login'))
        
    if request.method == 'POST':
        amount = request.form.get('amount')
        visibility = request.form.get('visibility_preference')
        
        conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
        c = conn.cursor()
        c.execute("INSERT INTO donations (member_id, amount, visibility_preference, status) VALUES (%s, %s, %s, 'Pending')",
                  (session['member_id'], amount, visibility))
        conn.commit()
        conn.close()
        
        return redirect(url_for('member_dashboard'))
        
    conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
    c = conn.cursor()
    c.execute("SELECT amount, status, admin_reply FROM donations WHERE member_id = %s", (session['member_id'],))
    my_donations = c.fetchall()
    
    c.execute("SELECT id, category, message, attachment, status, admin_reply, admin_media FROM tickets WHERE user_id = %s ORDER BY created_at DESC", (session['member_id'],))
    my_tickets = c.fetchall()
    
    c.execute("SELECT membership_tier, vip_admin_reply, vip_user_proof FROM members WHERE id = %s", (session['member_id'],))
    status_row = c.fetchone()
    current_tier = status_row[0] if status_row else 'Regular'
    admin_reply = status_row[1] if status_row else None
    user_proof = status_row[2] if status_row else None
    conn.close()
        
    return render_template('dashboard.html', fullname=session.get('member_fullname'), donations=my_donations, tickets=my_tickets, membership_tier=current_tier, vip_admin_reply=admin_reply, vip_user_proof=user_proof)

@app.route('/dashboard/request_vip', methods=['POST'])
def dashboard_request_vip():
    if 'member_id' not in session:
        return redirect(url_for('member_login'))
    conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
    c = conn.cursor()
    c.execute("UPDATE members SET membership_tier = 'Pending Request' WHERE id = %s", (session['member_id'],))
    conn.commit()
    conn.close()
    return redirect(url_for('member_dashboard'))

@app.route('/dashboard/submit_proof', methods=['POST'])
def dashboard_submit_proof():
    if 'member_id' not in session:
        return redirect(url_for('member_login'))
        
    proof_text = request.form.get('proof_text', '').strip()
    proof_file = request.files.get('proof_file')
    
    proof_reference = proof_text
    
    if proof_file and proof_file.filename != '':
        filename = secure_filename(proof_file.filename)
        filename = f"m{session['member_id']}_{filename}"
        proof_file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        proof_reference = f"/static/uploads/{filename}"
        
    conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
    c = conn.cursor()
    c.execute("UPDATE members SET membership_tier = 'Payment Received', vip_user_proof = %s WHERE id = %s", (proof_reference, session['member_id']))
    conn.commit()
    conn.close()
    return redirect(url_for('member_dashboard'))

@app.route('/profile')
def member_profile():
    if 'member_id' not in session:
        return redirect(url_for('member_login'))
    conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
    c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    c.execute("SELECT * FROM members WHERE id = %s", (session['member_id'],))
    member = c.fetchone()
    conn.close()
    return render_template('profile.html', member=member)

@app.route('/support', methods=['GET', 'POST'])
def support():
    if 'member_id' not in session:
        return redirect(url_for('member_login'))
        
    if request.method == 'POST':
        category = request.form.get('category')
        message = request.form.get('message')
        attachments = request.files.getlist('attachment')
        
        conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
        c = conn.cursor()
        
        # Insert ticket first to get ticket_id
        c.execute("INSERT INTO tickets (user_id, category, message) VALUES (%s, %s, %s) RETURNING id",
                  (session['member_id'], category, message))
        ticket_id = c.fetchone()[0]
        
        for file in attachments:
            if file and file.filename != '':
                filename = secure_filename(file.filename)
                filename = f"ticket_t{ticket_id}_m{session['member_id']}_{filename}"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                file_path = f"/static/uploads/{filename}"
                
                # Insert into attachments table
                c.execute("INSERT INTO attachments (ticket_id, file_path) VALUES (%s, %s)",
                          (ticket_id, file_path))
        
        conn.commit()
        conn.close()
        flash('Message sent successfully')
        return redirect(url_for('member_dashboard'))
        
    return render_template('support.html')

@app.route('/admin_login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        password = request.form.get('password')
        if password == 'boss123': 
            session['is_admin'] = True
            return redirect(url_for('admin_dashboard'))
        else:
            return "Incorrect Password. Get out."
    return render_template('admin_login.html')

@app.route('/admin')
def admin_dashboard():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))

    conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
    c = conn.cursor()
    c.execute("""
        SELECT m.*, 
        (SELECT message FROM tickets t WHERE t.user_id = m.id ORDER BY created_at DESC LIMIT 1) as latest_ticket 
        FROM members m
    """)
    all_members = c.fetchall()
    
    c.execute("SELECT * FROM donations")
    all_donations = c.fetchall()
    conn.close()

    return render_template('admin.html', members=all_members, donations=all_donations)

@app.route('/admin/reset/<int:member_id>', methods=['POST'])
def admin_reset_password(member_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
        
    import string, random
    new_password = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    hashed_password = generate_password_hash(new_password)
    
    conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
    c = conn.cursor()
    c.execute("SELECT fullname FROM members WHERE id = %s", (member_id,))
    member = c.fetchone()
    
    if not member:
        conn.close()
        return "System Crash Report: Member not found."
        
    c.execute("UPDATE members SET password_hash = %s WHERE id = %s", (hashed_password, member_id))
    conn.commit()
    conn.close()
    
    return f"Password for {member[0]} has been successfully reset. Their new temporary password is: {new_password}"

@app.route('/admin/donation_reply/<int:donation_id>', methods=['POST'])
def admin_donation_reply(donation_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
        
    reply_text = request.form.get('admin_reply')
    conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
    c = conn.cursor()
    c.execute("UPDATE donations SET status = 'Approved', admin_reply = %s WHERE id = %s", (reply_text, donation_id))
    conn.commit()
    conn.close()
    
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/reply/<int:member_id>', methods=['GET', 'POST'])
def admin_reply_member(member_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
        
    if request.method == 'POST':
        admin_reply_text = request.form.get('admin_reply_text')
        media_files = request.files.getlist('admin_media')
        
        conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
        c = conn.cursor()
        
        # Determine which ticket we are replying to
        c.execute("SELECT id FROM tickets WHERE user_id = %s AND status = 'Open' ORDER BY created_at DESC LIMIT 1", (member_id,))
        ticket_row = c.fetchone()
        
        if not ticket_row:
            c.execute("SELECT id FROM tickets WHERE user_id = %s ORDER BY created_at DESC LIMIT 1", (member_id,))
            ticket_row = c.fetchone()
            
        if ticket_row:
            ticket_id = ticket_row[0]
            # Update ticket status and reply text
            c.execute("UPDATE tickets SET admin_reply = %s, status = 'Replied' WHERE id = %s", (admin_reply_text, ticket_id))
            
            # Save admin media attachments
            for file in media_files:
                if file and file.filename != '':
                    filename = secure_filename(file.filename)
                    filename = f"admin_reply_t{ticket_id}_m{member_id}_{filename}"
                    file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                    media_path = f"/static/uploads/{filename}"
                    
                    c.execute("INSERT INTO attachments (ticket_id, file_path, uploaded_by_admin) VALUES (%s, %s, TRUE)",
                              (ticket_id, media_path))
                              
        conn.commit()
        conn.close()
        return redirect(url_for('admin_dashboard'))
        
    conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
    c = conn.cursor()
    c.execute("SELECT id, message FROM tickets WHERE user_id = %s ORDER BY created_at DESC LIMIT 1", (member_id,))
    ticket_msg = c.fetchone()
    ticket_id = ticket_msg[0] if ticket_msg else None
    ticket_text = ticket_msg[1] if ticket_msg else "No ticket messages found."
    
    # Fetch attachments for this ticket
    attachments = []
    if ticket_id:
        c.execute("SELECT file_path FROM attachments WHERE ticket_id = %s AND uploaded_by_admin = FALSE", (ticket_id,))
        attachments = [row[0] for row in c.fetchall()]
        
    conn.close()

    return render_template('admin_reply.html', member_id=member_id, ticket_text=ticket_text, attachments=attachments)

@app.route('/admin/send_instructions/<int:member_id>', methods=['POST'])
def admin_send_instructions(member_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    instruction_text = request.form.get('admin_instructions', '')
    conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
    c = conn.cursor()
    c.execute("UPDATE members SET membership_tier = 'Awaiting Payment', vip_admin_reply = %s WHERE id = %s", (instruction_text, member_id))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/finalize_vip/<int:member_id>', methods=['POST'])
def admin_finalize_vip(member_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
        
    conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
    c = conn.cursor()
    c.execute("UPDATE members SET membership_tier = 'VIP' WHERE id = %s", (member_id,))
    conn.commit()
    conn.close()
    
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/demote/<int:member_id>', methods=['POST'])
def admin_demote_member(member_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
        
    conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
    c = conn.cursor()
    c.execute("UPDATE members SET membership_tier = 'Regular' WHERE id = %s", (member_id,))
    conn.commit()
    conn.close()
    
    return redirect(url_for('admin_dashboard'))

@app.route('/logout')
def member_logout():
    session.pop('member_id', None)
    session.pop('member_fullname', None)
    return redirect(url_for('member_login'))

@app.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    return redirect(url_for('admin_login'))

if __name__ == '__main__':
    init_db()
    app.run(debug=True)