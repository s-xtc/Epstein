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

# Track connected clients with their usernames
connected_clients = {}  # {ws: username}
current_username = "User"


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
    username = "Anonymous"
    connected_clients[ws] = username
    
    # Notify others of user joining
    await broadcast_event("user_joined", {"username": username, "count": len(connected_clients)})
    
    try:
        while True:
            data = await ws.receive_text()
            if not data.strip():
                continue

            # Parse message type
            try:
                import json
                msg_data = json.loads(data)
                msg_type = msg_data.get("type", "message")
                content = msg_data.get("content", "")
            except:
                msg_type = "message"
                content = data

            if msg_type == "set_username":
                # Change username
                old_username = username
                username = content.strip() or "Anonymous"
                connected_clients[ws] = username
                await broadcast_event("username_changed", {"old": old_username, "new": username})
                continue

            elif msg_type == "typing":
                # Broadcast typing indicator
                await broadcast_event("user_typing", {"username": username})
                continue

            elif msg_type == "message":
                # Store in database with username and timestamp
                msg_doc = {
                    "username": username,
                    "message": content,
                    "timestamp": datetime.utcnow(),
                }
                messages.insert_one(msg_doc)

                # Broadcast to all connected clients
                await broadcast_event("message", {
                    "username": username,
                    "text": content,
                    "timestamp": datetime.utcnow().isoformat()
                })

    except WebSocketDisconnect:
        connected_clients.pop(ws, None)
        await broadcast_event("user_left", {"username": username, "count": len(connected_clients)})


async def broadcast_event(event_type: str, data: dict):
    """Broadcast an event to all connected clients."""
    import json
    message = {"type": event_type, "data": data}
    for client in list(connected_clients.keys()):
        try:
            await client.send_json(message)
        except Exception:
            pass


# Serve static files AFTER defining API routes
app.mount("/", StaticFiles(directory="static", html=True), name="static")
