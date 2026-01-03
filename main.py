from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from database import messages
from datetime import datetime

app = FastAPI()

# Allow all origins for development/testing
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve the static front-end
app.mount("/", StaticFiles(directory="static", html=True), name="static")

# Track connected clients
connected_clients = set()


@app.get("/messages")
async def get_messages(limit: int = 50):
    """Return the most recent `limit` messages (oldest first)."""
    cursor = messages.find().sort("_id", -1).limit(limit)
    docs = list(cursor)
    msgs = [
        {"username": d.get("username", "Anonymous"), "text": d.get("message", "")}
        for d in reversed(docs)
    ]
    return msgs


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    connected_clients.add(ws)
    try:
        while True:
            data = await ws.receive_text()
            if not data.strip():
                continue

            # Store in database with username
            msg_doc = {
                "username": "User",  # could be enhanced with auth
                "message": data,
                "timestamp": datetime.utcnow(),
            }
            messages.insert_one(msg_doc)

            # Broadcast to all connected clients
            for client in connected_clients:
                try:
                    await client.send_json(
                        {"username": "User", "text": data}
                    )
                except Exception:
                    pass
    except WebSocketDisconnect:
        connected_clients.discard(ws)
