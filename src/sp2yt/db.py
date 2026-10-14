"""Stav přenosu v SQLite.

Každá oblíbená skladba ze Spotify má jeden řádek a prochází stavy:
    pending   → stažena ze Spotify, ještě nehledána
    matched   → nalezena jistá shoda v YT Music (video_id)
    review    → nalezena nejistá shoda, čeká na ruční potvrzení
    not_found → v YT Music nic vhodného
    duplicate → stejné video_id už má jiná skladba, nepřidává se znovu
    added     → úspěšně označena v YT Music jako „To se mi líbí“
"""

import sqlite3
from pathlib import Path

STATUSES = ("pending", "matched", "review", "not_found", "duplicate", "added")

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS tracks (
    spotify_id    TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    artists       TEXT NOT NULL,          -- JSON pole jmen
    album         TEXT,
    duration_ms   INTEGER,
    isrc          TEXT,
    added_at      TEXT NOT NULL,          -- kdy byla skladba oblíbena na Spotify (ISO 8601)
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ({", ".join(f"'{s}'" for s in STATUSES)})),
    video_id      TEXT,
    match_score   REAL,
    match_title   TEXT,
    match_artists TEXT,
    error         TEXT,
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tracks_status ON tracks(status);
CREATE INDEX IF NOT EXISTS idx_tracks_video_id ON tracks(video_id);
"""


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def status_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts = dict.fromkeys(STATUSES, 0)
    for row in conn.execute("SELECT status, COUNT(*) AS n FROM tracks GROUP BY status"):
        counts[row["status"]] = row["n"]
    return counts
