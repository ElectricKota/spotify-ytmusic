"""Vytvoření přihlášených klientů pro Spotify a YouTube Music."""

import os
import subprocess

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
    return YTMusic(str(cfg.ytm_auth_file))


def save_ytm_headers(cfg: Config, headers_raw: str) -> None:
    """Uloží hlavičky z prohlížeče do auth souboru, čitelného jen pro vlastníka."""
    setup_browser(str(cfg.ytm_auth_file), headers_raw)
    os.chmod(cfg.ytm_auth_file, 0o600)


def read_clipboard() -> str:
    return subprocess.run(["pbpaste"], capture_output=True, text=True, check=True).stdout
