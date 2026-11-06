# Smoke check (server must be running): python backend/smoke_check.py some.wav
# stream a wav to the server like the browser does, expect a final transcript + saved file.
import asyncio, json, os, sys, websockets

async def main():
    data = open(sys.argv[1], "rb").read()
    async with websockets.connect("ws://localhost:8000/ws/transcribe") as ws:
        for i in range(0, len(data), 8000):  # ~0.25s chunks, sent in real time
            await ws.send(data[i:i + 8000]); await asyncio.sleep(0.25)
        await ws.send("stop")
        msgs = [json.loads(m) async for m in ws]
    finals = [m["text"] for m in msgs if m.get("is_final")]
    saved = [m for m in msgs if m["type"] == "saved"]
    print("interim:", sum(1 for m in msgs if m.get("is_final") is False), "finals:", finals, "saved:", saved)
    assert finals and saved and os.path.exists(saved[0]["path"])
    print("OK")
asyncio.run(main())
