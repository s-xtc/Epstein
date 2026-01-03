from fastapi import FastAPI, WebSocket
from database import messages

app = FastAPI()

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    while True:
        data = await ws.receive_text()

        messages.insert_one({"message": data})

        await ws.send_text(data)
