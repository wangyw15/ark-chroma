import asyncio
import json
import math
import time

import aioble
import bluetooth
import machine
import network
from machine import Pin
from neopixel import NeoPixel

from microdot import Microdot, Request
from microdot.websocket import with_websocket, WebSocket


class WebServer:
    app = Microdot()

    @staticmethod
    @app.route("/")
    def index(request: Request):
        with open("light_control.html", encoding="utf-8") as f:
            return f.read(), 200, {"Content-Type": "text/html"}

    @staticmethod
    @app.route("/api/ping")
    def ping(request: Request):
        return "pong", 200

    @staticmethod
    @app.route("/api/light", methods=["GET"])
    def get_light(request: Request):
        with_color = request.args.get("color", None)

        if with_color:
            lights = [Util.rgb_to_hex(Light.np[i]) for i in range(Light.count)]
        else:
            lights = []

        return {
            "gpio": Light.gpio,
            "count": Light.count,
            "color": lights,
        }

    @staticmethod
    @app.route("/api/light", methods=["POST"])
    def set_light(request: Request):
        if body := request.json:
            Light.set_light(body)
            return "success"
        else:
            return 400

    @staticmethod
    @app.route("/api/animation", methods=["GET"])
    def get_animation(request: Request):
        return {
            "current": Light.effect_name,
            "param": Light.effect_param,
            "available": list(Light.effect_available.keys()),
        }

    @staticmethod
    @app.route("/api/animation", methods=["POST"])
    def set_animation(request: Request):
        body = request.json
        if not isinstance(body, dict) or "effect" not in body:
            return 400

        name = body["effect"]
        if name != "" and name not in Light.effect_available:
            return 400

        # 重置旧灯效的内部状态（约定灯效对象可实现 reset()）
        if Light.effect_instance is not None and hasattr(
            Light.effect_instance, "reset"
        ):
            Light.effect_instance.reset()

        Light.set_effect(name, body.get("param", {}))
        return {"current": Light.effect_name, "param": Light.effect_param}

    @staticmethod
    @app.route("/api/ws")
    @with_websocket
    async def websocket(request: Request, ws: WebSocket):
        while True:
            message = await ws.receive()
            body: dict = json.loads(message)
            action = body.get("action", "")

            if action == "light":
                if param := body.get("param", {}):
                    Light.set_light(param)
            elif action == "exit":
                break

        await ws.send('{ "success": true, "message": "goodbye" }')


# prebuilt light effect
# 约定：灯效类实现 __call__，每调用一次渲染一帧并推进内部状态，返回下一帧的
# 间隔秒数；可选实现 reset() 供切换灯效时重置状态；内部状态保存在实例属性上。
class Wave:
    """余弦波浪跑马灯：亮区沿灯带环形移动。"""

    FRAME_DELAY = 0.03  # 每帧延时（秒）

    def __init__(
        self,
        color: str = "",
        max_brightness: float = 1.0,
        min_brightness: float = 0.05,
        step: float = 0.15,
    ):
        self.pos = 0.0  # 亮区中心位置
        self.max_brightness = max_brightness
        self.min_brightness = min_brightness
        self.step = step

        if not color:
            color = Light.default_color
        self.color = Util.hex_to_rgb(color)

    def reset(self):
        self.pos = 0.0

    def __call__(
        self,
    ):
        for i in range(Light.count):
            d = abs(i - self.pos)
            d = min(d, Light.count - d)  # 环形距离
            w = (1 + math.cos(math.pi * d / (Light.count / 2))) / 2
            factor = (
                self.min_brightness + (self.max_brightness - self.min_brightness) * w
            )
            Light.np[i] = (
                int(self.color[0] * factor),
                int(self.color[1] * factor),
                int(self.color[2] * factor),
            )
        Light.np.write()

        self.pos = (self.pos + self.step) % Light.count
        return self.FRAME_DELAY


