"""
通知中心管理器 (多渠道统一调度)
负责分发上新商品至声音、Windows桌面弹窗、Web实时流、LINE、邮件等通道
"""
import time
import logging
import threading
from typing import List, Dict, Callable
from notifiers.sound import SoundNotifier
from notifiers.desktop import DesktopNotifier
from notifiers.discord import DiscordNotifier
from notifiers.line import LineNotifier
from notifiers.email_notify import EmailNotifier

logger = logging.getLogger("dollyteria.notifier.manager")


class NotifierManager:
    def __init__(self, config: Dict = None):
        config = config or {}
        notif_cfg = config.get("notifications", {})

        self.sound = SoundNotifier(enabled=notif_cfg.get("sound_enabled", True))
        self.desktop = DesktopNotifier(enabled=notif_cfg.get("desktop_enabled", True))

        discord_cfg = notif_cfg.get("discord", {})
        self.discord = DiscordNotifier(
            webhook_url=discord_cfg.get("webhook_url", ""),
            enabled=discord_cfg.get("enabled", False),
        )

        line_cfg = notif_cfg.get("line", {})
        self.line = LineNotifier(
            token=line_cfg.get("token", ""),
            enabled=line_cfg.get("enabled", False),
        )

        email_cfg = notif_cfg.get("email", {})
        self.email = EmailNotifier(
            smtp_host=email_cfg.get("smtp_host", ""),
            smtp_port=email_cfg.get("smtp_port", 465),
            smtp_user=email_cfg.get("smtp_user", ""),
            smtp_password=email_cfg.get("smtp_password", ""),
            receiver_email=email_cfg.get("receiver_email", ""),
            use_ssl=email_cfg.get("use_ssl", True),
            enabled=email_cfg.get("enabled", False),
        )

        # 内存中保存最近的触发事件，供 Web UI 实时查看
        self.recent_events: List[Dict] = []
        self._listeners: List[Callable[[Dict], None]] = []

    @property
    def listeners(self) -> List[Callable[[Dict], None]]:
        return self._listeners

    def add_listener(self, callback: Callable[[Dict], None]):
        """注册实时监听器 (用于 Web SSE 推送)"""
        self._listeners.append(callback)

    def dispatch(self, items: List[Dict], rule: Dict):
        """统一分发通知"""
        if not items:
            return

        rule_name = rule.get("name", "默认规则")
        logger.info(f"🚀 触发上新提醒: 规则 [{rule_name}] 命中 {len(items)} 件新商品！")

        event = {
            "id": f"evt_{int(time.time() * 1000)}",
            "timestamp": int(time.time()),
            "rule_id": rule.get("id"),
            "rule_name": rule_name,
            "items": items,
        }

        self.recent_events.insert(0, event)
        if len(self.recent_events) > 50:
            self.recent_events.pop()

        # 异步分发至各个外部网络/系统通知通道，绝不阻塞抢购与主监控轮询
        def _async_notify():
            if self.sound.enabled:
                self.sound.notify(items, rule)
            if self.desktop.enabled:
                self.desktop.notify(items, rule)
            if self.discord.enabled:
                self.discord.notify(items, rule)
            if self.line.enabled:
                self.line.notify(items, rule)
            if self.email.enabled:
                self.email.notify(items, rule)

        threading.Thread(target=_async_notify, name="AsyncNotifyThread", daemon=True).start()

        # 推送给 Web UI 实时监听器 (SSE，毫秒级就地推送)
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception as e:
                logger.debug(f"推送监听器回调异常: {e}")

    def dispatch_order_success(self, item: Dict, order_result: Dict, rule: Dict = None):
        """自动下单成功统一分发给所有激活的通知渠道"""
        if not item or not order_result:
            return

        rule_name = (rule.get("name") if rule else "自动监控抢购") or "自动监控抢购"
        logger.info(f"🎉 [通知中心] 正在分发【自动下单成功】通知: [{item.get('title', item.get('id'))}]")

        event = {
            "id": f"evt_order_{int(time.time() * 1000)}",
            "type": "order_success",
            "timestamp": int(time.time()),
            "rule_name": rule_name,
            "item": item,
            "order_result": order_result,
        }

        self.recent_events.insert(0, event)
        if len(self.recent_events) > 50:
            self.recent_events.pop()

        # 异步分发到各个激活的外部通道
        def _async_order_notify():
            if self.desktop.enabled and hasattr(self.desktop, "notify_order_success"):
                self.desktop.notify_order_success(item, order_result, rule_name)
            if self.discord.enabled and hasattr(self.discord, "notify_order_success"):
                self.discord.notify_order_success(item, order_result, rule_name)
            if self.line.enabled and hasattr(self.line, "notify_order_success"):
                self.line.notify_order_success(item, order_result, rule_name)
            if self.email.enabled and hasattr(self.email, "notify_order_success"):
                self.email.notify_order_success(item, order_result, rule_name)

        threading.Thread(target=_async_order_notify, name="AsyncOrderNotifyThread", daemon=True).start()

        # 推送给 Web UI 实时监听器 (SSE)
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception as e:
                logger.debug(f"推送监听器回调异常: {e}")

    def update_config(self, notif_cfg: Dict):
        """动态更新通知渠道配置"""
        if "sound_enabled" in notif_cfg:
            self.sound.enabled = bool(notif_cfg["sound_enabled"])
        if "desktop_enabled" in notif_cfg:
            self.desktop.enabled = bool(notif_cfg["desktop_enabled"])

        if "discord" in notif_cfg:
            discord_cfg = notif_cfg["discord"]
            self.discord.webhook_url = discord_cfg.get("webhook_url", "").strip()
            self.discord.enabled = bool(discord_cfg.get("enabled", False))

        if "line" in notif_cfg:
            line_cfg = notif_cfg["line"]
            self.line.token = line_cfg.get("token", "").strip()
            self.line.enabled = bool(line_cfg.get("enabled", False))

        if "email" in notif_cfg:
            email_cfg = notif_cfg["email"]
            self.email.smtp_host = email_cfg.get("smtp_host", "").strip()
            self.email.smtp_port = int(email_cfg.get("smtp_port", 465))
            self.email.smtp_user = email_cfg.get("smtp_user", "").strip()
            self.email.smtp_password = email_cfg.get("smtp_password", "").strip()
            self.email.receiver_email = email_cfg.get("receiver_email", "").strip()
            self.email.use_ssl = bool(email_cfg.get("use_ssl", True))
            self.email.enabled = bool(email_cfg.get("enabled", False))
