# 0.5.3-preview — account export consolidation

2026-10-24: 153 synthetic tests pass on Linux/Python 3.12. New coverage verifies unchanged account output path across formats, zero network calls when exporting synced data, new-export data reused by sync, source-day retention after narrower exports, legacy/nested-cache import, original-file resumption, UI use of the shared archive, optional history independent of request tuning, wrong-account rejection, stale-output cleanup, manual-file preservation, failed-write preservation, publication rollback and path traversal rejection. Existing date/reset/session/rate-limit regressions remain. Native Windows result is recorded in the PR Actions run. No live Garmin calls made.

# 0.5.2-preview — remembered first-activity dates

2026-10-24: 140 synthetic tests pass on Linux/Python 3.12 and app smoke test succeeds. New regression coverage checks account date persistence across restart, custom Export/Data starts and reset persistence, account isolation, local archive independence, invalid-date rejection and switching between online/offline export accounts. UI reset controls inspected with synthetic data; no live Garmin access. Native Windows result is recorded on the PR Actions run.

# Review follow-ups — 2026-10-24 (unreleased)

- 135 synthetic tests pass on Linux/Python 3.12, plus the app smoke test.
- Original fatal reason survives failures while saving in-flight responses. A 429 emits its sanitized rate-limit signal before any final report writes; tests cover both export and archive reporting failures and both Garmin rate-limit exception shapes.
- Rejected automatic sessions produce a log hint and disable further automatic restores while preserving account fields. Temporary connection/rate-limit failures retain the saved selection; 429 still sets the local pause.
- UI explicitly distinguishes disabling automatic sign-in from deleting stored tokens. No change to the token-deletion behavior of the checkbox.
- These changes are under review and are not part of the existing release. Windows results are available on the pull-request Actions run.

# 0.5.1-preview — 2026-10-23

- 129 synthetic tests pass locally on Linux/Python 3.12; packaged app smoke test succeeds.
- Added remembered-account startup without saved passwords, explicit persistence failures, disabling auto sign-in, deletion of expired sessions without login and honoring request pauses.
- Parallel ETA uses actual batch wall-clock cost, without summing overlapping worker waits or treating local database writes as network time. Serial/cache work and minimum request spacing remain distinct.
- Activity inventory distinguishes missing activity responses from saved empty replies. Data-page map uses a full-height right pane, compact left-hand tools/filters, Ctrl/Shift selection and bilingual UI; the guide is the final main tab. Synthetic layout inspected at large and smaller window sizes.
- Windows test/setup/repair results for this commit are recorded in Actions. The earlier parallel-only commit passed Windows CI with 118 tests; it does not validate the newer session/layout changes.
- User reports that the patched download now takes only a few minutes on their account. This is an individual observation without a controlled before/after dataset comparison, not a universal speed guarantee.

# Unreleased parallel-download review — 2026-10-23

- Uploaded patch applies to main commit a1f7140. Its original 116 synthetic tests pass locally (Linux, Python 3.12).
- Added regression coverage for completed responses arriving after an earlier 429, and for Stop during concurrent requests. Both ordinary exports and SQLite sync preserve those successes without new network requests. 118 tests pass after these corrections.
- Shared scheduling now uses actual request-start times rather than reserved future slots, preventing late workers from catching up in a burst.
- XML encoding guard, mixed timestamp import, per-batch activity fallback, skipped-day repair tracking and raw wellness ZIP resumption/export have synthetic coverage.
- Wellness endpoint checked against the pinned Garmin library: download_health_snapshot targets /download-service/files/wellness/{date}. No live Garmin calls made. Wellness ZIPs are not converted into tables and do not automatically replace selected JSON datasets.
- No fourfold speedup or 4–5 hour full-account duration verified. Windows validation is available through the PR workflow. This development change is not part of the existing v0.5.0-preview release.

# Validation — 0.5.0-preview

2026-10-23 · prepared on Linux / Python 3.12.

- 106 tests passed, including existing export/offline/map/UI tests and new starter packaging, missing/corrupt-file repair, no redundant download, wheel traversal rejection and SHA-256 mismatch tests.
- App --smoke-test passed with the renamed application; legacy internal profile remains for session compatibility.
- Official Python 3.13.16 Windows x64 embeddable archive downloaded and SHA-256 matched against Python.org.
- All 20 resolved Windows wheels checked against PyPI's published SHA-256, installed with the bootstrap's actual wheel installer and reverified from disk. Required Qt, platform plugin, WebEngine process and CFFI files present. No preinstalled system Python needed by the distributed starter.
- First-time setup, offline restart and cached missing-package repair have a native Windows CI workflow. See the Actions run for this commit for actual execution status; local Linux checks do not prove Windows execution.
- No Garmin account traffic during these tests, no universal download acceleration claimed. Existing full-detail archive behavior remains.

Earlier validation history follows:

# Validation — 0.4.0-preview

2026-10-23 · Linux, Python 3.12, PySide6 6.12.0.

