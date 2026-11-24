import codecs
import json
import logging
import os
import smtplib
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from pathlib import PureWindowsPath

import psycopg2
from watchdog.events import FileSystemEventHandler
from watchdog.observers.polling import PollingObserver


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
directory = "/mnt/logs/ZaraLogs"
LOG_ROOTS = (
    r"C:\Users\KVWJ\Desktop\pls",
    r"C:\Users\KVWJ\Desktop\ye olde new gold\playback\clockwheel rotation",
    r"C:\ye olde new gold\playback\clockwheel rotation",
)


class SongParseError(ValueError):
    pass


def get_connection():
    return psycopg2.connect(
        dbname=os.getenv("DATABASE_NAME"),
        user=os.getenv("DATABASE_USER"),
        password=os.getenv("DATABASE_PASSWORD"),
        host=os.getenv("DATABASE_HOST"),
        port=os.getenv("DATABASE_PORT", 5432),
    )


def report_database_error(error):
    logging.exception("Playback import failed; transaction rolled back.")
    try:
        with closing(get_connection()) as connection, connection:
            with connection.cursor() as cursor:
                cursor.execute("NOTIFY database_errors, %s", (json.dumps({
                    "status": "ROLLBACK", "error_message": str(error),
                }),))
    except psycopg2.Error:
        logging.exception("Failed to send database error notification.")

    recipients = [value.strip() for value in os.getenv("TO_EMAIL", "").split(",") if value.strip()]
    if not recipients:
        logging.warning("TO_EMAIL is not configured; database error email not sent.")
        return
    message = MIMEText(f"Playback import failed: {error}", "plain")
    message["Subject"] = "KVWJ Playback Insert Error"
    message["From"] = os.getenv("FROM_EMAIL", "")
    message["To"] = ", ".join(recipients)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            server.login(os.getenv("GMAIL_USERNAME", ""), os.getenv("GMAIL_APP_PASSWORD", ""))
            server.sendmail(message["From"], recipients, message.as_string())
    except (smtplib.SMTPException, OSError):
        logging.exception("Unable to send playback error email.")


def parse_song(line):
    for root in LOG_ROOTS:
        position = line.casefold().find(root.casefold())
        if position >= 0:
            relative = line[position + len(root):].strip()
            break
    else:
        return None
    parts = PureWindowsPath(relative).parts
    if "special liners" in relative.casefold() or len(parts) < 3:
        return None
    filename = parts[-1]
    if PureWindowsPath(filename).suffix.casefold() not in (".mp3", ".wav"):
        return None
    category = parts[-2].strip()
    stem = PureWindowsPath(filename).stem
    if "sunday" in category.casefold():
        title, artist, year, category = stem.split(" - ")[0].strip(), None, None, "Sunday"
    elif "80s" in category.casefold():
        details = stem.split(" - ", 1)
        if len(details) != 2:
            raise ValueError(f"Invalid 80s song filename: {filename}")
        artist, title = (value.strip() for value in details)
        year = None
    else:
        details = stem.rsplit(" - ", 2)
        if len(details) != 3:
            raise ValueError(f"Invalid song filename: {filename}")
        title, artist, year_text = (value.strip() for value in details)
        year = int(year_text[:4]) if year_text[:4].isascii() and year_text[:4].isdigit() else None
    if not title or artist == "" or not category:
        raise ValueError(f"Missing title, artist, or category: {filename}")
    timestamp = line.split("\t", 1)[0].strip()
    if datetime.strptime(timestamp, "%H:%M:%S").strftime("%H:%M:%S") != timestamp:
        raise ValueError(f"Invalid playback timestamp: {timestamp}")
    return title, artist, year, category, filename, timestamp


def lookup_or_insert(cursor, table, column, value, *, insensitive=False):
    condition = f"UPPER(BTRIM({column})) = UPPER(BTRIM(%s))" if insensitive else f"{column} = %s"
    cursor.execute(
        f"SELECT id FROM {table} WHERE {condition} ORDER BY COALESCE({column} = %s, FALSE) DESC, id",
        (value, value),
    )
    rows = cursor.fetchall()
    if len(rows) > 1:
        logging.warning("Multiple %s entries match %r; using id %s.", table, value, rows[0][0])
    if rows:
        return rows[0][0]
    cursor.execute(f"INSERT INTO {table} ({column}) VALUES (%s) RETURNING id", (value,))
    return cursor.fetchone()[0]


