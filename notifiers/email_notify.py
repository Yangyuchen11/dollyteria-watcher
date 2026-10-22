"""
邮件通知模块 (预留扩展接口)
支持标准 SMTP 协议 (QQ邮箱 / 163 / Gmail / Outlook 等)
配置好 SMTP 账户与授权码后即可发送精美的 HTML 上新提醒邮件
"""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
import logging
from typing import List, Dict
from notifiers.base import BaseNotifier

logger = logging.getLogger("dollyteria.notifier.email")


class EmailNotifier(BaseNotifier):
    def __init__(
        self,
        smtp_host: str = "",
        smtp_port: int = 465,
        smtp_user: str = "",
        smtp_password: str = "",
        receiver_email: str = "",
        use_ssl: bool = True,
        enabled: bool = False,
    ):
        super().__init__("email", enabled)
        self.smtp_host = smtp_host.strip()
        self.smtp_port = int(smtp_port) if smtp_port else 465
        self.smtp_user = smtp_user.strip()
        self.smtp_password = smtp_password.strip()
        self.receiver_email = receiver_email.strip() or self.smtp_user
        self.use_ssl = use_ssl

    def _build_html(self, items: List[Dict], rule_name: str) -> str:
        rows = []
        for it in items[:10]:
            img_tag = f'<img src="{it.get("img_url")}" width="100" style="border-radius:6px;object-fit:cover;" />' if it.get("img_url") else ""
            rows.append(f"""
            <tr style="border-bottom:1px solid #eee;">
                <td style="padding:10px;text-align:center;">{img_tag}</td>
                <td style="padding:10px;">
                    <div style="font-size:14px;font-weight:bold;color:#333;margin-bottom:6px;">{it.get('title')}</div>
                    <div style="font-size:12px;color:#888;">编号: {it.get('model_number')}</div>
                </td>
                <td style="padding:10px;font-size:16px;font-weight:bold;color:#e63946;white-space:nowrap;">{it.get('price_str')}</td>
                <td style="padding:10px;text-align:center;white-space:nowrap;">
                    <a href="{it.get('url')}" style="display:inline-block;padding:8px 16px;background:#e63946;color:#fff;text-decoration:none;border-radius:4px;font-size:13px;font-weight:bold;">查看商品</a>
                </td>
            </tr>
            """)

        html = f"""
        <div style="max-width:700px;margin:0 auto;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;border:1px solid #e0e0e0;border-radius:8px;overflow:hidden;">
            <div style="background:#1a1a2e;color:#fff;padding:18px 24px;">
                <h2 style="margin:0;font-size:18px;">🔔 Dollyteria 上新提醒</h2>
                <div style="font-size:13px;color:#cbd5e1;margin-top:4px;">命中规则: {rule_name} | 发现 {len(items)} 件有货商品</div>
            </div>
            <table style="width:100%;border-collapse:collapse;">
                <thead>
                    <tr style="background:#f8f9fa;color:#666;font-size:13px;border-bottom:1px solid #ddd;">
                        <th style="padding:8px;width:110px;">图片</th>
                        <th style="padding:8px;text-align:left;">商品名称</th>
                        <th style="padding:8px;text-align:left;">价格</th>
                        <th style="padding:8px;text-align:center;">操作</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join(rows)}
                </tbody>
            </table>
            <div style="background:#f8f9fa;padding:12px 24px;font-size:12px;color:#999;text-align:center;">
                由 Dollyteria 上新提醒小工具自动推送
            </div>
        </div>
        """
        return html

    def notify(self, items: List[Dict], rule: Dict) -> bool:
        if not self.enabled:
            return False

        if not (self.smtp_host and self.smtp_user and self.smtp_password):
            logger.warning("邮件通知已开启但未完整配置 SMTP 服务器/用户名/授权码")
            return False

        if not items:
            return False

        rule_name = rule.get("name", "上新监控")
        subject = f"【Dollyteria上新】{rule_name} 发现 {len(items)} 件有货商品！"
        html_content = self._build_html(items, rule_name)

        return self._send_email(subject, html_content)

    def notify_order_success(self, item: Dict, order_result: Dict, rule_name: str = "") -> bool:
        """推送自动下单成功专属邮件"""
        if not self.enabled:
            return False
        if not (self.smtp_host and self.smtp_user and self.smtp_password):
            return False

        details = order_result.get("details", {})
        pay_method = order_result.get("payment_method") or details.get("payment_method") or "代金引換"
        total_price = details.get("total_price") or item.get("price_str") or f"￥{item.get('price', 0):,}"

        title_short = (item.get("title") or "商品")[:35]
        subject = f"🎉【Dollyteria 自动下单成功】已成功为您抢购: {title_short}"

        img_tag = f'<img src="{item.get("img_url")}" width="150" style="border-radius:8px;object-fit:cover;box-shadow:0 4px 12px rgba(0,0,0,0.1);" />' if item.get("img_url") else ""

        html = f"""
        <div style="max-width:650px;margin:0 auto;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;border:1px solid #e2e8f0;border-radius:12px;overflow:hidden;background:#ffffff;">
            <div style="background:linear-gradient(135deg, #10b981 0%, #059669 100%);color:#ffffff;padding:22px 28px;">
                <div style="font-size:22px;font-weight:bold;margin-bottom:6px;">🎉 Dollyteria 自动下单成功通知</div>
                <div style="font-size:13px;opacity:0.95;">系统监控到目标商品，已毫秒级推进结算完成 Dollyteria 官网正式下单！</div>
            </div>
            <div style="padding:24px 28px;">
                <div style="display:flex;margin-bottom:20px;border-bottom:1px solid #f1f5f9;padding-bottom:18px;">
                    <div style="margin-right:18px;">{img_tag}</div>
                    <div>
                        <h3 style="margin:0 0 8px 0;font-size:15px;color:#1e293b;line-height:1.4;">{item.get('title', '未知商品')}</h3>
                        <div style="font-size:13px;color:#64748b;margin-bottom:6px;">商品编号: <b>{item.get('model_number') or item.get('id')}</b></div>
                        <div style="font-size:20px;font-weight:bold;color:#10b981;">{total_price}</div>
                    </div>
                </div>
                <table style="width:100%;border-collapse:collapse;font-size:14px;color:#334155;margin-bottom:22px;">
                    <tr style="border-bottom:1px solid #f8fafc;"><td style="padding:8px 0;color:#64748b;">支付方式:</td><td style="padding:8px 0;font-weight:600;text-align:right;">【{pay_method}】</td></tr>
                    <tr style="border-bottom:1px solid #f8fafc;"><td style="padding:8px 0;color:#64748b;">收货人:</td><td style="padding:8px 0;font-weight:600;text-align:right;">{details.get('recipient_name') or '官网默认收货人'}</td></tr>
                    <tr style="border-bottom:1px solid #f8fafc;"><td style="padding:8px 0;color:#64748b;">触发来源:</td><td style="padding:8px 0;font-weight:600;text-align:right;">{rule_name or '自动监控'}</td></tr>
                    <tr style="border-bottom:1px solid #f8fafc;"><td style="padding:8px 0;color:#64748b;">下单耗时:</td><td style="padding:8px 0;font-weight:600;text-align:right;">{order_result.get('elapsed_sec', 0)} 秒</td></tr>
                </table>
                <div style="text-align:center;margin-top:20px;">
                    <a href="https://www.dollyteria.com/member" style="display:inline-block;padding:12px 28px;background:#10b981;color:#ffffff;text-decoration:none;border-radius:6px;font-weight:bold;font-size:14px;">前往官网个人中心核对订单</a>
                </div>
            </div>
            <div style="background:#f8fafc;padding:12px 28px;font-size:12px;color:#94a3b8;text-align:center;border-top:1px solid #f1f5f9;">
                Dollyteria 自动下单抢购助手 • 请及时前往官网核对发货与支付信息
            </div>
        </div>
        """
        return self._send_email(subject, html)

    def _send_email(self, subject: str, html_content: str) -> bool:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = Header(subject, "utf-8")
            msg["From"] = Header(f"Dollyteria Watcher <{self.smtp_user}>", "utf-8")
            msg["To"] = Header(self.receiver_email, "utf-8")
            msg.attach(MIMEText(html_content, "html", "utf-8"))

            if self.smtp_port == 465 or (self.use_ssl and self.smtp_port != 587):
                server = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=12)
            else:
                server = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=12)
                server.starttls()

            server.login(self.smtp_user, self.smtp_password)
            server.sendmail(self.smtp_user, [self.receiver_email], msg.as_string())
            server.quit()
            logger.info(f"邮件通知已成功发送至 {self.receiver_email}")
            return True
        except Exception as e:
            logger.error(f"发送邮件异常: {e}")
            return False

    def test(self) -> Dict:
        """测试邮件发送连通性"""
        if not (self.smtp_host and self.smtp_user and self.smtp_password):
            return {"status": "error", "message": "SMTP 主机/账号/密码尚未填完整"}
        ok = self._send_email(
            "🔔 Dollyteria 邮件通知连通性测试",
            "<p>您好！这是一封测试邮件，说明您的 Dollyteria 上新邮件提醒已正确配置并能够正常工作！</p>",
        )
        return {"status": "ok" if ok else "error", "message": "测试邮件已发送，请查收" if ok else "发送失败，请检查 SMTP 配置"}
