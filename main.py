"""
Dollyteria 上新提醒小工具 (Python 主入口)
支持三种运行模式:
1. 默认模式: 启动 Web 管理看板 (http://127.0.0.1:5000) 并自动在浏览器打开，同时后台守护进程进行定点上新与轮询监控
2. --cli 模式: 纯终端字符界面，适合后台运行或服务器无头部署
3. --check 模式: 单次执行检测并打印匹配结果后退出
"""
import sys
import os
import time
import argparse
import webbrowser
import logging
from core.monitor import DollyteriaMonitor
from core.scheduler import DollyteriaScheduler
from web.app import create_app

# 配置控制台 UTF-8 编码，防止 Windows 控制台输出日文时发生 UnicodeEncodeError
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 配置日志格式
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("dollyteria.main")


def run_check_once():
    """单次检测模式"""
    print("=" * 60)
    print("🔍 Dollyteria 上新单次检测")
    print("=" * 60)
    monitor = DollyteriaMonitor()
    result = monitor.check_once()
    print(f"检测状态: {result.get('status')}")
    print(f"扫描候选商品: {result.get('scanned_count', 0)} 件")
    print(f"新命中商品: {result.get('new_items_count', 0)} 件")

    new_items = result.get("new_items", [])
    if new_items:
        print("\n✨ 本次新发现的有货商品列表:")
        for it in new_items:
            print(f" • [{it['id']}] {it['price_str']} | {it['title']}")
            print(f"   链接: {it['url']}")
    else:
        print("\n暂无未记录的全新商品。")


def run_cli_mode():
    """终端控制台监控循环模式"""
    print("=" * 60)
    print("🚀 Dollyteria 上新提醒小工具 - CLI 控制台模式已启动")
    print("=" * 60)
    monitor = DollyteriaMonitor()
    scheduler = DollyteriaScheduler(monitor)
    scheduler.start()

    print("监控已在后台运行中，按 Ctrl + C 即可退出。")
    try:
        while True:
            time.sleep(2)
    except KeyboardInterrupt:
        print("\n正在停止调度器...")
        scheduler.stop()
        print("已退出。")


def run_web_mode(host: str = "0.0.0.0", port: int = 5000, no_browser: bool = False):
    """Web 控制台模式 (默认)"""
    print("=" * 60)
    print("🎀 Dollyteria 上新提醒助手 - Web 控制看板已启动")
    print(f"🌐 服务访问地址: http://{host if host != '0.0.0.0' else '127.0.0.1'}:{port}")
    print("=" * 60)

    monitor = DollyteriaMonitor()
    scheduler = DollyteriaScheduler(monitor)
    scheduler.start()

    app = create_app(monitor, scheduler)

    # 优雅停机信号捕获 (容器环境下 Zeabur 发送 SIGTERM)
    import signal
    def handle_sigterm(signum, frame):
        logger.info("接收到终止信号，正在优雅关闭调度器...")
        scheduler.stop()
        sys.exit(0)

    try:
        signal.signal(signal.SIGTERM, handle_sigterm)
    except Exception:
        pass

    # 仅在非容器环境且允许打开浏览器的 Windows 桌面自动弹出
    is_docker = os.path.exists("/.dockerenv") or os.environ.get("DOCKER") == "1" or os.environ.get("ZEABUR") == "1"
    should_open_browser = not no_browser and not is_docker and sys.platform == "win32"

    if should_open_browser:
        def open_browser():
            time.sleep(1.2)
            try:
                webbrowser.open(f"http://127.0.0.1:{port}")
            except Exception:
                pass
        import threading
        threading.Thread(target=open_browser, daemon=True).start()

    try:
        # 关闭 werkzeug 的默认访问日志，保持控制台整洁
        log = logging.getLogger("werkzeug")
        log.setLevel(logging.WARNING)

        app.run(host=host, port=port, debug=False, use_reloader=False)
    except (KeyboardInterrupt, SystemExit):
        print("\n正在停止调度器...")
        scheduler.stop()
        print("已安全退出。")


def main():
    env_port = int(os.environ.get("PORT", 5000))
    env_host = os.environ.get("HOST", "0.0.0.0")
    env_no_browser = os.environ.get("NO_BROWSER", "0").lower() in ["1", "true", "yes"]

    parser = argparse.ArgumentParser(description="Dollyteria 上新提醒小工具")
    parser.add_argument("--check", action="store_true", help="单次检测并退出")
    parser.add_argument("--cli", action="store_true", help="以终端控制台交互模式运行")
    parser.add_argument("--host", type=str, default=env_host, help=f"Web 绑定地址 (默认 {env_host})")
    parser.add_argument("--port", type=int, default=env_port, help=f"Web 看板端口 (默认 {env_port})")
    parser.add_argument("--no-browser", action="store_true", default=env_no_browser, help="不自动弹出打开浏览器")

    args = parser.parse_args()

    if args.check:
        run_check_once()
    elif args.cli:
        run_cli_mode()
    else:
        run_web_mode(host=args.host, port=args.port, no_browser=args.no_browser)


if __name__ == "__main__":
    main()
