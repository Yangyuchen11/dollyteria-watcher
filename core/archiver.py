"""
Dollyteria 全站商品温和爬取与归档引擎 (后台异步线程)
支持按每页 100 件平缓抓取、实时进度反馈、断点续爬、暂停/恢复与增量写入 SQLite
"""
import time
import math
import re
import threading
import logging
from typing import Dict, Optional
from bs4 import BeautifulSoup

from core.scraper import DollyteriaScraper, BASE_URL
from core.archive_db import DollyteriaArchiveDB

logger = logging.getLogger("dollyteria.archiver")


class DollyteriaArchiver:
    def __init__(self, db: DollyteriaArchiveDB, scraper: Optional[DollyteriaScraper] = None):
        self.db = db
        self.scraper = scraper or DollyteriaScraper()

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()

        self.status = "idle"  # idle | running | paused | finished | error
        self.current_page = 0
        self.total_pages = 0
        self.total_items_estimated = 0
        self.total_scraped = 0
        self.new_inserted = 0
        self.updated_existing = 0
        self.delay_sec = 1.2
        self.last_error = ""
        self.started_at = 0
        self.finished_at = 0

    def get_status(self) -> Dict:
        """获取当前采集任务状态与归档库综合统计"""
        stats = self.db.get_stats()
        progress_pct = 0.0
        if self.total_pages > 0:
            progress_pct = round(min(100.0, (self.current_page / self.total_pages) * 100), 1)

        return {
            "status": self.status,
            "current_page": self.current_page,
            "total_pages": self.total_pages,
            "total_items_estimated": self.total_items_estimated,
            "total_scraped": self.total_scraped,
            "new_inserted": self.new_inserted,
            "updated_existing": self.updated_existing,
            "delay_sec": self.delay_sec,
            "progress_pct": progress_pct,
            "last_error": self.last_error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "archive_stats": stats,
        }

    def start(self, start_page: Optional[int] = None, delay: float = 1.2) -> Dict:
        """启动或继续全站采集"""
        if self.status == "running":
            return {"status": "already_running", "message": "全站采集任务已在运行中"}

        if self.status == "paused":
            self.resume()
            return {"status": "ok", "message": "采集任务已从暂停恢复继续"}

        self.delay_sec = max(0.5, float(delay))
        self._stop_event.clear()
        self._pause_event.clear()

        if start_page is not None and start_page > 0:
            self.current_page = start_page - 1
            self.total_scraped = 0
            self.new_inserted = 0
            self.updated_existing = 0
        elif self.status in ["finished", "error", "idle"] and self.current_page >= self.total_pages and self.total_pages > 0:
            # 重新开始
            self.current_page = 0
            self.total_scraped = 0
            self.new_inserted = 0
            self.updated_existing = 0

        self.status = "running"
        self.last_error = ""
        self.started_at = int(time.time())
        self.finished_at = 0

        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info(f"全站采集任务已启动，从第 {self.current_page + 1} 页开始，单页间隔 {self.delay_sec}s")
        return {"status": "ok", "message": f"采集任务已启动，从第 {self.current_page + 1} 页开始"}

    def pause(self) -> Dict:
        """暂停采集"""
        if self.status != "running":
            return {"status": "not_running", "message": f"当前状态 ({self.status}) 无法暂停"}
        self._pause_event.set()
        self.status = "paused"
        logger.info(f"全站采集已暂停，当前停留在第 {self.current_page} 页")
        return {"status": "ok", "message": f"已暂停在第 {self.current_page} 页"}

    def resume(self) -> Dict:
        """恢复采集"""
        if self.status != "paused":
            return {"status": "not_paused", "message": f"当前状态 ({self.status}) 非暂停状态"}
        self._pause_event.clear()
        self.status = "running"
        logger.info(f"全站采集已恢复，正在继续第 {self.current_page + 1} 页...")
        return {"status": "ok", "message": "采集已恢复运行"}

    def stop(self) -> Dict:
        """终止采集任务"""
        self._stop_event.set()
        self._pause_event.clear()
        self.status = "idle"
        logger.info(f"全站采集已手动停止，共抓取到第 {self.current_page} 页")
        return {"status": "ok", "message": "采集已停止"}

    def _extract_total_pages(self, html: str) -> int:
        """从页面提取全站商品总数并计算总页数"""
        try:
            soup = BeautifulSoup(html, "html.parser")
            # 优先从 .count_number .number 匹配
            num_el = soup.select_one(".count_number .number") or soup.select_one(".number")
            if num_el:
                txt = num_el.get_text(strip=True).replace(",", "")
                m = re.search(r"(\d+)", txt)
                if m:
                    total_items = int(m.group(1))
                    self.total_items_estimated = total_items
                    return math.ceil(total_items / 100)

            # 备选：查找任何包含 "件" 的文本
            for el in soup.find_all(text=True):
                if "件" in el and any(c.isdigit() for c in el) and len(el.strip()) < 30:
                    clean = re.sub(r"[^\d]", "", el)
                    if clean:
                        total_items = int(clean)
                        if total_items > 1000:
                            self.total_items_estimated = total_items
                            return math.ceil(total_items / 100)
        except Exception as e:
            logger.warning(f"提取总页数出错: {e}")
        return 0

    def _run_loop(self):
        """核心后台爬取循环"""
        logger.info("全站归档爬虫线程启动")
        url = f"{BASE_URL}/product-list"

        page = self.current_page + 1
        consecutive_errors = 0

        while not self._stop_event.is_set():
            # 暂停处理
            while self._pause_event.is_set():
                if self._stop_event.is_set():
                    break
                time.sleep(0.5)

            if self._stop_event.is_set():
                break

            logger.info(f"正在爬取全站商品第 {page} 页 (每页 100 件)...")
            params = {
                "num": "100",
                "page": str(page),
            }

            try:
                html = self.scraper._get(url, params=params)
                if not html:
                    consecutive_errors += 1
                    logger.warning(f"第 {page} 页请求失败，连续失败 {consecutive_errors} 次")
                    if consecutive_errors >= 5:
                        self.status = "error"
                        self.last_error = f"第 {page} 页连续多次请求失败，已自动中断"
                        break
                    time.sleep(2.0 * consecutive_errors)
                    continue

                consecutive_errors = 0

                # 第一次提取总页数
                if self.total_pages <= 0:
                    est_pages = self._extract_total_pages(html)
                    if est_pages > 0:
                        self.total_pages = est_pages
                        logger.info(f"探测到全站商品预估 {self.total_items_estimated} 件，共 {self.total_pages} 页")

                # 解析商品
                items = self.scraper.parse_items(html)
                if not items:
                    logger.info(f"第 {page} 页未解析出任何商品，爬取完毕或已到底")
                    self.status = "finished"
                    self.finished_at = int(time.time())
                    break

                # 写入 SQLite
                ins, upd = self.db.upsert_batch(items)
                self.new_inserted += ins
                self.updated_existing += upd
                self.total_scraped += len(items)
                self.current_page = page

                logger.info(
                    f"第 {page} 页抓取成功: 解析 {len(items)} 件 (新增 {ins}, 更新 {upd}) | "
                    f"进度: {self.current_page}/{self.total_pages or '?'} 页"
                )

                # 检查是否到达最后一页
                if self.total_pages > 0 and page >= self.total_pages:
                    logger.info(f"已达到预估最后一页 ({self.total_pages})，采集任务全部完成！")
                    self.status = "finished"
                    self.finished_at = int(time.time())
                    break

                page += 1

                # 间隔休息，防止高频封禁
                sleep_time = self.delay_sec
                step = 0.2
                while sleep_time > 0:
                    if self._stop_event.is_set() or self._pause_event.is_set():
                        break
                    time.sleep(min(step, sleep_time))
                    sleep_time -= step

            except Exception as e:
                consecutive_errors += 1
                logger.error(f"处理第 {page} 页异常: {e}")
                self.last_error = str(e)
                if consecutive_errors >= 5:
                    self.status = "error"
                    break
                time.sleep(3.0)

        if self.status == "running":
            self.status = "idle"
        logger.info(f"全站归档爬虫线程结束，最终状态: {self.status}")
