from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, status
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from database import messages, users
from pydantic import BaseModel
from jose import JWTError, jwt
from passlib.context import CryptContext
from datetime import timedelta
from typing import Optional
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

# Auth / password setup
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
SECRET_KEY = "dev-secret-change-me"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24


# Track connected clients with their usernames
connected_clients = {}  # {ws: username}


class UserCreate(BaseModel):
    username: str
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str


def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password):
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def authenticate_user(username: str, password: str):
    user = users.find_one({"username": username})
    if not user:
        return False
    if not verify_password(password, user.get("hashed_password", "")):
        return False
    return user


@app.post("/register", response_model=Token)
async def register(u: UserCreate):
    if users.find_one({"username": u.username}):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username already registered")
    hashed = get_password_hash(u.password)
    users.insert_one({"username": u.username, "hashed_password": hashed})
    access_token = create_access_token({"sub": u.username})
    return {"access_token": access_token, "token_type": "bearer"}


@app.post("/login", response_model=Token)
async def login(u: UserCreate):
    user = authenticate_user(u.username, u.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect username or password")
    access_token = create_access_token({"sub": u.username})
    return {"access_token": access_token, "token_type": "bearer"}


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
    # require token in query params
    token = ws.query_params.get("token")
    username = "Anonymous"
    if token:
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            username = payload.get("sub", "Anonymous")
        except JWTError:
            await ws.close(code=1008)
            return

    await ws.accept()
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
