import asyncio
import json
import time
import sys
import platform
import logging
from typing import Optional, Coroutine
from io import BytesIO

from aiohttp import web

from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRelay
from sdk.wsconnect import (
    connect, object_from_string, encode_mess,
    pack_message, send_action, login, exit_game
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
        self.token: Optional[str] = None
        self.cloud_game_task: Optional[asyncio.Task] = None

    def reset(self):
        self.pc = None
        self.sock = None
        self.snapshotter = None
        self.is_ready = False
        self.token = None
        self.cloud_game_task = None

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
        except asyncio.CancelledError:
            pass # 任务被取消是正常的
        except Exception:
            pass

    async def wait_ready(self, timeout=20.0):
        try:
            await asyncio.wait_for(self._got_first.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def snapshot_bytes(self, fmt="jpeg") -> Optional[bytes]:
        if not self._got_first.is_set():
            return None
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
            "status": "connecting" if app_state.cloud_game_task and not app_state.cloud_game_task.done() else "disconnected",
            "message": "Cloud gaming service not ready or not connected."
        })
    
    return web.json_response({
        "status": "ok",
        "width": app_state.width,
        "height": app_state.height
    })

async def handle_screencap(request: web.Request):
    if not app_state.is_ready or not app_state.snapshotter:
        return web.json_response({"status": "error", "message": "Service not ready"}, status=503)

    image_bytes = await app_state.snapshotter.snapshot_bytes(fmt="jpeg")
    if not image_bytes:
        return web.json_response({"status": "error", "message": "Failed to capture screen"}, status=500)

    return web.Response(body=image_bytes, content_type="image/jpeg")

async def handle_click(request: web.Request):
    if not app_state.is_ready or not app_state.sock:
        return web.json_response({"status": "error", "message": "Service not ready"}, status=503)
    
    try:
        data = await request.json()
        x, y = int(data['x']), int(data['y'])
    except (json.JSONDecodeError, KeyError, ValueError):
        return web.json_response({"status": "error", "message": "Invalid request body"}, status=400)

    logging.warning(f"Action: Click at ({x}, {y})")
    
    # 1. 先移动鼠标到目标位置
    move_action = pack_message("mm", {"x": x, "y": y})
    await send_action(app_state.sock, move_action)
    await asyncio.sleep(0.05) # 短暂等待，模拟真实操作

    # 2. 再执行点击操作
    click_action = pack_message("cm", {"x": x, "y": y})
    await send_action(app_state.sock, click_action)
    
    return web.json_response({"status": "ok"})

async def handle_swipe(request: web.Request):
    if not app_state.is_ready or not app_state.sock:
        return web.json_response({"status": "error", "message": "Service not ready"}, status=503)

    try:
        data = await request.json()
        x1, y1 = int(data['x1']), int(data['y1'])
        x2, y2 = int(data['x2']), int(data['y2'])
        duration_ms = int(data['duration'])
    except (json.JSONDecodeError, KeyError, ValueError):
        return web.json_response({"status": "error", "message": "Invalid request body"}, status=400)

    logging.warning(f"Action: Swipe from ({x1}, {y1}) to ({x2}, {y2}) in {duration_ms}ms")

    # 模拟滑动需要 "按下 -> 移动 -> 松开"
    # cmd "2" for mouse down, "4" for mouse up.
    
    # 1. 移动到起点并按下
    await send_action(app_state.sock, {"id": str(int(time.time() * 1000)), "op": "input", "data": {"cmd": f"1 {x1} {y1} 0"}})
    await asyncio.sleep(0.02)
    await send_action(app_state.sock, {"id": str(int(time.time() * 1000)), "op": "input", "data": {"cmd": f"2 {x1} {y1} 0"}})
    await asyncio.sleep(0.05)

    # 2. 模拟拖动过程 (一系列移动)
    steps = max(2, int(duration_ms / 20)) # 每20ms移动一次
    for i in range(1, steps + 1):
        progress = i / steps
        x = int(x1 + (x2 - x1) * progress)
        y = int(y1 + (y2 - y1) * progress)
        # 在拖动过程中，我们仍然发送移动指令
        await send_action(app_state.sock, {"id": str(int(time.time() * 1000)), "op": "input", "data": {"cmd": f"1 {x} {y} 0"}})
        await asyncio.sleep(duration_ms / 1000 / steps)

    # 3. 在终点松开
    await asyncio.sleep(0.02)
    await send_action(app_state.sock, {"id": str(int(time.time() * 1000)), "op": "input", "data": {"cmd": f"4 {x2} {y2} 0"}})
        
    return web.json_response({"status": "ok"})

async def handle_input(request: web.Request):
    if not app_state.is_ready or not app_state.sock:
        return web.json_response({"status": "error", "message": "Service not ready"}, status=503)

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

