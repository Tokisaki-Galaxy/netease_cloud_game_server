import asyncio
import json
import time
import sys
import signal
import platform
import logging
import base64
from typing import Optional, Coroutine
try:
    from websockets.legacy.client import WebSocketClientProtocol
except ImportError:  # websockets >=12 removed legacy package
    from websockets.client import WebSocketClientProtocol  # type: ignore[attr-defined]
from io import BytesIO

import requests
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


# --- API 解码工具 ---
def decode_api_response(encoded_data: str) -> dict:
    """Decode base64 encoded API response from cg.163.com"""
    try:
        raw_bytes = base64.b64decode(encoded_data)
    except Exception:
        return {}

    # Find the decryption key by detecting JSON start pattern
    decode_key = None
    if len(raw_bytes) >= 2:
        first_byte = raw_bytes[0]
        second_byte = raw_bytes[1]
        for key_candidate in range(256):
            char1 = chr((first_byte - key_candidate) % 256)
            char2 = chr((second_byte - key_candidate) % 256)
            if char1 == "{" and char2 == "\"":
                decode_key = key_candidate
                break

    if decode_key is None:
        return {}

    decoded_str = "".join(chr((b - decode_key) % 256) for b in raw_bytes)
    try:
        return json.loads(decoded_str)
    except json.JSONDecodeError:
        return {}


def fetch_user_info(token: str) -> dict:
    """Fetch user info including remaining game time from cg.163.com API"""
    try:
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(
            "https://n.cg.163.com/api/v2/users/@me",
            headers=headers,
            timeout=10
        )
        if response.status_code != 200:
            return {}
        return decode_api_response(response.text)
    except Exception as e:
        logging.warning(f"Failed to fetch user info: {e}")
        return {}


