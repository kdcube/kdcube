from abc import ABC, abstractmethod

class AbstractReputationProvider(ABC):
    @abstractmethod
    async def check_url(self, url: str) -> bool:
        pass