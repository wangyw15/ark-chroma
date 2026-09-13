import asyncio
import json
from typing import Awaitable, Callable, Literal

from websockets import ClientConnection, WebSocketException
from websockets.asyncio.client import connect as ws_connect

type T_LightColor = dict[str, str] | list[str] | str
type T_Frame = Callable[[], Awaitable[T_LightColor]]


class WebSocketClient:
    def __init__(
        self,
        host: str,
        port: int,
        protocal: Literal["ws", "wss"] = "ws",
        send_fps: int = 30,
        reconnect_delay: float = 3.0,
    ) -> None:
        self.host = host
        self.port = port
        self.protocal = protocal
        self.send_fps = send_fps
        self.reconnect_delay = reconnect_delay

        self._last_sent: str = ""

    async def _send_loop(self, ws: ClientConnection, get_frame: T_Frame) -> None:
        """向 ESP32 持续推送最新帧（内容变化时才发送）。"""
        while True:
            frame = await get_frame()
            frame = {
                "action": "light",
                "param": frame,
            }
            frame = json.dumps(frame)

            if frame != self._last_sent:
                await ws.send(frame)
                self._last_sent = frame

            await asyncio.sleep(1.0 / self.send_fps)

    async def run(self, get_frame: T_Frame) -> None:
        """WebSocket 客户端主循环，断线自动重连。

        get_frame：无参回调，返回当前帧 "#rrggbb"（亮度通过缩放 RGB 体现）。
        """
        ws_url = f"{self.protocal}://{self.host}:{self.port}/api/ws"
        while True:
            try:
                print(f"[*] 连接 ESP32: {ws_url}")
                async with ws_connect(ws_url) as ws:
                    print("[+] ESP32 已连接")
                    self._last_sent = ""
                    await self._send_loop(ws, get_frame)
            except (OSError, WebSocketException) as e:
                print(f"\n[-] 连接断开: {e}, {self.reconnect_delay}s 后重连...")
                await asyncio.sleep(self.reconnect_delay)
