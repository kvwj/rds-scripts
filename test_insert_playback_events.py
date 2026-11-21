import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from insert_playback_events import LogHandler, insert_song_playback, parse_song


class Cursor:
    def __init__(self, database):
        self.database = database
        self.result = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=()):
        if "pg_advisory_xact_lock" in sql:
            return
        sql = sql.replace("%s", "?").replace("FOR UPDATE", "").replace("IS NOT DISTINCT FROM", "IS")
        self.result = self.database.execute(sql, params)

    def fetchone(self):
        return self.result.fetchone()

    def fetchall(self):
        return self.result.fetchall()


class Connection:
    def __init__(self, database):
        self.database = database

    def cursor(self):
        return Cursor(self.database)

    def __enter__(self):
        return self

    def __exit__(self, error_type, error, traceback):
        if error_type:
            self.database.rollback()
        else:
            self.database.commit()
        return False


class PlaybackImportTests(unittest.TestCase):
    def setUp(self):
        self.database = sqlite3.connect(":memory:")
        self.addCleanup(self.database.close)
        self.database.execute("PRAGMA foreign_keys = ON")
        self.database.create_function("BTRIM", 1, lambda value: value.strip())
        self.database.executescript("""
            CREATE TABLE artists (id INTEGER PRIMARY KEY, artist_name TEXT UNIQUE);
            CREATE TABLE categories (id INTEGER PRIMARY KEY, category TEXT UNIQUE);
            CREATE TABLE years (id INTEGER PRIMARY KEY, year INTEGER UNIQUE);
            CREATE TABLE tracks (
                id INTEGER PRIMARY KEY, track_name TEXT, artistid INTEGER REFERENCES artists,
                categoryid INTEGER REFERENCES categories, yearid INTEGER REFERENCES years,
                filename TEXT UNIQUE, track_display_name TEXT
            );
            CREATE TABLE playback (
                id INTEGER PRIMARY KEY, trackid INTEGER REFERENCES tracks,
                date TEXT, timestamp TEXT, UNIQUE(date, timestamp)
            );
            CREATE TABLE playback_category (
                playback_id INTEGER PRIMARY KEY REFERENCES playback,
                category TEXT NOT NULL, inferred BOOLEAN NOT NULL
            );
        """)
        self.connection = Connection(self.database)
        self.date = "2026-11-19"

    def play(self, category, timestamp, year=1985, filename="Title - Artist - 1985.mp3"):
        return insert_song_playback(
            self.connection, ("Title", "Artist", year, category, filename, timestamp), self.date,
        )

    def test_move_preserves_id_display_name_and_historical_category(self):
        self.play("A", "01:00:00")
        self.database.execute("UPDATE tracks SET track_display_name = 'Display title'")
        self.database.commit()
        self.play("B", "02:00:00")
        self.assertEqual(self.database.execute("""
            SELECT t.id, t.track_display_name, c.category FROM tracks t
            JOIN categories c ON c.id = t.categoryid
        """).fetchall(), [(1, "Display title", "B")])
        self.assertEqual(self.database.execute(
            "SELECT trackid FROM playback ORDER BY id",
        ).fetchall(), [(1,), (1,)])
        self.assertEqual(self.database.execute(
            "SELECT category, inferred FROM playback_category ORDER BY playback_id",
        ).fetchall(), [("A", 0), ("B", 0)])

    def test_old_and_duplicate_events_do_not_restore_old_category_or_clear_year(self):
        self.play("B", "02:00:00")
        self.play("A", "01:00:00", year=None)
        self.assertFalse(self.play("A", "01:00:00", year=None))
        self.play("80s Rock", "03:00:00", year=None, filename="Artist - Title.mp3")
        self.assertEqual(self.database.execute("""
            SELECT c.category, y.year, t.filename FROM tracks t
            JOIN categories c ON c.id = t.categoryid JOIN years y ON y.id = t.yearid
        """).fetchone(), ("80s Rock", 1985, "Artist - Title.mp3"))
        self.assertEqual(self.database.execute(
            "SELECT category FROM playback_category ORDER BY playback_id",
        ).fetchall(), [("B",), ("A",), ("80s Rock",)])

    def test_old_unsnapshotted_history_is_frozen_and_marked_inferred(self):
        self.play("A", "01:00:00")
        self.database.execute("DELETE FROM playback_category")
        self.database.commit()
        self.play("B", "02:00:00")
        self.play("C", "03:00:00")
        self.assertEqual(self.database.execute(
            "SELECT category, inferred FROM playback_category ORDER BY playback_id",
        ).fetchall(), [("A", 1), ("B", 0), ("C", 0)])

    def test_filename_conflict_rolls_back_catalog_and_history_changes(self):
        self.play("A", "01:00:00")
        self.database.execute("""
            INSERT INTO tracks (track_name, categoryid, filename) VALUES ('Other', 1, 'Taken.mp3')
        """)
        self.database.execute("DELETE FROM playback_category")
        self.database.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.play("B", "02:00:00", filename="Taken.mp3")
        self.assertEqual(self.database.execute("SELECT categoryid FROM tracks WHERE id=1").fetchone(), (1,))
        self.assertEqual(self.database.execute("SELECT COUNT(*) FROM playback").fetchone(), (1,))
        self.assertEqual(self.database.execute("SELECT COUNT(*) FROM playback_category").fetchone(), (0,))
        self.assertEqual(self.database.execute("SELECT COUNT(*) FROM categories").fetchone(), (1,))

    def test_ambiguous_identity_and_conflicting_event_fail_without_inserting(self):
        self.play("A", "01:00:00")
        with self.assertRaisesRegex(ValueError, "Different song already recorded"):
            insert_song_playback(
                self.connection, ("Other", "Artist", None, "B", "Other.mp3", "01:00:00"), self.date,
            )
        self.database.execute("INSERT INTO tracks (track_name,artistid,categoryid) VALUES (' TITLE ',1,1)")
        self.database.commit()
        with self.assertRaisesRegex(ValueError, "Ambiguous track identity"):
            self.play("B", "02:00:00")
        self.assertEqual(self.database.execute("SELECT COUNT(*) FROM playback").fetchone(), (1,))
        self.assertEqual(self.database.execute("SELECT COUNT(*) FROM categories").fetchone(), (1,))

    def test_case_and_whitespace_identity_reuses_track(self):
        self.play("A", "01:00:00")
        insert_song_playback(
            self.connection, (" title ", " ARTIST ", None, "B", "Title.mp3", "02:00:00"), self.date,
        )
        self.assertEqual(self.database.execute("SELECT COUNT(*) FROM tracks").fetchone(), (1,))

    def test_snapshot_failure_rolls_back_entire_event(self):
        self.database.executescript("""
            CREATE TRIGGER fail_snapshot BEFORE INSERT ON playback_category
            BEGIN SELECT RAISE(ABORT, 'snapshot unavailable'); END;
        """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "snapshot unavailable"):
            self.play("A", "01:00:00")
        for table in ("tracks", "artists", "categories", "years", "playback"):
            self.assertEqual(self.database.execute(f"SELECT COUNT(*) FROM {table}").fetchone(), (0,))

    def test_sunday_track_can_move_without_artist_or_year(self):
        for category, timestamp in (("Sunday", "01:00:00"), ("Sunday archive", "02:00:00")):
            insert_song_playback(
                self.connection, ("Sermon", None, None, category, "Sermon.mp3", timestamp), self.date,
            )
        self.assertEqual(self.database.execute(
            "SELECT artistid, yearid FROM tracks",
        ).fetchall(), [(None, None)])
        self.assertEqual(self.database.execute(
            "SELECT category FROM playback_category ORDER BY playback_id",
        ).fetchall(), [("Sunday",), ("Sunday archive",)])


