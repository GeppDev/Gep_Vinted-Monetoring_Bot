# GepVintage

Discord-Bot für Vinted: Überwacht Such-URLs und postet neue Listings als Embed in einen Kanal. Kein Vinted-Login, kein automatischer Kauf.

## Funktionen

- Mehrere Watches pro Server (eigene Vinted-Such-URL je Watch)
- Filter: Preis, Keywords (inkl./exkl.), Marken
- Duplikat-Schutz pro Watch und pro Server (`seen`, `sent_guild`)
- Optional: privater Monitor-Kanal pro Nutzer (`/privat`)
- Optional: Fast-Open-Assistent (öffnet Treffer im Browser auf dem Rechner, auf dem der Bot läuft)
- Slash- und Prefix-Befehle

**Nicht enthalten:** Kaufen, Angebote, Favoriten auf Vinted, Zugriff auf dein Vinted-Konto.

## Voraussetzungen

- Python 3.11+ (getestet mit 3.13)
- Discord-Bot-Token ([Discord Developer Portal](https://discord.com/developers/applications))
- Bot mit Berechtigungen: Nachrichten senden, Embeds, Slash-Befehle

## Installation

```bash
git clone <dein-repo-url>
cd Vinted_Bot_GepVintage
python -m venv .venv
.venv\Scripts\activate    # Windows
# source .venv/bin/activate   # Linux/macOS
pip install -r requirements.txt
cp .env.example .env
```

`.env` bearbeiten und mindestens `DISCORD_TOKEN` setzen.

Bot starten:

```bash
python -m gepvintage
```

Datenbank und Logs landen standardmäßig unter `data/gepvintage.sqlite3`.

## Discord einrichten

1. Application anlegen, Bot erstellen, Token kopieren → `DISCORD_TOKEN`
2. Bot auf den Server einladen (Scopes: `bot`, `applications.commands`)
3. Im Zielkanal: Bot darf schreiben / Embeds senden
4. Für `/privat erstellen`: Berechtigung **Kanäle verwalten**

## Konfiguration (`.env`)

| Variable | Beschreibung | Standard |
|----------|--------------|----------|
| `DISCORD_TOKEN` | Bot-Token | — |
| `COMMAND_PREFIX` | Prefix neben Slash | `!` |
| `DEFAULT_POLL_INTERVAL_SEC` | Abfrage-Intervall in Sekunden (8–120) | `22` |
| `DATA_DIR` | Ordner für SQLite | `data` |
| `FAST_ASSISTANT_ENABLED` | Fast-Open global | `true` |
| `FAST_ASSISTANT_SOUND` | Ton bei Fast-Open | `false` |
| `FAST_ASSISTANT_SOUND_PATH` | Pfad zu `.wav` (Windows) | leer |
| `FAST_ASSISTANT_MAX_PER_MINUTE` | Max. Tab-Opens/Minute | `10` |
| `FAST_ASSISTANT_MAX_PARALLEL_TABS` | Parallele Tabs | `3` |
| `FAST_ASSISTANT_MIN_INTERVAL_SEC` | Mindestabstand zwischen Opens | `1.2` |
| `PRIVATE_MONITOR_ENABLED` | `/privat` erlauben | `true` |
| `PRIVATE_MONITOR_CATEGORY_ID` | Kategorie für private Kanäle (optional) | leer |

## Vinted-URL

Kopiere die **komplette Katalog-/Such-URL** aus dem Browser (mit allen Filtern in der Adresszeile).

Kategorie-Parameter werden normalisiert, z. B.:

- `catalog`, `catalog[]`, `catalog_id` → `catalog_ids`
- Pfad `/catalog/<id>` wird ebenfalls als Kategorie übernommen

Sortierung wird auf `newest_first` gesetzt.

Beispiel:

```
https://www.vinted.de/catalog?catalog_ids=123&brand_ids[]=53
```

Dieselbe Suche lässt sich nicht zweimal anlegen (`/vinted add` erkennt Duplikate).

## Befehle

Alle Befehle als Slash (`/…`) oder mit Prefix (`!vinted …` usw.).

### `/vinted`

| Befehl | Beschreibung |
|--------|----------------|
| `add` | Watch anlegen (URL, optional Kanal, Label, Intervall) |
| `remove` | Watch löschen |
| `list` | Alle Watches (bei vielen Einträgen mehrere Nachrichten) |
| `filter` | Preis, Keywords, Marken pro Watch |
| `start` / `stop` | Monitoring an/aus (eine Watch oder alle) |
| `status` | Latenz, Anzahl Watches |
| `channel` | Zielkanal ändern |
| `interval` | Intervall pro Watch (8–120 s) |

Server-Befehle brauchen meist **Server verwalten**.

Beim ersten Lauf nach `add` werden bestehende Treffer nur gespeichert, **ohne** Discord-Nachricht (Snapshot).


Steuert den lokalen Assistenten (Browser auf dem Bot-Host, kein Auto-Kauf):

| Befehl | Beschreibung |
|--------|----------------|
| `open` | Bei Treffern Tab öffnen (pro Server) |
| `sound` | Ton an/aus |
| `limits` | Opens/Minute, parallele Tabs, Mindestabstand |
| `priority` | Nur bei Preis/Marke/Keywords Fast-Open |
| `bestdeal` | Markierung unter Preisgrenze |
| `status` | Einstellungen anzeigen |

### `/privat`

| Befehl | Beschreibung |
|--------|----------------|
| `erstellen` | Privater Kanal + eine URL |
| `link` | URL ändern (neuer Snapshot) |
| `start` / `stop` | Monitor an/aus |
| `status` | Infos |
| `löschen` | Kanal + Watch entfernen |

### `/uebersicht`

Statistik aus Bot-Daten (Alerts, Watches) — keine Vinted-Konto-Daten.

## Intervall und Geschwindigkeit

- Jede Watch wird nacheinander abgefragt (kein paralleles Polling).
- Effektives Intervall: eingestellte Sekunden **plus 0–3,5 s Jitter**.
- Zwischen Watches: kurze Pause (~0,35–0,9 s).

**Richtwerte bei ~10 Watches:**

| Ziel | `DEFAULT_POLL_INTERVAL_SEC` |
|------|-------------------------------|
| stabil | 15–20 |
| schnell | 10–12 |
| aggressiv | 8–10 (mehr Fehler/Blocks möglich) |

Pro Watch: `/vinted interval watch_id:<id> seconds:<8-120>`.

Sehr niedrige Werte erhöhen die Last auf Vinted (Session-Cookie-Fehler in den Logs).

## Datenbank

SQLite unter `DATA_DIR/gepvintage.sqlite3`:

| Tabelle | Inhalt |
|---------|--------|
| `watches` | URLs, Kanal, Filter, Intervall, aktiv, Snapshot-Status |
| `seen` | Bereits bekannte Artikel-IDs pro Watch |
| `sent_guild` | Bereits gepostete Artikel-IDs pro Server (Dedupe) |
| `guild_fast_assistant` | Fastbuy-Einstellungen pro Server |

## Projektstruktur

```
gepvintage/
  __main__.py      # Einstieg
  bot.py           # Discord-Bot
  config.py        # .env
  poll_service.py  # Abfrage-Loop
  scraper_pool.py  # vinted_scraper Sessions
  storage.py       # SQLite
  vinted_util.py   # URL-Parsing, Embeds, Filter
  cog_vinted.py    # /vinted
  cog_fastbuy.py   # /fastbuy
  cog_privat.py    # /privat
  cog_uebersicht.py
  fast_assistant.py
```

## Hinweise

- Der Bot nutzt die öffentliche Katalog-API über [`vinted_scraper`](https://pypi.org/project/vinted-scraper/). Vinted kann Antworten oder Cookies ändern — dann helfen Neustart und höheres Intervall.
- Automatisierung kann gegen die Vinted-Nutzungsbedingungen verstoßen. Nutzung auf eigenes Risiko.
- **`.env` nicht committen** (Token).


