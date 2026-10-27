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
from core.terminal import TerminalLogManager

logger = logging.getLogger("dollyteria.web")


def create_app(monitor: DollyteriaMonitor, scheduler: DollyteriaScheduler) -> Flask:
    template_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "templates"))
    static_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "static"))

    app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)
    app.config["JSON_AS_ASCII"] = False
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

    # 初始化实时运行终端日志拦截管理器
    terminal_mgr = TerminalLogManager.get_instance()
    terminal_mgr.install()
    terminal_mgr.seed_status(monitor, scheduler)

    # 访问密码鉴权助手
    def get_configured_password() -> str:
        # 1. 优先读取用户在 Web 界面中主动设置/保存的密码 (持久化在 config.json 中)
        cfg_pwd = monitor.config.get("web_password")
        if cfg_pwd == "__DISABLED__":
            return ""  # 用户明确在界面清空并取消了密码保护
        if cfg_pwd and str(cfg_pwd).strip():
            return str(cfg_pwd).strip()
        # 2. 如果 config.json 未显式设置，回退使用环境变量 (例如 docker-compose 初次启动的默认值)
        env_pwd = os.environ.get("WEB_PASSWORD", "").strip()
        if env_pwd:
            return env_pwd
        return ""

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
        # 放行静态文件、健康检查与鉴权相关接口
        if request.path.startswith("/static/") or request.path in [
            "/favicon.ico",
            "/health",
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

    @app.route("/health")
    def health_check():
        """轻量级健康检查接口，供 Docker 与 Dokploy 容器平台探针使用"""
        return jsonify({"status": "ok", "app": "dollyteria-watcher"})

    # SSE 事件广播队列列表
    event_queues = []

    def on_new_event(event_data: dict):
        for q in list(event_queues):
            try:
                q.put_nowait(event_data)
            except Exception:
                pass
        for listener in list(getattr(monitor.notifier, 'listeners', getattr(monitor.notifier, '_listeners', []))):
            if listener != on_new_event:
                try:
                    listener(event_data)
                except Exception:
                    pass

    monitor.notifier.add_listener(on_new_event)
    terminal_mgr.add_listener(lambda entry: on_new_event({"type": "terminal_log", "log": entry}))

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
        monitor.config["web_password"] = new_pwd if new_pwd else "__DISABLED__"
        monitor._save_config()
        # 同步更新运行时环境变量，确保当前进程无需重启即刻使用新密码
        os.environ["WEB_PASSWORD"] = new_pwd
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

    # ==================== 商品全量档案与收录 API ====================
    @app.route("/api/archive/stats")
    def api_archive_stats():
        return jsonify(monitor.archiver.get_status())

    @app.route("/api/archive/crawler/start", methods=["POST"])
    def api_archive_crawler_start():
        data = request.json or {}
        start_page = data.get("start_page")
        if start_page is not None and str(start_page).isdigit():
            start_page = int(start_page)
        else:
            start_page = None
        delay = float(data.get("delay", 1.2))
        res = monitor.archiver.start(start_page=start_page, delay=delay)
        return jsonify(res)

    @app.route("/api/archive/crawler/pause", methods=["POST"])
    def api_archive_crawler_pause():
        return jsonify(monitor.archiver.pause())

    @app.route("/api/archive/crawler/resume", methods=["POST"])
    def api_archive_crawler_resume():
        return jsonify(monitor.archiver.resume())

    @app.route("/api/archive/crawler/stop", methods=["POST"])
    def api_archive_crawler_stop():
        return jsonify(monitor.archiver.stop())

    @app.route("/api/archive/sync_stock", methods=["POST"])
    def api_archive_sync_stock():
        res = monitor.archiver.start_stock_sync(delay=1.0)
        return jsonify(res)

    @app.route("/api/archive/sync_new_stream", methods=["POST"])
    def api_archive_sync_new_stream():
        data = request.json or {}
        default_max = int(monitor.config.get("schedule", {}).get("max_stream_pages", 5))
        max_pages = int(data.get("max_pages", default_max))
        res = monitor.sync_new_arrivals_to_archive(max_pages=max_pages)
        return jsonify(res)

    @app.route("/api/archive/products")
    def api_archive_products():
        kw = request.args.get("keyword", "").strip()
        in_stock = request.args.get("in_stock", "all").strip()
        is_new = request.args.get("is_new", "all").strip()
        sort_by = request.args.get("sort_by", "updated_desc").strip()
        try:
            page = int(request.args.get("page", 1))
        except ValueError:
            page = 1
        try:
            page_size = int(request.args.get("page_size", 30))
        except ValueError:
            page_size = 30

        preset_kw_raw = request.args.get("preset_keywords", "")
        preset_kw_list = []
        if preset_kw_raw:
            if preset_kw_raw.startswith("["):
                try:
                    import json
                    preset_kw_list = json.loads(preset_kw_raw)
                except Exception:
                    preset_kw_list = [k.strip() for k in preset_kw_raw.split(",") if k.strip()]
            else:
                preset_kw_list = [k.strip() for k in preset_kw_raw.split(",") if k.strip()]
        else:
            preset_kw_list = request.args.getlist("preset_keywords") or []

        preset_match_mode = request.args.get("preset_match_mode", "all").strip()

        res = monitor.archive_db.query_products(
            keyword=kw,
            in_stock=in_stock,
            is_new=is_new,
            preset_keywords=preset_kw_list,
            preset_match_mode=preset_match_mode,
            sort_by=sort_by,
            page=page,
            page_size=page_size,
        )
        return jsonify(res)

    @app.route("/api/archive/preset_keywords", methods=["GET", "POST"])
    def api_archive_preset_keywords():
        if request.method == "POST":
            data = request.json or {}
            action = data.get("action", "")
            if action == "add":
                kw = str(data.get("keyword", "")).strip()
                if not kw:
                    return jsonify({"status": "error", "message": "关键词不能为空"}), 400
                keywords = monitor.archive_db.add_preset_keyword(kw)
                return jsonify({"status": "ok", "message": f"成功添加预设关键词: {kw}", "keywords": keywords})
            elif action == "remove":
                kw = str(data.get("keyword", "")).strip()
                keywords = monitor.archive_db.remove_preset_keyword(kw)
                return jsonify({"status": "ok", "message": f"已移除预设关键词: {kw}", "keywords": keywords})
            elif "keywords" in data and isinstance(data["keywords"], list):
                keywords = monitor.archive_db.set_preset_keywords(data["keywords"])
                return jsonify({"status": "ok", "message": "预设关键词已更新", "keywords": keywords})
            elif "keyword" in data:
                kw = str(data["keyword"]).strip()
                if not kw:
                    return jsonify({"status": "error", "message": "关键词不能为空"}), 400
                keywords = monitor.archive_db.add_preset_keyword(kw)
                return jsonify({"status": "ok", "message": f"成功添加预设关键词: {kw}", "keywords": keywords})
            return jsonify({"status": "error", "message": "无效的请求格式"}), 400

        keywords = monitor.archive_db.get_preset_keywords()
        return jsonify({"status": "ok", "keywords": keywords})

    @app.route("/api/archive/apply_baseline", methods=["POST"])
    def api_archive_apply_baseline():
        count = monitor.apply_archive_baseline()
        return jsonify({
            "status": "ok",
            "message": f"成功将档案库中 {count} 件在售商品同步为监控基准线！",
            "count": count,
            "total_baseline": len(monitor.state.get("seen_ids", []))
        })

    @app.route("/api/archive/clear", methods=["POST"])
    def api_archive_clear():
        monitor.archive_db.clear_archive()
        return jsonify({"status": "ok", "message": "商品归档数据库已清空"})

    @app.route("/api/history")
    def api_history():
        history = list(monitor.state.get("history", []))
        if history:
            ids = [it.get("id") for it in history if it.get("id")]
            if ids:
                stock_map = monitor.archive_db.get_stock_status_map(ids)
                for it in history:
                    pid = str(it.get("id", ""))
                    if pid in stock_map:
                        it["is_in_stock"] = stock_map[pid]
        return jsonify(history)

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

        ao_cfg = monitor.config.get("auto_order", {})
        from hooks.auto_order import AutoOrderHook
        ao = AutoOrderHook(
            username=username,
            password=password,
            dry_run=True,
            payment_method=payment_method,
            retry_delay=float(ao_cfg.get("retry_delay_sec", 0.15)),
            max_retries=int(ao_cfg.get("max_retries", 35)),
        )
        probe_recaptcha = bool(data.get("probe_recaptcha", True))
        res = ao.execute_checkout(
            item_id=item_id,
            dry_run=True,
            clear_after_dry_run=True,
            payment_method=payment_method,
            probe_recaptcha=probe_recaptcha,
        )
        try:
            order_rec = monitor.archive_db.record_order(
                item={"id": item_id, "title": f"接口联调测试靶标 #{item_id}"},
                rule_name="接口联调测试",
                trigger_type="test",
                order_res=res,
            )
            on_new_event({"type": "order_recorded", "order": order_rec})
        except Exception as e:
            logger.error(f"接口测试记录订单异常: {e}")
        return jsonify(res)

    @app.route("/api/test_recaptcha", methods=["POST"])
    def api_test_recaptcha():
        from hooks.auto_order import RecaptchaTokenProvider, DEFAULT_RECAPTCHA_SITEKEY
        provider = RecaptchaTokenProvider.get_instance()
        data = request.json or {}
        sitekey = (data.get("sitekey") or DEFAULT_RECAPTCHA_SITEKEY).strip()
        action = (data.get("action") or "submit").strip()
        res = provider.test_token_generation(sitekey=sitekey, action=action)
        return jsonify(res)

    @app.route("/api/browser/open_gui", methods=["POST"])
    def api_browser_open_gui():
        from hooks.auto_order import RecaptchaTokenProvider
        data = request.json or {}
        url = (data.get("url") or "https://accounts.google.com/").strip()
        res = RecaptchaTokenProvider.get_instance().open_gui_browser(start_url=url)
        return jsonify(res)

    @app.route("/api/browser/auto_warm", methods=["POST"])
    def api_browser_auto_warm():
        from hooks.auto_order import RecaptchaTokenProvider
        res = RecaptchaTokenProvider.get_instance().auto_warm_environment()
        return jsonify(res)

    @app.route("/api/browser/status", methods=["GET"])
    def api_browser_status():
        from hooks.auto_order import RecaptchaTokenProvider
        res = RecaptchaTokenProvider.get_instance().get_profile_status()
        return jsonify(res)

    @app.route("/api/auto_order/buy", methods=["POST"])
    def api_auto_order_buy():
        data = request.json or {}
        item_id = str(data.get("item_id", "")).strip()
        item_title = str(data.get("item_title", "")).strip()
        if not item_id:
            return jsonify({"success": False, "message": "未指定商品 ID"}), 400

        # 校验全站档案一键下单独立开关
        qb_cfg = monitor.config.get("quick_buy", {})
        if not qb_cfg.get("enabled", False):
            return jsonify({
                "success": False,
                "message": "一键下单功能已关闭，请先在【🛒 自动下单】设置中启动一键下单"
            }), 400

        ao_cfg = monitor.config.get("auto_order", {})
        username = (ao_cfg.get("username") or "").strip()
        password = (ao_cfg.get("password") or "").strip()
        if not username or not password:
            return jsonify({
                "success": False,
                "message": "未配置 Dollyteria 账号邮箱或密码，请先在【🛒 自动下单接口】填写登录凭据"
            }), 400

        payment_method = (data.get("payment_method") or ao_cfg.get("payment_method", "cod")).strip()
        dry_run = data.get("dry_run")
        if dry_run is None:
            dry_run = qb_cfg.get("dry_run", True)
        else:
            dry_run = bool(dry_run)

        # 借用后台已预热好的 Worker 执行一键购买，享受 0 延迟秒级开抢与独立 Session
        worker = monitor.auto_order.get_worker()
        worker.dry_run = dry_run
        worker.payment_method = payment_method
        worker.retry_delay = float(ao_cfg.get("retry_delay_sec", 0.15))
        worker.max_retries = int(ao_cfg.get("max_retries", 35))
        try:
            res = worker.execute_checkout(
                item_id=item_id,
                item_title=item_title,
                dry_run=dry_run,
                clear_after_dry_run=dry_run,
                payment_method=payment_method,
            )
        finally:
            monitor.auto_order.release_worker(worker)

        # 若加购返回商品已明确售罄，或真实下单购买成功 (孤品已被本账号买断)，立即标记归档库与历史流并向 Web 前端广播 SoldOut 事件
        is_bought_or_soldout = (
            (not res.get("success") and res.get("is_sold_out"))
            or (res.get("success") and not res.get("dry_run"))
        )
        if is_bought_or_soldout:
            monitor.archive_db.mark_sold_out(item_id)
            for h in monitor.state.get("history", []):
                if str(h.get("id", "")) == item_id:
                    h["is_in_stock"] = False
            on_new_event({
                "type": "item_sold_out",
                "item_id": item_id,
            })
        if res.get("success") and not res.get("dry_run"):
            item_info = {
                "id": item_id,
                "title": item_title or f"商品 #{item_id}",
                "url": f"https://www.dollyteria.com/product/{item_id}",
            }
            archived = monitor.archive_db.get_product_by_id(item_id)
            if archived:
                item_info.update(archived)
            try:
                monitor.notifier.dispatch_order_success(
                    item=item_info,
                    order_result=res,
                    rule={"name": "看板一键购买"}
                )
            except Exception as e:
                logger.error(f"一键购买分发成功通知异常: {e}")

        # 写入购买记录数据库
        try:
            order_rec = monitor.archive_db.record_order(
                item={
                    "id": item_id,
                    "title": item_title or f"商品 #{item_id}",
                    "url": f"https://www.dollyteria.com/product/{item_id}",
                },
                rule_name="看板一键抢购",
                trigger_type="quick_buy",
                order_res=res,
            )
            on_new_event({"type": "order_recorded", "order": order_rec})
        except Exception as e:
            logger.error(f"一键抢购写入购买记录异常: {e}")

        return jsonify(res)

    # ==================== 实时运行终端 API ====================
    @app.route("/api/terminal/logs")
    def api_terminal_logs():
        limit = request.args.get("limit", 100)
        level = request.args.get("level", None)
        kw = request.args.get("keyword", None)
        since_id = request.args.get("since_id", None)
        if since_id is not None and str(since_id).isdigit():
            since_id = int(since_id)
        else:
            since_id = None
        return jsonify(terminal_mgr.get_logs(limit=limit, level=level, keyword=kw, since_id=since_id))

    @app.route("/api/terminal/clear", methods=["POST"])
    def api_terminal_clear():
        terminal_mgr.clear()
        return jsonify({"status": "ok", "message": "终端日志输出已清空"})

    # ==================== 购买与抢购记录 API ====================
    @app.route("/api/orders")
    def api_orders():
        status = request.args.get("status", "all")
        trigger_type = request.args.get("trigger_type", "all")
        kw = request.args.get("keyword", "")
        try:
            page = int(request.args.get("page", 1))
        except ValueError:
            page = 1
        try:
            page_size = int(request.args.get("page_size", 20))
        except ValueError:
            page_size = 20
        return jsonify(monitor.archive_db.query_orders(
            status=status,
            trigger_type=trigger_type,
            keyword=kw,
            page=page,
            page_size=page_size
        ))

    @app.route("/api/orders/stats")
    def api_orders_stats():
        return jsonify(monitor.archive_db.get_order_stats())

    @app.route("/api/orders/delete", methods=["POST"])
    def api_orders_delete():
        data = request.json or {}
        order_id = str(data.get("id", "")).strip()
        if not order_id:
            return jsonify({"status": "error", "message": "未指定记录 ID"}), 400
        ok = monitor.archive_db.delete_order(order_id)
        return jsonify({"status": "ok" if ok else "error", "message": "已删除该条抢购记录"})

    @app.route("/api/orders/clear", methods=["POST"])
    def api_orders_clear():
        ok = monitor.archive_db.clear_orders()
        return jsonify({"status": "ok" if ok else "error", "message": "已成功清空全部购买记录"})

    @app.route("/api/orders/export")
    def api_orders_export():
        import io
        import csv
        fmt = request.args.get("format", "csv").lower()
        res = monitor.archive_db.query_orders(page=1, page_size=1000)
        orders = res.get("orders", [])
        if fmt == "json":
            return jsonify(orders)

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["订单ID", "商品ID", "商品名称", "价格", "状态", "运行模式", "触发来源", "支付方式", "执行耗时(ms)", "下单时间", "执行反馈"])
        status_map = {
            "SUCCESS": "真实下单成功",
            "DRY_RUN_SUCCESS": "仿真模拟成功",
            "SOLDOUT": "商品已售罄",
            "FAILED": "下单失败"
        }
        for o in orders:
            mode_str = "仿真模拟(DRY-RUN)" if o.get("dry_run") else "实盘购买"
            status_str = status_map.get(o.get("status"), o.get("status"))
            writer.writerow([
                o.get("id"),
                o.get("item_id"),
                o.get("title"),
                o.get("price_str"),
                status_str,
                mode_str,
                o.get("rule_name") or o.get("trigger_type"),
                o.get("payment_method"),
                o.get("duration_ms"),
                o.get("time_str"),
                o.get("message"),
            ])
        csv_bytes = output.getvalue().encode("utf-8-sig")
        return Response(
            csv_bytes,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=dollyteria_orders.csv"}
        )

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
