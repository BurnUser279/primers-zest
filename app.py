import os
import traceback
import time
import uuid
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
load_dotenv()

# Auto-clean CLOUDINARY_URL environment variable if malformed or copied with prefix/quotes
_c_url = os.environ.get('CLOUDINARY_URL')
if _c_url:
    _c_url = _c_url.strip().strip('"').strip("'")
    if _c_url.startswith('CLOUDINARY_URL='):
        _c_url = _c_url[len('CLOUDINARY_URL='):].strip().strip('"').strip("'")
    os.environ['CLOUDINARY_URL'] = _c_url

from flask import Flask, render_template, request, session, redirect, url_for, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import smtplib
import sqlite3
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import datetime
import cloudinary
import cloudinary.uploader

# Cloudinary Setup
HAS_CLOUDINARY = False
if os.environ.get('CLOUDINARY_URL') or (os.environ.get('CLOUDINARY_CLOUD_NAME') and os.environ.get('CLOUDINARY_API_KEY') and os.environ.get('CLOUDINARY_API_SECRET')):
    if not os.environ.get('CLOUDINARY_URL'):
        cloudinary.config(
            cloud_name=os.environ.get('CLOUDINARY_CLOUD_NAME'),
            api_key=os.environ.get('CLOUDINARY_API_KEY'),
            api_secret=os.environ.get('CLOUDINARY_API_SECRET'),
            secure=True
        )
    HAS_CLOUDINARY = True
    print("Cloudinary Media Storage Active.")
else:
    print("Local Filesystem Media Storage Active.")

def save_uploaded_file(file, folder=None, custom_filename=None):
    """
    Saves an uploaded file. If Cloudinary is configured, uploads directly to Cloudinary.
    Otherwise, saves to the local filesystem (fallback) with a fully randomized name to
    prevent leakage of original filenames/details.
    """
    if not file or file.filename == '':
        return None
        
    # Extract file extension from the original filename safely
    _, ext = os.path.splitext(file.filename)
    ext = ext.lower()
    if not ext and custom_filename:
        _, ext = os.path.splitext(custom_filename)
        ext = ext.lower()
    if not ext:
        ext = '.bin' # Safe fallback

    # Generate a unique randomized ID (wipes original file traces)
    unique_id = f"{int(time.time())}_{uuid.uuid4().hex[:12]}"
    
    # Check if a custom prefix/pattern is requested (like star_ or slide_ or chat_)
    prefix = ""
    if custom_filename:
        parts = custom_filename.split('_')
        if custom_filename.startswith('star_'):
            if 'update' in parts:
                prefix = "_".join(parts[:5]) + "_"
            else:
                prefix = "_".join(parts[:4]) + "_"
        elif custom_filename.startswith('slide_'):
            prefix = "_".join(parts[:2]) + "_"
        elif custom_filename.startswith('chat_'):
            prefix = "_".join(parts[:3]) + "_"
                
    filename = f"{prefix}{unique_id}{ext}"
        
    if HAS_CLOUDINARY:
        try:
            # Generate a secure public ID using the randomized filename (without extension)
            public_id, _ = os.path.splitext(filename)
            upload_result = cloudinary.uploader.upload(
                file, 
                public_id=public_id,
                resource_type="auto", 
                folder="primers_zest",
                use_filename=False,
                unique_filename=False
            )
            return upload_result.get('secure_url')
        except Exception as e:
            print(f"Cloudinary Upload Failed, falling back to local: {e}")
            
    # Fallback/Local storage
    target_folder = folder or 'static/uploads'
    os.makedirs(target_folder, exist_ok=True)
    file_path = os.path.join(target_folder, filename)
    file.seek(0) # Ensure we are at the start of the stream
    file.save(file_path)
    return file_path.replace('\\', '/')


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

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587, timeout=10)
        server.starttls()
        server.login(sender_email, sender_password)
        server.send_message(msg)
        server.quit()
    except Exception as smtp_err:
        print(f"SMTP Delivery Error: {smtp_err}")
        return False

    # Audit Logging
    if user_id:
        try:
            conn, db_type = get_db_connection()
            c = get_cursor(conn, db_type)
            c.execute("INSERT INTO email_logs (user_id, subject, body) VALUES (%s, %s, %s)", (user_id, subject, body))
            conn.commit()
        except Exception as log_err:
            print(f"Log Error: {log_err}")

    return True

def get_templated_email(event_type, name, admin_text=None):
    conn = None
    try:
        conn, db_type = get_db_connection()
        c = get_cursor(conn, db_type)
        c.execute("SELECT subject, body FROM email_templates WHERE event_type = %s", (event_type,))
        row = c.fetchone()
        if row:
            subject = row[0].replace('{{name}}', name)
            body = row[1].replace('{{name}}', name)
            if admin_text:
                body = body.replace('{{admin_text}}', admin_text)
            return subject, body
    except Exception as e:
        print(f"Email Template Error ({event_type}): {e}")
    return None, None

# Security & Throttling
login_attempts = {} # {ip: {'count': N, 'last_attempt': timestamp}}
def get_ip():
    return request.headers.get('X-Forwarded-For', request.remote_addr)

def check_throttle():
    ip = get_ip()
    now = time.time()
    if ip in login_attempts:
        attempt = login_attempts[ip]
        # Calculate delay: 2^count seconds (caps at 30s)
        delay = min(2 ** (attempt['count'] - 1), 30) if attempt['count'] > 2 else 0
        if now - attempt['last_attempt'] < delay:
            return False, int(delay - (now - attempt['last_attempt']))
    return True, 0

def record_login_attempt(ip, success):
    now = time.time()
    if success:
        if ip in login_attempts: del login_attempts[ip]
    else:
        if ip not in login_attempts:
            login_attempts[ip] = {'count': 1, 'last_attempt': now}
        else:
            login_attempts[ip]['count'] += 1
            login_attempts[ip]['last_attempt'] = now

app = Flask(__name__)
# --- Secret Key (Phase 2: never use a guessable fallback) ---
_secret = os.environ.get('SECRET_KEY')
if not _secret:
    if os.environ.get('DATABASE_URL'):  # Production environment detected
        raise RuntimeError(
            "CRITICAL: SECRET_KEY environment variable is not set. "
            "Set it in your Render dashboard to a long random string."
        )
    import warnings
    warnings.warn("SECRET_KEY not set — using dev fallback. NOT safe for production.", stacklevel=1)
    _secret = 'dev_only_fallback_do_not_use_in_production'
app.secret_key = _secret
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB hard limit — prevents OOM on large uploads
app.config['CHATROOM_UPLOAD_FOLDER'] = 'static/chatroom_uploads'

# --- Membership Cards Init ---
def init_membership_cards():
    try:
        conn, db_type = get_db_connection()
        c = get_cursor(conn, db_type)
        c.execute("""
            CREATE TABLE IF NOT EXISTS membership_cards (
                id SERIAL PRIMARY KEY,
                tier_name VARCHAR(50) NOT NULL,
                price DECIMAL(10, 2) NOT NULL,
                features TEXT NOT NULL,
                image_path TEXT NOT NULL
            )
        """)
        c.execute("SELECT COUNT(*) FROM membership_cards")
        cards = [
            ('Bronze Card', 100.00, 'Standard access to community features, Basic support, Regular member status', '/static/uploads/bronze_membership_card.png'),
            ('Silver Card', 250.00, 'Exclusive access to Silver events, Priority support, 10% discount on Star bookings', '/static/uploads/silver_membership_card.png'),
            ('Gold Executive Card', 750.00, 'All Silver benefits, VIP lounge access, 25% discount on Star bookings, Personal concierge', '/static/uploads/gold_membership_card.png'),
            ('Platinum Card', 2500.00, 'Ultimate executive access, Private jet concierge, 50% discount on Star bookings, Lifetime VIP status', '/static/uploads/platinum_membership_card.png')
        ]
        for tier, price, features, img in cards:
            c.execute("SELECT id, image_path FROM membership_cards WHERE tier_name = %s", (tier,))
            row = c.fetchone()
            if not row:
                c.execute("INSERT INTO membership_cards (tier_name, price, features, image_path) VALUES (%s, %s, %s, %s)",
                          (tier, price, features, img))
            elif row[1] != img:
                # Update image path if it's different from the standard default
                c.execute("UPDATE membership_cards SET image_path = %s WHERE id = %s", (img, row[0]))
        # Phase 3: Re-apply correct image paths on every startup — no destructive dummy UPDATE.
        # Previously: c.execute("UPDATE membership_cards SET image_path = tier_name") wiped all paths.
        ts = "v35_3"  # Increment when card designs change
        c.execute("UPDATE membership_cards SET image_path = %s WHERE tier_name = 'Bronze Card'", ('/static/uploads/bronze_membership_card.png?v=' + ts,))
        c.execute("UPDATE membership_cards SET image_path = %s WHERE tier_name = 'Silver Card'", ('/static/uploads/silver_membership_card.png?v=' + ts,))
        c.execute("UPDATE membership_cards SET image_path = %s WHERE tier_name = 'Gold Executive Card'", ('/static/uploads/gold_membership_card.png?v=' + ts,))
        c.execute("UPDATE membership_cards SET image_path = %s WHERE tier_name = 'Platinum Card'", ('/static/uploads/platinum_membership_card.png?v=' + ts,))
        
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Cards Init Error: {e}")


os.makedirs('static/uploads', exist_ok=True)
os.makedirs('static/chatroom_uploads', exist_ok=True)

# --- DB COMPATIBILITY LAYER ---
# --- Database Connectivity & Global Pool ---
_db_pool = None

def init_pool():
    global _db_pool
    db_url = os.environ.get('DATABASE_URL')
    if _db_pool is None and db_url and str(db_url).startswith('postgres'):
        try:
            from psycopg2 import pool
            # Use ThreadedConnectionPool to ensure thread-safety across Gunicorn threads
            _db_pool = pool.ThreadedConnectionPool(2, 10, db_url, cursor_factory=psycopg2.extras.DictCursor)
            print("Database Threaded Connection Pool Initialized.")
        except Exception as e:
            print(f"CRITICAL: Pool Initialization Failed: {e}")

class SQLiteConnectionWrapper:
    def __init__(self, conn):
        super().__setattr__('_conn', conn)
    def cursor(self, *args, **kwargs):
        return self._conn.cursor(*args, **kwargs)
    def commit(self):
        return self._conn.commit()
    def rollback(self):
        return self._conn.rollback()
    def close(self):
        # In a request-bound environment (flask.g), we don't close manually.
        # The teardown_db function handles the actual release.
        pass
    def __enter__(self): return self
    def __exit__(self, exc_type, exc_val, exc_tb): pass
    
    def __getattr__(self, name):
        return getattr(self._conn, name)
    def __setattr__(self, name, value):
        if name == '_conn':
            super().__setattr__(name, value)
        else:
            setattr(self._conn, name, value)

class PostgresConnectionWrapper:
    def __init__(self, conn, pool=None):
        self._conn = conn
        self._pool = pool
    def cursor(self, *args, **kwargs):
        return self._conn.cursor(*args, **kwargs)
    def commit(self):
        return self._conn.commit()
    def rollback(self):
        return self._conn.rollback()
    def close(self):
        # In a request-bound environment (flask.g), we don't close manually.
        # The teardown_db function handles the actual release.
        pass
    def __enter__(self): return self
    def __exit__(self, exc_type, exc_val, exc_tb): pass # Don't close on exit either

def get_db_connection():
    from flask import g
    
    # Return existing connection if already opened in this request context
    if 'db_conn' in g:
        return g.db_conn, g.db_type

    db_url = os.environ.get('DATABASE_URL')
    
    # SQLite Path
    if not db_url or not str(db_url).startswith('postgres'):
        conn = sqlite3.connect('dev_database.db', check_same_thread=False)
        conn.row_factory = sqlite3.Row
        wrapped_conn = SQLiteConnectionWrapper(conn)
        g.db_conn = wrapped_conn
        g.db_type = 'sqlite'
        return wrapped_conn, 'sqlite'
    
    # Postgres with Pool Support
    if _db_pool is None:
        init_pool()

    max_retries = 5
    retry_delay = 1
    conn = None
    
    for attempt in range(max_retries):
        try:
            if _db_pool:
                conn = _db_pool.getconn()
                # Proactively verify the connection is alive
                if getattr(conn, 'closed', 0) != 0:
                    _db_pool.putconn(conn, close=True)
                    continue
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1")
                except Exception:
                    _db_pool.putconn(conn, close=True)
                    continue
            else:
                conn = psycopg2.connect(db_url, cursor_factory=psycopg2.extras.DictCursor)
            
            break # Successfully got a valid connection
        except Exception as e:
            if "max clients reached" in str(e).lower() and attempt < max_retries - 1:
                time.sleep(retry_delay)
                retry_delay *= 1.5
                continue
            if attempt == max_retries - 1:
                raise e

    if conn:
        # Wrap the connection to handle monkey-patched close (backward compatibility)
        wrapped_conn = PostgresConnectionWrapper(conn, _db_pool)
        g.db_conn = wrapped_conn
        g.db_type = 'postgres'
        return wrapped_conn, 'postgres'
    
    raise Exception("Failed to acquire database connection.")

@app.teardown_appcontext
def teardown_db(exception):
    from flask import g
    conn = g.pop('db_conn', None)
    db_type = g.pop('db_type', None)
    
    if conn is not None:
        if db_type == 'postgres' and _db_pool:
            try:
                # If it's a wrapper, we use its underlying connection
                raw_conn = conn._conn if hasattr(conn, '_conn') else conn
                
                # If the request had an exception or the connection is broken, close it
                close_it = False
                if exception is not None or getattr(raw_conn, 'closed', 0) != 0:
                    close_it = True
                
                if not close_it:
                    try:
                        raw_conn.rollback() # Reset transaction state
                    except:
                        close_it = True
                
                _db_pool.putconn(raw_conn, close=close_it)
            except:
                try:
                    raw_conn = conn._conn if hasattr(conn, '_conn') else conn
                    raw_conn.close()
                except: pass
        else:
            try:
                raw_conn = conn._conn if hasattr(conn, '_conn') else conn
                raw_conn.close()
            except: pass

class SQLiteCursorWrapper:
    """Wraps sqlite3 cursor to mimic psycopg2 behavior (like %s placeholders and RETURNING id)."""
    def __init__(self, cursor):
        self.cursor = cursor
        self._returning_id = False

    def execute(self, query, params=None):
        if "RETURNING id" in query.upper():
            self._returning_id = True
            import re
            query = re.sub(r'(?i)RETURNING\s+id', '', query)
        else:
            self._returning_id = False

        if params:
            query = query.replace('%s', '?')
            return self.cursor.execute(query, params)
        return self.cursor.execute(query)

    def fetchone(self):
        if self._returning_id:
            self._returning_id = False
            return (self.cursor.lastrowid,)
        return self.cursor.fetchone()

    def fetchall(self): return self.cursor.fetchall()
    def __iter__(self): return iter(self.cursor)
    @property
    def lastrowid(self): return self.cursor.lastrowid
    def close(self): self.cursor.close()

def get_cursor(conn, db_type):
    if db_type == 'sqlite':
        return SQLiteCursorWrapper(conn.cursor())
    return conn.cursor(cursor_factory=psycopg2.extras.DictCursor) if hasattr(psycopg2.extras, 'DictCursor') else conn.cursor()

PL = '%s' # This will be replaced in execute if needed

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True) 
os.makedirs(app.config['CHATROOM_UPLOAD_FOLDER'], exist_ok=True)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'pdf'}

# Admin Audit Helper
def log_admin_action(action, target_type=None, target_id=None, details=None):
    try:
        conn, db_type = get_db_connection()
        c = get_cursor(conn, db_type)
        # Using session.get('member_id') as admin_id since Admin is a member record
        admin_id = session.get('member_id', 0)
        c.execute("""
            INSERT INTO admin_audit_logs (admin_id, action, target_type, target_id, details)
            VALUES (%s, %s, %s, %s, %s)
        """, (admin_id, action, target_type, target_id, details))
        conn.commit()
    except Exception as e:
        print(f"Audit Log Error: {e}")

