#!/usr/bin/env python3
import os
import time
import select
import socket
import logging
import psycopg2
import psycopg2.extensions

def get_env_value(env_variable):
    try:
        return os.environ[env_variable]
    except KeyError:
        error_msg = 'Set the {} environment variable'.format(env_variable)
        #raise ImproperlyConfigured(error_msg)

#FILE = "/mnt/zara/ZaraLogs/CurrentSong.txt"
#RDS_HOST = "192.168.0.108
#RDS_PORT = 7005
MAX_LEN = 64
#DB_HOST = '127.0.0.1'
DB_HOST = get_env_value('DB_HOST')
DB_NAME = get_env_value('DB_NAME')
DB_USER = get_env_value('DB_USER')
DB_PASSWORD = get_env_value('DB_PASSWORD')
RDS_HOST = get_env_value('RDS_HOST')
RDS_PORT = get_env_value('RDS_PORT')
ROTATE_SECONDS = int(get_env_value('ROTATE_SECONDS'))
CURRENT_SONG_FILE = get_env_value('CURRENT_SONG_FILE')

DB_CONFIG = {
    "host": DB_HOST,
    "port": 5432,
    "dbname": DB_NAME,
    "user": DB_USER,
    "password": DB_PASSWORD,
}

SPECIAL_CASES = {
    "FSN": "FSN World News",
    "The Magic of Christmas": "The Magic of Christmas",
    "Hyrum City Patriotic Program": "Hyrum City Patriotic Program",
    "Hyrum City Civics Night": "Hyrum City Civics Night",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

def safe_trim(text: str, max_len: int = MAX_LEN) -> str:
    return text[:max_len]

def send_rds_command(command: str) -> None:
    with socket.create_connection((RDS_HOST, RDS_PORT), timeout=2) as sock:
        sock.sendall((command + "\n").encode("utf-8"))


def send_rds_text(text: str) -> None:
    text = safe_trim(text)
    send_rds_command(f"TEXT={text}")
    send_rds_command("TEXT?")

def write_now_playing(text: str) -> None:
    text = safe_trim(text)
    with open('now-playing.txt', "w", encoding="utf-8", errors="replace") as np:
        np.write(text)
        np.close()

def connect_db():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = True
    return conn

def read_stable_file(path: str, checks: int = 2, delay: float = 0.2) -> str | None:
    previous = None
    stable_count = 0

    for _ in range(20):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                current = f.read().strip().replace("\x00", "")
                #logging.info('CurrentSong.txt contents: %r', current)
        except FileNotFoundError:
            return None

        if current and current == previous:
            stable_count += 1
            if stable_count >= checks:
                return current
        else:
            stable_count = 0
            previous = current

        time.sleep(delay)

    return previous if previous else None

def resolve_special_case(current_song_text: str) -> str | None:
    for needle, rds_text in SPECIAL_CASES.items():
        if needle.casefold() in current_song_text.casefold():
            return rds_text

    return None

def file_changed(path: str, last_mtime: float | None) -> tuple[bool, float | None]:
    try:
        current_mtime = os.path.getmtime(path)
    except FileNotFoundError:
        return False, last_mtime

    # Treat initial startup as a file change.
    if last_mtime is None:
        return True, current_mtime

    return current_mtime != last_mtime, current_mtime


def resolve_file_override(raw_text: str) -> str | None:
    cleaned = raw_text.replace("\x00", "").strip()

    if not cleaned:
        return None

    return resolve_special_case(cleaned)

def connect_listener():
    conn = psycopg2.connect(**DB_CONFIG)

    # LISTEN must not remain inside an uncommitted transaction.
    conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)

    with conn.cursor() as cur:
        cur.execute("LISTEN rds_playback;")

    logging.info("Listening for PostgreSQL notifications on rds_playback")
    return conn

