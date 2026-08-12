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

def process_notification(conn, notification) -> str | None:
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
    current_songname = None
    current_song_text = None
    last_sent_text = None
    latest_id = None
    current_file_text = None
    last_file_text = ''

    rotation_epoch = time.monotonic()
    force_send_song_now = False

    if listener_conn is None or listener_conn.closed:
        listener_conn = connect_listener()

        latest_id = lookup_latest_playback_id(listener_conn)
        current_song_text = lookup_display_text(
            listener_conn,
            latest_id,
        )
        current_song_category = lookup_category(
            listener_conn,
            latest_id,
        )

    send_rds_text(current_song_text)
    last_sent_text = current_song_text
    write_now_playing(current_song_text)
    logging.info("Sent to RDS: %s", current_song_text)

    try:
        while True:
            if listener_conn is None or listener_conn.closed:
                listener_conn = connect_listener()
                
            if current_file_text != last_file_text:

                current_file_text = read_stable_file(CURRENT_SONG_FILE)

                if current_file_text:
                    special_text = resolve_special_case(current_file_text)

                    if special_text:
                        logging.info(
                            "Special Zara item detected: raw=%r resolved=%r",
                            current_file_text,
                            special_text,
                        )

                        current_file_text = special_text
                        send_rds_text(current_file_text)
                        write_now_playing(current_file_text)
                        last_file_text = current_file_text
                        logging.info("Sent to RDS: %s", current_file_text)

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
                    current_song_text, current_song_category = process_notification(
                        listener_conn,
                        notification,
                    )

                    if not current_song_text:
                        logging.warning(
                            "No RDS text resolved for playback payload=%r",
                            notification.payload,
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
                    rotation_epoch = time.monotonic()
                    force_send_song_now = True
                    logging.info("Song category: %s", current_song_category)
                    logging.info("Resolved display text: %s", current_song_text)
                    if current_song_text:
                        if force_send_song_now:
                            text_to_send = current_song_text
                            force_send_song_now = False
                        if that_80s_show:
                            PROGRAM_TEXT = "That 80s Show"
                            elapsed = time.monotonic() - rotation_epoch
                            slot = int(elapsed // ROTATE_SECONDS)

                            if slot % 2 == 0:
                                text_to_send = current_song_text
                            elif text_to_send is None:
                                text_to_send = PROGRAM_TEXT
                            else:
                                text_to_send = PROGRAM_TEXT
                        # if a_category:
                        #     PROGRAM_TEXT = "A Power Oldie on KVWJ"
                        #     elapsed = time.monotonic() - rotation_epoch
                        #     slot = int(elapsed // ROTATE_SECONDS)

                        #     if slot % 2 == 0:
                        #         text_to_send = current_song_text
                        #     else:
                        #         text_to_send = PROGRAM_TEXT

                        # if f_category:
                        #     PROGRAM_TEXT = "An Image Track on KVWJ"
                        #     elapsed = time.monotonic() - rotation_epoch
                        #     slot = int(elapsed // ROTATE_SECONDS)

                        #     if slot % 2 == 0:
                        #         text_to_send = current_song_text
                        #     else:
                        #         text_to_send = PROGRAM_TEXT

                        # if g_category:
                        #     PROGRAM_TEXT = "A Golden Oldie on KVWJ"
                        #     elapsed = time.monotonic() - rotation_epoch
                        #     slot = int(elapsed // ROTATE_SECONDS)

                        #     if slot % 2 == 0:
                        #         text_to_send = current_song_text
                        #     else:
                        #         text_to_send = PROGRAM_TEXT

                        # text_to_send = safe_trim(text_to_send)

                        if text_to_send != last_sent_text:
                            send_rds_text(text_to_send)
                            write_now_playing(text_to_send)
                            last_sent_text = text_to_send
                            logging.info("Sent to RDS: %s", text_to_send)

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
    main()                current = f.read().strip().replace("\x00", "")
                print('CurrentSong.txt contents:', current)
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

def lookup_display_text(conn, songname: str) -> str | None:    
    sql = """
        SELECT CONCAT_WS(
            ' - ',
            COALESCE(NULLIF(t.track_display_name, ''), NULLIF(t.track_name, '')),
            COALESCE(NULLIF(a.artist_display_name, ''), NULLIF(a.artist_name, '')),
            NULLIF(y.year::text, '')
        )
        FROM tracks t
        LEFT JOIN artists a
            ON a.id = t.artistid
        LEFT JOIN years y
            ON y.id = t.yearid
        WHERE t.filename = %s 
        LIMIT 1
        """
    with conn.cursor() as cur:
        debug_sql = cur.mogrify(sql, (songname,)).decode("utf-8", errors="replace")
        logging.info("lookup_display_text songname raw=%r", songname)
        logging.info("lookup_display_text SQL=%s", debug_sql)
        cur.execute(sql, (songname,))
        row = cur.fetchone()

        logging.info("lookup_display_text row=%r", row)

        return row[0] if row and row[0] else None

def lookup_category(conn, filename: str) -> str | None:
    sql =   """
            SELECT c.category
            FROM categories c
            JOIN tracks t ON t.categoryid = c.id
            WHERE t.filename = %s
            LIMIT 1
            """
    with conn.cursor() as cur:
        debug_sql = cur.mogrify(sql, (filename + '.mp3',)).decode("utf-8", errors="replace")
        logging.info("lookup_category songname raw=%r", filename + '.mp3')
        logging.info("lookup_category SQL=%s", debug_sql)
        cur.execute(sql, (filename + '.mp3',))
        row = cur.fetchone()

        logging.info("lookup_category row=%r", row)
        
        return row[0] if row and row[0] else None

def file_changed(path: str, last_mtime: float | None) -> tuple[bool, float | None]:
    try:
        current_mtime = os.path.getmtime(path)
    except FileNotFoundError:
        return False, last_mtime

    if last_mtime is None:
        return False, current_mtime

    if current_mtime != last_mtime:
        return True, current_mtime

    return False, current_mtime

def resolve_song_text(conn, filename: str) -> str:
    for needle, replacement in SPECIAL_CASES.items():
        if needle in filename:
            return replacement
    if filename is not None:
        filename = filename.replace("\x00", "").strip()
        try:
            db_text = lookup_display_text(conn, filename + '.mp3')
        except:
            logging.warning("DB Text lookup failed looking for: %s", filename + '.mp3')
            return None
        if db_text:
            return db_text
        if db_text is None:
            try:
                logging.info("Performing additional DB Text lookup for: %s", filename + '.mp3')
                db_text = lookup_display_text(conn, filename + '.mp3')
            except:
                logging.warning("DB Text lookup failed looking for: %s", filename + '.mp3')
                return None
        if db_text:
            return db_text

    return None

def main():
    conn = connect_db()

    last_mtime = None
    current_songname = None
    current_song_text = None
    last_sent_text = None

    rotation_epoch = time.monotonic()
    force_send_song_now = False

    try:
        while True:
            changed, last_mtime = file_changed(CURRENT_SONG_FILE, last_mtime)

            if changed:
                current_song_category = None
                that_80s_show = False
                a_category = False
                b_category = False
                c_category = False
                d_category = False
                e_category = False
                f_category = False
                g_category = False
                songname = read_stable_file(CURRENT_SONG_FILE)
                if songname:
                    current_songname = songname
                    current_song_text = resolve_song_text(conn, songname)
                    try:
                        if current_song_text:
                            current_song_category = lookup_category(conn, songname)    
                    except:
                        current_song_text = None
                    if current_song_text is None:
                        time.sleep(5)
                        logging.info("Performing additional Resolve Text lookup for: %s", songname + '.mp3')
                        current_song_text = resolve_song_text(conn, songname)
                        try:
                            if current_song_text:
                                current_song_category = lookup_category(conn, songname)    
                        except:
                            current_song_text = None
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
                    rotation_epoch = time.monotonic()
                    force_send_song_now = True
                    logging.info("Detected new song: %s", current_songname)
                    logging.info("Song category: %s", current_song_category)
                    logging.info("Resolved display text: %s", current_song_text)
            if current_song_text:
                if force_send_song_now:
                    text_to_send = current_song_text
                    force_send_song_now = False
                if that_80s_show:
                    PROGRAM_TEXT = "That 80s Show"
                    elapsed = time.monotonic() - rotation_epoch
                    slot = int(elapsed // ROTATE_SECONDS)

                    if slot % 2 == 0:
                        text_to_send = current_song_text
                    elif text_to_send is None:
                        text_to_send = PROGRAM_TEXT
                    else:
                        text_to_send = PROGRAM_TEXT
                # if a_category:
                #     PROGRAM_TEXT = "A Power Oldie on KVWJ"
                #     elapsed = time.monotonic() - rotation_epoch
                #     slot = int(elapsed // ROTATE_SECONDS)

                #     if slot % 2 == 0:
                #         text_to_send = current_song_text
                #     else:
                #         text_to_send = PROGRAM_TEXT

                # if f_category:
                #     PROGRAM_TEXT = "An Image Track on KVWJ"
                #     elapsed = time.monotonic() - rotation_epoch
                #     slot = int(elapsed // ROTATE_SECONDS)

                #     if slot % 2 == 0:
                #         text_to_send = current_song_text
                #     else:
                #         text_to_send = PROGRAM_TEXT

                # if g_category:
                #     PROGRAM_TEXT = "A Golden Oldie on KVWJ"
                #     elapsed = time.monotonic() - rotation_epoch
                #     slot = int(elapsed // ROTATE_SECONDS)

                #     if slot % 2 == 0:
                #         text_to_send = current_song_text
                #     else:
                #         text_to_send = PROGRAM_TEXT

                # text_to_send = safe_trim(text_to_send)

                if text_to_send != last_sent_text:
                    send_rds_text(text_to_send)
                    write_now_playing(text_to_send)
                    last_sent_text = text_to_send
                    logging.info("Sent to RDS: %s", text_to_send)

            time.sleep(1)

    except KeyboardInterrupt:
        logging.info("Exiting on keyboard interrupt")
    except psycopg2.Error as e:
        logging.exception("Database error: %s", e)
    except Exception as e:
        logging.exception("Unhandled error: %s", e)
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
