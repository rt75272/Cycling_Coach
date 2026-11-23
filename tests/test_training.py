from datetime import date, timedelta

from coach.training import RideFeedback, assess_training, estimate_ftp_from_zone2, make_plan, power_zones


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


def test_positive_feedback_allows_at_most_five_percent_progression():
    today = date(2026, 1, 5)
    rides = [RideFeedback(today - timedelta(days=d), 150, "good", 5, "same")
             for d in (1, 3, 8, 10)]
    adjustment = assess_training(today, 8, rides, (today, "good"))
    assert adjustment.weekly_minutes == 315
    plan = make_plan(today + timedelta(days=35), today, 8, adjustment=adjustment)
    weeks = {}
    for session in plan:
        week = date.fromisoformat(session["date"]).isocalendar()[:2]
        weeks[week] = weeks.get(week, 0) + session["duration_minutes"]
    assert all(minutes <= 315 for minutes in weeks.values())
    assert any("Tempo" in session["title"] for session in plan)
    no_checkin = assess_training(today, 8, rides, None)
    assert no_checkin.weekly_minutes == 300 and no_checkin.easy_only
    assert assess_training(today, 8, rides[:-1], (today, "good")).weekly_minutes == 300


def test_fatigue_reduces_volume_and_removes_intensity():
    today = date(2026, 1, 5)
    ride = RideFeedback(today - timedelta(days=1), 200, "okay", 9, "same")
    adjustment = assess_training(today, 8, [ride], (today, "good"))
    assert adjustment.weekly_minutes == 150
    assert adjustment.easy_only
    plan = make_plan(today + timedelta(days=35), today, 8, adjustment=adjustment)
    assert plan and all("Tempo" not in session["title"] for session in plan)
    assert max(session["duration_minutes"] for session in plan) <= 75
    assert assess_training(today, 8, [], (today, "tired")).weekly_minutes == 360
    very_short = RideFeedback(today, 10, "poor", 4, "worse")
    low = assess_training(today, 8, [very_short], None)
    assert make_plan(today + timedelta(days=35), today, 8, adjustment=low) == []


def test_unwell_checkin_pauses_until_a_new_checkin_and_missing_data_never_increases():
    today = date(2026, 1, 5)
    paused = assess_training(today, 8, [], (today - timedelta(days=10), "unwell"))
    assert paused.paused
    assert make_plan(today + timedelta(days=35), today, 8, adjustment=paused) == []
    unknown = assess_training(today, 8, [], None)
    assert not unknown.paused and unknown.weekly_minutes is None and unknown.easy_only
    assert "not enough evidence" in unknown.explanation
    stale = RideFeedback(today - timedelta(days=7), 120, "poor", 9, "worse")
    no_recent = assess_training(today, 8, [stale], None)
    assert no_recent.weekly_minutes == 90 and no_recent.easy_only
    assert assess_training(today, 8, [], None, has_history=True).weekly_minutes == 0
