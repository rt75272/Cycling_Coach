"""Flask routes for the local cycling coach."""

import calendar
import os
import secrets
from collections import defaultdict
from dataclasses import replace
from datetime import date, timedelta
from math import floor
from pathlib import Path

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for

from .db import close_db, get_db, init_db
from .training import RideFeedback, assess_training, estimate_ftp_from_zone2, make_plan, power_zones


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

    def feedback_input(required=False, default_actual=None):
        actual = number("actual_minutes", "Actual duration", 1, 1440, integer=True, required=required)
        if actual is None:
            actual = default_actual
        effort = number("effort", "Effort", 1, 10, integer=True, required=False)
        feeling = request.form.get("feeling", "").strip()
        performance = request.form.get("performance", "").strip()
        if feeling not in ("", "good", "okay", "poor") or performance not in ("", "better", "same", "worse"):
            raise ValueError("Choose valid ride feedback.")
        if actual is None and (effort is not None or feeling or performance):
            raise ValueError("Enter actual duration with ride feedback.")
        return actual, feeling or None, effort, performance or None

    def save_feedback(db, ride_id, feedback):
        db.execute(
            "INSERT INTO ride_feedback (ride_id, actual_minutes, feeling, effort, performance) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(ride_id) DO UPDATE SET actual_minutes=excluded.actual_minutes, feeling=excluded.feeling, "
            "effort=excluded.effort, performance=excluded.performance",
            (ride_id, *feedback),
        )

    def refresh_active_plans(db, today):
        profile = db.execute("SELECT * FROM profile WHERE id = 1").fetchone()
        events = db.execute(
            "SELECT events.* FROM events JOIN active_plans ON events.id=active_plans.event_id "
            "WHERE event_date > ? ORDER BY event_date, events.id", (today.isoformat(),)
        ).fetchall()
        if not profile or not events:
            return {}
        feedback = db.execute(
            "SELECT rides.ride_date, ride_feedback.* FROM ride_feedback JOIN rides ON rides.id=ride_feedback.ride_id "
            "WHERE rides.status='completed' AND rides.ride_date BETWEEN ? AND ?",
            ((today - timedelta(days=13)).isoformat(), today.isoformat()),
        ).fetchall()
        observations = [RideFeedback(date.fromisoformat(r["ride_date"]), r["actual_minutes"],
                                     r["feeling"], r["effort"], r["performance"]) for r in feedback]
        checkin = db.execute(
            "SELECT * FROM checkins WHERE checkin_date <= ? ORDER BY checkin_date DESC LIMIT 1", (today.isoformat(),)
        ).fetchone()
        latest = (date.fromisoformat(checkin["checkin_date"]), checkin["feeling"]) if checkin else None
        has_history = db.execute(
            "SELECT 1 FROM ride_feedback JOIN rides ON rides.id=ride_feedback.ride_id "
            "WHERE rides.status='completed' AND rides.ride_date <= ? LIMIT 1", (today.isoformat(),)
        ).fetchone() is not None
        adjustment = assess_training(today, profile["hours_per_week"], observations, latest, has_history)
        budget = adjustment.weekly_minutes if adjustment.weekly_minutes is not None else profile["hours_per_week"] * 60
        if len(events) > 1:
            adjustment = replace(adjustment, weekly_minutes=budget / len(events),
                                 explanation=adjustment.explanation + " The budget is shared across active event plans.")
        # Reserve time already completed this week and manually planned rides before adding generated sessions.
        used = defaultdict(int)
        monday = today - timedelta(days=today.weekday())
        reserved = db.execute(
            "SELECT rides.ride_date, COALESCE(ride_feedback.actual_minutes, rides.duration_minutes) AS minutes "
            "FROM rides LEFT JOIN ride_feedback ON rides.id=ride_feedback.ride_id "
            "WHERE (rides.status='completed' AND rides.ride_date BETWEEN ? AND ?) "
            "OR (rides.status='planned' AND rides.generated=0 AND rides.ride_date >= ?)",
            (monday.isoformat(), today.isoformat(), today.isoformat()),
        ).fetchall()
        for ride in reserved:
            used[date.fromisoformat(ride["ride_date"]).isocalendar()[:2]] += ride["minutes"]
        result = {}
        for event in events:
            completed = {
                row["ride_date"] for row in db.execute(
                    "SELECT ride_date FROM rides WHERE event_id=? AND generated=1 AND status='completed'", (event["id"],)
                )
            }
            candidates = make_plan(date.fromisoformat(event["event_date"]), today, profile["hours_per_week"],
                                   event["distance_km"], adjustment)
            db.execute("DELETE FROM rides WHERE event_id=? AND generated=1 AND status='planned' AND ride_date >= ?",
                       (event["id"], today.isoformat()))
            sessions = []
            for item in candidates:
                if item["date"] in completed:
                    continue
                week = date.fromisoformat(item["date"]).isocalendar()[:2]
                remaining = floor((budget - used[week]) / 5) * 5
                item["duration_minutes"] = min(item["duration_minutes"], remaining)
                if item["duration_minutes"] < 15:
                    continue
                if item["title"] == "Tempo intervals" and item["duration_minutes"] < 40:
                    item.update(title="Easy endurance", zone="Zone 2",
                                notes="Keep the effort easy; shorten or skip if fatigued.")
                used[week] += item["duration_minutes"]
                sessions.append(item)
            db.executemany(
                "INSERT INTO rides (ride_date, title, status, duration_minutes, zone, notes, event_id, generated) "
                "VALUES (?, ?, 'planned', ?, ?, ?, ?, 1)",
                [(s["date"], s["title"], s["duration_minutes"], s["zone"], s["notes"], event["id"]) for s in sessions],
            )
            explanation = adjustment.explanation
            if not sessions and not adjustment.paused:
                explanation += " No sessions fit the available budget or remaining dates."
            db.execute("UPDATE active_plans SET explanation=?, updated_on=? WHERE event_id=?",
                       (explanation, today.isoformat(), event["id"]))
            result[event["id"]] = (sessions, explanation)
        return result

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
        events = db.execute(
            "SELECT events.*, active_plans.explanation AS plan_explanation, active_plans.updated_on AS plan_updated_on FROM events "
            "LEFT JOIN active_plans ON events.id=active_plans.event_id ORDER BY event_date, events.id"
        ).fetchall()
        rides = db.execute(
            "SELECT rides.*, events.name AS event_name, ride_feedback.actual_minutes, ride_feedback.feeling, "
            "ride_feedback.effort, ride_feedback.performance FROM rides "
            "LEFT JOIN events ON rides.event_id = events.id LEFT JOIN ride_feedback ON rides.id=ride_feedback.ride_id "
            "ORDER BY ride_date, rides.id"
        ).fetchall()
        checkin = db.execute("SELECT * FROM checkins ORDER BY checkin_date DESC LIMIT 1").fetchone()
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
            "index.html", profile=profile, events=events, rides=rides, checkin=checkin,
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
            with db:
                db.execute(
                    "INSERT INTO profile (id, height_cm, weight_kg, ftp, ftp_source, zone2, hours_per_week) "
                    "VALUES (1, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET height_cm=excluded.height_cm, "
                    "weight_kg=excluded.weight_kg, ftp=excluded.ftp, ftp_source=excluded.ftp_source, "
                    "zone2=excluded.zone2, hours_per_week=excluded.hours_per_week",
                    (height, weight, ftp, source, zone2, hours),
                )
                refreshed = refresh_active_plans(db, date.today())
            flash("Profile saved." + (" Active event plans updated." if refreshed else ""), "success")
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
            if (status != "completed" or ride_date > date.today()) and any(
                request.form.get(field, "").strip() for field in ("actual_minutes", "feeling", "effort", "performance")
            ):
                raise ValueError("Ride feedback is only available for completed rides on or before today.")
            feedback = feedback_input(default_actual=duration if status == "completed" and ride_date <= date.today() else None)
            db = get_db()
            with db:
                cursor = db.execute(
                    "INSERT INTO rides (ride_date, title, status, duration_minutes, zone, notes) VALUES (?, ?, ?, ?, ?, ?)",
                    (ride_date.isoformat(), title, status, duration, zone, notes),
                )
                if status == "completed" and ride_date <= date.today():
                    save_feedback(db, cursor.lastrowid, (duration, *feedback[1:]))
                refreshed = refresh_active_plans(db, date.today())
            flash("Ride added." + (" Active event plans updated." if refreshed else ""), "success")
            return redirect(url_for("index", month=ride_date.isoformat()[:7]) + "#calendar")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("index") + "#rides")

    @app.post("/rides/<int:ride_id>/complete")
    def complete_ride(ride_id):
        db = get_db()
        ride = db.execute("SELECT * FROM rides WHERE id = ? AND status = 'planned'", (ride_id,)).fetchone()
        if ride is None:
            abort(404)
        try:
            feedback = feedback_input()
            if feedback[0] is not None and ride["ride_date"] > date.today().isoformat():
                raise ValueError("Ride feedback is only available on or after the ride date.")
            with db:
                db.execute("UPDATE rides SET status='completed' WHERE id=?", (ride_id,))
                if feedback[0] is not None:
                    save_feedback(db, ride_id, feedback)
                refreshed = refresh_active_plans(db, date.today())
            flash("Ride marked completed." + (" Active event plans updated." if refreshed else "") +
                  (" Add actual ride feedback to inform future adjustments." if feedback[0] is None else ""), "success")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("index", month=ride["ride_date"][:7]) + "#calendar")

    @app.post("/rides/<int:ride_id>/feedback")
    def update_feedback(ride_id):
        db = get_db()
        ride = db.execute("SELECT * FROM rides WHERE id=? AND status='completed'", (ride_id,)).fetchone()
        if ride is None:
            abort(404)
        try:
            if ride["ride_date"] > date.today().isoformat():
                raise ValueError("Ride feedback is only available on or after the ride date.")
            feedback = feedback_input(required=True)
            with db:
                save_feedback(db, ride_id, feedback)
                refreshed = refresh_active_plans(db, date.today())
            flash("Feedback saved." + (" Active event plans updated." if refreshed else ""), "success")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("index", month=ride["ride_date"][:7]) + "#calendar")

    @app.post("/checkin")
    def save_checkin():
        feeling = request.form.get("readiness", "")
        if feeling not in ("good", "okay", "tired", "unwell"):
            flash("Choose how you are feeling today.", "error")
            return redirect(url_for("index") + "#checkin")
        db = get_db()
        with db:
            db.execute(
                "INSERT INTO checkins VALUES (?, ?) ON CONFLICT(checkin_date) DO UPDATE SET feeling=excluded.feeling",
                (date.today().isoformat(), feeling),
            )
            refreshed = refresh_active_plans(db, date.today())
        message = "Check-in saved." + (" Active event plans updated." if refreshed else "")
        if feeling == "unwell":
            message += " Training is paused. Seek appropriate advice and update your check-in before resuming."
        flash(message, "success")
        return redirect(url_for("index") + "#checkin")

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
        with db:
            db.execute("INSERT OR IGNORE INTO active_plans (event_id) VALUES (?)", (event_id,))
            refreshed = refresh_active_plans(db, today)
        sessions, explanation = refreshed[event_id]
        flash(f"Generated {len(sessions)} sessions for {event['name']}. {explanation}", "success")
        return redirect(url_for("index", month=(sessions[0]["date"] if sessions else event["event_date"])[:7]) + "#calendar")

    return app
