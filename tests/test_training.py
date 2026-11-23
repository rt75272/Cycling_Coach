from datetime import date, timedelta

from coach.training import estimate_ftp_from_zone2, make_plan, power_zones


def test_power_zones_cover_whole_watts_without_gaps():
    zones = power_zones(200)
    assert [(z["minimum"], z["maximum"]) for z in zones] == [
        (0, 110), (111, 150), (151, 180), (181, 210),
        (211, 240), (241, 300), (301, None),
    ]
    assert estimate_ftp_from_zone2(131) == 200


def test_plan_dates_taper_distance_and_short_sessions():
    today = date(2026, 1, 5)
    event = today + timedelta(weeks=10)
    plan = make_plan(event, today, 8, 25)
    assert plan
    assert all(event - timedelta(weeks=8) <= date.fromisoformat(s["date"]) < event for s in plan)
    assert all(date.fromisoformat(s["date"]).weekday() in (1, 3, 5) for s in plan)
    long_ride = next(s for s in plan if s["title"] == "Endurance ride")
    assert long_ride["duration_minutes"] == 60
    assert any("Tempo" in s["title"] for s in plan)
    taper = [s for s in plan if (event - date.fromisoformat(s["date"])).days <= 7]
    assert taper and all("Tempo" not in s["title"] for s in taper)
    assert all("Tempo" not in s["title"] for s in make_plan(event, today, 1))


def test_event_too_close_has_no_sessions():
    assert make_plan(date(2026, 1, 6), date(2026, 1, 5), 5) == []