- 99 tests passed; app --smoke-test exit 0. Existing download, rate-stop, preview, archive, import, repair, map, log, UI and export tests retained.
- Synthetic full daily-summary fixture: 56 days, two 28-day API calls instead of 56 daily calls. Every field, nested value, null and day retained; disabling batching after that causes zero requests due to compatible daily fingerprints. Old daily cache imports and offline export verified.
- All ten daily-statistics adapters: 56 days in two library calls each with one dated record per day. Generic CSV/JSON output, separate -daily resources, cache resumption, archive coexistence, strict offline export and missing-full-detail behavior checked. This is an API-call-count result on synthetic fixtures, not a measured live speedup.
- Missing range dates trigger individual checks. Empty series require single-day checks before persisting empties. Foreign dates reject batch before storage. HTTP 400 range fallback stays on the statistics endpoint; 401/403/429 stop without extra requests.
- Daily preview has a strict request budget and does not fetch the complete interval or create cache files. Missing-data repair selects matching statistics resources. Health table reads supported daily statistics fields. Old presets default to full detail; new settings roundtrip. All-data archive button explicitly retains full detail.
- Default output uses QStandardPaths.DownloadLocation/Garmin_Export. Test redirects Downloads and verifies output; actual Windows known-folder lookup not executed here.
- German/English UI checked at 150% scale, 1180×1000 and 900×650 logical window sizes. Output controls retain styled minimum heights; small Export pages scroll. Synthetic screenshots use an example path and no Garmin account data.
- No dependency changes. Windows update overlays the existing portable 0.2/0.3 runtime. Source and ZIP contents statically checked. No Windows execution or live Garmin range/data-schema verification. No universal speedup claim; detailed health endpoints still run daily. Monitoring FIT ingestion and lossless HRV range adaptation remain deferred.

Earlier validation history follows:

# Validation — 0.3.0-preview

2026-10-23 · Linux, Python 3.12, PySide6 6.12.0.

- `python -m pytest -q`: **74 passed**, exit 0.
- `QT_QPA_PLATFORM=offscreen python app.py --smoke-test`: exit 0.
- Body Battery fixture, 14 days: 2 app API calls instead of 14 with optimization disabled; all 14 original daily records preserved. Existing old daily cache and SQLite archive reuse verified, with zero requests on a warm historical run.
- Activity fixture, 14 days / 420 activities: 6 paginated range calls instead of 28 daily calls (including explicit empty terminal pages); every activity retained under its original day.
- Additional tests: truncated range dates fetched individually; one daily fallback after rejected range; no fallback/extra requests after HTTP 429; activity date-filter validation; cancellation/resumption retaining completed days; offline archive exports without network.
- ETA tests cover measured request timing, cache weighting, newly discovered slow file work, provisional activity counts, retry countdown, rate-limit stop, and local export completion. Selected-category archive sync tested. Preset and prior UI/backup/import/map tests retained.
- Synthetic DE/EN ETA screenshots visually inspected; no account data in source/update packages.
- Windows update is an overlay for the previously assembled portable 0.2 runtime. No dependency changes. Source compile/smoke tests passed here; Windows execution and live Garmin range calls were not tested. Detailed daily health endpoints remain daily, default pacing is unchanged; no universal speedup factor is claimed.

Previous 0.2 validation notes follow for unchanged features:

# Validation — 0.2.0-preview

Date: 2026-10-23. Host: Linux, Python 3.12, PySide6 6.12.0.

- `python -m pytest -q`: **63 passed**, exit 0.
- `QT_QPA_PLATFORM=offscreen python app.py --smoke-test`: exit 0. Online synthetic export, SQLite sync, offline export/preview, actual GPX import, archive backup/integrity, bilingual UI, map rendering and PNG output.
- New integration tests cover valid binary FIT decoding with CRC, segmented GPX, nested Garmin activity ZIPs without disk extraction, duplicate handling, partial imported days, offline export, formula-safe CSV/XLSX, native JSON, health units, missing readings, SQLite reopen/backup, account mismatch, targeted GPS repair, forced failed-request retry, 429 stop, cache migration, route-frequency normalization and approximate route matching.
- Qt tests exercise actual Ctrl/Shift clicks, checkbox/row selection sync, filters, DE/EN with selection retained, lazy background GPS loading, missing-track handling, image export, masking, persistent logs beyond 500 lines/clear and bounded oldest-date lookup.
- Synthetic DE/EN data page and checkbox screenshots visually inspected. Data page scrolls at smaller sizes. No real account data used in screenshots or package.
- Windows portable bundle: official Python 3.13.16 x64 archive SHA-256 checked against Python.org; Windows-targeted wheels extracted with full metadata/licenses, paths, x64 executables, platform plugin, QtWebEngine and native CFFI files checked. Dependency SHA-256 manifest included. **Not executed on Windows here.**
- GitHub/PyInstaller workflow is provided but not run here. No live Garmin multi-year sync, Google Maps API-key rendering, OSM network-tile session, My Maps upload/link creation or Windows launch was tested here. No claim of complete Garmin account recovery from official account ZIPs; only supported activity files are imported.

## 0.3.1 output layout hotfix

74 existing tests passed, followed by the app smoke test. Offscreen PySide6 layout checks at QT_SCALE_FACTOR=1.5 verified that all Output input/button/combo/checkbox heights meet their styled size hints in German and English, at 1180×870 and 900×650 logical window sizes. Export contents remain accessible by scrolling. Screenshots output-v031-*.png contain no Garmin account data. No Windows execution or live Garmin request was performed.
