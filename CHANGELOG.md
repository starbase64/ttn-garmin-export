# 0.5.4-preview · optional inferred skips

- Experimental skip option is off by default. Requires explicit null fields; no sport-name rules. Summaries are processed before dependent requests are scheduled.
- Skips remain unverified/missing, with request and rule evidence in an additive optional SQLite table. Existing responses take precedence; offline exports flag missing data and inferred runs are incomplete.
- Offline-data button retries marked requests in the chosen date range without inference. Actual responses remove the marker; failures remain retryable. Rate limits remain enforced.
- Shared account storage, export folders and saved first-activity dates from 0.5.3 are retained. No added dependencies. Savings are not validated against live Garmin accounts.

# 0.5.3-preview · 2026-10-24

- Default to one exports/current folder per account; optional history uses output settings instead of transfer tuning for its identity. Stage output before publication, clean stale generated files, preserve unrelated files and roll back ordinary publication failures.
- Online exports reuse the selected SQLite archive and store new responses there, sharing the same source as archive sync. Legacy cache responses are imported without network requests, including older nested account caches.
- Normalize account-folder output selections without nesting account directories. Existing older export folders are not removed. No database schema or dependency changes.

# 0.5.2-preview · 2026-10-24

- Remember the detected first activity date per account, including after session restoration. Missing dates are looked up once when the suggestion option is enabled; cached dates avoid repeated account queries.
- Retain user-selected start dates separately for Export and Data. Add bilingual reset buttons that restore the saved account first activity date without network requests. End dates retain their existing behavior.
- Keep accounts and local archive selections isolated; incomplete local history never replaces the detected account date.

## Review follow-ups

- Rate limits are signalled to the UI immediately, even if later storage/report writes fail. A storage error while saving already received responses no longer hides the original stop reason. A rate limit (429) is kept, so the local request pause still applies; the storage error is listed as a warning.
- A rejected or expired saved session no longer shows an error dialog on every start: the email stays filled in, the automatic sign-in is turned off and a hint appears in the log. Temporary network/server errors keep the saved account for the next start.
- The UI explicitly explains that unchecking Remember disables automatic sign-in while retaining tokens. Remove local session remains the explicit deletion action.

# 0.5.1-preview · 2026-10-23

- Restore the remembered account on startup using saved session tokens, without saving passwords. Verify token writes explicitly and report save failures. Respect request pauses. Allow removing expired local sessions without signing in. After updating, sign in once with Remember enabled.

- Estimate parallel downloads from measured batch wall-clock throughput, keeping serial work and cached data separate.
- Data browser: full-height map pane, compact tools/selection menus, guide as final tab, and activity inventory diagnostics distinguishing missing requests from empty responses.

- Parallel requests (Options → "Parallele Abfragen", 1–8, default 3) for independent single-day requests and activity details/files. One shared schedule: the request interval is the minimum gap between any two request starts across all workers. Responses are stored in the original order; stored response payloads and exported data files match sequential runs in synthetic tests (timestamps and reports differ).
- Completed parallel responses are saved even after an earlier request failed or Stop was pressed. Actual request starts use a shared schedule that handles delayed workers.
- A rate limit, authentication or server/network failure in any worker stops new requests at once; already received responses are saved and the run stays resumable.
- Archive sync no longer forces a 2 s interval; it uses the configured interval (minimum 0.25 s, default 0.5 s) and retries transient network/5xx errors (never 4xx/429).
- New opt-in dataset "Tagesrohdaten als FIT-Datei": one /download-service/files/wellness/{date} ZIP per day (monitoring FIT files), stored and exported unchanged. Not part of "all supported data"; no table conversion yet.
- Fix: days skipped after Garmin rejected a query type are now recorded as missing (coverage, "retry failed" queue) instead of silently absent.
- Fix: one activity outside its range batch no longer switches all later activity batches to daily requests.
- Security: XML import refused DTDs only by byte search, bypassable with UTF-16; now checked by the parser for every encoding.
- Fix: GPX/TCX files mixing UTC ("Z") and naive timestamps no longer fail to import.

# 0.5.0-preview · 2026-10-23

- Renamed the application to TTN Garmin Export to distinguish it from existing garmin-data-export projects. Retained internal application profile name for existing sessions/logs.
- Small Windows x64 starter: private Python runtime, pinned/checksummed PyPI wheels, missing/corrupt-package repair, local download cache, progress and setup.log. No admin or preinstalled Python required.
- Reproducible starter builder, locked full transitive Windows dependency set and Windows CI for first setup, offline restart and cached missing-file repair.
- Existing 0.4 export behavior retained, including full-detail archive sync and optional daily statistics. No claim of faster complete archive downloads or live Garmin verification.

# 0.4.0-preview · 2026-10-23

- Full daily summary batching, configurable 1–28 days, preserving original daily cache/archive identities and returned fields.
- Explicit full-timeline/default versus daily-statistics health mode for ten categories, with distinct -daily resources, matching bilingual labels and preview/fields/offline/preset/repair integration.
- Strict dated-range validation; missing days and unsupported ranges use matching daily reads, never substituting statistics for detailed timelines. Auth/rate/connection errors stop.
- Actual system Downloads/Garmin_Export as default output, including redirected Windows Downloads. Existing data is not moved automatically.
- Full archive button retains full-detail behavior; selected-category sync honors mode. No dependency changes; existing Windows runtime reused.
- Monitoring FIT ingestion and HRV/detail endpoint batching are not implemented in this release. No live Garmin or Windows execution test.