@app.after_request
def add_security_headers(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    # Strict-Transport-Security is only effective over HTTPS
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

@app.template_filter('media_url')
def media_url_filter(path):
    if not path:
        return '/static/img/default_star.jpg'
    if path.startswith(('http://', 'https://')):
        return path
    if path.startswith('/'):
        return path
    return '/' + path

def datetimeformat(value, format='%Y-%m-%d %H:%M'):
    if not value: return ""
    if isinstance(value, str):
        try:
            # Handle standard ISO format and space-separated format
            val = value.replace(' ', 'T')
            dt = datetime.datetime.fromisoformat(val.split('.')[0])
            return dt.strftime(format)
        except Exception:
            return value
    try:
        return value.strftime(format)
    except Exception:
        return str(value)

app.jinja_env.filters['datetimeformat'] = datetimeformat

def get_site_setting(key, default=""):
    try:
        conn, db_type = get_db_connection()
        c = get_cursor(conn, db_type)
        c.execute("SELECT setting_value FROM site_settings WHERE setting_key = %s", (key,))
        row = c.fetchone()
        if row:
            # Handle both Row objects and tuples
            return row[0] if isinstance(row, (tuple, list)) else row['setting_value']
    except Exception as e:
        print(f"Setting Error ({key}): {e}")
    return default

@app.context_processor
def inject_unread_count():
    ctx = {'datetime_now': datetime.datetime.utcnow()}
    if 'member_id' in session:
        try:
            conn, db_type = get_db_connection()
            c = get_cursor(conn, db_type)
            c.execute("SELECT membership_tier FROM members WHERE id = %s", (session['member_id'],))
            row = c.fetchone()
            ctx['membership_tier'] = row[0] if row else 'Regular'
            
            c.execute("SELECT COUNT(*) FROM member_notifications WHERE member_id = %s AND is_read = FALSE", (session['member_id'],))
            ctx['unread_notifications_count'] = c.fetchone()[0]
            
            if session.get('is_admin'):
                c.execute("SELECT * FROM admin_notifications WHERE is_read = FALSE")
                ctx['notifications'] = c.fetchall()
                c.execute("SELECT * FROM star_bookings WHERE status = 'Pending'")
                ctx['star_bookings'] = c.fetchall()
                c.execute("SELECT * FROM vip_submissions WHERE status = 'Pending'")
                ctx['vip_submissions'] = c.fetchall()
                
        except:
            ctx['unread_notifications_count'] = 0
            ctx['membership_tier'] = 'Regular'
        return ctx
    
    if session.get('is_admin'):
        try:
            conn, db_type = get_db_connection()
            c = get_cursor(conn, db_type)
            c.execute("SELECT * FROM admin_notifications WHERE is_read = FALSE")
            ctx['notifications'] = c.fetchall()
            c.execute("SELECT * FROM star_bookings WHERE status = 'Pending'")
            ctx['star_bookings'] = c.fetchall()
            c.execute("SELECT * FROM vip_submissions WHERE status = 'Pending'")
            ctx['vip_submissions'] = c.fetchall()
        except Exception as e:
            print(f"Global Context Admin Data Fetch Error: {e}")

    ctx['unread_notifications_count'] = 0
    ctx['membership_tier'] = None
    return ctx

@app.before_request
def check_account_status():
    # Only monitor logged-in members
    if session.get('member_id'):
        # Administrators are exempt from member status checks
        if session.get('is_admin'):
            return
            
        # Allow essential and appeal routes
        exempt = ['member_logout', 'member_login', 'static', 'index', 'member_appeal']
        if request.endpoint in exempt:
            return

        try:
            conn, db_type = get_db_connection()
            c = get_cursor(conn, db_type)
            c.execute("SELECT is_active FROM members WHERE id = %s", (session['member_id'],))
            row = c.fetchone()
            if row and int(row[0]) == 0:
                return redirect(url_for('member_appeal'))
        except Exception as e:
            print(f"Account Status Check Error: {e}")

def add_admin_notification(member_id, action_type, message, target_url=None):
    try:
        conn, db_type = get_db_connection()
        c = get_cursor(conn, db_type)
        c.execute("INSERT INTO admin_notifications (member_id, action_type, message, target_url) VALUES (%s, %s, %s, %s)",
                  (member_id, action_type, message, target_url))
        conn.commit()
    except Exception as e:
        print(f"Notification Error: {e}")

def add_member_notification(member_id, action_type, message, target_url=None):
    try:
        conn, db_type = get_db_connection()
        c = get_cursor(conn, db_type)
        c.execute("INSERT INTO member_notifications (member_id, action_type, message, target_url) VALUES (%s, %s, %s, %s)",
                  (member_id, action_type, message, target_url))
        conn.commit()
    except Exception as e:
        print(f"Member Notification Error: {e}")

def init_db():
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    # helper for SERIAL vs AUTOINCREMENT
    pk_type = "SERIAL PRIMARY KEY" if db_type == 'postgres' else "INTEGER PRIMARY KEY AUTOINCREMENT"

    # --- PARENT TABLES ---
    c.execute(f'''CREATE TABLE IF NOT EXISTS members
                 (id {pk_type},
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
                  is_locked BOOLEAN DEFAULT FALSE,
                  country VARCHAR(100),
                  state VARCHAR(100),
                  can_write_news BOOLEAN DEFAULT FALSE,
                  can_write_insights BOOLEAN DEFAULT FALSE,
                  bio TEXT,
                  profile_photo TEXT,
                  vip_since TIMESTAMP)''')

    if db_type == 'postgres':
        c.execute("ALTER TABLE members ADD COLUMN IF NOT EXISTS country VARCHAR(100);")
        c.execute("ALTER TABLE members ADD COLUMN IF NOT EXISTS state VARCHAR(100);")
        c.execute("ALTER TABLE members ADD COLUMN IF NOT EXISTS vip_since TIMESTAMP;")
        c.execute("ALTER TABLE members ADD COLUMN IF NOT EXISTS industry VARCHAR(255);")
        c.execute("ALTER TABLE members ADD COLUMN IF NOT EXISTS net_worth VARCHAR(255);")
        c.execute("ALTER TABLE members ADD COLUMN IF NOT EXISTS can_write_news BOOLEAN DEFAULT FALSE;")
        c.execute("ALTER TABLE members ADD COLUMN IF NOT EXISTS can_write_insights BOOLEAN DEFAULT FALSE;")
        c.execute("ALTER TABLE members ADD COLUMN IF NOT EXISTS bio TEXT;")
        c.execute("ALTER TABLE members ADD COLUMN IF NOT EXISTS profile_photo TEXT;")
        c.execute("ALTER TABLE members ADD COLUMN IF NOT EXISTS role VARCHAR(20) DEFAULT 'Member';")
        c.execute("ALTER TABLE members ADD COLUMN IF NOT EXISTS is_locked BOOLEAN DEFAULT FALSE;")
        c.execute("ALTER TABLE members ADD COLUMN IF NOT EXISTS failed_attempts INTEGER DEFAULT 0;")
        c.execute("ALTER TABLE members ADD COLUMN IF NOT EXISTS vip_admin_reply TEXT;")
        c.execute("ALTER TABLE members ADD COLUMN IF NOT EXISTS vip_user_proof TEXT;")
        c.execute("ALTER TABLE members ADD COLUMN IF NOT EXISTS kyc_status VARCHAR(20) DEFAULT 'Unverified';")
        c.execute("ALTER TABLE subscription_plans ADD COLUMN IF NOT EXISTS billing_period VARCHAR(100) DEFAULT 'Per Executive Year';")
    else:
        # SQLite
        def add_sqlite_col(table, col_def):
            try:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
            except Exception as e:
                # Log instead of silent pass
                if 'duplicate column' not in str(e).lower():
                    pass # Only ignore expected duplicate column errors
        
        add_sqlite_col('members', 'vip_since TIMESTAMP')
        add_sqlite_col('members', 'industry VARCHAR(255)')
        add_sqlite_col('members', 'net_worth VARCHAR(255)')
        add_sqlite_col('members', 'can_write_news BOOLEAN DEFAULT FALSE')
        add_sqlite_col('members', 'can_write_insights BOOLEAN DEFAULT FALSE')
        add_sqlite_col('members', 'bio TEXT')
        add_sqlite_col('members', 'profile_photo TEXT')
        add_sqlite_col('members', 'kyc_status VARCHAR(20) DEFAULT "Unverified"')
        add_sqlite_col('subscription_plans', 'billing_period VARCHAR(100) DEFAULT "Per Executive Year"')

    c.execute(f'''CREATE TABLE IF NOT EXISTS member_profile_audit
                 (id {pk_type},
                  member_id INTEGER NOT NULL,
                  field_name TEXT NOT NULL,
                  old_value TEXT,
                  new_value TEXT,
                  changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (member_id) REFERENCES members (id))''')

    c.execute(f'''CREATE TABLE IF NOT EXISTS chatrooms
                 (id {pk_type},
                  room_name TEXT NOT NULL,
                  created_by_admin_id INTEGER,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    c.execute(f'''CREATE TABLE IF NOT EXISTS subscription_plans
                 (id {pk_type},
                  plan_name TEXT NOT NULL,
                  price REAL NOT NULL,
                  features TEXT)''') # Added features column here for convenience

    c.execute(f'''CREATE TABLE IF NOT EXISTS system_settings
                 (id {pk_type},
                  support_email TEXT)''')

    c.execute(f'''CREATE TABLE IF NOT EXISTS email_templates
                 (id {pk_type},
                  event_type TEXT NOT NULL UNIQUE,
                  subject TEXT NOT NULL,
                  body TEXT NOT NULL,
                  trigger_event TEXT DEFAULT 'Manual',
                  plan_id INTEGER)''')

    c.execute(f'''CREATE TABLE IF NOT EXISTS vip_verification_fields
                 (id {pk_type},
                  label VARCHAR(255) NOT NULL,
                  field_type VARCHAR(50) NOT NULL,
                  target_country VARCHAR(100) DEFAULT 'Global')''')

    c.execute(f'''CREATE TABLE IF NOT EXISTS onboarding_configs
                 (id {pk_type},
                  plan_id INTEGER NOT NULL,
                  country VARCHAR(10) DEFAULT 'DEFAULT',
                  welcome_message TEXT NOT NULL,
                  FOREIGN KEY(plan_id) REFERENCES subscription_plans(id))''')

    c.execute(f'''CREATE TABLE IF NOT EXISTS onboarding_fields
                 (id {pk_type},
                  config_id INTEGER NOT NULL,
                  field_name VARCHAR(255) NOT NULL,
                  field_type VARCHAR(50) DEFAULT 'file',
                  FOREIGN KEY(config_id) REFERENCES onboarding_configs(id))''')

    # --- CHILD TABLES ---
    c.execute(f'''CREATE TABLE IF NOT EXISTS verification_tokens
                 (id {pk_type},
                  user_id INTEGER NOT NULL,
                  token_string TEXT UNIQUE NOT NULL,
                  is_used BOOLEAN DEFAULT FALSE,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY(user_id) REFERENCES members(id))''')

    c.execute(f'''CREATE TABLE IF NOT EXISTS donations
                 (id {pk_type},
                  member_id INTEGER NOT NULL,
                  amount REAL NOT NULL,
                  visibility_preference TEXT NOT NULL,
                  status TEXT DEFAULT 'Pending',
                  admin_reply TEXT,
                  FOREIGN KEY(member_id) REFERENCES members(id))''')

    c.execute(f'''CREATE TABLE IF NOT EXISTS tickets
                 (id {pk_type},
                  user_id INTEGER NOT NULL,
                  category TEXT NOT NULL,
                  message TEXT NOT NULL,
                  attachment TEXT,
                  status TEXT DEFAULT 'Open',
                  admin_reply TEXT,
                  admin_media TEXT,
                  parent_id INTEGER,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY(user_id) REFERENCES members(id))''')

    c.execute(f'''CREATE TABLE IF NOT EXISTS attachments
                 (id {pk_type},
                  ticket_id INTEGER NOT NULL,
                  file_path TEXT NOT NULL,
                  uploaded_by_admin BOOLEAN DEFAULT FALSE,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY(ticket_id) REFERENCES tickets(id))''')

    c.execute(f'''CREATE TABLE IF NOT EXISTS chatroom_messages
                 (id {pk_type},
                  room_id INTEGER NOT NULL,
                  sender_id INTEGER NOT NULL,
                  message_text TEXT NOT NULL,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  is_pinned BOOLEAN DEFAULT FALSE,
                  channel_id VARCHAR(50) DEFAULT 'main',
                  reply_to_id INTEGER,
                  FOREIGN KEY(room_id) REFERENCES chatrooms(id),
                  FOREIGN KEY(sender_id) REFERENCES members(id))''')

    if db_type == 'postgres':
        c.execute("ALTER TABLE chatroom_messages ADD COLUMN IF NOT EXISTS is_pinned BOOLEAN DEFAULT FALSE;")
        c.execute("ALTER TABLE chatroom_messages ADD COLUMN IF NOT EXISTS channel_id VARCHAR(50) DEFAULT 'main';")
        c.execute("ALTER TABLE chatroom_messages ADD COLUMN IF NOT EXISTS reply_to_id INTEGER;")
    else:
        add_sqlite_col('chatroom_messages', 'is_pinned BOOLEAN DEFAULT FALSE')
        add_sqlite_col('chatroom_messages', 'channel_id VARCHAR(50) DEFAULT "main"')
        add_sqlite_col('chatroom_messages', 'reply_to_id INTEGER')

    c.execute(f'''CREATE TABLE IF NOT EXISTS chatroom_members
                 (id {pk_type},
                  room_id INTEGER NOT NULL,
                  member_id INTEGER NOT NULL)''')

    c.execute(f'''CREATE TABLE IF NOT EXISTS chatroom_attachments
                 (id {pk_type},
                  message_id INTEGER NOT NULL,
                  file_path TEXT NOT NULL,
                  file_size INTEGER,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY(message_id) REFERENCES chatroom_messages(id))''')

    c.execute(f'''CREATE TABLE IF NOT EXISTS kyc_configs
                 (id {pk_type},
                  country VARCHAR(100) NOT NULL,
                  documents_required TEXT,
                  external_link TEXT,
                  post_info_required TEXT)''')

    c.execute(f'''CREATE TABLE IF NOT EXISTS kyc_submissions
                 (id {pk_type},
                  user_id INTEGER NOT NULL,
                  status VARCHAR(20) DEFAULT 'Pending',
                  documents_path TEXT,
                  post_info_data TEXT,
                  post_documents_path TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    c.execute(f'''CREATE TABLE IF NOT EXISTS vip_submissions
                 (id {pk_type},
                  user_id INTEGER NOT NULL,
                  plan_id INTEGER NOT NULL,
                  payment_method VARCHAR(50),
                  transaction_hash TEXT,
                  wire_reference TEXT,
                  giftcard_code TEXT,
                  status VARCHAR(50) DEFAULT 'Pending',
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY(user_id) REFERENCES members(id))''')

    if db_type == 'postgres':
        c.execute("ALTER TABLE vip_submissions ADD COLUMN IF NOT EXISTS payment_method VARCHAR(50);")
        c.execute("ALTER TABLE vip_submissions ADD COLUMN IF NOT EXISTS transaction_hash TEXT;")
        c.execute("ALTER TABLE vip_submissions ADD COLUMN IF NOT EXISTS wire_reference TEXT;")
        c.execute("ALTER TABLE vip_submissions ADD COLUMN IF NOT EXISTS giftcard_code TEXT;")
    else:
        add_sqlite_col('vip_submissions', 'payment_method VARCHAR(50)')
        add_sqlite_col('vip_submissions', 'transaction_hash TEXT')
        add_sqlite_col('vip_submissions', 'wire_reference TEXT')
        add_sqlite_col('vip_submissions', 'giftcard_code TEXT')

    c.execute(f'''CREATE TABLE IF NOT EXISTS vip_submission_data
                 (id {pk_type},
                  submission_id INTEGER REFERENCES vip_submissions(id),
                  field_id INTEGER REFERENCES vip_verification_fields(id),
                  text_response TEXT,
                  file_paths TEXT)''')

    c.execute(f'''CREATE TABLE IF NOT EXISTS vip_pre_payment_chats
                 (id {pk_type},
                  submission_id INTEGER REFERENCES vip_submissions(id),
                  member_id INTEGER REFERENCES members(id),
                  sender_id INTEGER,
                  message TEXT,
                  media_path TEXT,
                  timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    if db_type == 'postgres':
        c.execute("ALTER TABLE vip_pre_payment_chats ADD COLUMN IF NOT EXISTS submission_id INTEGER REFERENCES vip_submissions(id);")
        c.execute("ALTER TABLE vip_pre_payment_chats ADD COLUMN IF NOT EXISTS member_id INTEGER REFERENCES members(id);")
        c.execute("ALTER TABLE vip_pre_payment_chats ADD COLUMN IF NOT EXISTS media_path TEXT;")
        c.execute("ALTER TABLE vip_pre_payment_chats ADD COLUMN IF NOT EXISTS sender_id INTEGER;")
        c.execute("ALTER TABLE vip_pre_payment_chats ADD COLUMN IF NOT EXISTS message TEXT;")
    else:
        # SQLite
        add_sqlite_col('vip_pre_payment_chats', 'media_path TEXT')
        add_sqlite_col('vip_pre_payment_chats', 'sender_id INTEGER')
        add_sqlite_col('vip_pre_payment_chats', 'message TEXT')

    c.execute(f'''CREATE TABLE IF NOT EXISTS vip_periods
                 (id {pk_type},
                  user_id INTEGER NOT NULL,
                  start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  end_time TIMESTAMP,
                  FOREIGN KEY(user_id) REFERENCES members(id))''')

    c.execute(f'''CREATE TABLE IF NOT EXISTS admin_notifications
                 (id {pk_type},
                  member_id INTEGER REFERENCES members(id),
                  action_type VARCHAR(100),
                  message TEXT,
                  target_url TEXT,
                  is_read BOOLEAN DEFAULT FALSE,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    c.execute(f'''CREATE TABLE IF NOT EXISTS member_notifications
                 (id {pk_type},
                  member_id INTEGER REFERENCES members(id),
                  action_type VARCHAR(100),
                  message TEXT,
                  target_url TEXT,
                  is_read BOOLEAN DEFAULT FALSE,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    if db_type == 'postgres':
        c.execute("ALTER TABLE member_notifications ADD COLUMN IF NOT EXISTS target_url TEXT;")
        c.execute("ALTER TABLE member_notifications ADD COLUMN IF NOT EXISTS is_read BOOLEAN DEFAULT FALSE;")
        c.execute("ALTER TABLE admin_notifications ADD COLUMN IF NOT EXISTS is_read BOOLEAN DEFAULT FALSE;")
    else:
        add_sqlite_col('member_notifications', 'target_url TEXT')
        add_sqlite_col('member_notifications', 'is_read BOOLEAN DEFAULT FALSE')
        add_sqlite_col('admin_notifications', 'is_read BOOLEAN DEFAULT FALSE')

    c.execute(f'''CREATE TABLE IF NOT EXISTS club_slideshows
                 (id {pk_type},
                  image_path VARCHAR(255) NOT NULL,
                  info_text TEXT,
                  is_active BOOLEAN DEFAULT TRUE,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    c.execute(f'''CREATE TABLE IF NOT EXISTS site_settings
                 (id {pk_type},
                  setting_key VARCHAR(50) UNIQUE,
                  setting_value TEXT)''')

    c.execute(f'''CREATE TABLE IF NOT EXISTS invite_tokens
                 (id {pk_type},
                  token TEXT UNIQUE NOT NULL,
                  created_by_admin BOOLEAN DEFAULT TRUE,
                  note TEXT,
                  is_used BOOLEAN DEFAULT FALSE,
                  used_by_member_id INTEGER,
                  expires_at TIMESTAMP,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    c.execute(f'''CREATE TABLE IF NOT EXISTS lounge_polls
                 (id {pk_type},
                  question TEXT NOT NULL,
                  options TEXT NOT NULL,
                  is_closed BOOLEAN DEFAULT FALSE,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    c.execute(f'''CREATE TABLE IF NOT EXISTS lounge_poll_votes
                 (id {pk_type},
                  poll_id INTEGER REFERENCES lounge_polls(id),
                  option_index INTEGER,
                  member_id INTEGER,
                  is_injected BOOLEAN DEFAULT FALSE,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    c.execute(f'''CREATE TABLE IF NOT EXISTS lounge_message_reactions
                 (id {pk_type},
                  message_id INTEGER REFERENCES chatroom_messages(id),
                  reaction_type VARCHAR(20),
                  count INTEGER DEFAULT 1,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    c.execute(f'''CREATE TABLE IF NOT EXISTS admin_audit_logs
                 (id {pk_type},
                  admin_id INTEGER REFERENCES members(id),
                  action VARCHAR(100) NOT NULL,
                  target_type VARCHAR(50),
                  target_id INTEGER,
                  details TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    # Seed site_settings default rows
    if db_type == 'postgres':
        c.execute("INSERT INTO site_settings (setting_key, setting_value) VALUES ('footer_info', 'Welcome to Primers Zest App. All rights reserved.') ON CONFLICT (setting_key) DO NOTHING;")
        c.execute("INSERT INTO site_settings (setting_key, setting_value) VALUES ('concierge_welcome_msg', 'An admin has been automatically notified of your arrival. Please use the live chat below to request your specific verification requirements and payment details.') ON CONFLICT (setting_key) DO NOTHING;")
    else:
        c.execute("INSERT OR IGNORE INTO site_settings (setting_key, setting_value) VALUES ('footer_info', 'Welcome to Primers Zest App. All rights reserved.');")
        c.execute("INSERT OR IGNORE INTO site_settings (setting_key, setting_value) VALUES ('concierge_welcome_msg', 'An admin has been automatically notified of your arrival. Please use the live chat below to request your specific verification requirements and payment details.');")
        c.execute("INSERT OR IGNORE INTO site_settings (setting_key, setting_value) VALUES ('member_count_display', '4,726');")

    # Seed chatrooms default row
    if db_type == 'postgres':
        c.execute("INSERT INTO chatrooms (room_name) SELECT 'VIP Lounge' WHERE NOT EXISTS (SELECT 1 FROM chatrooms WHERE room_name = 'VIP Lounge');")
    else:
        c.execute("INSERT OR IGNORE INTO chatrooms (room_name) VALUES ('VIP Lounge');")

    # Ensure Official Admin Member exists
    dummy_hash = generate_password_hash('AdminPostIdentity2026')
    if db_type == 'postgres':
        c.execute("INSERT INTO members (email, mobile, fullname, username, age, gender, travel, income, password_hash, membership_tier, role) SELECT 'admin@system.local', '0000000000', 'Official Admin', 'AdminMaster', 99, 'System', 'N/A', 'Infinite', %s, 'VIP', 'Admin' WHERE NOT EXISTS (SELECT 1 FROM members WHERE username = 'AdminMaster');", (dummy_hash,))
        c.execute("UPDATE members SET role = 'Admin' WHERE username = 'AdminMaster'")
        c.execute("INSERT INTO system_settings (id, support_email) SELECT 1, 'support@yourdomain.com' WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE id = 1);")
    else:
        # Check if AdminMaster exists in SQLite
        c.execute("SELECT 1 FROM members WHERE username = 'AdminMaster'")
        if not c.fetchone():
            c.execute("INSERT INTO members (email, mobile, fullname, username, age, gender, travel, income, password_hash, membership_tier) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                      ('admin@system.local', '0000000000', 'Official Admin', 'AdminMaster', 99, 'System', 'N/A', 'Infinite', dummy_hash, 'VIP'))
        c.execute("SELECT 1 FROM system_settings WHERE id = 1")
        if not c.fetchone():
            c.execute("INSERT INTO system_settings (id, support_email) VALUES (1, 'support@yourdomain.com')")

    # Phase 2: Only set admin password on FIRST creation — never overwrite on restart.
    # Set ADMIN_PASSWORD in your environment variables (Render dashboard) to control this.
    c.execute("SELECT id FROM members WHERE username = 'AdminMaster'")
    if not c.fetchone():
        _initial_admin_pass = os.environ.get('ADMIN_PASSWORD', 'admin123')
        admin_recovery_hash = generate_password_hash(_initial_admin_pass)
        c.execute("UPDATE members SET password_hash = %s WHERE username = 'AdminMaster'", (admin_recovery_hash,))
        print("[INIT] Admin password set from ADMIN_PASSWORD env var.")
    
    # Seed subscription plans if empty
    c.execute("SELECT COUNT(*) FROM subscription_plans")
    if c.fetchone()[0] == 0:
        plans = [
            ('Bronze VIP', 49.99, 'Access to VIP Lounge, 24/7 Priority Support, Entry-level Networking'),
            ('Silver Elite', 149.99, 'All Bronze features + Private Concierge, Exclusive Events, Advanced Networking'),
            ('Gold Executive', 499.99, 'Full Platform Access, Personal Executive Assistant, All Exclusive Invitations')
        ]
        for p_name, p_price, p_feats in plans:
            c.execute("INSERT INTO subscription_plans (plan_name, price, features) VALUES (%s, %s, %s)", (p_name, p_price, p_feats))
        
        # Seed default onboarding configs for the new plans
        c.execute("SELECT id, plan_name FROM subscription_plans")
        rows = c.fetchall()
        for p_id, p_name in rows:
            msg = f"Hello! I'm your dedicated {p_name} onboarding assistant. Please submit your verification details to begin our private consultation."
            c.execute("INSERT INTO onboarding_configs (plan_id, country, welcome_message) VALUES (%s, %s, %s)", (p_id, 'DEFAULT', msg))
            
            # Get the config ID
            c.execute("SELECT id FROM onboarding_configs WHERE plan_id = %s AND country = 'DEFAULT'", (p_id,))
            config_id = c.fetchone()[0]
            
            # Default fields
            fields = [("Government Issued ID", "file"), ("Proof of Funds", "file")]
            for f_name, f_type in fields:
                c.execute("INSERT INTO onboarding_fields (config_id, field_name, field_type) VALUES (%s, %s, %s)", (config_id, f_name, f_type))

    # --- Always-created tables (moved out of seed conditional) ---

    c.execute(f'''CREATE TABLE IF NOT EXISTS card_orders
                 (id {pk_type},
                  member_id INTEGER REFERENCES members(id),
                  card_id INTEGER REFERENCES membership_cards(id),
                  status VARCHAR(50) DEFAULT 'Pending',
                  payment_method VARCHAR(50),
                  proof_path TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    c.execute(f'''CREATE TABLE IF NOT EXISTS polls
                 (id {pk_type},
                  question TEXT NOT NULL,
                  options TEXT NOT NULL,
                  is_active BOOLEAN DEFAULT TRUE,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    c.execute(f'''CREATE TABLE IF NOT EXISTS poll_votes
                 (id {pk_type},
                  poll_id INTEGER REFERENCES polls(id),
                  member_id INTEGER REFERENCES members(id),
                  option_index INTEGER,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  UNIQUE(poll_id, member_id))''')

    c.execute(f'''CREATE TABLE IF NOT EXISTS stars
                 (id {pk_type},
                  name VARCHAR(255) NOT NULL,
                  category VARCHAR(255),
                  bio TEXT,
                  price VARCHAR(100),
                  location VARCHAR(255),
                  image_path TEXT,
                  is_active BOOLEAN DEFAULT TRUE,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    if db_type == 'postgres':
        c.execute("ALTER TABLE stars ADD COLUMN IF NOT EXISTS location VARCHAR(255);")
    else:
        add_sqlite_col('stars', 'location VARCHAR(255)')

    c.execute(f'''CREATE TABLE IF NOT EXISTS star_media
                 (id {pk_type},
                  star_id INTEGER REFERENCES stars(id) ON DELETE CASCADE,
                  file_path TEXT NOT NULL,
                  media_type VARCHAR(20) DEFAULT 'image',
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    # --- MISSING TABLES (Phase 1 crash fixes) ---

    c.execute(f'''CREATE TABLE IF NOT EXISTS star_bookings
                 (id {pk_type},
                  member_id INTEGER REFERENCES members(id),
                  star_id INTEGER REFERENCES stars(id),
                  chatroom_id INTEGER REFERENCES chatrooms(id),
                  request_details TEXT,
                  status VARCHAR(50) DEFAULT 'Pending',
                  occasion VARCHAR(255),
                  timeframe VARCHAR(255),
                  start_time VARCHAR(255),
                  address TEXT,
                  recipient VARCHAR(255),
                  arrival_time VARCHAR(255),
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    c.execute(f'''CREATE TABLE IF NOT EXISTS chatroom_reactions
                 (id {pk_type},
                  message_id INTEGER REFERENCES chatroom_messages(id) ON DELETE CASCADE,
                  member_id INTEGER REFERENCES members(id),
                  emoji VARCHAR(20),
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  UNIQUE(message_id, member_id))''')

    c.execute(f'''CREATE TABLE IF NOT EXISTS message_reactions
                 (id {pk_type},
                  message_id INTEGER REFERENCES chatroom_messages(id) ON DELETE CASCADE,
                  reaction_type VARCHAR(50),
                  count INTEGER DEFAULT 1,
                  is_artificial BOOLEAN DEFAULT FALSE,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    c.execute(f'''CREATE TABLE IF NOT EXISTS crypto_wallets
                 (id {pk_type},
                  currency VARCHAR(50) NOT NULL,
                  network VARCHAR(100),
                  address TEXT NOT NULL,
                  is_active BOOLEAN DEFAULT TRUE,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    c.execute(f'''CREATE TABLE IF NOT EXISTS email_logs
                 (id {pk_type},
                  user_id INTEGER REFERENCES members(id),
                  subject TEXT NOT NULL,
                  body TEXT NOT NULL,
                  sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    # Postgres: ensure new table columns exist on existing deployments
    if db_type == 'postgres':
        c.execute("ALTER TABLE star_bookings ADD COLUMN IF NOT EXISTS arrival_time VARCHAR(255);")
        c.execute("ALTER TABLE star_bookings ADD COLUMN IF NOT EXISTS occasion VARCHAR(255);")
        c.execute("ALTER TABLE star_bookings ADD COLUMN IF NOT EXISTS timeframe VARCHAR(255);")
        c.execute("ALTER TABLE star_bookings ADD COLUMN IF NOT EXISTS start_time VARCHAR(255);")
        c.execute("ALTER TABLE star_bookings ADD COLUMN IF NOT EXISTS address TEXT;")
        c.execute("ALTER TABLE star_bookings ADD COLUMN IF NOT EXISTS recipient VARCHAR(255);")
    else:
        add_sqlite_col('star_bookings', 'arrival_time VARCHAR(255)')
        add_sqlite_col('star_bookings', 'occasion VARCHAR(255)')

    # Phase 5: Add indexes for key lookup columns
    c.execute("CREATE INDEX IF NOT EXISTS idx_members_email ON members(email)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_members_role ON members(role)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_tickets_user ON tickets(user_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_donations_member ON donations(member_id)")

    conn.commit()

# Database initialization removed from top level to prevent import-time crashes.
# It is now called inside the if __name__ == '__main__': block.

@app.route('/')
def index():
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("SELECT setting_value FROM site_settings WHERE setting_key = 'footer_info'")
    row_footer = c.fetchone()
    footer_info = row_footer[0] if row_footer else "Welcome to Primer's Zest"
    
    c.execute("SELECT setting_value FROM site_settings WHERE setting_key = 'member_count_display'")
    row_count = c.fetchone()
    member_count_display = row_count[0] if row_count else "4,726"
    
    return render_template('landing.html', 
                           member_count=member_count_display, 
                           footer_info=footer_info)

    from flask import Response
    return Response("User-agent: *\nDisallow: /\n", mimetype='text/plain')

@app.route('/register', methods=['GET', 'POST'])
def register():
    # --- Invite Token Validation ---
    invite_token = request.args.get('invite', '').strip() or request.form.get('invite_token', '')
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("SELECT setting_value FROM site_settings WHERE setting_key = 'footer_info'")
    row_footer = c.fetchone()
    footer_info = row_footer[0] if row_footer else "Welcome to Primer's Zest"

    # Validate token exists, is unused, and not expired
    token_valid = False
    if invite_token:
        # Use UPPER() for case-insensitive matching
        c.execute("SELECT id, is_used, expires_at FROM invite_tokens WHERE UPPER(token) = UPPER(%s)", (invite_token,))
        tok = c.fetchone()
        if tok:
            tok_is_used = tok[1]
            tok_expires = tok[2]
            
            # Handle potential string type from SQLite or other sources
            if isinstance(tok_expires, str):
                try:
                    # Parse standard ISO format (Y-m-d H:M:S)
                    tok_expires = datetime.datetime.fromisoformat(tok_expires.replace(' ', 'T').split('.')[0])
                except Exception:
                    tok_expires = None
                    
            if not tok_is_used and (tok_expires is None or tok_expires > datetime.datetime.utcnow()):
                token_valid = True

    if not token_valid:
        conn.close()
        # Only flash if an invitation token was actually attempted
        if invite_token:
            flash("The invitation token provided is either invalid, already used, or has expired. Please contact the administrator for a fresh access key.", "error")
        return render_template('invite_required.html')

    if request.method == 'POST':
        try:
            consent_tos = request.form.get('consent_tos')
            consent_privacy = request.form.get('consent_privacy')
            
            if not consent_tos or not consent_privacy:
                flash("You must accept both the Terms of Service and Privacy Policy to continue.", "error")
                return redirect(url_for('register', invite=invite_token))

            email = request.form.get('email', '').strip()
            mobile = request.form.get('mobile', '').strip()
            username = request.form.get('username', '').strip()
            
            c.execute("SELECT id FROM members WHERE email = %s OR mobile = %s OR username = %s", (email, mobile, username))
            if c.fetchone():
                conn.close()
                flash("Email, Mobile, or Username already exists. Please use unique credentials.", "error")
                return redirect(url_for('register', invite=invite_token))

            final_fullname = request.form['fullname'].strip()
            if len(final_fullname.split()) < 2:
                conn.close()
                flash("Please enter your full name (first and last name).", "error")
                return redirect(url_for('register', invite=invite_token))

            final_gender = request.form['gender']
            if final_gender == 'Other':
                final_gender = request.form.get('gender_other', 'Other')

            raw_password = request.form['password']
            confirm_password = request.form['confirm_password']
            
            import re
            if len(raw_password) < 10 or not re.search(r"[a-zA-Z]", raw_password) or not re.search(r"[0-9!@#$%^&*(),.?\":{}|<>]", raw_password):
                flash("Membership integrity requirement: Password must be at least 10 characters long and include a letter and either a number or a symbol.", "error")
                return redirect(url_for('register', invite=invite_token))

            if raw_password != confirm_password:
                flash("Passwords do not match.", "error")
                return redirect(url_for('register', invite=invite_token))
                
            hashed_pw = generate_password_hash(raw_password)
            member_country = request.form.get('country', '').strip()
            if member_country == 'Other':
                member_country = request.form.get('country_custom', 'Other').strip()
                
            member_state = request.form.get('state', '').strip()
            member_industry = request.form.get('industry', '').strip()
            member_networth = request.form.get('net_worth', '').strip()
            # Default income to N/A as requested by user to simplify
            member_income = request.form.get('income') or "Not Specified"

            try:
                if db_type == 'postgres':
                    c.execute("""INSERT INTO members 
                                 (email, mobile, fullname, username, age, gender, travel, income, password_hash, membership_tier, is_verified, country, state, industry, net_worth) 
                                 VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'Regular', FALSE, %s, %s, %s, %s) RETURNING id""",
                              (email, mobile, final_fullname, username, request.form['age'], final_gender, request.form['travel'], member_income, hashed_pw, member_country, member_state, member_industry, member_networth))
                    new_user_id = c.fetchone()[0]
                else:
                    c.execute("""INSERT INTO members 
                                 (email, mobile, fullname, username, age, gender, travel, income, password_hash, membership_tier, is_verified, country, state, industry, net_worth) 
                                 VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'Regular', 0, %s, %s, %s, %s)""",
                              (email, mobile, final_fullname, username, request.form['age'], final_gender, request.form['travel'], member_income, hashed_pw, member_country, member_state, member_industry, member_networth))
                    new_user_id = c.cursor.lastrowid

                # Mark invite token as used
                c.execute("UPDATE invite_tokens SET is_used = TRUE, used_by_member_id = %s WHERE token = %s", (new_user_id, invite_token))

                subj, body = get_templated_email('Registration', final_fullname)
                if subj:
                    send_email_notification(email, subj, body, user_id=new_user_id)
                    
                conn.commit()
                conn.close()
                flash('Account created successfully. Welcome to Primer\'s Zest.', 'success')
                return redirect(url_for('member_login'))
            except Exception as db_err:
                conn.rollback()
                conn.close()
                print(f"Registration DB Error: {db_err}")
                flash("Account creation failed due to a system error. Please try again or contact support.", 'error')
                return redirect(url_for('register', invite=invite_token))
        except Exception as e:
            conn.close()
            return f"System Error: {str(e)}"
    
    conn.close()
    return render_template('register.html', footer_info=footer_info, invite_token=invite_token)

@app.route('/verify_email/<token>')
def verify_email(token):
    # Step 4: Locate/Implement verify email route with already-verified check
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
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
    try:
        conn, db_type = get_db_connection()
        c = get_cursor(conn, db_type)
        c.execute("SELECT setting_value FROM site_settings WHERE setting_key = 'footer_info'")
        res = c.fetchone()
        footer_info = res[0] if res else "Welcome to Primers Zest"

        if request.method == 'POST':
            throttled, wait_time = check_throttle()
            if not throttled:
                flash(f"Security throttle active. Please wait {wait_time}s.", "error")
                return render_template('member_login.html', footer_info=footer_info)

            email = request.form.get('email')
            password = request.form.get('password')
            
            c.execute("SELECT id, fullname, password_hash, is_verified, is_active, is_locked, failed_attempts, membership_tier FROM members WHERE email = %s", (email,))
            member = c.fetchone()
            
            if member:
                user_id, fullname, hashed_pw, is_verified, is_active, is_locked, failed_attempts, membership_tier = member
                
                if is_locked:
                    # Step 4: Query system settings for support email
                    c.execute("SELECT support_email FROM system_settings WHERE id = 1")
                    support_res = c.fetchone()
                    support_email = support_res[0] if support_res else "support@primerszest.com"
                    # Phase 4: escape support_email to prevent stored XSS via HTML in flash
                    from markupsafe import escape as _esc
                    _safe_email = _esc(support_email)
                    flash(f'Your account has been frozen. Contact support at {_safe_email} to request reactivation.', "error")
                    conn.close()
                    return render_template('member_login.html', footer_info=footer_info)
                
                if check_password_hash(hashed_pw, password):
                    record_login_attempt(get_ip(), True)
                    session['member_id'] = user_id
                    session['member_fullname'] = fullname
                    session['membership_tier'] = membership_tier
                    # Ensure we clear any stale admin session flag
                    session.pop('is_admin', None)
                    
                    if int(is_active) == 0:
                        conn.close()
                        flash("Note: Your account is currently under review. Use the appeal channel to contact us.", "warning")
                        return redirect(url_for('member_appeal'))
                    
                    # Step 5: Reset failed attempts on success (Targeted)
                    c.execute("UPDATE members SET failed_attempts = 0 WHERE email = %s", (email,))
                    conn.commit()
                    conn.close()

                    return redirect(url_for('member_dashboard'))
                else:
                    record_login_attempt(get_ip(), False)
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
                        conn, db_type = get_db_connection()
                        c = get_cursor(conn, db_type)
                        c.execute("SELECT support_email FROM system_settings WHERE id = 1")
                        support_email = c.fetchone()[0] if c.fetchone() else 'support@primerszest.com'
                        conn.close()
                        from markupsafe import escape as _esc
                        flash(f'Your account has been frozen. Contact support at {_esc(support_email)} to request reactivation.', "error")
                    else:
                        c.execute("UPDATE members SET failed_attempts = %s WHERE email = %s", (new_failed, email))
                        conn.commit()
                        conn.close()
                        flash("Invalid credentials.", "error")
                    return redirect(url_for('member_login'))
            else:
                conn.close()
                flash("Invalid credentials.", "error")
                return redirect(url_for('member_login'))
                
        return render_template('member_login.html', footer_info=footer_info)
    except Exception as e:
        return f"<h1>DIAGNOSTIC CRASH REPORT</h1><p><b>Error:</b> {str(e)}</p><pre>{traceback.format_exc()}</pre>", 200

@app.route('/admin/settings/update', methods=['POST'])
def admin_settings_update():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    # List of all possible settings that can be updated from the form
    fields = [
        'footer_info', 'star_booking_writeup', 'concierge_welcome_msg',
        'member_count_display', 'bank_instructions', 'star_occasions'
    ]
    
    for field in fields:
        val = request.form.get(field)
        if val is not None:
            # Use INSERT OR REPLACE to be absolutely sure the key exists and is updated
            if db_type == 'sqlite':
                c.execute("INSERT OR REPLACE INTO site_settings (setting_key, setting_value) VALUES (?, ?)", (field, val))
            else:
                c.execute("""
                    INSERT INTO site_settings (setting_key, setting_value) 
                    VALUES (%s, %s) 
                    ON CONFLICT (setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value
                """, (field, val))
        
    conn.commit()
    conn.close()
    log_admin_action('update_settings', details="Updated global system configuration")
    flash("Global system configuration updated successfully.")
    return redirect(request.referrer or url_for('admin_dashboard'))


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
            conn, db_type = get_db_connection()
            c = get_cursor(conn, db_type)
            
            if db_type == 'postgres':
                c.execute("INSERT INTO tickets (user_id, category, message) VALUES (%s, %s, %s) RETURNING id",
                          (session['member_id'], category, message))
                ticket_id = c.fetchone()[0]
            else:
                c.execute("INSERT INTO tickets (user_id, category, message) VALUES (%s, %s, %s)",
                          (session['member_id'], category, message))
                ticket_id = c.lastrowid
            
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
            
            add_admin_notification(session['member_id'], 'Message', f"New ticket message from {session.get('member_fullname')}: {category}", url_for('admin_user_vault', member_id=session['member_id']))
            
            flash('Message successfully sent.')
            return redirect(url_for('member_dashboard'))
            
        else: # Default donation form
            amount = request.form.get('amount')
            visibility = request.form.get('visibility_preference')
            
            conn, db_type = get_db_connection()
            c = get_cursor(conn, db_type)
            c.execute("INSERT INTO donations (member_id, amount, visibility_preference, status) VALUES (%s, %s, %s, 'Pending')",
                      (session['member_id'], amount, visibility))
            conn.commit()
            conn.close()
            return redirect(url_for('member_dashboard'))
        
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("SELECT amount, status, admin_reply, visibility_preference as method, 'Contribution' as type, created_at as timestamp FROM donations WHERE member_id = %s", (session['member_id'],))
    my_donations = c.fetchall()
    
    # Fetch VIP submissions (Subscription Ledger)
    c.execute("""
        SELECT p.plan_name, s.status, s.payment_method, p.price, s.created_at 
        FROM vip_submissions s
        JOIN subscription_plans p ON s.plan_id = p.id
        WHERE s.user_id = %s
        ORDER BY s.created_at DESC
    """, (session['member_id'],))
    my_subscriptions = c.fetchall()
    
    c.execute("""
        SELECT t.id, t.category, t.message, t.status, t.admin_reply, a.file_path, a.uploaded_by_admin 
        FROM tickets t 
        LEFT JOIN attachments a ON t.id = a.ticket_id 
        WHERE t.user_id = %s 
        ORDER BY t.created_at DESC
        LIMIT 5
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
    c.execute("SELECT plan_name, price, features, id FROM subscription_plans ORDER BY id")
    plans = c.fetchall()
    
    # Fetch active slideshows — use IS NOT FALSE to include rows where is_active is NULL
    c.execute("SELECT image_path, info_text FROM club_slideshows WHERE is_active IS NOT FALSE ORDER BY created_at DESC")
    slides = c.fetchall()
    
    conn.close()
        
    return render_template('dashboard.html', 
                           fullname=session.get('member_fullname'), 
                           donations=my_donations, 
                           subscriptions=my_subscriptions,
                           tickets=my_tickets, 
                           membership_tier=current_tier, 
                           vip_admin_reply=admin_reply, 
                           vip_user_proof=user_proof, 
                           plans=plans, 
                           slides=slides)

@app.route('/request_payment_details', methods=['POST'])
def request_payment_details():
    if 'member_id' not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    data = request.get_json()
    plan_name = data.get('plan_name', 'Unknown')
    member_id = session['member_id']
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    # Insert a ticket for the admin
    message = f"I am requesting payment details for the {plan_name} plan."
    c.execute("""
        INSERT INTO tickets (user_id, category, message, status) 
        VALUES (%s, %s, %s, %s)
    """, (member_id, 'Payment Detail Request', message, 'Open'))
    
    # Insert Admin Notification
    add_admin_notification(member_id, 'Plan Request', f"{session.get('member_fullname')} has requested payment details for the {plan_name} plan.", url_for('admin_user_vault', member_id=member_id))
    
    conn.commit()
    conn.close()
    
    return jsonify({"success": True})

@app.route('/dashboard/request_vip', methods=['POST'])
def dashboard_request_vip():
    if 'member_id' not in session:
        return redirect(url_for('member_login'))
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
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
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
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
    
    add_admin_notification(session['member_id'], 'Payment Verification', f"New payment receipt uploaded by {session.get('member_fullname')}.", url_for('admin_user_vault', member_id=session['member_id']))
    
    flash("Payment receipt submitted. An admin will review your 'VIP Payment Verification' ticket in the Vault shortly.")
    return redirect(url_for('member_dashboard'))
    

@app.route('/member/kyc_verify', methods=['GET', 'POST'])
def member_kyc_verify():
    if 'member_id' not in session: return redirect(url_for('member_login'))
    user_id = session['member_id']
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    c.execute("SELECT country, kyc_status FROM members WHERE id = %s", (user_id,))
    member_data = c.fetchone()
    if not member_data: return redirect(url_for('member_login'))
    
    country = member_data[0] or 'Global'
    kyc_status = member_data[1]
    
    # Get config for country or fallback to Global
    c.execute("SELECT * FROM kyc_configs WHERE country = %s", (country,))
    config = c.fetchone()
    if not config:
        c.execute("SELECT * FROM kyc_configs WHERE country = 'Global'")
        config = c.fetchone()
        
    if not config:
        # Emergency fallback if admin hasn't set anything up yet
        config = (0, 'Global', 'Government ID', None, 'ID Number')
        
    if request.method == 'POST':
        post_info_data = request.form.get('post_info_data')
        
        # Handle Multi-file upload for Step 1
        documents_paths = []
        files = request.files.getlist('kyc_documents')
        upload_folder = os.path.join('static', 'uploads')
        os.makedirs(upload_folder, exist_ok=True)
        
        for file in files:
            if file and file.filename != '':
                filename = secure_filename(file.filename)
                timestamp = int(time.time())
                unique_name = f"kyc_step1_{user_id}_{timestamp}_{filename}"
                file_path = os.path.join(upload_folder, unique_name)
                file.save(file_path)
                documents_paths.append(file_path)

        # Handle Multi-file upload for Step 3 (Post-Verification Docs)
        post_documents_paths = []
        post_files = request.files.getlist('post_kyc_documents')
        
        for file in post_files:
            if file and file.filename != '':
                filename = secure_filename(file.filename)
                timestamp = int(time.time())
                unique_name = f"kyc_step3_{user_id}_{timestamp}_{filename}"
                file_path = os.path.join(upload_folder, unique_name)
                file.save(file_path)
                post_documents_paths.append(file_path)

        documents_path_str = ",".join(documents_paths)
        post_documents_path_str = ",".join(post_documents_paths)

        c.execute("INSERT INTO kyc_submissions (user_id, status, documents_path, post_info_data, post_documents_path) VALUES (%s, 'Pending', %s, %s, %s)",
                  (user_id, documents_path_str, post_info_data, post_documents_path_str))
        
        c.execute("UPDATE members SET kyc_status = 'Pending' WHERE id = %s", (user_id,))
        conn.commit()
        conn.close()
        flash("Your KYC application has been successfully submitted and is under review.", "success")
        return redirect(url_for('member_profile'))
        
    conn.close()
    return render_template('member_kyc_verify.html', country=country, config=config, kyc_status=kyc_status)

@app.route('/profile', methods=['GET', 'POST'])
def member_profile():
    if 'member_id' not in session:
        return redirect(url_for('member_login'))
    
    member_id = session['member_id']
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)

    # Fetch current member data first to avoid UnboundLocalError during POST
    c.execute("SELECT * FROM members WHERE id = %s", (member_id,))
    member_row = c.fetchone()
    if not member_row:
        conn.close()
        return "Member not found", 404
    member = dict(member_row)

    if request.method == 'POST':
        # Check if it's a photo upload or general update
        if 'profile_photo' in request.files:
            file = request.files['profile_photo']
            if file and file.filename:
                filename = f"profile_{member_id}_{int(time.time())}_{secure_filename(file.filename)}"
                save_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(save_path)
                c.execute("UPDATE members SET profile_photo = %s WHERE id = %s", (f"/static/uploads/{filename}", member_id))
        
        # Handle textual updates
        fullname = request.form.get('fullname')
        mobile = request.form.get('mobile')
        bio = request.form.get('bio')
        country = request.form.get('country')
        state = request.form.get('state')
        income = request.form.get('income', member['income'])
        travel = request.form.get('travel', member['travel'])
        
        updates = []
        if fullname and fullname != member['fullname']:
            updates.append(('fullname', member['fullname'], fullname))
        if mobile and mobile != member['mobile']:
            updates.append(('mobile', member['mobile'], mobile))
        if bio and bio != member['bio']:
            updates.append(('bio', member['bio'], bio))
        if country and country != member['country']:
            updates.append(('country', member['country'], country))
        if state and state != member['state']:
            updates.append(('state', member['state'], state))
        if income and income != member['income']:
            updates.append(('income', member['income'], income))
        if travel and travel != member['travel']:
            updates.append(('travel', member['travel'], travel))

        if updates:
            # 1. Update the database
            c.execute("""
                UPDATE members SET 
                fullname = %s, mobile = %s, bio = %s, 
                country = %s, state = %s, income = %s, travel = %s 
                WHERE id = %s
            """, (fullname or member['fullname'], 
                  mobile or member['mobile'], 
                  bio or member['bio'], 
                  country or member['country'], 
                  state or member['state'], 
                  income, 
                  travel, 
                  member_id))
            
            # 2. Log changes for audit
            for field, old, new in updates:
                c.execute("INSERT INTO member_profile_audit (member_id, field_name, old_value, new_value) VALUES (%s, %s, %s, %s)",
                          (member_id, field, str(old), str(new)))
            
            # 3. Notify Admin
            update_summary = ", ".join([u[0] for u in updates])
            add_admin_notification(member_id, "PROFILE_UPDATE", 
                                   f"Member {member['fullname']} updated profile fields: {update_summary}", 
                                   f"/admin/vault/{member_id}")
            
            session['member_fullname'] = fullname or member['fullname'] # Update session name
            
        conn.commit()
        flash("Profile successfully updated.")
        return redirect(url_for('member_profile'))

    conn.close()

    # Calculate Profile Completeness
    fields_to_check = [
        'fullname', 'email', 'mobile', 'age', 'gender', 
        'country', 'state', 'income', 'bio', 'profile_photo'
    ]
    completed_fields = 0
    for field in fields_to_check:
        if member.get(field):
            completed_fields += 1
    
    completeness = int((completed_fields / len(fields_to_check)) * 100)
    
    # Status tier mapping
    status_tier = "New Member"
    if completeness >= 100: status_tier = "Executive All-Star"
    elif completeness >= 80: status_tier = "Intermediate"
    elif completeness >= 50: status_tier = "Rising Professional"
    elif completeness >= 20: status_tier = "Getting Started"

    return render_template('profile.html', 
                           member=member, 
                           completeness=completeness, 
                           status_tier=status_tier)

@app.route('/become_model')
def become_model():
    if 'member_id' not in session:
        return redirect(url_for('member_login'))
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("SELECT kyc_status FROM members WHERE id = %s", (session['member_id'],))
    member = c.fetchone()
    conn.close()
    
    kyc_status = member[0] if member else None
    return render_template('become_model.html', kyc_status=kyc_status)

@app.route('/notifications')
def member_notifications():
    if 'member_id' not in session:
        return redirect(url_for('member_login'))
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("SELECT * FROM member_notifications WHERE member_id = %s ORDER BY created_at DESC", (session['member_id'],))
    notifications = c.fetchall()
    conn.close()
    
    return render_template('member_notifications.html', notifications=notifications)
    
@app.route('/notifications/action/<int:n_id>')
def member_notification_action(n_id):
    if 'member_id' not in session:
        return redirect(url_for('member_login'))
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("SELECT target_url FROM member_notifications WHERE id = %s AND member_id = %s", (n_id, session['member_id']))
    row = c.fetchone()
    
    if row:
        target_url = row[0]
        c.execute("UPDATE member_notifications SET is_read = TRUE WHERE id = %s AND member_id = %s", (n_id, session['member_id']))
        conn.commit()
        conn.close()
        if target_url and target_url.startswith('/'):
            return redirect(target_url)
    
    conn.close()
    return redirect(url_for('member_notifications'))

@app.route('/notifications/read/<int:n_id>', methods=['POST'])
def member_mark_read(n_id):
    if 'member_id' not in session:
        return redirect(url_for('member_login'))
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("UPDATE member_notifications SET is_read = TRUE WHERE id = %s AND member_id = %s", (n_id, session['member_id']))
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for('member_notifications'))

@app.route('/notifications/mark_all_read', methods=['POST'])
def member_mark_all_read():
    if 'member_id' not in session:
        return redirect(url_for('member_login'))
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("UPDATE member_notifications SET is_read = TRUE WHERE member_id = %s", (session['member_id'],))
    conn.commit()
    conn.close()
    flash("All notifications marked as read.")
    return redirect(url_for('member_notifications'))

@app.route('/profile/change_password', methods=['POST'])
def change_password():
    if 'member_id' not in session:
        return redirect(url_for('member_login'))
        
    curr_pass = request.form.get('current_password')
    new_pass = request.form.get('new_password')
    conf_pass = request.form.get('confirm_password')
    
    if new_pass != conf_pass:
        flash("New passwords do not match.")
        return redirect(url_for('member_profile'))
        
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("SELECT password_hash FROM members WHERE id = %s", (session['member_id'],))
    row = c.fetchone()
    
    if row and check_password_hash(row[0], curr_pass):
        hashed = generate_password_hash(new_pass)
        c.execute("UPDATE members SET password_hash = %s WHERE id = %s", (hashed, session['member_id']))
        conn.commit()
        flash("Password updated successfully.")
    else:
        flash("Incorrect current password.")
        
    conn.close()
    return redirect(url_for('member_profile'))

@app.route('/support', methods=['GET', 'POST'])
def support():
    if 'member_id' not in session:
        return redirect(url_for('member_login'))
        
    if request.method == 'POST':
        category = request.form.get('category')
        message = request.form.get('message')
        attachments = request.files.getlist('attachment')
        
        conn, db_type = get_db_connection()
        c = get_cursor(conn, db_type)
        
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
        
        add_admin_notification(session['member_id'], 'Support Ticket', f"New support ticket from {session.get('member_fullname')}: {category}", url_for('admin_user_vault', member_id=session['member_id']))
        
        flash('Message sent successfully')
        return redirect(url_for('member_dashboard'))
        
    return render_template('support.html')

@app.route('/admin_login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        throttled, wait_time = check_throttle()
        if not throttled:
            flash(f"Security throttle active. Please wait {wait_time}s.", "error")
            return render_template('admin_login.html')

        password = request.form.get('password')
        conn, db_type = get_db_connection()
        c = get_cursor(conn, db_type)
        
        # Security: Check for Admin role instead of hardcoded username
        c.execute("SELECT id, password_hash, email FROM members WHERE role = 'Admin' LIMIT 1")
        admin_row = c.fetchone()
        
        # Phase 2: use index access — works on both psycopg2 DictCursor and sqlite3.Row
        if admin_row and check_password_hash(admin_row[1], password):
            record_login_attempt(get_ip(), True)
            session['is_admin'] = True
            session['member_id'] = admin_row[0]
            session['admin_username'] = admin_row[2]  # Phase 2: populate for audit logs
            log_admin_action('login_success', details="Admin logged in directly")
            conn.close()
            return redirect(url_for('admin_dashboard'))
        else:
            conn.close()
            record_login_attempt(get_ip(), False)
            flash("Invalid administrative credentials.")
            return render_template('admin_login.html')
    return render_template('admin_login.html')

@app.route('/admin')
def admin_dashboard():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))

    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)

    sort_by = request.args.get('sort_by', 'newest')

    if sort_by == 'oldest':
        order_clause = "ORDER BY m.id ASC"
    elif sort_by == 'name_asc':
        order_clause = "ORDER BY m.fullname ASC"
    elif sort_by == 'vip':
        order_clause = "ORDER BY (m.membership_tier = 'VIP') DESC, m.id DESC"
    elif sort_by == 'tier':
        order_clause = "ORDER BY m.membership_tier ASC, m.id DESC"
    else:  # default: newest
        order_clause = "ORDER BY m.id DESC"

    c.execute(f"""
        SELECT m.*,
        (SELECT message FROM tickets t WHERE t.user_id = m.id ORDER BY created_at DESC LIMIT 1) as latest_ticket
        FROM members m
        {order_clause}
    """)
    all_members = c.fetchall()
    
    # Phase 4: limit to prevent admin dashboard timing out as data grows
    c.execute("SELECT * FROM donations ORDER BY created_at DESC LIMIT 200")
    all_donations = c.fetchall()

    # Fetch Audit Logs
    c.execute("""
        SELECT a.*, m.username as admin_username 
        FROM admin_audit_logs a 
        LEFT JOIN members m ON a.admin_id = m.id 
        ORDER BY a.created_at DESC LIMIT 100
    """)
    audit_logs = c.fetchall()
    
    c.execute("SELECT * FROM subscription_plans ORDER BY id")
    all_plans = c.fetchall()

    c.execute("SELECT * FROM email_templates ORDER BY id")
    all_templates = c.fetchall()

    c.execute("SELECT * FROM vip_verification_fields ORDER BY id")
    vip_fields = c.fetchall()

    c.execute("""
        SELECT an.*, m.username 
        FROM admin_notifications an
        JOIN members m ON an.member_id = m.id
        ORDER BY an.created_at DESC
    """)
    notifications = c.fetchall()
    
    c.execute("SELECT * FROM club_slideshows ORDER BY created_at DESC")
    slides = c.fetchall()
    
    c.execute("SELECT setting_key, setting_value FROM site_settings")
    settings_dict = {row[0]: row[1] for row in c.fetchall()}
    
    c.execute("""
        SELECT DISTINCT m.id, m.fullname, m.email, m.membership_tier
        FROM members m
        JOIN vip_pre_payment_chats vpc ON m.id = vpc.member_id
        ORDER BY m.id DESC
    """)
    active_chats = c.fetchall()
    
    # Fetch all star bookings for the admin
    c.execute("""
        SELECT sb.*, s.name as star_name, m.fullname as member_name, m.username, c.room_name
        FROM star_bookings sb
        LEFT JOIN stars s ON sb.star_id = s.id
        JOIN members m ON sb.member_id = m.id
        JOIN chatrooms c ON sb.chatroom_id = c.id
        ORDER BY sb.created_at DESC
    """)
    star_bookings = c.fetchall()
    
    # Fetch all stars for management
    c.execute("SELECT * FROM stars ORDER BY name ASC")
    all_stars = c.fetchall()

    c.execute("""
        SELECT t.*, m.fullname, m.username, m.email 
        FROM tickets t
        JOIN members m ON t.user_id = m.id
        WHERE t.category = 'APPEAL'
        ORDER BY t.created_at DESC
    """)
    appeals = c.fetchall()
    c.execute("SELECT id, token, note, is_used, expires_at, created_at, used_by_member_id FROM invite_tokens ORDER BY created_at DESC")
    raw_invites = c.fetchall()
    invite_tokens = []
    for inv in raw_invites:
        # Support both tuple/list results and dict-like results
        if isinstance(inv, (tuple, list)):
            # id, token, note, is_used, expires_at, created_at, used_by_member_id
            inv_dict = {
                'id': inv[0], 'token': inv[1], 'note': inv[2], 'is_used': inv[3],
                'expires_at': inv[4], 'created_at': inv[5], 'used_by_member_id': inv[6]
            }
        else:
            inv_dict = dict(inv)

        # Parse expires_at
        exp = inv_dict.get('expires_at')
        if isinstance(exp, str):
            try:
                inv_dict['expires_at'] = datetime.datetime.fromisoformat(exp.replace(' ', 'T').split('.')[0])
            except:
                pass
        
        # Parse created_at
        cre = inv_dict.get('created_at')
        if isinstance(cre, str):
            try:
                inv_dict['created_at'] = datetime.datetime.fromisoformat(cre.replace(' ', 'T').split('.')[0])
            except Exception as e:
                print(f"Date Parse Error: {e}")
                
        invite_tokens.append(inv_dict)
    c.execute("""
        SELECT s.*, m.username, m.fullname, p.plan_name, p.price,
               (SELECT GROUP_CONCAT(file_paths) FROM vip_submission_data d WHERE d.submission_id = s.id) as all_files
        FROM vip_submissions s
        JOIN members m ON s.user_id = m.id
        JOIN subscription_plans p ON s.plan_id = p.id
        ORDER BY s.created_at DESC
    """) if db_type == 'sqlite' else c.execute("""
        SELECT s.*, m.username, m.fullname, p.plan_name, p.price,
               (SELECT string_agg(file_paths, ',') FROM vip_submission_data d WHERE d.submission_id = s.id) as all_files
        FROM vip_submissions s
        JOIN members m ON s.user_id = m.id
        JOIN subscription_plans p ON s.plan_id = p.id
        ORDER BY s.created_at DESC
    """)
    vip_submissions = c.fetchall()

    c.execute("SELECT * FROM crypto_wallets ORDER BY currency ASC")
    crypto_wallets = c.fetchall()

    # Fetch unread notifications for badge
    if db_type == 'postgres':
        c.execute("SELECT * FROM admin_notifications WHERE is_read = FALSE ORDER BY created_at DESC")
    else:
        c.execute("SELECT * FROM admin_notifications WHERE is_read = 0 ORDER BY created_at DESC")
    notifications = c.fetchall()
    
    # Fetch all notifications for the Resolution Centre
    c.execute("SELECT * FROM admin_notifications ORDER BY created_at DESC LIMIT 50")
    all_notifications = c.fetchall()

    c.execute("SELECT * FROM lounge_polls ORDER BY created_at DESC")
    polls = c.fetchall()

    conn.close()
    return render_template('admin.html', 
                           members=all_members, 
                           donations=all_donations, 
                           audit_logs=audit_logs,
                           plans=all_plans, 
                           email_templates=all_templates, 
                           vip_fields=vip_fields, 
                           notifications=notifications, 
                           all_notifications=all_notifications,
                           slides=slides, 
                           settings=settings_dict, 
                           active_chats=active_chats,
                           star_bookings=star_bookings,
                           all_stars=all_stars,
                           appeals=appeals,
                           invite_tokens=invite_tokens,
                           vip_submissions=vip_submissions,
                           crypto_wallets=crypto_wallets,
                           polls=polls,
                           host_url=request.host_url)

@app.route('/admin/stars/add', methods=['POST'])
def admin_add_star():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
        
    name = request.form.get('name')
    category = request.form.get('category')
    bio = request.form.get('bio')
    price = request.form.get('price')
    location = request.form.get('location')
    media_files = request.files.getlist('star_media')
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    # Insert base star record
    if db_type == 'postgres':
        c.execute("INSERT INTO stars (name, category, bio, price, location) VALUES (%s, %s, %s, %s, %s) RETURNING id", 
                  (name, category, bio, price, location))
        star_id = c.fetchone()[0]
    else:
        c.execute("INSERT INTO stars (name, category, bio, price, location) VALUES (%s, %s, %s, %s, %s)", 
                  (name, category, bio, price, location))
        star_id = c.cursor.lastrowid
        
    # Process multiple media files (Limit to 10)
    primary_path = ""
    for i, media in enumerate(media_files[:10]):
        if media and media.filename != '':
            filename = secure_filename(media.filename)
            filename = f"star_{star_id}_{int(time.time())}_{i}_{filename}"
            db_path = save_uploaded_file(media, custom_filename=filename)
            if db_path:
                if i == 0: primary_path = db_path
                m_type = 'video' if filename.lower().endswith(('.mp4', '.mov', '.avi')) else 'image'
                c.execute("INSERT INTO star_media (star_id, file_path, media_type) VALUES (%s, %s, %s)", 
                          (star_id, db_path, m_type))
    
    # Update primary image path for legacy support
    if primary_path:
        c.execute("UPDATE stars SET image_path = %s WHERE id = %s", (primary_path, star_id))
        
    conn.commit()
    conn.close()
    flash(f"New talent '{name}' added with {len(media_files[:10])} media assets.")
    return redirect(url_for('admin_dashboard', section='stars-roster'))

@app.route('/api/admin/unread_count')
def api_admin_unread_count():
    if not session.get('is_admin'):
        return {"error": "Unauthorized"}, 401
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    if db_type == 'postgres':
        c.execute("SELECT COUNT(*) FROM admin_notifications WHERE is_read = FALSE")
    else:
        c.execute("SELECT COUNT(*) FROM admin_notifications WHERE is_read = 0")
    count = c.fetchone()[0]
    conn.close()
    return {"unread_count": count}

@app.route('/admin/stars/edit/<int:star_id>', methods=['POST'])
def admin_edit_star(star_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
        
    name = request.form.get('name', '').strip()
    category = request.form.get('category', '').strip()
    bio = request.form.get('bio', '').strip()
    location = request.form.get('location', '').strip()
    replace_media = request.form.get('replace_media') == '1'
    
    # Safely parse price — default to 0 if blank/invalid
    try:
        price = float(request.form.get('price', 0) or 0)
    except (ValueError, TypeError):
        price = 0.0
    
    new_media = request.files.getlist('star_media')
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    try:
        c.execute("UPDATE stars SET name=%s, category=%s, bio=%s, price=%s, location=%s WHERE id=%s",
                  (name, category, bio, price, location, star_id))
        
        # Optionally wipe existing media before adding new uploads
        if replace_media:
            c.execute("DELETE FROM star_media WHERE star_id = %s", (star_id,))
            c.execute("UPDATE stars SET image_path = NULL WHERE id = %s", (star_id,))
            count_existing = 0
        else:
            c.execute("SELECT COUNT(*) FROM star_media WHERE star_id = %s", (star_id,))
            res_count = c.fetchone()
            count_existing = res_count[0] if res_count else 0
        
        for i, media in enumerate(new_media):
            if count_existing >= 10:
                break
            if media and media.filename != '':
                filename = secure_filename(media.filename)
                filename = f"star_{star_id}_update_{int(time.time())}_{i}_{filename}"
                db_path = save_uploaded_file(media, custom_filename=filename)
                if db_path:
                    m_type = 'video' if filename.lower().endswith(('.mp4', '.mov', '.avi')) else 'image'
                    c.execute("INSERT INTO star_media (star_id, file_path, media_type) VALUES (%s, %s, %s)", 
                              (star_id, db_path, m_type))
                    count_existing += 1
                    
                    # Update primary image if none is set yet
                    c.execute("SELECT image_path FROM stars WHERE id = %s", (star_id,))
                    row = c.fetchone()
                    if not row or not row[0]:
                        c.execute("UPDATE stars SET image_path = %s WHERE id = %s", (db_path, star_id))
        
        conn.commit()
        flash("Talent details and media updated successfully.", "success")
    except Exception as e:
        conn.rollback()
        print(f"[admin_edit_star] Error updating star {star_id}: {e}")
        flash(f"Update failed: an internal error occurred. Please try again.", "error")
    finally:
        conn.close()
    
    return redirect(url_for('admin_dashboard', section='stars-roster'))

@app.route('/admin/stars/delete/<int:star_id>', methods=['POST'])
def admin_delete_star(star_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
        
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("DELETE FROM stars WHERE id = %s", (star_id,))
    conn.commit()
    conn.close()
    flash("Talent removed from roster.")
    return redirect(url_for('admin_dashboard', section='stars-roster'))

@app.route('/admin/bookings/status/<int:booking_id>', methods=['POST'])
def admin_update_booking_status(booking_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
        
    new_status = request.form.get('status')
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    c.execute("UPDATE star_bookings SET status = %s WHERE id = %s", (new_status, booking_id))
    
    # Notify member
    c.execute("SELECT member_id, star_id FROM star_bookings WHERE id = %s", (booking_id,))
    booking = c.fetchone()
    if booking:
        c.execute("SELECT name FROM stars WHERE id = %s", (booking[1],))
        star_name = c.fetchone()[0]
        add_member_notification(booking[0], 'Booking Update', f"Your request for {star_name} is now: {new_status}")
        
    conn.commit()
    conn.close()
    flash(f"Booking status updated to {new_status}.")
    return redirect(request.referrer or url_for('admin_dashboard'))

@app.route('/admin/notifications/mark_read/<int:n_id>', methods=['POST'])
def admin_mark_read(n_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("UPDATE admin_notifications SET is_read = TRUE WHERE id = %s", (n_id,))
    conn.commit()
    conn.close()
    log_admin_action('mark_read', target_type='notification', target_id=n_id)
    return redirect(request.referrer or url_for('admin_dashboard'))

@app.route('/admin/notifications/mark_all_read', methods=['POST'])
def admin_mark_all_read():
    if not session.get('is_admin'): return redirect(url_for('admin_login'))
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("UPDATE admin_notifications SET is_read = TRUE")
    conn.commit()
    conn.close()
    
    log_admin_action('mark_all_read', target_type='notifications')
    flash("All notifications marked as read.")
    return redirect(request.referrer or url_for('admin_dashboard'))

# --- Admin Interaction Injectors ---

@app.route('/admin/inject_reactions', methods=['POST'])
def admin_inject_reactions():
    if not session.get('is_admin'): return redirect(url_for('admin_login'))
    
    msg_id = request.form.get('message_id')
    reaction_type = request.form.get('reaction_type')
    count = int(request.form.get('count', 1))
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    # Check if reaction exists
    c.execute("SELECT id, count FROM lounge_message_reactions WHERE message_id = %s AND reaction_type = %s", (msg_id, reaction_type))
    row = c.fetchone()
    if row:
        c.execute("UPDATE lounge_message_reactions SET count = count + %s WHERE id = %s", (count, row[0]))
    else:
        c.execute("INSERT INTO lounge_message_reactions (message_id, reaction_type, count) VALUES (%s, %s, %s)", (msg_id, reaction_type, count))
    
    conn.commit()
    conn.close()
    log_admin_action('inject_reactions', target_type='message', target_id=msg_id, details=f"Injected {count} {reaction_type} reactions")
    flash(f"Successfully injected {count} {reaction_type} reactions.")
    return redirect(request.referrer or url_for('admin_dashboard'))

@app.route('/admin/create_poll', methods=['POST'])
def admin_create_poll():
    if not session.get('is_admin'): return redirect(url_for('admin_login'))
    
    question = request.form.get('question')
    options = request.form.get('options') # Comma separated
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    if db_type == 'postgres':
        c.execute("INSERT INTO lounge_polls (question, options) VALUES (%s, %s) RETURNING id", (question, options))
        poll_id = c.fetchone()[0]
    else:
        c.execute("INSERT INTO lounge_polls (question, options) VALUES (%s, %s)", (question, options))
        poll_id = c.cursor.lastrowid
    conn.commit()
    conn.close()
    
    log_admin_action('create_poll', target_type='poll', target_id=poll_id, details=f"Question: {question}")
    flash("Poll created successfully.")
    return redirect(request.referrer or url_for('admin_dashboard'))

@app.route('/admin/delete_poll/<int:poll_id>', methods=['POST'])
def admin_delete_poll(poll_id):
    if not session.get('is_admin'): return redirect(url_for('admin_login'))

    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("DELETE FROM lounge_polls WHERE id = %s", (poll_id,))
    c.execute("DELETE FROM lounge_poll_votes WHERE poll_id = %s", (poll_id,))
    conn.commit()
    conn.close()
    
    log_admin_action('delete_poll', target_type='poll', target_id=poll_id)
    flash("Poll deleted.")
    return redirect(request.referrer or url_for('admin_dashboard'))

@app.route('/admin/inject_votes', methods=['POST'])
def admin_inject_votes():
    if not session.get('is_admin'): return redirect(url_for('admin_login'))
    
    poll_id = request.form.get('poll_id')
    option_index = int(request.form.get('option_index'))
    count = int(request.form.get('count', 1))
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    for _ in range(count):
        c.execute("INSERT INTO lounge_poll_votes (poll_id, option_index, member_id, is_injected) VALUES (%s, %s, 0, 1)", (poll_id, option_index))
    conn.commit()
    conn.close()
    
    log_admin_action('inject_votes', target_type='poll', target_id=poll_id, details=f"Injected {count} votes for option {option_index}")
    flash(f"Injected {count} votes.")
    return redirect(request.referrer or url_for('admin_dashboard'))

@app.route('/admin/close_poll/<int:poll_id>')
def admin_close_poll(poll_id):
    if not session.get('is_admin'): return redirect(url_for('admin_login'))
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("UPDATE lounge_polls SET is_closed = 1 WHERE id = %s", (poll_id,))
    conn.commit()
    conn.close()
    
    log_admin_action('close_poll', target_type='poll', target_id=poll_id)
    flash("Poll closed.")
    return redirect(request.referrer or url_for('admin_dashboard'))

@app.route('/api/lounge/vote', methods=['POST'])
def api_vote_poll():
    if not session.get('member_id'):
        return jsonify({"error": "Unauthorized"}), 401
        
    data = request.get_json()
    poll_id = data.get('poll_id')
    option_index = data.get('option_index')
    member_id = session.get('member_id')
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    # Check if user already voted
    c.execute("SELECT id FROM lounge_poll_votes WHERE poll_id = %s AND member_id = %s", (poll_id, member_id))
    existing_vote = c.fetchone()
    if existing_vote:
        conn.close()
        return jsonify({"error": "You have already cast your vote."}), 403
    else:
        # Insert new vote
        c.execute("INSERT INTO lounge_poll_votes (poll_id, option_index, member_id) VALUES (%s, %s, %s)", (poll_id, option_index, member_id))
    
    conn.commit()
    conn.close()
    return jsonify({"status": "success"}), 200

@app.route('/admin/notifications')
def admin_notifications_center():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    c.execute("""
        SELECT an.*, m.username 
        FROM admin_notifications an
        JOIN members m ON an.member_id = m.id
        ORDER BY an.created_at DESC
    """)
    notifications = c.fetchall()
    conn.close()
    
    return render_template('admin_notifications.html', notifications=notifications)


@app.route('/admin/notifications/action/<int:n_id>')
def admin_notification_action(n_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("SELECT target_url FROM admin_notifications WHERE id = %s", (n_id,))
    row = c.fetchone()
    
    if row:
        target_url = row[0]
        c.execute("UPDATE admin_notifications SET is_read = TRUE WHERE id = %s", (n_id,))
        conn.commit()
        conn.close()
        if target_url and target_url.startswith('/'):
            return redirect(target_url)
    
    conn.close()
    return redirect(url_for('admin_notifications_center'))

@app.route('/admin/vip_fields/add', methods=['POST'])
def admin_add_vip_field():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    label = request.form.get('label')
    target_country = request.form.get('target_country', 'Global')
    
    if label:
        conn, db_type = get_db_connection()
        c = get_cursor(conn, db_type)
        c.execute("INSERT INTO vip_verification_fields (label, field_type, target_country) VALUES (%s, %s, %s)",
                  (label, 'combined', target_country))
        conn.commit()
        conn.close()
    
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/vip_fields/delete/<int:field_id>', methods=['POST'])
def admin_delete_vip_field(field_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("DELETE FROM vip_verification_fields WHERE id = %s", (field_id,))
    conn.commit()
    conn.close()
    
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/slideshow/add', methods=['POST'])
def admin_add_slideshow():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
        
    image = request.files.get('image')
    info_text = request.form.get('info_text', '').strip()
    
    if not image or image.filename == '':
        flash("Please select an image file to upload.", "error")
        return redirect(url_for('admin_dashboard', section='slideshows'))
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    try:
        filename = secure_filename(image.filename)
        filename = f"slide_{int(time.time())}_{filename}"
        db_path = save_uploaded_file(image, custom_filename=filename)
        
        if not db_path:
            raise ValueError("File could not be saved — upload returned no path.")
        
        c.execute("INSERT INTO club_slideshows (image_path, info_text, is_active) VALUES (%s, %s, TRUE)",
                  (db_path, info_text))
        conn.commit()
        flash("Broadcast slide added successfully.", "success")
    except Exception as e:
        conn.rollback()
        print(f"[admin_add_slideshow] Error: {e}")
        flash(f"Upload failed: {e}", "error")
    finally:
        conn.close()
    
    return redirect(url_for('admin_dashboard', section='slideshows'))

@app.route('/admin/slides/delete/<int:slide_id>', methods=['POST'])
def admin_delete_slide(slide_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
        
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("DELETE FROM club_slideshows WHERE id = %s", (slide_id,))
    conn.commit()
    conn.close()
    
    flash("Slideshow entry deleted.")
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/slides/edit/<int:slide_id>', methods=['GET', 'POST'])
def admin_edit_slide(slide_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
        
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    if request.method == 'POST':
        info_text = request.form.get('info_text', '').strip()
        image = request.files.get('image')
        
        try:
            if image and image.filename != '':
                filename = secure_filename(image.filename)
                filename = f"slide_{int(time.time())}_{filename}"
                db_path = save_uploaded_file(image, custom_filename=filename)
                if not db_path:
                    raise ValueError("File could not be saved — upload returned no path.")
                c.execute("UPDATE club_slideshows SET image_path = %s, info_text = %s, is_active = TRUE WHERE id = %s",
                          (db_path, info_text, slide_id))
            else:
                c.execute("UPDATE club_slideshows SET info_text = %s, is_active = TRUE WHERE id = %s",
                          (info_text, slide_id))
                
            conn.commit()
            flash("Broadcast slide updated.", "success")
        except Exception as e:
            conn.rollback()
            print(f"[admin_edit_slide] Error: {e}")
            flash(f"Update failed: {e}", "error")
        finally:
            conn.close()
        return redirect(url_for('admin_dashboard', section='slideshows'))
    
    c.execute("SELECT * FROM club_slideshows WHERE id = %s", (slide_id,))
    slide = c.fetchone()
    conn.close()
    
    if not slide:
        return "Slide not found."
        
    return render_template('edit_slide.html', slide=slide)

@app.route('/vip_verification/<int:plan_id>')
def vip_verification(plan_id):
    if not session.get('member_id'):
        return redirect(url_for('member_login'))
    
    user_id = session['member_id']
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    # Fetch plan details
    c.execute("SELECT plan_name FROM subscription_plans WHERE id = %s", (plan_id,))
    plan_row = c.fetchone()
    plan_name = plan_row[0] if plan_row else "Premium Plan"

    # Fetch user's country
    c.execute("SELECT country FROM members WHERE id = %s", (user_id,))
    country_row = c.fetchone()
    country = country_row[0] if country_row else 'DEFAULT'
    if not country: country = 'DEFAULT'
    else: country = country.upper()
    
    # Fetch onboarding config for this plan + country
    c.execute("SELECT id, welcome_message FROM onboarding_configs WHERE plan_id = %s AND country = %s", (plan_id, country))
    config = c.fetchone()
    
    # Fallback to DEFAULT if not found
    if not config:
        c.execute("SELECT id, welcome_message FROM onboarding_configs WHERE plan_id = %s AND country = 'DEFAULT'", (plan_id,))
        config = c.fetchone()
    
    # Fetch fields from the global Verification Logic system
    # Priority: Country-specific fields > Global fields
    c.execute("SELECT label, field_type, id FROM vip_verification_fields WHERE target_country = %s OR target_country = 'Global' ORDER BY target_country DESC", (country,))
    rows = c.fetchall()
    
    fields = []
    for row in rows:
        fields.append({'field_name': row[0], 'field_type': row[1], 'id': row[2]})
    
    if not fields:
        # Final fallback
        welcome_msg = "Hello! Please submit your verification details to begin our private consultation."
        fields = [{'field_name': 'Proof of Status', 'field_type': 'file', 'id': 0}]
    
    # Use the welcome message from the config if available
    if config:
        welcome_msg = config[1]
    else:
        welcome_msg = "Hello! I'm your dedicated onboarding assistant. Please submit your verification details to begin our private consultation."
    
    # Fetch chat history
    c.execute("SELECT * FROM vip_pre_payment_chats WHERE member_id = %s ORDER BY timestamp ASC", (user_id,))
    chats = c.fetchall()

    # Fetch Crypto Wallets and System Settings for dynamic payment display
    c.execute("SELECT * FROM crypto_wallets WHERE is_active = TRUE")
    crypto_wallets = c.fetchall()
    
    c.execute("SELECT setting_key, setting_value FROM site_settings")
    settings = {row[0]: row[1] for row in c.fetchall()}
        
    c.execute("SELECT id FROM vip_submissions WHERE user_id = %s ORDER BY created_at DESC LIMIT 1", (user_id,))
    sub_row = c.fetchone()
    submission_id = sub_row[0] if sub_row else None

    # Notify Admin that user is in the concierge
    if not session.get('is_admin'):
        c.execute("SELECT fullname FROM members WHERE id = %s", (user_id,))
        m_row = c.fetchone()
        m_name = m_row[0] if m_row else "Unknown Member"
        target = url_for('admin_vip_review', sub_id=submission_id) if submission_id else url_for('admin_dashboard') + "?section=finance"
        add_admin_notification(user_id, 'Concierge Active', f"Member {m_name} is currently in the payment concierge for {plan_name}.", target)

    conn.close()
    return render_template('vip_verification.html', 
                           fields=fields, 
                           plan_id=plan_id, 
                           plan_name=plan_name,
                           chats=chats, 
                           welcome_msg=welcome_msg, 
                           submission_id=submission_id,
                           crypto_wallets=crypto_wallets,
                           settings=settings)

@app.route('/admin/kyc_config', methods=['GET'])
def admin_kyc_config():
    if not session.get('is_admin'): return redirect(url_for('admin_login'))
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("SELECT * FROM kyc_configs ORDER BY id DESC")
    configs = c.fetchall()
    conn.close()
    return render_template('admin_kyc_config.html', configs=configs)

@app.route('/admin/kyc_config/add', methods=['POST'])
def admin_add_kyc_config():
    if not session.get('is_admin'): return "Unauthorized", 403
    country = request.form.get('country')
    documents_required = request.form.get('documents_required')
    external_link = request.form.get('external_link')
    post_info_required = request.form.get('post_info_required')
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("INSERT INTO kyc_configs (country, documents_required, external_link, post_info_required) VALUES (%s, %s, %s, %s)",
              (country, documents_required, external_link, post_info_required))
    conn.commit()
    conn.close()
    flash("New KYC Rule added successfully.", "success")
    return redirect(url_for('admin_kyc_config'))

@app.route('/admin/kyc_config/delete/<int:config_id>', methods=['POST'])
def admin_delete_kyc_config(config_id):
    if not session.get('is_admin'): return "Unauthorized", 403
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("DELETE FROM kyc_configs WHERE id = %s", (config_id,))
    conn.commit()
    conn.close()
    flash("KYC Rule removed.", "success")
    return redirect(url_for('admin_kyc_config'))

@app.route('/admin/kyc_submissions', methods=['GET'])
def admin_kyc_submissions():
    if not session.get('is_admin'): return redirect(url_for('admin_login'))
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    # Fetch submissions with member details
    query = """
        SELECT ks.id as sub_id, ks.*, m.fullname, m.username, m.email, m.country
        FROM kyc_submissions ks
        JOIN members m ON ks.user_id = m.id
        ORDER BY ks.created_at DESC
    """
    c.execute(query)
    submissions = c.fetchall()
    conn.close()
    return render_template('admin_kyc_submissions.html', submissions=submissions)

@app.route('/admin/kyc/approve/<int:sub_id>', methods=['POST'])
def admin_kyc_approve(sub_id):
    if not session.get('is_admin'): return "Unauthorized", 403
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    c.execute("SELECT user_id FROM kyc_submissions WHERE id = %s", (sub_id,))
    sub = c.fetchone()
    if sub:
        user_id = sub[0]
        c.execute("UPDATE members SET kyc_status = 'Verified' WHERE id = %s", (user_id,))
        c.execute("UPDATE kyc_submissions SET status = 'Approved' WHERE id = %s", (sub_id,))
        conn.commit()
        flash(f"KYC Application #{sub_id} approved.", "success")
    
    conn.close()
    return redirect(url_for('admin_kyc_submissions'))

@app.route('/admin/kyc/reject/<int:sub_id>', methods=['POST'])
def admin_kyc_reject(sub_id):
    if not session.get('is_admin'): return "Unauthorized", 403
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    c.execute("SELECT user_id FROM kyc_submissions WHERE id = %s", (sub_id,))
    sub = c.fetchone()
    if sub:
        user_id = sub[0]
        c.execute("UPDATE members SET kyc_status = 'Rejected' WHERE id = %s", (user_id,))
        c.execute("UPDATE kyc_submissions SET status = 'Rejected' WHERE id = %s", (sub_id,))
        conn.commit()
        flash(f"KYC Application #{sub_id} rejected.", "error")
    
    conn.close()
    return redirect(url_for('admin_kyc_submissions'))

@app.route('/admin/crypto_wallets/add', methods=['POST'])
def admin_add_crypto_wallet():
    if not session.get('is_admin'): return "Unauthorized", 403
    currency = request.form.get('currency')
    network = request.form.get('network')
    address = request.form.get('address')
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("INSERT INTO crypto_wallets (currency, network, address) VALUES (%s, %s, %s)", (currency, network, address))
    conn.commit()
    conn.close()
    flash("New crypto wallet added successfully.")
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/crypto_wallets/delete/<int:wallet_id>', methods=['POST'])
def admin_delete_crypto_wallet(wallet_id):
    if not session.get('is_admin'): return "Unauthorized", 403
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("DELETE FROM crypto_wallets WHERE id = %s", (wallet_id,))
    conn.commit()
    conn.close()
    flash("Crypto wallet removed.")
    return redirect(url_for('admin_dashboard'))

@app.route('/submit_vip_verification', methods=['POST'])
def submit_vip_verification():
    if not session.get('member_id'):
        return redirect(url_for('member_login'))
    
    user_id = session['member_id']
    plan_id = request.form.get('plan_id')
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    # Get user country for field lookup
    c.execute("SELECT country FROM members WHERE id = %s", (user_id,))
    country = c.fetchone()[0]
    if not country: country = 'DEFAULT'
    else: country = country.upper()
    
    # Get payment details from form
    payment_method = request.form.get('payment_method')
    transaction_hash = request.form.get('transaction_hash')
    wire_reference = request.form.get('wire_reference')
    giftcard_code = request.form.get('giftcard_code')

    # Create submission
    if db_type == 'postgres':
        c.execute("""
            INSERT INTO vip_submissions (user_id, plan_id, payment_method, transaction_hash, wire_reference, giftcard_code) 
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
        """, (user_id, plan_id, payment_method, transaction_hash, wire_reference, giftcard_code))
        submission_id = c.fetchone()[0]
    else:
        c.execute("""
            INSERT INTO vip_submissions (user_id, plan_id, payment_method, transaction_hash, wire_reference, giftcard_code) 
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, plan_id, payment_method, transaction_hash, wire_reference, giftcard_code))
        submission_id = c.lastrowid

    # Find the config used
    c.execute("SELECT id FROM onboarding_configs WHERE plan_id = %s AND country = %s", (plan_id, country))
    config = c.fetchone()
    if not config:
        c.execute("SELECT id FROM onboarding_configs WHERE plan_id = %s AND country = 'DEFAULT'", (plan_id,))
        config = c.fetchone()
    
    if config:
        config_id = config[0]
    # Identify which fields we are expecting based on user country
    c.execute("SELECT id FROM vip_verification_fields WHERE target_country = %s OR target_country = 'Global'", (country,))
    field_rows = c.fetchall()
    
    for (field_id,) in field_rows:
        text_resp = request.form.get(f'text_input_{field_id}')
        uploaded_files = request.files.getlist(f'files_{field_id}')
        saved_paths = []
        for file in uploaded_files:
            if file and file.filename:
                fname = f"vip_evid_{submission_id}_{uuid.uuid4().hex}_{secure_filename(file.filename)}"
                fpath = os.path.join(app.config['UPLOAD_FOLDER'], fname)
                file.save(fpath)
                saved_paths.append(f"/static/uploads/{fname}")
        
        # Also check for the general 'payment_evidence' field
        general_files = request.files.getlist('payment_evidence')
        for file in general_files:
            if file and file.filename:
                fname = f"vip_pay_{submission_id}_{uuid.uuid4().hex}_{secure_filename(file.filename)}"
                fpath = os.path.join(app.config['UPLOAD_FOLDER'], fname)
                file.save(fpath)
                saved_paths.append(f"/static/uploads/{fname}")
        
        if text_resp or saved_paths:
            c.execute("INSERT INTO vip_submission_data (submission_id, field_id, text_response, file_paths) VALUES (%s, %s, %s, %s)",
                      (submission_id, field_id, text_resp, ",".join(saved_paths)))

    conn.commit()
    conn.close()
    
    add_admin_notification(user_id, 'VIP Verification', f"VIP Verification details submitted by {session.get('member_fullname')}.", url_for('admin_vip_requests'))
    
    flash("Your VIP verification details have been received. Please use the live chat for further instructions.")
    return redirect(url_for('vip_verification', plan_id=plan_id))

@app.route('/admin/vip_requests')
def admin_vip_requests():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("""
        SELECT vs.id, m.username, m.country, vs.status, vs.created_at 
        FROM vip_submissions vs
        JOIN members m ON vs.user_id = m.id
        ORDER BY vs.created_at DESC
    """)
    requests = c.fetchall()
    conn.close()
    return render_template('admin_vip_requests.html', requests=requests)

@app.route('/admin/vip_requests/<int:sub_id>')
def admin_vip_review(sub_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    # Submission details
    c.execute("""
        SELECT vs.*, m.username, m.fullname, m.email, m.country 
        FROM vip_submissions vs
        JOIN members m ON vs.user_id = m.id
        WHERE vs.id = %s
    """, (sub_id,))
    submission = c.fetchone()
    
    # Submission data with labels
    c.execute("""
        SELECT vsd.*, vf.label 
        FROM vip_submission_data vsd
        JOIN vip_verification_fields vf ON vsd.field_id = vf.id
        WHERE vsd.submission_id = %s
    """, (sub_id,))
    data = c.fetchall()
    
    # Chat history - fetch by member_id since it's user-specific concierge
    c.execute("SELECT * FROM vip_pre_payment_chats WHERE member_id = %s ORDER BY timestamp ASC", (submission['user_id'],))
    chats = c.fetchall()
    conn.close()
    
    return render_template('admin_vip_review.html', submission=submission, data=data, chats=chats)

@app.route('/vip_chat/send/<int:member_id>', methods=['POST'])
def vip_chat_send(member_id):
    is_admin = session.get('is_admin') == True
    user_id = session.get('member_id')
    
    if not is_admin and not user_id:
        return redirect(url_for('member_login'))
    
    message = request.form.get('message')
    chat_media = request.files.get('chat_media')
    sender_id = 0 if is_admin else user_id # 0 for admin
    
    if message or chat_media:
        conn, db_type = get_db_connection()
        c = get_cursor(conn, db_type)
        
        media_path = None
        if chat_media and chat_media.filename != '':
            filename = secure_filename(chat_media.filename)
            filename = f"chat_{member_id}_{uuid.uuid4().hex}_{filename}"
            media_path = save_uploaded_file(chat_media, custom_filename=filename)

        c.execute("INSERT INTO vip_pre_payment_chats (member_id, sender_id, message, media_path) VALUES (%s, %s, %s, %s)",
                  (member_id, sender_id, message, media_path))
        
        # Notify Admin if it's a member message
        if not is_admin:
            c.execute("SELECT fullname FROM members WHERE id = %s", (member_id,))
            m_name = c.fetchone()[0]
            # Fetch the latest submission ID to provide a direct link
            c.execute("SELECT id FROM vip_submissions WHERE user_id = %s ORDER BY created_at DESC LIMIT 1", (member_id,))
            sub_row = c.fetchone()
            target = url_for('admin_vip_review', sub_id=sub_row[0]) if sub_row else url_for('admin_dashboard') + "?section=finance"
            
            add_admin_notification(member_id, 'Concierge Chat', f"New payment concierge message from {m_name}.", target)

        conn.commit()
        conn.close()

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.headers.get('Accept') == 'application/json' or request.args.get('ajax') == '1':
        return jsonify({"status": "success"})

    if is_admin:
        # Redirect back to the page the admin was on (Vault or Review)
        return redirect(request.referrer or url_for('admin_dashboard'))
    
    # Phase 3: redirect member back to dashboard (vip_verification requires plan_id — unsafe to assume)
    return redirect(request.referrer or url_for('member_dashboard'))



@app.route('/admin/settings', methods=['POST'])
def admin_settings():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    # Update existing plans
    plan_ids = request.form.getlist('plan_id')
    plan_names = request.form.getlist('plan_name')
    plan_prices = request.form.getlist('plan_price')
    plan_features = request.form.getlist('plan_features')
    plan_durations = request.form.getlist('billing_period')
    
    # Phase 4: use zip() with strict length checking to prevent IndexError
    # when form lists are different lengths due to partial submissions
    plan_tuples = list(zip(plan_ids, plan_names, plan_prices, plan_features, plan_durations))
    for p_id, p_name, p_price, p_feat, p_dur in plan_tuples:
        try:
            c.execute("UPDATE subscription_plans SET plan_name = %s, price = %s, features = %s, billing_period = %s WHERE id = %s",
                      (p_name, float(p_price), p_feat, p_dur, int(p_id)))
        except (ValueError, TypeError) as plan_err:
            print(f"Plan update skipped (invalid data): {plan_err}")
            continue
    
    # Add new plan if provided
    new_name = request.form.get('new_plan_name')
    new_price = request.form.get('new_plan_price')
    new_features = request.form.get('new_plan_features')
    new_duration = request.form.get('new_billing_period', 'Per Executive Year')
    if new_name and new_price:
        c.execute("SELECT id FROM subscription_plans WHERE plan_name = %s", (new_name,))
        if not c.fetchone():
            c.execute("INSERT INTO subscription_plans (plan_name, price, features, billing_period) VALUES (%s, %s, %s, %s)",
                      (new_name, float(new_price), new_features if new_features else "", new_duration))
    
    conn.commit()
    conn.close()
    flash("Platform settings updated successfully.")
    return redirect(url_for('admin_dashboard', section='plans'))

@app.route('/admin/plans/delete/<int:plan_id>', methods=['POST'])
def admin_delete_plan(plan_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("DELETE FROM subscription_plans WHERE id = %s", (plan_id,))
    conn.commit()
    conn.close()
    
    flash("Subscription plan deleted successfully.")
    return redirect(url_for('admin_dashboard', section='plans'))

# ─────────────────────────────────────────────
#  INVITE LINK MANAGEMENT
# ─────────────────────────────────────────────
@app.route('/admin/invite/generate', methods=['POST'])
def admin_generate_invite():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    import secrets, string
    note = request.form.get('invite_note', '').strip()
    
    # Generate 6-char alphanumeric code (excluding ambiguous chars if needed, but standard is fine)
    alphabet = string.ascii_uppercase + string.digits
    token = ''.join(secrets.choice(alphabet) for i in range(6))
    
    # Set expiry to 20 minutes as requested
    expires = datetime.datetime.utcnow() + datetime.timedelta(minutes=20)
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    # Ensure uniqueness (rare collision with 6 chars, but good practice)
    c.execute("SELECT id FROM invite_tokens WHERE token = %s", (token,))
    if c.fetchone():
        token = ''.join(secrets.choice(alphabet) for i in range(6))
        
    c.execute("INSERT INTO invite_tokens (token, note, expires_at) VALUES (%s, %s, %s)", (token, note, expires))
    conn.commit()
    conn.close()
    
    flash(f"Invite Code Generated: {token}. Valid for 20 minutes. Link: {request.host_url}register?invite={token}")
    return redirect(url_for('admin_dashboard', section='invites'))

@app.route('/admin/invite/revoke/<int:token_id>', methods=['POST'])
def admin_revoke_invite(token_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("UPDATE invite_tokens SET is_used = TRUE WHERE id = %s", (token_id,))
    conn.commit()
    conn.close()
    flash("Invite link revoked.")
    return redirect(url_for('admin_dashboard', section='invites'))

@app.route('/admin/onboarding')
def admin_onboarding():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("SELECT id, plan_name FROM subscription_plans")
    plans = c.fetchall()
    
    # Fetch existing configs and their fields
    c.execute("""
        SELECT oc.id, oc.plan_id, sp.plan_name, oc.country, oc.welcome_message 
        FROM onboarding_configs oc
        JOIN subscription_plans sp ON oc.plan_id = sp.id
        ORDER BY oc.plan_id, oc.country
    """)
    configs = []
    for row in c.fetchall():
        config_id = row[0]
        c.execute("SELECT field_name, field_type FROM onboarding_fields WHERE config_id = %s", (config_id,))
        fields = c.fetchall()
        configs.append({
            'id': row[0],
            'plan_id': row[1],
            'plan_name': row[2],
            'country': row[3],
            'welcome_message': row[4],
            'fields': fields
        })
    conn.close()
    return render_template('admin_onboarding.html', plans=plans, configs=configs)

@app.route('/admin/onboarding/save', methods=['POST'])
def admin_onboarding_save():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    plan_id = request.form.get('plan_id')
    country = request.form.get('country', 'DEFAULT').strip().upper()
    if not country: country = 'DEFAULT'
    welcome_message = request.form.get('welcome_message')
    field_names = request.form.getlist('field_name[]')
    field_types = request.form.getlist('field_type[]')
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    # Check if config exists for this plan + country
    c.execute("SELECT id FROM onboarding_configs WHERE plan_id = %s AND country = %s", (plan_id, country))
    existing = c.fetchone()
    
    if existing:
        config_id = existing[0]
        c.execute("UPDATE onboarding_configs SET welcome_message = %s WHERE id = %s", (welcome_message, config_id))
        # Clear old fields
        c.execute("DELETE FROM onboarding_fields WHERE config_id = %s", (config_id,))
    else:
        c.execute("INSERT INTO onboarding_configs (plan_id, country, welcome_message) VALUES (%s, %s, %s)", 
                  (plan_id, country, welcome_message))
        c.execute("SELECT id FROM onboarding_configs WHERE plan_id = %s AND country = %s", (plan_id, country))
        config_id = c.fetchone()[0]
        
    for name, type_ in zip(field_names, field_types):
        if name.strip():
            c.execute("INSERT INTO onboarding_fields (config_id, field_name, field_type) VALUES (%s, %s, %s)", 
                      (config_id, name, type_))
            
    conn.commit()
    conn.close()
    flash("Onboarding configuration saved.")
    return redirect(url_for('admin_onboarding'))

@app.route('/admin/onboarding/delete/<int:config_id>', methods=['POST'])
def admin_onboarding_delete(config_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("DELETE FROM onboarding_fields WHERE config_id = %s", (config_id,))
    c.execute("DELETE FROM onboarding_configs WHERE id = %s", (config_id,))
    conn.commit()
    conn.close()
    flash("Configuration deleted.")
    return redirect(url_for('admin_onboarding'))

@app.route('/admin/reset/<int:member_id>', methods=['POST'])
def admin_reset_password(member_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
        
    import string, secrets
    new_password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))
    hashed_password = generate_password_hash(new_password)
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
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
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
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
#            send_email_notification(m_row[0], subj, body, user_id=m_row[2])
        else:
            # Fallback to legacy
            subj, body = get_templated_email('Subscription_Success', m_row[1])
            if subj:
                pass
#                send_email_notification(m_row[0], subj, body, user_id=m_row[2])
            
    conn.commit()
    conn.close()
    
    if m_row:
        add_member_notification(m_row[2], 'Contribution Approved', "Your contribution/donation has been approved by the admin.", url_for('member_dashboard'))
    
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/reply/<int:member_id>', methods=['GET', 'POST'])
def admin_reply_member(member_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
        
    if request.method == 'POST':
        admin_reply_text = request.form.get('admin_reply_text')
        media_files = request.files.getlist('admin_media')
        
        conn, db_type = get_db_connection()
        c = get_cursor(conn, db_type)
        
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
                    pass
#                    send_email_notification(m_row[0], subj, body, user_id=m_row[2])
            
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
        
        add_member_notification(member_id, 'Admin Reply', f"Administrator has replied to your ticket.", url_for('member_ticket_thread', ticket_id=ticket_id))
        
        return redirect(url_for('admin_dashboard'))
        
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
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
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("UPDATE members SET membership_tier = 'Awaiting Payment', vip_admin_reply = %s WHERE id = %s", (instruction_text, member_id))
    conn.commit()
    conn.close()
    
    add_member_notification(member_id, 'Payment Instructions', "Administrator has sent you payment instructions for VIP upgrade.", url_for('member_dashboard'))
    
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/finalize_vip/<int:member_id>', methods=['POST'])
def admin_finalize_vip(member_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
        
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("UPDATE members SET membership_tier = 'VIP', vip_since = CURRENT_TIMESTAMP WHERE id = %s", (member_id,))
    # Start a new VIP period
    c.execute("INSERT INTO vip_periods (user_id, start_time) VALUES (%s, CURRENT_TIMESTAMP)", (member_id,))
    
    # Send VIP Welcome Email
    c.execute("SELECT email, fullname, id FROM members WHERE id = %s", (member_id,))
    m_row = c.fetchone()
    if m_row:
        subj, body = get_templated_email('VIP_Welcome', m_row[1])
        if subj:
            pass
#            send_email_notification(m_row[0], subj, body, user_id=m_row[2])
            
    conn.commit()
    conn.close()
    
    add_member_notification(member_id, 'VIP Approved', "Congratulations! Your VIP membership has been approved.", url_for('vip_lounge'))
    
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/demote/<int:member_id>', methods=['POST'])
def admin_demote_member(member_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
        
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("UPDATE members SET membership_tier = 'Regular' WHERE id = %s", (member_id,))
    # Close the active VIP period
    c.execute("UPDATE vip_periods SET end_time = CURRENT_TIMESTAMP WHERE user_id = %s AND end_time IS NULL", (member_id,))
    
    # Notify user of removal
    c.execute("SELECT email, fullname FROM members WHERE id = %s", (member_id,))
    user_row = c.fetchone()
    if user_row:
        subj, body = get_templated_email('VIP_Removal', user_row[1])
        if subj:
            pass
#            send_email_notification(user_row[0], subj, body, user_id=member_id)
            
    conn.commit()
    conn.close()
    
    add_member_notification(member_id, 'Membership Update', "Your VIP status has been removed. You are now a Regular member.", url_for('member_dashboard'))
    
    flash(f"User {user_row[1] if user_row else ''} demoted and notified.")
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/manual_vip/<int:member_id>', methods=['POST'])
def admin_manual_vip(member_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
        
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    # Directly update membership tier first
    c.execute("UPDATE members SET membership_tier = 'VIP', vip_since = CURRENT_TIMESTAMP WHERE id = %s", (member_id,))
    
    # Only attempt to approve submission if it exists
    c.execute("SELECT id FROM vip_submissions WHERE user_id = %s", (member_id,))
    sub_check = c.fetchone()
    if sub_check:
        c.execute("UPDATE vip_submissions SET status = 'approved' WHERE id = %s", (sub_check['id'],))
        
    # Start a new VIP period
    c.execute("INSERT INTO vip_periods (user_id, start_time) VALUES (%s, CURRENT_TIMESTAMP)", (member_id,))
    
    # Send VIP Welcome Email
    c.execute("SELECT email, fullname, id FROM members WHERE id = %s", (member_id,))
    m_row = c.fetchone()
    if m_row:
        try:
            safe_name = m_row['fullname'] if m_row['fullname'] else "VIP Member"
            subj, body = get_templated_email('VIP_Welcome', safe_name)
            if subj:
                pass
#                send_email_notification(m_row['email'], subj, body, user_id=m_row['id'])
        except Exception as e:
            print(f"Error sending VIP Welcome email: {e}")
            
    conn.commit()
    conn.close()
    
    flash("Manual VIP Override successful.")
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/toggle_vip/<int:user_id>', methods=['POST'])
def admin_toggle_vip(user_id):
    try:
        if not session.get('is_admin'): return redirect(url_for('admin_login'))
        conn, db_type = get_db_connection()
        c = get_cursor(conn, db_type)
        c.execute("SELECT membership_tier, email, fullname FROM members WHERE id = %s", (user_id,))
        user = c.fetchone()
        if not user:
            conn.close()
            return "User not found", 404
        
        current_tier = user['membership_tier']
        email = user['email']
        fullname = user['fullname']
        new_tier = 'VIP' if current_tier != 'VIP' else 'Regular'
        
        if new_tier == 'VIP':
            log_admin_action('grant_vip', target_type='member', target_id=user_id)
            c.execute("UPDATE members SET membership_tier = 'VIP', vip_since = CURRENT_TIMESTAMP WHERE id = %s", (user_id,))
            
            # Only attempt to approve submission if it exists
            c.execute("SELECT id FROM vip_submissions WHERE user_id = %s", (user_id,))
            sub_check = c.fetchone()
            if sub_check:
                c.execute("UPDATE vip_submissions SET status = 'approved' WHERE id = %s", (sub_check['id'],))
                
            c.execute("INSERT INTO vip_periods (user_id, start_time) VALUES (%s, CURRENT_TIMESTAMP)", (user_id,))
            # Trigger VIP Added email
            c.execute("SELECT subject, body FROM email_templates WHERE trigger_event = 'VIP Added'")
            t = c.fetchone()
            try:
                safe_name = fullname if fullname else "VIP Member"
                if t:
#                    send_email_notification(email, t['subject'].replace('{{name}}', safe_name), t['body'].replace('{{name}}', safe_name), user_id=user_id)
                    pass
                else:
                    subj, body = get_templated_email('VIP_Welcome', safe_name)
#                    if subj: send_email_notification(email, subj, body, user_id=user_id)
                    pass
            except Exception as e:
                print(f"Error sending VIP Add email: {e}")
        else:
            log_admin_action('revoke_vip', target_type='member', target_id=user_id)
            c.execute("UPDATE members SET membership_tier = 'Regular' WHERE id = %s", (user_id,))
            c.execute("UPDATE vip_periods SET end_time = CURRENT_TIMESTAMP WHERE user_id = %s AND end_time IS NULL", (user_id,))
            # Trigger VIP Removed email
            c.execute("SELECT subject, body FROM email_templates WHERE trigger_event = 'VIP Removed'")
            t = c.fetchone()
            try:
                safe_name = fullname if fullname else "VIP Member"
                if t:
#                    send_email_notification(email, t['subject'].replace('{{name}}', safe_name), t['body'].replace('{{name}}', safe_name), user_id=user_id)
                    pass
                else:
                    subj, body = get_templated_email('VIP_Removal', safe_name)
#                    if subj: send_email_notification(email, subj, body, user_id=user_id)
                    pass
            except Exception as e:
                print(f"Error sending VIP Remove email: {e}")

        conn.commit()
        conn.close()
        
        add_member_notification(user_id, 'Membership Update', f"Your membership status has been updated to {new_tier}.", url_for('member_dashboard'))
        
        flash(f"VIP status {'granted' if new_tier == 'VIP' else 'revoked'} for {fullname}.")
        return redirect(request.referrer or url_for('admin_dashboard'))
    except Exception as e:
        import traceback
        return f"<h1>DIAGNOSTIC CRASH REPORT</h1><p><b>Error:</b> {str(e)}</p><pre>{traceback.format_exc()}</pre>", 200

@app.route('/admin/email_settings', methods=['POST'])
def admin_email_settings():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    event_types = request.form.getlist('event_type')
    subjects = request.form.getlist('subject')
    bodies = request.form.getlist('body')
    trigger_events = request.form.getlist('trigger_event')
    plan_ids = request.form.getlist('template_plan_id')
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
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
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("SELECT email, fullname FROM members WHERE id = %s", (member_id,))
    res = c.fetchone()
    conn.close()
    
    if res:
        recipient_email = res[0]
        member_name = res[1]
        
        # Render through manual_email_template.html
        html_body = render_template('manual_email_template.html', name=member_name, custom_message=custom_message)
        
#        if send_email_notification(recipient_email, custom_subject, html_body, user_id=member_id):
#            flash(f"Manual dispatch successful to {recipient_email}.")
#        else:
#            flash("Dispatch failed. Check SMTP settings.")
        flash("Email dispatch skipped (Service Offline).")
    else:
        flash("Member not found.")
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/view_emails/<int:user_id>')
@app.route('/admin/user/<int:user_id>/emails')
def admin_view_user_emails(user_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
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

@app.route('/admin/user/<int:user_id>/add_donation', methods=['POST'])
def admin_add_donation(user_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
        
    amount = request.form.get('amount')
    note = request.form.get('note', 'Manual Contribution')
    
    if not amount:
        flash("Amount is required.")
        return redirect(url_for('admin_user_profile', user_id=user_id))
        
    try:
        amount_float = float(amount)
        if amount_float <= 0:
            flash("Amount must be greater than zero.")
            return redirect(url_for('admin_user_profile', user_id=user_id))
            
        conn, db_type = get_db_connection()
        c = get_cursor(conn, db_type)
        
        # Determine the user's current tier to know which plan_id to attribute this to (or default to 1)
        c.execute("SELECT plan_id FROM vip_submissions WHERE user_id = %s AND status = 'Approved' ORDER BY created_at DESC LIMIT 1", (user_id,))
        plan_res = c.fetchone()
        plan_id = plan_res[0] if plan_res else 1
        
        if db_type == 'postgres':
            c.execute("INSERT INTO donations (member_id, amount, status, visibility_preference) VALUES (%s, %s, 'Completed', %s)",
                      (user_id, amount_float, note))
        else:
            c.execute("INSERT INTO donations (member_id, amount, status, visibility_preference) VALUES (%s, %s, 'Completed', %s)",
                      (user_id, amount_float, note))
                      
        log_admin_action('manual_donation', target_type='member', target_id=user_id, details=f"Added ${amount_float:.2f}: {note}")
        conn.commit()
        conn.close()
        
        flash(f"Successfully injected ${amount_float:.2f} into member's ledger.")
    except Exception as e:
        print(f"Error adding donation: {e}")
        flash("Failed to add contribution due to a system error.")
        
    return redirect(url_for('admin_user_profile', user_id=user_id))

@app.route('/admin/user/<int:user_id>')
def admin_user_profile(user_id):
    if not session.get('is_admin'): return redirect(url_for('admin_login'))
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    # Fetch member details
    c.execute("""
        SELECT * FROM members WHERE id = %s
    """, (user_id,))
    member = c.fetchone()
    
    # Fetch profile update audit logs
    c.execute("""
        SELECT * FROM member_profile_audit 
        WHERE member_id = %s 
        ORDER BY changed_at DESC
    """, (user_id,))
    audit_logs = c.fetchall()
    
    conn.close()
    
    if not member: return "User not found", 404
    return render_template('admin_user_profile.html', member=member, audit_logs=audit_logs)

@app.route('/admin/user/<int:user_id>/toggle_kyc', methods=['POST'])
def admin_toggle_kyc(user_id):
    if not session.get('is_admin'): return redirect(url_for('admin_login'))
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    c.execute("SELECT kyc_status FROM members WHERE id = %s", (user_id,))
    current_status = c.fetchone()[0]
    
    new_status = 'Verified'
    if current_status == 'Verified':
        new_status = 'Unverified'
    elif current_status == 'Unverified':
        new_status = 'Pending'
    elif current_status == 'Pending':
        new_status = 'Verified'
        
    c.execute("UPDATE members SET kyc_status = %s WHERE id = %s", (new_status, user_id))
    log_admin_action('toggle_kyc', target_type='member', target_id=user_id, details=f"Changed KYC status to {new_status}")
    conn.commit()
    conn.close()
    
    flash(f"KYC Status for member updated to {new_status}.")
    return redirect(url_for('admin_user_profile', user_id=user_id))

@app.route('/admin/user/<int:user_id>/disable', methods=['POST'])
def admin_disable_user(user_id):
    if not session.get('is_admin'): return redirect(url_for('admin_login'))
    admin_password = request.form.get('admin_password')
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("SELECT password_hash FROM members WHERE username = 'AdminMaster'")
    admin_row = c.fetchone()
    conn.close()
    
    if admin_row and check_password_hash(admin_row[0], admin_password):
        conn, db_type = get_db_connection()
        c = get_cursor(conn, db_type)
        c.execute("UPDATE members SET is_active = FALSE WHERE id = %s", (user_id,))
        conn.commit()
        conn.close()
        
        add_admin_notification(user_id, 'Account Disability', f"Account for member ID {user_id} has been disabled.", url_for('admin_user_profile', user_id=user_id))
        
        flash("User account disabled.")
    else:
        flash("Invalid admin password.")
    return redirect(url_for('admin_user_profile', user_id=user_id))

@app.route('/admin/user/<int:user_id>/delete', methods=['POST'])
def admin_delete_user(user_id):
    if not session.get('is_admin'): return redirect(url_for('admin_login'))
    admin_password = request.form.get('admin_password')
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("SELECT password_hash FROM members WHERE username = 'AdminMaster'")
    admin_row = c.fetchone()
    conn.close()
    
    if admin_row and check_password_hash(admin_row[0], admin_password):
        conn, db_type = get_db_connection()
        c = get_cursor(conn, db_type)
        try:
            # Delete child records first to avoid FK issues
            c.execute("DELETE FROM donations WHERE member_id = %s", (user_id,))
            c.execute("DELETE FROM tickets WHERE user_id = %s", (user_id,))
            c.execute("DELETE FROM vip_submissions WHERE user_id = %s", (user_id,))
            c.execute("DELETE FROM vip_periods WHERE user_id = %s", (user_id,))
            c.execute("DELETE FROM vip_pre_payment_chats WHERE member_id = %s", (user_id,))
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

@app.route('/admin/user/<int:user_id>/enable', methods=['POST'])
def admin_enable_user(user_id):
    if not session.get('is_admin'): return redirect(url_for('admin_login'))
    admin_password = request.form.get('admin_password')
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("SELECT password_hash FROM members WHERE username = 'AdminMaster'")
    admin_row = c.fetchone()
    conn.close()
    
    if admin_row and check_password_hash(admin_row[0], admin_password):
        conn, db_type = get_db_connection()
        c = get_cursor(conn, db_type)
        # Unlock, activate and reset attempts
        c.execute("UPDATE members SET is_locked = FALSE, is_active = TRUE, failed_attempts = 0 WHERE id = %s", (user_id,))
        conn.commit()
        conn.close()
        
        add_member_notification(user_id, 'Account Reactivated', "Your account has been reactivated by the administrator.", url_for('member_dashboard'))
        
        flash("Account successfully reactivated.")
    else:
        flash("Invalid admin password.")
    return redirect(url_for('admin_user_profile', user_id=user_id))

@app.route('/admin/global_settings', methods=['POST'])
def admin_global_settings():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
        
    form_type = request.form.get('form_type')
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    if form_type == 'update_footer':
        footer_info = request.form.get('footer_info')
        concierge_msg = request.form.get('concierge_welcome_msg')
        member_count = request.form.get('member_count_display', '4,726')
        
        c.execute("UPDATE site_settings SET setting_value = %s WHERE setting_key = 'footer_info'", (footer_info,))
        c.execute("UPDATE site_settings SET setting_value = %s WHERE setting_key = 'concierge_welcome_msg'", (concierge_msg,))
        c.execute("UPDATE site_settings SET setting_value = %s WHERE setting_key = 'member_count_display'", (member_count,))
        conn.commit()
        flash("Global content updated successfully.")
        
    elif form_type == 'update_password':
        new_pass = request.form.get('new_password')
        confirm_pass = request.form.get('confirm_password')
        
        if new_pass == confirm_pass:
            new_hash = generate_password_hash(new_pass)
            c.execute("UPDATE members SET password_hash = %s WHERE username = 'AdminMaster'", (new_hash,))
            conn.commit()
            flash("Admin password updated successfully.")
        else:
            flash("Passwords do not match.")
            
    conn.close()
    return redirect(url_for('admin_dashboard', section='settings'))

@app.route('/admin/appeal/reply/<int:user_id>', methods=['GET', 'POST'])
def admin_appeal_reply(user_id):
    if not session.get('is_admin'): return redirect(url_for('admin_login'))
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    if request.method == 'POST':
        admin_reply = request.form.get('admin_reply')
        file = request.files.get('attachment')
        if admin_reply or file:
            # Find the latest open APPEAL ticket for this user
            c.execute("SELECT id FROM tickets WHERE user_id = %s AND category = 'APPEAL' ORDER BY created_at DESC LIMIT 1", (user_id,))
            ticket = c.fetchone()
            
            if ticket:
                ticket_id = ticket[0]
                if admin_reply:
                    c.execute("UPDATE tickets SET admin_reply = %s, status = 'Replied' WHERE id = %s", (admin_reply, ticket_id))
            else:
                # If no ticket exists, create one proactive appeal resolution
                c.execute("INSERT INTO tickets (user_id, category, message, admin_reply, status) VALUES (%s, 'APPEAL', '[ADMIN INITIATED RESOLUTION]', %s, 'Replied') RETURNING id",
                          (user_id, admin_reply or ''))
                ticket_id = c.fetchone()[0] if db_type == 'postgres' else c.lastrowid

            if file and file.filename:
                filename = secure_filename(f"{uuid.uuid4()}_{file.filename}")
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename).replace('\\', '/')
                file.save(file_path)
                c.execute("INSERT INTO attachments (ticket_id, file_path, uploaded_by_admin) VALUES (%s, %s, %s)",
                          (ticket_id, file_path, True))
            
            conn.commit()
            
            add_member_notification(user_id, 'Appeal Reply', "Administrator has replied to your account appeal.", url_for('member_appeal'))
            
            flash("Resolution sent to member.")
                
    # Fetch member info
    c.execute("SELECT id, fullname, username, is_active FROM members WHERE id = %s", (user_id,))
    member = c.fetchone()
    
    # Fetch appeal history
    c.execute("""
        SELECT t.message, t.admin_reply, t.created_at, a.file_path, a.uploaded_by_admin 
        FROM tickets t
        LEFT JOIN attachments a ON t.id = a.ticket_id
        WHERE t.user_id = %s AND t.category = 'APPEAL' 
        ORDER BY t.created_at ASC
    """, (user_id,))
    chats = c.fetchall()
    conn.close()
    
    return render_template('admin_appeal_reply.html', member=member, chats=chats)

@app.route('/appeal', methods=['GET', 'POST'])
def member_appeal():
    if 'member_id' not in session:
        return redirect(url_for('member_login'))
        
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    if request.method == 'POST':
        message = request.form.get('message')
        file = request.files.get('attachment')
        if message or file:
            # Insert as a ticket with category 'APPEAL'
            c.execute("INSERT INTO tickets (user_id, category, message, status) VALUES (%s, 'APPEAL', %s, 'Open') RETURNING id",
                      (session['member_id'], message or ''))
            ticket_id = c.fetchone()[0] if db_type == 'postgres' else c.lastrowid
            
            if file and file.filename:
                filename = secure_filename(f"{uuid.uuid4()}_{file.filename}")
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename).replace('\\', '/')
                file.save(file_path)
                c.execute("INSERT INTO attachments (ticket_id, file_path, uploaded_by_admin) VALUES (%s, %s, %s)",
                          (ticket_id, file_path, False))
            
            conn.commit()
            
            add_admin_notification(session['member_id'], 'Appeal', f"New account appeal from {session.get('member_fullname')}.", url_for('admin_appeal_reply', user_id=session['member_id']))
            
            flash("Message dispatched to administrator.")
            return redirect(url_for('member_appeal'))
    
    # Fetch all 'APPEAL' tickets for this user
    c.execute("""
        SELECT t.message, t.admin_reply, t.created_at, a.file_path, a.uploaded_by_admin 
        FROM tickets t
        LEFT JOIN attachments a ON t.id = a.ticket_id
        WHERE t.user_id = %s AND t.category = 'APPEAL'
        ORDER BY t.created_at ASC
    """, (session['member_id'],))
    chats = c.fetchall()
    conn.close()
    
    return render_template('appeal.html', chats=chats, fullname=session.get('member_fullname', 'Member'))

@app.route('/verify_identity', methods=['GET', 'POST'])
def member_verify_identity():
    if 'member_id' not in session:
        return redirect(url_for('member_login'))
    
    member_id = session['member_id']
    conn = None
    try:
        conn, db_type = get_db_connection()
        c = get_cursor(conn, db_type)
        c.execute("SELECT fullname, email, is_verified FROM members WHERE id = %s", (member_id,))
        member = c.fetchone()
        
        if not member or member[2]:  # Already verified — use index access
            return redirect(url_for('member_dashboard'))

        if request.method == 'POST':
            user_code = request.form.get('verification_code')
            if user_code and user_code == session.get('member_verification_code'):
                if db_type == 'postgres':
                    c.execute("UPDATE members SET is_verified = TRUE WHERE id = %s", (member_id,))
                else:
                    c.execute("UPDATE members SET is_verified = 1 WHERE id = %s", (member_id,))
                conn.commit()
                session.pop('member_verification_code', None)
                flash("Identity verified. Welcome to the elite circle.", "success")
                return redirect(url_for('member_dashboard'))
            else:
                flash("Invalid verification code. Please check your email.", "error")
        else:
            # Phase 4: Email sending is currently offline.
            # Auto-verify the member so they are not permanently blocked.
            # When SMTP is re-enabled: generate code here, store in session, send via email.
            if db_type == 'postgres':
                c.execute("UPDATE members SET is_verified = TRUE WHERE id = %s", (member_id,))
            else:
                c.execute("UPDATE members SET is_verified = 1 WHERE id = %s", (member_id,))
            conn.commit()
            flash("Your identity has been automatically verified. Welcome to Primer's Zest.", "success")
            return redirect(url_for('member_dashboard'))
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    return render_template('member_verify_identity.html')

@app.route('/logout')
def member_logout():
    session.clear()
    return redirect(url_for('member_login'))

@app.route('/history/<int:ticket_id>', methods=['GET', 'POST'])
def member_ticket_thread(ticket_id):
    if 'member_id' not in session:
        return redirect(url_for('member_login'))
        
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    if request.method == 'POST':
        reply_message = request.form.get('reply_message')
        if reply_message:
            c.execute(
                "INSERT INTO tickets (user_id, category, message, status, parent_id) "
                "SELECT user_id, category, %s, 'Open', %s FROM tickets WHERE id = %s AND user_id = %s",
                (f"[Member Reply] {reply_message}", ticket_id, ticket_id, session['member_id'])
            )
            conn.commit()
            
            add_admin_notification(session['member_id'], 'Ticket Reply', f"Member {session.get('member_fullname')} replied to ticket #{ticket_id}.", url_for('admin_user_vault', member_id=session['member_id']))
            
            flash('Reply sent successfully to admin.')
        return redirect(url_for('member_ticket_thread', ticket_id=ticket_id))
        
def get_threaded_history(member_id, single_ticket_id=None):
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    if single_ticket_id:
        c.execute("""
            SELECT t.id, t.category, t.message, t.status, t.admin_reply, a.file_path, a.uploaded_by_admin, t.created_at, t.parent_id
            FROM tickets t 
            LEFT JOIN attachments a ON t.id = a.ticket_id 
            WHERE t.user_id = %s AND (t.id = %s OR t.parent_id = %s)
            ORDER BY t.created_at ASC
        """, (member_id, single_ticket_id, single_ticket_id))
    else:
        c.execute("""
            SELECT t.id, t.category, t.message, t.status, t.admin_reply, a.file_path, a.uploaded_by_admin, t.created_at, t.parent_id
            FROM tickets t 
            LEFT JOIN attachments a ON t.id = a.ticket_id 
            WHERE t.user_id = %s 
            ORDER BY t.created_at ASC
        """, (member_id,))
        
    rows = c.fetchall()
    conn.close()
    
    tickets_dict = {}
    child_replies = []
    
    for row in rows:
        t_id = row[0]
        parent_id = row[8]
        raw_msg = row[2] or ""
        
        clean_msg = raw_msg
        if clean_msg.startswith("[Member Reply] "):
            clean_msg = clean_msg[len("[Member Reply] "):]
            
        msg_parts = []
        if "--- User Reply ---" in clean_msg:
            parts = clean_msg.split("--- User Reply ---")
            for p in parts:
                p_str = p.strip()
                if p_str:
                    msg_parts.append({
                        'sender': 'Member',
                        'text': p_str,
                        'created_at': row[7]
                    })
        else:
            msg_parts.append({
                'sender': 'Member',
                'text': clean_msg,
                'created_at': row[7]
            })
            
        if row[4]:
            msg_parts.append({
                'sender': 'Concierge',
                'text': row[4],
                'created_at': row[7]
            })
            
        if not parent_id and raw_msg.startswith("[Member Reply]"):
            potential_parents = [tid for tid, t in tickets_dict.items() if t['category'] == row[1] and tid < t_id]
            if potential_parents:
                parent_id = potential_parents[-1]
                
        if parent_id:
            child_replies.append({
                'id': t_id,
                'parent_id': parent_id,
                'messages': msg_parts,
                'user_attachments': [row[5]] if (row[5] and not row[6]) else [],
                'admin_attachments': [row[5]] if (row[5] and row[6]) else []
            })
        else:
            if t_id not in tickets_dict:
                tickets_dict[t_id] = {
                    'id': t_id,
                    'category': row[1],
                    'status': row[3],
                    'created_at': row[7],
                    'thread': msg_parts,
                    'user_attachments': [],
                    'admin_attachments': []
                }
            if row[5]:
                if row[6]: tickets_dict[t_id]['admin_attachments'].append(row[5])
                else: tickets_dict[t_id]['user_attachments'].append(row[5])
                
    for child in child_replies:
        p_id = child['parent_id']
        if p_id in tickets_dict:
            tickets_dict[p_id]['thread'].extend(child['messages'])
            tickets_dict[p_id]['user_attachments'].extend(child['user_attachments'])
            tickets_dict[p_id]['admin_attachments'].extend(child['admin_attachments'])
            tickets_dict[p_id]['status'] = child.get('status', tickets_dict[p_id]['status'])
            
    sorted_history = sorted(tickets_dict.values(), key=lambda x: x['created_at'], reverse=True)
    return sorted_history

    # Now define member_ticket_thread using the helper
    history = get_threaded_history(session['member_id'], single_ticket_id=ticket_id)
    return render_template('member_history.html', history=history, single_thread=True, ticket_id=ticket_id)

@app.route('/history', methods=['GET', 'POST'])
def member_history():
    if 'member_id' not in session:
        return redirect(url_for('member_login'))
        
    if request.method == 'POST':
        category = request.form.get('category')
        message = request.form.get('message')
        attachments = request.files.getlist('attachment')
        
        conn, db_type = get_db_connection()
        c = get_cursor(conn, db_type)
        
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

    history = get_threaded_history(session['member_id'])
    return render_template('member_history.html', history=history)

@app.route('/admin/vault/<int:member_id>', methods=['GET', 'POST'])
def admin_user_vault(member_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
        
    if request.method == 'POST':
        admin_new_message = request.form.get('admin_new_message')
        
        if admin_new_message:
            category = request.form.get('category_new')
            content = request.form.get('message_new')
            
            conn, db_type = get_db_connection()
            c = get_cursor(conn, db_type)
            # Create a brand new ticket (message) initiated by the admin
            if db_type == 'postgres':
                c.execute("INSERT INTO tickets (user_id, category, message, status, admin_reply) VALUES (%s, %s, %s, 'Replied', %s) RETURNING id",
                          (member_id, category, '[PROACTIVE ADMIN MESSAGE]', content))
                t_id = c.fetchone()[0]
            else:
                c.execute("INSERT INTO tickets (user_id, category, message, status, admin_reply) VALUES (%s, %s, %s, 'Replied', %s)",
                          (member_id, category, '[PROACTIVE ADMIN MESSAGE]', content))
                t_id = c.cursor.lastrowid
            conn.commit()
            conn.close()
            flash('Proactive message dispatched.')
            return redirect(url_for('admin_user_vault', member_id=member_id))
            
        else: # Standard Quick Reply to existing ticket
            admin_reply_text = request.form.get('admin_reply_text')
            media_files = request.files.getlist('admin_media')
            
            conn, db_type = get_db_connection()
            c = get_cursor(conn, db_type)
            
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

    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
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
            
    # Fetch concierge chat history using member_id decoupled from submissions
    c.execute("SELECT * FROM vip_pre_payment_chats WHERE member_id = %s ORDER BY timestamp ASC", (member_id,))
    concierge_chats = c.fetchall()
    sub_id = member_id # Pass member_id to template so form posts to /vip_chat/send/member_id

    # Fetch member details for name and permissions
    c.execute("SELECT fullname, membership_tier, can_write_news, can_write_insights FROM members WHERE id = %s", (member_id,))
    member_row = c.fetchone()
    member_name = member_row[0] if member_row else "Unknown Member"
    membership_tier = member_row[1] if member_row else "N/A"
    can_write_news = member_row[2] if member_row else False
    can_write_insights = member_row[3] if member_row else False

    conn.close()
    
    return render_template('user_vault.html', 
                           member_id=member_id, 
                           member_name=member_name, 
                           membership_tier=membership_tier,
                           can_write_news=can_write_news,
                           can_write_insights=can_write_insights,
                           vault_history=list(vault_history.values()), 
                           concierge_chats=concierge_chats, 
                           sub_id=sub_id)

@app.route('/admin/update_permissions/<int:member_id>', methods=['POST'])
def update_member_permissions(member_id):
    if not session.get('is_admin') == True:
        return "Unauthorized", 403
    
    can_write_news = 'can_write_news' in request.form
    can_write_insights = 'can_write_insights' in request.form
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("UPDATE members SET can_write_news = %s, can_write_insights = %s WHERE id = %s",
              (can_write_news, can_write_insights, member_id))
    conn.commit()
    conn.close()
    
    flash(f"Permissions updated successfully for user ID {member_id}.")
    return redirect(url_for('admin_user_vault', member_id=member_id))

@app.route('/admin/logout')
def admin_logout():
    session.clear()
    return redirect(url_for('admin_login'))

@app.route('/vip_lounge', methods=['GET', 'POST'])
def vip_lounge():
    is_admin = session.get('is_admin') == True
    member_id = session.get('member_id')
    
    if not is_admin and not member_id:
        return redirect(url_for('member_login'))
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    member_data = None
    can_write = is_admin
    if member_id:
        c.execute("SELECT fullname, membership_tier, vip_since FROM members WHERE id = %s", (member_id,))
        member_data = c.fetchone()
        if member_data:
            can_write = (member_data[1] == 'VIP') or is_admin
            session['membership_tier'] = member_data[1]
            # Phase 3: guard is now INSIDE the member_data check — prevents NoneType crash
            if member_data[2] is None:
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
        elif not is_admin and (not member_data or member_data[1] != 'VIP'):
            flash("Strictly active VIP members can post in the lounge.")
            conn.close()
            return redirect(url_for('vip_lounge', channel=request.form.get('channel_id', 'main')))
        else:
            msg_text = request.form.get('message_text')
            channel_id = request.form.get('channel_id', 'main')
            files = request.files.getlist('attachments')
            
            if len(files) > 5:
                flash("Maximum 5 files allowed.")
            else:
                total_size = sum([len(f.read()) for f in files if f.filename != ''])
                for f in files: f.seek(0)
                
                if total_size > 200 * 1024 * 1024:
                    flash("Collective file size exceeds 200MB.")
                else:
                    reply_to_id = request.form.get('reply_to_id')
                    if reply_to_id == '': reply_to_id = None
                    
                    # Permission Check for specialized channels
                    can_post = True
                    if not is_admin:
                        # Check if user has specific grant for this channel
                        c.execute("SELECT can_write_news, can_write_insights FROM members WHERE id = %s", (member_id,))
                        perms = c.fetchone()
                        if channel_id == 'announcements' and (not perms or not perms[0]):
                            can_post = False
                        elif channel_id == 'strategic' and (not perms or not perms[1]):
                            can_post = False
                    
                    if not can_post:
                        conn.close()
                        flash("You do not have administrative permission to post in this specialized channel.")
                        return redirect(url_for('vip_lounge', channel=channel_id))

                    c.execute("INSERT INTO chatroom_messages (room_id, sender_id, message_text, channel_id, reply_to_id) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                              (room_id, selected_sender_id, msg_text, channel_id, reply_to_id))
                    msg_id = c.fetchone()[0]
                    
                    for f in files:
                        if f and f.filename != '':
                            filename = f"{int(time.time())}_{secure_filename(f.filename)}"
                            db_path = save_uploaded_file(f, folder='static/chatroom_uploads', custom_filename=filename)
                            if db_path:
                                c.execute("INSERT INTO chatroom_attachments (message_id, file_path, file_size) VALUES (%s, %s, %s)",
                                          (msg_id, db_path, total_size)) 
                    conn.commit()
                    # Success flash removed as requested
                    conn.close()
                    return redirect(url_for('vip_lounge', channel=channel_id))

    channel_id = request.args.get('channel', 'main')
    
    # Fetch Pinned Messages for this channel
    c.execute("""
        SELECT m.fullname, cm.message_text, cm.created_at, cm.id
        FROM chatroom_messages cm
        JOIN members m ON cm.sender_id = m.id
        WHERE cm.room_id = %s AND cm.channel_id = %s AND cm.is_pinned = TRUE
        ORDER BY cm.created_at DESC
    """, (room_id, channel_id))
    pinned_rows = c.fetchall()
    pinned_messages = []
    for p in pinned_rows:
        pinned_messages.append({'id': p['id'], 'sender': p['fullname'], 'text': p['message_text'], 'time': p['created_at']})

    if is_admin:
        c.execute("""
            SELECT m.fullname, cm.message_text, cm.created_at, cm.id, cm.is_pinned, cm.reply_to_id,
            (SELECT fullname FROM members m2 JOIN chatroom_messages cm2 ON m2.id = cm2.sender_id WHERE cm2.id = cm.reply_to_id) as reply_sender,
            (SELECT message_text FROM chatroom_messages cm3 WHERE cm3.id = cm.reply_to_id) as reply_text
            FROM chatroom_messages cm
            JOIN members m ON cm.sender_id = m.id
            WHERE cm.room_id = %s AND cm.channel_id = %s
            ORDER BY cm.created_at ASC
        """, (room_id, channel_id))
    else:
        c.execute("""
            SELECT m.fullname, cm.message_text, cm.created_at, cm.id, cm.is_pinned, cm.reply_to_id,
            (SELECT fullname FROM members m2 JOIN chatroom_messages cm2 ON m2.id = cm2.sender_id WHERE cm2.id = cm.reply_to_id) as reply_sender,
            (SELECT message_text FROM chatroom_messages cm3 WHERE cm3.id = cm.reply_to_id) as reply_text
            FROM chatroom_messages cm
            JOIN members m ON cm.sender_id = m.id
            WHERE cm.room_id = %s AND cm.channel_id = %s
            AND (cm.channel_id = 'announcements' OR EXISTS (
                SELECT 1 FROM vip_periods vp 
                WHERE vp.user_id = %s 
                AND cm.created_at >= vp.start_time 
                AND (vp.end_time IS NULL OR cm.created_at <= vp.end_time)
            ))
            ORDER BY cm.created_at ASC
        """, (room_id, channel_id, member_id))
    
    # Retrieve messages with unique IDs, attachments, and reactions
    msgs = c.fetchall()
    display_messages = []
    for m in msgs:
        c.execute("SELECT file_path FROM chatroom_attachments WHERE message_id = %s", (m['id'],))
        atts = [r[0] for r in c.fetchall()]
        
        # Fetch reactions grouped by emoji
        c.execute("""
            SELECT emoji, COUNT(*) as count 
            FROM chatroom_reactions 
            WHERE message_id = %s 
            GROUP BY emoji
        """, (m['id'],))
        reactions = {r['emoji']: r['count'] for r in c.fetchall()}
        
        display_messages.append({
            'id': m['id'],
            'sender': m['fullname'], 
            'text': m['message_text'], 
            'time': m['created_at'], 
            'attachments': atts,
            'is_pinned': m['is_pinned'],
            'reactions': reactions,
            'reply_to_id': m['reply_to_id'],
            'reply_sender': m['reply_sender'],
            'reply_text': m['reply_text']
        })
        
    # Check write permission for UI
    can_write = is_admin or (member_data and member_data[1] == 'VIP')
    if not is_admin and can_write:
        c.execute("SELECT can_write_news, can_write_insights FROM members WHERE id = %s", (member_id,))
        p = c.fetchone()
        if channel_id == 'announcements' and (not p or not p[0]):
            can_write = False
        elif channel_id == 'strategic' and (not p or not p[1]):
            can_write = False

    slides = []
    if channel_id == 'announcements':
        c.execute("SELECT image_path, info_text FROM club_slideshows WHERE is_active = TRUE ORDER BY created_at DESC")
        slide_rows = c.fetchall()
        for row in slide_rows:
            slides.append({'image': row[0], 'text': row[1]})

    conn.close()
    return render_template('chatroom.html', 
                           messages=display_messages, 
                           pinned_messages=pinned_messages,
                           is_admin=is_admin, 
                           channel_id=channel_id,
                           room_id=room_id,
                           can_write=can_write,
                           slides=slides,
                           fullname=member_data[0] if member_data else 'Official Admin')

@app.route('/trigger_admin_alert')
def trigger_admin_alert():
    member_id = session.get('member_id')
    if not member_id:
        return "Not authorized", 403
    
    channel_id = request.args.get('channel', 'main')
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    c.execute("SELECT fullname FROM members WHERE id = %s", (member_id,))
    member_name = c.fetchone()[0]
    
    # Post a prominent system message to the chat
    c.execute("SELECT id FROM chatrooms WHERE room_name = 'VIP Lounge' LIMIT 1")
    room_row = c.fetchone()
    if room_row:
        room_id = room_row[0]
        # Use a distinctive style for the system message
        system_chat_msg = f"⚠️ [SYSTEM] {member_name} has SUMMONED THE ADMIN for immediate assistance."
        c.execute("INSERT INTO chatroom_messages (room_id, sender_id, message_text, channel_id) VALUES (%s, %s, %s, %s)",
                  (room_id, member_id, system_chat_msg, channel_id))
    
    # Trigger the formal Admin Notification
    msg = f"🔴 URGENT SUMMON: {member_name} is requesting help in the VIP Lounge (Channel: {channel_id})."
    c.execute("INSERT INTO admin_notifications (member_id, action_type, message, target_url) VALUES (%s, 'Summon', %s, %s)", 
              (member_id, msg, f"/vip_lounge?channel={channel_id}"))
    
    conn.commit()
    conn.close()
    return "OK", 200

@app.route('/admin/boost_message', methods=['POST'])
def admin_boost_message():
    if not session.get('is_admin'): return "Unauthorized", 403
    message_id = request.form.get('message_id')
    reaction_type = request.form.get('reaction_type', '❤️')
    count = int(request.form.get('count', 1))
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("SELECT id, count FROM message_reactions WHERE message_id = %s AND reaction_type = %s", (message_id, reaction_type))
    row = c.fetchone()
    if row:
        c.execute("UPDATE message_reactions SET count = count + %s WHERE id = %s", (count, row[0]))
    else:
        c.execute("INSERT INTO message_reactions (message_id, reaction_type, count, is_artificial) VALUES (%s, %s, %s, 1)", 
                  (message_id, reaction_type, count))
    conn.commit()
    conn.close()
    return "OK", 200

@app.route('/admin_delete_chat_message/<int:msg_id>', methods=['POST'])
def admin_delete_chat_message(msg_id):
    if not session.get('is_admin'):
        flash("Not authorized.")
        return redirect(url_for('member_login'))
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    # 1. Handle Foreign Key constraints for replies (nullify children)
    c.execute("UPDATE chatroom_messages SET reply_to_id = NULL WHERE reply_to_id = %s", (msg_id,))
    
    # 2. Delete related data in correct order
    c.execute("DELETE FROM chatroom_attachments WHERE message_id = %s", (msg_id,))
    c.execute("DELETE FROM lounge_message_reactions WHERE message_id = %s", (msg_id,))
    c.execute("DELETE FROM chatroom_reactions WHERE message_id = %s", (msg_id,))
    c.execute("DELETE FROM message_reactions WHERE message_id = %s", (msg_id,))
    c.execute("DELETE FROM chatroom_messages WHERE id = %s", (msg_id,))
    
    conn.commit()
    conn.close()
    
    flash("Message deleted by Admin.")
    return redirect(request.referrer or url_for('vip_lounge'))

@app.route('/api/chat/react', methods=['POST'])
def chat_react():
    if not session.get('member_id') and not session.get('is_admin'):
        return jsonify({"error": "Unauthorized"}), 401
        
    data = request.get_json()
    message_id = data.get('message_id')
    CANONICAL = {
        '❤️': '❤️', '👍': '👍', '🔥': '🔥', '👏': '👏', '😂': '😂', '😮': '😮',
        '✅': '✅', '🚀': '🚀', '💡': '💡', '💯': '💯', '🤝': '🤝', '🙏': '🙏',
        '✨': '✨', '🎉': '🎉', '🌟': '🌟', '😎': '😎'
    }
    def canon(e):
        if not e: return e
        e_norm = e.replace('\ufe0f', '')
        for c_emoji in CANONICAL.values():
            if c_emoji.replace('\ufe0f', '') == e_norm:
                return c_emoji
        return e

    emoji = canon(data.get('emoji'))
    
    # Prioritize session member_id (works for both Members and Admins logged in as members)
    member_id = session.get('member_id')
    
    if not member_id:
        # Fallback for pure Admin logins
        conn, db_type = get_db_connection()
        c = get_cursor(conn, db_type)
        c.execute("SELECT id FROM members WHERE username = 'AdminMaster' LIMIT 1")
        row = c.fetchone()
        member_id = row[0] if row else None
        conn.close()

    if not member_id:
        return jsonify({"error": "Identity could not be verified"}), 403

    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    try:
        # Enforce one reaction per person
        c.execute("SELECT id, emoji FROM chatroom_reactions WHERE message_id = %s AND member_id = %s", (message_id, member_id))
        existing = c.fetchone()
        
        if existing:
            # Normalize for comparison
            if canon(existing['emoji']) == emoji:
                status = "maintained"
            else:
                # Switch to new emoji
                c.execute("DELETE FROM chatroom_reactions WHERE id = %s", (existing['id'],))
                c.execute("INSERT INTO chatroom_reactions (message_id, member_id, emoji) VALUES (%s, %s, %s)", (message_id, member_id, emoji))
                status = "switched"
        else:
            c.execute("INSERT INTO chatroom_reactions (message_id, member_id, emoji) VALUES (%s, %s, %s)", (message_id, member_id, emoji))
            status = "added"
            
        conn.commit()
        return jsonify({"status": "success", "action": status})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/chat/pin/<int:msg_id>', methods=['POST'])
def chat_toggle_pin(msg_id):
    if not session.get('is_admin'):
        return jsonify({"error": "Forbidden"}), 403
        
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("SELECT is_pinned FROM chatroom_messages WHERE id = %s", (msg_id,))
    row = c.fetchone()
    if row:
        new_status = not row[0]
        c.execute("UPDATE chatroom_messages SET is_pinned = %s WHERE id = %s", (new_status, msg_id))
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "is_pinned": new_status})
    conn.close()
    return jsonify({"error": "Message not found"}), 404

@app.route('/api/chat/search')
def chat_search():
    if not session.get('member_id') and not session.get('is_admin'):
        return jsonify({"error": "Unauthorized"}), 401
        
    query = request.args.get('q', '')
    if len(query) < 2:
        return jsonify({"results": []})
        
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("""
        SELECT m.fullname, cm.message_text, cm.created_at, cm.channel_id
        FROM chatroom_messages cm
        JOIN members m ON cm.sender_id = m.id
        WHERE cm.message_text LIKE %s
        ORDER BY cm.created_at DESC LIMIT 20
    """, (f'%{query}%',))
    rows = c.fetchall()
    conn.close()
    
    results = [{'sender': r['fullname'], 'text': r['message_text'], 'time': r['created_at'], 'channel': r['channel_id']} for r in rows]
    return jsonify({"results": results})

@app.route('/stars')
def stars_roster():
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    c.execute("SELECT setting_value FROM site_settings WHERE setting_key = 'star_booking_writeup'")
    # Phase 3: assign fetchone() to variable first — calling it twice consumed the row leaving None
    _writeup_row = c.fetchone()
    writeup = _writeup_row[0] if _writeup_row else "Book your favorite stars for exclusive events and personal sessions."

    c.execute("SELECT * FROM stars WHERE is_active = TRUE")
    stars_raw = c.fetchall()
    
    # Enrich stars with media carousels
    stars = []
    for s in stars_raw:
        star_dict = dict(s)  # Always convert to plain dict so we can add 'media' key
        c.execute("SELECT file_path, media_type FROM star_media WHERE star_id = %s ORDER BY created_at ASC", (star_dict['id'],))
        star_dict['media'] = [dict(m) for m in c.fetchall()]
        stars.append(star_dict)

    my_bookings = []
    if 'member_id' in session:
        c.execute("""
            SELECT sb.*, s.name as star_name, s.image_path 
            FROM star_bookings sb 
            LEFT JOIN stars s ON sb.star_id = s.id 
            WHERE sb.member_id = %s
            ORDER BY sb.created_at DESC
        """, (session['member_id'],))
        my_bookings = c.fetchall()
        
    conn.close()
    return render_template('stars_roster.html', stars=stars, my_bookings=my_bookings, writeup=writeup)

@app.route('/book_a_star/request/<int:star_id>', methods=['GET', 'POST'])
def request_star(star_id):
    if 'member_id' not in session:
        return redirect(url_for('member_login'))
        
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    c.execute("SELECT setting_value FROM site_settings WHERE setting_key = 'star_occasions'")
    occ_row = c.fetchone()
    occasions_str = occ_row[0] if occ_row else "Birthday, Anniversary, Expert Advice, Personal Shoutout, Business Endorsement, Other"
    occasions = [o.strip() for o in occasions_str.split(',')]

    if request.method == 'POST':
        occasion = request.form.get('occasion')
        timeframe = request.form.get('timeframe')
        start_time = request.form.get('start_time')
        address = request.form.get('address')
        recipient = request.form.get('recipient', 'N/A')
        instructions = request.form.get('instructions', 'No specific instructions provided.')
        
        details = f"Occasion: {occasion}\nTimeframe: {timeframe}\nStarts: {start_time}\nAddress: {address}\nFor: {recipient}\nInstructions: {instructions}"
        
        member_id = session['member_id']
        room_name = f"StarBooking_{member_id}_{star_id}_{int(time.time())}"
        
        if db_type == 'postgres':
            c.execute("INSERT INTO chatrooms (room_name) VALUES (%s) RETURNING id", (room_name,))
            room_id = c.fetchone()[0]
        else:
            c.execute("INSERT INTO chatrooms (room_name) VALUES (%s)", (room_name,))
            room_id = c.cursor.lastrowid
            
        c.execute("""
            INSERT INTO star_bookings (member_id, star_id, request_details, chatroom_id, status, occasion, timeframe, start_time, address, recipient) 
            VALUES (%s, %s, %s, %s, 'Pending', %s, %s, %s, %s, %s)
        """, (member_id, star_id, details, room_id, occasion, timeframe, start_time, address, recipient))
        
        c.execute("SELECT name FROM stars WHERE id = %s", (star_id,))
        star_name = c.fetchone()[0]
        
        add_admin_notification(member_id, 'Star Booking', f"{session.get('member_fullname')} requested {star_name}.", url_for('star_booking_chat', room_id=room_id))
        
        conn.commit()
        conn.close()
        flash(f"Your request for {star_name} has been submitted. Check the booking chat for updates.", "success")
        return redirect(url_for('star_booking_chat', room_id=room_id))
        
    c.execute("SELECT * FROM stars WHERE id = %s", (star_id,))
    star = c.fetchone()
    conn.close()
    
    if not star:
        return "Star not found."
        
    return render_template('book_star_form.html', star=star, occasions=occasions)

@app.route('/book_a_star/special_request', methods=['POST'])
def request_special_star():
    if 'member_id' not in session:
        return redirect(url_for('member_login'))
        
    category = request.form.get('special_category')
    if category == 'Other':
        category = request.form.get('other_category', 'Special Category')
        
    celebrity_name = request.form.get('celebrity_name')
    date_needed = request.form.get('date_needed')
    duration = request.form.get('duration')
    budget = request.form.get('budget')
    description = request.form.get('description', '')
    
    details = f"SPECIAL REQUEST\nCategory: {category}\nName: {celebrity_name}\nDate: {date_needed}\nDuration: {duration}\nBudget: {budget}\nDescription: {description}"
    
    member_id = session['member_id']
    room_name = f"SpecialStar_{member_id}_{int(time.time())}"
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    if db_type == 'postgres':
        c.execute("INSERT INTO chatrooms (room_name) VALUES (%s) RETURNING id", (room_name,))
        room_id = c.fetchone()[0]
    else:
        c.execute("INSERT INTO chatrooms (room_name) VALUES (%s)", (room_name,))
        room_id = c.cursor.lastrowid
        
    # Ensure Special Dummy Star exists
    c.execute("SELECT id FROM stars WHERE name = 'Special Celebrity Request' LIMIT 1")
    dummy_star = c.fetchone()
    if not dummy_star:
        if db_type == 'postgres':
            c.execute("INSERT INTO stars (name, category, bio, price, location, image_path, is_active) VALUES ('Special Celebrity Request', 'Special', 'Custom global celebrity request.', 'Variable', 'Global', '', FALSE) RETURNING id")
            star_id = c.fetchone()[0]
        else:
            c.execute("INSERT INTO stars (name, category, bio, price, location, image_path, is_active) VALUES ('Special Celebrity Request', 'Special', 'Custom global celebrity request.', 'Variable', 'Global', '', FALSE)")
            star_id = c.cursor.lastrowid
    else:
        star_id = dummy_star[0]
        
    c.execute("""
        INSERT INTO star_bookings (member_id, star_id, request_details, chatroom_id, status) 
        VALUES (%s, %s, %s, %s, 'Pending')
    """, (member_id, star_id, details, room_id))
    
    add_admin_notification(member_id, 'Special Star Booking', f"{session.get('member_fullname')} requested a special celebrity: {celebrity_name}.", url_for('star_booking_chat', room_id=room_id))
    
    conn.commit()
    conn.close()
    flash("Your special request has been submitted to the admin.", "success")
    return redirect(url_for('star_booking_chat', room_id=room_id))


@app.route('/admin/bookings/arrival_time/<int:booking_id>', methods=['POST'])
def admin_update_arrival_time(booking_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
        
    arrival_time = request.form.get('arrival_time')
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("UPDATE star_bookings SET arrival_time = %s WHERE id = %s", (arrival_time, booking_id))
    
    # Notify Member in chat
    c.execute("SELECT chatroom_id FROM star_bookings WHERE id = %s", (booking_id,))
    room_row = c.fetchone()
    if room_row:
        room_id = room_row[0]
        c.execute("SELECT id FROM members WHERE username = 'AdminMaster' LIMIT 1")
        admin_row = c.fetchone()
        admin_id = admin_row[0] if admin_row else 0
        
        msg = f"System Update: Talent arrival confirmed for {arrival_time}"
        c.execute("INSERT INTO chatroom_messages (room_id, sender_id, message_text) VALUES (%s, %s, %s)",
                  (room_id, admin_id, msg))
        
    conn.commit()
    conn.close()
    flash("Arrival time updated and member notified.")
    return redirect(request.referrer or url_for('admin_dashboard'))

@app.route('/api/chat/messages/<int:room_id>')
def api_chat_messages(room_id):
    # Zero-cost AJAX polling endpoint
    if not session.get('member_id') and not session.get('is_admin'):
        return {"error": "Unauthorized"}, 401
        
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    # Get current user's identity for reaction highlighting
    current_uid = session.get('member_id')
    if not current_uid:
        # Check for AdminMaster fallback
        c.execute("SELECT id FROM members WHERE username = 'AdminMaster' LIMIT 1")
        row = c.fetchone()
        current_uid = row[0] if row else 0

    channel_id = request.args.get('channel', 'main')
    last_id = request.args.get('last_id', 0, type=int)
    is_admin = session.get('is_admin') == True
    
    if is_admin:
        c.execute("""
            SELECT cm.id, m.fullname, cm.message_text, cm.created_at, cm.is_pinned, cm.reply_to_id,
            (SELECT fullname FROM members m2 JOIN chatroom_messages cm2 ON m2.id = cm2.sender_id WHERE cm2.id = cm.reply_to_id) as reply_sender,
            (SELECT message_text FROM chatroom_messages cm3 WHERE cm3.id = cm.reply_to_id) as reply_text
            FROM chatroom_messages cm
            JOIN members m ON cm.sender_id = m.id
            WHERE cm.room_id = %s AND cm.channel_id = %s AND cm.id > %s 
            ORDER BY cm.id ASC
        """, (room_id, channel_id, last_id))
    else:
        c.execute("""
            SELECT cm.id, m.fullname, cm.message_text, cm.created_at, cm.is_pinned, cm.reply_to_id,
            (SELECT fullname FROM members m2 JOIN chatroom_messages cm2 ON m2.id = cm2.sender_id WHERE cm2.id = cm.reply_to_id) as reply_sender,
            (SELECT message_text FROM chatroom_messages cm3 WHERE cm3.id = cm.reply_to_id) as reply_text
            FROM chatroom_messages cm
            JOIN members m ON cm.sender_id = m.id
            WHERE cm.room_id = %s AND cm.channel_id = %s AND cm.id > %s 
            AND (cm.channel_id = 'announcements' OR EXISTS (
                SELECT 1 FROM vip_periods vp 
                WHERE vp.user_id = %s 
                AND cm.created_at >= vp.start_time 
                AND (vp.end_time IS NULL OR cm.created_at <= vp.end_time)
            ))
            ORDER BY cm.id ASC
        """, (room_id, channel_id, last_id, current_uid))
    rows = c.fetchall()
    
    # Canonical Emoji mapping to ensure color rendering (Force variation selector)
    CANONICAL = {
        '❤️': '❤️', '👍': '👍', '🔥': '🔥', '👏': '👏', '😂': '😂', '😮': '😮',
        '✅': '✅', '🚀': '🚀', '💡': '💡', '💯': '💯', '🤝': '🤝', '🙏': '🙏',
        '✨': '✨', '🎉': '🎉', '🌟': '🌟', '😎': '😎'
    }
    def canon(e):
        e_norm = e.replace('\ufe0f', '')
        for c_emoji in CANONICAL.values():
            if c_emoji.replace('\ufe0f', '') == e_norm:
                return c_emoji
        return e # fallback

    messages = []
    for r in rows:
        # 1. Fetch real member reactions
        c.execute("""
            SELECT emoji as reaction_type, COUNT(*) as count 
            FROM chatroom_reactions 
            WHERE message_id = %s 
            GROUP BY emoji
        """, (r[0],))
        real_reactions = {}
        for rec in c.fetchall():
            ce = canon(rec[0])
            real_reactions[ce] = real_reactions.get(ce, 0) + rec[1]
        
        # 2. Fetch artificial boosts
        c.execute("""
            SELECT reaction_type, SUM(count) as count 
            FROM lounge_message_reactions 
            WHERE message_id = %s 
            GROUP BY reaction_type
        """, (r[0],))
        boost_rows = c.fetchall()
        
        # 3. Merge them
        all_reactions = real_reactions.copy()
        for rec in boost_rows:
            if not rec[0]: continue
            ce = canon(rec[0])
            all_reactions[ce] = all_reactions.get(ce, 0) + (rec[1] or 0)
            
        # 4. Get user's own reaction
        c.execute("SELECT emoji FROM chatroom_reactions WHERE message_id = %s AND member_id = %s", (r[0], current_uid))
        u_row = c.fetchone()
        u_react = canon(u_row[0]) if u_row else None
            
        messages.append({
            'id': r[0],
            'sender': r[1],
            'text': r[2],
            'time': r[3],
            'is_pinned': r[4],
            'reply_to_id': r[5],
            'reply_sender': r[6],
            'reply_text': r[7],
            'reactions': all_reactions,
            'user_reaction': u_react
        })
    
    # 4. Reaction Sync for visible messages (Last 20)
    reaction_sync = []
    c.execute("""
        SELECT id FROM chatroom_messages 
        WHERE room_id = %s AND channel_id = %s 
        ORDER BY id DESC LIMIT 20
    """, (room_id, channel_id))
    sync_ids = [r[0] for r in c.fetchall()]
    
    for s_id in sync_ids:
        # Fetch real reactions
        c.execute("SELECT emoji, COUNT(*) FROM chatroom_reactions WHERE message_id = %s GROUP BY emoji", (s_id,))
        real = {}
        for rec in c.fetchall():
            ce = canon(rec[0])
            real[ce] = real.get(ce, 0) + rec[1]
        
        # Fetch boosts
        c.execute("SELECT reaction_type, SUM(count) FROM lounge_message_reactions WHERE message_id = %s GROUP BY reaction_type", (s_id,))
        boost_rows = c.fetchall()
        
        merged = real.copy()
        for rec in boost_rows:
            if not rec[0]: continue
            ce = canon(rec[0])
            merged[ce] = merged.get(ce, 0) + (rec[1] or 0)
        
        # Get user's own reaction
        c.execute("SELECT emoji FROM chatroom_reactions WHERE message_id = %s AND member_id = %s", (s_id, current_uid))
        u_row = c.fetchone()
        u_react = canon(u_row[0]) if u_row else None
        
        reaction_sync.append({'id': s_id, 'reactions': merged, 'user_reaction': u_react})

    # 5. Fetch Active Polls (Only for #News / announcements)
    active_polls = []
    if channel_id == 'announcements':
        # ... (rest of the logic)
        c.execute("SELECT id, question, options FROM lounge_polls WHERE is_closed = FALSE ORDER BY created_at DESC")
        poll_rows = c.fetchall()
        for poll_row in poll_rows:
            p_id = poll_row[0]
            options_list = poll_row[2].split(',')
            options_data = []
            
            for idx, opt_text in enumerate(options_list):
                c.execute("SELECT COUNT(*) FROM lounge_poll_votes WHERE poll_id = %s AND option_index = %s", (p_id, idx))
                vote_sum = c.fetchone()[0] or 0
                options_data.append({'id': idx, 'text': opt_text.strip(), 'votes': vote_sum})
                
            c.execute("SELECT option_index FROM lounge_poll_votes WHERE poll_id = %s AND member_id = %s", (p_id, current_uid))
            u_vote_row = c.fetchone()
            u_vote = u_vote_row[0] if u_vote_row else None
            
            active_polls.append({
                'id': p_id,
                'question': poll_row[1],
                'options': options_data,
                'user_vote': u_vote
            })

    # 6. Fetch Slideshows (Only for #News)
    slides = []
    if channel_id == 'announcements':
        c.execute("SELECT image_path, info_text FROM club_slideshows WHERE is_active = TRUE ORDER BY created_at DESC")
        slide_rows = c.fetchall()
        for row in slide_rows:
            slides.append({'image': row[0], 'text': row[1]})

    conn.close()
    return {"messages": messages, "active_polls": active_polls, "reaction_sync": reaction_sync, "slides": slides}

@app.route('/star_booking_chat/<int:room_id>', methods=['GET', 'POST'])
def star_booking_chat(room_id):
    is_admin = session.get('is_admin') == True
    member_id = session.get('member_id')
    
    if not is_admin and not member_id:
        return redirect(url_for('member_login'))
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    # Resolve the current user's display name for correct bubble alignment
    if is_admin:
        current_fullname = 'Official Admin'
    else:
        c.execute("SELECT fullname FROM members WHERE id = %s", (member_id,))
        row = c.fetchone()
        current_fullname = row[0] if row else session.get('member_fullname', '')

    # Security: Non-admin can only access their own room
    if not is_admin:
        c.execute("SELECT room_name FROM chatrooms WHERE id = %s", (room_id,))
        room_row = c.fetchone()
        member_prefixes = (f"StarBooking_{member_id}", f"SpecialStar_{member_id}")
        if not room_row or not room_row[0].startswith(member_prefixes):
            conn.close()
            flash("Unauthorized access to private booking.")
            return redirect(url_for('member_dashboard'))

    if request.method == 'POST':
        selected_sender_id = member_id
        if is_admin:
            c.execute("SELECT id FROM members WHERE username = 'AdminMaster' LIMIT 1")
            admin_row = c.fetchone()
            selected_sender_id = admin_row[0] if admin_row else None
            
        if not selected_sender_id:
            flash("System Error: Identity not initialized.")
        else:
            msg_text = request.form.get('message_text', '').strip()
            files = request.files.getlist('attachments')
            real_files = [f for f in files if f and f.filename != '']

            # Allow empty text if files are attached
            if not msg_text and not real_files:
                flash("Please type a message or attach a file.")
            elif len(real_files) > 5:
                flash("Maximum 5 files allowed.")
            else:
                total_size = sum([len(f.read()) for f in real_files])
                for f in real_files: f.seek(0)
                
                if total_size > 200 * 1024 * 1024:
                    flash("Collective file size exceeds 200MB.")
                else:
                    if db_type == 'postgres':
                        c.execute("INSERT INTO chatroom_messages (room_id, sender_id, message_text, channel_id) VALUES (%s, %s, %s, %s) RETURNING id",
                                  (room_id, selected_sender_id, msg_text or '', 'booking'))
                        msg_id = c.fetchone()[0]
                    else:
                        c.execute("INSERT INTO chatroom_messages (room_id, sender_id, message_text, channel_id) VALUES (%s, %s, %s, %s)",
                                  (room_id, selected_sender_id, msg_text or '', 'booking'))
                        msg_id = c.cursor.lastrowid
                    
                    for f in real_files:
                        filename = f"{int(time.time())}_{secure_filename(f.filename)}"
                        # Determine file size safely from the stream before upload
                        f.seek(0, 2)
                        f_size = f.tell()
                        f.seek(0)
                        
                        db_path = save_uploaded_file(f, folder='static/chatroom_uploads', custom_filename=filename)
                        if db_path:
                            c.execute("INSERT INTO chatroom_attachments (message_id, file_path, file_size) VALUES (%s, %s, %s)",
                                      (msg_id, db_path, f_size))
                    conn.commit()
                    
                    # Notify Member if Admin replied
                    if is_admin:
                        try:
                            c.execute("SELECT m.email, m.fullname, s.name as star_name FROM members m JOIN star_bookings sb ON m.id = sb.member_id LEFT JOIN stars s ON sb.star_id = s.id WHERE sb.chatroom_id = %s LIMIT 1", (room_id,))
                            member_row = c.fetchone()
                            if member_row:
                                m_email = member_row[0]
                                m_name = member_row[1]
                                s_name = member_row[2]
                                subj = f"New Message: Your booking for {s_name}"
                                body = f"Hello {m_name},<br><br>An administrator has replied to your booking request for <strong>{s_name}</strong>. Please check your dashboard for the latest update.<br><br><a href='{url_for('star_booking_chat', room_id=room_id, _external=True)}'>View Message</a>"
#                                send_email_notification(m_email, subj, body)
                        except Exception as e:
                            print(f"Chat Notification Error: {e}")

                    conn.close()
                    
                    # AJAX Support
                    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                        return jsonify({"status": "success", "message": "Sent"})
                        
                    return redirect(url_for('star_booking_chat', room_id=room_id))

    # Fetch messages
    c.execute("""
        SELECT m.fullname, cm.message_text, cm.created_at, cm.id
        FROM chatroom_messages cm
        JOIN members m ON cm.sender_id = m.id
        WHERE cm.room_id = %s
        ORDER BY cm.created_at ASC
    """, (room_id,))
    
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
            'attachments': atts,
            'reactions': {},
            'is_pinned': False,
            'reply_to_id': None
        })
        
    conn.close()
    return render_template('chatroom.html',
                           messages=display_messages,
                           is_admin=is_admin,
                           room_id=room_id,
                           is_star_booking=True,
                           channel_id='booking',
                           can_write=True,
                           fullname=current_fullname)


@app.route('/api/keepalive', methods=['GET'])
def api_keepalive():
    try:
        conn, db_type = get_db_connection()
        c = get_cursor(conn, db_type)
        c.execute("SELECT 1;")
        c.fetchone()
        conn.close()
        return jsonify({"status": "alive", "database": "active"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ─────────────────────────────────────────────
#  LEGAL & TRUST PAGES
# ─────────────────────────────────────────────
@app.route('/terms')
def terms_of_service():
    return render_template('terms.html')

@app.route('/privacy')
def privacy_policy():
    return render_template('privacy_policy.html')

@app.route('/cookies')
def cookie_policy():
    return render_template('cookies.html')

# Initialize DB within app context for safety - ensure it only runs once per process
_db_initialized = False

def run_startup_logic():
    global _db_initialized
    if _db_initialized: return
    
    with app.app_context():
        # Auto-migration on startup
        try:
            from initialize_remote import run_migrations
            run_migrations()
        except Exception as e:
            print(f"Startup Migration Error: {e}")
            
        try:
            init_db()
            init_membership_cards()
            _db_initialized = True
        except Exception as e:
            print(f"Startup DB Init Error: {e}")

# Call startup logic once at module level (still runs per worker, but we limited workers to 2)
run_startup_logic()

# --- Poll System Routes ---

@app.route('/admin/create_executive_poll', methods=['POST'])
def admin_create_executive_poll():
    if not session.get('is_admin'):
        flash("Unauthorized.")
        return redirect(url_for('member_login'))
        
    question = request.form.get('question')
    options = request.form.get('options')
    
    if not question or not options:
        flash("Question and options are required.")
        return redirect(request.referrer)
        
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("INSERT INTO polls (question, options) VALUES (%s, %s)", (question, options))
    conn.commit()
    conn.close()
    
    flash("Executive poll has been successfully injected.")
    return redirect(request.referrer)

@app.route('/get_polls')
def get_polls():
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    c.execute("SELECT id, question, options, is_active FROM polls ORDER BY created_at DESC")
    polls = c.fetchall()
    
    results = []
    for p in polls:
        poll_id, question, options_str, is_active = p
        options = [o.strip() for o in options_str.split(',')]
        
        c.execute("SELECT option_index, COUNT(*) FROM poll_votes WHERE poll_id = %s GROUP BY option_index", (poll_id,))
        vote_data = dict(c.fetchall())
        total_votes = sum(vote_data.values())
        
        user_vote = None
        if session.get('member_id'):
            c.execute("SELECT option_index FROM poll_votes WHERE poll_id = %s AND member_id = %s", (poll_id, session['member_id']))
            v = c.fetchone()
            if v: user_vote = v[0]
            
        poll_info = {
            'id': poll_id,
            'question': question,
            'options': [],
            'total_votes': total_votes,
            'is_active': is_active,
            'user_vote': user_vote
        }
        
        for idx, opt in enumerate(options):
            votes = vote_data.get(idx, 0)
            percent = (votes / total_votes * 100) if total_votes > 0 else 0
            poll_info['options'].append({
                'text': opt,
                'votes': votes,
                'percent': round(percent, 1)
            })
        results.append(poll_info)
        
    conn.close()
    return jsonify(results)

@app.route('/vote_poll', methods=['POST'])
def vote_poll_action():
    if not session.get('member_id'):
        return jsonify({'error': 'Login required'}), 403
        
    data = request.get_json()
    poll_id = data.get('poll_id')
    option_idx = data.get('option_idx')
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    try:
        c.execute("INSERT INTO poll_votes (poll_id, member_id, option_index) VALUES (%s, %s, %s)", 
                  (poll_id, session['member_id'], option_idx))
        conn.commit()
        success = True
    except:
        success = False
    conn.close()
    return jsonify({'success': success})

@app.route('/admin/close_poll/<int:poll_id>', methods=['POST'])
def admin_close_poll_action(poll_id):
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 403
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("UPDATE polls SET is_active = FALSE WHERE id = %s", (poll_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

# --- Membership Card Features ---
@app.route('/membership_cards')
def membership_cards_view():
    if 'member_id' not in session:
        return redirect(url_for('member_login'))
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("SELECT * FROM members WHERE id = %s", (session['member_id'],))
    member = c.fetchone()
    c.execute("SELECT * FROM membership_cards ORDER BY price ASC")
    cards = c.fetchall()
    conn.close()
    return render_template('membership_cards.html', cards=cards, member=member)

@app.route('/admin/membership_cards')
def admin_membership_cards():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    c.execute("SELECT * FROM membership_cards ORDER BY price ASC")
    cards = c.fetchall()
    conn.close()
    return render_template('admin_membership_cards.html', cards=cards)

@app.route('/admin/edit_membership_card/<int:card_id>', methods=['GET', 'POST'])
def admin_edit_membership_card(card_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    if request.method == 'POST':
        tier_name = request.form.get('tier_name')
        price = request.form.get('price')
        features = request.form.get('features')
        image_file = request.files.get('card_image')
        
        image_path = request.form.get('current_image')
        if image_file and image_file.filename != '':
            filename = secure_filename(image_file.filename)
            filename = f"card_{card_id}_{filename}"
            image_file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            image_path = f"/static/uploads/{filename}"
            
        c.execute("""
            UPDATE membership_cards 
            SET tier_name = %s, price = %s, features = %s, image_path = %s
            WHERE id = %s
        """, (tier_name, price, features, image_path, card_id))
        conn.commit()
        conn.close()
        flash('Membership card successfully updated.')
        return redirect(url_for('admin_membership_cards'))
        
    c.execute("SELECT * FROM membership_cards WHERE id = %s", (card_id,))
    card = c.fetchone()
    conn.close()
    return render_template('admin_edit_card.html', card=card)


# --- Membership Card Ordering & Verification ---

@app.route('/membership_card/order/<int:card_id>', methods=['GET', 'POST'])
def order_membership_card(card_id):
    if not session.get('member_id'):
        flash("Please login to order a membership card.")
        return redirect(url_for('member_login'))
    
    user_id = session['member_id']
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    # Fetch card details
    c.execute("SELECT tier_name, price FROM membership_cards WHERE id = %s", (card_id,))
    card = c.fetchone()
    if not card:
        conn.close()
        flash("Card not found.")
        return redirect(url_for('membership_cards_view'))
    
    card_name = card[0]
    
    # Create the order
    c.execute("""
        INSERT INTO card_orders (member_id, card_id, status)
        VALUES (%s, %s, 'Pending') RETURNING id
    """, (user_id, card_id))
    order_id = c.fetchone()[0]
    
    # Notify Admin
    add_admin_notification(
        user_id, 
        'New Card Order', 
        f"{session.get('member_fullname')} has ordered a {card_name}. Finalize verification to proceed.",
        url_for('admin_membership_cards')
    )
    
    conn.commit()
    conn.close()
    
    flash(f"{card_name} ordered successfully. The admin will message you shortly. Please proceed to payment verification.")
    return redirect(url_for('verify_card_payment', order_id=order_id))

@app.route('/membership_card/verify/<int:order_id>')
def verify_card_payment(order_id):
    if not session.get('member_id'):
        return redirect(url_for('member_login'))
        
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    c.execute("""
        SELECT co.id, co.status, mc.tier_name, mc.price 
        FROM card_orders co 
        JOIN membership_cards mc ON co.card_id = mc.id 
        WHERE co.id = %s AND co.member_id = %s
    """, (order_id, session['member_id']))
    order = c.fetchone()
    
    if not order:
        conn.close()
        flash("Order not found.")
        return redirect(url_for('membership_cards_view'))
        
    # Fetch crypto wallets and site settings for the payment UI
    c.execute("SELECT id, currency, network, address FROM crypto_wallets WHERE is_active = TRUE")
    wallets = c.fetchall()
    
    settings = {}
    c.execute("SELECT setting_key, setting_value FROM site_settings")
    for k, v in c.fetchall(): settings[k] = v
    
    conn.close()
    
    return render_template('card_verification.html', 
                           order=order, 
                           card_name=order[2], 
                           price=order[3],
                           crypto_wallets=wallets,
                           settings=settings)

@app.route('/membership_card/submit_verification', methods=['POST'])
def submit_card_verification():
    if not session.get('member_id'):
        return redirect(url_for('member_login'))
        
    order_id = request.form.get('order_id')
    payment_method = request.form.get('payment_method')
    evidence = request.files.get('payment_evidence')
    
    if not order_id or not payment_method or not evidence:
        flash("Please provide all required payment details.")
        return redirect(request.referrer)
        
    filename = secure_filename(evidence.filename)
    evidence_path = f"uploads/cards/proof_{order_id}_{filename}"
    full_path = os.path.join(app.config['UPLOAD_FOLDER'], 'cards')
    if not os.path.exists(full_path): os.makedirs(full_path)
    evidence.save(os.path.join(app.config['UPLOAD_FOLDER'], f"cards/proof_{order_id}_{filename}"))
    
    conn, db_type = get_db_connection()
    c = get_cursor(conn, db_type)
    
    # Update order
    c.execute("""
        UPDATE card_orders 
        SET status = 'Verifying', payment_method = %s, proof_path = %s 
        WHERE id = %s AND member_id = %s
    """, (payment_method, f"/static/{evidence_path}", order_id, session['member_id']))
    
    # Notify Admin
    add_admin_notification(
        session['member_id'],
        'Card Payment Uploaded',
        f"{session.get('member_fullname')} uploaded payment proof for their card order.",
        url_for('admin_membership_cards')
    )
    
    conn.commit()
    conn.close()
    
    flash("Payment evidence submitted successfully. An executive will verify your order shortly.")
    return redirect(url_for('membership_cards_view'))

# ─────────────────────────────────────────────
#  PHASE 2: ERROR HANDLERS
# ─────────────────────────────────────────────
@app.errorhandler(404)
def not_found_error(e):
    try:
        return render_template('404.html'), 404
    except Exception:
        return "<h2>404 — Page Not Found</h2><p><a href='/'>Return Home</a></p>", 404

@app.errorhandler(413)
def request_entity_too_large(e):
    flash("Your upload exceeds the 50MB file size limit. Please reduce the file size and try again.", "error")
    return redirect(request.referrer or url_for('member_dashboard'))

@app.errorhandler(500)
def internal_error(e):
    import traceback
    print(f"[500 ERROR] {traceback.format_exc()}")
    try:
        return render_template('500.html'), 500
    except Exception:
        return (
            "<h2>500 — Something went wrong on our end.</h2>"
            "<p>Our team has been notified. <a href='/'>Return Home</a></p>"
        ), 500

# ─────────────────────────────────────────────
#  PHASE 2: CSRF TOKEN INFRASTRUCTURE
# ─────────────────────────────────────────────
import secrets as _secrets_mod

def _generate_csrf_token():
    """Creates a per-session CSRF token and exposes it to all Jinja templates."""
    if 'csrf_token' not in session:
        session['csrf_token'] = _secrets_mod.token_hex(32)
    return session['csrf_token']

app.jinja_env.globals['csrf_token'] = _generate_csrf_token

def _verify_csrf():
    """Returns True if the CSRF token in the form matches the session token."""
    form_token = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
    return form_token and form_token == session.get('csrf_token')

app.jinja_env.globals['verify_csrf'] = _verify_csrf

# End of routes
if __name__ == '__main__':
    app.run(debug=False)