# app.py
import os
import sys
from waitress import serve
from flask import Flask, Response, request, render_template_string
from openai import OpenAI
from datetime import datetime

app = Flask(__name__)
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

HTML_PAGE = """
<!doctype html>
<html>
  <head><title>TTS Stream Demo</title></head>
  <body>
    <h1>TTS Stream Demo</h1>
    <!--<form method="GET" action="/tts">
      <label>Text:</label>
      <input type="text" name="text" size="60" value="You're listening to 94.9 KVWJ-LP, Hyrum." />
      <button type="submit">Play</button>
    </form>-->

    <!--<p>Raw audio stream URL for current text:</p>
    <audio controls autoplay>
      <source src="/tts?text={{ text }}" type="audio/mpeg">
      Your browser does not support audio.
    </audio>-->
  </body>
</html>
"""

def logger(*args):
    print(*args, file=sys.stderr, flush=True)

@app.route("/")
def index():
    text = request.args.get("text", "You're listening to 94.9 KVWJ-LP, Hyrum.")
    return render_template_string(HTML_PAGE, text=text)

@app.route("/tts")
def tts():
    #text = request.args.get("text", "You're listening to 94.9 KVWJ-LP, Hyrum.")
    today = datetime.now()
    filename = today.strftime('%Y-%m-%d') + '.log'
    try:
        with open('counter.txt', 'r') as counter:
                count_txt = counter.readline()
                count = int(count_txt)
    except:
        print('No counter.txt file exists, creating file...')
        count = 0
    with open('/mnt/zara/ZaraLogs/'+filename, 'r', encoding='utf-8', errors='ignore') as log:
        lines = log.readlines()
        # If using the browser, use lines[-1]
        line = lines[-1] if lines else ''
        # Change to lines[-2] when being used by Zara
        # line = lines[-2] if lines else ''
        print('Line: ', line)
        if r'C:\Users\KVWJ\Desktop\ye olde new gold\playback\clockwheel rotation' in line or r'C:\Users\KVWJ\Desktop\pls\Night only' in line or r'C:\Users\KVWJ\Desktop\pls\Day only' in line:
            if r'C:\Users\KVWJ\Desktop\pls\Night only' in line or r'C:\Users\KVWJ\Desktop\pls\Day only' in line:
                song_info = line.split(r'C:\Users\KVWJ\Desktop\pls')
            if r'C:\Users\KVWJ\Desktop\ye olde new gold\playback\clockwheel rotation' in line:
                song_info = line.split(r'C:\Users\KVWJ\Desktop\ye olde new gold\playback\clockwheel rotation')
            if song_info:
                print('Song Info: ', song_info)
                song_info_split = song_info[1].split('\\')
                timestamp = song_info[0].split('\t')[0]
                try:
                    song_name = song_info_split[2].strip().split(' - ')[0]
                    song_artist = song_info_split[2].strip().split(' - ')[1]
                    song_year = song_info_split[2].strip().split(' - ')[2].split('.mp3')[0]
                    song_category = song_info_split[1]
                except:
                    #print('Skipping line, not song:',song_info_split)
                    pass
                #print('counter: ', counter)
                print("Song Artist: ", song_artist)
                print("Song Name: ", song_name)
                if count == 0:
                    text = song_artist + ' with ' + song_name + ' on J-95!'
                if count == 1:
                    text = song_name + ", that's "  + song_artist + ' on K-V-W-J!'
                logger('Generating audio file with text:\n' + text)
                print('Count: ', count)
                count +=1
    with open('counter.txt', 'w') as counter:
        if count ==2:
            count = 0
        counter.write(str(count))
            
    def generate():
        # Streaming request to OpenAI text-to-speech
        # NOTE: adjust model/voice/format to what you want
        with client.audio.speech.with_streaming_response.create(
            model="gpt-4o-mini-tts",  # example model name
            voice="nova",
            input=text,
            instructions="Speak with excitement like a radio DJ!",
            response_format="mp3",   # or "wav", "mp3", etc.
            stream_format="audio",   # important for audio streaming
        ) as resp:
            for chunk in resp.iter_bytes(chunk_size=4096):
                if chunk:
                    yield chunk

    # Use chunked transfer encoding
    return Response(
        generate(),
        mimetype="audio/mpeg",
        direct_passthrough=True,
    )

if __name__ == "__main__":
    #app.run(debug=True, threaded=True)
    serve(app, host="0.0.0.0", port=5000, threads=2)