# 0.3.1-preview · 2026-10-23

- Fix clipped Output text and checkbox indicators: normal control font weight, font-aware minimum heights and spacing.
- Export page scrolls at small window sizes or high display scaling instead of squeezing the Output form.
- German/English offscreen layout checks at 150% scaling, 1180×870 and 900×650 logical window sizes. Windows execution remains unverified.
- Existing download optimization and ETA retained; no dependency or data-format changes.

# 0.3.0-preview · 2026-10-23

- Bounded Body Battery and paginated activity-range retrieval; only contiguous missing days are batched. Existing request fingerprints and daily archives preserved.
- Conservative response validation, daily fallback for missing dates or unsupported ranges, no fallback on rate/auth/connection blocks.
- Measured transfer ETA with cache/file weighting, provisional activity workload, retry-wait display, request count and separate writing phase.
- Selected-category archive sync; optimization toggle and activity range size saved in presets.
- Windows overlay update uses the existing bundled runtime; no new Python installation or dependency update required.
- Detailed heart rate, sleep and stress endpoints remain daily; pacing is unchanged. Synthetic regression tests and smoke test, not live Garmin or Windows validation.

# 0.2.0-preview · 2026-10-23

New local data browser, Ctrl/Shift multi-selection maps and heatmap, optional OSM/Google views, PNG/KML sharing with endpoint masking, FIT/GPX/TCX/activity ZIP import and cache migration, daily health tables, month/year totals, comparison and approximate route matching, validated SQLite backups and targeted repair queues. Persistent session logs and log buttons, visible checkbox indicators, and optional oldest activity date suggestion. Existing SQLite schema remains compatible; imported partial days are explicitly marked. Separate Windows portable CMD bundle assembled with the official embedded runtime; no custom app EXE or live Windows/Garmin/Google account validation in this release.

# Changes

## 2026-10-23 · Sportart-Dropdown / Sport dropdown

- Durchsuchbare Auswahl mit „Alle Sportarten“, deutschen/englischen Bezeichnungen und stabilen Garmin-Schlüsseln.
- Vollständige Sportartenliste nach der Anmeldung und über „Liste aktualisieren“.
- Eigene Schlüssel und vorhandene Vorlagen bleiben unterstützt.
- Optionen sind scrollbar, damit alle Einstellungen zugänglich bleiben.
- Searchable bilingual sport list, live Garmin taxonomy retrieval and manual refresh; custom keys and existing presets remain supported.

## 2026-10-23 · Layout „Abruf und Tabellen“

- Beschriftungen über den Eingabefeldern statt in einer schmalen Seitenspalte.
- Kürzere Texte und separate Erklärung zur Cache-Einstellung.
- Deutsche und englische Darstellung bei 150 % Skalierung geprüft.

## 2026-10-23 · Lesbare Aktivitätsliste und Abrufkorrektur

- Eine Zeile pro Aktivität mit Datum, Tourname, Ort, Distanz, Zeit, Geschwindigkeit und weiteren üblichen Summenwerten.
- Schnellwahl „Nur Aktivitätsliste (CSV)“; Original-CSV als „CSV (Runden)“ kenntlich gemacht.
- Tageslisten ohne Sportarten-Serverfilter abrufen, explizite Paginierung und lokale Auswahl nach Typ/Untertyp.
- Bei Abruffehlern den verfügbaren HTTP-Status im Protokoll/Bericht anzeigen, ohne private Serverantworten zu speichern.
- Mit 30 simulierten E-Mountainbike-Aktivitäten geprüft; echter Kontotest noch erforderlich.

## 2026-10-23 · Begrenzte Exportvorschau

- Neuer Button „Vorschau anzeigen“ vor dem Export, mit Tabellen und JSON-Ausschnitten.
- Dieselben Formate, Spalten, Einheiten, Sportarten- und Feldfilter wie bei der Dateiausgabe.
- Einstellbare Zeilen-/Abfragelimits, Abbruch und Hinweise zu unvollständigen Stichproben.
- Keine Export-/Cache-Dateien und kein Download der eingestellten Originaldateien.

## 2026-10-23 · Verbindungsfehler und Ratenlimit

- Keine doppelten API-Wiederholungen in App und Bibliothek.
- Verbindungsfehler stoppen die Serie; Fehlermeldungen zeigen HTTP-Status und sichere Diagnosekategorien.
- Nach Ratenlimit keine neuen Aufrufe bis zum Ende einer lokalen Pause; Retry-After wird berücksichtigt, sonst 15 Minuten. Kein automatischer Neustart.
- Ratenlimit beim Laden der Sportarten nach der Anmeldung wird nicht mehr verschluckt.

## 2026-10-23 · Optionale SQLite-Offlinedatenbank

- Neue Seite „Offline-Daten“ mit Sammelsicherung aller 27 unterstützten Standarddatenarten im gewählten Zeitraum.
- Optionale Aktivitätsdetails und ORIGINAL/GPX/TCX/KML/Runden-CSV; erfolgreiche Antworten einzeln und dauerhaft gespeichert.
- Fortsetzen, Lücken ergänzen, jüngere Daten aktualisieren und vorhandenen Dateicache übernehmen.
- Bestandsübersicht mit gespeicherten/fehlenden Tagen, leeren Antworten und offenen Abruffehlern.
- Offline-Vorschau und Export ohne Anmeldung oder Online-Fallback, auch während einer lokalen Abrufpause.
- Datenbanken sind pro Konto geschützt; private SQLite-Dateien in .gitignore ausgeschlossen.
