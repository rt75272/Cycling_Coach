"""Power-zone and basic event-planning calculations."""

from datetime import date, timedelta
from dataclasses import dataclass
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


@dataclass(frozen=True)
class RideFeedback:
    ride_date: date
    actual_minutes: int
    feeling: str | None
    effort: int | None
    performance: str | None


@dataclass(frozen=True)
class PlanAdjustment:
    weekly_minutes: float | None
    easy_only: bool
    paused: bool
    explanation: str


def assess_training(today: date, hours_per_week: float, rides: list[RideFeedback],
                    checkin: tuple[date, str] | None, has_history: bool = False) -> PlanAdjustment:
    """Apply conservative, transparent heuristics to explicitly recorded feedback."""
    recent = [r for r in rides if 0 <= (today - r.ride_date).days <= 6]
    prior = [r for r in rides if 7 <= (today - r.ride_date).days <= 13]
    target = hours_per_week * 60
    recent_minutes = sum(r.actual_minutes for r in recent)
    evidence = f" Last 7 days: {len(recent)} recorded rides, {recent_minutes} minutes."
    if checkin and checkin[1] == "unwell":
        return PlanAdjustment(0, True, True,
            "Training paused: your latest check-in says unwell or injured. Seek appropriate advice and update your check-in before resuming." + evidence)
    if not recent and (has_history or prior):
        base = min(target, sum(r.actual_minutes for r in prior)) if prior else 0
        return PlanAdjustment(base * 0.75, True, False,
            "No actual rides recorded in the last 7 days: using at most 75% of the previous week's recorded time, with easy rides only. Log recent rides to reassess." + evidence)
    tired = checkin and 0 <= (today - checkin[0]).days <= 2 and checkin[1] == "tired"
    difficult = any(r.feeling == "poor" or r.performance == "worse" or
                    (r.effort is not None and r.effort >= 8) for r in recent)
    if tired or difficult:
        base = min(target, recent_minutes) if recent else target
        return PlanAdjustment(base * 0.75, True, False,
            "Recovery: recent fatigue, difficult effort, or worse performance reduces the weekly budget by 25% and removes tempo." + evidence)
    if not recent:
        return PlanAdjustment(None, True, False,
            "No recorded actual rides in the last 7 days. Using your entered weekly hours with easy rides only; there is not enough evidence to increase training." + evidence)
    ready = checkin and 0 <= (today - checkin[0]).days <= 2 and checkin[1] == "good"
    consistent = (
        len(recent) >= 2 and len(prior) >= 2 and recent_minutes >= 60
        and recent_minutes >= 0.8 * sum(r.actual_minutes for r in prior)
        and all(r.feeling == "good" and r.effort is not None and r.effort <= 6
                and r.performance in ("same", "better") for r in recent)
    )
    if ready and consistent:
        return PlanAdjustment(min(target, recent_minutes * 1.05), False, False,
            "Progression: consistent rides and positive feedback allow up to 5% above the last 7 days, capped by your weekly hours." + evidence)
    return PlanAdjustment(min(target, recent_minutes), True, False,
        "Holding volume: limited or mixed feedback caps the weekly budget at the last 7 days of recorded riding, with easy rides only and no increase." + evidence)


def make_plan(event_date: date, today: date, hours_per_week: float, distance_km: float | None = None,
              adjustment: PlanAdjustment | None = None) -> list[dict]:
    """Build up to eight weeks of Tue/Thu/Sat sessions before an event."""
    if adjustment and adjustment.paused:
        return []
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
            elif day.weekday() == 1 and not taper and not recovery_week and not (adjustment and adjustment.easy_only):
                name = "Tempo intervals"
                zone = "Zones 2–3"
                detail = "Warm up, then ride 2 × 10 minutes in zone 3 with easy recovery; cool down."
                fraction = 0.25
            else:
                name = "Easy endurance"
                zone = "Zone 2"
                detail = "Keep the effort easy; shorten or skip if fatigued."
                fraction = 0.25
            if adjustment and adjustment.weekly_minutes is not None:
                minutes = floor(adjustment.weekly_minutes * fraction * factor / 5) * 5
                if minutes < 15:
                    day += timedelta(days=1)
                    continue
            else:
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
