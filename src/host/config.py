from typing import TypedDict


class ConnectionConfig(TypedDict):
    host: str
    port: int


class LightConfig(TypedDict):
    fps: int


class Config(TypedDict):
    connection: ConnectionConfig
    light: LightConfig