def lookup_display_text(conn, playback_id: int) -> str | None:
    sql = """
        SELECT CONCAT_WS(
            ' - ',
            COALESCE(NULLIF(t.track_display_name, ''), NULLIF(t.track_name, '')),
            COALESCE(NULLIF(a.artist_display_name, ''), NULLIF(a.artist_name, '')),
            NULLIF(y.year::text, '')
        )
        FROM playback p
        JOIN tracks t
          ON t.id = p.trackid
        LEFT JOIN artists a
          ON a.id = t.artistid
        LEFT JOIN years y
          ON y.id = t.yearid
        WHERE p.id = %s
        LIMIT 1
    """

    with conn.cursor() as cur:
        logging.info("Looking up playback.id=%s", playback_id)
        logging.debug(
            "Playback query: %s",
            cur.mogrify(sql, (playback_id,)).decode(
                "utf-8",
                errors="replace",
            ),
        )

        cur.execute(sql, (playback_id,))
        row = cur.fetchone()

    logging.info("Playback lookup result: %r", row)
    return row[0] if row and row[0] else None

def lookup_category(conn, playback_id: int) -> str | None:
    sql = """
        SELECT c.category
        FROM playback p
        JOIN tracks t
          ON t.id = p.trackid
        LEFT JOIN categories c
          ON c.id = t.categoryid
        WHERE p.id = %s
        LIMIT 1
    """

    with conn.cursor() as cur:
        debug_sql = cur.mogrify(
            sql,
            (playback_id,),
        ).decode("utf-8", errors="replace")

        logging.info("lookup_category playback_id=%s", playback_id)
        logging.debug("lookup_category SQL=%s", debug_sql)

        cur.execute(sql, (playback_id,))
        row = cur.fetchone()

        logging.info("lookup_category row=%r", row)

        return row[0] if row and row[0] else None

