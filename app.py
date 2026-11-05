"""app.py - Personal Expense Tracker (Flask). Run with: python app.py"""
import csv, io, os, re
from datetime import date, datetime
from functools import wraps
from xml.sax.saxutils import escape

from flask import (Flask, Response, flash, g, redirect, render_template,
                   request, session, url_for)
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import check_password_hash, generate_password_hash

import database

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
CATEGORIES = ["Food", "Education", "Transportation", "Shopping",
              "Entertainment", "Medical", "Bills", "Other"]
KINDS = {  # whitelist: table names are never taken from user input
    "expenses": {"field": "category", "title": "Expenses", "singular": "Expense", "label": "Category"},
    "income": {"field": "source", "title": "Income", "singular": "Income", "label": "Source"},
}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-secret-change-me")
app.config["DATABASE"] = os.path.join(BASE_DIR, "database", "expenses.db")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("PRODUCTION") == "1"
CSRFProtect(app)
app.teardown_appcontext(database.close_db)
os.makedirs(os.path.dirname(app.config["DATABASE"]), exist_ok=True)
with app.app_context():
    database.init_db()


def db():
    return database.get_db()


# ---------------- Helpers ----------------
@app.before_request
def load_user():
    g.user = None
    uid = session.get("user_id")
    if uid:
        g.user = db().execute("SELECT id, name, email FROM users WHERE id=?", (uid,)).fetchone()


def login_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if g.user is None:
            flash("Please log in first.", "error")
            return redirect(url_for("login"))
        return view(*a, **kw)
    return wrapped


def parse_amount(raw):
    try:
        v = round(float(raw), 2)
    except (TypeError, ValueError):
        return None
    return v if 0 < v <= 1_000_000_000 else None


def parse_date(raw):
    try:
        datetime.strptime(raw, "%Y-%m-%d")
        return raw
    except (TypeError, ValueError):
        return None


def parse_month(raw):
    try:
        datetime.strptime(raw, "%Y-%m")
        return raw
    except (TypeError, ValueError):
        return None


def this_month():
    return date.today().strftime("%Y-%m")


def total(kind, month=None):
    sql, p = f"SELECT COALESCE(SUM(amount),0) FROM {kind} WHERE user_id=?", [g.user["id"]]
    if month:
        sql += " AND strftime('%Y-%m', date)=?"
        p.append(month)
    return round(db().execute(sql, p).fetchone()[0], 2)


def get_budget(month):
    r = db().execute("SELECT amount FROM budgets WHERE user_id=? AND month=?",
                     (g.user["id"], month)).fetchone()
    return r["amount"] if r else None


def last_months(n=6):
    y, m, out = date.today().year, date.today().month, []
    for i in range(n - 1, -1, -1):
        mm, yy = m - i, y
        while mm <= 0:
            mm, yy = mm + 12, yy - 1
        out.append(f"{yy}-{mm:02d}")
    return out


def monthly_totals(kind, months):
    rows = db().execute(f"SELECT strftime('%Y-%m', date) m, SUM(amount) t FROM {kind} "
                        "WHERE user_id=? GROUP BY m", (g.user["id"],)).fetchall()
    d = {r["m"]: r["t"] for r in rows}
    return [round(d.get(m, 0), 2) for m in months]


def category_totals(month):
    return db().execute("SELECT category, SUM(amount) t FROM expenses WHERE user_id=? "
                        "AND strftime('%Y-%m', date)=? GROUP BY category ORDER BY t DESC",
                        (g.user["id"], month)).fetchall()