class ArkRadarDetect:
    """雷达探测灯效：瞬间全亮后线性渐暗至熄灭，长暗 1.5s、短暗 0.2s 交替，
    每轮结束后间隔 0.2s 再进入下一轮。"""

    FRAME_DELAY = 0.02  # 每帧延时（秒）
    FADE_LONG = 1500    # 长渐暗时长（毫秒）
    FADE_SHORT = 200    # 短渐暗时长（毫秒）
    GAP = 200           # 两轮之间的全暗间隔（毫秒）

    def __init__(self, color: str = ""):
        if not color:
            color = Light.default_color
        self.color = Util.hex_to_rgb(color)
        self.start = time.ticks_ms()  # 周期起点

    def reset(self):
        self.start = time.ticks_ms()

    def __call__(self):
        cycle = self.FADE_LONG + self.FADE_SHORT + self.GAP
        t = time.ticks_diff(time.ticks_ms(), self.start) % cycle

        if t < self.FADE_LONG:
            factor = 1 - t / self.FADE_LONG
        elif t < self.FADE_LONG + self.FADE_SHORT:
            factor = 1 - (t - self.FADE_LONG) / self.FADE_SHORT
        else:
            factor = 0.0  # 间隔期保持熄灭

        r, g, b = self.color
        pixel = (int(r * factor), int(g * factor), int(b * factor))
        for i in range(Light.count):
            Light.np[i] = pixel
        Light.np.write()

        return self.FRAME_DELAY


class Light:
    # default for onboard light
    gpio = 48
    count = 1
    pin = Pin(gpio, Pin.OUT)
    np = NeoPixel(pin, count)

    default_color = "ffffff"

    effect_name = ""
    effect_instance = None
    effect_param = {}
    effect_available = {"wave": Wave, "arkradardetect": ArkRadarDetect}

    @staticmethod
    def init(gpio: int, count: int):
        Light.gpio = gpio
        Light.count = count
        Light.pin = Pin(Light.gpio, Pin.OUT)
        Light.np = NeoPixel(Light.pin, Light.count)

    @staticmethod
    async def loop():
        while True:
            if Light.effect_instance is None:
                await asyncio.sleep(0.1)
            else:
                delay = Light.effect_instance()
                await asyncio.sleep(delay if delay else 0.03)

    @staticmethod
    def set_light(color: str | dict[str, str] | list[str]):
        Light.set_effect("")

        if isinstance(color, str):
            for i in range(Light.count):
                Light.np[i] = Util.hex_to_rgb(color)

        if isinstance(color, dict):
            if "ALL" in color:
                for i in range(Light.count):
                    Light.np[i] = Util.hex_to_rgb(color["ALL"])

            for light, color in color.items():
                if light == "ALL":
                    continue
                Light.np[int(light)] = Util.hex_to_rgb(color)

        elif isinstance(color, list):
            for light, color in enumerate(color):
                Light.np[light] = Util.hex_to_rgb(color)

        Light.np.write()

    @staticmethod
    def set_effect(effect: str, param: dict | None = None):
        if effect == "":
            Light.effect_name = ""
            Light.effect_param = {}
            Light.effect_instance = None
            return

        if effect not in Light.effect_available:
            raise ValueError(f"Unknown light effect: {effect}")

        Light.effect_name = effect
        Light.effect_param = param or {}
        Light.effect_instance = Light.effect_available[effect](**Light.effect_param)


class Util:
    @staticmethod
    def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
        hex_color = hex_color.lstrip("#")
        rgb_color = tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
        return (rgb_color[0], rgb_color[1], rgb_color[2])

    @staticmethod
    def rgb_to_hex(rgb_color: tuple[int, int, int]) -> str:
        return f"#{rgb_color[0]:02x}{rgb_color[1]:02x}{rgb_color[2]:02x}"


