"""Výměna videoklipů v Liked music za audio verze skladeb.

Videoklipy (OMV/UGC) se v autě a offline přehrávají hůř než skladby z katalogu (ATV).
Pro každý lajknutý klip se hledá audio verze: nejdřív protějšek „skladba/video“ z přehrávače,
pak vyhledávání v katalogu skladeb. Při jisté shodě se lajkne audio a klip se odlajkne.
Klipy bez audio verze (živáky, fanouškovská videa) zůstanou.
"""

import re
from dataclasses import dataclass
from typing import Any

from ytmusicapi import YTMusic

from sp2yt.matcher import normalize, score

AUDIO = "MUSIC_VIDEO_TYPE_ATV"
# Přísnější než při párování: klip se odlajkne, takže chceme mít jistotu, že jde o tutéž skladbu
SEARCH_ACCEPT = 0.90

# Přídavky v názvech klipů, které v názvu skladby nejsou
_VIDEO_NOISE = re.compile(
    r"[\(\[]\s*(?:official\s+)?(?:music\s+|lyrics?\s+|hd\s+|4k\s+)?(?:video(?:clip)?|audio|lyrics?|visuali[sz]er|clip)"
    r"(?:\s+version)?\s*[\)\]]|\bofficial\s+(?:music\s+)?video\b|\b(?:hd|4k|hq)\b|//.*$|\|.*$",
    re.I,
)


@dataclass(frozen=True)
class Replacement:
    video_id: str
    video_title: str
    artists: list[str]
    audio_id: str | None
    audio_title: str | None
    audio_artists: list[str]
    method: str | None  # counterpart | search
    score: float | None


def clean_video_title(title: str, artists: list[str]) -> str:
    t = _VIDEO_NOISE.sub(" ", title)
    # „Interpret - Název“ → „Název“
    for a in artists:
        m = re.match(rf"\s*{re.escape(a)}\s*[-–—:]\s*(.+)$", t, re.I)
        if m:
            t = m.group(1)
            break
    return re.sub(r"\s+", " ", t).strip()


def _artists(item: dict[str, Any]) -> list[str]:
    return [a["name"] for a in item.get("artists") or [] if a.get("name")]


def find_audio(yt: YTMusic, track: dict[str, Any]) -> Replacement:
    vid, title, artists = track["videoId"], track.get("title") or "", _artists(track)
    none = Replacement(vid, title, artists, None, None, [], None, None)

    try:
        watch = yt.get_watch_playlist(videoId=vid, limit=1)
        counterpart = (watch.get("tracks") or [{}])[0].get("counterpart")
    except Exception:
        counterpart = None
    if counterpart and counterpart.get("videoType") == AUDIO and counterpart.get("videoId"):
        return Replacement(
            vid, title, artists, counterpart["videoId"], counterpart.get("title"), _artists(counterpart), "counterpart", 1.0
        )

    clean = clean_video_title(title, artists)
    if not clean:
        return none
    query = f"{artists[0] if artists else ''} {clean}".strip()
    best = none
    for r in (yt.search(query, filter="songs", limit=5) or [])[:5]:
        if not r.get("videoId") or r.get("isAvailable") is False:
            continue
        r_artists = _artists(r)
        # Délka klipu se od skladby často liší (intro, outro), proto se neporovnává
        s = score(clean, artists, None, r.get("title") or "", r_artists, None)
        # Interpret z kanálu klipu nemusí sedět (fanouškovská videa) → kontrola, že se aspoň potkávají
        if normalize(" ".join(artists)) and not set(normalize(" ".join(artists)).split()) & set(
            normalize(" ".join(r_artists) + " " + (r.get("title") or "")).split()
        ):
            continue
        if best.score is None or s > best.score:
            best = Replacement(vid, title, artists, r["videoId"], r.get("title"), r_artists, "search", round(s, 3))
    if best.score is not None and best.score >= SEARCH_ACCEPT:
        return best
    return Replacement(vid, title, artists, None, best.audio_title, best.audio_artists, None, best.score)
