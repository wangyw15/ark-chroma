# ArkChroma - 明日方舟通行证底座

软硬件结合项目：ESP32 驱动 WS2812 灯带（MicroPython 固件）

Windows 上位机采集系统状态并实时推送灯光效果。

## 目录结构

```
src/
├── esp32/                   # ESP32 固件（MicroPython + microdot）
│   ├── main.py              #   Web 服务器、灯效、WiFi、BLE 雷达
│   └── config.example.json  #   设备配置（WiFi / http 端口 / 灯带 gpio、count）示例
├── host/                    # 上位机（Python 3.13，包名 host）
│   ├── main.py              #   入口：选择灯效 -> init -> ws.run(effect.loop)
│   ├── config.py            #   Config TypedDict（对应仓库根目录 config.json）
│   ├── api/                 #   ESP32 通信客户端
│   │   ├── http.py          #     HTTPClient：/api/ping、/api/light、/api/animation
│   │   └── ws.py            #     WebSocketClient：/api/ws 持续推帧（去重 + 自动重连）
│   └── light_effect/        #   灯效类
│       ├── base.py          #     BaseAnimation 抽象基类
│       ├── __init__.py      #     包含 AVAILABLE_EFFECTS 注册表
│       └── ...              #     现有灯效见下方说明
├── config.example.json      # 上位机运行配置示例
└── pyproject.toml           # uv 管理；dev 组含 micropython-esp32-stubs 供固件补全
```

## ESP32 端（src/esp32）

- 固件：MicroPython；Web 框架：microdot（`WebServer` 持有全部路由）
- HTTP 路由（端口见设备 `config.json` 的 `http.port`，默认 80）：
  - `GET  /api/ping` → `"pong"`
  - `GET  /api/light[?color=1]` → `{gpio, count, color[]}`（带 `color` 参数时返回每灯颜色）
  - `POST /api/light` → body 为颜色（见下方 `T_LightColor`），设置颜色并清除灯效
  - `GET  /api/animation` → `{current, param, available}`
  - `POST /api/animation` → `{effect, param}`，`effect=""` 清除灯效
- WebSocket：`/api/ws`，接收 JSON 帧 `{"action": "light", "param": <颜色>}`；
  `{"action": "exit"}` 断开。灯带实时数据走 WebSocket，状态查询/灯效切换走 HTTP
- 设备端灯效约定（`Light.effect_available`）：类实现 `__call__` 渲染一帧并返回下一帧
  间隔秒数，可选 `reset()`；`ArkRadar` 通过 BLE 扫描 `DEPRTS` 前缀信标自动触发
  `arkradardetect` 灯效

## 上位机端（src/host）

### 颜色类型约定

`T_LightColor = str | dict[str, str] | list[str]`（定义于 `api/ws.py`、`api/http.py`）：

- `"#rrggbb"`：全灯带同色
- `{"ALL": "#rrggbb", "0": "#ff0000"}`：ALL 铺底 + 按灯珠序号覆盖
- `["#rrggbb", ...]`：逐灯珠颜色列表

**亮度通过缩放 RGB 体现**（ESP32 协议无亮度通道），如 `current_hex()` 返回
`int(channel * brightness)` 后的 hex。

### 灯效开发约定（light_effect）

新增灯效步骤：

1. 在 `light_effect/` 新建模块，类继承 `BaseAnimation`：

   ```python
   class MyEffect(BaseAnimation):
       def __init__(self, light_count: int, fps: int) -> None: ...
       async def init(self): ...    # 启动后台采集线程/任务
       async def exit(self): ...    # 收尾（join 线程等）
       async def loop(self): ...    # 返回当前帧 T_LightColor，由 WebSocketClient 按 fps 轮询
   ```

2. 在 `light_effect/__init__.py` 的 `AVAILABLE_EFFECTS` 注册（`Effect(name, description, class_type)`）。

- 阻塞式采集（音频、UDP）放独立 daemon 线程，共享状态用 `threading.Lock` 保护
- 配置项写成类常量（参考 `Music` / `ForzaHorizon`）
- `WebSocketClient.run(get_frame)` 内部做帧去重（内容不变不发送）与断线重连，
  `loop()` 只需返回当前帧，无需关心发送时机

### 运行

**ESP32 端部署**：

1. 复制 `src/esp32/config.example.json` 为 `src/esp32/config.json`，按实际环境修改
   （WiFi 模式/SSID/密码、HTTP 端口、灯带 `gpio` 与 `count`、默认颜色）
2. 在设备上安装 MicroPython 依赖：
   [aioble](https://github.com/micropython/micropython-lib/tree/master/micropython/bluetooth/aioble)
   与 [microdot](https://github.com/miguelgrinberg/microdot)
   （如通过 `mpremote mip install`，或手动拷贝源码到设备）
3. 将 `main.py` 与 `config.json` 一并上传到设备

**上位机端**：将仓库根目录 `config.json` 中的 `connection.host`/`port` 修改为
ESP32 实际地址后运行：

```bash
# 上位机（仓库根目录，读取 ./config.json；包相对导入要求以模块方式运行）
uv run python -m src.host.main

# 依赖管理（uv）
uv add <package>

# 代码检查（ruff，配置在 pyproject.toml：lint 启用 I/F 规则，双引号）
uvx ruff check src/
uvx ruff format src/
```

上位机 `config.json` 格式见 `src/host/config.py` 的 `Config` TypedDict：

```json
{
    "connection": { "host": "<ESP32 IP>", "port": 80 },
    "light": { "fps": 30 }
}
```

## 现有灯效

| 键名 | 类 | 数据来源 | 说明 |
|---|---|---|---|
| `music` | `Music` | SMTC 封面（winsdk）+ WASAPI 回环音频 | 颜色取专辑封面主色调，亮度跟随音量（`BrightnessTracker` 自适应动态范围） |
| `horizon` | `ForzaHorizon` | Forza Horizon 4/5 UDP Data Out（默认 20777 端口） | 颜色随转速比由绿转红，恒定亮度 |

## 注意事项

- ESP32 设备配置 `src/esp32/config.json` 与上位机 `config.json` 均含真实凭证/IP，
  勿提交到公开仓库
- Forza 遥测需在 游戏内：HUD 与游戏性 -> 数据输出 IP/端口 指向运行上位机的机器
- 上位机依赖 Windows 专有 API（winsdk SMTC、WASAPI loopback），仅支持 Windows
