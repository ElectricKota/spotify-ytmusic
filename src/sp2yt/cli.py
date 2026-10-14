from contextlib import closing
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table
from spotipy import SpotifyException, SpotifyOauthError
from ytmusicapi.exceptions import YTMusicError

from sp2yt import clients, db, sources
from sp2yt.config import Config, load_config

app = typer.Typer(
    help="Přesun oblíbených skladeb ze Spotify do YouTube Music bez duplicit.",
    no_args_is_help=True,
)
console = Console()


def _not_yet(step: int) -> None:
    console.print(f"[yellow]Zatím neimplementováno (krok {step}).[/yellow]")
    raise typer.Exit(1)


@app.command("ytm-setup")
def ytm_setup(
    file: Annotated[
        Path | None,
        typer.Option(help="Soubor s hlavičkami; bez něj se načtou ze schránky."),
    ] = None,
) -> None:
    """Uloží přihlášení k YouTube Music z hlaviček požadavku zkopírovaných z prohlížeče."""
    cfg = load_config()
    headers_raw = file.read_text() if file else clients.read_clipboard()
    try:
        clients.save_ytm_headers(cfg, headers_raw)
    except YTMusicError as e:
        console.print(f"[red]Hlavičky se nepodařilo načíst:[/red] {e}")
        raise typer.Exit(1)
    console.print(f"[green]Uloženo do {cfg.ytm_auth_file}.[/green] Ověřuji přihlášení…")
    _check_ytmusic(cfg)


@app.command("auth-check")
def auth_check() -> None:
    """Ověří přihlášení ke Spotify i YouTube Music."""
    cfg = load_config()
    ok = _check_spotify(cfg)
    ok = _check_ytmusic(cfg) and ok
    if not ok:
        raise typer.Exit(1)


def _check_spotify(cfg: Config) -> bool:
    if not cfg.spotify_client_id:
        console.print("[yellow]– Spotify:[/yellow] API není nastavené, stačí export ze souboru (`export --file`).")
        return True
    try:
        sp = clients.spotify(cfg)
        user = sp.current_user()
        total = sp.current_user_saved_tracks(limit=1)["total"]
    except (clients.AuthError, SpotifyException, SpotifyOauthError) as e:
        console.print(f"[red]✗ Spotify:[/red] {e}")
        return False
    console.print(
        f"[green]✓ Spotify:[/green] {user.get('display_name') or user['id']}, "
        f"oblíbených skladeb: [bold]{total}[/bold]"
    )
    return True


def _check_ytmusic(cfg: Config) -> bool:
    try:
        info = clients.ytmusic(cfg).get_account_info()
    except clients.AuthError as e:
        console.print(f"[red]✗ YouTube Music:[/red] {e}")
        return False
    except Exception as e:  # ytmusicapi hází různé výjimky při prošlých cookies
        console.print(
            f"[red]✗ YouTube Music:[/red] přihlášení nefunguje ({e}). "
            "Zkopíruj hlavičky znovu a spusť `uv run sp2yt ytm-setup`."
        )
        return False
    console.print(
        f"[green]✓ YouTube Music:[/green] {info.get('accountName')} {info.get('channelHandle') or ''}"
    )
    return True


@app.command()
def export(
    file: Annotated[
        Path | None,
        typer.Option(
            "--file",
            "-f",
            help="CSV z Exportify nebo YourLibrary.json z exportu dat. Bez něj se čte ze Spotify API (vyžaduje Premium).",
        ),
    ] = None,
) -> None:
    """Načte oblíbené skladby ze Spotify do lokální databáze. Lze opakovat, nic se nezdvojí."""
    cfg = load_config()
    try:
        tracks = sources.from_file(file) if file else sources.from_api(clients.spotify(cfg))
        with closing(db.connect(cfg.db_path)) as conn, console.status("Načítám skladby…"):
            new, total = db.upsert_tracks(conn, tracks)
    except (sources.SourceError, clients.AuthError, SpotifyException, SpotifyOauthError) as e:
        console.print(f"[red]Export selhal:[/red] {e}")
        raise typer.Exit(1)
    console.print(f"[green]Načtené skladby: {total}[/green], z toho nové v databázi: [bold]{new}[/bold].")


@app.command()
def match() -> None:
    """Najde ke každé skladbě odpovídající skladbu v YouTube Music."""
    _not_yet(4)


@app.command()
def review() -> None:
    """Vypíše nejisté shody do CSV k ruční kontrole."""
    _not_yet(5)


@app.command("import")
def import_() -> None:
    """Označí nalezené skladby v YouTube Music jako „To se mi líbí“."""
    _not_yet(6)


@app.command()
def status() -> None:
    """Přehled stavu přenosu."""
    cfg = load_config()
    with closing(db.connect(cfg.db_path)) as conn:
        counts = db.status_counts(conn)

    table = Table(title="Stav přenosu")
    table.add_column("Stav")
    table.add_column("Počet", justify="right")
    for name, n in counts.items():
        table.add_row(name, str(n))
    table.add_row("[bold]celkem[/bold]", f"[bold]{sum(counts.values())}[/bold]")
    console.print(table)
