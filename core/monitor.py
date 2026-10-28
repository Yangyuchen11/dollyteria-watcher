"""
监控核心引擎 (Monitor Engine)
负责规则匹配、去重缓存、有货过滤、触发调度与状态持久化
"""
import os
import json
import time
import datetime
import logging
import threading
import concurrent.futures
from typing import List, Dict, Set, Optional, Tuple
import requests
from core.scraper import DollyteriaScraper
from core.archive_db import DollyteriaArchiveDB
from core.archiver import DollyteriaArchiver
from notifiers.manager import NotifierManager
from hooks.auto_order import AutoOrderHook

logger = logging.getLogger("dollyteria.monitor")

DATA_DIR = os.environ.get("DATA_DIR", "").strip()
if DATA_DIR:
    os.makedirs(DATA_DIR, exist_ok=True)
    CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
    STATE_PATH = os.path.join(DATA_DIR, "state.json")
    ARCHIVE_DB_PATH = os.path.join(DATA_DIR, "archive.db")
    # 若挂载的持久化目录中尚无配置文件，自动从项目根目录复制一份已有的模版
    default_template = os.path.abspath("config.json")
    if not os.path.exists(CONFIG_PATH) and os.path.exists(default_template):
        try:
            import shutil
            shutil.copy2(default_template, CONFIG_PATH)
            logger.info(f"已自动从模版初始化配置文件至持久化卷: {CONFIG_PATH}")
        except Exception as e:
            logger.warning(f"初始化持久化配置文件异常: {e}")
else:
    CONFIG_PATH = os.path.abspath(os.environ.get("CONFIG_PATH", "config.json"))
    STATE_PATH = os.path.abspath(os.environ.get("STATE_PATH", "data/state.json"))
    ARCHIVE_DB_PATH = os.path.abspath(os.environ.get("ARCHIVE_DB_PATH", "data/archive.db"))

DEFAULT_CONFIG = {
    "watches": [
        {
            "id": "w_blythe",
            "name": "Blythe 小布系列",
            "keywords": ["ブライス", "Blythe"],
            "exclude_keywords": ["難あり", "ジャンク"],
            "min_price": 0,
            "max_price": 0,
            "enabled": True,
            "search_mode": "both",  # "new_stream", "keyword_search", "both"
            "match_mode": "any",    # "all": 多个词同时全部满足 (AND); "any": 满足任一词即可 (OR)
        },
        {
            "id": "w_sdm_of",
            "name": "SDM / MSD 娃衣 (多词同时满足)",
            "keywords": ["SDM", "OF"],
            "exclude_keywords": ["難あり", "ジャンク"],
            "min_price": 0,
            "max_price": 0,
            "enabled": True,
            "search_mode": "both",
            "match_mode": "all",    # 必须同时包含 SDM 和 OF
        },
    ],
    "schedule": {
        "enabled": True,
        "drop_times": ["20:00:00", "21:00:00"],  # 定点准时触发时间 (支持多个)
        "burst_duration_sec": 180,                 # 触发后密集轮询持续秒数
        "burst_interval_sec": 0.2,                 # 密集轮询间隔 (秒)
        "burst_probe_timeout_sec": 2.0,            # 爆发期单次探针快速超时上限 (秒)
        "interval_minutes": 5,                     # 日常平缓轮询间隔 (分钟)
        "interval_enabled": True,                  # 是否开启日常平缓轮询
        "max_stream_pages": 5,                     # 上新流最大翻页深度 (每页100件，默认5页覆盖500件)
    },
    "notifications": {
        "sound_enabled": True,
        "desktop_enabled": True,
        "discord": {
            "enabled": False,
            "webhook_url": "",
        },
        "email": {
            "enabled": False,
            "smtp_host": "",
            "smtp_port": 465,
            "smtp_user": "",
            "smtp_password": "",
            "receiver_email": "",
            "use_ssl": True,
        },
    },
    "auto_order": {
        "enabled": False,
        "dry_run": False,
        "username": "",
        "password": "",
        "payment_method": "cod",
        "max_workers": 6,
        "standby_workers": 2,
        "retry_delay_sec": 0.15,
        "max_retries": 35,
    },
    "quick_buy": {
        "enabled": False,
        "dry_run": True,
    },
    "web_password": "",
}



