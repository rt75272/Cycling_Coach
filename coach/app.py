"""Flask routes for the local cycling coach."""

import calendar
import os
import secrets
from collections import defaultdict
from datetime import date
from pathlib import Path

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for

from .db import close_db, get_db, init_db
from .training import estimate_ftp_from_zone2, make_plan, power_zones


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("CYCLING_COACH_SECRET") or secrets.token_hex(32),
        DATABASE=str(Path(app.instance_path) / "cycling_coach.sqlite3"),
        MAX_CONTENT_LENGTH=64 * 1024,
    )
    if test_config:
        app.config.update(test_config)
    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()

    @app.before_request
    def check_csrf():
        if request.method == "POST":
            expected = session.get("csrf_token", "")
            supplied = request.form.get("csrf_token", "")
            if not expected or not supplied.isascii() or not secrets.compare_digest(expected, supplied):
                abort(400, "Invalid form token. Reload the page and try again.")

    @app.context_processor
    def form_token():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_urlsafe(32)
        return {"csrf_token": session["csrf_token"]}

    def text(field, label, maximum=200, required=True):
        value = request.form.get(field, "").strip()
        if (required and not value) or len(value) > maximum:
            raise ValueError(f"{label} must be {'1 to ' if required else 'at most '}{maximum} characters.")
        return value

    def number(field, label, minimum, maximum, integer=False, required=True):
        raw = request.form.get(field, "").strip()
        if not raw and not required:
            return None
        try:
            value = int(raw) if integer else float(raw)
        except ValueError:
            raise ValueError(f"Enter a valid {label}.") from None
        if not minimum <= value <= maximum:
            raise ValueError(f"{label} must be between {minimum} and {maximum}.")
        return value

    def form_date(field, label):
        try:
            return date.fromisoformat(request.form.get(field, ""))
        except ValueError:
            raise ValueError(f"Enter a valid {label}.") from None

    @app.get("/")
    def index():
        today = date.today()
        month_arg = request.args.get("month")
        if month_arg is None:
            month = today.replace(day=1)
        else:
            try:
                month = date.fromisoformat(month_arg + "-01")
                if month.isoformat()[:7] != month_arg:
                    raise ValueError
            except ValueError:
                abort(400, "Invalid calendar month. Use YYYY-MM.")

        db = get_db()
        profile = db.execute("SELECT * FROM profile WHERE id = 1").fetchone()
        events = db.execute("SELECT * FROM events ORDER BY event_date, id").fetchall()
        rides = db.execute(
            "SELECT rides.*, events.name AS event_name FROM rides "
            "LEFT JOIN events ON rides.event_id = events.id ORDER BY ride_date, rides.id"
        ).fetchall()
        rides_by_date = defaultdict(list)
        events_by_date = defaultdict(list)
        for ride in rides:
            rides_by_date[ride["ride_date"]].append(ride)
        for event in events:
            events_by_date[event["event_date"]].append(event)
        weeks = []
        for week in calendar.Calendar().monthdayscalendar(month.year, month.month):
            days = []
            for day in week:
                if not day:
                    days.append(None)
                    continue
                day_date = month.replace(day=day).isoformat()
                days.append({
                    "day": day, "date": day_date,
                    "rides": rides_by_date[day_date], "events": events_by_date[day_date],
                })
            weeks.append(days)
        previous = None if month == date.min else (
            date(month.year - 1, 12, 1) if month.month == 1 else date(month.year, month.month - 1, 1)
        ).isoformat()[:7]
        following = None if month.year == 9999 and month.month == 12 else (
            date(month.year + 1, 1, 1) if month.month == 12 else date(month.year, month.month + 1, 1)
        ).isoformat()[:7]
        return render_template(
            "index.html", profile=profile, events=events, rides=rides,
            past_rides=[ride for ride in rides if ride["ride_date"] < today.isoformat()],
            weeks=weeks, month_title=f"{calendar.month_name[month.month]} {month.year}",
            previous_month=previous, next_month=following, current_month=today.isoformat()[:7],
            zones=power_zones(profile["ftp"]) if profile else [], today=today.isoformat(),
        )

    @app.post("/profile")
    def save_profile():
        try:
            feet = number("height_ft", "Height in feet", 1, 8, integer=True, required=False)
            inches = number("height_in", "Height in inches", 0, 12, required=False)
            if inches is not None and feet is None:
                raise ValueError("Enter feet along with inches.")
            if inches is not None and inches >= 12:
                raise ValueError("Inches must be less than 12.")
            if feet is None:
                height = number("height_cm", "Height", 50, 250)
            else:
                height = (feet * 12 + (inches if inches is not None else 0)) * 2.54
                if not 50 <= height <= 250:
                    raise ValueError("Height must be between 50 and 250 cm after conversion.")
                height = round(height, 2)
            weight_lb = number("weight_lb", "Weight in pounds", 0.01, 1000, required=False)
            if weight_lb is None:
                weight = number("weight_kg", "Weight", 20, 350)
            else:
                weight = weight_lb * 0.45359237
                if not 20 <= weight <= 350:
                    raise ValueError("Weight must be between 20 and 350 kg after conversion.")
                weight = round(weight, 2)
            hours = number("hours_per_week", "Weekly hours", 1, 30)
            ftp = number("ftp", "FTP", 50, 1000, integer=True, required=False)
            zone2 = number("zone2", "Zone 2 power", 33, 655, integer=True, required=False)
            if ftp is None and zone2 is None:
                raise ValueError("Enter FTP or a representative zone 2 power.")
            if ftp is None:
                ftp = estimate_ftp_from_zone2(zone2)
                source = f"Estimated from {zone2} W zone 2"
            else:
                source = "FTP entered directly"
                zone2 = None
            db = get_db()
            db.execute(
                "INSERT INTO profile (id, height_cm, weight_kg, ftp, ftp_source, zone2, hours_per_week) "
                "VALUES (1, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET height_cm=excluded.height_cm, "
                "weight_kg=excluded.weight_kg, ftp=excluded.ftp, ftp_source=excluded.ftp_source, "
                "zone2=excluded.zone2, hours_per_week=excluded.hours_per_week",
                (height, weight, ftp, source, zone2, hours),
            )
            db.commit()
            flash("Profile saved. Regenerate an event plan to apply changes to its future sessions.", "success")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("index") + "#profile")

    @app.post("/rides")
    def add_ride():
        try:
            ride_date = form_date("ride_date", "ride date")
            title = text("title", "Ride title")
            status = request.form.get("status", "")
            if status not in ("planned", "completed"):
                raise ValueError("Choose a valid ride status.")
            duration = number("duration_minutes", "Duration", 1, 1440, integer=True)
            zone = text("zone", "Zone", 100, required=False)
            notes = text("notes", "Notes", 2000, required=False)
            db = get_db()
            db.execute(
                "INSERT INTO rides (ride_date, title, status, duration_minutes, zone, notes) VALUES (?, ?, ?, ?, ?, ?)",
                (ride_date.isoformat(), title, status, duration, zone, notes),
            )
            db.commit()
            flash("Ride added.", "success")
            return redirect(url_for("index", month=ride_date.isoformat()[:7]) + "#calendar")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("index") + "#rides")

    @app.post("/rides/<int:ride_id>/complete")
    def complete_ride(ride_id):
        db = get_db()
        result = db.execute("UPDATE rides SET status = 'completed' WHERE id = ? AND status = 'planned'", (ride_id,))
        db.commit()
        if not result.rowcount:
            abort(404)
        ride = db.execute("SELECT ride_date FROM rides WHERE id = ?", (ride_id,)).fetchone()
        flash("Ride marked completed. Its planned duration and notes are retained.", "success")
        return redirect(url_for("index", month=ride["ride_date"][:7]) + "#calendar")

    @app.post("/events")
    def add_event():
        try:
            name = text("name", "Event name")
            event_date = form_date("event_date", "event date")
            if event_date <= date.today():
                raise ValueError("Event date must be in the future.")
            distance = number("distance_km", "Distance", 0.1, 1000, required=False)
            details = text("details", "Details", 2000, required=False)
            db = get_db()
            db.execute(
                "INSERT INTO events (name, event_date, distance_km, details) VALUES (?, ?, ?, ?)",
                (name, event_date.isoformat(), distance, details),
            )
            db.commit()
            flash("Event added. Generate its plan when you are ready.", "success")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("index") + "#events")

    @app.post("/events/<int:event_id>/plan")
    def generate_plan(event_id):
        db = get_db()
        event = db.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        if event is None:
            abort(404)
        profile = db.execute("SELECT * FROM profile WHERE id = 1").fetchone()
        if profile is None:
            flash("Save your profile before generating a plan.", "error")
            return redirect(url_for("index") + "#profile")
        today = date.today()
        event_date = date.fromisoformat(event["event_date"])
        if event_date <= today:
            flash("Plans can only be generated for future events.", "error")
            return redirect(url_for("index") + "#events")
        completed_dates = {
            row["ride_date"] for row in db.execute(
                "SELECT ride_date FROM rides WHERE event_id = ? AND generated = 1 AND status = 'completed'",
                (event_id,),
            )
        }
        sessions = [
            item for item in make_plan(event_date, today, profile["hours_per_week"], event["distance_km"])
            if item["date"] not in completed_dates
        ]
        if not sessions:
            flash("There are no remaining training days to generate before this event.", "error")
            return redirect(url_for("index") + "#events")
        with db:
            db.execute(
                "DELETE FROM rides WHERE event_id = ? AND generated = 1 AND status = 'planned' AND ride_date >= ?",
                (event_id, today.isoformat()),
            )
            db.executemany(
                "INSERT INTO rides (ride_date, title, status, duration_minutes, zone, notes, event_id, generated) "
                "VALUES (?, ?, 'planned', ?, ?, ?, ?, 1)",
                [(s["date"], s["title"], s["duration_minutes"], s["zone"], s["notes"], event_id) for s in sessions],
            )
        flash(f"Generated {len(sessions)} sessions for {event['name']}.", "success")
        return redirect(url_for("index", month=sessions[0]["date"][:7]) + "#calendar")

    return app
