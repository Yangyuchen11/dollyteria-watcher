"""
声音提醒模块 (Windows winsound 支持，异步无阻塞播放)
"""
import sys
import threading
import logging
from typing import List, Dict
from notifiers.base import BaseNotifier

logger = logging.getLogger("dollyteria.notifier.sound")


class SoundNotifier(BaseNotifier):
    def __init__(self, enabled: bool = True):
        super().__init__("sound", enabled)

    def _play_chime(self):
        try:
            if sys.platform == "win32":
                import winsound
                # 播放四音阶欢快提示音 (A5 -> D6 -> E6 -> A6)
                tones = [(880, 100), (1175, 100), (1318, 120), (1760, 250)]
                for freq, dur in tones:
                    winsound.Beep(freq, dur)
            else:
                # 兼容 Linux / Mac 终端蜂鸣
                print("\a", end="", flush=True)
        except Exception as e:
            logger.debug(f"播放声音提示失败: {e}")

    def notify(self, items: List[Dict], rule: Dict) -> bool:
        if not self.enabled or not items:
            return False

        # 在子线程中播放声音，不阻塞主流程
        thread = threading.Thread(target=self._play_chime, daemon=True)
        thread.start()
        return True

    def test(self):
        """测试音效"""
        self._play_chime()