class TriProbeBurstEngine:
    """
    三路协同并发爆发探测引擎 (Tri-Probe Burst Engine)
    针对定点放货高峰期官网 503 限流与长连接挂起卡死设计的并发探测集群：
    - 探针 A (BurstWorker_A_Full100)：单次拉取 100 件，timeout=2.0s，独立 Session A，全量保底覆盖与大深度溢出翻页
    - 探针 B (BurstWorker_B_Fast30)：单次极速拉取 30 件，timeout=2.0s，独立 Session B，先锋突击
    - 探针 C (BurstWorker_C_Fast30)：单次极速拉取 30 件，timeout=2.0s，独立 Session C，与探针 B 错峰交替探测
    - 共享内存原子排重 (process_stream_items)：A 与 BC 互相对照校验，先到先抢，绝不漏单，绝不重单！
    """
    def __init__(self, monitor: "DollyteriaMonitor"):
        self.monitor = monitor
        self.running = False
        self._threads: List[threading.Thread] = []
        self._stop_event = threading.Event()
        self.burst_end_time = 0.0
        self._drop_active = False
        self._drop_detected_time = 0.0

    def notify_drop_detected(self):
        """通知引擎检测到官网上新发生，驱动探针C顺延深挖"""
        self._drop_active = True
        self._drop_detected_time = time.time()

    def start(self, duration_sec: float = 180):
        if self.running:
            self.burst_end_time = max(self.burst_end_time, time.time() + duration_sec)
            return

        self.running = True
        self._stop_event.clear()
        self.burst_end_time = time.time() + duration_sec
        timeout_sec = float(self.monitor.config.get("schedule", {}).get("burst_probe_timeout_sec", 2.0))

        logger.info(
            f"🚀 [三路协同探测] 启动爆发期极速探测引擎 (持续 {int(duration_sec)} 秒)！\n"
            f"  ├── 探针 A: 100 件全量保底 (timeout={timeout_sec}s, Session A, 覆盖大批量上新)\n"
            f"  ├── 探针 B: 30 件轻量先锋 (timeout={timeout_sec}s, Session B, 毫秒级极速突击)\n"
            f"  └── 探针 C: 30 件交替错峰 (timeout={timeout_sec}s, Session C, 错峰互锁防挂死)"
        )

        self._threads = [
            threading.Thread(target=self._worker_a_heavy, name="BurstWorker_A_Full100", daemon=True),
            threading.Thread(target=self._worker_b_light, name="BurstWorker_B_Fast30", daemon=True),
            threading.Thread(target=self._worker_c_light, name="BurstWorker_C_Fast30", daemon=True),
        ]
        for t in self._threads:
            t.start()

    def stop(self):
        if not self.running:
            return
        self.running = False
        self._stop_event.set()
        logger.info("🛑 [三路协同探测] 正在停止爆发期探针 A/B/C...")
        for t in self._threads:
            t.join(timeout=0.8)
        self._threads.clear()
        logger.info("✅ [三路协同探测] 探针 A/B/C 已全部安全退出。")

    def _worker_a_heavy(self):
        """探针 A：全量保底 100 件，保证任何深度的商品不漏"""
        session_a = requests.Session()
        session_a.headers.update(self.monitor.scraper.session.headers)
        timeout_sec = float(self.monitor.config.get("schedule", {}).get("burst_probe_timeout_sec", 2.0))
        interval = float(self.monitor.config.get("schedule", {}).get("burst_interval_sec", 0.2))

        while not self._stop_event.is_set() and time.time() < self.burst_end_time:
            t0 = time.time()
            try:
                logger.info(f"⚡ [探针A-全量保底] 正在抓取 /new 最新 100 件商品流 (timeout={timeout_sec}s)...")
                items = self.monitor.scraper.fetch_new_arrivals(
                    num=100, page=1, in_stock_only=True,
                    timeout=timeout_sec, session=session_a, max_retries=0
                )
                if items:
                    self.monitor.process_stream_items(items, source_tag="探针A-100件", is_burst=True)
                    if len(items) >= 100 and not self._stop_event.is_set():
                        try:
                            items_p2 = self.monitor.scraper.fetch_new_arrivals(
                                num=100, page=2, in_stock_only=True,
                                timeout=timeout_sec, session=session_a, max_retries=0
                            )
                            if items_p2:
                                self.monitor.process_stream_items(items_p2, source_tag="探针A-第2页", is_burst=True)
                        except Exception as e:
                            logger.debug(f"⚠️ [探针A-第2页] 抓取异常: {e}")
                else:
                    logger.debug("⚠️ [探针A] 未能获取数据 (503/超时)，准备下一轮重发...")
            except Exception as e:
                logger.debug(f"⚠️ [探针A] 请求异常: {e}")

            elapsed = time.time() - t0
            sleep_time = max(0.05, interval - elapsed)
            if self._stop_event.wait(timeout=sleep_time):
                break

        try:
            session_a.close()
        except Exception:
            pass

    def _worker_b_light(self):
        """探针 B：轻量先锋 30 件，以极低服务端负载毫秒级抢占最新放出商品"""
        session_b = requests.Session()
        session_b.headers.update(self.monitor.scraper.session.headers)
        timeout_sec = float(self.monitor.config.get("schedule", {}).get("burst_probe_timeout_sec", 2.0))
        interval = float(self.monitor.config.get("schedule", {}).get("burst_interval_sec", 0.2))

        while not self._stop_event.is_set() and time.time() < self.burst_end_time:
            t0 = time.time()
            try:
                logger.info(f"⚡ [探针B-死守前30] 正在极速抓取 /new 第 1 页 (1~30件, timeout={timeout_sec}s)...")
                items = self.monitor.scraper.fetch_new_arrivals(
                    num=30, page=1, in_stock_only=True,
                    timeout=timeout_sec, session=session_b, max_retries=0
                )
                if items:
                    self.monitor.process_stream_items(items, source_tag="探针B-30件", is_burst=True)
                else:
                    logger.debug("⚠️ [探针B] 未能获取数据，准备下一轮...")
            except Exception as e:
                logger.debug(f"⚠️ [探针B] 请求异常: {e}")

            elapsed = time.time() - t0
            sleep_time = max(0.05, interval - elapsed)
            if self._stop_event.wait(timeout=sleep_time):
                break

        try:
            session_b.close()
        except Exception:
            pass

    def _worker_c_light(self):
        """
        探针 C：轻量交替与顺延探针
        - 平常与探针 B 错峰交替死守第 1 页 (1~30件)
        - 一旦捕获到上新 (或系统检测到放货)，探针 C 立即顺延往后拉取第 2 页 (31~60件)、第 3 页 (61~90件)...
        - 顺延深挖全部接驳完毕后，自动返回第 1 页继续与探针 B 错峰守候
        """
        session_c = requests.Session()
        session_c.headers.update(self.monitor.scraper.session.headers)
        timeout_sec = float(self.monitor.config.get("schedule", {}).get("burst_probe_timeout_sec", 2.0))
        interval = float(self.monitor.config.get("schedule", {}).get("burst_interval_sec", 0.2))
        stagger = max(0.08, interval / 2.0)
        current_page = 1
        max_c_pages = int(self.monitor.config.get("schedule", {}).get("max_stream_pages", 5))

        # 启动时先错开半个周期
        if self._stop_event.wait(timeout=stagger):
            return

        while not self._stop_event.is_set() and time.time() < self.burst_end_time:
            t0 = time.time()
            try:
                item_start = (current_page - 1) * 30 + 1
                item_end = current_page * 30

                if current_page == 1:
                    logger.info(f"⚡ [探针C-交替前30] 正在抓取 /new 第 1 页 (1~30件, timeout={timeout_sec}s)...")
                else:
                    logger.info(f"⚡ [探针C-顺延深挖] 正在顺延抓取 /new 第 {current_page} 页 ({item_start}~{item_end}件, timeout={timeout_sec}s)...")

                items = self.monitor.scraper.fetch_new_arrivals(
                    num=30, page=current_page, in_stock_only=True,
                    timeout=timeout_sec, session=session_c, max_retries=0
                )

                if items:
                    tag = "探针C-第1页(1~30件)" if current_page == 1 else f"探针C-第{current_page}页({item_start}~{item_end}件)"
                    matches = self.monitor.process_stream_items(items, source_tag=tag, is_burst=True)

                    # 判断是否继续顺延翻页：
                    # 1. 如果在第 1 页发现上新 (或最近 30 秒内有放货被记录) 且当前页满 30 件，立即启动顺延进入第 2 页
                    # 2. 如果在第 2 页及以后且该页满 30 件，继续顺延至下一页
                    is_drop_active = self._drop_active and (time.time() - self._drop_detected_time < 45.0)

                    if current_page == 1:
                        if (matches or is_drop_active) and len(items) >= 30 and max_c_pages > 1:
                            logger.info(f"🔄 [探针C-顺延启动] 第 1 页已取到并确认上新流，探针C立即顺延向后拉取第 2 页 (31~60件)...")
                            current_page = 2
                        else:
                            current_page = 1
                    else:
                        # 处于顺延翻页阶段 (第 2 页及以后)
                        if len(items) >= 30 and current_page < max_c_pages:
                            current_page += 1
                            logger.info(f"🔄 [探针C-继续顺延] 第 {current_page - 1} 页仍然满额，继续顺延抓取第 {current_page} 页 ({(current_page - 1)*30 + 1}~{current_page*30}件)...")
                        else:
                            logger.info(f"🏁 [探针C-顺延完成] 顺延深挖至第 {current_page} 页已全部接驳完毕，探针C回到第 1 页继续与探针B交替守候。")
                            current_page = 1
                            self._drop_active = False
                else:
                    logger.debug(f"⚠️ [探针C-第{current_page}页] 未能获取数据 (503/超时)")
                    # 深度翻页遇阻时回到第 1 页保底
                    if current_page > 1:
                        current_page = 1

            except Exception as e:
                logger.debug(f"⚠️ [探针C] 请求异常: {e}")
                if current_page > 1:
                    current_page = 1

            elapsed = time.time() - t0
            sleep_time = max(0.05, interval - elapsed)
            if self._stop_event.wait(timeout=sleep_time):
                break

        try:
            session_c.close()
        except Exception:
            pass


