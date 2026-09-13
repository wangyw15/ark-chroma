import colorsys
import socket
import struct
import threading
import time

from .base import BaseEffect


class ForzaHorizon(BaseEffect):
    """读取地平线 UDP 遥测，颜色随转速由绿转红。

    游戏内设置：HUD 与游戏性 -> 数据输出 IP 地址 = 本机 IP，数据输出端口 = UDP_PORT
    """

    UDP_HOST = "0.0.0.0"  # 遥测 UDP 监听地址
    UDP_PORT = 20777  # 遥测 UDP 端口，需与游戏内"数据输出端口"一致

    FIXED_BRIGHTNESS = 0.8  # 恒定亮度 0~1
    DEFAULT_COLOR = (255, 255, 255)  # 无遥测数据时的默认颜色
    DATA_TIMEOUT = 2.0  # 超过该时间未收到遥测则回退默认颜色

    LOW_RPM_HUE = 0.33  # 低转速色相（绿）
    # 高转速固定为红（hue=0.0），中间自然过渡为黄

    # Forza 数据输出包格式（FH4=324 字节 / FH5=312 字节，首部布局一致）
    OFF_IS_RACE_ON = 0  # s32
    OFF_ENGINE_MAX_RPM = 8  # f32
    OFF_ENGINE_IDLE_RPM = 12  # f32
    OFF_CURRENT_RPM = 16  # f32
    MIN_PACKET_SIZE = 20

    def __init__(self, light_count: int, fps: int) -> None:
        self._light_count = light_count
        self._lock = threading.Lock()
        self._rpm_ratio = 0.0
        self._last_data_time = 0.0

        self._telemetry_thread: threading.Thread | None = None

    # ------------------------ 共享状态 ------------------------

    def set_telemetry(self, ratio: float) -> None:
        with self._lock:
            self._rpm_ratio = ratio
            self._last_data_time = time.monotonic()

    def get_telemetry(self) -> tuple[float, bool]:
        """返回 (转速比, 数据是否新鲜)。"""
        with self._lock:
            fresh = (
                time.monotonic() - self._last_data_time
            ) < ForzaHorizon.DATA_TIMEOUT
            return self._rpm_ratio, fresh

    def current_hex(self) -> str:
        """当前转速比对应的颜色（按恒定亮度缩放）的 #rrggbb。"""
        ratio, fresh = self.get_telemetry()
        if fresh:
            hue = ForzaHorizon.LOW_RPM_HUE * (1.0 - ratio)  # 绿 -> 黄 -> 红
            r, g, b = colorsys.hsv_to_rgb(hue, 1.0, ForzaHorizon.FIXED_BRIGHTNESS)
        else:
            k = ForzaHorizon.FIXED_BRIGHTNESS
            r, g, b = (c / 255.0 * k for c in ForzaHorizon.DEFAULT_COLOR)
        return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"

    # ======================== 遥测接收 ========================

    def _telemetry_loop(self) -> None:
        """阻塞式接收地平线 UDP 遥测包（在独立线程中运行），解析转速比。"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((ForzaHorizon.UDP_HOST, ForzaHorizon.UDP_PORT))
        print(
            f"[*] 等待地平线遥测数据 udp://{ForzaHorizon.UDP_HOST}:{ForzaHorizon.UDP_PORT} ..."
        )
        last_print = 0.0
        while True:
            data, _ = sock.recvfrom(512)
            if len(data) < ForzaHorizon.MIN_PACKET_SIZE:
                continue
            is_race_on = struct.unpack_from("<i", data, ForzaHorizon.OFF_IS_RACE_ON)[0]
            if not is_race_on:
                continue
            max_rpm = struct.unpack_from("<f", data, ForzaHorizon.OFF_ENGINE_MAX_RPM)[0]
            idle_rpm = struct.unpack_from("<f", data, ForzaHorizon.OFF_ENGINE_IDLE_RPM)[
                0
            ]
            cur_rpm = struct.unpack_from("<f", data, ForzaHorizon.OFF_CURRENT_RPM)[0]
            span = max_rpm - idle_rpm
            if span <= 0:
                continue
            ratio = min(max((cur_rpm - idle_rpm) / span, 0.0), 1.0)
            self.set_telemetry(ratio)

            now = time.monotonic()
            if now - last_print > 0.1:  # 10Hz 打印，避免刷屏
                last_print = now
                bar = "#" * int(ratio * 30)
                print(
                    f"\r[遥测中] 转速比={ratio:.2f} |{bar:<30}| {self.current_hex()}   ",
                    end="",
                    flush=True,
                )

    # ============================ 入口 ============================

    async def init(self):
        self._telemetry_thread = threading.Thread(
            target=self._telemetry_loop, daemon=True
        )
        self._telemetry_thread.start()

    async def exit(self):
        if self._telemetry_thread:
            self._telemetry_thread.join()

    async def loop(self):
        return self.current_hex()
