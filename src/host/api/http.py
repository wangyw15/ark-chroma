from typing import Literal, NotRequired, TypedDict

import httpx

# 与 esp32/main.py 中 Light.set_light 接受的参数一致：
# 单色 "#rrggbb" / {"ALL": "#rrggbb", "0": ...} / 每灯颜色列表
type T_LightColor = dict[str, str] | list[str] | str


class LightState(TypedDict):
    """GET /api/light 响应。"""

    gpio: int
    count: int
    color: list[str]  # 仅在请求参数 color 存在时非空


class AnimationState(TypedDict):
    """GET /api/animation 响应（POST 响应不含 available）。"""

    current: str
    param: dict
    available: NotRequired[list[str]]


class HTTPClient:
    """ESP32 HTTP API 客户端（路由定义见 esp32/main.py）。"""

    def __init__(
        self,
        host: str,
        port: int,
        protocal: Literal["http", "https"] = "http",
        reconnect_delay: float = 3.0,
    ) -> None:
        self.host = host
        self.port = port
        self.protocal = protocal
        self.reconnect_delay = reconnect_delay

        self._base_url = f"{protocal}://{host}:{port}"

    async def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        async with httpx.AsyncClient(base_url=self._base_url) as client:
            return await client.get(path, params=params)

    async def _post(self, path: str, body) -> httpx.Response:
        async with httpx.AsyncClient(base_url=self._base_url) as client:
            return await client.post(path, json=body)

    # ======================== /api/ping ========================

    async def ping(self) -> bool:
        """连通性检查，ESP32 正常响应 "pong" 时返回 True。"""
        resp = await self._get("/api/ping")
        return resp.status_code == 200 and resp.text == "pong"

    # ======================== /api/light ========================

    async def get_light(self, with_color: bool = False) -> LightState:
        """获取灯带状态；with_color=True 时返回每灯颜色列表。"""
        params = {"color": "1"} if with_color else None
        resp = await self._get("/api/light", params=params)
        resp.raise_for_status()
        return resp.json()

    async def set_light(self, color: T_LightColor) -> bool:
        """设置灯光颜色（会清除当前灯效），成功返回 True。"""
        resp = await self._post("/api/light", color)
        return resp.status_code == 200 and resp.text == "success"

    # ====================== /api/animation ======================

    async def get_animation(self) -> AnimationState:
        """获取当前灯效、参数及可用灯效列表。"""
        resp = await self._get("/api/animation")
        resp.raise_for_status()
        return resp.json()

    async def set_animation(
        self, effect: str, param: dict | None = None
    ) -> AnimationState:
        """切换灯效（effect="" 清除灯效），返回切换后的状态。"""
        resp = await self._post(
            "/api/animation", {"effect": effect, "param": param or {}}
        )
        resp.raise_for_status()
        return resp.json()
