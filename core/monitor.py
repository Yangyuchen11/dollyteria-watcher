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
        "burst_interval_sec": 0.3,                 # 密集轮询间隔 (秒)
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
        "dry_run": True,
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
            dry_run=ao_cfg.get("dry_run", True),
            payment_method=ao_cfg.get("payment_method", "cod"),
            retry_delay=float(ao_cfg.get("retry_delay_sec", 0.15)),
            max_retries=int(ao_cfg.get("max_retries", 35)),
            standby_workers=int(ao_cfg.get("standby_workers", 2)),
        )

        self.is_checking = False
        self.last_check_time = 0
        self.last_check_status = "就绪"
        self._incomplete_from_page = 0

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
        self.auto_order.dry_run = ao_cfg.get("dry_run", True)
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
        self._save_state()
        logger.info("已清空历史已记录商品集合")

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
                if order_res and (
                    (order_res.get("success") and not order_res.get("dry_run"))
                    or order_res.get("is_sold_out")
                ):
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

                    # 对全新商品执行规则匹配
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
                        logger.info(f"✨ [/new 实时流式命中 (第 {page_idx} 页)] 发现 {len(stage_matches)} 件符合规则的有货新商品！")

                        # 立即触发自动下单 (多线程并发抢购，非阻塞)
                        if self.config.get("auto_order", {}).get("enabled", False):
                            auto_order_tasks = [(it, r) for it, r in stage_matches if r.get("auto_order", False)]
                            if auto_order_tasks:
                                logger.info(f"⚡ [流式立即下单] 触发 {len(auto_order_tasks)} 件新商品的自动抢购流水线...")
                                self._execute_auto_orders_concurrent(auto_order_tasks)
                            non_ao_rules = {r.get("name") for it, r in stage_matches if not r.get("auto_order", False)}
                            for nr in non_ao_rules:
                                logger.info(f"ℹ️ 规则 [{nr}] 专属自动购买开关为关闭状态，仅执行消息提醒，不触发自动下单")

                        # 分组分发各渠道提醒
                        rule_groups = {}
                        for it, r in stage_matches:
                            rule_groups.setdefault(r["id"], {"rule": r, "items": []})["items"].append(it)
                        for r_data in rule_groups.values():
                            self.notifier.dispatch(r_data["items"], r_data["rule"])

                        # 记录至历史动态与已见缓存
                        for it, r in stage_matches:
                            seen_set.add(it["id"])
                            hist_entry = dict(it)
                            hist_entry["rule_name"] = r.get("name")
                            self.state.setdefault("history", []).insert(0, hist_entry)
                            total_new_matches.append(it)

                        # 立即保存状态与归档，确保断电或异常不丢
                        self.state["seen_ids"] = list(seen_set)
                        self._save_state()
                        try:
                            self.archive_db.upsert_batch([it for it, r in stage_matches])
                        except Exception as e:
                            logger.warning(f"同步流式命中商品至归档库失败: {e}")

                    # 将该页未命中的全新商品也加入 seen_set，避免下一次循环重复判断
                    for it in page_new_items:
                        seen_set.add(it["id"])

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

    def sync_new_arrivals_to_archive(self, max_pages: int = 5) -> Dict:
        """
        全量同步 /new 最新上新流至商品归档库 (archive.db)
        支持抓取前 N 页（默认 5 页，500 件），包含在售与售罄状态，
        确保大上新的所有新品 100% 完整录入档案与行情库
        """
        logger.info(f"⚡ 开始同步 /new 上新流商品至归档库 (最大 {max_pages} 页)...")
        all_items = []
        for p in range(1, max_pages + 1):
            try:
                items = self.scraper.fetch_new_arrivals(num=100, page=p, in_stock_only=False)
                if not items:
                    break
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
