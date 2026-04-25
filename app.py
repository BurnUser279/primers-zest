import os
import time
import uuid
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
load_dotenv()
from flask import Flask, render_template, request, session, redirect, url_for, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Email Notification Utility
def send_email_notification(recipient_email, subject, body, user_id=None):
    sender_email = os.environ.get('MAIL_USERNAME')
    sender_password = os.environ.get('MAIL_PASSWORD')
    
    if not sender_email or not sender_password:
        print("Email configuration missing (MAIL_USERNAME/MAIL_PASSWORD).")
        return False

    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = recipient_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'html'))

    server = smtplib.SMTP('smtp.gmail.com', 587)
    server.starttls()
    server.login(sender_email, sender_password)
    server.send_message(msg)
    server.quit()

    # Audit Logging
    if user_id:
        try:
            conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
            c = conn.cursor()
            c.execute("INSERT INTO email_logs (user_id, subject, body) VALUES (%s, %s, %s)", (user_id, subject, body))
            conn.commit()
            conn.close()
        except Exception as log_err:
            print(f"Log Error: {log_err}")

    return True

def get_templated_email(event_type, name, admin_text=None):
    try:
        conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
        c = conn.cursor()
        c.execute("SELECT subject, body FROM email_templates WHERE event_type = %s", (event_type,))
        row = c.fetchone()
        conn.close()
        
        if row:
            subject = row[0].replace('{{name}}', name)
            body = row[1].replace('{{name}}', name)
            if admin_text:
                body = body.replace('{{admin_text}}', admin_text)
            return subject, body
    except Exception as e:
        print(f"Template Error: {e}")
    return None, None

app = Flask(__name__)
app.secret_key = 'super_secret_prototype_key' 
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['CHATROOM_UPLOAD_FOLDER'] = 'static/chatroom_uploads'

# Ensure the upload folders exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True) 
os.makedirs(app.config['CHATROOM_UPLOAD_FOLDER'], exist_ok=True)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'pdf'}

