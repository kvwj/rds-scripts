# RDS scripts

## Playback log importer

`insert_playback_events.py` reads the latest 29 days of Zara logs from
`/mnt/logs/ZaraLogs`, then monitors new entries. Database configuration uses
`DATABASE_NAME`, `DATABASE_USER`, `DATABASE_PASSWORD`, `DATABASE_HOST`, and
`DATABASE_PORT`.

Logs default to UTF-8. If Zara writes Windows-1252 logs, set
`ZARA_LOG_ENCODING=cp1252` in the importer environment and restart it. Use the
actual encoding configured on the Zara machine: a decoding error alone does
not identify the encoding, and the wrong encoding can change song names.
Invalid bytes are not silently discarded. Decoding stops at the affected line
and logs the file, byte offset, and configured encoding; the importer skips
that line and continues. Restarting replays the last 29 days with the configured
encoding; existing duplicate plays are skipped. Verify the encoding promptly
because undecodable entries are not imported.

Apply KVWJ Logs migrations through **0012** before deploying the importer.
Both applications must use the same database. Migration 0012 adds
`playback_category`; it does not alter the existing unmanaged catalog tables.

Songs are matched by title and artist, ignoring case and surrounding whitespace.
Moving a song updates its category and filename without replacing its track ID,
display names, or announcement metadata. A missing year does not erase an
existing year. Sunday recordings retain their artist-less identity.

Each log line is imported in one transaction: catalog creation or updates,
playback insertion, and category capture commit together. Duplicate events
are skipped before updating the catalog. Older events cannot revert the category
of a track with a newer recorded playback. Ambiguous track identities, filename
uniqueness conflicts, and a different song at the same date/time are reported
instead of guessing or merging records. An advisory transaction lock serializes
instances of this importer; other catalog writers must still respect database
constraints.

New events capture their actual log category in `playback_category`. Before
changing a track's category, earlier events without snapshots retain its last
known category, explicitly marked **inferred**. Their original historical
categories cannot be recovered reliably from the catalog alone.

Database errors and import conflicts roll back and send the existing
database-error notification and, when configured, email (`TO_EMAIL`,
`FROM_EMAIL`, `GMAIL_USERNAME`, `GMAIL_APP_PASSWORD`). Malformed log entries
and decoding errors are logged without email, skipped, and retried on restart.
Database errors and import conflicts stop reading at the failed line; a later
file event or restart retries it. Partial lines are held until their terminating
newline arrives.

## Finding 80s copies to remove

KVWJ Logs provides Utilities > 80s category overlap, a read-only folder scan and
CSV download. Mount the complete music library into the web application at
`/mnt/songs` or configure `MUSIC_LIBRARY_ROOT`. The report lists 80s copies and
regular-category copies; it does not delete files or update the catalog.

## Tests

Run `python -m unittest test_insert_playback_events test_rds_display_state`.
Importer tests use an isolated SQLite database with SQL dialect adaptations.
PostgreSQL locking and deployment constraints should also be checked in a
staging environment before production rollout.
