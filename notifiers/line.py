"""
LINE 通知模块 (预留扩展接口)
支持 LINE Notify 与 LINE Messaging API
当用户配置了 Token 后即可直接生效
"""
import logging
import requests
from typing import List, Dict
from notifiers.base import BaseNotifier

logger = logging.getLogger("dollyteria.notifier.line")


class LineNotifier(BaseNotifier):
    def __init__(self, token: str = "", enabled: bool = False):
        super().__init__("line", enabled)
        self.token = token.strip()

    def notify(self, items: List[Dict], rule: Dict) -> bool:
        if not self.enabled:
            return False

        if not self.token:
            logger.warning("LINE 通知已开启但未填写 Token，请在设置中配置 LINE Token")
            return False

        if not items:
            return False

        rule_name = rule.get("name", "上新监控")
        lines = [
            f"\n🔔 [Dollyteria 上新提醒]",
            f"监控规则: {rule_name}",
            f"发现匹配商品 {len(items)} 件:",
            "-" * 20,
        ]

        for item in items[:5]:
            lines.append(f"• {item.get('title')}")
            lines.append(f"  价格: {item.get('price_str')}")
            lines.append(f"  链接: {item.get('url')}")
            lines.append("")

        if len(items) > 5:
            lines.append(f"... 还有 {len(items) - 5} 件商品已匹配")

        message = "\n".join(lines)
        return self._send_line_notify(message, image_thumbnail=items[0].get("img_url"))

    def notify_order_success(self, item: Dict, order_result: Dict, rule_name: str = "") -> bool:
        """推送自动下单成功 LINE 消息"""
        if not self.enabled or not self.token:
            return False

        details = order_result.get("details", {})
        pay_method = order_result.get("payment_method") or details.get("payment_method") or "代金引換"
        total_price = details.get("total_price") or item.get("price_str") or f"￥{item.get('price', 0):,}"

        lines = [
            "\n🎉 [Dollyteria 自动下单成功！]",
            f"商品: {item.get('title')}",
            f"总金额: {total_price}",
            f"支付方式: 【{pay_method}】",
            f"触发来源: {rule_name or '自动监控'}",
            f"下单耗时: {order_result.get('elapsed_sec', 0)}s",
            "官网个人中心: https://www.dollyteria.com/member",
            "请尽快前往官网个人中心核对订单！",
        ]
        message = "\n".join(lines)
        return self._send_line_notify(message, image_thumbnail=item.get("img_url"))

    def _send_line_notify(self, message: str, image_thumbnail: str = "") -> bool:
        """通过 LINE Notify API 发送消息"""
        url = "https://notify-api.line.me/api/notify"
        headers = {"Authorization": f"Bearer {self.token}"}
        data = {"message": message}

        if image_thumbnail and image_thumbnail.startswith("http"):
            data["imageThumbnail"] = image_thumbnail
            data["imageFullsize"] = image_thumbnail

        try:
            resp = requests.post(url, headers=headers, data=data, timeout=10)
            if resp.status_code == 200:
                logger.info("LINE 通知发送成功！")
                return True
            else:
                logger.error(f"LINE Notify 发送失败 (HTTP {resp.status_code}): {resp.text}")
                return False
        except Exception as e:
            logger.error(f"请求 LINE Notify API 异常: {e}")
            return False

    def test(self) -> Dict:
        """测试 LINE 通知连通性"""
        if not self.token:
            return {"status": "error", "message": "尚未设置 LINE Token，请先在配置中填入"}
        ok = self._send_line_notify("🔔 这是一条来自 Dollyteria 监控小工具的测试通知！")
        return {"status": "ok" if ok else "error", "message": "已发送测试通知，请检查 LINE" if ok else "发送失败，请检查 Token"}
