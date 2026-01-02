import os
import json
import asyncio
from dotenv import load_dotenv

# Load the API Key from the .env file
load_dotenv()

from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import PlainTextResponse

# Import Deepgram
from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveTranscriptionEvents,
    LiveOptions,
)
# Initialize the application
app = FastAPI()
# Validate configuration
API_KEY = os.getenv("DEEPGRAM_API_KEY")
if not API_KEY:
    raise ValueError("Deepgram API Key is missing from .env file")

# config: quiet the logs
config = DeepgramClientOptions(options={"keepalive": "true"})
deepgram = DeepgramClient(API_KEY, config)

@app.post("/incoming-call")
async def handle_incoming_call(request: Request):
    """
    Handle incoming calls and start a Media Stream.
    """
    # 1. Get the domain we are currently running on (e.g., your-ngrok-url.app)
    # This ensures the TwiML always points to the correct WebSocket address.
    host = request.headers.get("host")
    
    if not host:
        return PlainTextResponse("Missing Host Header", status_code=400)

    # 2. Construct the TwiML instructions
    # We tell Twilio to connect to our WebSocket route: wss://{host}/media-stream
    xml_response = f"""
    <Response>
        <Say>The Anti-Scam Vigilante is listening.</Say>
        <Connect>
            <Stream url="wss://{host}/media-stream" />
        </Connect>
    </Response>
    """
    
    # 3. Return the XML
    return PlainTextResponse(xml_response, media_type="application/xml")

@app.websocket("/media-stream")
async def websocket_endpoint(websocket: WebSocket):
    """
    The main WebSocket handler for the audio stream.
    """
    # 1. Accept the connection (Handshake)
    await websocket.accept()
    print(">> Connection accepted. Waiting for audio...")

    try:
        while True:
            # 2. Receive the next message from Twilio
            # This 'await' releases the CPU to handle other calls while we wait for data
            message = await websocket.receive_text()
            
            # 3. Parse the JSON
            data = json.loads(message)
            event_type = data.get("event")

            # 4. Handle specific events
            if event_type == "start":
                stream_sid = data['start']['streamSid']
                print(f">> Stream Started! SID: {stream_sid}")
            
            elif event_type == "media":
                # RAW AUDIO DATA IS HERE
                # In Phase 2, we will send this payload to Deepgram
                pass 
            
            elif event_type == "stop":
                print(">> Stream stopped by Twilio.")
                break

    except Exception as e:
        # Catch unexpected errors so the server doesn't crash
        print(f"!! WebSocket Error: {e}")
        
    finally:
        # Always close the socket cleanly
        await websocket.close()
        print(">> Connection closed.")