class PlaybackParsingTests(unittest.TestCase):
    def test_regular_eighties_and_sunday_paths(self):
        root = r"C:\ye olde new gold\playback\clockwheel rotation"
        for relative, expected in (
            (r"\A\Title - Artist - 1985.mp3", ("Title", "Artist", 1985, "A")),
            (r"\That 80s Show\80s Rock\Artist - Title.mp3", ("Title", "Artist", None, "80s Rock")),
            (r"\Sunday\Title.mp3", ("Title", None, None, "Sunday")),
        ):
            with self.subTest(relative=relative):
                self.assertEqual(parse_song("01:02:03\t" + root + relative)[:4], expected)
        self.assertIsNone(parse_song("01:02:03\tNot a music entry"))
        self.assertIsNone(parse_song("01:02:03\t" + root + r"\Special Liners\Station.mp3"))

    def test_non_padded_timestamps_cannot_break_chronological_ordering(self):
        root = r"C:\ye olde new gold\playback\clockwheel rotation"
        with self.assertRaises(ValueError):
            parse_song("1:02:03\t" + root + r"\A\Title - Artist - 1985.mp3")

    def test_retry_failed_line_and_wait_for_complete_line(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / f"{datetime.now():%Y-%m-%d}.log"
            path.write_text("first\npartial")
            handler = LogHandler(None)
            with patch.object(handler, "parse_and_insert", side_effect=ValueError("offline")), patch(
                "insert_playback_events.report_database_error",
            ) as notify:
                handler.process_log_file(str(path))
            notify.assert_called_once()
            self.assertEqual(handler.file_state[str(path)]["offset"], 0)
            with patch.object(handler, "parse_and_insert") as parse:
                handler.process_log_file(str(path))
            self.assertEqual(parse.call_count, 1)
            with path.open("a") as log:
                log.write("\n")
            with patch.object(handler, "parse_and_insert") as parse:
                handler.process_log_file(str(path))
            self.assertEqual(parse.call_args.args[0], "partial\n")

    def test_windows_encoded_logs_preserve_non_ascii_and_partial_lines(self):
        root = r"C:\ye olde new gold\playback\clockwheel rotation"
        line = "01:02:03\t" + root + "\\A\\Canción - Artista - 1985.mp3"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / f"{datetime.now():%Y-%m-%d}.log"
            path.write_bytes(("header º\r\n" + line).encode("cp1252"))
            handler = LogHandler(None, encoding="cp1252")
            with patch.object(handler, "parse_and_insert", wraps=handler.parse_and_insert) as parse:
                handler.process_log_file(str(path))
            self.assertEqual(parse.call_count, 1)
            with path.open("ab") as log:
                log.write(b"\r\n")
            with patch("insert_playback_events.insert_song_playback") as insert:
                handler.process_log_file(str(path))
            self.assertEqual(insert.call_args.args[1], (
                "Canción", "Artista", 1985, "A", "Canción - Artista - 1985.mp3", "01:02:03",
            ))

    def test_decoding_failure_is_logged_and_later_lines_continue(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / f"{datetime.now():%Y-%m-%d}.log"
            path.write_bytes(b"first\nsecond \xba\nthird\n")
            handler = LogHandler(None)
            with patch.object(handler, "parse_and_insert") as parse, self.assertLogs(level="ERROR") as logs:
                handler.process_log_file(str(path))
            self.assertEqual([call.args[0] for call in parse.call_args_list], ["first\n", "third\n"])
            self.assertEqual(handler.file_state[str(path)]["offset"], path.stat().st_size)
            self.assertIn("ZARA_LOG_ENCODING", logs.output[0])
            handler = LogHandler(None, encoding="cp1252")
            with patch.object(handler, "parse_and_insert") as parse:
                handler.process_log_file(str(path))
            self.assertEqual([call.args[0] for call in parse.call_args_list], ["first\n", "second º\n", "third\n"])

    def test_invalid_configured_encoding_fails_explicitly(self):
        with self.assertRaises(LookupError):
            LogHandler(None, encoding="not-a-real-encoding")

    def test_invalid_song_name_is_logged_without_email_and_later_songs_are_imported(self):
        root = r"C:\ye olde new gold\playback\clockwheel rotation"
        line = "01:02:03\t" + root + "\\A\\bad filename.mp3\n"
        valid_line = "01:03:03\t" + root + "\\A\\Title - Artist - 1985.mp3\n"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / f"{datetime.now():%Y-%m-%d}.log"
            path.write_text(line + valid_line)
            handler = LogHandler(None)
            with patch("insert_playback_events.report_database_error") as notify, patch(
                "insert_playback_events.insert_song_playback",
            ) as insert, self.assertLogs(level="ERROR") as logs:
                handler.process_log_file(str(path))
            notify.assert_not_called()
            self.assertIn("Invalid song filename: bad filename.mp3", logs.output[0])
            self.assertIn("byte offset 0", logs.output[0])
            self.assertEqual(insert.call_args.args[1], (
                "Title", "Artist", 1985, "A", "Title - Artist - 1985.mp3", "01:03:03",
            ))
            self.assertEqual(handler.file_state[str(path)]["offset"], path.stat().st_size)
            with patch("insert_playback_events.insert_song_playback") as insert:
                handler.process_log_file(str(path))
            insert.assert_not_called()


if __name__ == "__main__":
    unittest.main()
