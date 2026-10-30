import os
import psycopg2
import time
import json
import logging
from datetime import datetime, timedelta
from watchdog.observers.polling import PollingObserver
from watchdog.events import FileSystemEventHandler
import threading
import smtplib
from email.mime.text import MIMEText

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

to_email_addresses = os.getenv('TO_EMAIL')
from_email = os.getenv('FROM_EMAIL')
gmail_username = os.getenv('GMAIL_USERNAME')
gmail_app_password = os.getenv('GMAIL_APP_PASSWORD')

to_email = to_email_addresses.split(",")

#Function to send email
def email_new_notification(from_email, to_email, text_msg, mail_host, mail_port):

    logging.info(f"Sending new email message to: {to_email} from: {from_email}")
    message = MIMEText(text_msg, "plain")
    message["Subject"] = "KVWJ Playback Insert Error"
    message["From"] = from_email
    message["To"] = ", ".join(to_email)


    #part1 = MIMEText(text_msg, "plain")

    #message.attach(part1)

    try:
        server = smtplib.SMTP_SSL(mail_host, mail_port)
        server.login(gmail_username, gmail_app_password)

        server.sendmail(
            from_email, to_email,  message.as_string()
        )
    except smtplib.SMTPException:
        logging.error(f"Unable to send email to {to_email} through server {mail_host}")


# PostgreSQL connection - update with your details
def get_connection():
    return psycopg2.connect(
        dbname=os.getenv("DATABASE_NAME"),
        user=os.getenv("DATABASE_USER"),
        password=os.getenv("DATABASE_PASSWORD"),
        host=os.getenv("DATABASE_HOST"),
        port=os.getenv("DATABASE_PORT", 5432)
    )

# Global connection
con = get_connection()
con.autocommit = False  # Manual commits for transactions
cur = con.cursor()

directory = '/mnt/logs/ZaraLogs'