# --- 全局状态 ---
class AppState:
    def __init__(self):
        self.pc: Optional[RTCPeerConnection] = None
        self.sock: Optional[WebSocketClientProtocol] = None
        self.snapshotter: Optional['VideoSnapshotter'] = None
        self.is_ready = False
        self.width = WIDTH
        self.height = HEIGHT
        self.token: Optional[str] = None
        self.cloud_game_task: Optional[asyncio.Task] = None
        self.shutdown_event: Optional[asyncio.Event] = None

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
        self._last_recv_ts: float = 0.0

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
                self._last_recv_ts = time.time()
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
        if self._last_recv_ts and time.time() - self._last_recv_ts > 10:
            self._last_frame = None
            if app_state.is_ready:
                app_state.is_ready = False
                logging.warning("Video stream stale for over 10s; marking service as not ready")
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

    # Fetch remaining game time from API
    remaining_time_seconds = None
    if app_state.token:
        loop = asyncio.get_running_loop()
        user_info = await loop.run_in_executor(None, fetch_user_info, app_state.token)
        if user_info:
            # free_time_left is remaining mobile game time in seconds
            remaining_time_seconds = user_info.get("free_time_left")

    response_data = {
        "status": "ok",
        "width": app_state.width,
        "height": app_state.height
    }

    if remaining_time_seconds is not None:
        response_data["remaining_time"] = remaining_time_seconds

    return web.json_response(response_data)

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
        start_x, start_y = int(data['x1']), int(data['y1'])
        end_x, end_y = int(data['x2']), int(data['y2'])
        swipe_duration = int(data['duration'])
    except (json.JSONDecodeError, KeyError, ValueError):
        return web.json_response({"status": "error", "message": "Invalid request body"}, status=400)

    logging.warning(f"Action: Swipe from ({start_x}, {start_y}) to ({end_x}, {end_y}) in {swipe_duration}ms")

    # Cloud game touch protocol:
    # - Event codes: down=1, move=2, up=3
    # - Coordinates must be normalized to 0-65535 range
    # - Formula: normalized = ceil(65535 * pixel / dimension)

    def normalize_coord(pixel_val: int, dimension: int) -> int:
        """Normalize pixel coordinate to 0-65535 range"""
        clamped = max(0, min(pixel_val, dimension))
        return int((65535 * clamped) // dimension)

    # Get current screen dimensions
    screen_width = app_state.width
    screen_height = app_state.height

    num_points = max(5, swipe_duration // 30)
    interval = swipe_duration / 1000.0 / num_points
    touch_id = 0

    def create_touch_cmd(evt_type: int, px: int, py: int, tid: int) -> dict:
        """Create touch input command with normalized coordinates"""
        norm_x = normalize_coord(px, screen_width)
        norm_y = normalize_coord(py, screen_height)
        timestamp = str(int(time.time() * 1000))
        cmd_str = f"{evt_type} {norm_x} {norm_y} {tid}"
        return {"id": timestamp, "op": "input", "data": {"cmd": cmd_str}}

    # Touch down at starting point
    down_cmd = create_touch_cmd(1, start_x, start_y, touch_id)
    await send_action(app_state.sock, down_cmd)
    await asyncio.sleep(0.02)

    # Move through path points
    delta_x = end_x - start_x
    delta_y = end_y - start_y
    for step in range(1, num_points + 1):
        progress = step / num_points
        current_x = int(start_x + delta_x * progress)
        current_y = int(start_y + delta_y * progress)

        move_cmd = create_touch_cmd(2, current_x, current_y, touch_id)
        await send_action(app_state.sock, move_cmd)
        await asyncio.sleep(interval)

    # Touch up at ending point
    up_cmd = create_touch_cmd(3, end_x, end_y, touch_id)
    await send_action(app_state.sock, up_cmd)

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
        return web.json_response({"status": "ok", "message": "Cloud game connection already active."})

    if app_state.cloud_game_task and app_state.cloud_game_task.done():
        app_state.cloud_game_task = None

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
        if not isinstance(remote, RTCSessionDescription):
            raise RuntimeError(f"Unexpected offer type from signaling: {type(remote)!r}")
        
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
        if answer is None:
            raise RuntimeError("Failed to create SDP answer")
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

        # Use a cancellable wait instead of Event().wait()
        # This allows the task to be properly cancelled by signals
        while True:
            await asyncio.sleep(1)

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
            await loop.run_in_executor(None, exit_game, token_to_use, GAME_CODE)
        
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
        if sock_to_close and sock_to_close.close_code is None:
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
async def run_server():
    """Run the aiohttp server with proper signal handling."""
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s - %(levelname)s - %(message)s')
    access_log = logging.getLogger("aiohttp.access")

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
    
    runner = web.AppRunner(app, access_log=access_log)
    await runner.setup()
    
    site = web.TCPSite(runner, HOST, PORT)
    await site.start()
    
    print(f"{Colors.GREEN}{Colors.BOLD}[✓] API server is running at http://{HOST}:{PORT}{Colors.RESET}")
    print(f"{Colors.YELLOW}Send POST to /start to connect to the cloud game.{Colors.RESET}")
    print(f"{Colors.YELLOW}(Press CTRL+C to quit){Colors.RESET}")
    
    # Create shutdown event
    shutdown_event = asyncio.Event()
    
    # Setup signal handlers
    loop = asyncio.get_running_loop()
    
    def signal_handler():
        print(f"\n{Colors.CYAN}[*] Shutdown signal received...{Colors.RESET}")
        shutdown_event.set()
    
    # Register signal handlers
    # Note: loop.add_signal_handler() is not supported on Windows event loops,
    # so the KeyboardInterrupt fallback in main() is needed for Windows support
    if platform.system() != "Windows":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, signal_handler)
    
    try:
        # Wait for shutdown signal
        await shutdown_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        print(f"{Colors.CYAN}[*] Shutting down server...{Colors.RESET}")
        # Cleanup cloud game task if running
        if app_state.cloud_game_task and not app_state.cloud_game_task.done():
            app_state.cloud_game_task.cancel()
            try:
                await app_state.cloud_game_task
            except asyncio.CancelledError:
                pass
        await runner.cleanup()
        print(f"{Colors.GREEN}[✓] Server stopped.{Colors.RESET}")

def main():
    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        # This handles Ctrl+C on Windows
        print(f"\n{Colors.GREEN}[✓] Server stopped by user.{Colors.RESET}")

if __name__ == "__main__":
    main()
