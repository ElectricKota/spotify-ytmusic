"""Vytvoření přihlášených klientů pro Spotify a YouTube Music."""

import json
import os
import re
import subprocess
from dataclasses import dataclass

import spotipy
from spotipy.cache_handler import CacheFileHandler
from spotipy.oauth2 import SpotifyOAuth
from ytmusicapi import YTMusic
from ytmusicapi.setup import setup_browser

from sp2yt.config import Config

SPOTIFY_SCOPE = "user-library-read"
SPOTIFY_CACHE = ".spotify_cache"


class AuthError(Exception):
    """Chybějící nebo neplatné přihlášení, zpráva je určená uživateli."""


def spotify(cfg: Config) -> spotipy.Spotify:
    if not cfg.spotify_client_id or not cfg.spotify_client_secret:
        raise AuthError(
            "Chybí SPOTIFY_CLIENT_ID nebo SPOTIFY_CLIENT_SECRET v .env "
            "(zkopíruj .env.example do .env a doplň údaje z developer.spotify.com)."
        )
    # Při prvním spuštění otevře prohlížeč a přesměrování zachytí lokální server
    # na adrese z SPOTIFY_REDIRECT_URI; token se pak obnovuje z .spotify_cache.
    auth = SpotifyOAuth(
        client_id=cfg.spotify_client_id,
        client_secret=cfg.spotify_client_secret,
        redirect_uri=cfg.spotify_redirect_uri,
        scope=SPOTIFY_SCOPE,
        cache_handler=CacheFileHandler(cache_path=SPOTIFY_CACHE),
        open_browser=True,
    )
    return spotipy.Spotify(auth_manager=auth, retries=5, status_retries=5, backoff_factor=0.5)


def ytmusic(cfg: Config) -> YTMusic:
    if not cfg.ytm_auth_file.exists():
        raise AuthError(
            f"Chybí {cfg.ytm_auth_file}. Zkopíruj hlavičky požadavku z prohlížeče "
            "a spusť `uv run sp2yt ytm-setup`."
        )
    return YTMusic(str(cfg.ytm_auth_file), user=cfg.ytm_user)


@dataclass(frozen=True)
class Channel:
    name: str
    handle: str
    user_id: str  # hodnota pro YTMusic(user=…) / YTM_USER
    selected: bool


def ytmusic_channels(cfg: Config) -> list[Channel]:
    """Kanály (osobní i brand účty) dostupné pod přihlášeným Google účtem."""
    yt = YTMusic(str(cfg.ytm_auth_file))
    response = yt._send_request("account/accounts_list", {})

    items: list[dict] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if "accountItem" in node:
                items.append(node["accountItem"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(response)
    channels = []
    for item in items:
        text = lambda key: "".join(r.get("text", "") for r in item.get(key, {}).get("runs", []))  # noqa: E731
        # ID kanálu je v endpointu pro přepnutí identity
        ids = re.findall(r'"(?:obfuscatedGaiaId|pageId)":\s*"(\d+)"', json.dumps(item.get("serviceEndpoint", {})))
        if ids:
            channels.append(Channel(text("accountName"), text("channelHandle"), ids[0], bool(item.get("isSelected"))))
    return channels


def save_ytm_headers(cfg: Config, raw: str, authuser: int = 0) -> None:
    """Uloží přihlášení z prohlížeče do auth souboru, čitelného jen pro vlastníka.

    `raw` jsou buď celé hlavičky požadavku, nebo jen hodnota hlavičky Cookie.
    Authorization si ytmusicapi dopočítá z cookie SAPISID samo.
    """
    raw = raw.strip()
    if "\n" not in raw and "SAPISID" in raw:
        raw = raw.removeprefix("Cookie:").removeprefix("cookie:").strip()
        # ytmusicapi podle „SAPISIDHASH“ v authorization pozná přihlášení z prohlížeče;
        # skutečnou hodnotu dopočítá z cookie při každém požadavku
        raw = f"cookie: {raw}\nx-goog-authuser: {authuser}\nauthorization: SAPISIDHASH 0_0"
    setup_browser(str(cfg.ytm_auth_file), raw)
    os.chmod(cfg.ytm_auth_file, 0o600)


def read_clipboard() -> str:
    return subprocess.run(["pbpaste"], capture_output=True, text=True, check=True).stdout
