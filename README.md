# Ridewise: Cycling Coach

A local, single-user Flask application for tracking rides and preparing for cycling events. It stores a rider profile, past and planned rides, and events in SQLite. Ridewise provides general training guidance, not medical advice or a substitute for a qualified coach. Start at a level appropriate for your experience, adjust for fatigue and health, and consult a professional when appropriate.

## Requirements and setup

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and Python 3.10 or later. From the project directory:

```sh
uv sync
uv run python -m coach
```

Open <http://127.0.0.1:5000>. Stop the server with Ctrl+C. `uv sync` creates `.venv` and installs the runtime and development dependencies from `pyproject.toml` and `uv.lock`; uv can obtain a compatible Python if needed.

The app listens on localhost only and is intended for use by one trusted user on their own computer. It does **not** have accounts or authentication; do not expose it to a network or deploy it as a public service. Data lives in `instance/cycling_coach.sqlite3`; back up that file to preserve your data. Cookies and form tokens are invalidated when the app restarts by default. To keep them across restarts, set a private `CYCLING_COACH_SECRET` environment variable before launching the app.

## Using the coach

1. Enter height, weight, weekly riding hours, and either FTP or a representative zone 2 power. Enter height in centimeters or feet and inches (inches are optional and must be below 12). If both height formats are supplied, feet and inches take precedence; the result is stored in centimeters, rounded to two decimal places. The accepted height is 50–250 cm after conversion. Enter weight in kilograms or pounds; if both are supplied, pounds take precedence and are converted to kilograms, rounded to two decimal places. The accepted range is 20–350 kg after conversion. Direct FTP takes precedence if both power values are supplied. If only zone 2 is known, the app assumes that power is 65.5% of FTP (the midpoint of 56–75%) and labels the calculated FTP as an estimate; use a measured FTP when available. Height and weight are recorded; weight is also used to show W/kg.
2. View the seven FTP-based power zones. Watt ranges are rounded to whole watts; these are general estimates, not physiological measurements.
3. Add a future event, optionally with its distance and notes. Generate its plan to place rides on the training calendar.
4. Add completed or planned rides manually. Use Previous and Next to browse the monthly calendar, including all planned rides and events; select a ride to see details and mark it complete. The calendar opens at the first generated session when a plan is created. Past rides also remain in the ride log. On smaller screens, scroll the calendar sideways. The app does not display GPS routes or import activity files.

With JavaScript enabled, profile setup walks through three steps with a review before saving. Height and weight conversion, power, event countdown, and ride-duration previews update as you type; quick duration buttons are available for ride entry. Without JavaScript, the regular forms remain available. Previews are informational and values are validated when saved.

### How plans work

The plan schedules up to eight weeks before an event, beginning no earlier than today. It uses Tuesday and Thursday sessions and a Saturday endurance ride, with approximate durations of 25%, 25%, and 50% of the entered weekly hours. It includes a lighter week 8–14 days before the event and a taper in the final seven days. Some sessions may be absent in partial weeks, and rounding and minimum 15-minute sessions mean totals are approximate. The plan never schedules a ride on event day.

If event distance is entered, the Saturday ride is capped at an approximate event duration using an assumed 25 km/h, with the same recovery/taper reduction; this is a planning cap, **not** a speed prediction. Event notes are for the rider's reference and do not alter workouts. FTP determines the displayed watt ranges for the prescribed zones; plans do not account for terrain, fitness history, medical conditions, other events, or actual recovery. Modify or skip sessions when needed. There may be no available scheduled day before a very near event.

Regenerating a plan replaces only that event's generated, **planned** rides from today onward. Completed rides and manually entered rides remain; generated rides completed on a given day are not duplicated on refresh. Changing the profile does not automatically regenerate plans. Multiple event plans can overlap, so review the combined calendar and avoid stacking sessions.

## Tests

```sh
uv run pytest
```

## Project layout

```text
pyproject.toml          Project metadata, dependencies, and pytest configuration
uv.lock                 Resolved dependencies for reproducible installs
coach/
  __main__.py           Local server entry point
  app.py                Flask routes and input validation
  db.py                 SQLite schema and connection lifecycle
  training.py           Power zones and training plan calculations
  templates/index.html  User interface
  static/style.css      Responsive styling
tests/                  Calculation and application tests
instance/               Local database (created at runtime, ignored by Git)
```
