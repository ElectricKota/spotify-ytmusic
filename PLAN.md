# Spotify → YouTube Music: přesun oblíbených skladeb

## Proč online nástroje selhávají

- **Limity**: používají oficiální YouTube Data API, které má denní kvótu 10 000 jednotek
  a přidání jedné skladby stojí 50 jednotek, takže zvládnou zhruba 200 skladeb denně.
- **Duplicity**: nepamatují si, co už přenesly. Po pádu nebo dalším spuštění přidávají
  všechno znovu a nekontrolují, co už v YT Music je.

## Zvolené technologie

| Část | Volba | Důvod |
|---|---|---|
| Jazyk | Python 3.12 (přes `uv`) | Nejlepší knihovny pro obě služby |
| Spotify | `spotipy` + oficiální Web API (OAuth) | Stabilní, stránkování po 50 skladbách |
| YouTube Music | `ytmusicapi` (interní API YT Music, přihlášení přes hlavičky z prohlížeče) | **Bez denní kvóty**, umí vyhledávání v katalogu „Songs“ i hromadné přidávání |
| Stav | SQLite (`state.db`) | Odolné proti pádu, lze kdykoli navázat, chrání proti duplicitám |
| CLI | `typer` + `rich` | Přehledné příkazy a průběh |

## Ochrana proti duplicitám (hlavní požadavek)

1. Každá skladba ze Spotify má v DB stav `pending → matched → added` (nebo `not_found` / `review`).
2. Před zápisem se načte, co už v cílovém seznamu v YT Music je, a tyto `videoId` se přeskočí.
3. Deduplikace i na vstupu: stejné `videoId` z různých Spotify záznamů (remaster, jiné album) se přidá jen jednou.
4. Každý příkaz je **idempotentní**: opakované spuštění nic nezdvojí, jen dokončí zbytek.

## Párování skladeb (kvalita)

- Hledá se v YT Music s filtrem `songs` (oficiální audio, ne videoklipy) dotazem `umělec + název`.
- Skóre kandidáta = podobnost názvu + shoda umělce + rozdíl délky (±3 s je ideální).
  Normalizace odstraní „- Remastered 2011“, „(feat. …)“ a diakritiku pro porovnání.
- Jistá shoda se přijme automaticky. Nejistá se uloží jako `review` a zapíše do CSV,
  kde jde ručně potvrdit nebo opravit. Když nic není v `songs`, zkusí se `videos`.
- Vyhledávání běží paralelně (několik vláken) s mírným throttlingem. Cca 2 000 skladeb ≈ 10–15 min.

## Cíl v YT Music

- **Varianta A (doporučená)**: skladby „lajkovat“, aby se objevily v *Liked music*. Přidávají se
  od nejstarší po nejnovější, aby zůstalo pořadí jako na Spotify.
- **Varianta B**: přidat je do nového playlistu „Spotify Liked“ (hromadně, rychlejší).
- Lze obojí.

## Kroky implementace

1. ✅ **Kostra projektu**: `uv` projekt, závislosti, `.env.example`, `.gitignore` (tokeny a hlavičky nikdy do gitu).
2. **Přihlášení**:
   - Spotify: vytvořit aplikaci na developer.spotify.com, redirect `http://127.0.0.1:8888/callback`, scope `user-library-read`.
   - YT Music: `ytmusicapi browser` (vložíš hlavičky požadavku z prohlížeče) → `browser.json`.
   - Příkaz `auth-check` ověří obě strany.
3. **Export ze Spotify**: `export` stáhne všechny oblíbené (název, umělci, album, délka, ISRC, datum přidání) do DB.
4. **Párování**: `match` najde `videoId` pro každou skladbu, uloží skóre, vygeneruje `review.csv`.
5. **Kontrola**: `review` zobrazí nejisté shody, ruční opravy z CSV se načtou zpět (`apply-review`).
6. **Import**: `import --target liked|playlist [--dry-run]` zapíše do YT Music s kontrolou existujících položek.
7. **Report**: `status` ukáže přehled (přeneseno, nenalezeno, k revizi) a export nenalezených do CSV.

Každý krok je samostatný příkaz, takže ho po implementaci rovnou otestujeme na tvém účtu
(nejdřív na pár skladbách přes `--limit 20`).
