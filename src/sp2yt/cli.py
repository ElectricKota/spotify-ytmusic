from contextlib import closing

import typer
from rich.console import Console
from rich.table import Table

from sp2yt import db
from sp2yt.config import load_config

app = typer.Typer(
    help="Přesun oblíbených skladeb ze Spotify do YouTube Music bez duplicit.",
    no_args_is_help=True,
)
console = Console()


def _not_yet(step: int) -> None:
    console.print(f"[yellow]Zatím neimplementováno (krok {step}).[/yellow]")
    raise typer.Exit(1)


@app.command("auth-check")
def auth_check() -> None:
    """Ověří přihlášení ke Spotify i YouTube Music."""
    _not_yet(2)


@app.command()
def export() -> None:
    """Stáhne oblíbené skladby ze Spotify do lokální databáze."""
    _not_yet(3)


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
