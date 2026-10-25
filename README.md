# sp2yt — Spotify oblíbené → YouTube Music

Přenese oblíbené skladby ze Spotify do „Liked music“ v YouTube Music. Bez denních limitů
a bez duplicit: stav každé skladby je v lokální SQLite, takže jde přenos kdykoli přerušit a navázat.
Podrobnosti v [PLAN.md](PLAN.md).

## Instalace

```bash
uv sync
```

## 1. Oblíbené skladby ze Spotify

Vyber jeden ze zdrojů. Export lze kdykoli opakovat, i z jiného zdroje: nic se nezdvojí, jen se doplní nové skladby.

### A) Exportify (doporučeno, bez Spotify Premium)

1. Otevři https://exportify.net, přihlas se Spotify účtem a u **Liked Songs** klikni na *Export*.
2. Stažené CSV načti:

```bash
uv run sp2yt export --file ~/Downloads/liked_songs.csv
```

### B) Export osobních údajů

Na https://www.spotify.com/account/privacy zaškrtni *Account data* a pošli žádost. Spotify pošle ZIP e-mailem
(může to trvat až 30 dní). Z něj použij `YourLibrary.json`:

```bash
uv run sp2yt export --file ~/Downloads/my_spotify_data/YourLibrary.json
```

Tento soubor neobsahuje délku skladby ani datum přidání, takže párování je o něco méně přesné.

### C) Spotify Web API (jen se Spotify Premium)

Od února 2026 smí vývojářskou aplikaci vlastnit jen účet s Premium. Pak nastav přihlášení podle sekce níže a spusť:

```bash
uv run sp2yt export
```

## 2. Přihlášení

### Spotify (jen pro variantu C)

1. Na https://developer.spotify.com/dashboard vytvoř aplikaci (Create app):
   - Redirect URI: `http://127.0.0.1:8888/callback` (přesně takhle, `localhost` Spotify nepovoluje)
   - API: zaškrtni **Web API**
2. `cp .env.example .env` a doplň `SPOTIFY_CLIENT_ID` a `SPOTIFY_CLIENT_SECRET` (Settings aplikace).
3. Při prvním `auth-check` se otevře prohlížeč, potvrdíš přístup a token se uloží do `.spotify_cache`.

### YouTube Music

Stačí hodnota hlavičky **Cookie** z přihlášeného YT Music (Authorization si skript dopočítá sám):

1. Otevři https://music.youtube.com přihlášený a otevři DevTools (⌥⌘I) → záložka **Network**.
2. Do filtru napiš `browse` a klikni v YT Music třeba na Knihovnu, aby se objevil požadavek `browse?…` (metoda POST).
3. Klikni na něj → **Headers** → **Request Headers** → najdi řádek **Cookie** a zkopíruj celou jeho hodnotu
   (je dlouhá, musí obsahovat `SAPISID`). Do chatu ani nikam jinam ji nevkládej, je to přístup k tvému Google účtu.
4. Spusť (cookie se načte ze schránky a uloží do `browser.json`):

```bash
uv run sp2yt ytm-setup
```

Pokud máš v prohlížeči přihlášeno víc Google účtů a YT Music běží na jiném než prvním,
přidej `--authuser 1` (hodnota hlavičky `X-Goog-AuthUser`).

Přihlášení platí, dokud se z YT Music v tom prohlížeči neodhlásíš.

### Výběr kanálu

Pokud máš pod jedním Google přihlášením víc kanálů (brand účty), vyber ten, kde máš YT Music:

```bash
uv run sp2yt ytm-account              # vypíše kanály, šipka = aktuální
uv run sp2yt ytm-account @MujKanal    # uloží výběr do .env (YTM_USER)
```

### Ověření

```bash
uv run sp2yt auth-check
```

`.env`, `browser.json` a `.spotify_cache` obsahují přístupové údaje a jsou v `.gitignore`.

## 3. Přenos

```bash
uv run sp2yt match                 # najde skladby v YT Music (jen čte)
uv run sp2yt import --dry-run      # ukáže, co se lajkne
uv run sp2yt import --limit 20     # zkouška na 20 nejstarších
uv run sp2yt import                # zbytek; lze přerušit a pustit znovu
uv run sp2yt status
```
