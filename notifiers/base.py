from abc import ABC, abstractmethod
from typing import List, Dict

class BaseNotifier(ABC):
    def __init__(self, name: str, enabled: bool = True):
        self.name = name
        self.enabled = enabled

    @abstractmethod
    def notify(self, items: List[Dict], rule: Dict) -> bool:
        """
        发送提醒
        items: 符合条件的商品列表
        rule: 触发提醒的监控规则
        """
        pass
