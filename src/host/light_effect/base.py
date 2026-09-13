from abc import ABC, abstractmethod


class BaseEffect(ABC):
    @abstractmethod
    def __init__(self, fps: int): ...

    @abstractmethod
    async def init(self): ...

    @abstractmethod
    async def exit(self): ...

    @abstractmethod
    async def loop(self): ...