class Network:
    wifi_max_retry = 5

    @staticmethod
    def prepare_wifi(mode: int, ssid: str, password: str):
        if mode not in [network.STA_IF, network.AP_IF]:
            raise ValueError(f"Unsupported WiFi mode: {mode}")

        wlan = network.WLAN(mode)
        if mode == network.STA_IF:
            for attempt in range(Network.wifi_max_retry):
                if wlan.isconnected():
                    break
                try:
                    # 先复位接口状态，避免软复位后残留状态导致 Internal State Error
                    wlan.active(False)
                    time.sleep(1)
                    wlan.active(True)
                    time.sleep(1)

                    print(f"Connecting WiFi: {ssid}")
                    wlan.connect(ssid, password)
                    for _ in range(40):  # 最长等待 20 秒
                        if wlan.isconnected():
                            print("WiFi connected, IP:", wlan.ifconfig()[0])
                            return
                        time.sleep(0.5)
                except OSError:
                    pass
                print(f"Connection error, retrying (%d/5)... {attempt + 1}")
                time.sleep(2)

            if wlan.isconnected():
                print("WiFi connected, IP:", wlan.ifconfig()[0])
            else:
                print("WiFi connection failed, resetting...")
                machine.reset()

        elif mode == network.AP_IF:
            wlan.active(False)
            time.sleep(1)
            wlan.active(True)
            time.sleep(1)

            print("Configuring access point...")

            # 配置并创建热点
            wlan.config(
                essid=ssid,
                password=password,
                authmode=network.AUTH_WPA_WPA2_PSK,
            )

            # 获取 AP 的 IP 地址
            ip = wlan.ifconfig()[0]
            print(f"AP IP address: {ip}")
            print("AP started")


class ArkRadar:
    enable = True

    ARKRADAR_PREFIX = "DEPRTS"
    SIGNAL_THRESHOLD = -42

    SCAN_DURATION = 2  # 每轮扫描时长（秒）
    SCAN_INTERVAL = 1  # 两轮扫描之间的间隔（秒）
    LOST_ROUNDS = 2    # 连续多少轮未检测到才判定为离开，避免灯光闪烁

    _triggered = False  # 当前灯效是否由雷达触发
    _missed = 0         # 连续未检测到的轮数
    _previous_effect = ""
    _previous_param = {}

    @staticmethod
    def _match(result) -> bool:
        """匹配名称前缀且信号强度达标"""
        name = result.name()
        return (
            name is not None
            and name.startswith(ArkRadar.ARKRADAR_PREFIX)
            and result.rssi >= ArkRadar.SIGNAL_THRESHOLD
        )

    @staticmethod
    async def loop():
        while ArkRadar.enable:
            found = None
            async with aioble.scan(
                duration_ms=ArkRadar.SCAN_DURATION * 1000,
                interval_us=30000,
                window_us=30000,
                active=True,
            ) as scanner:
                async for result in scanner:
                    if ArkRadar._match(result):
                        found = result

            if found is not None:
                ArkRadar._missed = 0
                if not ArkRadar._triggered:
                    print(f"ArkRadar detected: {found.name()}, RSSI: {found.rssi}")
                    ArkRadar._previous_effect = Light.effect_name
                    ArkRadar._previous_param = Light.effect_param
                    Light.set_effect("arkradardetect")
                    ArkRadar._triggered = True
            else:
                ArkRadar._missed += 1
                if ArkRadar._triggered and ArkRadar._missed >= ArkRadar.LOST_ROUNDS:
                    print("ArkRadar lost")
                    Light.set_effect(ArkRadar._previous_effect, ArkRadar._previous_param)
                    ArkRadar._triggered = False

            await asyncio.sleep(ArkRadar.SCAN_INTERVAL)


def main():
    with open("config.json", "r", encoding="utf-8") as f:
        config = json.load(f)

    Light.default_color = config["light"]["default_color"]
    Light.init(config["light"]["gpio"], config["light"]["count"])

    # 设置默认灯光颜色
    color = Util.hex_to_rgb(Light.default_color)
    for i in range(Light.count):
        Light.np[i] = color
    Light.np.write()

    wifi_mode = network.STA_IF
    if config["wifi"]["mode"] == "sta":
        wifi_mode = network.STA_IF
    elif config["wifi"]["mode"] == "ap":
        wifi_mode = network.AP_IF
    else:
        raise ValueError(f"Unknown WiFi mode: {wifi_mode}")

    Network.prepare_wifi(wifi_mode, config["wifi"]["ssid"], config["wifi"]["password"])

    http_port = config["http"]["port"]
    print(f"Server listening on {http_port}")

    loop = asyncio.get_event_loop()
    loop.create_task(WebServer.app.start_server(port=http_port))
    loop.create_task(Light.loop())
    loop.create_task(ArkRadar.loop())
    loop.run_forever()


main()
