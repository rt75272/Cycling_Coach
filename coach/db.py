"""SQLite persistence for the local application."""

import sqlite3
from pathlib import Path

from flask import current_app, g


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        Path(current_app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_error=None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    get_db().executescript("""
        CREATE TABLE IF NOT EXISTS profile (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            height_cm REAL NOT NULL,
            weight_kg REAL NOT NULL,
            ftp INTEGER NOT NULL,
            ftp_source TEXT NOT NULL,
            zone2 INTEGER,
            hours_per_week REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            event_date TEXT NOT NULL,
            distance_km REAL,
            details TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS rides (
            id INTEGER PRIMARY KEY,
            ride_date TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('planned', 'completed')),
            duration_minutes INTEGER NOT NULL,
            zone TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            event_id INTEGER REFERENCES events(id) ON DELETE SET NULL,
            generated INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS ride_feedback (
            ride_id INTEGER PRIMARY KEY REFERENCES rides(id) ON DELETE CASCADE,
            actual_minutes INTEGER NOT NULL CHECK (actual_minutes BETWEEN 1 AND 1440),
            feeling TEXT CHECK (feeling IN ('good', 'okay', 'poor')),
            effort INTEGER CHECK (effort BETWEEN 1 AND 10),
            performance TEXT CHECK (performance IN ('better', 'same', 'worse'))
        );
        CREATE TABLE IF NOT EXISTS checkins (
            checkin_date TEXT PRIMARY KEY,
            feeling TEXT NOT NULL CHECK (feeling IN ('good', 'okay', 'tired', 'unwell'))
        );
        CREATE TABLE IF NOT EXISTS active_plans (
            event_id INTEGER PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
            explanation TEXT NOT NULL DEFAULT '',
            updated_on TEXT NOT NULL DEFAULT ''
        );
        INSERT OR IGNORE INTO active_plans (event_id)
        SELECT DISTINCT event_id FROM rides WHERE generated = 1 AND event_id IS NOT NULL;
    """)
    get_db().commit()
