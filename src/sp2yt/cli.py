from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeRemainingColumn
from rich.table import Table
from spotipy import SpotifyException, SpotifyOauthError
from ytmusicapi.exceptions import YTMusicError

from sp2yt import clients, db, sources
from sp2yt.config import Config, load_config
from sp2yt.matcher import ACCEPT, REVIEW, Matcher

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
        typer.Option(help="Soubor s hlavičkami nebo cookie; bez něj se načtou ze schránky."),
    ] = None,
    authuser: Annotated[
        int,
        typer.Option(help="Pořadí Google účtu v prohlížeči (X-Goog-AuthUser), když se ukládá jen cookie."),
    ] = 0,
) -> None:
    """Uloží přihlášení k YouTube Music z hodnoty Cookie (nebo celých hlaviček) zkopírované z prohlížeče."""
    cfg = load_config()
    if file and not file.exists():
        console.print(f"[red]Soubor {file.resolve()} neexistuje.[/red]")
        raise typer.Exit(1)
    raw = file.read_text() if file else clients.read_clipboard()
    try:
        clients.save_ytm_headers(cfg, raw, authuser)
    except YTMusicError as e:
        console.print(f"[red]Hlavičky se nepodařilo načíst:[/red] {e}")
        raise typer.Exit(1)
    console.print(f"[green]Uloženo do {cfg.ytm_auth_file}.[/green] Ověřuji přihlášení…")
    if not _check_ytmusic(cfg):
        raise typer.Exit(1)


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
        # Nepřihlášený uživatel dostane menu bez hlavičky účtu → ytmusicapi selže na cestě k 'header'
        reason = "YouTube tě nepoznal jako přihlášeného" if "'header'" in str(e) else str(e)[:200]
        console.print(
            f"[red]✗ YouTube Music:[/red] přihlášení nefunguje ({reason}). "
            "Zkopíruj Cookie znovu z přihlášeného music.youtube.com a spusť `uv run sp2yt ytm-setup`."
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
def match(
    limit: Annotated[int | None, typer.Option(help="Zpracovat jen prvních N skladeb (na zkoušku).")] = None,
    retry: Annotated[
        bool, typer.Option("--retry", help="Znovu hledat i skladby ve stavu not_found a review.")
    ] = False,
    workers: Annotated[int, typer.Option(min=1, max=8, help="Počet paralelních vyhledávání.")] = 4,
) -> None:
    """Najde ke každé skladbě odpovídající skladbu v YouTube Music. Lze přerušit a navázat."""
    cfg = load_config()
    try:
        clients.ytmusic(cfg)
    except clients.AuthError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    statuses = ("pending", "not_found", "review") if retry else ("pending",)
    matcher = Matcher(lambda: clients.ytmusic(cfg))
    with closing(db.connect(cfg.db_path)) as conn:
        todo = db.tracks_to_match(conn, statuses, limit)
        if not todo:
            console.print("Není co hledat. Nové skladby přidáš přes `export`, nenalezené zkusíš znovu s `--retry`.")
            return

        found = {"matched": 0, "review": 0, "not_found": 0, "error": 0}
        progress = Progress(
            TextColumn("[bold]Hledám"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeRemainingColumn(),
            TextColumn("[green]✓{task.fields[matched]} [yellow]?{task.fields[review]} [red]✗{task.fields[not_found]}"),
            console=console,
        )
        with progress, ThreadPoolExecutor(max_workers=workers) as pool:
            task = progress.add_task("match", total=len(todo), matched=0, review=0, not_found=0)
            futures = {pool.submit(matcher.best_match, t): t for t in todo}
            try:
                for i, future in enumerate(as_completed(futures), 1):
                    track = futures[future]
                    try:
                        best = future.result()
                    except Exception as e:  # síť / YT; skladba zůstane pending a zkusí se příště
                        found["error"] += 1
                        db.save_match(conn, track["spotify_id"], "pending", error=str(e)[:500])
                    else:
                        status = (
                            "matched" if best and best.score >= ACCEPT
                            else "review" if best and best.score >= REVIEW
                            else "not_found"
                        )
                        found[status] += 1
                        db.save_match(conn, track["spotify_id"], status, best)
                    if i % 20 == 0:
                        conn.commit()
                    progress.update(task, advance=1, **{k: v for k, v in found.items() if k != "error"})
            except KeyboardInterrupt:
                pool.shutdown(cancel_futures=True)
                conn.commit()
                console.print("[yellow]Přerušeno, hotové výsledky jsou uložené. Další `match` naváže.[/yellow]")
                raise typer.Exit(130)
        conn.commit()
        duplicates = db.mark_duplicates(conn)

    console.print(
        f"Jistá shoda: [green]{found['matched']}[/green], ke kontrole: [yellow]{found['review']}[/yellow], "
        f"nenalezeno: [red]{found['not_found']}[/red], duplicitní: {duplicates}"
        + (f", chyby (zkusí se příště): [red]{found['error']}[/red]" if found["error"] else "")
    )


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
