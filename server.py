import asyncio
import json
import time
import sys
import platform
import logging
from typing import Optional
from io import BytesIO

from aiohttp import web

from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRelay
from sdk.wsconnect import (
    connect, object_from_string, encode_mess,
    pack_message, send_action, login
)

# --- 配置 ---
GAME_CODE = "mrfz"
TOKEN_FILE = "token"
HOST = "127.0.0.1"
PORT = 22888
WIDTH = 1280
HEIGHT = 720

# --- 颜色定义 ---
class Colors:
    RESET = '\033[0m'
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    CYAN = '\033[36m'
    BOLD = '\033[1m'

if platform.system() == "Windows":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# --- 全局状态 ---
class AppState:
    def __init__(self):
        self.pc: Optional[RTCPeerConnection] = None
        self.sock: Optional[web.WebSocketResponse] = None
        self.snapshotter: Optional['VideoSnapshotter'] = None
        self.is_ready = False
        self.width = WIDTH
        self.height = HEIGHT

app_state = AppState()

# --- 快照工具 (从 ark-demo.py 移植并修改) ---
class VideoSnapshotter:
    def __init__(self, video_track):
        self._track = video_track
        self._task: Optional[asyncio.Task] = None
        self._last_frame = None
        self._got_first = asyncio.Event()
        self._running = False

    def start(self):
        if self._task:
            return
        self._running = True
        self._task = asyncio.create_task(self._pump())

    async def _pump(self):
        try:
            while self._running:
                frame = await self._track.recv()
                self._last_frame = frame
                if not self._got_first.is_set():
                    self._got_first.set()
        except Exception:
            pass

    async def wait_ready(self, timeout=20.0):
        try:
            await asyncio.wait_for(self._got_first.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def snapshot_bytes(self, fmt="jpeg") -> Optional[bytes]:
        await self._got_first.wait()
        frame = self._last_frame
        if frame is None:
            return None
        try:
            from PIL import Image
            img = frame.to_image()
            with BytesIO() as bio:
                img.save(bio, format=fmt.upper())
                return bio.getvalue()
        except Exception:
            return None

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

# --- API 处理函数 ---
async def handle_info(request: web.Request):
    if not app_state.is_ready:
        return web.json_response({
            "status": "error",
            "message": "Cloud gaming service not connected"
        }, status=500)
    
    return web.json_response({
        "status": "ok",
        "width": app_state.width,
        "height": app_state.height
    })

async def handle_screencap(request: web.Request):
    if not app_state.is_ready or not app_state.snapshotter:
        return web.json_response({"status": "error", "message": "Service not ready"}, status=500)

    image_bytes = await app_state.snapshotter.snapshot_bytes(fmt="jpeg")
    if not image_bytes:
        return web.json_response({"status": "error", "message": "Failed to capture screen"}, status=500)

    return web.Response(body=image_bytes, content_type="image/jpeg")

async def handle_click(request: web.Request):
    if not app_state.is_ready or not app_state.sock:
        return web.json_response({"status": "error", "message": "Service not ready"}, status=500)
    
    try:
        data = await request.json()
        x, y = int(data['x']), int(data['y'])
    except (json.JSONDecodeError, KeyError, ValueError):
        return web.json_response({"status": "error", "message": "Invalid request body"}, status=400)

    action = pack_message("cm", {"x": x, "y": y})
    await send_action(app_state.sock, action)
    return web.json_response({"status": "ok"})

async def handle_swipe(request: web.Request):
    if not app_state.is_ready or not app_state.sock:
        return web.json_response({"status": "error", "message": "Service not ready"}, status=500)

    try:
        data = await request.json()
        x1, y1 = int(data['x1']), int(data['y1'])
        x2, y2 = int(data['x2']), int(data['y2'])
        duration = int(data['duration'])
    except (json.JSONDecodeError, KeyError, ValueError):
        return web.json_response({"status": "error", "message": "Invalid request body"}, status=400)

    # aiortc/webrtc 不直接支持滑动，通过一系列快速的鼠标移动来模拟
    steps = max(2, int(duration / 50)) # 每50ms移动一次
    for i in range(steps + 1):
        progress = i / steps
        x = int(x1 + (x2 - x1) * progress)
        y = int(y1 + (y2 - y1) * progress)
        action = pack_message("mm", {"x": x, "y": y})
        await send_action(app_state.sock, action)
        await asyncio.sleep(duration / 1000 / steps)
        
    return web.json_response({"status": "ok"})

# --- 云游戏连接逻辑 ---
async def run_cloud_game(app: web.Application):
    try:
        token = open(TOKEN_FILE).read().strip()
    except FileNotFoundError:
        token = ""
    if not token:
        pnum = input(f"{Colors.YELLOW}input your phone number: {Colors.RESET}").strip()
        login("86-" + pnum)
        token = open(TOKEN_FILE).read().strip()
        if not token:
            print(f"{Colors.RED}Login failed, exiting.{Colors.RESET}", file=sys.stderr)
            sys.exit(1)

    print(f"{Colors.CYAN}[*] Connecting to cloud gaming service...{Colors.RESET}")
    res, sock = await connect(token, GAME_CODE, w=app_state.width, h=app_state.height)
    remote = object_from_string(res)
    
    pc = RTCPeerConnection()
    relay = MediaRelay()

    @pc.on("track")
    def on_track(track):
        if track.kind == "video":
            print(f"{Colors.BLUE}[*] Video track received.{Colors.RESET}")
            app_state.snapshotter = VideoSnapshotter(relay.subscribe(track))
            app_state.snapshotter.start()

    await pc.setRemoteDescription(remote)
    answer = await pc.createAnswer()
    patched = RTCSessionDescription(
        sdp=answer.sdp.replace("a=setup:active", "a=setup:passive"),
        type=answer.type,
    )
    await pc.setLocalDescription(patched)
    msg = {"id": str(int(time.time() * 1000)), "op": "answer", "data": {"sdp": patched.sdp}}
    await sock.send(encode_mess(json.dumps(msg)))

    app_state.pc = pc
    app_state.sock = sock

    print(f"{Colors.YELLOW}[*] Waiting for video stream...{Colors.RESET}")
    if not app_state.snapshotter:
        await asyncio.sleep(3)
    
    if app_state.snapshotter and await app_state.snapshotter.wait_ready():
        app_state.is_ready = True
        print(f"{Colors.GREEN}{Colors.BOLD}[✓] Service ready. API server is running at http://{HOST}:{PORT}{Colors.RESET}")
    else:
        print(f"{Colors.RED}[!] Failed to start video stream. API will not be fully functional.{Colors.RESET}", file=sys.stderr)

    # 保持连接
    try:
        await asyncio.Event().wait()
    finally:
        print(f"\n{Colors.CYAN}[*] Shutting down...{Colors.RESET}")
        if app_state.snapshotter:
            await app_state.snapshotter.stop()
        if pc and pc.connectionState != "closed":
            await pc.close()
        if sock and not sock.closed:
            await sock.close()
        print(f"{Colors.GREEN}[✓] Cloud game connection closed.{Colors.RESET}")

async def start_background_tasks(app: web.Application):
    app['cloud_game_task'] = asyncio.create_task(run_cloud_game(app))

async def cleanup_background_tasks(app: web.Application):
    app['cloud_game_task'].cancel()
    await app['cloud_game_task']

async def handle_input(request: web.Request):
    if not app_state.is_ready or not app_state.sock:
        return web.json_response({"status": "error", "message": "Service not ready"}, status=500)

    try:
        data = await request.json()
        text = data['text']
    except (json.JSONDecodeError, KeyError, TypeError):
        return web.json_response({"status": "error", "message": "Invalid request body, 'text' field is required"}, status=400)

    for char in str(text):
        action = pack_message("ip", {"word": char})
        await send_action(app_state.sock, action)
        await asyncio.sleep(0.05)

    return web.json_response({"status": "ok"})

async def handle_key(request: web.Request):
    if not app_state.is_ready or not app_state.sock:
        return web.json_response({"status": "error", "message": "Service not ready"}, status=500)
    # todo
    return web.json_response({"status": "ok"})

async def handle_root(request: web.Request):
    raise web.HTTPMovedPermanently('/screencap')

# --- 主函数 ---
def main():
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    access_log = logging.getLogger("aiohttp.access")

    app = web.Application()
    app.add_routes([
        web.get('/', handle_root),
        web.get('/info', handle_info),
        web.get('/screencap', handle_screencap),
        web.post('/click', handle_click),
        web.post('/swipe', handle_swipe),
        web.post('/input', handle_input),
        web.post('/key', handle_key),
    ])
    
    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(cleanup_background_tasks)
    
    web.run_app(app, host=HOST, port=PORT, access_log=access_log)

if __name__ == "__main__":
    main()