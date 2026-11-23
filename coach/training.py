"""Power-zone and basic event-planning calculations."""

from datetime import date, timedelta
from math import ceil, floor


ZONES = (
    ("Recovery", 0.0, 0.55),
    ("Endurance", 0.56, 0.75),
    ("Tempo", 0.76, 0.90),
    ("Threshold", 0.91, 1.05),
    ("VO2 max", 1.06, 1.20),
    ("Anaerobic", 1.21, 1.50),
    ("Neuromuscular", 1.51, None),
)


def power_zones(ftp: int) -> list[dict]:
    """Return rounded, non-overlapping watt ranges from an FTP estimate."""
    zones = []
    for index, (name, low, high) in enumerate(ZONES):
        minimum = 0 if index == 0 else zones[-1]["maximum"] + 1
        maximum = floor(ftp * high) if high is not None else None
        zones.append({"number": index + 1, "name": name, "minimum": minimum, "maximum": maximum})
    return zones


def estimate_ftp_from_zone2(watts: int) -> int:
    """Estimate FTP assuming the supplied Z2 power is its 65.5% midpoint."""
    return round(watts / 0.655)


def make_plan(event_date: date, today: date, hours_per_week: float, distance_km: float | None = None) -> list[dict]:
    """Build up to eight weeks of Tue/Thu/Sat sessions before an event."""
    start = max(today, event_date - timedelta(weeks=8))
    sessions = []
    day = start
    while day < event_date:
        days_to_event = (event_date - day).days
        if day.weekday() in (1, 3, 5):
            taper = days_to_event <= 7
            recovery_week = not taper and 8 <= days_to_event <= 14
            factor = 0.5 if taper else 0.7 if recovery_week else 1.0
            if day.weekday() == 5:
                name = "Endurance ride"
                zone = "Zone 2"
                detail = "Ride steadily at an easy conversational effort."
                fraction = 0.5
            elif day.weekday() == 1 and not taper and not recovery_week:
                name = "Tempo intervals"
                zone = "Zones 2–3"
                detail = "Warm up, then ride 2 × 10 minutes in zone 3 with easy recovery; cool down."
                fraction = 0.25
            else:
                name = "Easy endurance"
                zone = "Zone 2"
                detail = "Keep the effort easy; shorten or skip if fatigued."
                fraction = 0.25
            minutes = max(15, ceil(hours_per_week * 60 * fraction * factor / 5) * 5)
            if day.weekday() == 5 and distance_km is not None:
                # A rough time cap, not a prediction of the rider's speed.
                minutes = min(minutes, max(15, ceil(distance_km / 25 * 60 * factor / 5) * 5))
            # Do not prescribe more interval work than fits in a short session.
            if name == "Tempo intervals" and minutes < 40:
                name = "Easy endurance"
                zone = "Zone 2"
                detail = "Keep the effort easy; shorten or skip if fatigued."
            sessions.append({
                "date": day.isoformat(),
                "title": name,
                "duration_minutes": minutes,
                "zone": zone,
                "notes": detail,
            })
        day += timedelta(days=1)
    return sessions
