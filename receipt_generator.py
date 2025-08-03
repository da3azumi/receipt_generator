from flask import Flask, render_template, request, redirect, url_for, session, flash, make_response
from flask_mail import Mail, Message
from weasyprint import HTML
import json
import os
import datetime
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Email config
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')

mail = Mail(app)

####################
# AUTH ROUTES
####################

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        conn = sqlite3.connect('database.db')
        c = conn.cursor()
        c.execute('SELECT id, password FROM users WHERE username = ?', (username,))
        result = c.fetchone()
        conn.close()

        if result and check_password_hash(result[1], password):
            session['user_id'] = result[0]
            session['username'] = username
            return redirect(url_for('index'))
        else:
            flash("Invalid username or password.", "danger")
            return redirect(url_for('login'))

    # ✅ This part runs for GET requests
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

####################
# MAIN APP ROUTES
####################

@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('form.html')

@app.route('/generate', methods=['POST'])
def generate():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    username = session['username']
    business_name = request.form.get('business_name', '')
    client_name = request.form.get('client_name', '')
    client_email = request.form['client_email']
    item_names = request.form.getlist('item_name')
    item_prices = request.form.getlist('item_price')
    date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    items = list(zip(item_names, item_prices))
    total = sum(float(price) for price in item_prices)

    # ✅ Save to database
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute(
        'INSERT INTO receipts (user_id, client_name, items, date, total) VALUES (?, ?, ?, ?, ?)',
        (
            session['user_id'],
            client_name,
            json.dumps(items),
            date,
            total
        )
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

    business_name = request.form['business_name']
    client_name = request.form['client_name']
    item_names = request.form.getlist('item_name')
    item_prices = request.form.getlist('item_price')
    date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    items = list(zip(item_names, item_prices))
    total = sum(float(price) for price in item_prices)

    rendered = render_template('receipt.html',
                               business_name=business_name,
                               client_name=client_name,
                               items=items,
                               total=total,
                               date=date,
                               item_names=item_names,
                               item_prices=item_prices)

    pdf = HTML(string=rendered).write_pdf()
    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = 'attachment; filename=receipt.pdf'
    return response

@app.route('/send-email/<int:receipt_id>', methods=['GET', 'POST'])
def send_email_existing(receipt_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute('SELECT client_name, items, date, total FROM receipts WHERE id = ? AND user_id = ?', (receipt_id, session['user_id']))
    result = c.fetchone()
    conn.close()

    if result is None:
        return "Receipt not found or you don't have access.", 404

    client_name, items_json, date, total = result
    items = json.loads(items_json)

    if request.method == 'POST':
        client_email = request.form['client_email']

        rendered = render_template('view_receipt.html',
                                   business_name="Your Business",
                                   client_name=client_name,
                                   items=items,
                                   total=total,
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

    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute('SELECT client_name, items, date, total FROM receipts WHERE id = ? AND user_id = ?', (receipt_id, session['user_id']))
    result = c.fetchone()
    conn.close()

    if result is None:
        return render_template('404.html'), 404

    client_name, items_json, date, total = result
    items = json.loads(items_json)

    return render_template('view_receipt.html',
                           business_name="Your Business",  # Or get from config
                           client_name=client_name,
                           items=items,
                           total=total,
                           date=date)

@app.route('/receipt/<int:receipt_id>/pdf')
def receipt_pdf(receipt_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute('SELECT client_name, items, date, total FROM receipts WHERE id = ? AND user_id = ?', (receipt_id, session['user_id']))
    result = c.fetchone()
    conn.close()

    if result is None:
        return "Receipt not found or you don't have access.", 404

    client_name, items_json, date, total = result
    items = json.loads(items_json)

    rendered = render_template('view_receipt.html',
           business_name="Your Business",
           client_name=client_name,
           items=items,
           total=total,
           date=date,
           receipt_id=receipt_id)

    pdf = HTML(string=rendered).write_pdf()
    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename=receipt_{receipt_id}.pdf'
    return response

@app.route('/history')
def history():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute('SELECT id, client_name, date, total FROM receipts WHERE user_id = ?', (session['user_id'],))
    receipts = c.fetchall()
    conn.close()

    return render_template('history.html', receipts=receipts)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        hashed_password = generate_password_hash(password)

        conn = sqlite3.connect('database.db')
        c = conn.cursor()
        try:
            c.execute('INSERT INTO users (username, password) VALUES (?, ?)', (username, hashed_password))
            conn.commit()
        except sqlite3.IntegrityError:
            flash("username already exists. please choose another.", "danger")
            return redirect(url_for('register')),
        finally:
            conn.close()

        return "Registration successful! You can now log in."
    return render_template('register.html')

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
