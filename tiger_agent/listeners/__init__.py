from abc import ABC, abstractmethod
from asyncio import TaskGroup


class Listener(ABC):
    @abstractmethod
    async def start(self, tasks: TaskGroup) -> None: ...
