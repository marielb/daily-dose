from flask import Flask, render_template, request, redirect, session, flash, abort
from flask_sqlalchemy import SQLAlchemy
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
import smtplib
from email.message import EmailMessage
from datetime import date
import os

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///sms_app.db"
app.config["TEMPLATES_AUTO_RELOAD"] = True
db = SQLAlchemy(app)

# Twilio setup (add your credentials later)
TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_AUTH = os.getenv("TWILIO_AUTH")
TWILIO_NUMBER = os.getenv("TWILIO_NUMBER")

client = Client(TWILIO_SID, TWILIO_AUTH)

# --------------------------- DATABASE MODELS --------------------------- #
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    email = db.Column(db.String(100), unique=True)
    password = db.Column(db.String(100))
    rotation_length = db.Column(db.Integer, default=4)
    links = db.relationship("LinkLibrary", backref="user", lazy=True)
    students = db.relationship("Student", backref="user", lazy=True)


class LinkLibrary(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    week = db.Column(db.Integer)
    day = db.Column(db.Integer)
    link = db.Column(db.String(500))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    phone = db.Column(db.String(50))
    start_date = db.Column(db.Date)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


# --------------------------- HELPER FUNCTIONS --------------------------- #
def generate_schedule(start_date, rotation_length):
    """Generate weekday schedule with Week 1 Tue-Fri, others Mon-Fri"""
    days = []
    current_date = start_date
    for week in range(rotation_length):
        # Week 1 starts Tuesday
        if week == 0:
            weekday_offsets = [1, 2, 3, 4]  # Tue–Fri
        else:
            weekday_offsets = [0, 1, 2, 3, 4]  # Mon–Fri
        week_start = current_date + timedelta(days=(1 if week == 0 else 7 * week - 1))
        for offset in weekday_offsets:
            day_date = week_start + timedelta(days=offset)
            days.append(day_date)
    return days


def send_text(to, message):
    """Send text via Twilio"""
    try:
        client.messages.create(body=message, from_=TWILIO_NUMBER, to=to)
    except Exception as e:
        print(f"Error sending SMS to {to}: {e}")


def send_daily_texts():
    """Send texts at 7 AM daily"""
    today = datetime.now().date()
    students = Student.query.all()
    for s in students:
        faculty = s.user
        all_links = [l.link for l in LinkLibrary.query.filter_by(user_id=faculty.id).order_by(LinkLibrary.id).all()]
        rotation_days = generate_schedule(s.start_date, faculty.rotation_length)
        if today in rotation_days:
            idx = rotation_days.index(today)
            if idx < len(all_links):
                send_text(s.phone, f"Good morning! Here's today's link: {all_links[idx]}")
                print(f"Sent to {s.name}: {all_links[idx]}")
            else:
                print(f"No link available for {s.name} today.")



# --------------------------- ROUTES --------------------------- #
@app.route("/")
def home():
    if "user_id" not in session:
        return redirect("/login")
    user = User.query.get(session["user_id"])
    students = Student.query.filter_by(user_id=user.id).all()
    for student in students:
        week, day = _week_day_for_student(student.start_date)
        student.current_week = week
        student.current_day = day
    links = LinkLibrary.query.filter_by(user_id=user.id).all()
    return render_template("dashboard.html", user=user, students=students, links=links)


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]
        password = request.form["password"]
        new_user = User(name=name, email=email, password=password)
        db.session.add(new_user)
        db.session.commit()
        return redirect("/login")
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]
        user = User.query.filter_by(email=email, password=password).first()

        if user:
            session["user_id"] = user.id
            return redirect("/")
        else:
            print("Invalid credentials")
            flash("Invalid email or password. Please try again or register first.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("user_id", None)
    return redirect("/login")


@app.route("/update_rotation", methods=["POST"])
def update_rotation():
    user = User.query.get(session["user_id"])
    user.rotation_length = int(request.form["rotation_length"])
    db.session.commit()
    return redirect("/")


@app.route("/add_links", methods=["POST"])
def add_links():
    user = User.query.get(session["user_id"])
    LinkLibrary.query.filter_by(user_id=user.id).delete()
    links = request.form["links"].splitlines()
    week, day = 1, 1
    for link in links:
        db.session.add(LinkLibrary(week=week, day=day, link=link.strip(), user_id=user.id))
        day += 1
        if (week == 1 and day > 4) or (week > 1 and day > 5):
            week += 1
            day = 1
    db.session.commit()
    return redirect("/")


@app.route("/add_student", methods=["POST"])
def add_student():
    name = request.form["name"]
    phone = request.form["phone"]
    start_date = datetime.strptime(request.form["start_date"], "%Y-%m-%d").date()
    user_id = session["user_id"]
    new_student = Student(name=name, phone=phone, start_date=start_date, user_id=user_id)
    db.session.add(new_student)
    db.session.commit()
    return redirect("/")

@app.route("/edit_links", methods=["GET", "POST"])
def edit_links():
    user = User.query.get(session["user_id"])
    if request.method == "POST":
        # Clear old links
        LinkLibrary.query.filter_by(user_id=user.id).delete()
        links = request.form["links"].splitlines()
        week, day = 1, 1
        for link in links:
            db.session.add(LinkLibrary(week=week, day=day, link=link.strip(), user_id=user.id))
            day += 1
            if (week == 1 and day > 4) or (week > 1 and day > 5):
                week += 1
                day = 1
        db.session.commit()
        return redirect("/")
    else:
        # Pre-fill textarea with existing links
        links = LinkLibrary.query.filter_by(user_id=user.id).order_by(LinkLibrary.id).all()
        links_text = "\n".join([l.link for l in links])
        return render_template("edit_links.html", links_text=links_text)

@app.route("/edit_student/<int:student_id>", methods=["GET", "POST"])
def edit_student(student_id):
    student = Student.query.get_or_404(student_id)
    if request.method == "POST":
        student.name = request.form["name"]
        student.phone = request.form["phone"]
        student.start_date = datetime.strptime(request.form["start_date"], "%Y-%m-%d").date()
        db.session.commit()
        return redirect("/")
    return render_template("edit_student.html", student=student)


@app.route("/delete_student/<int:student_id>")
def delete_student(student_id):
    student = Student.query.get_or_404(student_id)
    db.session.delete(student)
    db.session.commit()
    return redirect("/")


@app.route("/clear_students", methods=["POST"])
def clear_students():
    user_id = session["user_id"]
    Student.query.filter_by(user_id=user_id).delete()
    db.session.commit()
    return redirect("/")

def _business_days_inclusive(start: date, end: date) -> int:
    """Count Mon–Fri between start..end inclusive. Returns 0 if end < start."""
    if end < start:
        return 0
    days = (end - start).days + 1
    full_weeks, extra = divmod(days, 7)
    count = full_weeks * 5
    start_wd = start.weekday()  # Mon=0..Sun=6
    for i in range(extra):
        if (start_wd + i) % 7 < 5:
            count += 1
    return count

def _week_day_for_student(start_date):
    """
    Week 1: Tue–Fri (4 days) -> 1..4
    Weeks 2+: Mon–Fri (5 days) -> 1..5
    """
    if start_date is None:
        abort(400, description="Student has no start_date set.")
    if hasattr(start_date, "date"):  # handle datetime
        start_date = start_date.date()

    today = date.today()
    # today = date(2025, 11, 14)  # set your test date here

    # First sendable day is the day AFTER start Monday
    first_send_day = start_date + timedelta(days=1)

    send_days = _business_days_inclusive(first_send_day, today)
    if send_days <= 0:
        # still on start Monday or before any sendable weekday
        return (1, 1)  # or (1, None) if you prefer to "skip send"

    if send_days <= 4:
        # In the short first week (Tue–Fri)
        return (1, send_days)

    # After the 4-day first week, group into 5-day weeks
    rem = send_days - 4
    week = 2 + (rem - 1) // 5
    day  = 1 + (rem - 1) % 5
    return week, day


@app.route("/resend_text/<int:student_id>", methods=["POST"])
def resend_text(student_id):
    user_id = session.get("user_id")
    if not user_id:
        flash("Please log in first.", "error")
        return redirect("/login")

    student = Student.query.get_or_404(student_id)

    # Compute the slot for “today”
    w, d = _week_day_for_student(student.start_date)

    # Look up the user’s link for that (week, day)
    link_row = LinkLibrary.query.filter_by(user_id=user_id, week=w, day=d).first()
    if not link_row:
        flash(f"No link found for week {w}, day {d}. Add one in your library.", "error")
        return redirect("/")

    # Send the SMS
    try:
        msg = f"Hi {student.name}, here’s your link for week {link_row.week}, day {link_row.day}: {link_row.link}"
        client.messages.create(body=msg, from_=TWILIO_NUMBER, to=student.phone)

        flash(f"Text re-sent to {student.name} for week {link_row.week}, day {link_row.day}.", "success")
    except TwilioRestException as e:
        flash(f"Failed to send text (Twilio {e.code}): {e.msg}", "error")
    except Exception as e:
        flash(f"Failed to send text: {e}", "error")

    return redirect("/")


# Test email resend route since text campaign isn't approved
@app.route("/resend_email/<int:student_id>", methods=["POST"])
def resend_email(student_id):
    user_id = session.get("user_id")
    if not user_id:
        flash("Please log in first.", "error")
        return redirect("/login")

    student = Student.query.get_or_404(student_id)

    # Compute the slot for “today”
    w, d = _week_day_for_student(student.start_date)

    # Look up the user’s link for that (week, day)
    link_row = LinkLibrary.query.filter_by(user_id=user_id, week=w, day=d).first()
    if not link_row:
        flash(f"No link found for week {w}, day {d}. Add one in your library.", "error")
        return redirect("/")

    # Send the email
    try:
        email_sender = os.getenv("EMAIL_SENDER")
        email_password = os.getenv("EMAIL_PASSWORD")
        # Build the email
        msg = EmailMessage()
        msg["Subject"] = f"Week {link_row.week}, Day {link_row.day} Link"
        msg["From"] = email_sender

        # For testing purposes, send to a fixed email address
        msg["To"] = "<FILL THIS OUT>"
        msg.set_content(
            f"Hi {student.name},\n\nHere’s your link for week {link_row.week}, day {link_row.day}:\n{link_row.link}\n\nHave a great day!"
        )

        # Send via SMTP (Gmail example)
        if email_sender and email_password:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
                smtp.login(email_sender, email_password)
                smtp.send_message(msg)

        flash(f"Email re-sent to {student.name} for week {link_row.week}, day {link_row.day}.", "success")

    except Exception as e:
        flash(f"Failed to send email: {e}", "error")

    return redirect("/")



@app.cli.command("init-db")
def init_db():
    with app.app_context():
        db.create_all()
    print("DB initialized.")

# --------------------------- SCHEDULER --------------------------- #
scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(send_daily_texts, "cron", hour=7, minute=0)
scheduler.start()

# --------------------------- INIT --------------------------- #
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)