@app.after_request
def add_header(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

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
                  vip_user_proof TEXT,
                  is_verified BOOLEAN DEFAULT FALSE,
                  is_active BOOLEAN DEFAULT TRUE,
                  failed_attempts INTEGER DEFAULT 0,
                  is_locked BOOLEAN DEFAULT FALSE)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS verification_tokens
                 (id SERIAL PRIMARY KEY,
                  user_id INTEGER NOT NULL,
                  token_string TEXT UNIQUE NOT NULL,
                  is_used BOOLEAN DEFAULT FALSE,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY(user_id) REFERENCES members(id))''')
                  
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

    c.execute('''CREATE TABLE IF NOT EXISTS chatroom_messages
                 (id SERIAL PRIMARY KEY,
                  room_id INTEGER NOT NULL,
                  sender_id INTEGER NOT NULL,
                  message_text TEXT NOT NULL,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY(room_id) REFERENCES chatrooms(id),
                  FOREIGN KEY(sender_id) REFERENCES members(id))''')

    c.execute('''CREATE TABLE IF NOT EXISTS subscription_plans
                 (id SERIAL PRIMARY KEY,
                  plan_name TEXT NOT NULL,
                  price REAL NOT NULL)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS system_settings
                 (id SERIAL PRIMARY KEY,
                  support_email TEXT)''')
                  
    # Ensure Official Admin Member exists for technical sender identity
    dummy_hash = generate_password_hash('AdminPostIdentity2026')
    c.execute("INSERT INTO members (email, mobile, fullname, username, age, gender, travel, income, password_hash, membership_tier) SELECT 'admin@system.local', '0000000000', 'Official Admin', 'AdminMaster', 99, 'System', 'N/A', 'Infinite', %s, 'VIP' WHERE NOT EXISTS (SELECT 1 FROM members WHERE username = 'AdminMaster');", (dummy_hash,))
    
    # Ensure system_settings exists
    c.execute("INSERT INTO system_settings (id, support_email) SELECT 1, 'support@yourdomain.com' WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE id = 1);")
    
    # Admin recovery overwrite
    admin_recovery_hash = generate_password_hash('admin123')
    c.execute("UPDATE members SET password_hash = %s WHERE username = 'AdminMaster'", (admin_recovery_hash,))
    conn.commit()

    
    # Step 1: Rescue SQL to unlock everyone on reboot (Mass Unlock Bug Fix)
    c.execute("UPDATE members SET is_locked = FALSE, failed_attempts = 0")
    
    conn.commit()
    conn.close()

@app.route('/')
def landing():
    return render_template('landing.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        try:
            email = request.form.get('email', '').strip()
            
            # Step 3: Strict database check for existing emails first
            conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
            c = conn.cursor()
            c.execute("SELECT id FROM members WHERE email = %s", (email,))
            if c.fetchone():
                conn.close()
                flash("Error: This email is already registered. Please login or use a different email.")
                return redirect(url_for('register'))
            conn.close()

            final_fullname = request.form['fullname'].strip()
            if len(final_fullname.split()) < 2:
                return "System Crash Report: You must enter both a First and Last name."

            final_gender = request.form['gender']
            if final_gender == 'Other':
                final_gender = request.form.get('gender_other', 'Other')

            # Check passwords before hashing
            raw_password = request.form['password']
            confirm_password = request.form['confirm_password']
            
            # Step 2: Specific password security enforcement message
            import re
            if len(raw_password) < 8 or not re.search(r"[a-zA-Z]", raw_password) or not re.search(r"[!@#$%^&*(),.?\":{}|<>]", raw_password):
                flash("Security Alert: password must be at least 8 characters long and include a letter and a symbol.")
                return redirect(url_for('register'))

            if raw_password != confirm_password:
                return "System Crash Report: Passwords do not match. Try again."
                
            hashed_pw = generate_password_hash(raw_password)

            conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
            c = conn.cursor()
            # Adding is_verified column usage (assuming it's added to schema)
            c.execute("INSERT INTO members (email, mobile, fullname, username, age, gender, travel, income, medical, password_hash, membership_tier, is_verified) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'Regular', FALSE) RETURNING id",
                      (email, request.form['mobile'], final_fullname, request.form['username'], request.form['age'], final_gender, request.form['travel'], request.form['income'], request.form['medical'], hashed_pw))
            new_user_id = c.fetchone()[0]
            
            # Send Registration Email (In a real system, this would include a token)
            subj, body = get_templated_email('Registration', final_fullname)
            if subj:
                send_email_notification(email, subj, body, user_id=new_user_id)
                
            conn.commit()
            conn.close()
            flash("Account created successfully. Please check your email to verify your account before logging in.")
            return redirect(url_for('member_login'))
        except Exception as e:
            return f"System Crash Report: {str(e)}"
    return render_template('register.html')

@app.route('/verify_email/<token>')
def verify_email(token):
    # Step 4: Locate/Implement verify email route with already-verified check
    conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
    c = conn.cursor()
    
    # Check if token exists and link to user
    # For this implementation, we assume token is checked against a tokens table
    c.execute("SELECT user_id, is_used FROM verification_tokens WHERE token_string = %s", (token,))
    token_row = c.fetchone()
    
    if not token_row:
        conn.close()
        flash("Invalid verification link.")
        return redirect(url_for('member_login'))
    
    user_id, is_used = token_row
    
    # Check if user is already verified (Step 4 requirement)
    c.execute("SELECT is_verified FROM members WHERE id = %s", (user_id,))
    user_row = c.fetchone()
    
    if is_used or (user_row and user_row[0]):
        conn.close()
        flash("The link has expired or already been used.")
        return redirect(url_for('member_login'))
    
    # Mark as verified and token as used
    c.execute("UPDATE members SET is_verified = TRUE WHERE id = %s", (user_id,))
    c.execute("UPDATE verification_tokens SET is_used = TRUE WHERE token_string = %s", (token,))
    
    conn.commit()
    conn.close()
    
    flash("Your email has been verified! You can now login.")
    return redirect(url_for('member_login'))

@app.route('/login', methods=['GET', 'POST'])
def member_login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
        c = conn.cursor()
        c.execute("SELECT id, fullname, password_hash, is_verified, is_active, is_locked, failed_attempts FROM members WHERE email = %s", (email,))
        member = c.fetchone()
        
        if member:
            user_id, fullname, hashed_pw, is_verified, is_active, is_locked, failed_attempts = member
            
            if is_locked:
                # Step 4: Query system settings for support email
                c.execute("SELECT support_email FROM system_settings WHERE id = 1")
                support_email = c.fetchone()[0]
                conn.close()
                flash(f'Your account has been frozen, contact the admin through <a href="mailto:{support_email}">{support_email}</a> for details on how to unfreeze your account.')
                return redirect(url_for('member_login'))
            
            if check_password_hash(hashed_pw, password):
                if not is_active:
                    conn.close()
                    flash("Account disabled. Please contact support.")
                    return redirect(url_for('member_login'))
                
                # Step 5: Reset failed attempts on success (Targeted)
                c.execute("UPDATE members SET failed_attempts = 0 WHERE email = %s", (email,))
                conn.commit()
                conn.close()
                
                session['member_id'] = user_id
                session['member_fullname'] = fullname
                return redirect(url_for('member_dashboard'))
            else:
                # Step 4: Increment failed attempts on failure (Targeted)
                new_failed = failed_attempts + 1
                if new_failed >= 5:
                    c.execute("UPDATE members SET failed_attempts = %s, is_locked = TRUE WHERE email = %s", (new_failed, email))
                    conn.commit()
                    conn.close()
                    
                    # Step 6: Hardcoded lock notification email
                    subj = "Security Alert: Account Frozen"
                    body = f"Hello {fullname},\n\nYour account has been frozen for security due to 5 consecutive failed login attempts. For your security, access has been restricted.\n\nPlease contact administration to verify your identity and unfreeze your account."
                    send_email_notification(email, subj, body, user_id=user_id)
                    
                    # Step 4: Inject dynamic support email
                    conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
                    c = conn.cursor()
                    c.execute("SELECT support_email FROM system_settings WHERE id = 1")
                    support_email = c.fetchone()[0]
                    conn.close()
                    flash(f'Your account has been frozen, contact the admin through <a href="mailto:{support_email}">{support_email}</a> for details on how to unfreeze your account.')
                else:
                    c.execute("UPDATE members SET failed_attempts = %s WHERE email = %s", (new_failed, email))
                    conn.commit()
                    conn.close()
                    flash("Invalid Email or Password.")
                return redirect(url_for('member_login'))
        else:
            conn.close()
            flash("Invalid Email or Password.")
            return redirect(url_for('member_login'))
            
    return render_template('member_login.html')

@app.route('/dashboard', methods=['GET', 'POST'])
def member_dashboard():
    if 'member_id' not in session:
        return redirect(url_for('member_login'))
        
    if request.method == 'POST':
        form_type = request.form.get('form_type')
        
        if form_type == 'new_message':
            category = request.form.get('category')
            message = request.form.get('message')
            attachments = request.files.getlist('attachment')
            
            conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
            c = conn.cursor()
            c.execute("INSERT INTO tickets (user_id, category, message) VALUES (%s, %s, %s) RETURNING id",
                      (session['member_id'], category, message))
            ticket_id = c.fetchone()[0]
            
            for file in attachments:
                if file and file.filename != '':
                    filename = secure_filename(file.filename)
                    filename = f"dash_new_m{session['member_id']}_t{ticket_id}_{filename}"
                    file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                    file_path = f"/static/uploads/{filename}"
                    c.execute("INSERT INTO attachments (ticket_id, file_path) VALUES (%s, %s)",
                              (ticket_id, file_path))
            
            conn.commit()
            conn.close()
            flash('Message successfully sent.')
            return redirect(url_for('member_dashboard'))
            
        else: # Default donation form
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
    
    c.execute("""
        SELECT t.id, t.category, t.message, t.status, t.admin_reply, a.file_path, a.uploaded_by_admin 
        FROM tickets t 
        LEFT JOIN attachments a ON t.id = a.ticket_id 
        WHERE t.user_id = %s 
        ORDER BY t.created_at DESC
        LIMIT 3
    """, (session['member_id'],))
    
    rows = c.fetchall()
    tickets_dict = {}
    for row in rows:
        t_id = row[0]
        if t_id not in tickets_dict:
            tickets_dict[t_id] = {
                'id': row[0],
                'category': row[1],
                'message': row[2],
                'status': row[3],
                'admin_reply': row[4],
                'admin_attachments': []
            }
        if row[5] and row[6]: # file_path exists and uploaded_by_admin is True
            tickets_dict[t_id]['admin_attachments'].append(row[5])
            
    my_tickets = list(tickets_dict.values())
    
    c.execute("SELECT membership_tier, vip_admin_reply, vip_user_proof FROM members WHERE id = %s", (session['member_id'],))
    status_row = c.fetchone()
    current_tier = status_row[0] if status_row else 'Regular'
    admin_reply = status_row[1] if status_row else None
    user_proof = status_row[2] if status_row else None
    c.execute("SELECT plan_name, price, features FROM subscription_plans ORDER BY id")
    plans = c.fetchall()
    conn.close()
        
    return render_template('dashboard.html', fullname=session.get('member_fullname'), donations=my_donations, tickets=my_tickets, membership_tier=current_tier, vip_admin_reply=admin_reply, vip_user_proof=user_proof, plans=plans)

@app.route('/request_payment_details', methods=['POST'])
def request_payment_details():
    if 'member_id' not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    data = request.get_json()
    plan_name = data.get('plan_name', 'Unknown')
    member_id = session['member_id']
    
    conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
    c = conn.cursor()
    
    # Insert a ticket for the admin
    message = f"I am requesting payment details for the {plan_name} plan."
    c.execute("""
        INSERT INTO tickets (user_id, category, message, status) 
        VALUES (%s, %s, %s, %s)
    """, (member_id, 'Payment Detail Request', message, 'Open'))
    
    conn.commit()
    conn.close()
    
    return jsonify({"success": True})

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

@app.route('/dashboard/verify_payment', methods=['POST'])
def dashboard_verify_payment():
    if 'member_id' not in session:
        return redirect(url_for('member_login'))
        
    receipt_file = request.files.get('receipt_file')
    if not receipt_file or receipt_file.filename == '':
        flash("Please upload a valid receipt file.")
        return redirect(url_for('member_dashboard'))
        
    # Save the physical file
    filename = secure_filename(receipt_file.filename)
    if '.' not in filename:
        flash("SECURITY ALERT: File has no extension.")
        return redirect(url_for('member_dashboard'))
    
    ext = filename.rsplit('.', 1)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        flash("SECURITY ALERT: Invalid file type. Only PNG, JPG, JPEG, and PDF are allowed.")
        return redirect(url_for('member_dashboard'))
        
    unique_filename = f"{uuid.uuid4().hex}.{ext}"
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
    receipt_file.save(file_path)
    web_path = f"/static/uploads/{unique_filename}"
    
    conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
    c = conn.cursor()
    
    # 1. Create the Ticket for Admin Review in the Vault
    c.execute("""
        INSERT INTO tickets (user_id, category, message, status) 
        VALUES (%s, %s, %s, %s) RETURNING id
    """, (session['member_id'], 'VIP Payment Verification', 'Payment receipt submitted via dashboard.', 'Open'))
    ticket_id = c.fetchone()[0]
    
    # 2. Attach the receipt to the ticket
    c.execute("INSERT INTO attachments (ticket_id, file_path) VALUES (%s, %s)", (ticket_id, web_path))
    
    # 3. Update Member Tier and store proof reference for legacy compatibility
    c.execute("""
        UPDATE members 
        SET membership_tier = 'Payment Received', vip_user_proof = %s 
        WHERE id = %s
    """, (web_path, session['member_id']))
    
    conn.commit()
    conn.close()
    
    flash("Payment receipt submitted. An admin will review your 'VIP Payment Verification' ticket in the Vault shortly.")
    return redirect(url_for('member_dashboard'))
    
@app.route('/dashboard/submit_proof', methods=['POST'])

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
        conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
        c = conn.cursor()
        c.execute("SELECT password_hash FROM members WHERE username = 'AdminMaster'")
        admin_row = c.fetchone()
        conn.close()
        
        if admin_row and check_password_hash(admin_row[0], password):
            session['is_admin'] = True
            return redirect(url_for('admin_dashboard'))
        else:
            flash("Incorrect Admin Password.")
            return render_template('admin_login.html')
    return render_template('admin_login.html')

@app.route('/admin')
def admin_dashboard():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))

    conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
    c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    c.execute("""
        SELECT m.*, 
        (SELECT message FROM tickets t WHERE t.user_id = m.id ORDER BY created_at DESC LIMIT 1) as latest_ticket 
        FROM members m
    """)
    all_members = c.fetchall()
    
    c.execute("SELECT * FROM donations")
    all_donations = c.fetchall()
    
    c.execute("SELECT * FROM subscription_plans ORDER BY id")
    all_plans = c.fetchall()

    c.execute("SELECT * FROM email_templates ORDER BY id")
    all_templates = c.fetchall()
    conn.close()

    return render_template('admin.html', members=all_members, donations=all_donations, plans=all_plans, email_templates=all_templates)

@app.route('/admin/settings', methods=['POST'])
def admin_settings():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
    c = conn.cursor()
    
    # Update existing plans
    plan_ids = request.form.getlist('plan_id')
    plan_names = request.form.getlist('plan_name')
    plan_prices = request.form.getlist('plan_price')
    plan_features = request.form.getlist('plan_features')
    
    for i in range(len(plan_ids)):
        c.execute("UPDATE subscription_plans SET plan_name = %s, price = %s, features = %s WHERE id = %s",
                  (plan_names[i], float(plan_prices[i]), plan_features[i], int(plan_ids[i])))
    
    # Add new plan if provided
    new_name = request.form.get('new_plan_name')
    new_price = request.form.get('new_plan_price')
    new_features = request.form.get('new_plan_features')
    if new_name and new_price:
        c.execute("INSERT INTO subscription_plans (plan_name, price, features) VALUES (%s, %s, %s)",
                  (new_name, float(new_price), new_features if new_features else ""))
    
    conn.commit()
    conn.close()
    flash("Platform settings updated successfully.")
    return redirect(url_for('admin_dashboard'))

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
    
    # Dynamic Automated Email Engine (Step 6)
    c.execute("SELECT m.email, m.fullname, m.id FROM members m JOIN donations m_don ON m.id = m_don.member_id WHERE m_don.id = %s", (donation_id,))
    m_row = c.fetchone()
    if m_row:
        # Check for dynamic plan-based template
        c.execute("""
            SELECT subject, body FROM email_templates 
            WHERE trigger_event = 'Plan Purchased' 
            AND (plan_id IS NULL OR plan_id = (SELECT id FROM subscription_plans WHERE plan_name = 'Monthly' LIMIT 1))
            ORDER BY plan_id DESC LIMIT 1
        """)
        # Note: In a real system, we'd determine which plan was actually purchased. 
        # Here we use 'Monthly' as placeholder or fetch from donation context if available.
        t_row = c.fetchone()
        if t_row:
            subj = t_row[0].replace('{{name}}', m_row[1])
            body = t_row[1].replace('{{name}}', m_row[1])
            send_email_notification(m_row[0], subj, body, user_id=m_row[2])
        else:
            # Fallback to legacy
            subj, body = get_templated_email('Subscription_Success', m_row[1])
            if subj:
                send_email_notification(m_row[0], subj, body, user_id=m_row[2])
            
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
            
            # Send Admin Reply Email
            c.execute("SELECT email, fullname, id FROM members WHERE id = %s", (member_id,))
            m_row = c.fetchone()
            if m_row:
                subj, body = get_templated_email('Admin_Reply', m_row[1], admin_text=admin_reply_text)
                if subj:
                    send_email_notification(m_row[0], subj, body, user_id=m_row[2])
            
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
    c.execute("UPDATE members SET membership_tier = 'VIP', vip_since = CURRENT_TIMESTAMP WHERE id = %s", (member_id,))
    # Start a new VIP period
    c.execute("INSERT INTO vip_periods (user_id, start_time) VALUES (%s, CURRENT_TIMESTAMP)", (member_id,))
    
    # Send VIP Welcome Email
    c.execute("SELECT email, fullname, id FROM members WHERE id = %s", (member_id,))
    m_row = c.fetchone()
    if m_row:
        subj, body = get_templated_email('VIP_Welcome', m_row[1])
        if subj:
            send_email_notification(m_row[0], subj, body, user_id=m_row[2])
            
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
    # Close the active VIP period
    c.execute("UPDATE vip_periods SET end_time = CURRENT_TIMESTAMP WHERE user_id = %s AND end_time IS NULL", (member_id,))
    
    # Notify user of removal
    c.execute("SELECT email, fullname FROM members WHERE id = %s", (member_id,))
    user_row = c.fetchone()
    if user_row:
        subj, body = get_templated_email('VIP_Removal', user_row[1])
        if subj:
            send_email_notification(user_row[0], subj, body, user_id=member_id)
            
    conn.commit()
    conn.close()
    flash(f"User {user_row[1] if user_row else ''} demoted and notified.")
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/manual_vip/<int:member_id>', methods=['POST'])
def admin_manual_vip(member_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
        
    conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
    c = conn.cursor()
    c.execute("UPDATE members SET membership_tier = 'VIP', vip_since = CURRENT_TIMESTAMP WHERE id = %s", (member_id,))
    # Start a new VIP period
    c.execute("INSERT INTO vip_periods (user_id, start_time) VALUES (%s, CURRENT_TIMESTAMP)", (member_id,))
    
    # Send VIP Welcome Email
    c.execute("SELECT email, fullname, id FROM members WHERE id = %s", (member_id,))
    m_row = c.fetchone()
    if m_row:
        subj, body = get_templated_email('VIP_Welcome', m_row[1])
        if subj:
            send_email_notification(m_row[0], subj, body, user_id=m_row[2])
            
    conn.commit()
    conn.close()
    
    flash("Manual VIP Override successful.")
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/toggle_vip/<int:user_id>', methods=['POST'])
def admin_toggle_vip(user_id):
    if not session.get('is_admin'): return redirect(url_for('admin_login'))
    conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
    c = conn.cursor()
    c.execute("SELECT membership_tier, email, fullname FROM members WHERE id = %s", (user_id,))
    user = c.fetchone()
    if not user:
        conn.close()
        return "User not found", 404
    
    current_tier, email, fullname = user
    new_tier = 'VIP' if current_tier != 'VIP' else 'Regular'
    
    if new_tier == 'VIP':
        c.execute("UPDATE members SET membership_tier = 'VIP', vip_since = CURRENT_TIMESTAMP WHERE id = %s", (user_id,))
        c.execute("INSERT INTO vip_periods (user_id, start_time) VALUES (%s, CURRENT_TIMESTAMP)", (user_id,))
        # Trigger VIP Added email
        c.execute("SELECT subject, body FROM email_templates WHERE trigger_event = 'VIP Added'")
        t = c.fetchone()
        if t:
            send_email_notification(email, t[0].replace('{{name}}', fullname), t[1].replace('{{name}}', fullname), user_id=user_id)
        else:
            subj, body = get_templated_email('VIP_Welcome', fullname)
            if subj: send_email_notification(email, subj, body, user_id=user_id)
    else:
        c.execute("UPDATE members SET membership_tier = 'Regular' WHERE id = %s", (user_id,))
        c.execute("UPDATE vip_periods SET end_time = CURRENT_TIMESTAMP WHERE user_id = %s AND end_time IS NULL", (user_id,))
        # Trigger VIP Removed email
        c.execute("SELECT subject, body FROM email_templates WHERE trigger_event = 'VIP Removed'")
        t = c.fetchone()
        if t:
            send_email_notification(email, t[0].replace('{{name}}', fullname), t[1].replace('{{name}}', fullname), user_id=user_id)
        else:
            subj, body = get_templated_email('VIP_Removal', fullname)
            if subj: send_email_notification(email, subj, body, user_id=user_id)

    conn.commit()
    conn.close()
    flash(f"User {fullname} status updated to {new_tier}.")
    return redirect(request.referrer or url_for('admin_dashboard'))

@app.route('/admin/email_settings', methods=['POST'])
def admin_email_settings():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    event_types = request.form.getlist('event_type')
    subjects = request.form.getlist('subject')
    bodies = request.form.getlist('body')
    trigger_events = request.form.getlist('trigger_event')
    plan_ids = request.form.getlist('template_plan_id')
    
    conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
    c = conn.cursor()
    for i in range(len(event_types)):
        p_id = plan_ids[i] if plan_ids[i] != 'None' else None
        c.execute("""
            UPDATE email_templates 
            SET subject = %s, body = %s, trigger_event = %s, plan_id = %s 
            WHERE event_type = %s
        """, (subjects[i], bodies[i], trigger_events[i], p_id, event_types[i]))
    conn.commit()
    conn.close()
    flash("Email templates updated successfully.")
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/send_custom_email', methods=['GET', 'POST'])
def admin_send_custom_email():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
        
    if request.method == 'GET':
        return redirect(url_for('admin_dashboard'))
    
    member_id = request.form.get('member_id')
    custom_subject = request.form.get('custom_subject')
    custom_message = request.form.get('custom_body')
    
    conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
    c = conn.cursor()
    c.execute("SELECT email, fullname FROM members WHERE id = %s", (member_id,))
    res = c.fetchone()
    conn.close()
    
    if res:
        recipient_email = res[0]
        member_name = res[1]
        
        # Render through manual_email_template.html
        html_body = render_template('manual_email_template.html', name=member_name, custom_message=custom_message)
        
        if send_email_notification(recipient_email, custom_subject, html_body, user_id=member_id):
            flash(f"Manual dispatch successful to {recipient_email}.")
        else:
            flash("Dispatch failed. Check SMTP settings.")
    else:
        flash("Member not found.")
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/view_emails/<int:user_id>')
@app.route('/admin/user/<int:user_id>/emails')
def admin_view_user_emails(user_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
    c = conn.cursor()
    c.execute("SELECT fullname FROM members WHERE id = %s", (user_id,))
    member_name = c.fetchone()
    if not member_name:
        conn.close()
        flash("Member not found.")
        return redirect(url_for('admin_dashboard'))
        
    c.execute("SELECT sent_at, subject, body FROM email_logs WHERE user_id = %s ORDER BY sent_at DESC", (user_id,))
    logs = c.fetchall()
    conn.close()
    return render_template('admin_user_emails.html', logs=logs, member_name=member_name[0], user_id=user_id)

@app.route('/admin/delete_plan/<int:plan_id>', methods=['POST'])
def admin_delete_plan(plan_id):
    if not session.get('is_admin'): return redirect(url_for('admin_login'))
    conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
    c = conn.cursor()
    c.execute("DELETE FROM subscription_plans WHERE id = %s", (plan_id,))
    conn.commit()
    conn.close()
    flash("Plan deleted successfully.")
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/user/<int:user_id>')
def admin_user_profile(user_id):
    if not session.get('is_admin'): return redirect(url_for('admin_login'))
    conn = psycopg2.connect(os.environ.get('DATABASE_URL'), cursor_factory=psycopg2.extras.DictCursor)
    c = conn.cursor()
    c.execute("SELECT * FROM members WHERE id = %s", (user_id,))
    member = c.fetchone()
    conn.close()
    if not member: return "User not found", 404
    return render_template('admin_user_profile.html', member=member)

@app.route('/admin/user/<int:user_id>/disable', methods=['POST'])
def admin_disable_user(user_id):
    if not session.get('is_admin'): return redirect(url_for('admin_login'))
    admin_password = request.form.get('admin_password')
    conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
    c = conn.cursor()
    c.execute("SELECT password_hash FROM members WHERE username = 'AdminMaster'")
    admin_row = c.fetchone()
    conn.close()
    
    if admin_row and check_password_hash(admin_row[0], admin_password):
        conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
        c = conn.cursor()
        c.execute("UPDATE members SET is_active = FALSE WHERE id = %s", (user_id,))
        conn.commit()
        conn.close()
        flash("User account disabled.")
    else:
        flash("Invalid admin password.")
    return redirect(url_for('admin_user_profile', user_id=user_id))

@app.route('/admin/user/<int:user_id>/delete', methods=['POST'])
def admin_delete_user(user_id):
    if not session.get('is_admin'): return redirect(url_for('admin_login'))
    admin_password = request.form.get('admin_password')
    conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
    c = conn.cursor()
    c.execute("SELECT password_hash FROM members WHERE username = 'AdminMaster'")
    admin_row = c.fetchone()
    conn.close()
    
    if admin_row and check_password_hash(admin_row[0], admin_password):
        conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
        c = conn.cursor()
        try:
            c.execute("DELETE FROM members WHERE id = %s", (user_id,))
            conn.commit()
            flash("User account deleted permanently.")
            conn.close()
            return redirect(url_for('admin_dashboard'))
        except Exception as e:
            flash(f"Delete failed: {e}")
            conn.close()
    else:
        flash("Invalid admin password.")
    return redirect(url_for('admin_user_profile', user_id=user_id))

@app.route('/admin/unfreeze/<int:user_id>', methods=['POST'])
def admin_unfreeze_user(user_id):
    if not session.get('is_admin'): return redirect(url_for('admin_login'))
    conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
    c = conn.cursor()
    c.execute("UPDATE members SET is_locked = FALSE, is_active = TRUE, failed_attempts = 0 WHERE id = %s", (user_id,))
    conn.commit()
    conn.close()
    flash("Account successfully unfrozen and reactivated.")
    return redirect(url_for('admin_user_profile', user_id=user_id))

@app.route('/admin/settings', methods=['GET', 'POST'])
def admin_global_settings():
    if not session.get('is_admin'): return redirect(url_for('admin_login'))
    
    conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
    c = conn.cursor()
    
    if request.method == 'POST':
        form_type = request.form.get('form_type')
        
        if form_type == 'update_email':
            support_email = request.form.get('support_email')
            if support_email:
                c.execute("UPDATE system_settings SET support_email = %s WHERE id = 1", (support_email,))
                conn.commit()
                flash("Support email updated successfully.")
        
        elif form_type == 'update_password':
            curr_pass = request.form.get('current_password')
            new_pass = request.form.get('new_password')
            conf_pass = request.form.get('confirm_password')
            
            c.execute("SELECT password_hash FROM members WHERE username = 'AdminMaster'")
            admin_row = c.fetchone()
            
            if admin_row and check_password_hash(admin_row[0], curr_pass):
                if new_pass == conf_pass:
                    hashed = generate_password_hash(new_pass)
                    c.execute("UPDATE members SET password_hash = %s WHERE username = 'AdminMaster'", (hashed,))
                    conn.commit()
                    flash("Admin password updated successfully.")
                else:
                    flash("New passwords do not match.")
            else:
                flash("Invalid current admin password.")
        
        return redirect(url_for('admin_global_settings'))
    
    c.execute("SELECT support_email FROM system_settings WHERE id = 1")
    settings = c.fetchone()
    conn.close()
    return render_template('admin_settings.html', settings=settings)

@app.route('/logout')
def member_logout():
    session.pop('member_id', None)
    session.pop('member_fullname', None)
    return redirect(url_for('member_login'))

@app.route('/history', methods=['GET', 'POST'])
def member_history():
    if 'member_id' not in session:
        return redirect(url_for('member_login'))
        
    if request.method == 'POST':
        category = request.form.get('category')
        message = request.form.get('message')
        attachments = request.files.getlist('attachment')
        
        conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
        c = conn.cursor()
        
        c.execute("INSERT INTO tickets (user_id, category, message) VALUES (%s, %s, %s) RETURNING id",
                  (session['member_id'], category, message))
        ticket_id = c.fetchone()[0]
        
        for file in attachments:
            if file and file.filename != '':
                filename = secure_filename(file.filename)
                filename = f"history_reply_t{ticket_id}_m{session['member_id']}_{filename}"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                file_path = f"/static/uploads/{filename}"
                c.execute("INSERT INTO attachments (ticket_id, file_path) VALUES (%s, %s)",
                          (ticket_id, file_path))
        
        conn.commit()
        conn.close()
        flash('Reply sent successfully')
        return redirect(url_for('member_history'))

    conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
    c = conn.cursor()
    c.execute("""
        SELECT t.id, t.category, t.message, t.status, t.admin_reply, a.file_path, a.uploaded_by_admin, t.created_at
        FROM tickets t 
        LEFT JOIN attachments a ON t.id = a.ticket_id 
        WHERE t.user_id = %s 
        ORDER BY t.created_at DESC
    """, (session['member_id'],))
    rows = c.fetchall()
    conn.close()
    
    history = {}
    for row in rows:
        t_id = row[0]
        if t_id not in history:
            history[t_id] = {
                'id': row[0],
                'category': row[1],
                'message': row[2],
                'status': row[3],
                'admin_reply': row[4],
                'admin_attachments': [],
                'user_attachments': [],
                'created_at': row[7]
            }
        if row[5]:
            if row[6]: history[t_id]['admin_attachments'].append(row[5])
            else: history[t_id]['user_attachments'].append(row[5])
            
    return render_template('member_history.html', history=list(history.values()))

@app.route('/admin/vault/<int:member_id>', methods=['GET', 'POST'])
def admin_user_vault(member_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
        
    if request.method == 'POST':
        admin_new_message = request.form.get('admin_new_message')
        
        if admin_new_message:
            category = request.form.get('category_new')
            content = request.form.get('message_new')
            
            conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
            c = conn.cursor()
            # Create a brand new ticket (message) initiated by the admin
            c.execute("INSERT INTO tickets (user_id, category, message, status, admin_reply) VALUES (%s, %s, %s, 'Replied', %s) RETURNING id",
                      (member_id, category, '[PROACTIVE ADMIN MESSAGE]', content))
            conn.commit()
            conn.close()
            flash('Proactive message dispatched.')
            return redirect(url_for('admin_user_vault', member_id=member_id))
            
        else: # Standard Quick Reply to existing ticket
            admin_reply_text = request.form.get('admin_reply_text')
            media_files = request.files.getlist('admin_media')
            
            conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
            c = conn.cursor()
            
            # Find the most recent open ticket for this member
            c.execute("SELECT id FROM tickets WHERE user_id = %s AND status = 'Open' ORDER BY created_at DESC LIMIT 1", (member_id,))
            ticket_row = c.fetchone()
            
            if not ticket_row:
                c.execute("SELECT id FROM tickets WHERE user_id = %s ORDER BY created_at DESC LIMIT 1", (member_id,))
                ticket_row = c.fetchone()
                
            if ticket_row:
                ticket_id = ticket_row[0]
                c.execute("UPDATE tickets SET admin_reply = %s, status = 'Replied' WHERE id = %s", (admin_reply_text, ticket_id))
                
                for file in media_files:
                    if file and file.filename != '':
                        filename = secure_filename(file.filename)
                        filename = f"vault_reply_t{ticket_id}_m{member_id}_{filename}"
                        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                        media_path = f"/static/uploads/{filename}"
                        c.execute("INSERT INTO attachments (ticket_id, file_path, uploaded_by_admin) VALUES (%s, %s, TRUE)",
                                  (ticket_id, media_path))
                                  
            conn.commit()
            conn.close()
            flash('Reply successfully dispatched to member history.')
            return redirect(url_for('admin_user_vault', member_id=member_id))

    conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
    c = conn.cursor()
    c.execute("""
        SELECT t.id, t.category, t.message, t.status, t.admin_reply, a.file_path, a.uploaded_by_admin, t.created_at
        FROM tickets t 
        LEFT JOIN attachments a ON t.id = a.ticket_id 
        WHERE t.user_id = %s 
        ORDER BY t.created_at DESC
    """, (member_id,))
    rows = c.fetchall()
    
    vault_history = {}
    for row in rows:
        t_id = row[0]
        if t_id not in vault_history:
            vault_history[t_id] = {
                'id': row[0],
                'category': row[1],
                'message': row[2],
                'status': row[3],
                'admin_reply': row[4],
                'admin_attachments': [],
                'user_attachments': [],
                'created_at': row[7]
            }
        if row[5]:
            if row[6]: vault_history[t_id]['admin_attachments'].append(row[5])
            else: vault_history[t_id]['user_attachments'].append(row[5])
            
    c.execute("SELECT fullname FROM members WHERE id = %s", (member_id,))
    member_row = c.fetchone()
    member_name = member_row[0] if member_row else "Unknown Member"
    conn.close()
    
    return render_template('user_vault.html', vault_history=list(vault_history.values()), member_id=member_id, member_name=member_name)

@app.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    return redirect(url_for('admin_login'))

@app.route('/vip_lounge', methods=['GET', 'POST'])
def vip_lounge():
    is_admin = session.get('is_admin')
    member_id = session.get('member_id')
    
    if not is_admin and not member_id:
        return redirect(url_for('member_login'))
    
    conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
    c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    member_data = None
    if member_id:
        c.execute("SELECT fullname, membership_tier, vip_since FROM members WHERE id = %s", (member_id,))
        member_data = c.fetchone()
        if not member_data or member_data['membership_tier'] != 'VIP':
            conn.close()
            return "Unauthorized. VIP Lounge access required."
            
        if member_data['vip_since'] is None:
            c.execute("UPDATE members SET vip_since = CURRENT_TIMESTAMP WHERE id = %s", (member_id,))
            conn.commit()
            c.execute("SELECT fullname, membership_tier, vip_since FROM members WHERE id = %s", (member_id,))
            member_data = c.fetchone()

    c.execute("SELECT id FROM chatrooms WHERE room_name = 'VIP Lounge' LIMIT 1")
    room_id = c.fetchone()[0]

    if request.method == 'POST':
        selected_sender_id = member_id
        if not member_id: # Admin is posting
            c.execute("SELECT id FROM members WHERE username = 'AdminMaster' LIMIT 1")
            admin_row = c.fetchone()
            selected_sender_id = admin_row[0] if admin_row else None
            
        if not selected_sender_id:
            flash("System Error: Admin Post Identity not initialized.")
        else:
            msg_text = request.form.get('message_text')
            files = request.files.getlist('attachments')
            
            if len(files) > 5:
                flash("Maximum 5 files allowed.")
            else:
                total_size = sum([len(f.read()) for f in files if f.filename != ''])
                for f in files: f.seek(0)
                
                if total_size > 200 * 1024 * 1024:
                    flash("Collective file size exceeds 200MB.")
                else:
                    c.execute("INSERT INTO chatroom_messages (room_id, sender_id, message_text) VALUES (%s, %s, %s) RETURNING id",
                              (room_id, selected_sender_id, msg_text))
                    msg_id = c.fetchone()[0]
                    
                    for f in files:
                        if f and f.filename != '':
                            filename = f"{int(time.time())}_{secure_filename(f.filename)}"
                            f.save(os.path.join(app.config['CHATROOM_UPLOAD_FOLDER'], filename))
                            c.execute("INSERT INTO chatroom_attachments (message_id, file_path, file_size) VALUES (%s, %s, %s)",
                                      (msg_id, f"/static/chatroom_uploads/{filename}", total_size)) 
                    conn.commit()
                    flash("Message dispatched successfully.")
                    conn.close()
                    return redirect(url_for('vip_lounge'))

    if is_admin:
        c.execute("""
            SELECT m.fullname, cm.message_text, cm.created_at, cm.id
            FROM chatroom_messages cm
            JOIN members m ON cm.sender_id = m.id
            WHERE cm.room_id = %s
            ORDER BY cm.created_at ASC
        """, (room_id,))
    else:
        c.execute("""
            SELECT m.fullname, cm.message_text, cm.created_at, cm.id
            FROM chatroom_messages cm
            JOIN members m ON cm.sender_id = m.id
            WHERE cm.room_id = %s 
            AND EXISTS (
                SELECT 1 FROM vip_periods vp 
                WHERE vp.user_id = %s 
                AND cm.created_at >= vp.start_time 
                AND (vp.end_time IS NULL OR cm.created_at <= vp.end_time)
            )
            ORDER BY cm.created_at ASC
        """, (room_id, member_id))
    
    # Retrieve messages with unique IDs and associated media attachments
    msgs = c.fetchall()
    display_messages = []
    for m in msgs:
        c.execute("SELECT file_path FROM chatroom_attachments WHERE message_id = %s", (m['id'],))
        atts = [r[0] for r in c.fetchall()]
        display_messages.append({
            'id': m['id'],
            'sender': m['fullname'], 
            'text': m['message_text'], 
            'time': m['created_at'], 
            'attachments': atts
        })
        
    conn.close()
    return render_template('chatroom.html', messages=display_messages, is_admin=is_admin)

@app.route('/admin_delete_chat_message/<int:msg_id>', methods=['POST'])
def admin_delete_chat_message(msg_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
        
    conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
    c = conn.cursor()
    # 1. Delete associated attachments
    c.execute("DELETE FROM chatroom_attachments WHERE message_id = %s", (msg_id,))
    # 2. Delete the message itself
    c.execute("DELETE FROM chatroom_messages WHERE id = %s", (msg_id,))
    conn.commit()
    conn.close()
    
    flash("Message and associated media purged successfully.")
    return redirect(url_for('vip_lounge'))

@app.route('/trigger_admin_alert')
def trigger_admin_alert():
    if 'member_id' not in session and not session.get('is_admin'):
        return redirect(url_for('member_login'))
    
    fullname = session.get('member_fullname', 'A VIP User')
    subj = "VIP LOUNGE: ASSISTANCE REQUESTED"
    body = f"Admin Alert Triggered!\n\nUser: {fullname}\nLocation: VIP Lounge Chat\n\nUsers are requesting immediate assistance in the VIP lounge chat."
    
    # Trigger existing email utility (Step 5)
    send_email_notification('admin@system.local', subj, body)
    
    flash("Admin was pinged! Assistance is on the way.")
    return redirect(url_for('vip_lounge'))

if __name__ == '__main__':
    # Auto-migration on startup
    try:
        from initialize_remote import run_migrations
        run_migrations()
    except Exception as e:
        print(f"Startup Migration Error: {e}")
        
    init_db()
    app.run(debug=True)