"""Independent capture worker with bounded asynchronous snapshot HTTP."""
import asyncio
import json
import logging
import os
from pathlib import Path
CAPTURE_PORT = int(os.environ.get("HIDAGENT_CAPTURE_PORT", "8891"))
OWNER = str(Path(__file__).resolve().parent)

async def handle(screen, reader, writer):
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)
        line = head.split(b"\r\n",1)[0].split()
        status, body = 404, b""
        headers = {"Cache-Control":"no-store"}
        if len(line) != 3 or line[0] != b"GET":
            status = 400
        elif line[1] == b"/health":
            status = 200
            snapshot = screen.snapshot()
            body = json.dumps({"service":"hidagent-capture", "owner":OWNER,
                "pid":os.getpid(), "live":snapshot is not None,
                "frame_snapshot_version":1}).encode()
            headers["Content-Type"] = "application/json"
        elif line[1] == b"/frame":
            snapshot = screen.snapshot()
            body = snapshot.jpeg if snapshot else b""
            status = 200 if snapshot else 204
            headers.update({"Content-Type":"image/jpeg", "X-Frame-Snapshot-Version":"1",
                            "X-Frame-Time":str(snapshot.timestamp if snapshot else 0)})
            if snapshot: headers["X-Frame-Id"] = snapshot.frame_id
        headers.update({"Content-Length":str(len(body)), "Connection":"close"})
        reason = {200:"OK",204:"No Content",400:"Bad Request",404:"Not Found"}[status]
        writer.write((f"HTTP/1.1 {status} {reason}\r\n"+"".join(f"{k}: {v}\r\n" for k,v in headers.items())+"\r\n").encode()+body)
        await asyncio.wait_for(writer.drain(), 2)
    except (TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
        pass
    finally:
        writer.close()
        try: await writer.wait_closed()
        except ConnectionError: pass

async def serve_async(screen, port=CAPTURE_PORT):
    server = await asyncio.start_server(lambda r,w:handle(screen,r,w), '127.0.0.1', port, limit=8192)
    screen.start()  # bind before touching the pipe or receiver
    try:
        async with server: await server.serve_forever()
    finally: screen.stop()

def serve(screen, port=CAPTURE_PORT):
    asyncio.run(serve_async(screen, port))

if __name__ == '__main__':
    from .screen_pipe import Screen
    logging.basicConfig(level=logging.INFO)
    serve(Screen(os.environ.get('AIRPLAY_NAME','iPhone-Agent')))
