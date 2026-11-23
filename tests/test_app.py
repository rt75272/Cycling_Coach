from datetime import date, timedelta

import pytest

from coach.app import create_app
from coach.db import get_db


@pytest.fixture
def app(tmp_path):
    return create_app({"TESTING": True, "DATABASE": str(tmp_path / "test.sqlite3"), "SECRET_KEY": "test"})


@pytest.fixture
def client(app):
    client = app.test_client()
    client.get("/")
    return client


def post(client, path, **data):
    with client.session_transaction() as session:
        data["csrf_token"] = session["csrf_token"]
    return client.post(path, data=data, follow_redirects=True)


def test_profile_zone2_estimate_and_validation(client, app):
    response = post(client, "/profile", height_cm="175", weight_kg="70", hours_per_week="6", zone2="131")
    assert b"200 W FTP" in response.data
    assert b"Estimated from 131 W zone 2" in response.data
    assert b'value="131"' in response.data
    response = post(client, "/profile", height_cm="175", weight_kg="nan", hours_per_week="6", ftp="200")
    assert b"Weight must be between" in response.data
    with app.app_context():
        assert get_db().execute("SELECT weight_kg FROM profile").fetchone()[0] == 70


def test_pounds_convert_to_kg_and_override_existing_weight(client, app):
    response = post(client, "/profile", height_cm="175", weight_lb="175", hours_per_week="6", ftp="200")
    assert b'value="79.38"' in response.data
    assert b"2.52 W/kg" in response.data
    with app.app_context():
        assert get_db().execute("SELECT weight_kg FROM profile").fetchone()[0] == 79.38
    response = post(client, "/profile", height_cm="175", weight_kg="70", weight_lb="175",
                    hours_per_week="6", ftp="200")
    assert b'value="79.38"' in response.data
    response = post(client, "/profile", height_cm="175", weight_lb="10", hours_per_week="6", ftp="200")
    assert b"Weight must be between 20 and 350 kg after conversion." in response.data
    with app.app_context():
        assert get_db().execute("SELECT weight_kg FROM profile").fetchone()[0] == 79.38


def test_imperial_height_is_converted_and_takes_precedence(client, app):
    response = post(client, "/profile", height_ft="5", height_in="9.5", weight_kg="70",
                    hours_per_week="6", ftp="200")
    assert b'value="176.53"' in response.data
    with app.app_context():
        assert get_db().execute("SELECT height_cm FROM profile").fetchone()[0] == 176.53
    post(client, "/profile", height_cm="175", height_ft="6", weight_kg="70",
         hours_per_week="6", ftp="200")
    with app.app_context():
        assert get_db().execute("SELECT height_cm FROM profile").fetchone()[0] == 182.88


@pytest.mark.parametrize(("height", "message"), [
    ({"height_in": "5"}, b"Enter feet along with inches."),
    ({"height_ft": "5", "height_in": "12"}, b"Inches must be less than 12."),
    ({"height_ft": "5", "height_in": "-1"}, b"Height in inches must be between"),
    ({"height_ft": "1"}, b"Height must be between 50 and 250 cm after conversion."),
    ({"height_ft": "8", "height_in": "7"}, b"Height must be between 50 and 250 cm after conversion."),
    ({"height_ft": "5.5"}, b"Enter a valid Height in feet."),
    ({"height_ft": "5", "height_in": "nan"}, b"Height in inches must be between"),
])
def test_invalid_imperial_height_is_rejected(client, app, height, message):
    response = post(client, "/profile", weight_kg="70", hours_per_week="6", ftp="200", **height)
    assert message in response.data
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM profile").fetchone()[0] == 0


def test_event_plan_refresh_preserves_completed_and_manual_rides(client, app):
    post(client, "/profile", height_cm="175", weight_kg="70", hours_per_week="6", ftp="200")
    post(client, "/events", name="Race", event_date=(date.today() + timedelta(days=30)).isoformat(), distance_km="50")
    post(client, "/rides", ride_date=date.today().isoformat(), title="Manual", status="planned", duration_minutes="60")
    response = post(client, "/events/1/plan")
    assert b"Generated" in response.data
    with app.app_context():
        db = get_db()
        generated = db.execute("SELECT id, ride_date FROM rides WHERE generated = 1 ORDER BY ride_date").fetchall()
        first_id, first_date = generated[0]
        original_count = len(generated)
    post(client, f"/rides/{first_id}/complete")
    post(client, "/events/1/plan")
    with app.app_context():
        db = get_db()
        rides = db.execute("SELECT * FROM rides").fetchall()
        assert len(rides) == original_count + 1
        assert sum(r["ride_date"] == first_date and r["generated"] for r in rides) == 1
        assert any(r["id"] == first_id and r["status"] == "completed" for r in rides)
        assert any(r["title"] == "Manual" for r in rides)


