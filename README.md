# NetEase Cloud Game API Server for MAA

![Python](https://img.shields.io/badge/Python-3.7%2B-blue.svg?logo=python&logoColor=white)
![License](https://img.shields.io/badge/license-Apache%202.0-green.svg)
![Framework](https://img.shields.io/badge/Framework-AIOHTTP-red.svg)

本项目基于 [wupco/netease_cloud_game_sdk](https://github.com/wupco/netease_cloud_game_sdk) 开发，旨在为 [MAA (明日方舟-MAA)](https://github.com/MaaAssistantArknights/MaaAssistantArknights) 提供一个稳定、高效的网易云游戏HTTP接口服务。通过此服务，MAA可以将操作指令发送到云端游戏，实现云端自动化代理。

---

## ✨ 功能特性

-   **持久化 HTTP 服务**: API 服务器持续运行，不因云游戏断开而终止。
-   **按需连接**: 通过 API (`/start`) 按需启动和连接云游戏。
-   **屏幕截图**: 获取实时游戏画面。
-   **模拟输入**: 支持点击、滑动和文字输入。
-   **后台运行**: 作为后台服务持续运行，可随时发起或断开云游戏连接。

---

## 🚀 快速开始

### 1. 环境准备

-   Python 3.7+
-   Git

### 2. 克隆项目

由于项目使用了 git submodule 来包含 SDK，克隆时请使用 `--recurse-submodules` 参数。

```bash
git clone --recurse-submodules https://github.com/Tokisaki-Galaxy/netease_cloud_game_server.git
cd netease_cloud_game_server
```

### 3. 安装依赖

依赖项在 `sdk` 目录的 `requirements.txt` 文件中。

```bash
pip install -r sdk/requirements.txt
```
> **注意**: `aiortc` 的依赖可能需要在您的系统上安装额外的编译工具。同时，建议安装 `Pillow` 以支持截图功能：`pip install Pillow`。

### 4. 运行 API 服务

直接运行 `server.py` 即可启动 API 服务。此时服务已在运行，但尚未连接到云游戏。

```bash
python server.py
```

服务成功启动后，您会看到类似以下的输出：

```
[✓] API server is running at http://127.0.0.1:22888
Send POST to /start to connect to the cloud game.
```

### 5. 连接到云游戏

向 `/start` 接口发送一个 POST 请求来启动云游戏连接。

```bash
curl -X POST http://127.0.0.1:22888/start
```

**首次运行**时，如果根目录没有 `token` 文件，程序会在**运行 `server.py` 的终端**中提示您输入手机号以获取登录 `token`。该 `token` 会被保存在 `token` 文件中，后续启动将自动读取。

连接成功后，您会在终端看到类似以下的输出：

```
[*] Connecting to cloud gaming service...
[*] Video track received.
[*] Waiting for video stream...
[✓] Cloud game ready. API is active.
```

现在，您可以开始使用其他 API 接口了。

---

## 🎮 API 接口文档

服务运行在 `http://127.0.0.1:22888`。

### `POST /start`

启动并连接到网易云游戏服务。

-   **成功响应**:
    ```json
    {
      "status": "ok",
      "message": "Cloud game connection initiated."
    }
    ```
-   **调用示例**:
    ```bash
    curl -X POST http://127.0.0.1:22888/start
    ```

### `GET /info`

获取云游戏服务的当前状态和屏幕分辨率。

-   **响应状态**:
    -   `disconnected`: 未连接到云游戏。
    -   `connecting`: 正在连接中。
    -   `ok`: 已成功连接，可以进行操作。
-   **成功连接响应**:
    ```json
    {
      "status": "ok",
      "width": 1280,
      "height": 720
    }
    ```
-   **未连接响应**:
    ```json
    {
      "status": "disconnected",
      "message": "Cloud gaming service not ready or not connected."
    }
    ```
-   **调用示例**:
    ```bash
    curl http://127.0.0.1:22888/info
    ```

### `GET /screencap`

获取当前游戏画面的截图。**(需要先成功 `/start`)**

-   **成功响应**: 返回 `image/jpeg` 格式的图片二进制数据。
-   **调用示例**:
    ```bash
    curl -o screenshot.jpg http://127.0.0.1:22888/screencap
    ```

### `POST /click`

在指定坐标进行点击。**(需要先成功 `/start`)**

-   **请求体** (`application/json`):
    ```json
    {
      "x": 640,
      "y": 360
    }
    ```
-   **调用示例**:
    ```bash
    curl -X POST -H "Content-Type: application/json" -d '{"x": 640, "y": 360}' http://127.0.0.1:22888/click
    ```

### `POST /swipe`

模拟一次滑动操作。**(需要先成功 `/start`)**

-   **请求体** (`application/json`):
    ```json
    {
      "x1": 100,
      "y1": 200,
      "x2": 800,
      "y2": 200,
      "duration": 500
    }
    ```
-   **调用示例**:
    ```bash
    curl -X POST -H "Content-Type: application/json" -d '{"x1":100,"y1":200,"x2":800,"y2":200,"duration":500}' http://127.0.0.1:22888/swipe
    ```

### `POST /input`

输入一段文本（逐字输入）。**(需要先成功 `/start`)**

-   **请求体** (`application/json`):
    ```json
    {
      "text": "arknights"
    }
    ```
-   **调用示例**:
    ```bash
    curl -X POST -H "Content-Type: application/json" -d '{"text": "arknights"}' http://127.0.0.1:22888/input
    ```

### `POST /exit`

断开与云游戏服务器的连接，并释放相关资源。**此操作不会关闭 API 服务本身**。

-   **成功响应**:
    ```json
    {
      "status": "ok",
      "message": "Cloud game connection terminated."
    }
    ```
-   **调用示例**:
    ```bash
    curl -X POST http://127.0.0.1:22888/exit
    ```

---

## ⚙️ 配置

你可以在 [`server.py`](server.py) 文件的开头修改配置项：

filepath: server.py
```python
# --- 配置 ---
GAME_CODE = "mrfz"
TOKEN_FILE = "token"
HOST = "127.0.0.1"
PORT = 22888
WIDTH = 1280
HEIGHT = 720
```

-   `GAME_CODE`: 游戏代码，`mrfz` 代表《明日方舟》。
-   `TOKEN_FILE`: 保存登录凭证的文件路径。
-   `HOST` / `PORT`: API 服务的监听地址和端口。
-   `WIDTH` / `HEIGHT`: 请求的云游戏分辨率。

## 🙏 致谢

-   **[wupco/netease_cloud_game_sdk](https://github.com/wupco/netease_cloud_game_sdk)**: 提供了核心的云游戏连接能力。
-   **[aiohttp](https://github.com/aio-libs/aiohttp)**: 用于构建异步 HTTP 服务器。
-   **[aiortc](https://github.com/aiortc/aiortc)**: 提供了 WebRTC 功能。