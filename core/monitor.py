"""
监控核心引擎 (Monitor Engine)
负责规则匹配、去重缓存、有货过滤、触发调度与状态持久化
"""
import os
import json
import time
import datetime
import logging
from typing import List, Dict, Set, Optional
from core.scraper import DollyteriaScraper
from notifiers.manager import NotifierManager
from hooks.auto_order import AutoOrderHook

logger = logging.getLogger("dollyteria.monitor")

DATA_DIR = os.environ.get("DATA_DIR", "").strip()
if DATA_DIR:
    os.makedirs(DATA_DIR, exist_ok=True)
    CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
    STATE_PATH = os.path.join(DATA_DIR, "state.json")
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
        "burst_interval_sec": 2.0,                 # 密集轮询间隔 (秒)
        "interval_minutes": 5,                     # 日常平缓轮询间隔 (分钟)
        "interval_enabled": True,                  # 是否开启日常平缓轮询
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
    },
}


class DollyteriaMonitor:
    def __init__(self):
        self.scraper = DollyteriaScraper()
        self.config = self._load_config()
        self.state = self._load_state()

        self.notifier = NotifierManager(self.config)
        self.auto_order = AutoOrderHook(
            username=self.config.get("auto_order", {}).get("username", ""),
            password=self.config.get("auto_order", {}).get("password", ""),
            dry_run=self.config.get("auto_order", {}).get("dry_run", True),
            payment_method=self.config.get("auto_order", {}).get("payment_method", "cod"),
        )

        self.is_checking = False
        self.last_check_time = 0
        self.last_check_status = "就绪"

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
            # 保留最近 2000 个 seen_id
            if len(self.state.get("seen_ids", [])) > 2000:
                self.state["seen_ids"] = self.state["seen_ids"][-2000:]
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
            # 抓取当前最新的上新流 100 件
            items = self.scraper.fetch_new_arrivals(num=100, in_stock_only=True)
            for it in items:
                seen_set.add(it["id"])

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
                else:
                    for kw in keywords:
                        search_items = self.scraper.search_products(kw, num=100, page=1, in_stock_only=True)
                        for sit in search_items:
                            seen_set.add(sit["id"])

            self.state["seen_ids"] = list(seen_set)
            self._save_state()
            logger.info(f"基准线建立完毕！已记录 {len(seen_set)} 件已有商品，后续仅提醒新上架/新补货商品。")
        except Exception as e:
            logger.error(f"建立基准线时异常: {e}")

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

    def check_once(self) -> Dict:
        """
        执行一次完整的巡检
        返回本次检测的结果摘要
        """
        if self.is_checking:
            return {"status": "busy", "message": "上一轮检测正在进行中"}

        self.is_checking = True
        self.last_check_time = int(time.time())
        self.last_check_status = "检测中"

        active_rules = [r for r in self.config.get("watches", []) if r.get("enabled", True)]
        if not active_rules:
            self.is_checking = False
            self.last_check_status = "无活跃规则"
            return {"status": "ok", "message": "当前没有已启用的监控规则", "new_items_count": 0}

        seen_set = set(self.state.get("seen_ids", []))
        total_new_matches = []
        rule_matches_map = {}

        try:
            # 1. 首先检查上新流 (/new)，这是定点上新最快出现的地方
            needs_new_stream = any(r.get("search_mode", "both") in ["new_stream", "both"] for r in active_rules)
            pool_items = {}

            if needs_new_stream:
                logger.info("正在抓取 Dollyteria /new 页面最新上新流...")
                new_arrivals = self.scraper.fetch_new_arrivals(num=100, in_stock_only=True)
                for it in new_arrivals:
                    pool_items[it["id"]] = it

            # 2. 检查特定关键词搜索 (/product-list/?keyword=...)
            for rule in active_rules:
                mode = rule.get("search_mode", "both")
                if mode in ["keyword_search", "both"]:
                    match_mode = rule.get("match_mode", "all")
                    keywords = [k.strip() for k in rule.get("keywords", []) if k.strip()]
                    if match_mode == "all":
                        # 多个词必须同时全部满足 (AND)：组合成以空格隔开的联合关键词在全站检索
                        combined_kw = " ".join(keywords)
                        if combined_kw:
                            logger.info(f"正在进行多词同时满足(AND)搜索: [{combined_kw}] (规则: {rule.get('name')})...")
                            res = self.scraper.search_products(combined_kw, num=100, page=1, in_stock_only=True)
                            for it in res:
                                pool_items[it["id"]] = it
                    else:
                        for kw in keywords:
                            logger.info(f"正在搜索关键词: [{kw}] (规则: {rule.get('name')})...")
                            res = self.scraper.search_products(kw, num=100, page=1, in_stock_only=True)
                            for it in res:
                                pool_items[it["id"]] = it

            # 3. 对抓取到的全部候选商品逐条匹配规则
            for it_id, item in pool_items.items():
                for rule in active_rules:
                    if self._matches_rule(item, rule):
                        rule_id = rule.get("id")
                        if rule_id not in rule_matches_map:
                            rule_matches_map[rule_id] = {"rule": rule, "items": []}
                        rule_matches_map[rule_id]["items"].append(item)

            # 4. 去重与提醒触发
            new_item_ids_to_add = set()

            for rule_id, match_data in rule_matches_map.items():
                rule = match_data["rule"]
                items = match_data["items"]

                # 仅筛选出未曾见过的商品
                unseen_items = [it for it in items if it["id"] not in seen_set]

                if unseen_items:
                    logger.info(f"✨ 规则 [{rule.get('name')}] 发现 {len(unseen_items)} 件全新商品！")
                    total_new_matches.extend(unseen_items)
                    for u in unseen_items:
                        new_item_ids_to_add.add(u["id"])

                    # 分发各渠道提醒
                    self.notifier.dispatch(unseen_items, rule)

                    # 自动下单 Hook
                    if self.config.get("auto_order", {}).get("enabled", False):
                        for u in unseen_items:
                            self.auto_order.on_item_matched(u, rule)

                    # 记录至历史动态
                    for u in unseen_items:
                        hist_entry = dict(u)
                        hist_entry["rule_name"] = rule.get("name")
                        self.state.setdefault("history", []).insert(0, hist_entry)

            # 更新已见缓存与统计数据
            if new_item_ids_to_add:
                self.state["seen_ids"] = list(seen_set.union(new_item_ids_to_add))

            stats = self.get_stats()
            stats["today_checks"] = stats.get("today_checks", 0) + 1
            stats["total_checks"] = stats.get("total_checks", 0) + 1
            if new_item_ids_to_add:
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
