"""
Windows 桌面原生气泡与横幅通知模块
点击通知可直接在浏览器中打开 Dollyteria 商品详情页
"""
import sys
import html
import subprocess
import threading
import logging
from typing import List, Dict
from notifiers.base import BaseNotifier

logger = logging.getLogger("dollyteria.notifier.desktop")


class DesktopNotifier(BaseNotifier):
    def __init__(self, enabled: bool = True):
        super().__init__("desktop", enabled)

    def _show_windows_toast(self, title: str, message: str, url: str):
        if sys.platform != "win32":
            logger.info(f"[桌面通知模拟] {title}: {message} ({url})")
            return

        safe_title = html.escape(title)
        safe_msg = html.escape(message)
        safe_url = html.escape(url)

        # 构建 WinRT Toast XML，带 protocol launch 支持点击跳转
        ps_script = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$template = @"
<toast activationType="protocol" launch="{safe_url}">
    <visual>
        <binding template="ToastGeneric">
            <text>{safe_title}</text>
            <text>{safe_msg}</text>
            <text>点击直接在浏览器打开商品页面</text>
        </binding>
    </visual>
</toast>
"@
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($template)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Dollyteria Watcher").Show($toast)
"""
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                timeout=5,
            )
        except Exception as e:
            logger.error(f"发送 Windows Toast 弹窗失败: {e}")

    def notify(self, items: List[Dict], rule: Dict) -> bool:
        if not self.enabled or not items:
            return False

        def _send_all():
            rule_name = rule.get("name", "上新监控")
            # 最多连续弹窗 3 个，避免几十件商品时卡死桌面
            for item in items[:3]:
                title = f"🔔 Dollyteria 上新: {rule_name}"
                body = f"【{item.get('price_str', '')}】{item.get('title', '')[:50]}"
                url = item.get("url", "https://www.dollyteria.com/new")
                self._show_windows_toast(title, body, url)

            if len(items) > 3:
                title = f"🔔 Dollyteria 上新: {rule_name}"
                body = f"还有另外 {len(items) - 3} 件商品匹配您的关键字，请前往查看！"
                self._show_windows_toast(title, body, "https://www.dollyteria.com/new")

        thread = threading.Thread(target=_send_all, daemon=True)
        thread.start()
        return True

    def notify_order_success(self, item: Dict, order_result: Dict, rule_name: str = "") -> bool:
        """推送自动下单成功 Windows 气泡通知"""
        if not self.enabled:
            return False

        details = order_result.get("details", {})
        pay_method = order_result.get("payment_method") or details.get("payment_method") or "代金引換"
        title = "🎉 Dollyteria 自动下单成功！"
        body = f"已成功抢到商品: {item.get('title', '')[:35]}\n支付: 【{pay_method}】 | 耗时: {order_result.get('elapsed_sec', 0)}s"
        url = "https://www.dollyteria.com/member"

        thread = threading.Thread(
            target=self._show_windows_toast,
            args=(title, body, url),
            daemon=True,
        )
        thread.start()
        return True

    def test(self):
        """测试弹窗"""
        self._show_windows_toast(
            "🔔 Dollyteria 桌面提醒测试",
            "测试成功！检测到有货时将通过此弹窗第一时间提醒您。",
            "https://www.dollyteria.com/new",
        )