async def handle_start(request: web.Request):
    if app_state.cloud_game_task and not app_state.cloud_game_task.done():
        return web.json_response({"status": "error", "message": "Cloud game connection is already in progress or active."}, status=409)

    app_state.cloud_game_task = asyncio.create_task(run_cloud_game())
    return web.json_response({"status": "ok", "message": "Cloud game connection initiated."})

async def handle_exit(request: web.Request):
    if not app_state.cloud_game_task or app_state.cloud_game_task.done():
        return web.json_response({"status": "ok", "message": "No active cloud game connection to exit."})

    print(f"{Colors.CYAN}[*] Received exit request. Disconnecting from cloud game...{Colors.RESET}")
    
    app_state.cloud_game_task.cancel()
    try:
        await app_state.cloud_game_task
    except asyncio.CancelledError:
        pass # Expected
    
    return web.json_response({"status": "ok", "message": "Cloud game connection terminated."})

async def handle_root(request: web.Request):
    raise web.HTTPMovedPermanently('/info')

# --- 云游戏连接逻辑 ---
async def run_cloud_game():
    try:
        try:
            token = open(TOKEN_FILE).read().strip()
        except FileNotFoundError:
            token = ""
        if not token:
            pnum = input(f"{Colors.YELLOW}Input your phone number (will not be stored): {Colors.RESET}").strip()
            login("86-" + pnum)
            token = open(TOKEN_FILE).read().strip()
            if not token:
                print(f"{Colors.RED}Login failed, exiting task.{Colors.RESET}", file=sys.stderr)
                return

        app_state.token = token
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
            print(f"{Colors.GREEN}{Colors.BOLD}[✓] Cloud game ready. API is active.{Colors.RESET}")
        else:
            print(f"{Colors.RED}[!] Failed to start video stream. API will not be functional.{Colors.RESET}", file=sys.stderr)

        await asyncio.Event().wait()

    except asyncio.CancelledError:
        print(f"\n{Colors.CYAN}[*] Cloud game task cancelled.{Colors.RESET}")
    except Exception as e:
        print(f"\n{Colors.RED}[!] An error occurred in cloud game task: {e}{Colors.RESET}", file=sys.stderr)
        logging.exception("Cloud game task error")
    finally:
        print(f"{Colors.CYAN}[*] Cleaning up cloud game resources...{Colors.RESET}")
        
        token_to_use = app_state.token
        pc_to_close = app_state.pc
        sock_to_close = app_state.sock
        snapshotter_to_stop = app_state.snapshotter

        # 重置状态
        app_state.reset()

        if token_to_use:
            print(f"{Colors.CYAN}[*] Sending exit command to cloud game server...{Colors.RESET}")
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, exit_game, token_to_use)
        
        async def close_with_timeout(awaitable: Coroutine, timeout=5.0):
            try:
                await asyncio.wait_for(awaitable, timeout=timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            except Exception:
                pass

        if snapshotter_to_stop:
            await close_with_timeout(snapshotter_to_stop.stop())
        if pc_to_close and pc_to_close.connectionState != "closed":
            await close_with_timeout(pc_to_close.close())
        if sock_to_close and not sock_to_close.closed:
            await close_with_timeout(sock_to_close.close())
        
        print(f"{Colors.GREEN}[✓] Cloud game resources cleaned up.{Colors.RESET}")


async def cleanup_background_tasks(app: web.Application):
    if app_state.cloud_game_task and not app_state.cloud_game_task.done():
        print(f"\n{Colors.CYAN}Server shutting down. Cleaning up active cloud game connection...{Colors.RESET}")
        app_state.cloud_game_task.cancel()
        try:
            await app_state.cloud_game_task
        except asyncio.CancelledError:
            pass

# --- 主函数 ---
def main():
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s - %(levelname)s - %(message)s')
    access_log = logging.getLogger("aiohttp.access")#.setLevel(logging.WARNING)

    app = web.Application()
    app.add_routes([
        web.get('/', handle_root),
        web.get('/info', handle_info),
        web.get('/screencap', handle_screencap),
        web.post('/click', handle_click),
        web.post('/swipe', handle_swipe),
        web.post('/input', handle_input),
        web.post('/start', handle_start),
        web.post('/exit', handle_exit),
    ])
    
    app.on_cleanup.append(cleanup_background_tasks)
    
    print(f"{Colors.GREEN}{Colors.BOLD}[✓] API server is running at http://{HOST}:{PORT}{Colors.RESET}")
    print(f"{Colors.YELLOW}Send POST to /start to connect to the cloud game.{Colors.RESET}")
    
    web.run_app(app, host=HOST, port=PORT, access_log=access_log)

if __name__ == "__main__":
    main()