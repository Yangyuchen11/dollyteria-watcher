"""
监控核心引擎 (Monitor Engine)
负责规则匹配、去重缓存、有货过滤、触发调度与状态持久化
"""
import os
import re
import json
import time
import random
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
    - 探针 A (BurstWorker_A_Full100)：开局前 12s 尚未破墙时以 30件/页 轻装协防突围，破墙后恢复 100件/页 全量保底覆盖与大深度溢出翻页
    - 探针 B (BurstWorker_B_Fast30)：单次极速拉取 30 件，独立持久 Session B，死守第 1 页毫秒级先锋突击；突破第 1 页且发现新品时立即联动探针 C 直扑第 2 页
    - 探针 C (BurstWorker_C_Fast30)：单次极速拉取 30 件，独立持久 Session C，与探针 B 错峰交替探测，一旦第 1 页破墙立即跃迁拉取第 2、3... 页直至见底
    - 探针 D (BurstWorker_D_Sniper)：当某条监控规则开启「🎯 优先 (priority=True)」时激活，独立持久 Session D，绕开 /new 翻页直接请求 /product-list/?keyword=... 精准直击目标商品
    - 共享内存原子排重 (process_stream_items)：ABCD 四路互相对照校验，先到先抢，绝不漏单，绝不重单！
    """
    def __init__(self, monitor: "DollyteriaMonitor"):
        self.monitor = monitor
        self.running = False
        self._threads: List[threading.Thread] = []
        self._stop_event = threading.Event()
        self.burst_end_time = 0.0
        self._burst_start_time = 0.0
        self._drop_active = False
        self._drop_detected_time = 0.0
        self._first_page_broken = False
        self._page1_cascade_ready = False
        self.baseline_seen_ids: Set[str] = set()
        # 持久复用的独立探针连接池 (支持在爆发前 -3s 预建连，开抢瞬间 0 握手直发)
        self.session_a = self.monitor.scraper.create_probe_session()
        self.session_b = self.monitor.scraper.create_probe_session()
        self.session_c = self.monitor.scraper.create_probe_session()
        self.session_d = self.monitor.scraper.create_probe_session()

    def get_priority_rule(self) -> Optional[Dict]:
        """获取当前已启用且开启了「🎯 优先 (priority=True)」的唯一监控规则"""
        for rule in self.monitor.config.get("watches", []):
            if rule.get("enabled", True) and rule.get("priority", False):
                kws = [k.strip() for k in rule.get("keywords", []) if k.strip()]
                if kws:
                    return rule
        return None

    def prewarm_connections(self) -> int:
        """
        在定点爆发前 3 秒并发预热探针 A/B/C(/D) 的底层 TCP + TLS 1.3 长连接，
        确保整点启动时所有 Socket 均处于 Keep-Alive ESTABLISHED 热态。
        """
        sessions = [self.session_a, self.session_b, self.session_c]
        if self.get_priority_rule() is not None:
            sessions.append(self.session_d)
        warmed = 0
        threads = []
        results = []

        def _warm_one(sess):
            ok = self.monitor.scraper.warm_session(sess, timeout=2.0)
            results.append(ok)

        for s in sessions:
            t = threading.Thread(target=_warm_one, args=(s,), daemon=True)
            threads.append(t)
            t.start()
        for t in threads:
            t.join(timeout=2.2)
        warmed = sum(1 for r in results if r)
        logger.info(f"🔥 [探针连接池预热] 已完成 {warmed}/{len(sessions)} 路爆发探针 TLS 长连接热身就绪！")
        return warmed

    def is_old_item(self, item: Dict) -> bool:
        """检查商品是否为爆发启动前已存在的历史在库老商品"""
        pid = str(item.get("id", "")).strip()
        if not pid:
            return False
        return pid in self.baseline_seen_ids

    def notify_drop_detected(self):
        """通知引擎检测到官网上新发生，驱动探针C顺延深挖"""
        self._drop_active = True
        self._drop_detected_time = time.time()
        self._first_page_broken = True
        self._page1_cascade_ready = True

    def start(self, duration_sec: float = 180):
        if self.running:
            self.burst_end_time = max(self.burst_end_time, time.time() + duration_sec)
            return

        self.running = True
        self._stop_event.clear()
        now_ts = time.time()
        self._burst_start_time = now_ts
        self.burst_end_time = now_ts + duration_sec
        self._first_page_broken = False
        self._page1_cascade_ready = False
        timeout_sec = float(self.monitor.config.get("schedule", {}).get("burst_probe_timeout_sec", 2.0))

        # 锁定爆发启动前的历史老商品基准快照 (用于精准判断大上新翻页何时见底)
        self.baseline_seen_ids = set(str(sid) for sid in self.monitor.state.get("seen_ids", []))
        prio_rule = self.get_priority_rule()
        d_desc = (
            f"已激活！精准直击规则 [{prio_rule.get('name')}] (关键词: {', '.join(prio_rule.get('keywords', []))})"
            if prio_rule
            else "待命 (未开启优先规则，开启后实时激活)"
        )

        logger.info(
            f"🚀 [四路协同探测] 启动爆发期极速探测引擎 (持续 {int(duration_sec)} 秒)！\n"
            f"  ├── 探针 A: 前12s以30件轻装协防破墙 -> 破墙后100件全量保底 (timeout=(1.5s, 3.5s))\n"
            f"  ├── 探针 B: 30 件轻量先锋 (死守第1页毫秒级突击，破墙瞬间联动探针C跃迁第2页)\n"
            f"  ├── 探针 C: 30 件交替顺延 (B破第1页后直扑第2/3页，循环深挖至见老商品)\n"
            f"  └── 探针 D: 高优关键词狙击手 ({d_desc})"
        )

        self._threads = [
            threading.Thread(target=self._worker_a_heavy, name="BurstWorker_A_Full100", daemon=True),
            threading.Thread(target=self._worker_b_light, name="BurstWorker_B_Fast30", daemon=True),
            threading.Thread(target=self._worker_c_light, name="BurstWorker_C_Fast30", daemon=True),
            threading.Thread(target=self._worker_d_sniper, name="BurstWorker_D_Sniper", daemon=True),
        ]
        for t in self._threads:
            t.start()

    def stop(self):
        if not self.running:
            return
        self.running = False
        self._stop_event.set()
        logger.info("🛑 [四路协同探测] 正在停止爆发期探针 A/B/C/D...")
        for t in self._threads:
            t.join(timeout=0.8)
        self._threads.clear()
        logger.info("✅ [四路协同探测] 探针 A/B/C/D 已全部安全退出。")

    def _yield_if_checkout_active(self, probe_name: str, max_yield_sec: float = 25.0) -> bool:
        """
        当有商品正在执行自动下单/结算冲刺时，爆发期探针主动降频/避让，
        将同 IP 并发连接配额与网络带宽 90%+ 优先供给 /cart/customer、/cart/shipping、/cart/confirm 结算通道，
        同时保留轻量护航探测以防同批或次秒放出第 2 件目标商品。
        返回 True 表示收到停止信号应当退出循环。
        """
        if not AutoOrderHook.is_checkout_active():
            return False
        t_yield = time.time()
        if max_yield_sec >= 5.0:
            logger.info(f"🛡️ [{probe_name}-抢购让路] 检测到下单通道正在冲刺结算，探针主动暂停避让以保障结算带宽...")
        while AutoOrderHook.is_checkout_active() and not self._stop_event.is_set() and time.time() < self.burst_end_time:
            if time.time() - t_yield >= max_yield_sec:
                break
            if self._stop_event.wait(timeout=0.10):
                return True
        return self._stop_event.is_set()

    def _worker_a_heavy(self):
        """
        探针 A：
        - 爆发启动前 12 秒且尚未有任何探针破墙 (_first_page_broken=False) 时：
          自动降载为 30件/页 轻装协防突围，避免 100件/页 重查询卡死数据库或挤占同 IP 并发配额；
        - 一旦任意探针破墙或超过 12 秒：
          立即恢复 100件/页 全量保底覆盖，循环翻页至第 15 页或看到老商品为止。
        """
        session_a = self.session_a
        session_a.headers.update(self.monitor.scraper.session.headers)
        timeout_sec = float(self.monitor.config.get("schedule", {}).get("burst_probe_timeout_sec", 2.0))
        interval = float(self.monitor.config.get("schedule", {}).get("burst_interval_sec", 0.2))
        max_a_pages = int(self.monitor.config.get("schedule", {}).get("max_burst_a_pages", 15))
        max_a_pages = max(5, min(max_a_pages, 25))

        fail_streak_a = 0
        while not self._stop_event.is_set() and time.time() < self.burst_end_time:
            # 当有结算任务冲刺时，探针 A 自动降频为 1.5s 轻量护航扫描一次 (既削减 90%+ 并发争抢，又确保第 2 件新商品不漏检)
            if self._yield_if_checkout_active("探针A", max_yield_sec=1.5):
                break
            t0 = time.time()
            had_error_a = False
            try:
                for page in range(1, max_a_pages + 1):
                    if self._stop_event.is_set() or time.time() >= self.burst_end_time:
                        break
                    if page > 1 and self._yield_if_checkout_active("探针A", max_yield_sec=1.0):
                        break

                    # 前 12 秒尚未破墙时，第 1 页使用 30 件轻装模式协助快速破墙
                    in_early_wall = (not self._first_page_broken) and ((time.time() - self._burst_start_time) < 12.0)
                    page_num_size = 30 if (page == 1 and in_early_wall) else 100
                    mode_tag = "探针A-轻装破墙" if page_num_size == 30 else "探针A-全量保底"

                    logger.info(f"⚡ [{mode_tag}] 正在抓取 /new 第 {page}/{max_a_pages} 页 ({page_num_size}件/页, timeout={timeout_sec}s)...")
                    items = self.monitor.scraper.fetch_new_arrivals(
                        num=page_num_size, page=page, in_stock_only=True,
                        timeout=timeout_sec, session=session_a, max_retries=0
                    )
                    if not items:
                        logger.debug(f"⚠️ [探针A] 第 {page} 页未能获取数据 (503/超时)，中断本轮下翻")
                        had_error_a = True
                        break

                    if page == 1:
                        self._first_page_broken = True

                    logger.info(f"✅ [探针A-第{page}页] 抓取成功: 获取 {len(items)} 条商品")
                    matches = self.monitor.process_stream_items(items, source_tag=f"探针A-第{page}页", is_burst=True)

                    # 判断是否看到老商品或触底
                    has_old = any(self.is_old_item(it) for it in items) if self.baseline_seen_ids else False
                    is_all_old = all(self.is_old_item(it) for it in items) if self.baseline_seen_ids else False

                    if page == 1 and page_num_size == 30:
                        if (matches or self._drop_active or (self.baseline_seen_ids and not is_all_old)) and not is_all_old and len(items) >= 30:
                            self._page1_cascade_ready = True
                        # 轻装破墙拿到前 30 件后，下一轮立即切回 100 件/页拉取完整列表
                        break

                    if has_old:
                        logger.info(f"🏁 [探针A-触达老商品] 第 {page} 页已检测到历史在库老商品，本轮上新流扫描接驳完毕，停止继续翻页。")
                        break

                    if len(items) < page_num_size:
                        logger.info(f"🏁 [探针A-触底] 第 {page} 页商品数不足 {page_num_size} 件 ({len(items)} 件)，已触底全部上新。")
                        break

            except Exception as e:
                logger.debug(f"⚠️ [探针A] 请求异常: {e}")
                had_error_a = True

            if had_error_a:
                fail_streak_a += 1
                if fail_streak_a <= 5:
                    sleep_time = min(0.20, 0.08 + (fail_streak_a - 1) * 0.025)
                else:
                    backoff = min(0.35, 0.20 + (fail_streak_a - 5) * 0.03)
                    sleep_time = backoff + random.uniform(0.02, 0.06)
            else:
                fail_streak_a = 0
                elapsed = time.time() - t0
                sleep_time = max(0.05, interval - elapsed)

            if self._stop_event.wait(timeout=sleep_time):
                break

    def _worker_b_light(self):
        """探针 B：轻量先锋 30 件，死守第 1 页；一旦突破第 1 页且确认有上新，立即置位 _page1_cascade_ready 驱动探针 C 跳过第 1 页直取第 2 页"""
        session_b = self.session_b
        session_b.headers.update(self.monitor.scraper.session.headers)
        timeout_sec = float(self.monitor.config.get("schedule", {}).get("burst_probe_timeout_sec", 2.0))
        interval = float(self.monitor.config.get("schedule", {}).get("burst_interval_sec", 0.2))

        fail_streak_b = 0
        while not self._stop_event.is_set() and time.time() < self.burst_end_time:
            if self._yield_if_checkout_active("探针B"):
                break
            t0 = time.time()
            had_error_b = False
            try:
                logger.info(f"⚡ [探针B-死守前30] 正在极速抓取 /new 第 1 页 (1~30件, timeout={timeout_sec}s)...")
                items = self.monitor.scraper.fetch_new_arrivals(
                    num=30, page=1, in_stock_only=True,
                    timeout=timeout_sec, session=session_b, max_retries=0
                )
                if items:
                    self._first_page_broken = True
                    logger.info(f"✅ [探针B-死守前30] 第 1 页抓取成功: 获取 {len(items)} 条商品")
                    matches = self.monitor.process_stream_items(items, source_tag="探针B-30件", is_burst=True)
                    is_all_old = all(self.is_old_item(it) for it in items) if self.baseline_seen_ids else False
                    is_drop_active = self._drop_active and (time.time() - self._drop_detected_time < 45.0)
                    if (matches or is_drop_active or (self.baseline_seen_ids and not is_all_old)) and not is_all_old and len(items) >= 30:
                        self._page1_cascade_ready = True
                else:
                    logger.debug("⚠️ [探针B] 未能获取数据，准备下一轮...")
                    had_error_b = True
            except Exception as e:
                logger.debug(f"⚠️ [探针B] 请求异常: {e}")
                had_error_b = True

            if had_error_b:
                fail_streak_b += 1
                if fail_streak_b <= 5:
                    sleep_time = min(0.20, 0.08 + (fail_streak_b - 1) * 0.025)
                else:
                    backoff = min(0.35, 0.20 + (fail_streak_b - 5) * 0.03)
                    sleep_time = backoff + random.uniform(0.02, 0.06)
            else:
                fail_streak_b = 0
                elapsed = time.time() - t0
                sleep_time = max(0.05, interval - elapsed)

            if self._stop_event.wait(timeout=sleep_time):
                break

    def _worker_c_light(self):
        """
        探针 C：轻量交替与顺延探针 (以 30 件极速轻量并发，一直循环顺延深挖至看到老商品为止)
        - 平常与探针 B 错峰交替死守第 1 页 (1~30件)
        - 一旦探针 B/A 已突破第 1 页并置位 _page1_cascade_ready，探针 C 立即跳过重复抓第 1 页，直接跃迁拉取第 2 页 (31~60件)！
        - 一直循环向后翻页深挖，直到在某页检测到历史在库老商品（或触底）
        - 顺延深挖全部接驳完毕后，自动返回第 1 页继续与探针 B 错峰守候
        """
        session_c = self.session_c
        session_c.headers.update(self.monitor.scraper.session.headers)
        timeout_sec = float(self.monitor.config.get("schedule", {}).get("burst_probe_timeout_sec", 2.0))
        interval = float(self.monitor.config.get("schedule", {}).get("burst_interval_sec", 0.2))
        stagger = max(0.08, interval / 2.0)
        current_page = 1
        MAX_C_SAFETY_PAGES = 50  # 安全上限 50 页 (覆盖 1500 件)，防止官网异常死循环

        # 启动时先错开半个周期
        if self._stop_event.wait(timeout=stagger):
            return

        c_retry_count = 0
        fail_streak_c = 0
        while not self._stop_event.is_set() and time.time() < self.burst_end_time:
            # 若探针 A/B 已经突破第 1 页并发现上新，探针 C 无需再重复抓第 1 页，直接跃迁至第 2 页 (31~60件)！
            if current_page == 1 and self._page1_cascade_ready:
                self._page1_cascade_ready = False
                current_page = 2
                c_retry_count = 0
                logger.info("🚀 [探针C-联动跃迁] 探针A/B已突破第 1 页并确认上新流，探针C免翻第 1 页直接跃迁拉取第 2 页 (31~60件)...")

            # 若探针 C 正处于顺延深挖阶段 (current_page > 1)，允许其先把本轮上新流第 2/3 页扫完以防漏掉同批第 2 件商品；回到第 1 页后再主动避让
            if current_page == 1 and self._yield_if_checkout_active("探针C", max_yield_sec=25.0):
                break
            t0 = time.time()
            had_error_c = False
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
                    if current_page == 1:
                        self._first_page_broken = True
                    c_retry_count = 0
                    tag = "探针C-第1页(1~30件)" if current_page == 1 else f"探针C-第{current_page}页({item_start}~{item_end}件)"
                    logger.info(f"✅ [{tag}] 第 {current_page} 页抓取成功: 获取 {len(items)} 条商品")
                    matches = self.monitor.process_stream_items(items, source_tag=tag, is_burst=True)

                    has_old = any(self.is_old_item(it) for it in items) if self.baseline_seen_ids else False
                    is_drop_active = self._drop_active and (time.time() - self._drop_detected_time < 45.0)

                    if current_page == 1:
                        is_all_old = all(self.is_old_item(it) for it in items) if self.baseline_seen_ids else False
                        should_cascade = (matches or is_drop_active or (self.baseline_seen_ids and not is_all_old)) and not is_all_old
                        if should_cascade and len(items) >= 30:
                            logger.info(f"🔄 [探针C-顺延启动] 第 1 页已确认上新流，探针C立即顺延向后拉取第 2 页 (31~60件)...")
                            current_page = 2
                            self._page1_cascade_ready = False
                        else:
                            current_page = 1
                    else:
                        if has_old:
                            logger.info(f"🏁 [探针C-顺延完成] 顺延深挖至第 {current_page} 页已遇到历史在库老商品，深挖接驳全部完毕，探针C回到第 1 页继续与探针B交替守候。")
                            current_page = 1
                            self._drop_active = False
                            self._page1_cascade_ready = False
                        elif len(items) < 30:
                            logger.info(f"🏁 [探针C-顺延触底] 顺延至第 {current_page} 页商品数不足 30 件 ({len(items)} 件)，已触底，回到第 1 页。")
                            current_page = 1
                            self._drop_active = False
                            self._page1_cascade_ready = False
                        elif current_page >= MAX_C_SAFETY_PAGES:
                            logger.info(f"🏁 [探针C-达到安全深度] 顺延已达到上限 {MAX_C_SAFETY_PAGES} 页 ({item_end} 件)，回到第 1 页。")
                            current_page = 1
                            self._drop_active = False
                            self._page1_cascade_ready = False
                        else:
                            current_page += 1
                            logger.info(f"🔄 [探针C-继续顺延] 第 {current_page - 1} 页仍然满额且全为新品，继续顺延抓取第 {current_page} 页 ({(current_page - 1)*30 + 1}~{current_page*30}件)...")
                else:
                    had_error_c = True
                    logger.debug(f"⚠️ [探针C-第{current_page}页] 未能获取数据 (503/超时)")
                    if current_page > 1:
                        if c_retry_count < 2:
                            c_retry_count += 1
                            logger.warning(f"⚠️ [探针C-顺延深挖] 第 {current_page} 页请求遇繁忙/超时，原地快速重试 (第 {c_retry_count}/2 次)...")
                        else:
                            logger.info(f"⚠️ [探针C-顺延中断] 第 {current_page} 页重试耗尽，探针C安全重置回第 1 页。")
                            current_page = 1
                            c_retry_count = 0

            except Exception as e:
                logger.debug(f"⚠️ [探针C] 请求异常: {e}")
                had_error_c = True
                if current_page > 1:
                    current_page = 1
                    c_retry_count = 0

            if had_error_c:
                fail_streak_c += 1
                if fail_streak_c <= 5:
                    sleep_time = min(0.20, 0.08 + (fail_streak_c - 1) * 0.025)
                else:
                    backoff = min(0.35, 0.20 + (fail_streak_c - 5) * 0.03)
                    sleep_time = backoff + random.uniform(0.02, 0.06)
            else:
                fail_streak_c = 0
                elapsed = time.time() - t0
                sleep_time = max(0.05, interval - elapsed)

            if self._stop_event.wait(timeout=sleep_time):
                break

    def _worker_d_sniper(self):
        """
        探针 D：高优关键词直达狙击手 (Sniper Probe)
        - 仅当存在已启用且开启「🎯 优先 (priority=True)」的监控规则时发起请求（若未开启则静默休眠 0 网络开销）
        - 直接请求 /product-list/?keyword=...&order=newdate&available=1&num=30
        - 绕开 /new 翻页拥堵，不论目标商品排在 /new 第 1 页、第 2 页还是第 5 页，第 1 次突破 503 即刻命中！
        """
        session_d = self.session_d
        session_d.headers.update(self.monitor.scraper.session.headers)
        timeout_sec = float(self.monitor.config.get("schedule", {}).get("burst_probe_timeout_sec", 2.0))
        interval = float(self.monitor.config.get("schedule", {}).get("burst_interval_sec", 0.2))

        q_idx = 0
        fail_streak_d = 0
        while not self._stop_event.is_set() and time.time() < self.burst_end_time:
            prio_rule = self.get_priority_rule()
            if not prio_rule:
                # 当前未开启任何优先规则，静默待命不发任何网络请求
                if self._stop_event.wait(timeout=0.30):
                    break
                continue

            if self._yield_if_checkout_active("探针D", max_yield_sec=25.0):
                break

            keywords = [k.strip() for k in prio_rule.get("keywords", []) if k.strip()]
            if not keywords:
                if self._stop_event.wait(timeout=0.30):
                    break
                continue

            match_mode = prio_rule.get("match_mode", "all")
            if match_mode == "all":
                queries = [" ".join(keywords)]
            else:
                queries = keywords

            kw_query = queries[q_idx % len(queries)]
            q_idx += 1
            rule_name = prio_rule.get("name", kw_query)

            t0 = time.time()
            had_error_d = False
            try:
                logger.info(f"🎯 [探针D-优先狙击] 正在直搜优先规则 [{rule_name}] 关键词 [{kw_query}] (30件/页, timeout={timeout_sec}s)...")
                items = self.monitor.scraper.search_products(
                    keyword=kw_query, num=30, page=1, in_stock_only=True,
                    timeout=timeout_sec, session=session_d, max_retries=0
                )
                if items is not None and not self.monitor.scraper.last_fetch_failed:
                    logger.info(f"✅ [探针D-优先狙击] 关键词 [{kw_query}] 抓取成功: 获取 {len(items)} 条候选商品")
                    if items:
                        self.monitor.process_stream_items(items, source_tag=f"探针D-优先狙击[{rule_name}]", is_burst=True)
                else:
                    logger.debug(f"⚠️ [探针D-优先狙击] 关键词 [{kw_query}] 遇繁忙/超时，准备下一轮...")
                    had_error_d = True
            except Exception as e:
                logger.debug(f"⚠️ [探针D] 请求异常: {e}")
                had_error_d = True

            if had_error_d:
                fail_streak_d += 1
                if fail_streak_d <= 5:
                    sleep_time = min(0.20, 0.08 + (fail_streak_d - 1) * 0.025)
                else:
                    backoff = min(0.35, 0.20 + (fail_streak_d - 5) * 0.03)
                    sleep_time = backoff + random.uniform(0.02, 0.06)
            else:
                fail_streak_d = 0
                elapsed = time.time() - t0
                sleep_time = max(0.05, interval - elapsed)

            if self._stop_event.wait(timeout=sleep_time):
                break


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
            proxy=(ao_cfg.get("proxy") or self.config.get("proxy") or "").strip(),
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

    def _normalize_watch_priorities(self, cfg: Dict):
        """
        确保监控规则中的「🎯 优先 (priority)」开关满足全局唯一互斥约束：
        - 未启用的规则 (enabled=False) 自动取消 priority
        - 全站最多只允许 1 条已启用规则同时保持 priority=True
        """
        watches = cfg.get("watches", [])
        if not isinstance(watches, list):
            return
        found_priority = False
        for rule in watches:
            if not isinstance(rule, dict):
                continue
            if not rule.get("enabled", True):
                rule["priority"] = False
                continue
            if bool(rule.get("priority", False)):
                if not found_priority:
                    rule["priority"] = True
                    found_priority = True
                else:
                    rule["priority"] = False
            else:
                rule["priority"] = False

    def _load_config(self) -> Dict:
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    # 合并默认值
                    for k, v in DEFAULT_CONFIG.items():
                        if k not in cfg:
                            cfg[k] = v
                    self._normalize_watch_priorities(cfg)
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
                    self._normalize_watch_priorities(cfg)
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
        self._normalize_watch_priorities(new_cfg)
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
        new_proxy = (ao_cfg.get("proxy") or new_cfg.get("proxy") or "").strip()
        if new_proxy != getattr(self.auto_order, "proxy", ""):
            self.auto_order.proxy = new_proxy
            if new_proxy:
                req_p = new_proxy.replace("socks5://", "socks5h://", 1) if new_proxy.startswith("socks5://") else new_proxy
                self.auto_order.session.proxies.update({"http": req_p, "https": req_p})
            else:
                self.auto_order.session.proxies.clear()

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
                logger.info(f"✅ [基准线初始化] /new 第 {p} 页抓取成功: 获取 {len(items)} 条商品")
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
                        logger.info(f"✅ [基准线初始化] 关键词 [{combined_kw}] 抓取成功: 获取 {len(search_items)} 条商品")
                        for sit in search_items:
                            seen_set.add(sit["id"])
                        if search_items:
                            self.archive_db.upsert_batch(search_items)
                else:
                    for kw in keywords:
                        search_items = self.scraper.search_products(kw, num=100, page=1, in_stock_only=True)
                        logger.info(f"✅ [基准线初始化] 关键词 [{kw}] 抓取成功: 获取 {len(search_items)} 条商品")
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
            full_text_nospace = re.sub(r"\s+", "", full_text)

            def _kw_in_text(kw_str: str) -> bool:
                parts = kw_str.split()
                # 原始分词包含，或者去除中英文空格后整体包含 (智能兼容 "DearSD" 与 "Dear SD"、"幼SD" 与 "幼 SD" 等)
                if all(p in full_text for p in parts):
                    return True
                kw_nospace = re.sub(r"\s+", "", kw_str)
                return bool(kw_nospace and kw_nospace in full_text_nospace)

            if match_mode == "all":
                # 多个词必须同时全部满足 (AND)
                for kw in keywords:
                    if not _kw_in_text(kw):
                        return False
            else:
                # 满足任一词即可 (OR)
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
                        # 无缝立即续抢调度：冷却 2 秒后立即在后台自主发起下一轮冲刺，绝不干等 15 分钟巡检！
                        self._spawn_immediate_reorder(item, rule, count)
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

    def _spawn_immediate_reorder(self, item: Dict, rule: Dict, attempt_count: int, cooldown_sec: float = 2.0) -> None:
        """
        无缝立即续抢调度器：
        在单轮极速冲刺因持续 503 等原因耗尽重试但商品尚未售罄时，
        无需干等下一轮 15~30 分钟大巡检，直接在后台短暂冷却 2 秒后瞬时核验库存，并自动发起下一轮冲刺！
        """
        pid = str(item.get("id", "")).strip()
        if not pid or not self.config.get("auto_order", {}).get("enabled", False):
            return

        def _reorder_worker():
            time.sleep(cooldown_sec)
            with self._reorder_lock:
                if pid not in self._pending_reorders:
                    return

            try:
                stock_info = self.scraper.check_product_stock(pid)
                if stock_info.get("is_in_stock"):
                    logger.info(
                        f"⚡⚡ [Auto-Order 快速续抢出击] 商品 [{pid}] 经询盘确认官网仍在库！"
                        f"冷却完毕立即发起第 {attempt_count} 轮抢购流水线..."
                    )
                    with self._reorder_lock:
                        self._pending_reorders.pop(pid, None)
                    self._execute_auto_orders_concurrent([(item, rule)])
                else:
                    logger.info(f"🛑 [Auto-Order 续抢终止] 商品 [{pid}] 经单品询盘确认官网已售罄，自动移出续抢队列。")
                    with self._reorder_lock:
                        self._pending_reorders.pop(pid, None)
                        self._reorder_attempt_counts.pop(pid, None)
            except Exception as e:
                logger.warning(f"[Auto-Order 续抢] 询盘核验商品 [{pid}] 异常: {e}")

        t = threading.Thread(target=_reorder_worker, name=f"FastReorder_{pid}_{attempt_count}", daemon=True)
        t.start()

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
                for rule in sorted(active_rules, key=lambda r: (not r.get("priority", False), not r.get("auto_order", False))):
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
                    hist_entry = dict(it)
                    hist_entry["rule_name"] = r.get("name")
                    self.state.setdefault("history", []).insert(0, hist_entry)

            # 无论是否命中监控规则，抓取到的所有全新商品（包含目标与非目标商品）只要库中没有均全部自动入库归档数据库
            if page_new_items:
                try:
                    ins_cnt, upd_cnt = self.archive_db.upsert_batch(page_new_items)
                    if ins_cnt > 0:
                        logger.info(f"📦 [新品入库 ({source_tag})] 成功将 {ins_cnt} 件新上线商品收录至全数据档案库 (更新 {upd_cnt} 件)")
                except Exception as e:
                    logger.warning(f"同步流式新品至归档库失败: {e}")

            for it in page_new_items:
                it_id = str(it.get("id", "")).strip()
                if it_id:
                    seen_set.add(it_id)

            self.state["seen_ids"] = list(seen_set)
            self._save_state()
            return [it for it, r in stage_matches]

    def start_burst_probes(self, duration_sec: float = 180):
        """启动四路协同爆发探测引擎 (A:全量/轻装保底 | B:30件先锋 | C:30件顺延 | D:优先关键词狙击)"""
        self._burst_engine.start(duration_sec)

    def stop_burst_probes(self):
        """停止四路协同爆发探测引擎"""
        self._burst_engine.stop()

    def is_burst_probes_running(self) -> bool:
        """检查四路协同爆发探测引擎是否正在运行"""
        return self._burst_engine.running

    def prewarm_burst_connections(self) -> int:
        """在定点爆发前 3 秒预热爆发探针与下单 Worker 的底层 TLS 长连接池"""
        warmed = self._burst_engine.prewarm_connections()
        if hasattr(self, "auto_order") and self.auto_order and hasattr(self.auto_order, "warm_standby_connections"):
            self.auto_order.warm_standby_connections()
        return warmed

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
                stream_pages_fetched = 0
                stream_items_fetched = 0
                for page_idx in range(1, max_pages + 1):
                    logger.info(f"⚡ 正在抓取 Dollyteria /new 页面最新上新流 (第 {page_idx}/{max_pages} 页, {'爆发抢购' if is_burst else '常规巡检'})...")
                    new_arrivals = self.scraper.fetch_new_arrivals(num=100, page=page_idx, in_stock_only=True)
                    if not new_arrivals:
                        if getattr(self.scraper, "last_fetch_failed", False):
                            self._incomplete_from_page = max(self._incomplete_from_page, page_idx)
                            logger.warning(f"⚠️ /new 第 {page_idx} 页因网络或 503 繁忙未能拉取，已记录断点，下一轮巡检将自动穿透续扫！")
                        else:
                            self._incomplete_from_page = 0
                            logger.info(f"ℹ️ /new 上新流第 {page_idx} 页抓取完成: 当前页无更多商品 (已触底)")
                        break

                    stream_pages_fetched += 1
                    stream_items_fetched += len(new_arrivals)

                    for it in new_arrivals:
                        pool_items[it["id"]] = it

                    # 区分当前页全新商品与已见商品
                    page_new_items = []
                    for it in new_arrivals:
                        if it["id"] not in seen_set:
                            page_new_items.append(it)

                    logger.info(f"✅ /new 上新流第 {page_idx}/{max_pages} 页抓取成功: 获取 {len(new_arrivals)} 条商品 (其中全新待比对 {len(page_new_items)} 件)")

                    stage_matches = self.process_stream_items(new_arrivals, source_tag=f"第 {page_idx} 页", is_burst=is_burst)
                    total_new_matches.extend(stage_matches)
                    seen_set = set(str(sid) for sid in self.state.get("seen_ids", []))

                    # 智能早停：若当前页已没有哪怕 1 件全新商品 (整页均为已见历史商品)，说明此次大上新已完全接驳完毕；或当前页已不足 100 件
                    if len(new_arrivals) < 100:
                        self._incomplete_from_page = 0
                        logger.info(f"ℹ️ /new 上新流第 {page_idx} 页商品数不足 100 件 ({len(new_arrivals)} 件)，已完全获取所有最新上新。")
                        break
                    if len(page_new_items) == 0:
                        if page_idx < self._incomplete_from_page:
                            logger.info(f"🔄 第 {page_idx} 页已见，但上一轮在第 {self._incomplete_from_page} 页曾遇繁忙中断，继续向下穿透扫描...")
                            continue
                        self._incomplete_from_page = 0
                        logger.info(f"ℹ️ /new 上新流第 {page_idx} 页均为已记录历史商品，上新流已完整接驳完毕，提前早停以节省网络消耗。")
                        break
                else:
                    self._incomplete_from_page = 0

                if stream_pages_fetched > 0:
                    logger.info(f"📊 /new 上新流抓取完毕: 共成功抓取 {stream_pages_fetched} 页，累计获取 {stream_items_fetched} 条商品")

            # 2. 关键词全店搜索 (/product-list/?keyword=...)
            # 核心策略：爆发抢购模式下专攻 /new 主干道，完全跳过耗时的关键词全店搜索，杜绝 503 官网限流！
            if is_burst:
                logger.debug("⚡ 处于爆发抢购模式 (Burst Mode)，跳过耗时的关键词全店搜索，专攻 /new 高速主干道。")
            else:
                search_queries_count = 0
                search_items_fetched = 0
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
                                search_queries_count += 1
                                search_items_fetched += len(res)
                                logger.info(f"✅ 关键词搜索 [{combined_kw}] 抓取成功: 获取 {len(res)} 条商品 (规则: {rule.get('name')})")
                                for it in res:
                                    pool_items[it["id"]] = it
                                    stage2_pool[it["id"]] = it
                        else:
                            for kw in keywords:
                                logger.info(f"正在搜索关键词: [{kw}] (规则: {rule.get('name')})...")
                                res = self.scraper.search_products(kw, num=100, page=1, in_stock_only=True)
                                search_queries_count += 1
                                search_items_fetched += len(res)
                                logger.info(f"✅ 关键词搜索 [{kw}] 抓取成功: 获取 {len(res)} 条商品 (规则: {rule.get('name')})")
                                for it in res:
                                    pool_items[it["id"]] = it
                                    stage2_pool[it["id"]] = it

                # 阶段二搜索到的商品匹配与触发
                stage2_matches = []
                for it_id, it in stage2_pool.items():
                    if it_id in seen_set:
                        continue
                    matched_rule = None
                    for rule in sorted(active_rules, key=lambda r: (not r.get("priority", False), not r.get("auto_order", False))):
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

            total_pages_fetched = (stream_pages_fetched if needs_new_stream else 0) + (search_queries_count if not is_burst else 0)
            logger.info(
                f"🎉 本轮检测流程执行完毕: 共成功抓取 {total_pages_fetched} 页/次 "
                f"(上新流 {stream_pages_fetched if needs_new_stream else 0} 页, 关键词搜索 {search_queries_count if not is_burst else 0} 次)，"
                f"累计扫描 {len(pool_items)} 条商品 (命中 {len(total_new_matches)} 件新商品)"
            )
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
        pages_fetched = 0
        for p in range(1, max_pages + 1):
            try:
                items = self.scraper.fetch_new_arrivals(num=100, page=p, in_stock_only=False)
                if not items:
                    break
                pages_fetched += 1
                logger.info(f"✅ [上新流同步] 第 {p} 页抓取成功: 获取 {len(items)} 条商品")
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
                # 同步更新已见缓存与基准线，确保与归档数据库完全一致
                seen_set = set(str(sid) for sid in self.state.get("seen_ids", []))
                for it in all_items:
                    if it.get("is_in_stock", True):
                        seen_set.add(str(it["id"]).strip())
                self.state["seen_ids"] = list(seen_set)
                self._save_state()
            except Exception as e:
                logger.error(f"写入归档库异常: {e}")

        logger.info(f"⚡ 上新流归档同步完成: 共成功抓取 {pages_fetched} 页 / 累计 {len(all_items)} 条商品 (新增入库: {inserted}, 更新: {updated})")
        return {
            "status": "ok",
            "total_fetched": len(all_items),
            "inserted": inserted,
            "updated": updated,
            "message": f"成功同步 {len(all_items)} 件最新上新商品至档案 (新增 {inserted} 件，更新状态 {updated} 件)",
        }
