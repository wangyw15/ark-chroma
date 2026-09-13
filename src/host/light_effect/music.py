import asyncio
import colorsys
import io
import threading

import numpy as np
import soundcard as sc
from PIL import Image

from .base import BaseEffect


class Music(BaseEffect):
    """颜色取自专辑封面、亮度跟随系统音量，经 WebSocket 推送给 ESP32。"""

    SAMPLE_RATE = 44100
    BLOCK_SIZE = 1024  # 每帧采样数（约 23ms，决定亮度刷新率）

    DEFAULT_COLOR = (255, 255, 255)  # 无媒体播放时的默认颜色
    COVER_POLL_INTERVAL = 1.0  # 专辑封面轮询间隔（秒）
    COVER_SATURATION = 0.85  # 输出颜色的饱和度（灯光效果更好）

    def __init__(self, light_count: int, fps: int) -> None:
        self._light_count = light_count
        self._lock = threading.Lock()
        self._color = Music.DEFAULT_COLOR
        self._brightness = 0.5

        self._audio_thread: threading.Thread | None = None
        self._cover_thread: threading.Thread | None = None

    # ------------------------ 共享状态 ------------------------

    def set_color(self, rgb: tuple[int, int, int]) -> None:
        with self._lock:
            self._color = rgb

    def set_brightness(self, brightness: float) -> None:
        with self._lock:
            self._brightness = brightness

    def current_hex(self) -> str:
        """当前颜色按亮度缩放后的 #rrggbb。"""
        with self._lock:
            r, g, b = self._color
            k = self._brightness
        return f"#{int(r * k):02x}{int(g * k):02x}{int(b * k):02x}"

    # ===================== 专辑封面 -> 颜色 =====================

    async def _cover_loop_async(self) -> None:
        last_track = None
        while True:
            try:
                track = await self._get_current_track()
                if track is None:
                    if last_track is not None:
                        print("\n[*] 无媒体播放, 恢复默认颜色")
                        self.set_color(Music.DEFAULT_COLOR)
                        last_track = None
                else:
                    track_id, thumb = track
                    if track_id != last_track:
                        last_track = track_id
                        if thumb:
                            color = self.extract_color(thumb)
                            self.set_color(color)
                            print(f"\n[*] 正在播放: {track_id}  封面颜色: RGB{color}")
                        else:
                            self.set_color(Music.DEFAULT_COLOR)
                            print(f"\n[*] 正在播放: {track_id}  (无封面, 使用默认颜色)")
            except Exception as e:  # 媒体会话查询偶发失败不应中断服务
                print(f"\n[!] 获取媒体信息失败: {e}")
            await asyncio.sleep(Music.COVER_POLL_INTERVAL)

    def _cover_loop(self):
        asyncio.run(self._cover_loop_async())

    def extract_color(self, image_bytes: bytes) -> tuple[int, int, int]:
        """从封面图片提取主色调：优先取鲜艳（高饱和）像素的加权平均。"""
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB").resize((64, 64))
        pixels = np.asarray(img, dtype=np.float32).reshape(-1, 3) / 255.0

        mx = pixels.max(axis=1)
        mn = pixels.min(axis=1)
        sat = np.where(mx > 1e-6, (mx - mn) / np.maximum(mx, 1e-6), 0.0)

        vivid = (sat > 0.30) & (mx > 0.20)
        if vivid.sum() >= 16:
            # 鲜艳像素按饱和度加权平均
            weights = sat[vivid][:, None]
            avg = (pixels[vivid] * weights).sum(axis=0) / weights.sum()
        else:
            avg = pixels.mean(axis=0)

        # 统一饱和度，保留色相与明度，使灯光更通透
        h, _, v = colorsys.rgb_to_hsv(*avg)
        r, g, b = colorsys.hsv_to_rgb(h, Music.COVER_SATURATION, max(v, 0.35))
        return int(r * 255), int(g * 255), int(b * 255)

    async def _get_current_track(self):
        """返回 (曲目标识, 封面字节)；无播放会话时返回 None。"""
        from winsdk.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as MediaManager,
        )

        manager = await MediaManager.request_async()
        session = manager.get_current_session()
        if session is None:
            return None
        props = await session.try_get_media_properties_async()
        track_id = f"{props.artist} - {props.title}"
        thumb = await self._read_thumbnail(props.thumbnail) if props.thumbnail else None
        return track_id, thumb

    async def _read_thumbnail(self, thumb_ref) -> bytes | None:
        from winsdk.windows.storage.streams import Buffer, InputStreamOptions

        stream = await thumb_ref.open_read_async()
        try:
            size = stream.size
            if size == 0:
                return None
            buf = Buffer(size)
            await stream.read_async(buf, size, InputStreamOptions.NONE)
            return bytes(buf)
        finally:
            stream.close()

    # ======================== 音频 -> 亮度 ========================

    def _audio_loop(self) -> None:
        """阻塞式音频采集（在独立线程中运行），持续更新亮度。"""
        speaker = sc.default_speaker()
        mic = sc.get_microphone(speaker.name, include_loopback=True)
        print(f"[*] 音频回环设备: {mic.name}")
        tracker = BrightnessTracker()
        with mic.recorder(samplerate=Music.SAMPLE_RATE) as recorder:
            while True:
                data = recorder.record(numframes=Music.BLOCK_SIZE)
                brightness = tracker.update(np.asarray(data))
                self.set_brightness(brightness)
                r, g, b = self._color
                bar = "#" * int(brightness * 30)
                print(
                    f"\rRGB=({r:3d},{g:3d},{b:3d}) 亮度={brightness:.2f} |{bar:<30}|",
                    end="",
                    flush=True,
                )

    # ============================ 入口 ============================

    async def init(self):
        self._audio_thread = threading.Thread(target=self._audio_loop, daemon=True)
        self._cover_thread = threading.Thread(target=self._cover_loop, daemon=True)
        self._audio_thread.start()
        self._cover_thread.start()

    async def exit(self):
        if self._audio_thread:
            self._audio_thread.join()
        if self._cover_thread:
            self._cover_thread.join()

    async def loop(self):
        return self.current_hex()


