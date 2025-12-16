import os
import sqlite3
import pandas as pd
from flask import Flask, render_template, request, send_file, redirect, url_for, flash, session
import nepali_datetime
from datetime import datetime
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import qrcode

UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'xls', 'xlsx', 'png', 'jpg', 'jpeg'}
TRIAL_LIMIT = 5  # Max 5 logins for trial

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.secret_key = 'secret_key_for_session'

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(os.path.join('static', 'qr'), exist_ok=True)  # Folder to store QR codes

# ------------------ DATABASE ------------------

def init_db():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password TEXT NOT NULL,
                    email TEXT,
                    phone_number TEXT,
                    usage_count INTEGER DEFAULT 0,
                    is_subscribed INTEGER DEFAULT 0,
                    custom_qr TEXT
                )''')
    conn.commit()

    # Auto-create admin user
    c.execute("INSERT OR IGNORE INTO users (username, password, email, phone_number, is_subscribed) VALUES (?, ?, ?, ?, ?)",
              ("admin", generate_password_hash("admin123"), "admin@example.com", "0000000000", 1))
    conn.commit()
    conn.close()

init_db()

# ------------------ HELPERS ------------------

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def bs_to_ad(value):
    try:
        if pd.isna(value):
            return None
        if isinstance(value, datetime):
            return value.strftime("%d/%m/%Y")
        bs_date = str(value).replace("-", "/").strip()
        parts = bs_date.split("/")
        if len(parts) != 3:
            return None
        if int(parts[0]) > 2000:
            y, m, d = map(int, parts)
        else:
            d, m, y = map(int, parts)
        bs_obj = nepali_datetime.date(y, m, d)
        ad_date = bs_obj.to_datetime_date()
        return ad_date.strftime("%d/%m/%Y")
    except:
        return None

def ad_to_bs(value):
    try:
        if pd.isna(value):
            return None
        if isinstance(value, datetime):
            ad_date = value
        else:
            ad_date = datetime.strptime(str(value).strip(), "%d/%m/%Y")
        bs_date = nepali_datetime.date.from_datetime_date(ad_date.date())
        return f"{bs_date.day:02d}/{bs_date.month:02d}/{bs_date.year}"
    except:
        return None

def increment_usage_login(username):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT usage_count, is_subscribed FROM users WHERE username=?", (username,))
    user = c.fetchone()
    if user:
        usage_count, is_subscribed = user
        if not is_subscribed:
            usage_count += 1
            c.execute("UPDATE users SET usage_count=? WHERE username=?", (usage_count, username))
            conn.commit()
    conn.close()
    return usage_count, is_subscribed

def generate_qr(username):
    qr_folder = os.path.join('static', 'qr')
    os.makedirs(qr_folder, exist_ok=True)
    qr_path = os.path.join(qr_folder, f"{username}_qr.png")
    qr = qrcode.QRCode(box_size=10, border=5)
    qr.add_data(f"https://example.com/subscribe?user={username}")  # Replace with your payment link
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img.save(qr_path)
    return f"qr/{username}_qr.png"

def check_trial_expired(username):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT usage_count, is_subscribed, custom_qr FROM users WHERE username=?", (username,))
    result = c.fetchone()
    conn.close()
    if not result:
        return False, None
    usage_count, is_subscribed, custom_qr = result
    if not is_subscribed and usage_count >= TRIAL_LIMIT:
        if custom_qr and os.path.exists(os.path.join('static', custom_qr)):
            return True, custom_qr
        else:
            return True, generate_qr(username)
    return False, None

# ------------------ AUTH ------------------

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = generate_password_hash(request.form['password'])
        email = request.form['email']
        phone = request.form['phone']
        try:
            conn = sqlite3.connect('users.db')
            c = conn.cursor()
            c.execute("INSERT INTO users (username, password, email, phone_number) VALUES (?, ?, ?, ?)",
                      (username, password, email, phone))
            conn.commit()
            conn.close()
            flash('Registration successful. Please log in.')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('Username already exists.')
            return redirect(url_for('register'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE username = ?", (username,))
        user = c.fetchone()
        conn.close()
        if user and check_password_hash(user[2], password):
            increment_usage_login(username)
            trial_expired, qr_file = check_trial_expired(username)
            if trial_expired:
                return render_template('trial_expired.html', qr=qr_file)
            session['user'] = username
            return redirect(url_for('index'))
        else:
            flash('Invalid username or password.')
            return redirect(url_for('login'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user', None)
    flash('You have been logged out.')
    return redirect(url_for('login'))

# ------------------ ADMIN PANEL ------------------

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if 'user' not in session or session['user'] != 'admin':
        flash("Admin access only.")
        return redirect(url_for('login'))

    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT id, username, email, phone_number, usage_count, is_subscribed FROM users")
    users = c.fetchall()
    conn.close()

    users_list = []
    for user in users:
        user_id, username, email, phone, usage_count, is_subscribed = user
        users_list.append({
            'id': user_id,
            'username': username,
            'email': email,
            'phone': phone,
            'usage_count': usage_count,
            'is_subscribed': is_subscribed
        })
    return render_template('admin.html', users=users_list)

@app.route('/admin/subscribe/<int:user_id>')
def admin_subscribe(user_id):
    if 'user' not in session or session['user'] != 'admin':
        flash("Admin access only.")
        return redirect(url_for('login'))
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("UPDATE users SET is_subscribed=1 WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    flash("User subscribed successfully.")
    return redirect(url_for('admin'))

@app.route('/admin/unsubscribe/<int:user_id>')
def admin_unsubscribe(user_id):
    if 'user' not in session or session['user'] != 'admin':
        flash("Admin access only.")
        return redirect(url_for('login'))
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("UPDATE users SET is_subscribed=0 WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    flash("User unsubscribed successfully.")
    return redirect(url_for('admin'))

@app.route('/admin/delete/<int:user_id>')
def admin_delete(user_id):
    if 'user' not in session or session['user'] != 'admin':
        flash("Admin access only.")
        return redirect(url_for('login'))
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    flash("User deleted successfully.")
    return redirect(url_for('admin'))

@app.route('/admin/upload_qr/<int:user_id>', methods=['POST'])
def upload_qr(user_id):
    if 'user' not in session or session['user'] != 'admin':
        flash("Admin access only.")
        return redirect(url_for('login'))
    file = request.files.get('qr_file')
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        qr_folder = os.path.join('static', 'qr')
        os.makedirs(qr_folder, exist_ok=True)
        save_path = os.path.join(qr_folder, f"user_{user_id}_qr{os.path.splitext(filename)[1]}")
        file.save(save_path)
        relative_path = f"qr/user_{user_id}_qr{os.path.splitext(filename)[1]}"
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        c.execute("UPDATE users SET custom_qr=? WHERE id=?", (relative_path, user_id))
        conn.commit()
        conn.close()
        flash("QR uploaded successfully.")
    return redirect(url_for('admin'))

# ------------------ MAIN ------------------

@app.route('/')
def index():
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template('index.html', username=session['user'])

# ------------------ FILE CONVERSION ------------------

def check_file_trial(username):
    trial_expired, qr_file = check_trial_expired(username)
    return qr_file

@app.route('/convert_bs_to_ad', methods=['POST'])
def convert_bs_to_ad():
    if 'user' not in session:
        return redirect(url_for('login'))
    qr_file = check_file_trial(session['user'])
    if qr_file:
        return render_template('trial_expired.html', qr=qr_file)
    file = request.files.get('file')
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        df = pd.read_excel(filepath)
        date_col = None
        for col in df.columns:
            for val in df[col]:
                if pd.notna(val) and bs_to_ad(val):
                    date_col = col
                    break
            if date_col:
                break
        if not date_col:
            flash("No valid BS date column found in file.")
            return redirect(url_for('index'))
        df["Converted_Date"] = df[date_col].apply(bs_to_ad)
        output_path = os.path.join(app.config['UPLOAD_FOLDER'], "converted_bs_to_ad_" + filename)
        df.to_excel(output_path, index=False)
        return send_file(output_path, as_attachment=True)
    flash("Invalid file format. Please upload an Excel file.")
    return redirect(url_for('index'))

@app.route('/convert_ad_to_bs', methods=['POST'])
def convert_ad_to_bs():
    if 'user' not in session:
        return redirect(url_for('login'))
    qr_file = check_file_trial(session['user'])
    if qr_file:
        return render_template('trial_expired.html', qr=qr_file)
    file = request.files.get('file')
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        df = pd.read_excel(filepath)
        date_col = None
        for col in df.columns:
            for val in df[col]:
                if pd.notna(val) and ad_to_bs(val):
                    date_col = col
                    break
            if date_col:
                break
        if not date_col:
            flash("No valid AD date column found in file.")
            return redirect(url_for('index'))
        df["Miti"] = df[date_col].apply(ad_to_bs)
        output_path = os.path.join(app.config['UPLOAD_FOLDER'], "converted_ad_to_bs_" + filename)
        df.to_excel(output_path, index=False)
        return send_file(output_path, as_attachment=True)
    flash("Invalid file format. Please upload an Excel file.")
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