class LogHandler(FileSystemEventHandler):
    def __init__(self):
        self.startup_complete = False
        self.db_lock = threading.Lock()
        self.file_state = {}

    def process_log_file(self, filepath, read_existing=False):
        date = os.path.basename(filepath).split(".log")[0]

        try:
            filedate = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return

        fourweeksago = datetime.now() - timedelta(days=29)

        if filedate <= fourweeksago:
            return

        try:
            stat = os.stat(filepath)
        except FileNotFoundError:
            return

        state = self.file_state.get(filepath)

        # Read from the beginning when:
        # - This is initial startup processing.
        # - This is a newly observed file.
        # - The file was rotated/replaced (inode changed).
        # - The file was truncated.
        if (
            read_existing
            or state is None
            or state["inode"] != stat.st_ino
            or stat.st_size < state["offset"]
        ):
            offset = 0
        else:
            offset = state["offset"]

        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as log:
                log.seek(offset)
                new_lines = log.readlines()
                new_offset = log.tell()

        except Exception as e:
            logging.error(f"Error reading {filepath}: {e}")
            return

        self.file_state[filepath] = {
            "inode": stat.st_ino,
            "offset": new_offset,
        }

        if not new_lines:
            return

        logging.info(
            f"Processing {len(new_lines)} new line(s) from "
            f"{filepath}, starting at byte offset {offset}"
        )

        with self.db_lock:
            for line in new_lines:
                try:
                    self.parse_and_insert(line, date, None)
                except Exception as e:
                    text_msg = "Error processing new log line in" + filepath + ":" + str(e)
                    logging.error(text_msg)
                    con.rollback()

        logging.info(f"Finished processing new entries in: {filepath}")

    def parse_and_insert(self, line, date, line_num):
        paths = [
            r'C:\Users\KVWJ\Desktop\pls',
            r'C:\Users\KVWJ\Desktop\ye olde new gold\playback\clockwheel rotation',
            r'C:\ye olde new gold\playback\clockwheel rotation'
        ]
        
        song_info = None
        is_sunday = False
        is_80s_show = False
        
        for path in paths:
            if path in line:
                if 'Special Liners' in line:
                    #print('Special Liner found')
                    raise Exception('Not a valid song line')
                try:
                    song_info = line.split(path)[1]
                    #print('Line contents are: ',line)
                    #print('Song Info is:', song_info)
                    if '\\Sunday' in path or '\\Sunday' in song_info:
                        #song_name = song_info.strip().split(' - ')[0].split('.mp3')[0]
                        song_info_split = song_info.split('\\')
                        #print('Sunday song name is: ',song_name)
                        song_category = song_info_split[1]
                        #print('Song category is: ',song_category)
                        if 'Sunday' in song_category:
                            song_category = 'Sunday'
                            #print(song_info)
                            is_sunday = True
                    if '\\That 80s Show' in path or '\\That 80s Show' in song_info:
                        try:
                            #print('That 80s Show song found: ',song_info)
                            song_info_split = song_info.split('\\')
                            song_category = song_info_split[2]
                            #print('Song category is: ',song_category)
                            is_80s_show = True
                        except Exception as e:
                            logging.error('Error processing That 80s Show song: ',e)
                except:
                    break
        
        if not song_info or len(song_info) < 2:
            return
        
        try:
            song_info_split = song_info.split('\\')
            #print('Song info split is: ',song_info_split)
            timestamp = line.split('\t')[0]
            #print('Timestamp is: ',timestamp)
            
            if is_sunday:
                song_name = song_info_split[2].strip().split(' - ')[0].split('.mp3')[0]
                #print(song_name)
                song_category = song_info_split[1]
                if 'Sunday' in song_category:
                    song_category = 'Sunday'
                #print(song_category)
                cur.execute("""
                    SELECT id FROM tracks WHERE track_name = %s AND artistid IS NULL""",(song_name,))
                trackid = cur.fetchone()
                #print(trackid)
                if trackid is None:
                    try:
                        logging.info(f'Inserting song {song_name} into tracks')
                        cur.execute("""
                                    INSERT INTO tracks (track_name, categoryid)
                                    VALUES (%s, %s)
                                    """, (song_name, 11))
                        con.commit()
                        cur.execute("""
                            SELECT id FROM tracks WHERE track_name = %s AND artistid IS NULL""",(song_name,))
                        trackid = cur.fetchone()
                    except psycopg2.Error as e:
                        con.rollback()
                        text_msg = 'Could not insert song ' + song_name, + ' into tracks\n' + str(e)
                        logging.error(text_msg)
                        email_new_notification(from_email, to_email, text_msg, 'smtp.gmail.com', 465)
                        try:
                            notify_conn = get_connection()
                            notify_conn.autocommit = True # Ensures the notification fires immediately
                            
                            notify_cursor = notify_conn.cursor()
                            
                            # Package up the error into a JSON string
                            payload = json.dumps({
                                "status": "ROLLBACK",
                                "error_message": str(e).strip()
                            })
                            
                            # Broadcast over the 'database_errors' channel
                            notify_cursor.execute("NOTIFY database_errors, %s;", (payload,))
                            
                            # Clean up the notification connection right away
                            notify_cursor.close()
                            notify_conn.close()
                        
                        except Exception as notify_error:
                            logging.error(f"Failed to send the error notification: {notify_error}")
                cur.execute("""
                    SELECT filename FROM tracks WHERE track_name = %s AND artistid IS NULL""",(song_name,))
                track_filename = cur.fetchone()
                if track_filename[0] is None:
                    logging.info(f'Updating Track filename: {song_info_split[2].strip()} with track id of {trackid}')
                    try:
                        cur.execute("""
                                    UPDATE tracks SET filename = %s WHERE id = %s""", (song_info_split[2].strip(),trackid[0]))
                        con.commit()
                    except Exception as e:
                        logging.error('Could not update filename for ', song_name, '\n', e)
                try:
                    logging.info(f'Inserting song {song_name} into playbacks')
                    cur.execute("""
                        INSERT INTO playback (trackid, date, timestamp)
                        VALUES (%s, %s, %s)
                        ON CONFLICT DO NOTHING
                        RETURNING id
                    """, (trackid[0], date, timestamp))

                    playback_row = cur.fetchone()
                    con.commit()

                    if playback_row:
                        logging.info(f"Inserted playback.id={playback_row[0]}")
                    else:
                        logging.info(
                            f"Playback already exists; skipped: "
                            f"trackid={trackid[0]}, date={date}, timestamp={timestamp}"
                        )
                    logging.info(f'Adding Sunday song play {song_category} {song_name} {date} {timestamp}')
                    self.song_name = song_name
                except psycopg2.Error as e:
                    con.rollback()
                    text_msg = "Database error:" + str(e)
                    logging.error(text_msg)
                    email_new_notification(from_email, to_email, text_msg, 'smtp.gmail.com', 465)
                    try:
                        notify_conn = get_connection()
                        notify_conn.autocommit = True # Ensures the notification fires immediately
                        
                        notify_cursor = notify_conn.cursor()
                        
                        # Package up the error into a JSON string
                        payload = json.dumps({
                            "status": "ROLLBACK",
                            "error_message": str(e).strip()
                        })
                        
                        # Broadcast over the 'database_errors' channel
                        notify_cursor.execute("NOTIFY database_errors, %s;", (payload,))
                        
                        # Clean up the notification connection right away
                        notify_cursor.close()
                        notify_conn.close()
                        
                    except Exception as notify_error:
                        logging.error(f"Failed to send the error notification: {notify_error}")
            elif is_80s_show:
                song_detail = song_info_split[3].strip().split(' - ')
                logging.info(f'Song detail is: {song_detail}')
                song_name = song_info_split[3].strip().split(' - ')[1].split('.mp3')[0]
                logging.info(f'{song_name}')
                song_category = song_info_split[2]
                logging.info(f'{song_category}')
                song_artist = song_info_split[3].strip().split(' - ')[0].split('.mp3')[0]
                logging.info(f'{song_artist}')
                cur.execute("""
                        SELECT id FROM artists WHERE UPPER(artist_name) = %s""",(song_artist.upper().strip(),))
                artistid = cur.fetchone()
                if artistid is None:
                    try:
                        logging.info(f'Artist {song_artist} not found. Creating...')
                        logging.info(f'Song detail is {song_detail}')
                        logging.info(f'{repr(type(song_artist))} {repr(song_artist)}')
                        logging.info(f"INSERT params count: {len((song_artist,))}")
                        logging.info(f"Params repr: {repr((song_artist,))}")
                        logging.info(f"First 50 chars: {repr((song_artist,)[:50])}")
                        cur.execute("INSERT INTO artists (artist_name) VALUES (%s)", (song_artist,))
                        logging.info(f"AFTER INSERT - rowcount: {cur.rowcount}")
                        con.commit()
                        logging.info('Commit succeeded')
                        cur.execute("""
                            SELECT id FROM artists WHERE UPPER(artist_name) = %s""",(song_artist.upper(),))
                        artistid = cur.fetchone()
                    except psycopg2.Error as e:
                        con.rollback()
                        text_msg = 'Error inserting artist ' + song_artist + str(e) + '\n'
                        import sys
                        text_msg = "EXCEPTION LINE: " + str(sys.exc_info()[2].tb_lineno) + '\n'
                        text_msg = text_msg + "EXCEPTION TYPE: " + str(type(e).__name__) + '\n'
                        text_msg = text_msg + "EXCEPTION FULL: " + str(repr(str(e)))
                        logging.error(text_msg)
                        email_new_notification(from_email, to_email, text_msg, 'smtp.gmail.com', 465)
                        try:
                            notify_conn = get_connection()
                            notify_conn.autocommit = True # Ensures the notification fires immediately
                            
                            notify_cursor = notify_conn.cursor()
                            
                            # Package up the error into a JSON string
                            payload = json.dumps({
                                "status": "ROLLBACK",
                                "error_message": str(e).strip()
                            })
                            
                            # Broadcast over the 'database_errors' channel
                            notify_cursor.execute("NOTIFY database_errors, %s;", (payload,))
                            
                            # Clean up the notification connection right away
                            notify_cursor.close()
                            notify_conn.close()
                            
                        except Exception as notify_error:
                            logging.error(f"Failed to send the error notification: {notify_error}")
                        import traceback
                        traceback.print_exc()
                cur.execute("""
                    SELECT id FROM categories WHERE category = %s""",(song_category,))
                categoryid = cur.fetchone()
                if categoryid is None:
                    try:
                        logging.info('Category not found. Creating...')
                        cur.execute("""
                                    INSERT INTO categories
                                        (category)
                                        VALUES (%s)
                                    """, (song_category,))
                        con.commit()
                        cur.execute("""
                                    SELECT id FROM categories where category = %s""", (song_category,))
                        categoryid = cur.fetchone()
                    except psycopg2.Error as e:
                        con.rollback()
                        text_msg = 'Error inserting category ' + song_category + str(e) + '\n'
                        import sys
                        text_msg = text_msg + "EXCEPTION LINE: " + str(sys.exc_info()[2].tb_lineno) + '\n'
                        text_msg = text_msg + "EXCEPTION TYPE: " + str(type(e).__name__) + '\n'
                        text_msg = text_msg + "EXCEPTION FULL:" + str(repr(str(e)))
                        logging.error(text_msg)
                        email_new_notification(from_email, to_email, text_msg, 'smtp.gmail.com', 465)
                        try:
                            notify_conn = get_connection()
                            notify_conn.autocommit = True # Ensures the notification fires immediately
                            
                            notify_cursor = notify_conn.cursor()
                            
                            # Package up the error into a JSON string
                            payload = json.dumps({
                                "status": "ROLLBACK",
                                "error_message": str(e).strip()
                            })
                            
                            # Broadcast over the 'database_errors' channel
                            notify_cursor.execute("NOTIFY database_errors, %s;", (payload,))
                            
                            # Clean up the notification connection right away
                            notify_cursor.close()
                            notify_conn.close()
                            
                        except Exception as notify_error:
                            logging.error(f"Failed to send the error notification: {notify_error}")
                        import traceback
                        traceback.print_exc()
                cur.execute("""
                    SELECT id FROM tracks WHERE track_name = %s AND artistid = %s""",(song_name,artistid[0]))
                trackid = cur.fetchone()
                #logging.info(trackid)
                if trackid is None:
                    try:
                        logging.info('Track not found.  Creating...')
                        logging.info(f'Inserting {song_artist} - {song_name} \ncategory id is: {categoryid[0]} artist id is: {artistid[0]}')
                        cur.execute("""
                                    INSERT INTO tracks
                                        (track_name,categoryid,artistid)
                                        VALUES (%s,%s,%s)
                                    """, (song_name,categoryid[0],artistid[0]))
                        con.commit()
                        cur.execute("""
                            SELECT id FROM tracks WHERE track_name = %s AND artistid = %s""",(song_name,artistid[0]))
                        trackid = cur.fetchone()
                    except psycopg2.Error as e:
                        con.rollback()
                        text_msg = 'Could not insert ' + song_name + ' by ' + song_artist + ' into tracks table.\n' + str(e)
                        logging.error(text_msg)
                        email_new_notification(from_email, to_email, text_msg, 'smtp.gmail.com', 465)
                        try:
                            notify_conn = get_connection()
                            notify_conn.autocommit = True # Ensures the notification fires immediately
                            
                            notify_cursor = notify_conn.cursor()
                            
                            # Package up the error into a JSON string
                            payload = json.dumps({
                                "status": "ROLLBACK",
                                "error_message": str(e).strip()
                            })
                            
                            # Broadcast over the 'database_errors' channel
                            notify_cursor.execute("NOTIFY database_errors, %s;", (payload,))
                            
                            # Clean up the notification connection right away
                            notify_cursor.close()
                            notify_conn.close()
                            
                        except Exception as notify_error:
                            logging.error(f"Failed to send the error notification: {notify_error}")
                cur.execute("""
                    SELECT filename FROM tracks WHERE track_name = %s AND artistid = %s""",(song_name,artistid[0]))
                track_filename = cur.fetchone()
                if track_filename[0] is None:
                    logging.info(f'Updating Track filename: {song_info_split[3].strip()} with track id of {trackid}')
                    try:
                        cur.execute("""
                                    UPDATE tracks SET filename = %s WHERE id = %s""", (song_info_split[3].strip(),trackid[0]))
                        con.commit()
                    except Exception as e:
                        logging.info(f'Could not update filename for {song_artist} - {song_name} \n {e}')
                try:
                    logging.info(f'Inserting song {song_name} into playbacks')
                    cur.execute("""
                        INSERT INTO playback (trackid, date, timestamp)
                        VALUES (%s, %s, %s)
                        ON CONFLICT DO NOTHING
                        RETURNING id
                    """, (trackid[0], date, timestamp))

                    playback_row = cur.fetchone()
                    con.commit()

                    if playback_row:
                        logging.info(f"Inserted playback.id={playback_row[0]}")
                    else:
                        logging.info(
                            f"Playback already exists; skipped: "
                            f"trackid={trackid[0]}, date={date}, timestamp={timestamp}"
                        )
                    logging.info(f'Adding That 80s Show song play {song_category} {song_name} {date} {timestamp}')
                    self.song_name = song_name
                except psycopg2.Error as e:
                    con.rollback()
                    text_msg = "Database error: " + str(e)
                    logging.error(text_msg)
                    email_new_notification(from_email, to_email, text_msg, 'smtp.gmail.com', 465)
                    try:
                        notify_conn = get_connection()
                        notify_conn.autocommit = True # Ensures the notification fires immediately
                        
                        notify_cursor = notify_conn.cursor()
                        
                        # Package up the error into a JSON string
                        payload = json.dumps({
                            "status": "ROLLBACK",
                            "error_message": str(e).strip()
                        })
                        
                        # Broadcast over the 'database_errors' channel
                        notify_cursor.execute("NOTIFY database_errors, %s;", (payload,))
                        
                        # Clean up the notification connection right away
                        notify_cursor.close()
                        notify_conn.close()
                        
                    except Exception as notify_error:
                        logging.error(f"Failed to send the error notification: {notify_error}")
            else:
                song_detail = song_info_split[2].strip().split(' - ')
                #print('Song detail is: ', song_detail)
                if 'Sunday' in song_detail:
                    logging.info(song_detail)
                if len(song_detail) >= 3:
                    song_name = song_detail[0]
                    #print('Song name is: ',song_name)
                    song_artist = song_detail[1]
                    #print('Song artist is: ',song_artist)
                    song_year = song_detail[2].split('.mp3')[0]
                    #print('Song year is: ',song_year)
                    song_category = song_info_split[1]
                    #print('Song category is: ',song_category)
                    try:
                        song_year = int(song_year[:4])
                    except:
                        song_year = None
                    if isinstance(song_year, int):
                        #print('Song year is int and is: ', song_year)
                        cur.execute("""
                            SELECT id FROM years WHERE year = %s""",(song_year,))
                        yearid = cur.fetchone()
                        if not yearid:
                            year_int = int(song_year)
                            logging.info(f'Year {song_year} not found. Creating...')
                            try:
                                cur.execute("""
                                            INSERT INTO years
                                                (year)
                                                VALUES (%s)
                                            """,(year_int,))
                                con.commit()
                            except psycopg2.Error as e:
                                con.rollback()
                                text_msg = 'Could not insert ' + song_year + ' into years table.\n' + str(e)
                                logging.error(text_msg)
                                email_new_notification(from_email, to_email, text_msg, 'smtp.gmail.com', 465)
                                try:
                                    notify_conn = get_connection()
                                    notify_conn.autocommit = True # Ensures the notification fires immediately
                                    
                                    notify_cursor = notify_conn.cursor()
                                    
                                    # Package up the error into a JSON string
                                    payload = json.dumps({
                                        "status": "ROLLBACK",
                                        "error_message": str(e).strip()
                                    })
                                    
                                    # Broadcast over the 'database_errors' channel
                                    notify_cursor.execute("NOTIFY database_errors, %s;", (payload,))
                                    
                                    # Clean up the notification connection right away
                                    notify_cursor.close()
                                    notify_conn.close()
                                    
                                except Exception as notify_error:
                                    logging.error(f"Failed to send the error notification: {notify_error}")
                            cur.execute("""
                                        SELECT id FROM years WHERE year = %s""", (song_year,))
                            yearid = cur.fetchone()
                            logging.info(f'Got year {yearid} from years table')
                    else:
                        song_year = None
                    cur.execute("""
                        SELECT id FROM categories WHERE category = %s""",(song_category,))
                    categoryid = cur.fetchone()
                    if categoryid is None:
                        logging.info('Category not found. Creating...')
                        cur.execute("""
                                    INSERT INTO categories
                                        (category)
                                        VALUES (%s)
                                    """, (song_category,))
                        con.commit()
                        cur.execute("""
                                    SELECT id FROM categories where category = %s""", (song_category,))
                        categoryid = cur.fetchone()
                    cur.execute("""
                        SELECT id FROM artists WHERE UPPER(artist_name) = %s""",(song_artist.upper().strip(),))
                    artistid = cur.fetchone()
                    if artistid is None:
                        try:
                            logging.info(f'Artist {song_artist} not found. Creating...')
                            logging.info(f'Song detail is {song_detail}')
                            logging.info(f'{repr(type(song_artist))} {repr(song_artist)}')
                            logging.info(f"INSERT params count: {len((song_artist,))}")
                            logging.info(f"Params repr: {repr((song_artist,))}")
                            logging.info(f"First 50 chars: {repr((song_artist,)[:50])}")
                            logging.info(f"Song year is: {song_year}")
                            cur.execute("INSERT INTO artists (artist_name) VALUES (%s)", (song_artist,))
                            logging.info(f"AFTER INSERT - rowcount: {cur.rowcount}")
                            con.commit()
                            logging.info('Commit succeeded')
                            cur.execute("""
                                SELECT id FROM artists WHERE UPPER(artist_name) = %s""",(song_artist.upper(),))
                            artistid = cur.fetchone()
                        except psycopg2.Error as e:
                            con.rollback()
                            text_msg = 'Error inserting artist ' + song_artist + str(e) + '\n'
                            import sys
                            text_msg = text_msg + "EXCEPTION LINE: " + str(sys.exc_info()[2].tb_lineno) + '\n'
                            text_msg = text_msg + "EXCEPTION TYPE: " + str(type(e).__name__) + '\n'
                            text_msg = text_msg + "EXCEPTION FULL: " + str(repr(str(e)))
                            logging.error(text_msg)
                            email_new_notification(from_email, to_email, text_msg, 'smtp.gmail.com', 465)
                            try:
                                notify_conn = get_connection()
                                notify_conn.autocommit = True # Ensures the notification fires immediately
                                
                                notify_cursor = notify_conn.cursor()
                                
                                # Package up the error into a JSON string
                                payload = json.dumps({
                                    "status": "ROLLBACK",
                                    "error_message": str(e).strip()
                                })
                                
                                # Broadcast over the 'database_errors' channel
                                notify_cursor.execute("NOTIFY database_errors, %s;", (payload,))
                                
                                # Clean up the notification connection right away
                                notify_cursor.close()
                                notify_conn.close()
                                
                            except Exception as notify_error:
                                logging.error(f"Failed to send the error notification: {notify_error}")
                            import traceback
                            traceback.print_exc()
                    cur.execute("""
                        SELECT id FROM tracks WHERE track_name = %s AND artistid = %s""",(song_name,artistid[0]))
                    trackid = cur.fetchone()
                    #print('Track ID is: ',trackid)
                    if trackid is None:
                        logging.info('Track not found.  Creating...')
                        if song_year is None:
                            logging.info(f'{song_name} by {song_artist} does not have a year in the filename.')
                            try:
                                logging.info(f'Inserting {song_artist} - {song_name} \ncategory id is: {categoryid[0]} artist id is: {artistid[0]}')
                                cur.execute("""
                                            INSERT INTO tracks
                                                (track_name,categoryid,artistid)
                                                VALUES (%s,%s,%s)
                                            """, (song_name,categoryid[0],artistid[0]))
                                con.commit()
                                cur.execute("""
                                            SELECT id FROM tracks WHERE track_name = %s AND categoryid = %s AND artistid = %s""",(song_name,categoryid[0],artistid[0]))
                                trackid = cur.fetchone()
                            except psycopg2.Error as e:
                                con.rollback()
                                text_msg = 'Could not insert ' + song_name + ' by ' + song_artist + ' into tracks table.\n' + str(e)
                                logging.error(text_msg)
                                email_new_notification(from_email, to_email, text_msg, 'smtp.gmail.com', 465)
                                try:
                                    notify_conn = get_connection()
                                    notify_conn.autocommit = True # Ensures the notification fires immediately
                                    
                                    notify_cursor = notify_conn.cursor()
                                    
                                    # Package up the error into a JSON string
                                    payload = json.dumps({
                                        "status": "ROLLBACK",
                                        "error_message": str(e).strip()
                                    })
                                    
                                    # Broadcast over the 'database_errors' channel
                                    notify_cursor.execute("NOTIFY database_errors, %s;", (payload,))
                                    
                                    # Clean up the notification connection right away
                                    notify_cursor.close()
                                    notify_conn.close()
                                    
                                except Exception as notify_error:
                                    logging.error(f"Failed to send the error notification: {notify_error}")
                        try:
                            cur.execute("""
                                        INSERT INTO tracks
                                            (track_name,categoryid,yearid,artistid)
                                            VALUES (%s,%s,%s,%s)
                                        """, (song_name,categoryid[0],yearid[0],artistid[0]))
                            con.commit()
                            cur.execute("""
                                        SELECT id FROM tracks WHERE track_name = %s AND categoryid = %s AND yearid = %s AND artistid = %s""",(song_name,categoryid[0],yearid[0],artistid[0]))
                            trackid = cur.fetchone()
                        except psycopg2.Error as e:
                            con.rollback()
                            text_msg = 'Could not insert ' + song_name + ' by ' + song_artist + ' from ' + song_year + ' in ' + song_category + ' into tracks table.\n' + str(e)
                            logging.error(text_msg)
                            email_new_notification(from_email, to_email, text_msg, 'smtp.gmail.com', 465)
                            try:
                                notify_conn = get_connection()
                                notify_conn.autocommit = True # Ensures the notification fires immediately
                                
                                notify_cursor = notify_conn.cursor()
                                
                                # Package up the error into a JSON string
                                payload = json.dumps({
                                    "status": "ROLLBACK",
                                    "error_message": str(e).strip()
                                })
                                
                                # Broadcast over the 'database_errors' channel
                                notify_cursor.execute("NOTIFY database_errors, %s;", (payload,))
                                
                                # Clean up the notification connection right away
                                notify_cursor.close()
                                notify_conn.close()
                                
                            except Exception as notify_error:
                                logging.error(f"Failed to send the error notification: {notify_error}")
                    cur.execute("""
                        SELECT filename FROM tracks WHERE track_name = %s AND artistid = %s""",(song_name,artistid[0]))
                    track_filename = cur.fetchone()
                    #print('Track filename is: ', track_filename)
                    if track_filename[0] is None:
                        logging.info(f'Updating Track filename: {song_info_split[2].strip()} with track ID: {trackid}')
                        try:
                            cur.execute("""
                                        UPDATE tracks SET filename = %s WHERE id = %s""", (song_info_split[2].strip(),trackid[0]))
                            con.commit()
                        except Exception as e:
                            logging.info(f'Could not update filename for {song_artist} - {song_name} \n {e}')
                    try:
                        logging.info(f'Adding song play {song_category} {song_artist} {song_name} {song_year} {date} {timestamp}')
                        cur.execute("""
                            INSERT INTO playback (trackid, date, timestamp)
                            VALUES (%s, %s, %s)
                            ON CONFLICT DO NOTHING
                            RETURNING id
                        """, (trackid[0], date, timestamp))
                        playback_row = cur.fetchone()
                        con.commit()

                        if playback_row:
                            logging.info(f"Inserted playback.id={playback_row[0]}")
                        else:
                            logging.info(
                                f"Playback already exists; skipped: "
                                f"trackid={trackid[0]}, date={date}, timestamp={timestamp}"
                            )
                        self.song_name = song_name
                        self.song_artist = song_artist
                        self.song_year = song_year
                    except psycopg2.Error as e:
                        con.rollback()
                        text_msg = "Database error: " + str(e)
                        logging.error(text_msg)
                        email_new_notification(from_email, to_email, text_msg, 'smtp.gmail.com', 465)
                        try:
                            notify_conn = get_connection()
                            notify_conn.autocommit = True # Ensures the notification fires immediately
                            
                            notify_cursor = notify_conn.cursor()
                            
                            # Package up the error into a JSON string
                            payload = json.dumps({
                                "status": "ROLLBACK",
                                "error_message": str(e).strip()
                            })
                            
                            # Broadcast over the 'database_errors' channel
                            notify_cursor.execute("NOTIFY database_errors, %s;", (payload,))
                            
                            # Clean up the notification connection right away
                            notify_cursor.close()
                            notify_conn.close()
                            
                        except Exception as notify_error:
                            logging.error(f"Failed to send the error notification: {notify_error}")
            con.commit()
        except Exception as e:
            logging.info(f'Skipping line {line_num} in {date}:', e)
            logging.info(f'Line contents: {line}')
            if trackid is not None:
                logging.info(f'Trackid: {trackid[0]}')
            if track_filename is not None:
                logging.info(f'Filename: {track_filename[0]}')
            logging.info(f'Song name {song_name}')

    def on_created(self, event):
        if (
            not event.is_directory
            and event.src_path != os.path.join(directory, "CurrentSong.txt")
        ):
            time.sleep(1)
            self.process_log_file(event.src_path, read_existing=False)

    def on_modified(self, event):
        if (
            not event.is_directory
            and event.src_path != os.path.join(directory, "CurrentSong.txt")
        ):
            time.sleep(2)
            self.process_log_file(event.src_path, read_existing=False)

# Process existing files on startup
handler = LogHandler()
for filename in os.scandir(directory):
    process = False
    date = os.path.basename(filename.path).split('.log')[0]
    try:
        filedate = datetime.strptime(date, '%Y-%m-%d')
    except ValueError:
        continue
    fourweeksago = datetime.now() - timedelta(days=29)
    fourweeksago_str = fourweeksago.strftime('%Y-%m-%d')
    logging.info(f'{filename.path} - {fourweeksago_str}')
    if filedate >= fourweeksago:
        process = True
    if filename.is_file() and process == True and filename.name != 'CurrentSong.txt':
        handler.process_log_file(filename.path, read_existing=True)
    if process == False:
        logging.info(f'Not processing {filename.path}')
handler.startup_complete = True

# Setup watchdog
observer = PollingObserver(timeout=10)
observer.schedule(handler, directory, recursive=False)
observer.start()

try:
    logging.info(f"Monitoring {directory} for new log files...")
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    observer.stop()
    cur.close()
    con.close()

observer.join()
