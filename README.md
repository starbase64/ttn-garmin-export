# TTN Garmin Export

Lokale Windows-App zum Exportieren und Durchsuchen deiner Garmin-Connect-Daten. Deutsch/Englisch umschaltbar. Unabhängiges Community-Projekt; kein offizielles Garmin-Produkt.

## Quellstand 0.5.3-preview: schnellere Downloads

Der aktuelle Quellstand bietet 1–8 parallele Abfragen (Standard 3), gemeinsamer Mindestabstand von 0,5 Sekunden statt der bisherigen festen 2 Sekunden bei Sicherungen, begrenzte Wiederholungen und Erhalt erfolgreich geladener Antworten bei Abbruch. Garmin kann weiterhin Tagesabfragen und Ratenlimits erfordern; eine allgemeingültige Beschleunigung um einen festen Faktor ist nicht belegt.

Außerdem: automatische Wiederherstellung gemerkter Sitzungen (nach dem Update einmal mit gesetztem Haken anmelden), Restzeit für parallele Downloads und eine Datenansicht mit großer Karte rechts. „Anleitung“ ist der letzte Reiter. Das bestehende Release `v0.5.0-preview` enthält diese Änderungen noch nicht; aktuelle Windows-Starter entstehen als Artefakte der [Actions-Prüfläufe](https://github.com/starbase64/ttn-garmin-export/actions).

Neu in 0.5.3: ein aktueller Export pro Account unter `exports/current`, optionale getrennte Ergebnisordner und ein gemeinsamer SQLite-Bestand für Online-Export und Offline-Sicherung. Neue Ordner bedeuten keine neuen Downloads. Alte Ergebnisordner bleiben erhalten.

Neu in 0.5.2: Das ermittelte erste Aktivitätsdatum und die gewählten Anfangsdaten auf Export und Daten werden pro Konto gespeichert. Ein Zurücksetzen-Button verwendet das gemerkte Kontodatum ohne neue Garmin-Anfrage.

Optional: Tages-Wellness-ZIPs unverändert sichern. Diese Rohdateien werden derzeit nicht in CSV/Excel umgewandelt und ersetzen JSON-Abfragen nicht automatisch. Anleitung in der App erklärt die Auswahl.

## Windows starten — ohne Python-Installation

1. Unter [Releases](https://github.com/starbase64/ttn-garmin-export/releases) das **Windows-Starter-ZIP** herunterladen.
2. **Vollständig** in einen eigenen beschreibbaren Ordner entpacken, beispielsweise im Benutzerordner. Nicht direkt im ZIP starten.
3. **Start-TTN-Garmin-Export.cmd** doppelklicken. Beim ersten Start werden eine eigene Python-Laufzeit und die festgelegten Abhängigkeiten eingerichtet. Internet wird für diese Einrichtung benötigt; der Fortschritt erscheint im Konsolenfenster.
4. Danach öffnet sich die App. Die Einrichtung braucht keine Administratorrechte und ändert keine vorhandene Python-Installation, PATH oder Registry. Das Programm prüft lokale Komponenten bei jedem Start und lädt fehlende/beschädigte Pakete bei Bedarf nach.

Python kommt von **python.org**, Pakete von **PyPI / files.pythonhosted.org**. Versionen und SHA-256-Prüfsummen stehen in `windows-dependencies.lock.json`. Keine stillen Updates auf neuere Bibliotheksversionen, kein pip-Bootstrap aus beliebigen Skripten. Die komplette Erstinstallation lädt deutlich mehr Daten als das kleine Starter-ZIP. `runtime/` und `.setup-cache/` bleiben lokal im Programmordner; letzterer erlaubt Reparaturen ohne erneuten Download. Internet bleibt für Garmin-Anmeldung und Online-Datenabruf nötig. Offline-Datenbanken lassen sich anschließend ohne Garmin-Anmeldung verwenden.

Bei Problemen die Konsolenmeldung und `setup.log` im Programmordner prüfen. Unternehmensrichtlinien können PowerShell-Skripte sperren. Nicht parallel in denselben Ordner installieren/starten. Dieses erste Release ist eine **Vorschau**; den jeweiligen Stand der Windows-Prüfung zeigen [GitHub Actions](https://github.com/starbase64/ttn-garmin-export/actions) und die Release-Hinweise.

**Vorhandene Nutzer:** Anmeldeschlüssel und App-Profil bleiben unter dem bisherigen internen Namen `GarminExportStudio` erhalten. Zum Fortsetzen bisheriger Downloads denselben Ausgabeordner und dieselbe SQLite-Datei auswählen. Daten werden nicht automatisch verschoben. Neuer Standardordner: **Downloads\Garmin_Export**.

## Funktionen

- Mehrjährige Zeiträume; CSV, XLSX, JSON; Datenarten und Feldpfade auswählen.
- Aktivitätenliste mit Datum, Name, Sportart, Distanz und Dauer; optionale Originaldateien und Detaildaten.
- Tagesübersichten in Zeiträumen; optional Tageskennzahlen statt vollständiger Gesundheitsverläufe. Fehlende Antworttage werden gezielt geprüft, Ratenlimits stoppen den Lauf.
- Fortsetzbare Offline-Sicherung, begrenzte Exportvorschau, geschätzte Download-Restzeit, Sitzungslogs.
- Lokale Aktivitätstabelle, Filter, Ctrl/Shift-Mehrfachauswahl, Karten und Heatmap; Import unterstützter Aktivitätsdateien und Datenbanksicherung.
- Eingebaute [deutsche](docs/guide-de.html) und [englische](docs/guide-en.html) Anleitung.

**Vollständigkeit und Geschwindigkeit:** „Alle unterstützten Daten sichern“ verwendet weiterhin vollständige Messverläufe, weshalb viele Kategorien täglich einzeln abgefragt werden. Der schnellere Kennzahlenmodus enthält weniger Details und wird bei „Nur gewählte Daten sichern“ berücksichtigt. Es gibt kein Versprechen eines vollständigen Garmin-Kontobackups. Eine Tageszeile im Protokoll entspricht nicht zwingend einer eigenen Netzwerkanfrage.

## English

A local bilingual Garmin Connect exporter with CSV/XLSX/JSON, arbitrary date ranges, resumable offline SQLite storage, bounded previews, ETA, activity maps and import/export tools. Download the Windows Starter ZIP from Releases, extract everything to a writable folder and double-click `Start-TTN-Garmin-Export.cmd`. On first launch it installs its own Python and pinned dependencies from official sources, with SHA-256 verification. No admin rights or existing Python required. Missing packages are repaired locally; subsequent offline archive use needs no Garmin login. Logs: console and `setup.log`. This is a preview; see Actions and release notes for actual Windows validation status. Full archive sync retains detailed daily retrieval; daily-statistics mode is explicitly opt-in for selected sync. Independent project, not affiliated with Garmin.

## Entwicklung / Development

```sh
python -m venv .venv
# Activate .venv for your operating system, then:
python -m pip install -r requirements.txt -r requirements-build.txt
python -m pytest -q
python app.py --demo
```

Synthetic tests do not access Garmin accounts. Run `python app.py --smoke-test` for app/export/archive/import checks. The GUI source also runs on Linux with its dependencies; the small automatic starter targets **Windows 10/11 x64** only.

### Starter neu bauen / Rebuild starter

```sh
python build_starter.py dist/TTN-Garmin-Export-Windows-Starter.zip
```

Regenerate the lock only after reviewing dependency updates. Resolve Windows wheels with `pip download --only-binary=:all: --platform win_amd64 --python-version 3.13 --implementation cp --abi cp313 -r requirements.txt -d windows-wheels`, then run `python generate_windows_lock.py windows-wheels`. The generator validates every wheel against PyPI's published SHA-256. Do not commit downloaded wheels or runtimes. `.github/workflows/starter.yml` checks the actual Windows embedded-runtime setup, warm offline startup and cached missing-package repair. The separate manually started Windows workflow can build the full PyInstaller package.

## Credits, privacy and licence

MIT for this application's code; dependency licences remain applicable. Uses [python-garminconnect](https://github.com/cyberjunky/python-garminconnect), PySide6, openpyxl and fitdecode. **GarminDB is not a code dependency of this app**; its approach was considered during research. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Garmin is a trademark of its owner; this app is neither made nor endorsed by Garmin. GitHub name search found existing `garmin-data-export` projects, so this project uses the distinct TTN name; this is not a trademark clearance.

Garmin credentials, tokens, user exports, GPS data and local databases are not part of the repository or release. Setup never asks for Garmin credentials. Any session storage happens only in the actual app. Do not attach private archives or account credentials to public issues.

[Changelog](CHANGELOG.md) · [Validation](docs/VALIDATION.md) · [Roadmap](ROADMAP.md)
