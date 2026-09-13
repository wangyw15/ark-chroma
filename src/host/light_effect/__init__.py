from dataclasses import dataclass

from .horizon import ForzaHorizon
from .music import Music


@dataclass
class Effect:
    name: str
    description: str
    class_type: type


AVAILABLE_EFFECTS = {
    "music": Effect(
        name="音乐律动",
        description="颜色跟随封面，亮度跟随音量律动",
        class_type=Music,
    ),
    "horizon": Effect(
        name="Forza Horizon 转速表",
        description="颜色跟随转速变化",
        class_type=ForzaHorizon,
    ),
}