def lookup_latest_playback_id(conn) -> int | None:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id
            FROM playback
            ORDER BY id DESC
            LIMIT 1
        """)
        row = cur.fetchone()

    return row[0] if row else None


def process_notification(
    conn,
    notification,
) -> tuple[str | None, str | None]:
    logging.info(
        "Received notification: channel=%s pid=%s playback_id=%r",
        notification.channel,
        notification.pid,
        notification.payload,
    )

    try:
        playback_id = int(notification.payload)
    except (TypeError, ValueError):
        logging.exception(
            "Invalid playback ID payload received: %r",
            notification.payload,
        )
        return None

    return lookup_display_text(conn, playback_id), lookup_category(conn, playback_id)

def main():
    conn = connect_db()

    listener_conn = None
    database_song_text = None
    file_override_text = None
    active_rds_text = None
    last_sent_text = None

    rotation_enabled = False
    rotation_program_text = None
    next_rotation_at = None

    latest_id = None
    last_file_mtime = None

    rotation_epoch = time.monotonic()
    force_send_song_now = False

    if listener_conn is None or listener_conn.closed:
        listener_conn = connect_listener()

        latest_id = lookup_latest_playback_id(listener_conn)

        if latest_id is not None:
            database_song_text = lookup_display_text(
                listener_conn,
                latest_id,
            )
            current_song_category = lookup_category(
                listener_conn,
                latest_id,
            )

    try:
        while True:
            if listener_conn is None or listener_conn.closed:
                listener_conn = connect_listener()
                
            file_was_changed, last_file_mtime = file_changed(
                CURRENT_SONG_FILE,
                last_file_mtime,
            )

            if file_was_changed:
                raw_file_text = read_stable_file(CURRENT_SONG_FILE)
                special_text = (
                    resolve_file_override(raw_file_text)
                    if raw_file_text
                    else None
                )

                if special_text:
                    file_override_text = special_text

                    logging.info(
                        "Special CurrentSong override enabled: raw=%r resolved=%r",
                        raw_file_text,
                        file_override_text,
                    )

                    if file_override_text != last_sent_text:
                        send_rds_text(file_override_text)
                        write_now_playing(file_override_text)
                        last_sent_text = file_override_text

                        logging.info(
                            "Sent special CurrentSong override to RDS: %s",
                            file_override_text,
                        )

                elif file_override_text:
                    # The file changed away from a special item back to a normal song.
                    # Remove the override and restore the most recent database text.
                    logging.info(
                        "Special CurrentSong override cleared: raw=%r",
                        raw_file_text
                    )

                    file_override_text = None

                    if database_song_text and database_song_text != last_sent_text:
                        send_rds_text(database_song_text)
                        write_now_playing(database_song_text)
                        last_sent_text = database_song_text

                        logging.info(
                            "Restored database RDS text: %s",
                            database_song_text,
                        )

            # Wait for a Postgres notification, up to one second.
            ready, _, _ = select.select([listener_conn], [], [], 1)

            if ready:
                listener_conn.poll()

                while listener_conn.notifies:
                    current_song_category = None
                    that_80s_show = False
                    a_category = False
                    b_category = False
                    c_category = False
                    d_category = False
                    e_category = False
                    f_category = False
                    g_category = False
                    notification = listener_conn.notifies.pop(0)
                    database_song_text, current_song_category = process_notification(
                        listener_conn,
                        notification,
                    )

                    if not database_song_text:
                        logging.warning(
                            "No RDS text resolved for playback payload=%r",
                            notification.payload,
                        )
                        continue

                    if file_override_text:
                        logging.info(
                            "Database RDS text not sent because special ooverride is active: "
                            "CurrentSong override is active: database=%r override=%r",
                            database_song_text,
                            file_override_text,
                        )
                        continue

                    try:
                        if current_song_category:
                            if '80s' in current_song_category:
                                that_80s_show = True
                            if 'A power oldie' in current_song_category:
                                a_category = True
                            if 'B medium oldie' in current_song_category:
                                b_category = True
                            if 'C slow oldie' in current_song_category:
                                c_category = True
                            if 'D extra slow oldie' in current_song_category:
                                d_category = True
                            if 'E beatles-beachboys-elvis' in current_song_category:
                                e_category = True
                            if 'F image track' in current_song_category:
                                f_category = True
                            if 'G golden oldie' in current_song_category:
                                g_category = True
                    except:
                        current_song_category = None
                    active_rds_text = database_song_text
                    rotation_epoch = time.monotonic()
                    force_send_song_now = True
                    rotation_enabled = that_80s_show
                    rotation_program_text = "That 80s Show" if that_80s_show else None
                    next_rotation_at = rotation_epoch + ROTATE_SECONDS
                    logging.info("Song category: %s", current_song_category)
                    logging.info("Resolved display text: %s", active_rds_text)
                    if active_rds_text:
                        text_to_send = active_rds_text

                        if text_to_send != last_sent_text:
                            send_rds_text(text_to_send)
                            write_now_playing(text_to_send)
                            last_sent_text = text_to_send

                            logging.info("Sent to RDS: %s", text_to_send)


            if (
                rotation_enabled
                and rotation_program_text
                and next_rotation_at is not None
                and not file_override_text
                and time.monotonic() >= next_rotation_at
            ):
                if last_sent_text == active_rds_text:
                    text_to_send = rotation_program_text
                else:
                    text_to_send = active_rds_text

                if text_to_send != last_sent_text:
                    send_rds_text(text_to_send)
                    write_now_playing(text_to_send)
                    last_sent_text = text_to_send

                    logging.info(
                        "Rotated RDS text: %s",
                        text_to_send,
                    )

                next_rotation_at = time.monotonic() + ROTATE_SECONDS

    except KeyboardInterrupt:
        logging.info("Exiting on keyboard interrupt")
    except psycopg2.Error as e:
        logging.exception("Database error: %s", e)
    except psycopg2.OperationalError as exc:
        logging.error("PostgreSQL listener connection error: %s", exc)

        if listener_conn:
            try:
                listener_conn.close()
            except Exception:
                pass

        listener_conn = None
        time.sleep(5)

    except Exception:
        logging.exception("Unhandled RDS listener error")
        time.sleep(2)
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