# ---------------- Authentication ----------------
@app.route("/")
def index():
    return redirect(url_for("dashboard" if g.user else "login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        pw, pw2 = request.form.get("password", ""), request.form.get("confirm", "")
        err = None
        if not 2 <= len(name) <= 60:
            err = "Name must be 2-60 characters."
        elif not EMAIL_RE.match(email) or len(email) > 120:
            err = "Enter a valid email address."
        elif len(pw) < 8:
            err = "Password must be at least 8 characters."
        elif pw != pw2:
            err = "Passwords do not match."
        elif db().execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            err = "That email is already registered."
        if err:
            flash(err, "error")
        else:
            db().execute("INSERT INTO users (name, email, password_hash) VALUES (?,?,?)",
                         (name, email, generate_password_hash(pw)))
            db().commit()
            flash("Account created. Please log in.", "success")
            return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        u = db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if u and check_password_hash(u["password_hash"], request.form.get("password", "")):
            session.clear()
            session["user_id"] = u["id"]
            return redirect(url_for("dashboard"))
        flash("Invalid email or password.", "error")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


# ---------------- Dashboard ----------------
@app.route("/dashboard")
@login_required
def dashboard():
    uid, month = g.user["id"], this_month()
    inc, exp = total("income"), total("expenses")
    month_spent, budget = total("expenses", month), get_budget(month)
    notes = []
    if budget is None:
        notes.append(("info", "You have not set a budget for this month yet."))
    else:
        pct = month_spent / budget * 100
        if pct >= 100:
            notes.append(("danger", f"Budget exceeded! You spent {pct:.0f}% of your monthly budget."))
        elif pct >= 80:
            notes.append(("warning", f"Warning: you have used {pct:.0f}% of your monthly budget."))
    recent = db().execute(
        "SELECT 'Expense' type, category label, amount, date, description FROM expenses WHERE user_id=? "
        "UNION ALL SELECT 'Income', source, amount, date, description FROM income WHERE user_id=? "
        "ORDER BY date DESC LIMIT 8", (uid, uid)).fetchall()
    months = last_months()
    cats = category_totals(month)
    chart = {"pieLabels": [c["category"] for c in cats], "pieData": [round(c["t"], 2) for c in cats],
             "months": months, "expenses": monthly_totals("expenses", months),
             "income": monthly_totals("income", months)}
    return render_template("dashboard.html", income=inc, expenses=exp, balance=round(inc - exp, 2),
                           budget=budget, month_spent=month_spent, month=month, recent=recent,
                           notes=notes, chart=chart,
                           pct=min(100, round(month_spent / budget * 100)) if budget else 0)


# ---------------- Income & Expenses (shared CRUD) ----------------
def make_routes(kind):
    cfg, f = KINDS[kind], KINDS[kind]["field"]

    @login_required
    def page():
        q = request.args.get("q", "").strip()
        cat = request.args.get("category", "").strip()
        start, end = request.args.get("start", ""), request.args.get("end", "")
        sql, p = f"SELECT * FROM {kind} WHERE user_id=?", [g.user["id"]]
        if q:
            sql += f" AND (description LIKE ? OR {f} LIKE ?)"
            p += [f"%{q}%"] * 2
        if cat:
            sql += f" AND {f} LIKE ?"
            p.append(cat)
        if parse_date(start):
            sql += " AND date>=?"
            p.append(start)
        if parse_date(end):
            sql += " AND date<=?"
            p.append(end)
        rows = db().execute(sql + " ORDER BY date DESC, id DESC", p).fetchall()
        edit = None
        if request.args.get("edit", "").isdigit():
            edit = db().execute(f"SELECT * FROM {kind} WHERE id=? AND user_id=?",
                                (request.args["edit"], g.user["id"])).fetchone()
        return render_template(f"{kind}.html", kind=kind, cfg=cfg, rows=rows, edit=edit,
                               choices=CATEGORIES if kind == "expenses" else None,
                               today=date.today().isoformat(),
                               total=round(sum(r["amount"] for r in rows), 2),
                               filters={"q": q, "category": cat, "start": start, "end": end})

    @login_required
    def save():
        val = request.form.get(f, "").strip()
        amount = parse_amount(request.form.get("amount"))
        d = parse_date(request.form.get("date"))
        desc = request.form.get("description", "").strip()[:200]
        err = None
        if kind == "expenses" and val not in CATEGORIES:
            err = "Choose a valid category."
        elif kind == "income" and not 1 <= len(val) <= 60:
            err = "Source is required (max 60 characters)."
        elif amount is None:
            err = "Enter a valid amount greater than 0."
        elif d is None:
            err = "Enter a valid date."
        if err:
            flash(err, "error")
            return redirect(url_for(kind))
        tid = request.form.get("id", "")
        if tid.isdigit():
            db().execute(f"UPDATE {kind} SET {f}=?, amount=?, date=?, description=? "
                         "WHERE id=? AND user_id=?", (val, amount, d, desc, tid, g.user["id"]))
            flash(f"{cfg['singular']} updated.", "success")
        else:
            db().execute(f"INSERT INTO {kind} (user_id, {f}, amount, date, description) "
                         "VALUES (?,?,?,?,?)", (g.user["id"], val, amount, d, desc))
            flash(f"{cfg['singular']} added.", "success")
        db().commit()
        return redirect(url_for(kind))

    @login_required
    def delete(id):
        db().execute(f"DELETE FROM {kind} WHERE id=? AND user_id=?", (id, g.user["id"]))
        db().commit()
        flash("Record deleted.", "success")
        return redirect(url_for(kind))

    app.add_url_rule(f"/{kind}", kind, page)
    app.add_url_rule(f"/{kind}/save", f"save_{kind}", save, methods=["POST"])
    app.add_url_rule(f"/{kind}/<int:id>/delete", f"delete_{kind}", delete, methods=["POST"])


for _k in KINDS:
    make_routes(_k)


# ---------------- Budget ----------------
@app.route("/budget", methods=["GET", "POST"])
@login_required
def budget():
    if request.method == "POST":
        month = parse_month(request.form.get("month"))
        amount = parse_amount(request.form.get("amount"))
        if not month or amount is None:
            flash("Enter a valid month and a budget amount greater than 0.", "error")
        else:
            db().execute("INSERT INTO budgets (user_id, month, amount) VALUES (?,?,?) "
                         "ON CONFLICT(user_id, month) DO UPDATE SET amount=excluded.amount",
                         (g.user["id"], month, amount))
            db().commit()
            flash("Budget saved.", "success")
        return redirect(url_for("budget"))
    month = this_month()
    spent, bud = total("expenses", month), get_budget(month)
    history = db().execute(
        "SELECT b.month, b.amount, COALESCE((SELECT SUM(amount) FROM expenses e WHERE e.user_id=b.user_id "
        "AND strftime('%Y-%m', e.date)=b.month),0) spent FROM budgets b WHERE b.user_id=? "
        "ORDER BY b.month DESC LIMIT 12", (g.user["id"],)).fetchall()
    return render_template("budget.html", month=month, spent=spent, budget=bud, history=history,
                           pct=round(spent / bud * 100) if bud else 0)


# ---------------- Reports ----------------
def report_data(month):
    uid = g.user["id"]
    txs = db().execute(
        "SELECT date, 'Expense' type, category label, amount, description FROM expenses "
        "WHERE user_id=? AND strftime('%Y-%m', date)=? UNION ALL "
        "SELECT date, 'Income', source, amount, description FROM income "
        "WHERE user_id=? AND strftime('%Y-%m', date)=? ORDER BY date", (uid, month, uid, month)).fetchall()
    inc, exp = total("income", month), total("expenses", month)
    return {"month": month, "income": inc, "expenses": exp, "balance": round(inc - exp, 2),
            "budget": get_budget(month), "cats": category_totals(month), "txs": txs}


@app.route("/reports")
@login_required
def reports():
    month = parse_month(request.args.get("month")) or this_month()
    return render_template("reports.html", r=report_data(month))


@app.route("/reports/pdf")
@login_required
def report_pdf():
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    month = parse_month(request.args.get("month")) or this_month()
    r, st = report_data(month), getSampleStyleSheet()

    def tbl(data, widths=None):
        t = Table(data, colWidths=widths, repeatRows=1)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4f46e5")),
                               ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                               ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                               ("FONTSIZE", (0, 0), (-1, -1), 9)]))
        return t
    buf = io.BytesIO()
    els = [Paragraph(f"Monthly Report - {month}", st["Title"]),
           Paragraph(f"Prepared for {escape(g.user['name'])}", st["Normal"]), Spacer(1, 12),
           tbl([["Income", "Expenses", "Balance", "Budget"],
                [f"{r['income']:.2f}", f"{r['expenses']:.2f}", f"{r['balance']:.2f}",
                 f"{r['budget']:.2f}" if r["budget"] else "Not set"]]), Spacer(1, 14),
           Paragraph("Expenses by Category", st["Heading2"]),
           tbl([["Category", "Amount"]] + [[c["category"], f"{c['t']:.2f}"] for c in r["cats"]]
               if r["cats"] else [["Category", "Amount"], ["No expenses", "-"]]), Spacer(1, 14),
           Paragraph("Transactions", st["Heading2"]),
           tbl([["Date", "Type", "Category/Source", "Amount", "Description"]] +
               [[t["date"], t["type"], t["label"], f"{t['amount']:.2f}", (t["description"] or "")[:40]]
                for t in r["txs"]] if r["txs"] else [["Date", "Type", "Category/Source", "Amount", "Description"],
                                                   ["-", "-", "-", "-", "No transactions"]])]
    SimpleDocTemplate(buf, pagesize=A4).build(els)
    return Response(buf.getvalue(), mimetype="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename=report_{month}.pdf"})


