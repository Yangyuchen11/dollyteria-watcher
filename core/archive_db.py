"""
Dollyteria 商品归档数据库管理模块 (SQLite)
零外部依赖，使用标准库 sqlite3，持久化存储全站历史商品档案
支持高速多关键词匹配、在库/售罄筛选与分页查询
"""
import os
import time
import math
import json
import sqlite3
import logging
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger("dollyteria.archive_db")

DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "archive.db")
DEFAULT_PRESET_KEYWORDS = [
    "幼SD", "SDGr", "SD13", "MSD", "MDD", "Dear SD", "Blythe", "靴", "ウィッグ", "グラスアイ"
]


class DollyteriaArchiveDB:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self.init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # 启用 WAL 模式提高读写并发性能
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def init_db(self):
        """初始化数据表与索引"""
        try:
            with self._get_connection() as conn:
                conn.execute("""
                CREATE TABLE IF NOT EXISTS products (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '',
                    model_number TEXT DEFAULT '',
                    price INTEGER DEFAULT 0,
                    price_str TEXT DEFAULT '',
                    url TEXT DEFAULT '',
                    img_url TEXT DEFAULT '',
                    is_in_stock INTEGER DEFAULT 1,
                    is_new INTEGER DEFAULT 0,
                    first_seen_at INTEGER DEFAULT 0,
                    updated_at INTEGER DEFAULT 0
                );
                """)
                # 迁移检查：若旧库中的 products 表缺少 is_new 列则自动添加
                cursor = conn.cursor()
                cursor.execute("PRAGMA table_info(products);")
                existing_cols = {col["name"] for col in cursor.fetchall()}
                if "is_new" not in existing_cols:
                    cursor.execute("ALTER TABLE products ADD COLUMN is_new INTEGER DEFAULT 0;")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_products_is_new ON products(is_new);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_products_in_stock ON products(is_in_stock);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_products_updated_at ON products(updated_at DESC);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_products_price ON products(price);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_products_id_num ON products(CAST(id AS INTEGER) DESC);")
                conn.execute("""
                CREATE TABLE IF NOT EXISTS archive_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                """)
                conn.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY,
                    item_id TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    price INTEGER DEFAULT 0,
                    price_str TEXT DEFAULT '',
                    url TEXT DEFAULT '',
                    img_url TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    dry_run INTEGER DEFAULT 0,
                    trigger_type TEXT DEFAULT 'auto',
                    rule_name TEXT DEFAULT '',
                    payment_method TEXT DEFAULT 'cod',
                    message TEXT DEFAULT '',
                    duration_ms INTEGER DEFAULT 0,
                    retries INTEGER DEFAULT 0,
                    created_at INTEGER DEFAULT 0,
                    raw_details TEXT DEFAULT ''
                );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders(created_at DESC);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_item_id ON orders(item_id);")
                conn.commit()
            logger.info(f"SQLite 商品归档数据库初始化成功: {self.db_path}")
        except Exception as e:
            logger.error(f"初始化归档数据库异常: {e}")

    def upsert_batch(self, items: List[Dict]) -> Tuple[int, int]:
        """
        批量插入或更新商品列表
        返回 (新增数, 更新数)
        """
        if not items:
            return 0, 0

        now = int(time.time())
        inserted_count = 0
        updated_count = 0

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # 先查出已存在的 ID 集合以统计新增 vs 更新
                ids = [it["id"] for it in items if it.get("id")]
                if not ids:
                    return 0, 0

                placeholders = ",".join("?" for _ in ids)
                cursor.execute(f"SELECT id FROM products WHERE id IN ({placeholders})", ids)
                existing_ids = {row["id"] for row in cursor.fetchall()}

                upsert_sql = """
                INSERT INTO products (
                    id, title, model_number, price, price_str, url, img_url,
                    is_in_stock, is_new, first_seen_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    model_number = CASE WHEN excluded.model_number != '' THEN excluded.model_number ELSE products.model_number END,
                    price = excluded.price,
                    price_str = excluded.price_str,
                    url = excluded.url,
                    img_url = CASE WHEN excluded.img_url != '' THEN excluded.img_url ELSE products.img_url end,
                    is_in_stock = excluded.is_in_stock,
                    is_new = CASE WHEN excluded.is_new IS NOT NULL THEN excluded.is_new ELSE products.is_new END,
                    updated_at = excluded.updated_at;
                """

                params_list = []
                for it in items:
                    pid = str(it.get("id", "")).strip()
                    if not pid:
                        continue
                    if pid in existing_ids:
                        updated_count += 1
                        is_new_param = (1 if it["is_new"] else 0) if "is_new" in it else None
                    else:
                        inserted_count += 1
                        is_new_param = (1 if it["is_new"] else 0) if "is_new" in it else 0

                    params_list.append((
                        pid,
                        it.get("title", ""),
                        it.get("model_number", ""),
                        int(it.get("price", 0) or 0),
                        it.get("price_str", ""),
                        it.get("url", ""),
                        it.get("img_url", ""),
                        1 if it.get("is_in_stock", True) else 0,
                        is_new_param,
                        it.get("found_at", now) or now,
                        now,
                    ))

                cursor.executemany(upsert_sql, params_list)
                conn.commit()

            return inserted_count, updated_count
        except Exception as e:
            logger.error(f"批量写入归档商品失败: {e}")
            return 0, 0

    def mark_sold_out(self, item_id: str) -> bool:
        """将指定商品状态更新为已售罄 (is_in_stock = 0)"""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "UPDATE products SET is_in_stock = 0, updated_at = ? WHERE id = ?",
                    (int(time.time()), str(item_id).strip())
                )
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"标记商品 #{item_id} 售罄失败: {e}")
            return False

    def set_new_arrivals_ids(self, new_ids: List[str]) -> Tuple[int, int]:
        """
        全量同步/更新当前官网上新商品状态
        将 new_ids 中的商品标记为 is_new = 1，其余历史商品置为 is_new = 0
        返回 (当前新品数, 取消新品数)
        """
        if not new_ids:
            return 0, 0
        try:
            clean_ids = [str(i).strip() for i in new_ids if str(i).strip()]
            if not clean_ids:
                return 0, 0
            with self._get_connection() as conn:
                cursor = conn.cursor()
                placeholders = ",".join("?" for _ in clean_ids)

                cursor.execute(f"UPDATE products SET is_new = 1 WHERE id IN ({placeholders}) AND is_new != 1", clean_ids)
                marked_new = cursor.rowcount

                cursor.execute(f"UPDATE products SET is_new = 0 WHERE id NOT IN ({placeholders}) AND is_new = 1", clean_ids)
                demoted_old = cursor.rowcount

                conn.commit()
                return marked_new, demoted_old
        except Exception as e:
            logger.error(f"批量更新官网上新状态异常: {e}")
            return 0, 0

    def get_stock_status_map(self, ids: List[str]) -> Dict[str, bool]:
        """批量获取指定商品的在库状态 (返回 {id: is_in_stock})"""
        if not ids:
            return {}
        try:
            clean_ids = [str(i).strip() for i in ids if str(i).strip()]
            placeholders = ",".join("?" for _ in clean_ids)
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(f"SELECT id, is_in_stock FROM products WHERE id IN ({placeholders})", clean_ids)
                return {row["id"]: bool(row["is_in_stock"]) for row in cursor.fetchall()}
        except Exception as e:
            logger.error(f"批量查询商品在库状态失败: {e}")
            return {}

    def query_products(
        self,
        keyword: str = "",
        in_stock: Optional[str] = None,
        is_new: Optional[str] = None,
        preset_keywords: Optional[List[str]] = None,
        preset_match_mode: str = "all",
        sort_by: str = "updated_desc",
        page: int = 1,
        page_size: int = 30,
    ) -> Dict:
        """
        多条件搜索归档商品
        :param keyword: 空格分隔的关键词 (AND 联合匹配 title 与 model_number)
        :param in_stock: 'all' | '1' (仅在售) | '0' (仅售罄)
        :param is_new: 'all' | '1' (仅新品) | '0' (仅非新品)
        :param preset_keywords: 预设筛选关键词列表 (支持多选组合)
        :param preset_match_mode: 'all' (AND全部包含) | 'any' (OR任一包含)
        :param sort_by: 'updated_desc' | 'updated_asc' | 'price_asc' | 'price_desc'
        :param page: 页码 (>= 1)
        :param page_size: 每页数量 (默认 30)
        """
        page = max(1, int(page))
        page_size = max(1, min(100, int(page_size)))
        offset = (page - 1) * page_size

        where_clauses = []
        params = []

        # 1. 在售状态筛选 (库存状态)
        if in_stock in ["1", 1, True, "true"]:
            where_clauses.append("is_in_stock = 1")
        elif in_stock in ["0", 0, False, "false"]:
            where_clauses.append("is_in_stock = 0")

        # 2. 新品状态筛选 (官网上新后的新品)
        if is_new in ["1", 1, True, "true", "new"]:
            where_clauses.append("is_new = 1")
        elif is_new in ["0", 0, False, "false", "used"]:
            where_clauses.append("is_new = 0")

        # 3. 搜索栏输入关键词匹配 (支持以空格拆分多个关键词同时匹配)
        kw_parts = [k.strip() for k in keyword.split() if k.strip()]
        for kw in kw_parts:
            where_clauses.append("(title LIKE ? OR model_number LIKE ?)")
            kw_pattern = f"%{kw}%"
            params.extend([kw_pattern, kw_pattern])

        # 4. 预设筛选关键词组 (支持多选组合，可选 AND 全部满足 或 OR 任一满足)
        if preset_keywords:
            clean_pkws = [k.strip() for k in preset_keywords if k and k.strip()]
            if clean_pkws:
                if preset_match_mode == "any":
                    sub_clauses = []
                    for pkw in clean_pkws:
                        sub_clauses.append("(title LIKE ? OR model_number LIKE ?)")
                        pkw_pat = f"%{pkw}%"
                        params.extend([pkw_pat, pkw_pat])
                    where_clauses.append(f"({' OR '.join(sub_clauses)})")
                else:
                    for pkw in clean_pkws:
                        where_clauses.append("(title LIKE ? OR model_number LIKE ?)")
                        pkw_pat = f"%{pkw}%"
                        params.extend([pkw_pat, pkw_pat])

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        # 排序
        sort_map = {
            "id_desc": "CAST(id AS INTEGER) DESC",
            "id_asc": "CAST(id AS INTEGER) ASC",
            "updated_desc": "CAST(id AS INTEGER) DESC",  # 兼容旧默认值，指向商品发布顺序从新到旧
            "crawl_time_desc": "updated_at DESC, CAST(id AS INTEGER) DESC",
            "price_asc": "price ASC, CAST(id AS INTEGER) DESC",
            "price_desc": "price DESC, CAST(id AS INTEGER) DESC",
        }
        order_by_sql = sort_map.get(sort_by, "CAST(id AS INTEGER) DESC")

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # 查询符合条件的总数
                count_sql = f"SELECT COUNT(*) FROM products {where_sql};"
                cursor.execute(count_sql, params)
                total_count = cursor.fetchone()[0]

                # 分页查询记录
                query_sql = f"""
                SELECT id, title, model_number, price, price_str, url, img_url,
                       is_in_stock, is_new, first_seen_at, updated_at
                FROM products
                {where_sql}
                ORDER BY {order_by_sql}
                LIMIT ? OFFSET ?;
                """
                cursor.execute(query_sql, params + [page_size, offset])
                rows = cursor.fetchall()

                items = [
                    {
                        "id": row["id"],
                        "title": row["title"],
                        "model_number": row["model_number"],
                        "price": row["price"],
                        "price_str": row["price_str"] or f"￥{row['price']:,}",
                        "url": row["url"],
                        "img_url": row["img_url"],
                        "is_in_stock": bool(row["is_in_stock"]),
                        "is_new": bool(row["is_new"]),
                        "first_seen_at": row["first_seen_at"],
                        "updated_at": row["updated_at"],
                    }
                    for row in rows
                ]

                total_pages = math.ceil(total_count / page_size) if page_size > 0 else 1
                return {
                    "items": items,
                    "total": total_count,
                    "page": page,
                    "page_size": page_size,
                    "total_pages": max(1, total_pages),
                }
        except Exception as e:
            logger.error(f"查询归档数据异常: {e}")
            return {"items": [], "total": 0, "page": page, "page_size": page_size, "total_pages": 1}

    def get_preset_keywords(self) -> List[str]:
        """获取用户保存的预设筛选关键词列表"""
        raw = self.get_meta("preset_keywords", None)
        if raw is None:
            self.set_meta("preset_keywords", json.dumps(DEFAULT_PRESET_KEYWORDS, ensure_ascii=False))
            return list(DEFAULT_PRESET_KEYWORDS)
        try:
            data = json.loads(raw)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def set_preset_keywords(self, keywords: List[str]) -> List[str]:
        """设置并保存预设关键词列表"""
        clean = []
        for k in keywords:
            k_str = str(k).strip()
            if k_str and k_str not in clean:
                clean.append(k_str)
        self.set_meta("preset_keywords", json.dumps(clean, ensure_ascii=False))
        return clean

    def add_preset_keyword(self, keyword: str) -> List[str]:
        """添加单个预设筛选关键词"""
        kw = str(keyword).strip()
        current = self.get_preset_keywords()
        if kw and kw not in current:
            current.append(kw)
            self.set_preset_keywords(current)
        return current

    def remove_preset_keyword(self, keyword: str) -> List[str]:
        """删除单个预设筛选关键词"""
        kw = str(keyword).strip()
        current = self.get_preset_keywords()
        if kw in current:
            current = [k for k in current if k != kw]
            self.set_preset_keywords(current)
        return current

    def get_stats(self) -> Dict:
        """获取归档数据库统计信息"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM products;")
                total = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM products WHERE is_in_stock = 1;")
                in_stock = cursor.fetchone()[0]

                sold_out = total - in_stock

                cursor.execute("SELECT MAX(updated_at) FROM products;")
                last_updated = cursor.fetchone()[0] or 0

                sync_at = int(self.get_meta("last_stock_sync_at", "0") or 0)
                sync_sold = int(self.get_meta("last_stock_sync_sold_count", "0") or 0)
                sync_live = int(self.get_meta("last_stock_sync_total_live", "0") or 0)

                return {
                    "total": total,
                    "in_stock": in_stock,
                    "sold_out": sold_out,
                    "last_updated": last_updated,
                    "last_stock_sync_at": sync_at,
                    "last_stock_sync_sold_count": sync_sold,
                    "last_stock_sync_total_live": sync_live,
                }
        except Exception as e:
            logger.error(f"获取归档统计异常: {e}")
            return {
                "total": 0,
                "in_stock": 0,
                "sold_out": 0,
                "last_updated": 0,
                "last_stock_sync_at": 0,
                "last_stock_sync_sold_count": 0,
                "last_stock_sync_total_live": 0,
            }

    def get_meta(self, key: str, default: str = "") -> str:
        """读取元数据键值"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT value FROM archive_meta WHERE key = ?;", (str(key),))
                row = cursor.fetchone()
                return str(row["value"]) if row else default
        except Exception as e:
            logger.warning(f"读取元数据 [{key}] 异常: {e}")
            return default

    def set_meta(self, key: str, value: str):
        """保存或更新元数据键值"""
        try:
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT INTO archive_meta (key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value;
                """, (str(key), str(value)))
                conn.commit()
        except Exception as e:
            logger.warning(f"写入元数据 [{key}] 异常: {e}")

    def reconcile_in_stock_ids(self, live_in_stock_ids: set) -> int:
        """
        根据官网实时抓取的全部在线在售商品 ID 集合做差集对齐：
        将数据库中所有已不在该集合内的在售商品自动标记为已售罄 (is_in_stock = 0)
        返回标记为已售罄的商品条数
        """
        if not live_in_stock_ids:
            logger.warning("传入的实时在售 ID 集合为空，为保护数据安全跳过售罄翻转")
            return 0

        now = int(time.time())
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM products WHERE is_in_stock = 1;")
                current_db_ids = {row["id"] for row in cursor.fetchall()}

                # 计算差集：数据库里以为有货、但官网实时在线清单已不存在的商品
                sold_out_ids = list(current_db_ids - live_in_stock_ids)
                if not sold_out_ids:
                    logger.info(f"在售盘点一致：数据库中 {len(current_db_ids)} 件在售商品均在官网在线，无需翻转")
                    self.set_meta("last_stock_sync_at", str(now))
                    self.set_meta("last_stock_sync_sold_count", "0")
                    self.set_meta("last_stock_sync_total_live", str(len(live_in_stock_ids)))
                    return 0

                # 分批批量翻转为已售罄 (每批 500 个防超过 SQLite 变量上限)
                chunk_size = 500
                for i in range(0, len(sold_out_ids), chunk_size):
                    chunk = sold_out_ids[i:i + chunk_size]
                    placeholders = ",".join("?" for _ in chunk)
                    cursor.execute(
                        f"UPDATE products SET is_in_stock = 0, updated_at = ? WHERE id IN ({placeholders});",
                        [now] + chunk
                    )

                conn.commit()
                logger.info(f"在售盘点完成：已成功将 {len(sold_out_ids)} 件下架/被买走商品精准标记为已售罄！")
                self.set_meta("last_stock_sync_at", str(now))
                self.set_meta("last_stock_sync_sold_count", str(len(sold_out_ids)))
                self.set_meta("last_stock_sync_total_live", str(len(live_in_stock_ids)))
                return len(sold_out_ids)
        except Exception as e:
            logger.error(f"对齐在售商品状态异常: {e}")
            return 0

    def get_in_stock_ids(self) -> List[str]:
        """获取当前数据库中所有在售商品 ID (用于一键设为监控基准)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM products WHERE is_in_stock = 1;")
                return [row["id"] for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"获取在售商品 ID 列表异常: {e}")
            return []

    def get_product_by_id(self, item_id: str) -> Optional[Dict]:
        """根据商品 ID 精确获取单个商品详细信息"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, title, model_number, price, price_str, url, img_url,
                           is_in_stock, first_seen_at, updated_at
                    FROM products
                    WHERE id = ?;
                """, (str(item_id).strip(),))
                row = cursor.fetchone()
                if row:
                    return {
                        "id": row["id"],
                        "title": row["title"],
                        "model_number": row["model_number"],
                        "price": row["price"],
                        "price_str": row["price_str"] or f"￥{row['price']:,}",
                        "url": row["url"],
                        "img_url": row["img_url"],
                        "is_in_stock": bool(row["is_in_stock"]),
                        "first_seen_at": row["first_seen_at"],
                        "updated_at": row["updated_at"],
                    }
        except Exception as e:
            logger.error(f"获取单商品数据异常: {e}")
        return None

    def clear_archive(self):
        """清空归档库"""
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM products;")
                conn.commit()
            logger.info("已清空归档数据库中的全部商品记录")
        except Exception as e:
            logger.error(f"清空归档数据库异常: {e}")

    # ==================== 购买与抢购记录管理 ====================

    def record_order(
        self,
        item: Dict,
        rule_name: str = "",
        trigger_type: str = "auto",
        order_res: Optional[Dict] = None
    ) -> Dict:
        """
        持久化一条购买/下单记录
        - item: 商品信息 (id, title, price, price_str, url, img_url 等)
        - rule_name: 命中的规则名称或操作来源 (如 "DearSD 监控"、"看板一键购买"、"接口联调测试")
        - trigger_type: auto (自动触发) / quick_buy (一键抢购) / test (接口测试)
        - order_res: 执行结果 (success, dry_run, is_sold_out, message, elapsed_sec, etc.)
        """
        import uuid
        import json
        order_res = order_res or {}
        now = int(time.time())
        order_id = f"ord_{now}_{uuid.uuid4().hex[:6]}"

        pid = str(item.get("id") or "").strip()
        title = item.get("title") or f"商品 #{pid}"
        price = int(item.get("price") or 0)
        price_str = item.get("price_str") or (f"￥{price:,}" if price else "")
        url = item.get("url") or (f"https://www.dollyteria.com/product/{pid}" if pid else "")
        img_url = item.get("img_url") or ""

        # 如果 item 中没有图片但 archive 库中有，补充图片与价格
        if pid and (not img_url or not price):
            try:
                prod = self.get_product_by_id(pid)
                if prod:
                    if not img_url and prod.get("img_url"):
                        img_url = prod["img_url"]
                    if not price and prod.get("price"):
                        price = prod["price"]
                        price_str = prod.get("price_str", "")
            except Exception:
                pass

        success = bool(order_res.get("success", False))
        dry_run = bool(order_res.get("dry_run", False))
        is_sold_out = bool(order_res.get("is_sold_out", False))

        if success and not dry_run:
            status = "SUCCESS"  # 真实下单成功
        elif success and dry_run:
            status = "DRY_RUN_SUCCESS"  # 仿真模拟成功
        elif is_sold_out:
            status = "SOLDOUT"  # 官方已售罄
        else:
            status = "FAILED"  # 下单失败

        payment_method = str(order_res.get("payment_method") or item.get("payment_method") or "cod")
        message = str(order_res.get("message") or "")
        elapsed_sec = float(order_res.get("elapsed_sec") or 0)
        duration_ms = int(elapsed_sec * 1000)
        retries = int(order_res.get("retries") or len(order_res.get("steps_log") or []))

        try:
            raw_details = json.dumps(order_res, ensure_ascii=False)
        except Exception:
            raw_details = str(order_res)

        try:
            with self._get_connection() as conn:
                conn.execute("""
                INSERT INTO orders (
                    id, item_id, title, price, price_str, url, img_url,
                    status, dry_run, trigger_type, rule_name, payment_method,
                    message, duration_ms, retries, created_at, raw_details
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    order_id, pid, title, price, price_str, url, img_url,
                    status, 1 if dry_run else 0, trigger_type, rule_name, payment_method,
                    message, duration_ms, retries, now, raw_details
                ))
                conn.commit()
        except Exception as e:
            logger.error(f"写入购买记录数据库异常: {e}")

        time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
        return {
            "id": order_id,
            "item_id": pid,
            "title": title,
            "price": price,
            "price_str": price_str,
            "url": url,
            "img_url": img_url,
            "status": status,
            "dry_run": dry_run,
            "trigger_type": trigger_type,
            "rule_name": rule_name,
            "payment_method": payment_method,
            "message": message,
            "duration_ms": duration_ms,
            "retries": retries,
            "created_at": now,
            "time_str": time_str,
            "raw_details": order_res,
        }

    def query_orders(
        self,
        status: Optional[str] = None,
        trigger_type: Optional[str] = None,
        keyword: Optional[str] = None,
        page: int = 1,
        page_size: int = 20
    ) -> Dict:
        """条件检索购买记录列表"""
        import json
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 100))
        offset = (page - 1) * page_size

        where_clauses = []
        params = []

        if status and status.lower() != "all":
            where_clauses.append("status = ?")
            params.append(status.upper())

        if trigger_type and trigger_type.lower() != "all":
            where_clauses.append("trigger_type = ?")
            params.append(trigger_type.lower())

        if keyword and keyword.strip():
            kw = f"%{keyword.strip()}%"
            where_clauses.append("(title LIKE ? OR item_id LIKE ? OR rule_name LIKE ?)")
            params.extend([kw, kw, kw])

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(f"SELECT COUNT(*) as cnt FROM orders {where_sql};", params)
                total = cursor.fetchone()["cnt"]

                cursor.execute(f"""
                    SELECT id, item_id, title, price, price_str, url, img_url,
                           status, dry_run, trigger_type, rule_name, payment_method,
                           message, duration_ms, retries, created_at, raw_details
                    FROM orders
                    {where_sql}
                    ORDER BY created_at DESC
                    LIMIT ? OFFSET ?;
                """, params + [page_size, offset])

                orders = []
                for row in cursor.fetchall():
                    raw_dt = {}
                    if row["raw_details"]:
                        try:
                            raw_dt = json.loads(row["raw_details"])
                        except Exception:
                            pass
                    orders.append({
                        "id": row["id"],
                        "item_id": row["item_id"],
                        "title": row["title"],
                        "price": row["price"],
                        "price_str": row["price_str"],
                        "url": row["url"],
                        "img_url": row["img_url"],
                        "status": row["status"],
                        "dry_run": bool(row["dry_run"]),
                        "trigger_type": row["trigger_type"],
                        "rule_name": row["rule_name"],
                        "payment_method": row["payment_method"],
                        "message": row["message"],
                        "duration_ms": row["duration_ms"],
                        "retries": row["retries"],
                        "created_at": row["created_at"],
                        "time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row["created_at"])),
                        "raw_details": raw_dt,
                    })

                total_pages = math.ceil(total / page_size) if total > 0 else 1
                return {
                    "total": total,
                    "page": page,
                    "page_size": page_size,
                    "total_pages": total_pages,
                    "orders": orders,
                }
        except Exception as e:
            logger.error(f"查询购买记录异常: {e}")
            return {"total": 0, "page": page, "page_size": page_size, "total_pages": 1, "orders": []}

    def get_order_stats(self) -> Dict:
        """获取购买与抢购统计指标"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) as total FROM orders;")
                total = cursor.fetchone()["total"]

                cursor.execute("SELECT COUNT(*) as cnt FROM orders WHERE status = 'SUCCESS';")
                success_count = cursor.fetchone()["cnt"]

                cursor.execute("SELECT COUNT(*) as cnt FROM orders WHERE status = 'DRY_RUN_SUCCESS';")
                dry_run_count = cursor.fetchone()["cnt"]

                cursor.execute("SELECT COUNT(*) as cnt FROM orders WHERE status = 'SOLDOUT';")
                soldout_count = cursor.fetchone()["cnt"]

                cursor.execute("SELECT COUNT(*) as cnt FROM orders WHERE status = 'FAILED';")
                failed_count = cursor.fetchone()["cnt"]

                cursor.execute("SELECT COALESCE(SUM(price), 0) as total_spent FROM orders WHERE status = 'SUCCESS';")
                total_spent = cursor.fetchone()["total_spent"]

                return {
                    "total": total,
                    "success_count": success_count,
                    "dry_run_count": dry_run_count,
                    "soldout_count": soldout_count,
                    "failed_count": failed_count,
                    "total_spent": total_spent,
                }
        except Exception as e:
            logger.error(f"获取购买统计异常: {e}")
            return {"total": 0, "success_count": 0, "dry_run_count": 0, "soldout_count": 0, "failed_count": 0, "total_spent": 0}

    def delete_order(self, order_id: str) -> bool:
        """删除指定的一条购买记录"""
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM orders WHERE id = ?;", (str(order_id).strip(),))
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"删除订单记录异常: {e}")
            return False

    def clear_orders(self) -> bool:
        """清空全部购买记录"""
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM orders;")
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"清空订单记录异常: {e}")
            return False
