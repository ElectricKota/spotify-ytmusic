"""Stav přenosu v SQLite.

Každá oblíbená skladba ze Spotify má jeden řádek a prochází stavy:
    pending   → stažena ze Spotify, ještě nehledána
    matched   → nalezena jistá shoda v YT Music (video_id)
    review    → nalezena nejistá shoda, čeká na ruční potvrzení
    not_found → v YT Music nic vhodného
    duplicate → stejné video_id už má jiná skladba, nepřidává se znovu
    added     → úspěšně označena v YT Music jako „To se mi líbí“
"""

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path

from sp2yt.sources import Track

STATUSES = ("pending", "matched", "review", "not_found", "duplicate", "added")

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS tracks (
    spotify_id    TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    artists       TEXT NOT NULL,          -- JSON pole jmen
    album         TEXT,
    duration_ms   INTEGER,
    isrc          TEXT,
    added_at      TEXT,                   -- kdy byla skladba oblíbena na Spotify (ISO 8601), pokud ho zdroj zná
    source_pos    INTEGER NOT NULL,       -- pořadí ve zdroji, 0 = naposledy oblíbená
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ({", ".join(f"'{s}'" for s in STATUSES)})),
    video_id      TEXT,
    match_score   REAL,
    match_title   TEXT,
    match_artists TEXT,
    match_duration_s INTEGER,
    match_method  TEXT,                   -- isrc | songs | videos | manual
    error         TEXT,
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tracks_status ON tracks(status);
CREATE INDEX IF NOT EXISTS idx_tracks_video_id ON tracks(video_id);
"""


# Sloupce přidané po vzniku první databáze; CREATE TABLE IF NOT EXISTS je do existující nedoplní
MIGRATIONS = {
    "match_duration_s": "INTEGER",
    "match_method": "TEXT",
}


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(tracks)")}
    for column, ddl in MIGRATIONS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE tracks ADD COLUMN {column} {ddl}")
    conn.commit()
    return conn


def upsert_tracks(conn: sqlite3.Connection, tracks: Iterable[Track]) -> tuple[int, int]:
    """Uloží skladby ze Spotify; existujícím aktualizuje metadata, ale nesahá na stav přenosu.

    Vrací (nových, celkem zpracovaných).
    """
    before = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    total = 0
    with conn:
        for t in tracks:
            conn.execute(
                """
                INSERT INTO tracks (spotify_id, name, artists, album, duration_ms, isrc, added_at, source_pos)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(spotify_id) DO UPDATE SET
                    name = excluded.name,
                    artists = excluded.artists,
                    album = excluded.album,
                    duration_ms = COALESCE(excluded.duration_ms, duration_ms),
                    isrc = COALESCE(excluded.isrc, isrc),
                    added_at = COALESCE(excluded.added_at, added_at),
                    source_pos = excluded.source_pos,
                    updated_at = datetime('now')
                """,
                (
                    t.spotify_id,
                    t.name,
                    json.dumps(t.artists, ensure_ascii=False),
                    t.album,
                    t.duration_ms,
                    t.isrc,
                    t.added_at,
                    t.source_pos,
                ),
            )
            total += 1
    after = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    return after - before, total


def tracks_to_match(conn: sqlite3.Connection, statuses: tuple[str, ...], limit: int | None) -> list[dict]:
    """Skladby k vyhledání, od naposledy oblíbené."""
    rows = conn.execute(
        f"""
        SELECT spotify_id, name, artists, duration_ms, isrc FROM tracks
        WHERE status IN ({", ".join("?" * len(statuses))})
        ORDER BY source_pos
        LIMIT ?
        """,
        (*statuses, -1 if limit is None else limit),
    ).fetchall()
    return [{**dict(r), "artists": json.loads(r["artists"])} for r in rows]


def save_match(conn: sqlite3.Connection, spotify_id: str, status: str, candidate=None, error: str | None = None) -> None:
    c = candidate
    conn.execute(
        """
        UPDATE tracks SET
            status = ?, video_id = ?, match_score = ?, match_title = ?, match_artists = ?,
            match_duration_s = ?, match_method = ?, error = ?, updated_at = datetime('now')
        WHERE spotify_id = ?
        """,
        (
            status,
            c.video_id if c else None,
            c.score if c else None,
            c.title if c else None,
            json.dumps(c.artists, ensure_ascii=False) if c else None,
            c.duration_s if c else None,
            c.method if c else None,
            error,
            spotify_id,
        ),
    )


def mark_duplicates(conn: sqlite3.Connection) -> int:
    """Když více skladeb ze Spotify vede na stejné video, ponechá jen jednu.

    Přednost má skladba už přidaná do YT Music, jinak ta dřív oblíbená na Spotify.
    Ostatní dostanou stav 'duplicate' a při importu se přeskočí.
    """
    before = conn.total_changes
    with conn:
        conn.execute(
            """
            WITH ranked AS (
                SELECT spotify_id, status, ROW_NUMBER() OVER (
                    PARTITION BY video_id
                    ORDER BY status = 'added' DESC, status = 'matched' DESC, source_pos DESC
                ) AS rn
                FROM tracks
                WHERE video_id IS NOT NULL AND status IN ('matched', 'review', 'added')
            )
            UPDATE tracks SET status = 'duplicate', updated_at = datetime('now')
            WHERE spotify_id IN (SELECT spotify_id FROM ranked WHERE rn > 1 AND status != 'added')
            """
        )
    return conn.total_changes - before


def status_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts = dict.fromkeys(STATUSES, 0)
    for row in conn.execute("SELECT status, COUNT(*) AS n FROM tracks GROUP BY status"):
        counts[row["status"]] = row["n"]
    return counts