class DollyteriaMonitor:
    def __init__(self):
        self.scraper = DollyteriaScraper()
        self.archive_db = DollyteriaArchiveDB(ARCHIVE_DB_PATH)
        self.archiver = DollyteriaArchiver(self.archive_db, self.scraper)
        self.config = self._load_config()
        self.state = self._load_state()

        self.notifier = NotifierManager(self.config)
        ao_cfg = self.config.get("auto_order", {})
        self.auto_order = AutoOrderHook(
            username=ao_cfg.get("username", ""),
            password=ao_cfg.get("password", ""),
            dry_run=ao_cfg.get("dry_run", False),
            payment_method=ao_cfg.get("payment_method", "cod"),
            retry_delay=float(ao_cfg.get("retry_delay_sec", 0.15)),
            max_retries=int(ao_cfg.get("max_retries", 35)),
            standby_workers=int(ao_cfg.get("standby_workers", 2)),
        )

        self.is_checking = False
        self.last_check_time = 0
        self.last_check_status = "就绪"
        self._incomplete_from_page = 0
        self._pending_reorders: Dict[str, Dict] = {}
        self._reorder_attempt_counts: Dict[str, int] = {}
        self._reorder_lock = threading.Lock()
        self._stream_lock = threading.Lock()

        # 三路协同并发爆发探测引擎 (A:100件保底 | B:30件先锋 | C:30件交替)
        self._burst_engine = TriProbeBurstEngine(self)

        # 异步非阻塞下单常驻线程池 (抓取与抢购彻底解耦)
        max_workers = int(ao_cfg.get("max_workers", 6))
        self._order_executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="AutoOrderWorker")

        # 若配置了账号密码，系统启动时在后台自动预登录并保持会话与备用池
        if self.auto_order.username and self.auto_order.password:
            def _startup_login():
                ok, _ = self.auto_order.login()
                if ok:
                    self.auto_order.preload_standby_workers()
            threading.Thread(target=_startup_login, name="AutoOrderStartupLogin", daemon=True).start()

    def _load_config(self) -> Dict:
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    # 合并默认值
                    for k, v in DEFAULT_CONFIG.items():
                        if k not in cfg:
                            cfg[k] = v
                    return cfg
            except Exception as e:
                logger.error(f"读取配置文件失败，使用默认配置: {e}")
        elif os.path.exists("config.example.json"):
            try:
                with open("config.example.json", "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    for k, v in DEFAULT_CONFIG.items():
                        if k not in cfg:
                            cfg[k] = v
                    self._save_config(cfg)
                    logger.info(f"已自动从 config.example.json 模版初始化新配置: {CONFIG_PATH}")
                    return cfg
            except Exception as e:
                logger.error(f"从模板读取配置失败: {e}")
        self._save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG

    def _save_config(self, cfg: Optional[Dict] = None):
        cfg = cfg or self.config
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存配置文件失败: {e}")

    def _load_state(self) -> Dict:
        today_str = datetime.date.today().isoformat()
        default_state = {
            "seen_ids": [],
            "history": [],
            "stats": {
                "total_checks": 0,
                "today_checks": 0,
                "today_date": today_str,
                "total_matches": 0,
            },
        }
        if os.path.exists(STATE_PATH):
            try:
                with open(STATE_PATH, "r", encoding="utf-8") as f:
                    state = json.load(f)
                    stats = state.setdefault("stats", {})
                    if stats.get("today_date") != today_str:
                        stats["today_date"] = today_str
                        stats["today_checks"] = 0
                    return state
            except Exception as e:
                logger.error(f"读取状态文件失败: {e}")
        return default_state

    def get_stats(self) -> Dict:
        """获取统计数据，自动检测跨天并在每日 0 点清零今日检测轮次"""
        stats = self.state.setdefault("stats", {})
        today_str = datetime.date.today().isoformat()
        if stats.get("today_date") != today_str:
            stats["today_date"] = today_str
            stats["today_checks"] = 0
            self._save_state()
        return stats

    def _save_state(self):
        try:
            os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
            # 保留最近 80000 个 seen_id
            if len(self.state.get("seen_ids", [])) > 80000:
                self.state["seen_ids"] = self.state["seen_ids"][-80000:]
            # 保留最近 100 条历史记录
            if len(self.state.get("history", [])) > 100:
                self.state["history"] = self.state["history"][:100]

            with open(STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存状态文件失败: {e}")

    def update_config(self, new_cfg: Dict):
        """更新配置并同步组件"""
        self.config = new_cfg
        self._save_config(new_cfg)
        self.notifier.update_config(new_cfg.get("notifications", {}))
        ao_cfg = new_cfg.get("auto_order", {})
        self.auto_order.username = ao_cfg.get("username", "")
        self.auto_order.password = ao_cfg.get("password", "")
        self.auto_order.dry_run = ao_cfg.get("dry_run", False)
        self.auto_order.payment_method = ao_cfg.get("payment_method", "cod")
        self.auto_order.retry_delay = float(ao_cfg.get("retry_delay_sec", 0.15))
        self.auto_order.max_retries = int(ao_cfg.get("max_retries", 35))
        self.auto_order.standby_workers_count = int(ao_cfg.get("standby_workers", 2))

        # 实时同步 reCAPTCHA v3 预热浏览器标签页池大小与备用会话数一致
        try:
            from hooks.auto_order import RecaptchaTokenProvider
            RecaptchaTokenProvider.get_instance().set_pool_size(self.auto_order.standby_workers_count)
        except Exception:
            pass

        # 实时同步至已有备用 Worker 实例
        with self.auto_order._pool_lock:
            for w in self.auto_order._standby_workers:
                w.retry_delay = self.auto_order.retry_delay
                w.max_retries = self.auto_order.max_retries
                w.dry_run = self.auto_order.dry_run
                w.payment_method = self.auto_order.payment_method

        # 动态同步抢购并发线程池大小
        new_max_workers = int(ao_cfg.get("max_workers", 6))
        if hasattr(self._order_executor, "_max_workers") and self._order_executor._max_workers != new_max_workers:
            try:
                self._order_executor._max_workers = new_max_workers
            except Exception:
                pass

        if self.auto_order.username and self.auto_order.password:
            def _update_login():
                ok, _ = self.auto_order.login()
                if ok:
                    self.auto_order.preload_standby_workers()
            threading.Thread(target=_update_login, name="AutoOrderUpdateLogin", daemon=True).start()

    def reset_seen_items(self):
        """重置已记录商品 (下次检测将视全部匹配商品为新商品)"""
        self.state["seen_ids"] = []
        with self._reorder_lock:
            self._pending_reorders.clear()
            self._reorder_attempt_counts.clear()
        self._save_state()
        logger.info("已清空历史已记录商品集合与待续抢队列")

    def initialize_baseline(self):
        """
        基准初始化：将当前线上已有的商品全部记录为已见，
        避免首次启动时因已有库存而触发大量报警
        """
        logger.info("正在建立当前库存基准线，抓取当前商品...")
        seen_set = set(self.state.get("seen_ids", []))
        try:
            # 抓取当前最新的上新流 (前 5 页，覆盖最多 500 件在售商品)
            for p in range(1, 6):
                items = self.scraper.fetch_new_arrivals(num=100, page=p, in_stock_only=True)
                if not items:
                    break
                for it in items:
                    seen_set.add(it["id"])
                self.archive_db.upsert_batch(items)
                if len(items) < 100:
                    break

            # 抓取所有监控规则的搜索结果第 1 页
            for rule in self.config.get("watches", []):
                if not rule.get("enabled", True):
                    continue
                match_mode = rule.get("match_mode", "all")
                keywords = [k.strip() for k in rule.get("keywords", []) if k.strip()]
                if match_mode == "all":
                    combined_kw = " ".join(keywords)
                    if combined_kw:
                        search_items = self.scraper.search_products(combined_kw, num=100, page=1, in_stock_only=True)
                        for sit in search_items:
                            seen_set.add(sit["id"])
                        if search_items:
                            self.archive_db.upsert_batch(search_items)
                else:
                    for kw in keywords:
                        search_items = self.scraper.search_products(kw, num=100, page=1, in_stock_only=True)
                        for sit in search_items:
                            seen_set.add(sit["id"])
                        if search_items:
                            self.archive_db.upsert_batch(search_items)

            # 如果归档库已有在售商品，也一并加入基准线
            archive_in_stock = self.archive_db.get_in_stock_ids()
            for aid in archive_in_stock:
                seen_set.add(aid)

            self.state["seen_ids"] = list(seen_set)
            self._save_state()
            logger.info(f"基准线建立完毕！已记录 {len(seen_set)} 件已有商品，后续仅提醒新上架/新补货商品。")
        except Exception as e:
            logger.error(f"建立基准线时异常: {e}")

    def apply_archive_baseline(self) -> int:
        """将归档数据库中所有在售商品同步为监控基准线"""
        in_stock_ids = self.archive_db.get_in_stock_ids()
        if not in_stock_ids:
            return 0
        seen_set = set(self.state.get("seen_ids", []))
        seen_set.update(in_stock_ids)
        self.state["seen_ids"] = list(seen_set)
        self._save_state()
        logger.info(f"已将归档库中 {len(in_stock_ids)} 件在售商品同步为监控基准线，当前基准总数: {len(seen_set)}")
        return len(in_stock_ids)

    def _matches_rule(self, item: Dict, rule: Dict) -> bool:
        """检查单件商品是否完全命中规则"""
        # 1. 检查是否在库
        if not item.get("is_in_stock", True):
            return False

        title = item.get("title", "")
        model = item.get("model_number", "")
        full_text = f"{title} {model}".lower()

        # 2. 检查排除词 (命中任一排除词则丢弃)
        exclude_keywords = [k.strip().lower() for k in rule.get("exclude_keywords", []) if k.strip()]
        for ek in exclude_keywords:
            if ek in full_text:
                return False

        # 3. 检查正向关键词 (支持 match_mode="all" 多个词同时全部满足，或 "any" 满足任一词)
        keywords = [k.strip().lower() for k in rule.get("keywords", []) if k.strip()]
        match_mode = rule.get("match_mode", "all")
        if keywords:
            if match_mode == "all":
                # 多个词必须同时全部满足 (AND)
                for kw in keywords:
                    parts = kw.split()
                    if not all(p in full_text for p in parts):
                        return False
            else:
                # 满足任一词即可 (OR)
                def _kw_in_text(kw_str):
                    parts = kw_str.split()
                    return all(p in full_text for p in parts)

                matched_kw = any(_kw_in_text(k) for k in keywords)
                if not matched_kw:
                    return False

        # 4. 检查价格区间
        price = item.get("price", 0)
        min_p = rule.get("min_price", 0) or 0
        max_p = rule.get("max_price", 0) or 0
        if min_p > 0 and price < min_p:
            return False
        if max_p > 0 and price > max_p:
            return False

        return True

    def _execute_auto_orders_concurrent(self, order_tasks: List[Tuple[Dict, Dict]], wait_completion: bool = False) -> None:
        """
        多线程并发抢购不同商品 (异步非阻塞流水线)：
        order_tasks: [(item, rule), ...]
        每个商品获取一个独立的 Worker (独立 Session/Cookie) 在独立线程中并发抢购，
        遇到 503 迅速微重试直到售罄，各商品互不阻塞。
        wait_completion: 默认为 False，即提交至常驻线程池后立即返回，让主监控/抓取线程毫无阻碍地继续发起下一轮 /new 抓取！
        """
        if not order_tasks:
            return

        def _worker_task(item: Dict, rule: Dict):
            worker = None
            try:
                worker = self.auto_order.get_worker()
                logger.info(f"⚡ [多线程抢购启动] 线程 [{threading.current_thread().name}] 开始抢购商品 [{item.get('id')}] {item.get('title', '')}...")
                order_res = worker.on_item_matched(item, rule)
                pid = str(item.get("id", "")).strip()

                # 记录购买与抢购历史
                if order_res:
                    try:
                        order_rec = self.archive_db.record_order(
                            item=item,
                            rule_name=rule.get("name", "监控规则"),
                            trigger_type="auto",
                            order_res=order_res,
                        )
                        for listener in list(getattr(self.notifier, "listeners", [])):
                            try:
                                listener({"type": "order_recorded", "order": order_rec})
                            except Exception:
                                pass
                    except Exception as e:
                        logger.error(f"写入自动下单购买记录异常: {e}")
                if order_res and (
                    (order_res.get("success") and not order_res.get("dry_run"))
                    or order_res.get("is_sold_out")
                ):
                    with self._reorder_lock:
                        self._pending_reorders.pop(pid, None)
                        self._reorder_attempt_counts.pop(pid, None)
                    if pid:
                        try:
                            self.archive_db.mark_sold_out(pid)
                            for h in self.state.get("history", []):
                                if str(h.get("id", "")) == pid:
                                    h["is_in_stock"] = False
                            for listener in list(getattr(self.notifier, "listeners", [])):
                                try:
                                    listener({"type": "item_sold_out", "item_id": pid})
                                except Exception:
                                    pass
                        except Exception as e:
                            logger.debug(f"同步商品 [{pid}] 售罄状态异常: {e}")
                elif order_res and not order_res.get("success") and not order_res.get("is_sold_out"):
                    # 本轮抢购重试耗尽，但官网尚未售罄 (如 503 持续时间超过了 max_retries 或瞬时网络抖动)
                    # 自动加入无限续抢队列，下一轮巡检只要该商品仍在库，将不设上限无限次自动继续尝试购买！
                    if pid:
                        with self._reorder_lock:
                            count = self._reorder_attempt_counts.get(pid, 0) + 1
                            self._reorder_attempt_counts[pid] = count
                            self._pending_reorders[pid] = {
                                "item": item,
                                "rule": rule,
                                "attempts": count,
                                "last_failed_at": time.time(),
                            }
                            logger.warning(
                                f"🔁 [Auto-Order 无限续抢机制] 商品 [{pid}] 本轮重试耗尽但官网尚未售罄！"
                                f"已自动加入续抢待命队列 (累计续抢轮次: 第 {count} 轮)，不设上限无限次自动续抢，直至抢购成功或官方售罄！"
                            )
                if order_res and order_res.get("success"):
                    try:
                        self.notifier.dispatch_order_success(item=item, order_result=order_res, rule=rule)
                    except Exception as e:
                        logger.error(f"分发下单成功通知异常: {e}")
                return item.get("id"), order_res
            except Exception as e:
                logger.error(f"商品 [{item.get('id')}] 抢购线程执行异常: {e}", exc_info=True)
                return item.get("id"), {"success": False, "message": str(e)}
            finally:
                if worker:
                    self.auto_order.release_worker(worker)

        logger.info(f"⚡⚡⚡ [多商品并发抢购] 触发 {len(order_tasks)} 件不同商品的多线程并发抢购 (异步非阻塞流水线)！")
        futures = [self._order_executor.submit(_worker_task, item, rule) for item, rule in order_tasks]
        if wait_completion:
            concurrent.futures.wait(futures, timeout=45)

    def process_stream_items(self, items: List[Dict], source_tag: str = "A", is_burst: bool = False) -> List[Dict]:
        """
        线程安全地处理抓取到的商品列表 (供三路探针 A/B/C 并发比对与排重)：
        1. 快速检查是否已有全新商品 (基于 seen_ids 内存原子排重)
        2. 对全新商品执行规则匹配
        3. 命中规则零延迟立即触发非阻塞多线程并发自动抢购流水线
        4. 分发通知提醒、持久化历史记录与 seen_ids
        """
        if not items:
            return []

        with self._stream_lock:
            seen_set = set(str(sid) for sid in self.state.get("seen_ids", []))
            active_rules = [r for r in self.config.get("watches", []) if r.get("enabled", True)]
            if not active_rules:
                return []

            page_new_items = []
            for it in items:
                it_id = str(it.get("id", "")).strip()
                if it_id and it_id not in seen_set:
                    page_new_items.append(it)

            if not page_new_items:
                return []

            if hasattr(self, "_burst_engine") and self._burst_engine:
                self._burst_engine.notify_drop_detected()

            stage_matches = []
            for it in page_new_items:
                matched_rule = None
                for rule in sorted(active_rules, key=lambda r: not r.get("auto_order", False)):
                    if not is_burst and rule.get("search_mode", "both") == "keyword_search":
                        continue
                    if self._matches_rule(it, rule):
                        matched_rule = rule
                        break
                if matched_rule:
                    stage_matches.append((it, matched_rule))

            if stage_matches:
                logger.info(f"✨ [/new 实时流命中 ({source_tag})] 发现 {len(stage_matches)} 件符合规则的有货新商品！")

                if self.config.get("auto_order", {}).get("enabled", False):
                    auto_order_tasks = [(it, r) for it, r in stage_matches if r.get("auto_order", False)]
                    if auto_order_tasks:
                        logger.info(f"⚡ [流式立即下单 ({source_tag})] 触发 {len(auto_order_tasks)} 件新商品的自动抢购流水线...")
                        self._execute_auto_orders_concurrent(auto_order_tasks)
                    non_ao_rules = {r.get("name") for it, r in stage_matches if not r.get("auto_order", False)}
                    for nr in non_ao_rules:
                        logger.info(f"ℹ️ 规则 [{nr}] 专属自动购买开关为关闭状态，仅执行消息提醒，不触发自动下单")

                rule_groups = {}
                for it, r in stage_matches:
                    rule_groups.setdefault(r["id"], {"rule": r, "items": []})["items"].append(it)
                for r_data in rule_groups.values():
                    try:
                        self.notifier.dispatch(r_data["items"], r_data["rule"])
                    except Exception as e:
                        logger.error(f"分发通知异常: {e}")

                for it, r in stage_matches:
                    it_id = str(it.get("id", "")).strip()
                    seen_set.add(it_id)
                    hist_entry = dict(it)
                    hist_entry["rule_name"] = r.get("name")
                    self.state.setdefault("history", []).insert(0, hist_entry)

                self.state["seen_ids"] = list(seen_set)
                self._save_state()
                try:
                    self.archive_db.upsert_batch([it for it, r in stage_matches])
                except Exception as e:
                    logger.warning(f"同步流式命中商品至归档库失败: {e}")

            for it in page_new_items:
                it_id = str(it.get("id", "")).strip()
                if it_id:
                    seen_set.add(it_id)

            self.state["seen_ids"] = list(seen_set)
            return [it for it, r in stage_matches]

    def start_burst_probes(self, duration_sec: float = 180):
        """启动三路协同爆发探测引擎 (A:100件保底 | B:30件先锋 | C:30件交替)"""
        self._burst_engine.start(duration_sec)

    def stop_burst_probes(self):
        """停止三路协同爆发探测引擎"""
        self._burst_engine.stop()

    def is_burst_probes_running(self) -> bool:
        """检查三路协同爆发探测引擎是否正在运行"""
        return self._burst_engine.running

    def check_once(self, is_burst: bool = False) -> Dict:
        """
        执行一次完整的巡检
        - 流式即时抢购 (Pipeline)：抓取 /new 后立即在本地内存匹配规则，发现有货新商品毫秒级直接触发自动下单，绝不等待后续网络搜索
        - 多线程并发抢购：多件不同商品并发分配独立 Worker (独立 Session/Cookie) 抢购，互不干扰
        - 爆发模式 (is_burst=True)：专攻 /new 高速主干道，跳过全店关键词搜索，杜绝 503 限流
        - 日常平缓模式 (is_burst=False)：/new 抢购处理完后，按需执行关键词全店搜索
        """
        if self.is_checking:
            return {"status": "busy", "message": "上一轮检测正在进行中"}

        self.is_checking = True
        self.last_check_time = int(time.time())
        self.last_check_status = "检测中 (爆发抢购)" if is_burst else "检测中"

        active_rules = [r for r in self.config.get("watches", []) if r.get("enabled", True)]
        if not active_rules:
            self.is_checking = False
            self.last_check_status = "无活跃规则"
            return {"status": "ok", "message": "当前没有已启用的监控规则", "new_items_count": 0}

        seen_set = set(self.state.get("seen_ids", []))
        total_new_matches = []
        pool_items = {}

        try:
            # 1. 首先检查上新流 (/new)，这是定点上新与补货最快出现的地方
            needs_new_stream = is_burst or any(r.get("search_mode", "both") in ["new_stream", "both"] for r in active_rules)

            if needs_new_stream:
                # 针对 Dollyteria 一次上新 300~500 件的大批量上新，采用「逐页流式匹配 + 智能早停」机制：
                # 1. 抓取第 1 页 (最新 100 件) -> 本地内存比对 -> 命中则零延迟直接触发并发抢购与提醒！
                # 2. 检查当前页是否有已记录过的历史商品 (seen_set)：
                #    - 若当前页中途出现了已见商品，说明此次大上新全部在该页截断，无需继续翻页，毫秒级直接结束！
                #    - 若当前页所有商品全部为全新商品 (说明上新数量超过 100 件溢出至下一页)，自动顺延拉取第 2 页、第 3 页 (最高可达 max_stream_pages，默认 5 页即 500 件)
                # 每页一旦匹配到规则立即触发抢购，绝不等待后续页面！
                max_pages = int(self.config.get("schedule", {}).get("max_stream_pages", 5))
                for page_idx in range(1, max_pages + 1):
                    logger.info(f"⚡ 正在抓取 Dollyteria /new 页面最新上新流 (第 {page_idx}/{max_pages} 页, {'爆发抢购' if is_burst else '常规巡检'})...")
                    new_arrivals = self.scraper.fetch_new_arrivals(num=100, page=page_idx, in_stock_only=True)
                    if not new_arrivals:
                        if getattr(self.scraper, "last_fetch_failed", False):
                            self._incomplete_from_page = max(self._incomplete_from_page, page_idx)
                            logger.warning(f"⚠️ /new 第 {page_idx} 页因网络或 503 繁忙未能拉取，已记录断点，下一轮巡检将自动穿透续扫！")
                        else:
                            self._incomplete_from_page = 0
                        break

                    for it in new_arrivals:
                        pool_items[it["id"]] = it

                    # 区分当前页全新商品与已见商品
                    page_new_items = []
                    for it in new_arrivals:
                        if it["id"] not in seen_set:
                            page_new_items.append(it)

                    stage_matches = self.process_stream_items(new_arrivals, source_tag=f"第 {page_idx} 页", is_burst=is_burst)
                    total_new_matches.extend(stage_matches)
                    seen_set = set(str(sid) for sid in self.state.get("seen_ids", []))

                    # 智能早停：若当前页已没有哪怕 1 件全新商品 (整页均为已见历史商品)，说明此次大上新已完全接驳完毕；或当前页已不足 100 件
                    if len(new_arrivals) < 100:
                        self._incomplete_from_page = 0
                        break
                    if len(page_new_items) == 0:
                        if page_idx < self._incomplete_from_page:
                            logger.info(f"🔄 第 {page_idx} 页已见，但上一轮在第 {self._incomplete_from_page} 页曾遇繁忙中断，继续向下穿透扫描...")
                            continue
                        self._incomplete_from_page = 0
                        break
                else:
                    self._incomplete_from_page = 0

            # 2. 关键词全店搜索 (/product-list/?keyword=...)
            # 核心策略：爆发抢购模式下专攻 /new 主干道，完全跳过耗时的关键词全店搜索，杜绝 503 官网限流！
            if is_burst:
                logger.debug("⚡ 处于爆发抢购模式 (Burst Mode)，跳过耗时的关键词全店搜索，专攻 /new 高速主干道。")
            else:
                stage2_pool = {}
                for rule in active_rules:
                    mode = rule.get("search_mode", "both")
                    if mode in ["keyword_search", "both"]:
                        match_mode = rule.get("match_mode", "all")
                        keywords = [k.strip() for k in rule.get("keywords", []) if k.strip()]
                        if match_mode == "all":
                            combined_kw = " ".join(keywords)
                            if combined_kw:
                                logger.info(f"正在进行多词同时满足(AND)搜索: [{combined_kw}] (规则: {rule.get('name')})...")
                                res = self.scraper.search_products(combined_kw, num=100, page=1, in_stock_only=True)
                                for it in res:
                                    pool_items[it["id"]] = it
                                    stage2_pool[it["id"]] = it
                        else:
                            for kw in keywords:
                                logger.info(f"正在搜索关键词: [{kw}] (规则: {rule.get('name')})...")
                                res = self.scraper.search_products(kw, num=100, page=1, in_stock_only=True)
                                for it in res:
                                    pool_items[it["id"]] = it
                                    stage2_pool[it["id"]] = it

                # 阶段二搜索到的商品匹配与触发
                stage2_matches = []
                for it_id, it in stage2_pool.items():
                    if it_id in seen_set:
                        continue
                    matched_rule = None
                    for rule in sorted(active_rules, key=lambda r: not r.get("auto_order", False)):
                        if self._matches_rule(it, rule):
                            matched_rule = rule
                            break
                    if matched_rule:
                        stage2_matches.append((it, matched_rule))

                if stage2_matches:
                    logger.info(f"✨ [全店搜索命中] 发现 {len(stage2_matches)} 件符合规则的有货新商品！")
                    if self.config.get("auto_order", {}).get("enabled", False):
                        auto_order_tasks = [(it, r) for it, r in stage2_matches if r.get("auto_order", False)]
                        if auto_order_tasks:
                            logger.info(f"⚡ [搜索结果下单] 触发 {len(auto_order_tasks)} 件新商品自动抢购流水线...")
                            self._execute_auto_orders_concurrent(auto_order_tasks)
                        non_ao_rules = {r.get("name") for it, r in stage2_matches if not r.get("auto_order", False)}
                        for nr in non_ao_rules:
                            logger.info(f"ℹ️ 规则 [{nr}] 专属自动购买开关为关闭状态，仅执行消息提醒，不触发自动下单")

                    rule_groups = {}
                    for it, r in stage2_matches:
                        rule_groups.setdefault(r["id"], {"rule": r, "items": []})["items"].append(it)
                    for r_data in rule_groups.values():
                        self.notifier.dispatch(r_data["items"], r_data["rule"])

                    for it, r in stage2_matches:
                        seen_set.add(it["id"])
                        hist_entry = dict(it)
                        hist_entry["rule_name"] = r.get("name")
                        self.state.setdefault("history", []).insert(0, hist_entry)
                        total_new_matches.append(it)

            # 同步本次巡检到的所有候选商品至归档数据库
            if pool_items:
                try:
                    self.archive_db.upsert_batch(list(pool_items.values()))
                except Exception as e:
                    logger.warning(f"同步巡检商品至归档库失败: {e}")

            # 3. 自动续抢机制 (Auto-Retry Until Sold Out)：
            # 若之前轮次抢购由于官网 503 持续时间过长用尽了单轮重试次数，但商品依然有货未售罄，
            # 系统会自动在此处接管并自动发起新一轮抢购，死磕到底直到买下或官方售罄！
            if self.config.get("auto_order", {}).get("enabled", False) and self._pending_reorders:
                reorder_tasks = []
                with self._reorder_lock:
                    now = time.time()
                    for re_pid, re_info in list(self._pending_reorders.items()):
                        if (now - re_info.get("last_failed_at", 0)) >= 1.0:
                            if re_pid in pool_items:
                                reorder_tasks.append((re_info["item"], re_info["rule"]))
                                self._pending_reorders.pop(re_pid, None)
                            else:
                                stock_info = self.scraper.check_product_stock(re_pid)
                                if stock_info.get("is_in_stock"):
                                    reorder_tasks.append((re_info["item"], re_info["rule"]))
                                    self._pending_reorders.pop(re_pid, None)
                                else:
                                    logger.info(f"🛑 [Auto-Order 续抢终止] 商品 [{re_pid}] 经单品询盘确认官网已售罄，自动移出续抢队列。")
                                    self._pending_reorders.pop(re_pid, None)
                                    self._reorder_attempt_counts.pop(re_pid, None)
                if reorder_tasks:
                    logger.info(f"⚡⚡ [Auto-Order 自动续抢触发] 发现 {len(reorder_tasks)} 件未售罄商品符合续抢条件，自动发起新一轮抢购！")
                    self._execute_auto_orders_concurrent(reorder_tasks)

            # 更新已见缓存与统计数据
            self.state["seen_ids"] = list(seen_set)
            stats = self.get_stats()
            stats["today_checks"] = stats.get("today_checks", 0) + 1
            stats["total_checks"] = stats.get("total_checks", 0) + 1
            if total_new_matches:
                stats["total_matches"] = stats.get("total_matches", 0) + len(total_new_matches)
            self._save_state()

            self.last_check_status = f"正常完成 (命中 {len(total_new_matches)} 件新商品)"
            return {
                "status": "ok",
                "scanned_count": len(pool_items),
                "new_items_count": len(total_new_matches),
                "new_items": total_new_matches,
                "timestamp": self.last_check_time,
            }
        except Exception as e:
            logger.error(f"执行检测流程出现异常: {e}", exc_info=True)
            self.last_check_status = f"异常: {str(e)}"
            return {"status": "error", "message": str(e)}
        finally:
            self.is_checking = False

    def sync_new_arrivals_to_archive(self, max_pages: int = 20) -> Dict:
        """
        全量同步 /new 最新上新流至商品归档库 (archive.db)
        支持抓取前 N 页（默认 5 页，500 件），包含在售与售罄状态，
        确保大上新的所有新品 100% 完整录入档案与行情库
        """
        logger.info(f"⚡ 开始同步 /new 上新流商品至归档库 (最大 {max_pages} 页)...")
        all_items = []
        seen_pids = set()
        for p in range(1, max_pages + 1):
            try:
                items = self.scraper.fetch_new_arrivals(num=100, page=p, in_stock_only=False)
                if not items:
                    break
                if items[0]["id"] in seen_pids:
                    break
                for it in items:
                    seen_pids.add(it["id"])
                all_items.extend(items)
                if len(items) < 100:
                    break
            except Exception as e:
                logger.error(f"同步上新流第 {p} 页出错: {e}")
                break

        inserted = 0
        updated = 0
        if all_items:
            try:
                inserted, updated = self.archive_db.upsert_batch(all_items)
                self.archive_db.set_new_arrivals_ids([it["id"] for it in all_items])
            except Exception as e:
                logger.error(f"写入归档库异常: {e}")

        logger.info(f"⚡ 上新流归档同步完成: 共获取 {len(all_items)} 件商品 (新增入库: {inserted}, 更新: {updated})")
        return {
            "status": "ok",
            "total_fetched": len(all_items),
            "inserted": inserted,
            "updated": updated,
            "message": f"成功同步 {len(all_items)} 件最新上新商品至档案 (新增 {inserted} 件，更新状态 {updated} 件)",
        }
