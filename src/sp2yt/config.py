from dataclasses import dataclass
from os import environ
from pathlib import Path

from dotenv import find_dotenv, load_dotenv


@dataclass(frozen=True)
class Config:
    spotify_client_id: str
    spotify_client_secret: str
    spotify_redirect_uri: str
    ytm_auth_file: Path
    db_path: Path


def load_config() -> Config:
    load_dotenv(find_dotenv(usecwd=True))
    return Config(
        spotify_client_id=environ.get("SPOTIFY_CLIENT_ID", ""),
        spotify_client_secret=environ.get("SPOTIFY_CLIENT_SECRET", ""),
        spotify_redirect_uri=environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback"),
        ytm_auth_file=Path(environ.get("YTM_AUTH_FILE", "browser.json")),
        db_path=Path(environ.get("DB_PATH", "state.db")),
    )