def safe_cell(v):
    """Stops spreadsheet formula injection (cells starting with = + - @)."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@") else s


@app.route("/reports/csv")
@login_required
def export_csv():
    uid = g.user["id"]
    start = parse_date(request.args.get("start")) or "0000-01-01"
    end = parse_date(request.args.get("end")) or "9999-12-31"
    rows = db().execute(
        "SELECT date, 'Expense' type, category label, amount, description FROM expenses "
        "WHERE user_id=? AND date BETWEEN ? AND ? UNION ALL "
        "SELECT date, 'Income', source, amount, description FROM income "
        "WHERE user_id=? AND date BETWEEN ? AND ? ORDER BY date", (uid, start, end, uid, start, end)).fetchall()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Date", "Type", "Category/Source", "Amount", "Description"])
    for r in rows:
        w.writerow([r["date"], r["type"], safe_cell(r["label"]), r["amount"], safe_cell(r["description"])])
    return Response("\ufeff" + out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=transactions.csv"})


# ---------------- Profile ----------------
@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        taken = db().execute("SELECT 1 FROM users WHERE email=? AND id<>?", (email, g.user["id"])).fetchone()
        if not 2 <= len(name) <= 60 or not EMAIL_RE.match(email):
            flash("Enter a valid name and email.", "error")
        elif taken:
            flash("That email is already in use.", "error")
        else:
            db().execute("UPDATE users SET name=?, email=? WHERE id=?", (name, email, g.user["id"]))
            db().commit()
            flash("Profile updated.", "success")
        return redirect(url_for("profile"))
    return render_template("profile.html")


@app.route("/profile/password", methods=["POST"])
@login_required
def change_password():
    u = db().execute("SELECT password_hash FROM users WHERE id=?", (g.user["id"],)).fetchone()
    new = request.form.get("new_password", "")
    if not check_password_hash(u["password_hash"], request.form.get("current_password", "")):
        flash("Current password is incorrect.", "error")
    elif len(new) < 8 or new != request.form.get("confirm", ""):
        flash("New password must be 8+ characters and match the confirmation.", "error")
    else:
        db().execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new), g.user["id"]))
        db().commit()
        flash("Password changed.", "success")
    return redirect(url_for("profile"))


if __name__ == "__main__":
    app.run(debug=os.environ.get("PRODUCTION") != "1")
