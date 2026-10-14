"""Zdroje oblíbených skladeb ze Spotify.

- Spotify Web API (vyžaduje vlastní vývojářskou aplikaci, tedy Spotify Premium)
- CSV z Exportify (https://exportify.net) — export „Liked Songs“
- YourLibrary.json z exportu osobních údajů (spotify.com/account/privacy)

Všechny zdroje vrací skladby od naposledy oblíbené po nejstarší (source_pos 0 = nejnovější),
pokud zdroj pořadí zná.
"""

import csv
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import spotipy


@dataclass(frozen=True)
class Track:
    spotify_id: str
    name: str
    artists: list[str]
    album: str | None
    duration_ms: int | None
    isrc: str | None
    added_at: str | None
    source_pos: int


class SourceError(Exception):
    """Soubor nemá očekávaný formát, zpráva je určená uživateli."""


def _id_from_uri(uri: str) -> str:
    # spotify:track:<id> → <id>; lokální soubory (spotify:local:…) nemají ID, necháme celé URI
    prefix = "spotify:track:"
    return uri[len(prefix):] if uri.startswith(prefix) else uri


def from_api(sp: spotipy.Spotify) -> Iterator[Track]:
    offset = 0
    while True:
        page = sp.current_user_saved_tracks(limit=50, offset=offset)
        for i, item in enumerate(page["items"]):
            t = item["track"]
            if not t:
                continue
            yield Track(
                spotify_id=t.get("id") or t["uri"],
                name=t["name"],
                artists=[a["name"] for a in t.get("artists", []) if a.get("name")],
                album=(t.get("album") or {}).get("name"),
                duration_ms=t.get("duration_ms"),
                isrc=(t.get("external_ids") or {}).get("isrc"),
                added_at=item.get("added_at"),
                source_pos=offset + i,
            )
        if not page.get("next"):
            return
        offset += len(page["items"])


# Exportify mezi verzemi mírně mění názvy sloupců, proto více variant
_CSV_COLUMNS = {
    "uri": ("track uri", "spotify uri", "uri"),
    "name": ("track name", "name"),
    "artists": ("artist name(s)", "artist names", "artist name", "artists"),
    "album": ("album name", "album"),
    "duration_ms": ("duration (ms)", "track duration (ms)", "duration_ms"),
    "isrc": ("isrc",),
    "added_at": ("added at", "added_at"),
}


def from_exportify_csv(path: Path) -> Iterator[Track]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = {h.strip().lower(): h for h in reader.fieldnames or []}
        col = {
            key: next((headers[c] for c in candidates if c in headers), None)
            for key, candidates in _CSV_COLUMNS.items()
        }
        missing = [k for k in ("uri", "name", "artists") if not col[k]]
        if missing:
            raise SourceError(
                f"{path.name}: chybí sloupce {', '.join(missing)}. "
                f"Nalezené sloupce: {', '.join(reader.fieldnames or [])}"
            )

        def get(row: dict[str, str], key: str) -> str | None:
            if not col[key]:
                return None
            return (row.get(col[key]) or "").strip() or None

        for pos, row in enumerate(reader):
            uri = get(row, "uri")
            name = get(row, "name")
            if not uri or not name:
                continue
            duration = get(row, "duration_ms")
            yield Track(
                spotify_id=_id_from_uri(uri),
                name=name,
                artists=[a.strip() for a in (get(row, "artists") or "").split(",") if a.strip()],
                album=get(row, "album"),
                duration_ms=int(float(duration)) if duration else None,
                isrc=get(row, "isrc"),
                added_at=get(row, "added_at"),
                source_pos=pos,
            )


def from_your_library_json(path: Path) -> Iterator[Track]:
    data = json.loads(path.read_text(encoding="utf-8"))
    tracks = data.get("tracks") if isinstance(data, dict) else None
    if not isinstance(tracks, list):
        raise SourceError(f"{path.name}: nenalezen seznam 'tracks' (očekávám YourLibrary.json z exportu dat).")
    for pos, t in enumerate(tracks):
        uri, name = t.get("uri"), t.get("track")
        if not uri or not name:
            continue
        yield Track(
            spotify_id=_id_from_uri(uri),
            name=name,
            artists=[t["artist"]] if t.get("artist") else [],
            album=t.get("album"),
            duration_ms=None,
            isrc=None,
            added_at=None,
            source_pos=pos,
        )


def from_file(path: Path) -> Iterator[Track]:
    if not path.exists():
        raise SourceError(f"Soubor {path} neexistuje.")
    match path.suffix.lower():
        case ".csv":
            return from_exportify_csv(path)
        case ".json":
            return from_your_library_json(path)
    raise SourceError(f"{path.name}: neznámý formát, podporuji .csv (Exportify) a .json (YourLibrary.json).")