def test_generated_plan_is_visible_across_calendar_months(client, app):
    event_date = date.today() + timedelta(days=100)
    post(client, "/profile", height_cm="175", weight_kg="70", hours_per_week="6", ftp="200")
    post(client, "/events", name="Race", event_date=event_date.isoformat(), distance_km="50")
    response = post(client, "/events/1/plan")
    with app.app_context():
        generated = get_db().execute(
            "SELECT id, ride_date FROM rides WHERE generated = 1 ORDER BY ride_date"
        ).fetchall()
    assert len({ride["ride_date"][:7] for ride in generated}) >= 2
    assert response.request.args["month"] == generated[0]["ride_date"][:7]
    for month in {ride["ride_date"][:7] for ride in generated}:
        page = client.get(f"/?month={month}").get_data(as_text=True)
        for ride in generated:
            if ride["ride_date"].startswith(month):
                cell = page.split(f'data-date="{ride["ride_date"]}"', 1)[1].split("</td>", 1)[0]
                assert f'data-ride-id="{ride["id"]}"' in cell
    page = client.get(f"/?month={event_date.isoformat()[:7]}").get_data(as_text=True)
    event_cell = page.split(f'data-date="{event_date.isoformat()}"', 1)[1].split("</td>", 1)[0]
    assert 'class="calendar-event"' in event_cell
    assert "Race" in event_cell


def test_calendar_manual_rides_and_completion_keep_the_selected_month(client, app):
    ride_date = date.today() + timedelta(days=80)
    response = post(client, "/rides", ride_date=ride_date.isoformat(), title="Future ride",
                    status="planned", duration_minutes="60", notes="My notes")
    month = ride_date.isoformat()[:7]
    assert response.request.args["month"] == month
    page = response.get_data(as_text=True)
    assert page.count("Future ride") == 1
    assert "My notes" in page
    with app.app_context():
        ride_id = get_db().execute("SELECT id FROM rides").fetchone()[0]
    response = post(client, f"/rides/{ride_id}/complete")
    assert response.request.args["month"] == month
    page = response.get_data(as_text=True)
    assert f'class="calendar-ride completed" data-ride-id="{ride_id}"' in page
    assert "My notes" in page


def test_calendar_month_navigation_and_invalid_months(client):
    page = client.get("/?month=2032-02").get_data(as_text=True)
    assert "February 2032" in page
    assert 'data-date="2032-02-29"' in page
    assert 'month=2032-01#calendar' in page
    assert 'month=2032-03#calendar' in page
    assert client.get("/?month=0001-01").status_code == 200
    assert client.get("/?month=9999-12").status_code == 200
    for month in ("", "2032-13", "2032-2", "0000-01", "oops"):
        assert client.get(f"/?month={month}").status_code == 400


def test_calendar_shows_overlapping_rides_and_event_on_same_day(client, app):
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO events (name, event_date) VALUES (?, ?)", ("Race day", "2032-02-29"))
        db.executemany(
            "INSERT INTO rides (ride_date, title, status, duration_minutes) VALUES (?, ?, ?, ?)",
            [("2032-02-29", "Morning", "planned", 60), ("2032-02-29", "Evening", "planned", 30)],
        )
        db.commit()
    page = client.get("/?month=2032-02").get_data(as_text=True)
    cell = page.split('data-date="2032-02-29"', 1)[1].split("</td>", 1)[0]
    assert all(value in cell for value in ("Race day", "Morning", "Evening"))
    assert cell.count('class="calendar-ride planned"') == 2


def test_validation_and_form_token(client, app):
    assert client.post("/rides", data={}).status_code == 400
    response = post(client, "/events", name="Yesterday", event_date=(date.today() - timedelta(days=1)).isoformat())
    assert b"Event date must be in the future" in response.data
    response = post(client, "/rides", ride_date="invalid", title="Ride", status="completed", duration_minutes="60")
    assert b"Enter a valid ride date" in response.data
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM rides").fetchone()[0] == 0
        assert get_db().execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
