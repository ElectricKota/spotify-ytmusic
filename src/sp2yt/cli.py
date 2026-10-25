import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from pathlib import Path
from typing import Annotated

import typer
from dotenv import set_key, unset_key
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeRemainingColumn
from rich.table import Table
from spotipy import SpotifyException, SpotifyOauthError
from ytmusicapi import LikeStatus
from ytmusicapi.exceptions import YTMusicError

from sp2yt import clients, db, sources, videos
from sp2yt.config import Config, load_config
from sp2yt.matcher import ACCEPT, REVIEW, Matcher, clean_title, normalize

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


@app.command("ytm-account")
def ytm_account(
    handle: Annotated[str | None, typer.Argument(help="@handle kanálu, do kterého se má importovat.")] = None,
) -> None:
    """Vypíše kanály pod přihlášeným Google účtem, nebo vybere ten, do kterého se importuje."""
    cfg = load_config()
    try:
        channels = clients.ytmusic_channels(cfg)
    except Exception as e:
        console.print(f"[red]Kanály se nepodařilo načíst:[/red] {str(e)[:200]}")
        raise typer.Exit(1)

    if handle is None:
        for c in channels:
            active = c.user_id == cfg.ytm_user or (cfg.ytm_user is None and c.selected)
            console.print(f"{'[green]→[/green]' if active else ' '} {c.name} [bold]{c.handle}[/bold]")
        console.print("Vybereš ho přes `uv run sp2yt ytm-account @handle`.")
        return

    chosen = next((c for c in channels if c.handle.lower() == handle.lower()), None)
    if chosen is None:
        console.print(f"[red]Kanál {handle} nenalezen.[/red] Dostupné: {', '.join(c.handle for c in channels)}")
        raise typer.Exit(1)
    env = Path(".env")
    env.touch()
    # Výchozí kanál přihlášení nepotřebuje ID; ostatní (brand účty) se volají jeho jménem
    if chosen.selected:
        unset_key(env, "YTM_USER")
    else:
        set_key(env, "YTM_USER", chosen.user_id, quote_mode="never")
    console.print(f"[green]Import půjde do kanálu {chosen.name} {chosen.handle}[/green] (uloženo v .env).")


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


MAX_CONSECUTIVE_ERRORS = 3


def _is_rate_limited(e: Exception) -> bool:
    # Google místo JSON vrátí HTML stránku „Sorry…“ (403) → ytmusicapi selže na parsování JSON
    return isinstance(e, json.JSONDecodeError) or "403" in str(e) or "Expecting value" in str(e)


def _song_key(title: str, artists: list[str]) -> tuple[str, str]:
    return clean_title(title), normalize(artists[0]) if artists else ""


