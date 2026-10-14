# sp2yt — Spotify oblíbené → YouTube Music

Přenese oblíbené skladby ze Spotify do „Liked music“ v YouTube Music. Bez denních limitů
a bez duplicit: stav každé skladby je v lokální SQLite, takže jde přenos kdykoli přerušit a navázat.
Podrobnosti v [PLAN.md](PLAN.md).

## Instalace

```bash
uv sync
```

## Přihlášení

### Spotify

1. Na https://developer.spotify.com/dashboard vytvoř aplikaci (Create app):
   - Redirect URI: `http://127.0.0.1:8888/callback` (přesně takhle, `localhost` Spotify nepovoluje)
   - API: zaškrtni **Web API**
2. `cp .env.example .env` a doplň `SPOTIFY_CLIENT_ID` a `SPOTIFY_CLIENT_SECRET` (Settings aplikace).
3. Při prvním `auth-check` se otevře prohlížeč, potvrdíš přístup a token se uloží do `.spotify_cache`.

### YouTube Music

1. V prohlížeči otevři https://music.youtube.com přihlášený a otevři DevTools (⌥⌘I) → záložka **Network**.
2. Do filtru napiš `browse` a klikni v YT Music třeba na Knihovnu, aby se objevil požadavek `browse?…` (metoda POST).
3. Klikni na něj → **Headers** → **Request Headers**:
   - Chrome: pravým tlačítkem na požadavek → *Copy* → *Copy request headers*
   - Firefox: přepni na *Raw* a zkopíruj celý text
4. Spusť (hlavičky se načtou ze schránky a uloží do `browser.json`):

```bash
uv run sp2yt ytm-setup
```

Přihlášení platí, dokud se z YT Music v tom prohlížeči neodhlásíš.

### Ověření

```bash
uv run sp2yt auth-check
```

`.env`, `browser.json` a `.spotify_cache` obsahují přístupové údaje a jsou v `.gitignore`.
