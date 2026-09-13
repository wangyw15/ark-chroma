import asyncio
import json

from .api import HTTPClient, WebSocketClient
from .config import Config
from .light_effect import AVAILABLE_EFFECTS


async def main():
    with open("config.json", "r", encoding="utf-8") as f:
        config: Config = json.load(f)

    host = config["connection"]["host"]
    port = config["connection"]["port"]

    http_client = HTTPClient(host, port)
    ws_client = WebSocketClient(host, port, send_fps=config["light"]["fps"])

    light_info = await http_client.get_light()

    effect_mapping = []
    for k, v in AVAILABLE_EFFECTS.items():
        effect_mapping.append(k)
        print(f"{len(effect_mapping)}. {v.name} ({v.description})")

    selected = input("选择灯效: ")
    selected_effect = AVAILABLE_EFFECTS[effect_mapping[int(selected) - 1]]
    print()

    effect_instance = selected_effect.class_type(
        light_info["count"], ws_client.send_fps
    )

    await effect_instance.init()
    await ws_client.run(effect_instance.loop)


if __name__ == "__main__":
    asyncio.run(main())