def insert_song_playback(connection, song, date):
    title, artist, year, category, filename, timestamp = song
    if datetime.strptime(date, "%Y-%m-%d").strftime("%Y-%m-%d") != date:
        raise ValueError(f"Invalid playback date: {date}")
    if datetime.strptime(timestamp, "%H:%M:%S").strftime("%H:%M:%S") != timestamp:
        raise ValueError(f"Invalid playback timestamp: {timestamp}")
    with connection:
        with connection.cursor() as cursor:
            # Serialize this importer's catalog lookup/create and event deduplication.
            cursor.execute("SELECT pg_advisory_xact_lock(95001)")
            cursor.execute(
                """SELECT p.id, t.track_name, a.artist_name FROM playback p
                   JOIN tracks t ON t.id = p.trackid LEFT JOIN artists a ON a.id = t.artistid
                   WHERE p.date = %s AND p.timestamp = %s""",
                (date, timestamp),
            )
            existing = cursor.fetchone()
            if existing:
                if (existing[1].strip().casefold(), (existing[2] or "").strip().casefold()) != (
                    title.strip().casefold(), (artist or "").strip().casefold(),
                ):
                    raise ValueError(f"Different song already recorded at {date} {timestamp}.")
                logging.info("Playback already exists; skipped: %s %s", date, timestamp)
                return False
            artist_id = lookup_or_insert(cursor, "artists", "artist_name", artist, insensitive=True) if artist else None
            category_id = lookup_or_insert(cursor, "categories", "category", category)
            year_id = lookup_or_insert(cursor, "years", "year", year) if year is not None else None
            cursor.execute("""
                SELECT id, categoryid FROM tracks
                WHERE UPPER(BTRIM(track_name)) = UPPER(BTRIM(%s))
                  AND artistid IS NOT DISTINCT FROM %s
                ORDER BY COALESCE(filename = %s, FALSE) DESC,
                    COALESCE(categoryid = %s, FALSE) DESC,
                    COALESCE(track_name = %s, FALSE) DESC, id
                FOR UPDATE
            """, (title, artist_id, filename, category_id, title))
            rows = cursor.fetchall()
            if len(rows) > 1:
                # Duplicate catalog rows exist; pick the closest match so imports keep flowing.
                logging.warning(
                    "Multiple tracks match %s - %s; using track %s.", artist, title, rows[0][0],
                )
            if rows:
                track_id, old_category_id = rows[0]
                cursor.execute("""
                    SELECT 1 FROM playback WHERE trackid = %s
                      AND (date > %s OR (date = %s AND timestamp > %s)) LIMIT 1
                """, (track_id, date, date, timestamp))
                is_latest = cursor.fetchone() is None
                if is_latest:
                    if old_category_id != category_id:
                        # Old unsnapshotted events can only retain an inferred category.
                        cursor.execute("""
                            INSERT INTO playback_category (playback_id, category, inferred)
                            SELECT p.id, COALESCE(c.category, ''), TRUE
                            FROM playback p JOIN tracks t ON t.id = p.trackid
                            LEFT JOIN categories c ON c.id = t.categoryid
                            WHERE p.trackid = %s
                            ON CONFLICT (playback_id) DO NOTHING
                        """, (track_id,))
                    cursor.execute("""
                        UPDATE tracks SET categoryid = %s, filename = %s,
                            yearid = COALESCE(%s, yearid) WHERE id = %s
                    """, (category_id, filename, year_id, track_id))
            else:
                cursor.execute("""
                    INSERT INTO tracks (track_name, artistid, categoryid, yearid, filename)
                    VALUES (%s, %s, %s, %s, %s) RETURNING id
                """, (title, artist_id, category_id, year_id, filename))
                track_id = cursor.fetchone()[0]
            cursor.execute("""
                INSERT INTO playback (trackid, date, timestamp)
                VALUES (%s, %s, %s) ON CONFLICT DO NOTHING RETURNING id
            """, (track_id, date, timestamp))
            row = cursor.fetchone()
            if row is None:
                raise ValueError(f"Playback conflict at {date} {timestamp}; catalog changes rolled back.")
            cursor.execute("""
                INSERT INTO playback_category (playback_id, category, inferred)
                VALUES (%s, %s, FALSE)
            """, (row[0], category))
            logging.info("Inserted playback.id=%s for track %s in %s", row[0], track_id, category)
    return True


