import os
import json
import asyncio
import base64

from dotenv import load_dotenv
from groq import Groq

# Load the API Key
load_dotenv()

from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import PlainTextResponse, HTMLResponse 
from twilio.jwt.access_token import AccessToken
from twilio.jwt.access_token.grants import VoiceGrant

from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveTranscriptionEvents,
    LiveOptions,
    SpeakOptions, 
)

import asyncio

#### Initialize the application ####

app = FastAPI()

# Validate configuration
API_KEY = os.getenv("DEEPGRAM_API_KEY")
if not API_KEY:
    raise ValueError("Deepgram API Key is missing from .env file")

# quiet the logs
config = DeepgramClientOptions(options={"keepalive": "true"})
deepgram = DeepgramClient(API_KEY, config)

@app.post("/incoming-call")
async def handle_incoming_call(request: Request):
    """
    Handle incoming calls and start a Media Stream.
    """
    # Get the domain
    host = request.headers.get("host")
    
    if not host:
        return PlainTextResponse("Missing Host Header", status_code=400)

    # Construct the TwiML instructions
    
    xml_response = f"""
    <Response>
        <Say>The Anti-Scam Vigilante is listening.</Say>
        <Connect>
            <Stream url="wss://{host}/media-stream" />
        </Connect>
    </Response>
    """
    
    # Return the XML
    return PlainTextResponse(xml_response, media_type="application/xml")

