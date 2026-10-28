"""Hledání skladeb ze Spotify v YouTube Music a hodnocení shody.

Postup pro každou skladbu (končí, jakmile najde jistou shodu):
  1. hledání podle ISRC — YT Music ho zná u oficiálních nahrávek a vrací přesně tu skladbu
  2. hledání „interpret název“ v katalogu skladeb (filter=songs)
  3. hledání mezi videi (filter=videos) — pro skladby, které v katalogu nejsou

Každý kandidát dostane skóre 0–1 z podobnosti názvu, interpreta a délky.
"""

import re
import threading
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from rapidfuzz import fuzz
from ytmusicapi import YTMusic

ACCEPT = 0.85  # od tohoto skóre se shoda přijme automaticky
REVIEW = 0.60  # od tohoto skóre je shoda „možná“ a čeká na ruční kontrolu
VIDEO_PENALTY = 0.05  # videoklip je horší volba než oficiální audio, pokud je skóre podobné

# Části názvu, které nemění skladbu: featuring, „(From <film>)“ a remastery
_FEAT = re.compile(r"[\(\[]\s*(?:feat|ft|featuring|with|from)\b[^\)\]]*[\)\]]|\s-\s*(?:feat|ft)\b.*$", re.I)
_REMASTER = re.compile(r"[\(\[]?[^\(\)\[\]\-]*\bre-?master(?:ed)?\b[^\(\)\[\]]*[\)\]]?|\b\d{4}\s+master\b", re.I)
# „(Original Mix)“ / „(Original)“ jen říká, že nejde o remix
_ORIGINAL = re.compile(r"[\(\[]\s*original(?:\s+mix)?\s*[\)\]]", re.I)
# Slova označující jinou verzi skladby; nesoulad mezi Spotify a YT se penalizuje
_VERSIONS = ("remix", "live", "acoustic", "instrumental", "karaoke", "cover", "sped up", "slowed", "extended", "edit")


@dataclass(frozen=True)
class Candidate:
    video_id: str
    title: str
    artists: list[str]
    duration_s: int | None
    method: str  # isrc | songs | videos
    score: float


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def clean_title(title: str) -> str:
    # Spotify píše verze za pomlčku („Exceeder - Original Mix“, „Tuyo - From "Narcos"“),
    # YT Music do závorek → převést na závorky, aby na ně fungovaly stejné úpravy
    head, *suffixes = re.split(r"\s[-–]\s+", title)
    title = head + "".join(f" ({s})" for s in suffixes)
    return normalize(_REMASTER.sub(" ", _ORIGINAL.sub(" ", _FEAT.sub(" ", title))))


def _versions(title: str) -> set[str]:
    t = normalize(title)
    return {v for v in _VERSIONS if re.search(rf"\b{v}\b", t)}


def score(
    sp_title: str,
    sp_artists: list[str],
    sp_duration_ms: int | None,
    yt_title: str,
    yt_artists: list[str],
    yt_duration_s: int | None,
) -> float:
    title_sim = fuzz.token_sort_ratio(clean_title(sp_title), clean_title(yt_title)) / 100

    sp_norm = [normalize(a) for a in sp_artists] or [""]
    yt_norm = [normalize(a) for a in yt_artists] or [""]
    primary = max(fuzz.ratio(sp_norm[0], y) for y in yt_norm) / 100
    joined = fuzz.token_set_ratio(" ".join(sp_norm), " ".join(yt_norm)) / 100
    artist_sim = max(primary, joined)

    version_penalty = min(0.45, 0.15 * len(_versions(sp_title) ^ _versions(yt_title)))

    if sp_duration_ms and yt_duration_s:
        diff = abs(sp_duration_ms / 1000 - yt_duration_s)
        dur_sim = 1.0 if diff <= 3 else max(0.0, 1 - (diff - 3) / 17)
        total = 0.40 * title_sim + 0.35 * artist_sim + 0.25 * dur_sim
    else:
        total = 0.55 * title_sim + 0.45 * artist_sim
    return max(0.0, total - version_penalty)


class Matcher:
    """Vyhledává v YT Music; bezpečné pro více vláken (každé má vlastního klienta)."""

    def __init__(self, make_client: Callable[[], YTMusic], results_per_query: int = 5) -> None:
        self._make_client = make_client
        self._local = threading.local()
        self._n = results_per_query

    def _search(self, query: str, filter_: str) -> list[dict[str, Any]]:
        if not hasattr(self._local, "yt"):
            self._local.yt = self._make_client()
        for attempt in range(4):
            try:
                return self._local.yt.search(query, filter=filter_, limit=self._n) or []
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(2**attempt)
        return []

    def _candidates(self, results: list[dict[str, Any]], method: str, track: dict[str, Any]) -> list[Candidate]:
        out = []
        for r in results[: self._n]:
            if not r.get("videoId") or r.get("isAvailable") is False:
                continue
            artists = [a["name"] for a in r.get("artists") or [] if a.get("name")]
            s = score(track["name"], track["artists"], track["duration_ms"], r.get("title") or "", artists, r.get("duration_seconds"))
            if method == "videos":
                s = max(0.0, s - VIDEO_PENALTY)
            out.append(Candidate(r["videoId"], r.get("title") or "", artists, r.get("duration_seconds"), method, round(s, 3)))
        return out

    def best_match(self, track: dict[str, Any]) -> Candidate | None:
        """track: dict s klíči name, artists (list), duration_ms, isrc."""
        query = f"{track['artists'][0] if track['artists'] else ''} {track['name']}".strip()
        steps: list[tuple[str, str, str]] = []
        if track.get("isrc"):
            steps.append(("isrc", track["isrc"], "songs"))
        steps += [("songs", query, "songs"), ("videos", query, "videos")]

        best: Candidate | None = None
        for method, q, filter_ in steps:
            # ISRC vrací přesnou skladbu jako první výsledek, další výsledky jsou šum
            results = self._search(q, filter_)
            if method == "isrc":
                results = results[:1]
            for c in self._candidates(results, method, track):
                if best is None or c.score > best.score:
                    best = c
            if best and best.score >= ACCEPT:
                break
        return best
