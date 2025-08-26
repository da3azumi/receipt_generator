import os
import json
import sqlite3
import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash, make_response, g, current_app
from werkzeug.security import generate_password_hash, check_password_hash
from flask_mail import Mail, Message
from weasyprint import HTML
from flask_sqlalchemy import SQLAlchemy

# --- Unified DB Path & app setup ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'database.db')   # use this single DB file
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or os.urandom(24)

# Mail config (your existing settings)
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
mail = Mail(app)

# --- DB helpers ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            client_name TEXT NOT NULL,
            items TEXT NOT NULL,
            date TEXT NOT NULL,
            total REAL NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    ''')
    conn.commit()
    conn.close()

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(error):
    db = g.pop('db', None)
    if db is not None:
        db.close()

#Initialize DB (dev convenience)
init_db()

#--- utility ---
def endpoint_exists(name: str) -> bool:
    return name in app.view_functions

# --- Add index route so base.html url_for('index') works ---
@app.route('/')
def index():
    # simple landing: redirect to home if logged in, else to login page
    if 'user_id' in session:
        return redirect(url_for('home'))
    return redirect(url_for('login'))

def get_receipt(receipt_id, user_id=None):
    """
    Returns a normalized receipt dict or None if not found.
    Items are returned as a list of dicts:
      {'name': ..., 'quantity': ..., 'price': '12.00', 'total': '24.00'}
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    if user_id:
        c.execute('SELECT id, user_id, client_name, items, date, total FROM receipts WHERE id = ? AND user_id = ?', (receipt_id, user_id))
    else:
        c.execute('SELECT id, user_id, client_name, items, date, total FROM receipts WHERE id = ?', (receipt_id,))

    row = c.fetchone()
    conn.close()

    if not row:
        return None

    rid, uid, client_name, items_json, date, total_value = row

    # items_json expected to be a JSON list of tuples/lists saved earlier
    # Example saved value: [["Item A", "100"], ["Item B", "20"]]
    try:
        raw_items = json.loads(items_json)
    except Exception:
        raw_items = []

    items = []
    grand_total = 0.0
    for entry in raw_items:
        # support multiple formats:
        # [name, price]  OR  [name, qty, price]
        try:
            if len(entry) >= 3:
                name = entry[0]
                qty = float(entry[1])
                price = float(entry[2])
            else:
                name = entry[0]
                qty = 1.0
                price = float(entry[1])
        except Exception:
            # fallback if values are unexpected
            name = str(entry[0])
            qty = 1.0
            try:
                price = float(entry[1])
            except Exception:
                price = 0.0

        line_total = qty * price
        # nicely format numbers as strings with 2 decimals
        def fmt(n):
            return f"{n:.2f}"

        # display quantity as int when appropriate
        if float(qty).is_integer():
            qty_display = int(qty)
        else:
            qty_display = qty

        items.append({
            'name': name,
            'quantity': qty_display,
            'price': fmt(price),
            'total': fmt(line_total)
        })
        grand_total += line_total

    # If the receipts.total column exists but may differ, we prefer computed grand_total for accuracy
    return {
        'id': rid,
        'user_id': uid,
        'client_name': client_name,
        'items': items,
        'date': date,
        'total': f"{grand_total:.2f}"
    }

####################
# AUTH ROUTES
####################

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        
        username = request.form['username']
        password = request.form['password']

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT id, password FROM users WHERE username = ?', (username,))
        result = c.fetchone()
        conn.close()

        if result and check_password_hash(result[1], password):
            session['user_id'] = result[0]
            session['username'] = username
            return redirect(url_for('home'))
        else:
            flash("Invalid username or password.", "danger")
            return redirect(url_for('login'))

    # ✅ This part runs for GET requests
    
    return render_template('login.html', app_name="FLEXMATE Invoice Generator", current_year=datetime.datetime.now().year)
# in receipt_generator.py (or wherever your route lives)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        hashed_password = generate_password_hash(password)
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        try:
            c.execute('INSERT INTO users (username, password) VALUES (?, ?)', (username, hashed_password))
            conn.commit()
        except sqlite3.IntegrityError:
            flash("username already exists. please choose another.", "danger")
            conn.close()
            return redirect(url_for('register'))
        conn.close()
        flash("Registration successful. Please log in.", "success")
        return redirect(url_for('login'))
    return render_template('register.html', app_name="FLEXMATE Invoice Generator", current_year=datetime.datetime.now().year)

@app.route('/home')
def home():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    templates_exists = endpoint_exists('templates')    # check if that endpoint is registered
    return render_template("home.html",
                           user_name=session.get('username'),
                            templates_exists=templates_exists,
                            has_receipts=endpoint_exists('recent_receipts'),
                            has_settings=endpoint_exists('settings'))   

# --- Example route for the receipt form (target of the icon) ---
@app.route("/receipt/new")
def new_receipt():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template("form.html")

# --- Fixed recent_receipts route: use DB columns and filter by user_id ---
@app.route('/recent_receipts')
def recent_receipts():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    db = get_db()
    rows = db.execute("""
        SELECT *
        FROM receipts
        WHERE user_id = ?
        ORDER BY date DESC
        LIMIT 10
    """, (session['user_id'],)).fetchall()

    # Normalize rows into plain dicts with predictable keys
    normalized = []
    for r in rows:
        # sqlite3.Row supports mapping with keys(), but be defensive
        rd = dict(r) if isinstance(r, sqlite3.Row) else dict(zip([c[0] for c in db.execute("PRAGMA table_info(receipts)").fetchall()], r))
        # prefer common names in this order
        client_name = rd.get('client_name') or rd.get('customer_name') or rd.get('client') or rd.get('name') or ''
        total_raw = rd.get('total') or rd.get('total_amount') or rd.get('amount') or rd.get('price') or 0
        # parse total to float safely
        try:
            total_val = float(total_raw)
        except Exception:
            total_val = 0.0
        date_val = rd.get('date') or rd.get('created_at') or rd.get('timestamp') or ''
        normalized.append({
            'id': rd.get('id'),
            'client_name': client_name,
            'total': total_val,
            'date': date_val
        })

    return render_template('recent_receipts.html', receipts=normalized)


# --- History page ---
'''@app.route("/history")
def history():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    # load receipts from DB for this user
    receipts = load_user_receipts(session['user_id'])
    return render_template("history.html", receipts=receipts)'''

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

####################
# MAIN APP ROUTES
####################

'''@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('form.html')'''

@app.route('/generate', methods=['POST'])
def generate():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    business_name = request.form.get('business_name', '')
    client_name = request.form.get('client_name', '')
    client_email = request.form.get('client_email', '')
    item_names = request.form.getlist('item_name')
    item_prices = request.form.getlist('item_price')
    date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    items = list(zip(item_names, item_prices))

    # safe total calculation
    total = 0.0
    for p in item_prices:
        try:
            total += float(p)
        except Exception:
            total += 0.0

    conn = sqlite3.connect(DB_PATH)   # <-- use variable, not string
    c = conn.cursor()
    c.execute(
        'INSERT INTO receipts (user_id, client_name, items, date, total) VALUES (?, ?, ?, ?, ?)',
        (session['user_id'], client_name, json.dumps(items), date, total)
    )
    conn.commit()
    conn.close()

    return render_template('receipt.html',
                           business_name=business_name,
                           client_name=client_name,
                           items=items,
                           total=total,
                           date=date,
                           item_names=item_names,
                           item_prices=item_prices,
                           client_email=client_email)

@app.route('/download-pdf', methods=['POST'])
def download_pdf():
    if 'username' not in session:
        return redirect(url_for('login'))

    business_name = request.form.get('business_name', '')
    client_name = request.form.get('client_name', '')
    item_names = request.form.getlist('item_name')
    item_prices = request.form.getlist('item_price')
    date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # Build items list as dicts (quantity assumed 1 for form submissions)
    items = []
    total = 0.0
    for name, price in zip(item_names, item_prices):
        try:
            p = float(price)
        except Exception:
            p = 0.0
        line_total = p * 1
        items.append({
            'name': name,
            'quantity': 1,
            'price': f"{p:.2f}",
            'total': f"{line_total:.2f}"
        })
        total += line_total

    rendered = render_template('receipt_pdf.html',
                               business_name=business_name,
                               client_name=client_name,
                               items=items,
                               total=f"{total:.2f}",
                               date=date)

    pdf = HTML(string=rendered).write_pdf()
    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = 'attachment; filename=receipt.pdf'
    return response

@app.route('/send-email/<int:receipt_id>', methods=['GET', 'POST'])
def send_email_existing(receipt_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT client_name, items, date, total FROM receipts WHERE id = ? AND user_id = ?', (receipt_id, session['user_id']))
    result = c.fetchone()
    conn.close()

    if result is None:
        return "Receipt not found or you don't have access.", 404

    client_name, items_json, date, total = result
    items = json.loads(items_json)
    # after items = json.loads(items_json)    (this is already in your function)
    # build items list in case items is a list of pairs from DB:
    normalized_items = []
    for entry in items:
        try:
            if len(entry) >= 3:
                name = entry[0]
                qty = float(entry[1])
                price = float(entry[2])
            else:
                name = entry[0]
                qty = 1.0
                price = float(entry[1])
        except Exception:
            name = str(entry[0])
            qty = 1.0
            price = 0.0

        line_total = qty * price
        normalized_items.append({
            'name': name,
            'quantity': int(qty) if float(qty).is_integer() else qty,
            'price': f"{price:.2f}",
            'total': f"{line_total:.2f}"
        })

    if request.method == 'POST':
        client_email = request.form['client_email']

        rendered = render_template('receipt_pdf.html',
                                   business_name="Your Business",
                                   client_name=client_name,
                                   items=normalized_items,
                                   total=f"{sum(float(it['total']) for it in normalized_items):.2f}",
                                   date=date,
                                   receipt_id=receipt_id)

        pdf_data = HTML(string=rendered).write_pdf()

        msg = Message(subject=f"Receipt from Your Business",
                      sender=app.config['MAIL_USERNAME'],
                      recipients=[client_email])
        msg.body = f"Hello {client_name},\n\nPlease find your receipt attached.\n\nThank you,\nYour Business"
        msg.attach("receipt.pdf", "application/pdf", pdf_data)
        try:
            mail.send(msg)
            flash("Email sent successfully!", "success")
        except Exception as e:
            flash(f"Failed to send email: {str(e)}", "danger")

        return redirect(url_for('view_receipt', receipt_id=receipt_id))

@app.route('/receipt/<int:receipt_id>')
def view_receipt(receipt_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    receipt = get_receipt(receipt_id, user_id=session['user_id'])
    if not receipt:
        return render_template('404.html'), 404

    return render_template('view_receipt.html',
                           business_name="Your Business",
                           client_name=receipt['client_name'],
                           items=receipt['items'],
                           total=receipt['total'],
                           date=receipt['date'],
                           receipt_id=receipt['id'])

@app.route('/receipt/<int:receipt_id>/pdf')
def receipt_pdf(receipt_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id, client_name, date, total FROM receipts WHERE user_id = ?', (session['user_id'],))
    receipts = c.fetchall()
    conn.close()

    receipt = get_receipt(receipt_id, user_id=session['user_id'])
    if not receipt:
        return "Receipt not found or you don't have access.", 404

    rendered = render_template('receipt_pdf.html',
                               business_name="Your Business",
                               client_name=receipt['client_name'],
                               items=receipt['items'],
                               total=receipt['total'],
                               date=receipt['date'],
                               receipt_id=receipt['id'])

    pdf = HTML(string=rendered).write_pdf()
    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename=receipt_{receipt_id}.pdf'
    return response

# alias so templates using download_receipt keep working
from flask import abort

@app.route('/receipt/<int:receipt_id>/download', endpoint='download_receipt')
def _download_receipt_alias(receipt_id):
    """
    Alias endpoint for older templates that call `url_for('download_receipt')`.
    Redirects to receipt_pdf if available, otherwise returns 404.
    """
    # If you have the receipt_pdf endpoint (GET /receipt/<id>/pdf) use it
    if 'receipt_pdf' in app.view_functions:
        return redirect(url_for('receipt_pdf', receipt_id=receipt_id))
    # If you had a different download endpoint name (e.g. receipt_pdf or download_pdf), add checks:
    if 'download_pdf' in app.view_functions:
        # download_pdf is POST-based in your code; can't call it via GET reliably.
        # So prefer returning 404 rather than incorrectly invoking.
        return abort(405)  # method not allowed — indicates wrong usage
    # nothing we can map to -> 404
    return abort(404)

@app.route('/history')
def history():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    db = get_db()
    rows = db.execute('SELECT id, client_name, date, total FROM receipts WHERE user_id = ?', (session['user_id'],)).fetchall()

    receipts = []
    for r in rows:
        receipts.append({
            'id': r['id'],
            'client_name': r['client_name'],
            'date': r['date'],
            'total': float(r['total'] or 0.0)
        })

    return render_template('history.html', receipts=receipts)

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    # Example POST: update business name (you can expand to save to DB)
    if request.method == 'POST':
        business_name = request.form.get('business_name', '').strip()
        # TODO: save business_name for the user (DB update)
        flash("Settings saved (not persisted in example).", "success")
        return redirect(url_for('settings'))

    # You can load existing values from your DB here
    existing = {
        'business_name': 'Your Business',
        'email': '',
    }
    return render_template('settings.html', settings=existing)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))