class BrightnessTracker:
    """把波形振幅自适应映射为 0~1 亮度。

    用缓慢移动的"地板/天花板"估计近期音量范围，当前振幅在该范围内的
    相对位置即亮度——安静段落与高潮段落的内部起伏都能拉开对比。
    """

    # 亮度：波形振幅自适应归一（跟随近期音量动态范围）
    BRIGHT_GAMMA = 1.4  # >1 拉大强弱对比
    SMOOTH_ATTACK = 0.7  # 亮度上升平滑系数（越大越灵敏）
    SMOOTH_RELEASE = 0.2  # 亮度下降平滑系数（越小越柔和）

    def __init__(self) -> None:
        self.floor = 1e-3
        self.ceil = 2e-2
        self.value = 0.0

    def update(self, samples: np.ndarray) -> float:
        mono = samples.mean(axis=1) if samples.ndim > 1 else samples
        # 电平：波形峰值为主、RMS 为辅，突出节拍瞬态
        peak = float(np.abs(mono).max())
        rms = float(np.sqrt(np.mean(mono**2)))
        level = 0.7 * peak + 0.3 * rms

        # 地板：安静时快速下探，吵闹时缓慢抬升
        if level < self.floor:
            self.floor += (level - self.floor) * 0.3
        else:
            self.floor += (level - self.floor) * 0.002
        # 天花板：瞬间跟随大音量，随后缓慢回落
        if level > self.ceil:
            self.ceil += (level - self.ceil) * 0.5
        else:
            self.ceil += (level - self.ceil) * 0.001

        span = max(self.ceil - self.floor, 1e-4)
        target = float(np.clip((level - self.floor) / span, 0.0, 1.0))
        target = target**BrightnessTracker.BRIGHT_GAMMA  # 拉大强弱对比

        k = (
            BrightnessTracker.SMOOTH_ATTACK
            if target > self.value
            else BrightnessTracker.SMOOTH_RELEASE
        )
        self.value += (target - self.value) * k
        return self.value