class LogHandler(FileSystemEventHandler):
    def __init__(self, connection, encoding=None):
        self.connection = connection
        self.encoding = codecs.lookup(encoding if encoding is not None else os.getenv("ZARA_LOG_ENCODING", "utf-8")).name
        self.db_lock = threading.Lock()
        self.file_state = {}

    def parse_and_insert(self, line, date, line_num=None):
        try:
            song = parse_song(line)
        except ValueError as error:
            raise SongParseError(str(error)) from error
        if song is not None:
            return insert_song_playback(self.connection, song, date)
        return False

    def process_log_file(self, filepath, read_existing=False):
        date = os.path.basename(filepath).split(".log")[0]
        try:
            filedate = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return
        if filedate <= datetime.now() - timedelta(days=29):
            return
        with self.db_lock:
            try:
                stat = os.stat(filepath)
                state = self.file_state.get(filepath)
                offset = 0 if (
                    read_existing or state is None or state["inode"] != stat.st_ino
                    or stat.st_size < state["offset"]
                ) else state["offset"]
                with open(filepath, "rb") as log:
                    log.seek(offset)
                    while True:
                        line_offset = log.tell()
                        raw_line = log.readline()
                        if not raw_line:
                            break
                        if not raw_line.endswith(b"\n"):
                            log.seek(line_offset)
                            break
                        try:
                            line = raw_line.decode(self.encoding)
                        except UnicodeDecodeError:
                            logging.exception(
                                "Could not decode playback log %s at byte offset %s using %s. "
                                "Set ZARA_LOG_ENCODING to the log's actual encoding; skipping this line.",
                                filepath, line_offset, self.encoding,
                            )
                            continue
                        try:
                            self.parse_and_insert(line, date)
                        except SongParseError as error:
                            logging.error(
                                "Invalid playback log entry in %s at byte offset %s: %s. "
                                "Skipping this line.",
                                filepath, line_offset, error,
                            )
                            continue
                        except (psycopg2.OperationalError, psycopg2.InterfaceError) as error:
                            report_database_error(error)
                            log.seek(line_offset)
                            break
                        except (psycopg2.Error, ValueError) as error:
                            # Data problems will fail the same way on retry, so skip the line.
                            report_database_error(error)
                            logging.error(
                                "Skipping playback log entry in %s at byte offset %s.",
                                filepath, line_offset,
                            )
                            continue
                    self.file_state[filepath] = {"inode": stat.st_ino, "offset": log.tell()}
            except FileNotFoundError:
                logging.warning("Log disappeared while reading: %s", filepath)
            except OSError:
                logging.exception("Could not read playback log: %s", filepath)

    def on_created(self, event):
        if not event.is_directory and event.src_path != os.path.join(directory, "CurrentSong.txt"):
            time.sleep(1)
            self.process_log_file(event.src_path)

    def on_modified(self, event):
        if not event.is_directory and event.src_path != os.path.join(directory, "CurrentSong.txt"):
            time.sleep(2)
            self.process_log_file(event.src_path)


def main():
    handler = LogHandler(None)
    connection = get_connection()
    connection.autocommit = False
    handler.connection = connection
    observer = PollingObserver(timeout=10)
    try:
        for entry in sorted(os.scandir(directory), key=lambda entry: entry.name):
            if entry.is_file() and entry.name != "CurrentSong.txt":
                handler.process_log_file(entry.path, read_existing=True)
        observer.schedule(handler, directory, recursive=False)
        observer.start()
        logging.info("Monitoring %s for new log files...", directory)
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Stopping playback importer.")
    finally:
        observer.stop()
        if observer.is_alive():
            observer.join()
        connection.close()


if __name__ == "__main__":
    main()
