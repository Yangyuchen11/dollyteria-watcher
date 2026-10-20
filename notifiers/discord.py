"""
Discord Webhook 提醒模块
通过 Discord 官方 Webhook 将上新商品以富文本 Embed 卡片（含大图、价格、直达链接）实时推送到指定频道
"""
import time
import datetime
import logging
from typing import List, Dict
import requests
from notifiers.base import BaseNotifier

logger = logging.getLogger("dollyteria.notifier.discord")


class DiscordNotifier(BaseNotifier):
    def __init__(self, webhook_url: str = "", enabled: bool = False):
        super().__init__("discord", enabled)
        self.webhook_url = webhook_url.strip()

    def _build_embed(self, item: Dict, rule_name: str) -> Dict:
        """构建 Discord 富文本 Embed 卡片"""
        embed = {
            "title": f"🎀 {item.get('title', '未知商品')[:250]}",
            "url": item.get("url", "https://www.dollyteria.com/"),
            "description": f"🎯 命中监控规则: **{rule_name}**",
            "color": 0xEC4899,  # 醒目粉紫色
            "fields": [
                {
                    "name": "💰 价格",
                    "value": f"**{item.get('price_str', '￥' + str(item.get('price', 0)))}**",
                    "inline": True,
                },
                {
                    "name": "🏷️ 商品编号",
                    "value": f"`{item.get('model_number', '无')}`",
                    "inline": True,
                },
                {
                    "name": "📦 状态",
                    "value": "🟢 在库 (可立即加购)",
                    "inline": True,
                },
            ],
            "footer": {
                "text": "Dollyteria 上新提醒助手 • 点击上方标题直达商品页面",
            },
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

        img_url = item.get("img_url", "")
        if img_url and img_url.startswith("http"):
            embed["image"] = {"url": img_url}

        return embed

    def notify(self, items: List[Dict], rule: Dict) -> bool:
        if not self.enabled:
            return False

        if not self.webhook_url:
            logger.warning("Discord 通知已开启但未填写 Webhook URL，请在设置中配置")
            return False

        if not items:
            return False

        rule_name = rule.get("name", "上新监控")

        # Discord 单次最多发送 10 个 embeds
        embeds = [self._build_embed(item, rule_name) for item in items[:10]]

        payload = {
            "username": "Dollyteria 上新提醒",
            "content": f"🚨 **Dollyteria 上新提醒**：规则 【**{rule_name}**】 命中 {len(items)} 件新在售商品！",
            "embeds": embeds,
        }

        return self._send_webhook(payload)

    def _send_webhook(self, payload: Dict) -> bool:
        if not self.webhook_url:
            return False
        try:
            resp = requests.post(self.webhook_url, json=payload, timeout=10)
            if resp.status_code in [200, 204]:
                logger.info("Discord Webhook 通知推送成功！")
                return True
            else:
                logger.error(f"Discord Webhook 推送失败 (HTTP {resp.status_code}): {resp.text}")
                return False
        except Exception as e:
            logger.error(f"请求 Discord Webhook 异常: {e}")
            return False

    def test(self, webhook_url: str = "") -> Dict:
        """测试 Discord Webhook 连通性"""
        target_url = (webhook_url or self.webhook_url).strip()
        if not target_url:
            return {"status": "error", "message": "尚未设置 Discord Webhook URL，请先填入"}

        if not target_url.startswith("https://discord.com/api/webhooks/") and not target_url.startswith("https://discordapp.com/api/webhooks/"):
            return {"status": "error", "message": "无效的 Discord Webhook 地址，需以 https://discord.com/api/webhooks/ 开头"}

        payload = {
            "username": "Dollyteria 上新提醒",
            "content": "🔔 这是一条来自 **Dollyteria 监控小工具** 的测试通知！",
            "embeds": [
                {
                    "title": "🎉 Discord 通知连接测试成功！",
                    "description": "您的 Discord Webhook 已成功接入，监控命中新商品时将第一时间通过此频道向您推送包含图片与链接的精美卡片！",
                    "color": 0x34D399,
                    "fields": [
                        {"name": "监控状态", "value": "🟢 正常运行中", "inline": True},
                        {"name": "推送方式", "value": "Discord 官方 Webhook", "inline": True},
                    ],
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                }
            ],
        }

        try:
            resp = requests.post(target_url, json=payload, timeout=10)
            if resp.status_code in [200, 204]:
                return {"status": "ok", "message": "Discord 测试消息已成功送达您的频道，请查看！"}
            else:
                return {"status": "error", "message": f"Discord 推送失败 (HTTP {resp.status_code}): {resp.text[:100]}"}
        except Exception as e:
            return {"status": "error", "message": f"连接 Discord 发生异常: {str(e)}"}