@app.get("/client", response_class=HTMLResponse)
async def client_interface():
    """
    Serves a simple HTML page to make the call from your browser.
    """
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Anti-Scam Softphone</title>
        <script src="https://cdn.jsdelivr.net/npm/@twilio/voice-sdk@2.10.0/dist/twilio.min.js"></script>
        <style>
            body { font-family: sans-serif; display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; background: #222; color: #fff; }
            button { padding: 15px 30px; font-size: 20px; cursor: pointer; border: none; border-radius: 5px; color: white; margin-top: 20px; }
            
            .btn-start { background: #4caf50; } /* Green */
            .btn-call { background: #00bcd4; display: none; } /* Blue, Hidden */
            .btn-hangup { background: #f44336; display: none; } /* Red, Hidden */
            
            button:disabled { background: #555; cursor: not-allowed; }
            #status { margin-top: 20px; color: #aaa; }
        </style>
    </head>
    <body>
        <h1>Vigilante Softphone (v2.2)</h1>
        <p id="status">Click Start to initialize audio...</p>
        
        <button id="startBtn" class="btn-start" onclick="init()">Start System</button>
        <button id="callBtn" class="btn-call" onclick="makeCall()">Call Bot</button>
        <button id="hangupBtn" class="btn-hangup" onclick="hangupCall()">Hang Up</button>

        <script>
            let device;

            async function init() {
                document.getElementById("startBtn").disabled = true;
                document.getElementById("status").innerText = "Requesting Token...";

                try {
                    const response = await fetch('/token');
                    const data = await response.json();
                    
                    document.getElementById("status").innerText = "Initializing Twilio...";
                    
                    device = new Twilio.Device(data.token, {
                        codecPreferences: ["opus", "pcmu"],
                        fakeLocalDTMF: true,
                        enableRingingState: true,
                        debug: true
                    });

                    // FIX: Don't wait for 'registered'. 
                    // Since we are outgoing-only, we are ready immediately after creation.
                    document.getElementById("status").innerText = "System Ready";
                    document.getElementById("startBtn").style.display = 'none';
                    document.getElementById("callBtn").style.display = 'block';

                    device.on("error", function(error) {
                        console.error("Twilio Error:", error);
                        document.getElementById("status").innerText = "Error: " + error.message;
                    });

                } catch (e) {
                    document.getElementById("status").innerText = "Setup Failed: " + e;
                    document.getElementById("startBtn").disabled = false;
                }
            }

            async function makeCall() {
                if (device) {
                    const params = { "To": "Bot" };
                    // Ensure we are ready
                    if (device.state === 'Unregistered') {
                         // This is fine for outgoing calls in v2
                    }
                    
                    try {
                        const call = await device.connect({ params: params });
                        
                        document.getElementById("status").innerText = "Calling Bot...";
                        toggleButtons(true);

                        call.on("disconnect", () => {
                            document.getElementById("status").innerText = "Call Ended";
                            toggleButtons(false);
                        });
                        
                        call.on("error", (e) => {
                             document.getElementById("status").innerText = "Call Error: " + e.message;
                        });
                        
                    } catch (e) {
                         document.getElementById("status").innerText = "Connect Failed: " + e.message;
                    }
                }
            }
            
            function hangupCall() {
                if(device) device.disconnectAll();
            }
            
            function toggleButtons(isCalling) {
                document.getElementById("callBtn").style.display = isCalling ? 'none' : 'block';
                document.getElementById("hangupBtn").style.display = isCalling ? 'block' : 'none';
            }
        </script>
    </body>
    </html>
    """

@app.get("/token")
async def get_token():
    """
    Generates a Twilio Access Token for the browser client.
    """
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    api_key = os.getenv("TWILIO_API_KEY_SID")
    api_secret = os.getenv("TWILIO_API_SECRET")
    app_sid = os.getenv("TWILIO_APP_SID")

    if not all([account_sid, api_key, api_secret, app_sid]):
        raise ValueError("Missing Twilio Credentials in .env")

    # Create the token
    token = AccessToken(account_sid, api_key, api_secret, identity="BrowserUser")
    
    # Allow outgoing calls to TwiML App
    voice_grant = VoiceGrant(
        outgoing_application_sid=app_sid,
        incoming_allow=False # We only make calls, don't receive thenm
    )
    token.add_grant(voice_grant)

    return {"token": token.to_jwt()}

@app.websocket("/media-stream")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print(">> Connection accepted. Waiting for audio...")
    
    # Get the current Async Event Loop
    
    loop = asyncio.get_event_loop()
    
    # State variables
    stream_sid = None
    
    async def process_and_reply(human_text):
        nonlocal stream_sid
        if not stream_sid:
            return

        print(f"Human said: {human_text}")

        # A. Generate Text with Groq
        try:
            chat_completion = groq_client.chat.completions.create(
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": human_text}
                ],
                model="llama-3.1-8b-instant",
                max_tokens=50,
            )
            ai_response = chat_completion.choices[0].message.content
            print(f">> Bob thinks: {ai_response}")

        except Exception as e:
            print(f"Groq Error: {e}")
            return

        # B. Generate Audio with Deepgram
        try:
            # Request Raw Mulaw 
            options = SpeakOptions(
                model="aura-asteria-en", # or aura-helios-en for male
                encoding="mulaw",
                sample_rate=8000,
                container="none"
            )
            
            res = deepgram.speak.v("1").stream({"text": ai_response}, options)
            
            # Extract raw bytes
            audio_data = None
            if hasattr(res, "stream"):
                audio_data = res.stream.getvalue()
            else:
                audio_data = res

            if audio_data:
                # Safety Header Strip
                if audio_data[:4] == b'RIFF':
                    audio_data = audio_data[44:]

                # CHUNKING: 160 bytes = 20ms @ 8000Hz
                chunk_size = 160 
                
                for i in range(0, len(audio_data), chunk_size):
                    chunk = audio_data[i:i+chunk_size]
                    
                    base64_audio = base64.b64encode(chunk).decode("utf-8")
                    media_message = {
                        "event": "media",
                        "streamSid": stream_sid,
                        "media": {
                            "payload": base64_audio
                        }
                    }
                    
                    await websocket.send_text(json.dumps(media_message))
                    
                    # PACING: Wait 20ms to match phone line speed
                    await asyncio.sleep(0.020)

                # Mark Complete 
                mark_message = {
                     "event": "mark",
                     "streamSid": stream_sid,
                     "mark": {
                         "name": "reply_complete"
                     }
                }
                await websocket.send_text(json.dumps(mark_message))
                
                print(">> Sent audio reply (Real Voice).")

        except Exception as e:
            print(f"TTS Error: {e}")

    # ----------------------------------------------------

    try:
        dg_connection = deepgram.listen.live.v("1")
        
        # Define the callback
        def on_message(self, result, **kwargs):
            sentence = result.channel.alternatives[0].transcript
            if len(sentence) > 0:
                
                asyncio.run_coroutine_threadsafe(process_and_reply(sentence), loop)

        dg_connection.on(LiveTranscriptionEvents.Transcript, on_message)

        options = LiveOptions(
            model="nova-2", 
            language="en-US", 
            smart_format=True, 
            encoding="mulaw", 
            sample_rate=8000,
            endpointing=300 # Wait 300ms of silence before sending transcript
        )

        if dg_connection.start(options) is False:
            print("Failed to start Deepgram connection")
            return

        print(">> Deepgram is ready and listening.")

        while True:
            message = await websocket.receive_text()
            data = json.loads(message)

            if data['event'] == 'start':
                # Capture the Stream SID so we know where to send audio back
                stream_sid = data['start']['streamSid']
                print(f">> Stream Started! SID: {stream_sid}")

            elif data['event'] == 'media':
                media_payload = data['media']['payload']
                audio_data = base64.b64decode(media_payload)
                dg_connection.send(audio_data)

            elif data['event'] == 'stop':
                print(">> Stream stopped.")
                break

    except Exception as e:
        print(f"Error: {e}")
    
    finally:
        await websocket.close()
        dg_connection.finish()
        print(">> Connection closed.")

# Load Groq Client
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
if not os.getenv("GROQ_API_KEY"):
    raise ValueError("Groq API Key missing")

# System Prompt
SYSTEM_PROMPT = "You are a suspicious elderly woman named 'Annie'. You are talking on the phone. You are skeptical of whoever calls you. Keep your responses short (under 10 words) and slightly rude."