@app.command("import")
def import_(
    limit: Annotated[int | None, typer.Option(help="Lajknout jen N nejstarších skladeb (na zkoušku).")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Jen vypsat, co by se lajklo, nic neměnit.")] = False,
    delay: Annotated[float, typer.Option(min=0, help="Pauza mezi lajky v sekundách.")] = 2.0,
) -> None:
    """Označí nalezené skladby v YouTube Music jako „To se mi líbí“, od nejstarší po nejnovější.

    YT Music řadí Liked music podle času lajku, proto se lajkuje postupně v pořadí ze Spotify.
    Skladby, které už v YT Music lajknuté jsou, se jen označí jako hotové.
    """
    cfg = load_config()
    try:
        yt = clients.ytmusic(cfg)
    except clients.AuthError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    with closing(db.connect(cfg.db_path)) as conn:
        todo = db.tracks_to_import(conn, limit)
        if not todo:
            console.print("Není co importovat. Spusť nejdřív `match`, nebo je už všechno přidané (`status`).")
            return

        if dry_run:
            table = Table(title=f"Lajklo by se {len(todo)} skladeb (prvních 20, od nejstarší)")
            for col in ("Oblíbeno na Spotify", "Spotify", "YouTube Music"):
                table.add_column(col)
            for t in todo[:20]:
                table.add_row(
                    (t["added_at"] or "")[:10],
                    f"{', '.join(t['artists'])} – {t['name']}",
                    f"{', '.join(t['match_artists'])} – {t['match_title']}",
                )
            console.print(table)
            return

        account = yt.get_account_info()
        console.print(f"Cílový kanál: [bold]{account.get('accountName')} {account.get('channelHandle') or ''}[/bold] "
                      "(změníš přes `sp2yt ytm-account`)")
        with console.status("Načítám, co už máš v YT Music lajknuté…"):
            liked = yt.get_liked_songs(limit=None).get("tracks", [])
        already = {t["videoId"] for t in liked if t.get("videoId")}
        # Stejnou skladbu můžeš mít lajknutou v jiné verzi (klip vs. audio) → porovnat i název a interpreta
        already_keys = {_song_key(t["title"], [a["name"] for a in t.get("artists") or []]) for t in liked}

        added = skipped = failed = consecutive_errors = 0
        progress = Progress(
            TextColumn("[bold]Lajkuji"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeRemainingColumn(),
            TextColumn("{task.fields[current]}"),
            console=console,
        )
        try:
            with progress:
                task = progress.add_task("import", total=len(todo), current="")
                for t in todo:
                    progress.update(task, current=f"{t['artists'][0] if t['artists'] else ''} – {t['name']}"[:50])
                    if (
                        t["video_id"] in already
                        or _song_key(t["match_title"], t["match_artists"]) in already_keys
                        or _song_key(t["name"], t["artists"]) in already_keys
                    ):
                        db.mark_added(conn, t["spotify_id"])
                        skipped += 1
                    else:
                        try:
                            yt.rate_song(t["video_id"], LikeStatus.LIKE)
                        except Exception as e:
                            failed += 1
                            consecutive_errors += 1
                            db.save_import_error(conn, t["spotify_id"], str(e)[:500])
                            if _is_rate_limited(e):
                                progress.stop()
                                console.print(
                                    "[red]YouTube dočasně zablokoval lajkování[/red] (ochrana proti automatickému "
                                    "provozu, HTTP 403). Nezkoušej to hned znovu, počkej několik hodin; "
                                    "import pak naváže. Pomalejší tempo: `--delay 5`."
                                )
                                break
                            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                                progress.stop()
                                console.print(
                                    f"[red]{MAX_CONSECUTIVE_ERRORS} chyby za sebou, zastavuji.[/red] Poslední: {str(e)[:200]}\n"
                                    "Nejspíš vypršelo přihlášení (`uv run sp2yt auth-check`) nebo YT omezuje počet "
                                    "požadavků; zkus to za chvíli znovu, import naváže."
                                )
                                break
                        else:
                            consecutive_errors = 0
                            already.add(t["video_id"])
                            db.mark_added(conn, t["spotify_id"])
                            added += 1
                            time.sleep(delay)
                    progress.advance(task)
        except KeyboardInterrupt:
            console.print("[yellow]Přerušeno, další `import` naváže tam, kde skončil.[/yellow]")

        remaining = db.status_counts(conn)["matched"]

    console.print(
        f"Lajknuto: [green]{added}[/green], už bylo lajknuté: {skipped}"
        + (f", chyby: [red]{failed}[/red]" if failed else "")
        + f". Zbývá importovat: [bold]{remaining}[/bold]."
    )


@app.command("fix-videos")
def fix_videos(
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Jen vypsat, co by se vyměnilo.")] = False,
    workers: Annotated[int, typer.Option(min=1, max=8, help="Počet paralelních vyhledávání.")] = 4,
) -> None:
    """Vymění v Liked music videoklipy za audio verze skladeb (lepší pro offline a auto).

    Nejdřív lajkne audio verzi, teprve pak odlajkne klip. Klipy bez jisté audio verze nechá.
    """
    cfg = load_config()
    try:
        yt = clients.ytmusic(cfg)
    except clients.AuthError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    account = yt.get_account_info()
    console.print(f"Kanál: [bold]{account.get('accountName')} {account.get('channelHandle') or ''}[/bold]")

    with console.status("Načítám Liked music…"):
        liked = yt.get_liked_songs(limit=None).get("tracks", [])
    liked_ids = {t["videoId"] for t in liked if t.get("videoId")}
    clips = [t for t in liked if t.get("videoId") and t.get("videoType") != videos.AUDIO]
    if not clips:
        console.print("V Liked music nejsou žádné videoklipy.")
        return

    local = threading.local()

    def find(track: dict) -> videos.Replacement:
        if not hasattr(local, "yt"):
            local.yt = clients.ytmusic(cfg)
        return videos.find_audio(local.yt, track)

    with ThreadPoolExecutor(max_workers=workers) as pool, console.status(f"Hledám audio verze pro {len(clips)} klipů…"):
        results = list(pool.map(find, clips))
    found = [r for r in results if r.audio_id]

    table = Table(title=f"Audio verze nalezena u {len(found)} z {len(clips)} klipů")
    for col in ("Klip", "→ Skladba", "Shoda"):
        table.add_column(col)
    for r in found:
        table.add_row(
            f"{', '.join(r.artists)} – {r.video_title}",
            f"{', '.join(r.audio_artists)} – {r.audio_title}",
            f"{r.score:.2f}",
        )
    console.print(table)
    if dry_run or not found:
        return

    replaced = 0
    with closing(db.connect(cfg.db_path)) as conn:
        for r in found:
            try:
                if r.audio_id not in liked_ids:
                    yt.rate_song(r.audio_id, LikeStatus.LIKE)
                    liked_ids.add(r.audio_id)
                yt.rate_song(r.video_id, LikeStatus.INDIFFERENT)
            except Exception as e:
                if _is_rate_limited(e):
                    console.print("[red]YouTube dočasně zablokoval lajkování (HTTP 403), zkus to za několik hodin.[/red]")
                    break
                console.print(f"[red]Chyba u {r.video_title}:[/red] {str(e)[:200]}")
                continue
            db.replace_video(conn, r.video_id, r.audio_id)
            replaced += 1
            time.sleep(2)
    console.print(
        f"[green]Vyměněno {replaced} klipů za audio.[/green] Zbylých {len(clips) - replaced} klipů audio verzi nemá "
        "(živáky, covery, mixy) a zůstávají."
    )


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
