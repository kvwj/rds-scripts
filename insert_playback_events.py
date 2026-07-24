import os
import psycopg2
import time
import socket
from datetime import datetime, timedelta
from watchdog.observers.polling import PollingObserver
from watchdog.events import FileSystemEventHandler
import threading

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

    def rds_send(self, host, port, content):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

            # 2-second overall "wait window" similar to nc -w 2
            s.settimeout(2.0)

            s.connect((host, int(port)))

            # Send content + newline to match `echo "TEXT?"`
            msg = (content if content.endswith("\n") else content + "\n").encode()
            s.sendall(msg)

            # Do NOT shutdown write; nc keeps the socket open during the wait
            # s.shutdown(socket.SHUT_WR)

            # Wait idle about 2 seconds like `-i 2` before we start reading
            time.sleep(2.0)

            data_received = []
            while True:
                try:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    data_received.append(chunk)
                except socket.timeout:
                    # no more data within timeout; stop
                    break

            print(repr(b"".join(data_received).decode(errors="replace")))
        except socket.error as e:
            print(f"RDS Socket error: {e}")
        finally:
            s.close()

    def process_log_file(self, filepath):
        date = os.path.basename(filepath).split('.log')[0]
        try:
            filedate = datetime.strptime(date, '%Y-%m-%d')
        except ValueError:
            return
        
        fourweeksago = datetime.now() - timedelta(days=29)
        fourweeksago_str = fourweeksago.strftime('%Y-%m-%d')
        if filedate <= fourweeksago:
            return
        
        # Delete old records
        try:
            cur.execute("DELETE FROM playback WHERE date < %s", (fourweeksago_str,))
            con.commit()
        except Exception as e:
            print('Not deleting old records:', e)
        
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as log:
                log_contents = log.readlines()
        except Exception as e:
            print(f'Error reading {filepath}:', e)
            return
        
        i = 1
        for line in log_contents:
            try:
                self.parse_and_insert(line, date, i)
                i += 1
            except Exception as e:
                #print(f'Skipping line {i} in {date}:', e)
                #print('Line contents: ', line)
                pass
        
        print(f'Processed log file: {filepath}')

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
                            print('Error processing That 80s Show song: ',e)
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
                        print('Inserting song ', song_name, ' into tracks')
                        cur.execute("""
                                    INSERT INTO tracks (track_name, categoryid)
                                    VALUES (%s, %s)
                                    """, (song_name, 11))
                        con.commit()
                        cur.execute("""
                            SELECT id FROM tracks WHERE track_name = %s AND artistid IS NULL""",(song_name,))
                        trackid = cur.fetchone()
                    except Exception as e:
                        print('Could not insert song ', song_name, ' into tracks\n', e)
                cur.execute("""
                    SELECT filename FROM tracks WHERE track_name = %s AND artistid IS NULL""",(song_name,))
                track_filename = cur.fetchone()
                if track_filename[0] is None:
                    print('Updating Track filename: ', song_info_split[2].strip(), ' with track id of ', trackid)
                    try:
                        cur.execute("""
                                    UPDATE tracks SET filename = %s WHERE id = %s""", (song_info_split[2].strip(),trackid[0]))
                        con.commit()
                    except Exception as e:
                        print('Could not update filename for ', song_name, '\n', e)
                try:
                    print('Inserting song ', song_name, ' into playbacks')
                    cur.execute("""
                        INSERT INTO playback (trackid, date, timestamp)
                        VALUES (%s, %s, %s)
                    """, (trackid[0], date, timestamp))
                    print('Adding Sunday song play', song_category, song_name, date, timestamp)
                    self.song_name = song_name
                except Exception as e:
                    print(e)
                    pass
                # try:
                #     rds_text = f"TEXT={song_name} - {song_artist} - {song_year}" 
                #     rds_send('192.168.0.108', 7005, rds_text)
                #     rds_send('192.168.0.108', 7005, 'TEXT=?')
                # except Exception as e:
                #     print(e)
            elif is_80s_show:
                song_detail = song_info_split[3].strip().split(' - ')
                print('Song detail is: ', song_detail)
                song_name = song_info_split[3].strip().split(' - ')[1].split('.mp3')[0]
                print(song_name)
                song_category = song_info_split[2]
                print(song_category)
                song_artist = song_info_split[3].strip().split(' - ')[0].split('.mp3')[0]
                print(song_artist)
                cur.execute("""
                        SELECT id FROM artists WHERE UPPER(artist_name) = %s""",(song_artist.upper().strip(),))
                artistid = cur.fetchone()
                if artistid is None:
                    try:
                        print('Artist ', song_artist, ' not found. Creating...')
                        print('Song detail is ', song_detail)
                        print(repr(type(song_artist)), repr(song_artist))
                        print("INSERT params count:", len((song_artist,)))
                        print("Params repr:", repr((song_artist,)))
                        print("First 50 chars:", repr((song_artist,)[:50]))
                        cur.execute("INSERT INTO artists (artist_name) VALUES (%s)", (song_artist,))
                        print(f"AFTER INSERT - rowcount: {cur.rowcount}")
                        con.commit()
                        print('Commit succeeded')
                        cur.execute("""
                            SELECT id FROM artists WHERE UPPER(artist_name) = %s""",(song_artist.upper(),))
                        artistid = cur.fetchone()
                    except Exception as e:
                        print('Error inserting artist ', song_artist, e)
                        import sys
                        print(f"EXCEPTION LINE: {sys.exc_info()[2].tb_lineno}")
                        print(f"EXCEPTION TYPE: {type(e).__name__}")
                        print(f"EXCEPTION FULL: {repr(str(e))}")
                        import traceback
                        traceback.print_exc()
                cur.execute("""
                    SELECT id FROM categories WHERE category = %s""",(song_category,))
                categoryid = cur.fetchone()
                if categoryid is None:
                    try:
                        print('Category not found. Creating...')
                        cur.execute("""
                                    INSERT INTO categories
                                        (category)
                                        VALUES (%s)
                                    """, (song_category,))
                        con.commit()
                        cur.execute("""
                                    SELECT id FROM categories where category = %s""", (song_category,))
                        categoryid = cur.fetchone()
                    except Exception as e:
                        print('Error inserting category ', song_category, e)
                        import sys
                        print(f"EXCEPTION LINE: {sys.exc_info()[2].tb_lineno}")
                        print(f"EXCEPTION TYPE: {type(e).__name__}")
                        print(f"EXCEPTION FULL: {repr(str(e))}")
                        import traceback
                        traceback.print_exc()
                cur.execute("""
                    SELECT id FROM tracks WHERE track_name = %s AND artistid = %s""",(song_name,artistid[0]))
                trackid = cur.fetchone()
                print(trackid)
                if trackid is None:
                    try:
                        print('Track not found.  Creating...')
                        print('Inserting ', song_artist, ' - ', song_name, '\ncategory id is:', categoryid[0], 'artist id is: ', artistid[0])
                        cur.execute("""
                                    INSERT INTO tracks
                                        (track_name,categoryid,artistid)
                                        VALUES (%s,%s,%s)
                                    """, (song_name,categoryid[0],artistid[0]))
                        con.commit()
                        cur.execute("""
                            SELECT id FROM tracks WHERE track_name = %s AND artistid = %s""",(song_name,artistid[0]))
                        trackid = cur.fetchone()
                    except Exception as e:
                        print('Could not insert ', song_name, ' by ', song_artist, ' into tracks table.\n',e)
                cur.execute("""
                    SELECT filename FROM tracks WHERE track_name = %s AND artistid = %s""",(song_name,artistid[0]))
                track_filename = cur.fetchone()
                if track_filename[0] is None:
                    print('Updating Track filename: ', song_info_split[3].strip(), ' with track id of ', trackid)
                    try:
                        cur.execute("""
                                    UPDATE tracks SET filename = %s WHERE id = %s""", (song_info_split[3].strip(),trackid[0]))
                        con.commit()
                    except Exception as e:
                        print('Could not update filename for ', song_artist, ' - ', song_name, '\n', e)
                try:
                    print('Inserting song ', song_name, ' into playbacks')
                    cur.execute("""
                        INSERT INTO playback (trackid, date, timestamp)
                        VALUES (%s, %s, %s)
                    """, (trackid[0], date, timestamp))
                    print('Adding That 80s Show song play', song_category, song_name, date, timestamp)
                    self.song_name = song_name
                except Exception as e:
                    print(e)
                    pass
            else:
                song_detail = song_info_split[2].strip().split(' - ')
                #print('Song detail is: ', song_detail)
                if 'Sunday' in song_detail:
                    print(song_detail)
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
                            print('Year ', song_year, ' not found. Creating...')
                            try:
                                cur.execute("""
                                            INSERT INTO years
                                                (year)
                                                VALUES (%s)
                                            """,(year_int,))
                                con.commit()
                            except Exception as e:
                                print('Could not insert ', song_year, ' into years table.\n',e)
                            cur.execute("""
                                        SELECT id FROM years WHERE year = %s""", (song_year,))
                            yearid = cur.fetchone()
                            print('Got year ', yearid, 'from years table')
                    else:
                        song_year = None
                    cur.execute("""
                        SELECT id FROM categories WHERE category = %s""",(song_category,))
                    categoryid = cur.fetchone()
                    if categoryid is None:
                        print('Category not found. Creating...')
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
                            print('Artist ', song_artist, ' not found. Creating...')
                            print('Song detail is ', song_detail)
                            print(repr(type(song_artist)), repr(song_artist))
                            print("INSERT params count:", len((song_artist,)))
                            print("Params repr:", repr((song_artist,)))
                            print("First 50 chars:", repr((song_artist,)[:50]))
                            print("Song year is: ", song_year)
                            cur.execute("INSERT INTO artists (artist_name) VALUES (%s)", (song_artist,))
                            print(f"AFTER INSERT - rowcount: {cur.rowcount}")
                            con.commit()
                            print('Commit succeeded')
                            cur.execute("""
                                SELECT id FROM artists WHERE UPPER(artist_name) = %s""",(song_artist.upper(),))
                            artistid = cur.fetchone()
                        except Exception as e:
                            print('Error inserting artist ', song_artist, e)
                            import sys
                            print(f"EXCEPTION LINE: {sys.exc_info()[2].tb_lineno}")
                            print(f"EXCEPTION TYPE: {type(e).__name__}")
                            print(f"EXCEPTION FULL: {repr(str(e))}")
                            import traceback
                            traceback.print_exc()
                    cur.execute("""
                        SELECT id FROM tracks WHERE track_name = %s AND artistid = %s""",(song_name,artistid[0]))
                    trackid = cur.fetchone()
                    #print('Track ID is: ',trackid)
                    if trackid is None:
                        print('Track not found.  Creating...')
                        if song_year is None:
                            print(song_name, ' by ', song_artist, ' does not have a year in the filename.')
                            try:
                                print('Inserting ', song_artist, ' - ', song_name, '\ncategory id is:', categoryid[0], 'artist id is: ', artistid[0])
                                cur.execute("""
                                            INSERT INTO tracks
                                                (track_name,categoryid,artistid)
                                                VALUES (%s,%s,%s)
                                            """, (song_name,categoryid[0],artistid[0]))
                                con.commit()
                                cur.execute("""
                                            SELECT id FROM tracks WHERE track_name = %s AND categoryid = %s AND artistid = %s""",(song_name,categoryid[0],artistid[0]))
                                trackid = cur.fetchone()
                            except Exception as e:
                                print('Could not insert ', song_name, ' by ', song_artist, ' into tracks table.\n',e)
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
                        except Exception as e:
                            print('Could not insert ', song_name, ' by ', song_artist, ' from ', song_year, ' in ', song_category, ' into tracks table.\n',e)
                    cur.execute("""
                        SELECT filename FROM tracks WHERE track_name = %s AND artistid = %s""",(song_name,artistid[0]))
                    track_filename = cur.fetchone()
                    #print('Track filename is: ', track_filename)
                    if track_filename[0] is None:
                        print('Updating Track filename: ', song_info_split[2].strip(), ' with track ID: ', trackid)
                        try:
                            cur.execute("""
                                        UPDATE tracks SET filename = %s WHERE id = %s""", (song_info_split[2].strip(),trackid[0]))
                            con.commit()
                        except Exception as e:
                            print('Could not update filename for ', song_artist, ' - ', song_name, '\n', e)
                    try:
                        print('Adding song play', song_category, song_artist, song_name, song_year, date, timestamp)
                        cur.execute("""
                            INSERT INTO playback (trackid, date, timestamp)
                            VALUES (%s, %s, %s)
                        """, (trackid[0], date, timestamp))
                        self.song_name = song_name
                        self.song_artist = song_artist
                        self.song_year = song_year
                    except Exception as e:
                        #print(e)
                        pass
                    # try:
                    #     rds_send('192.168.0.108', 7005, 'TEXT=', song_name, ' - ', song_artist, ' - ', song_year )
                    #     rds_send('192.168.0.108', 7005, 'TEXT=?')
                    # except Exception as e:
                    #     print(e)
            con.commit()
        except Exception as e:
            print(f'Skipping line {line_num} in {date}:', e)
            print('Line contents: ', line)
            if trackid is not None:
                print('Trackid: ', trackid[0])
            if track_filename is not None:
                print('Filename: ' , track_filename[0])
            print('Song name', song_name)

    def on_created(self, event):
        if not event.is_directory and event.src_path != os.path.join(directory, 'CurrentSong.txt'):
            time.sleep(1)
            self.process_log_file(event.src_path)

    def on_modified(self, event):
        if not event.is_directory and event.src_path != os.path.join(directory, 'CurrentSong.txt'):
            time.sleep(2)
            self.process_log_file(event.src_path)
            #try:
            #    if self.song_artist is not None and self.song_year is not None:
            #        rds_text = f"TEXT={self.song_name} - {self.song_artist} - {self.song_year}"
            #    if self.song_artist is not None and self.song_year is None:
            #        rds_text = f"TEXT={self.song_name} - {self.song_artist}"
            #    if self.song_artist is None and self.song_year is None:
            #        rds_text = f"TEXT={self.song_name}"
            #    self.rds_send('192.168.0.108', 7005, rds_text)
            #    self.rds_send('192.168.0.108', 7005, 'TEXT?')
            #except Exception as e:
            #    print(e)

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
    print(filename.path, ' - ', fourweeksago_str)
    if filedate >= fourweeksago:
        process = True
    if filename.is_file() and process == True and filename.name != 'CurrentSong.txt':
        handler.process_log_file(filename.path)
    if process == False:
        print('Not processing ', filename.path)
handler.startup_complete = True

# Setup watchdog
observer = PollingObserver(timeout=10)
observer.schedule(handler, directory, recursive=False)
observer.start()

try:
    print(f"Monitoring {directory} for new log files...")
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    observer.stop()
    cur.close()
    con.close()

observer.join()
