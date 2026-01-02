import os
import json
import asyncio
import base64
from dotenv import load_dotenv

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
)


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

    # Connect to Deepgram
    
    try:
        dg_connection = deepgram.listen.live.v("1")
        
        # Define what happens when Deepgram hears text
        def on_message(self, result, **kwargs):
            sentence = result.channel.alternatives[0].transcript
            if len(sentence) > 0:
                print(f"Human said: {sentence}")

        # Hook up the event listener
        dg_connection.on(LiveTranscriptionEvents.Transcript, on_message)

        # Configure Deepgram model and other parameters
        options = LiveOptions(
            model="nova-2", 
            language="en-US", 
            smart_format=True,
            encoding="mulaw", # Twilio sends 'mulaw' encoding
            sample_rate=8000  # Phone lines are 8000Hz
        )

        # Start the Deepgram connection
        if dg_connection.start(options) is False:
            print("Failed to start Deepgram connection")
            return

        print(">> Deepgram is ready and listening.")

        while True:
            # Receive data from Twilio
            message = await websocket.receive_text()
            data = json.loads(message)

            if data['event'] == 'media':
                # Extract raw audio
                media_payload = data['media']['payload']
                # Decode base64 to bytes
                audio_data = base64.b64decode(media_payload)
                # Send to Deepgram
                dg_connection.send(audio_data)

            elif data['event'] == 'stop':
                print(">> Stream stopped.")
                break

    except Exception as e:
        print(f"Error: {e}")
    
    finally:
        # Cleanup
        await websocket.close()
        dg_connection.finish()
        print(">> Connection closed.")