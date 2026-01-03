from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, status
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from database import messages, users
from pydantic import BaseModel
from jose import JWTError, jwt
import bcrypt
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
    plain_password = plain_password[:72].encode('utf-8')
    return bcrypt.checkpw(plain_password, hashed_password.encode('utf-8'))


def get_password_hash(password):
    password = password[:72].encode('utf-8')
    return bcrypt.hashpw(password, bcrypt.gensalt()).decode('utf-8')


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
    password = password[:72]
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


@app.get("/profile")
async def get_profile(token: str):
    """Get user profile data."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    user = users.find_one({"username": username})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return {
        "username": user.get("username"),
        "pfp": user.get("pfp", ""),
    }


class ProfileUpdate(BaseModel):
    token: str
    username: Optional[str] = None
    pfp: Optional[str] = None


@app.post("/profile")
async def update_profile(data: ProfileUpdate):
    """Update user profile (username and/or pfp)."""
    token = data.token
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        old_username = payload.get("sub")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    user = users.find_one({"username": old_username})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    updates = {}
    if data.username and data.username != old_username:
        # Check if new username exists
        if users.find_one({"username": data.username}):
            raise HTTPException(status_code=400, detail="Username already taken")
        updates["username"] = data.username
    
    if data.pfp:
        # Limit pfp size to 500KB
        if len(data.pfp) > 500000:
            raise HTTPException(status_code=400, detail="Image too large")
        updates["pfp"] = data.pfp
    
    if updates:
        users.update_one({"username": old_username}, {"$set": updates})
    
    # Generate new token if username changed
    new_username = updates.get("username", old_username)
    new_token = create_access_token({"sub": new_username})
    
    return {
        "access_token": new_token,
        "token_type": "bearer",
        "username": new_username,
        "pfp": updates.get("pfp", user.get("pfp", "")),
    }


@app.get("/search-users")
async def search_users(q: str, token: str):
    """Search for users by username."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        current_user = payload.get("sub")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    if not q or len(q) < 2:
        return []
    
    # Find users matching the query (exclude self)
    found = users.find({"username": {"$regex": f"^{q}", "$options": "i"}}).limit(10)
    results = [{"username": u.get("username"), "pfp": u.get("pfp", "")} for u in found if u.get("username") != current_user]
    return results


@app.post("/private-chats")
async def create_private_chat(token: str, with_user: str):
    """Create or get private chat with another user."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    if username == with_user:
        raise HTTPException(status_code=400, detail="Cannot chat with yourself")
    
    # Check if target user exists
    if not users.find_one({"username": with_user}):
        raise HTTPException(status_code=404, detail="User not found")
    
    # Create chat ID (sorted usernames for consistency)
    chat_id = "-".join(sorted([username, with_user]))
    
    return {"chat_id": chat_id, "with_user": with_user}


class PasswordChange(BaseModel):
    token: str
    old_password: str
    new_password: str


@app.post("/change-password")
async def change_password(data: PasswordChange):
    """Change user password."""
    try:
        payload = jwt.decode(data.token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    user = users.find_one({"username": username})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Verify old password
    if not verify_password(data.old_password, user.get("hashed_password", "")):
        raise HTTPException(status_code=401, detail="Incorrect old password")
    
    # Hash and update new password
    new_hashed = get_password_hash(data.new_password)
    users.update_one({"username": username}, {"$set": {"hashed_password": new_hashed}})
    
    return {"message": "Password changed successfully"}


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
@app.get("/")
async def root():
    return FileResponse("static/login.html")

app.mount("/", StaticFiles(directory="static", html=True), name="static")
