"""
Flask Web 控制台与 REST / SSE API
提供优雅实时的可视化管理界面，支持关键词维护、定点设置、即时检测与多通道测试
"""
import os
import re
import json
import queue
import logging
import hashlib
import urllib.parse
from flask import Flask, render_template, jsonify, request, Response, send_file
import requests

from core.monitor import DollyteriaMonitor
from core.scheduler import DollyteriaScheduler

logger = logging.getLogger("dollyteria.web")


def create_app(monitor: DollyteriaMonitor, scheduler: DollyteriaScheduler) -> Flask:
    template_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "templates"))
    static_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "static"))

    app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)
    app.config["JSON_AS_ASCII"] = False
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

    # 访问密码鉴权助手
    def get_configured_password() -> str:
        env_pwd = os.environ.get("WEB_PASSWORD", "").strip()
        if env_pwd:
            return env_pwd
        return str(monitor.config.get("web_password", "")).strip()

    def compute_auth_token(password: str) -> str:
        if not password:
            return ""
        return hashlib.sha256(f"dolly_salt_{password}".encode("utf-8")).hexdigest()

    def is_authenticated() -> bool:
        pwd = get_configured_password()
        if not pwd:
            return True  # 未设置密码，直接放行
        expected_token = compute_auth_token(pwd)
        cookie_token = request.cookies.get("dolly_token")
        if cookie_token and cookie_token == expected_token:
            return True
        header_token = request.headers.get("X-Auth-Token")
        if header_token and header_token == expected_token:
            return True
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            if auth_header[7:].strip() == expected_token:
                return True
        query_token = request.args.get("token")
        if query_token and query_token == expected_token:
            return True
        return False

    @app.before_request
    def enforce_authentication():
        # 放行静态文件与鉴权相关接口
        if request.path.startswith("/static/") or request.path in [
            "/favicon.ico",
            "/api/auth/status",
            "/api/auth/login",
        ]:
            return None
        # 页面首页放行，由前端展示锁屏
        if request.path == "/":
            return None
        # 其他所有 /api/* 以及 /img_proxy 均需鉴权
        if not is_authenticated():
            return jsonify({
                "status": "error",
                "error": "unauthorized",
                "message": "请先输入访问密码解锁看板"
            }), 401

    # SSE 事件广播队列列表
    event_queues = []

    def on_new_event(event_data: dict):
        for q in list(event_queues):
            try:
                q.put_nowait(event_data)
            except Exception:
                pass

    monitor.notifier.add_listener(on_new_event)

    @app.route("/")
    def index():
        return render_template("index.html")

    # 鉴权相关 API 路由
    @app.route("/api/auth/status")
    def api_auth_status():
        pwd = get_configured_password()
        return jsonify({
            "auth_required": bool(pwd),
            "authenticated": is_authenticated()
        })

    @app.route("/api/auth/login", methods=["POST"])
    def api_auth_login():
        data = request.json or {}
        input_pwd = str(data.get("password", "")).strip()
        real_pwd = get_configured_password()
        if not real_pwd or input_pwd == real_pwd:
            token = compute_auth_token(real_pwd)
            resp = jsonify({"status": "ok", "message": "解锁成功", "token": token})
            resp.set_cookie(
                "dolly_token",
                token,
                max_age=30 * 86400,
                httponly=False,
                samesite="Lax",
                path="/"
            )
            return resp
        return jsonify({"status": "error", "message": "访问密码错误，请重新输入"}), 401

    @app.route("/api/auth/logout", methods=["POST"])
    def api_auth_logout():
        resp = jsonify({"status": "ok", "message": "已锁定并退出登录"})
        resp.delete_cookie("dolly_token", path="/")
        return resp

    @app.route("/api/auth/set_password", methods=["POST"])
    def api_auth_set_password():
        data = request.json or {}
        new_pwd = str(data.get("password", "")).strip()
        monitor.config["web_password"] = new_pwd
        monitor._save_config()
        token = compute_auth_token(new_pwd) if new_pwd else ""
        resp = jsonify({
            "status": "ok",
            "message": "访问密码已更新" if new_pwd else "访问密码已清除 (无密码模式)",
            "token": token,
            "auth_required": bool(new_pwd)
        })
        if new_pwd:
            resp.set_cookie("dolly_token", token, max_age=30 * 86400, httponly=False, samesite="Lax", path="/")
        else:
            resp.delete_cookie("dolly_token", path="/")
        return resp

    @app.route("/api/status")
    def api_status():
        sched_status = scheduler.get_status()
        stats = monitor.get_stats()
        seen_count = len(monitor.state.get("seen_ids", []))
        return jsonify({
            "scheduler": sched_status,
            "stats": stats,
            "seen_count": seen_count,
            "recent_events": monitor.notifier.recent_events[:10],
            "active_watches_count": len([w for w in monitor.config.get("watches", []) if w.get("enabled", True)]),
        })

    @app.route("/api/config", methods=["GET", "POST"])
    def api_config():
        if request.method == "POST":
            data = request.json or {}
            monitor.update_config(data)
            return jsonify({"status": "ok", "message": "配置已保存并生效"})
        return jsonify(monitor.config)

    @app.route("/api/check_now", methods=["POST"])
    def api_check_now():
        result = monitor.check_once()
        return jsonify(result)

    @app.route("/api/trigger_burst", methods=["POST"])
    def api_trigger_burst():
        data = request.json or {}
        duration = int(data.get("duration", 180))
        scheduler.trigger_burst(duration)
        return jsonify({"status": "ok", "message": f"爆发抢购模式已启动，将持续高频检测 {duration} 秒"})

    @app.route("/api/reset_seen", methods=["POST"])
    def api_reset_seen():
        monitor.reset_seen_items()
        return jsonify({"status": "ok", "message": "历史已记录商品集合已清空"})

    @app.route("/api/init_baseline", methods=["POST"])
    def api_init_baseline():
        monitor.initialize_baseline()
        return jsonify({
            "status": "ok",
            "message": f"基准线建立成功，已收录 {len(monitor.state.get('seen_ids', []))} 件当前在售商品，后续仅提醒新上架/新补货商品",
        })

    @app.route("/api/history")
    def api_history():
        return jsonify(monitor.state.get("history", []))

    @app.route("/api/clear_history", methods=["POST"])
    def api_clear_history():
        monitor.state["history"] = []
        monitor._save_state()
        return jsonify({"status": "ok", "message": "上新发现流列表已成功清空"})

    @app.route("/api/test_sound", methods=["POST"])
    def api_test_sound():
        monitor.notifier.sound.test()
        return jsonify({"status": "ok", "message": "已播放声音提醒"})

    @app.route("/api/test_desktop", methods=["POST"])
    def api_test_desktop():
        monitor.notifier.desktop.test()
        return jsonify({"status": "ok", "message": "已发送 Windows 桌面弹窗测试通知"})

    @app.route("/api/test_discord", methods=["POST"])
    def api_test_discord():
        data = request.json or {}
        webhook_url = (data.get("webhook_url") or monitor.notifier.discord.webhook_url or "").strip()
        from notifiers.discord import DiscordNotifier
        res = DiscordNotifier(webhook_url=webhook_url, enabled=True).test(webhook_url)
        return jsonify(res)

    @app.route("/api/test_line", methods=["POST"])
    def api_test_line():
        data = request.json or {}
        token = (data.get("token") or monitor.notifier.line.token or "").strip()
        from notifiers.line import LineNotifier
        res = LineNotifier(token=token, enabled=True).test()
        return jsonify(res)

    @app.route("/api/test_email", methods=["POST"])
    def api_test_email():
        data = request.json or {}
        host = (data.get("smtp_host") or monitor.notifier.email.smtp_host or "").strip()
        user = (data.get("smtp_user") or monitor.notifier.email.smtp_user or "").strip()
        pwd = (data.get("smtp_password") or monitor.notifier.email.smtp_password or "").strip()
        port = int(data.get("smtp_port") or monitor.notifier.email.smtp_port or 465)
        receiver = (data.get("receiver_email") or monitor.notifier.email.receiver_email or user).strip()

        from notifiers.email_notify import EmailNotifier
        tester = EmailNotifier(
            smtp_host=host,
            smtp_port=port,
            smtp_user=user,
            smtp_password=pwd,
            receiver_email=receiver,
            use_ssl=(port == 465),
            enabled=True,
        )
        res = tester.test()
        return jsonify(res)

    @app.route("/api/test_auto_order", methods=["POST"])
    def api_test_auto_order():
        data = request.json or {}
        username = (data.get("username") or monitor.config.get("auto_order", {}).get("username") or "").strip()
        password = (data.get("password") or monitor.config.get("auto_order", {}).get("password") or "").strip()
        raw_item = str(data.get("item_id") or "").strip()

        # 提取数字商品 ID
        item_id = ""
        if raw_item:
            m = re.search(r"(\d+)", raw_item)
            if m:
                item_id = m.group(1)

        # 未指定时自动获取当前最新的在库商品作为测试靶标
        if not item_id:
            try:
                in_stock_items = monitor.scraper.fetch_new_arrivals(num=10, in_stock_only=True)
                if in_stock_items:
                    item_id = in_stock_items[0]["id"]
            except Exception:
                pass
            if not item_id:
                item_id = "603472"

        payment_method = (data.get("payment_method") or monitor.config.get("auto_order", {}).get("payment_method", "cod")).strip()

        from hooks.auto_order import AutoOrderHook
        ao = AutoOrderHook(username=username, password=password, dry_run=True, payment_method=payment_method)
        res = ao.execute_checkout(item_id=item_id, dry_run=True, clear_after_dry_run=True, payment_method=payment_method)
        return jsonify(res)

    @app.route("/api/events")
    def api_events():
        """Server-Sent Events (SSE) 实时长连接，供网页实时接收上新提醒"""
        def event_stream():
            q = queue.Queue(maxsize=50)
            event_queues.append(q)
            try:
                # 首次连接发送一次握手包
                yield f"data: {json.dumps({'type': 'connected'})}\n\n"
                while True:
                    try:
                        event = q.get(timeout=20)
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    except queue.Empty:
                        # 发送心跳包保持连接
                        yield f": ping\n\n"
            finally:
                if q in event_queues:
                    event_queues.remove(q)

        return Response(event_stream(), mimetype="text/event-stream")

    @app.route("/img_proxy")
    def img_proxy():
        """图片代理，避免外部防盗链导致图片无法显示"""
        raw_url = request.args.get("url", "")
        if not raw_url or not raw_url.startswith("https://www.dollyteria.com/"):
            return "Invalid URL", 400
        try:
            r = requests.get(raw_url, headers={"Referer": "https://www.dollyteria.com/"}, timeout=8)
            content_type = r.headers.get("Content-Type", "image/jpeg")
            return Response(r.content, mimetype=content_type)
        except Exception:
            return "Failed to fetch image", 500

    return app
