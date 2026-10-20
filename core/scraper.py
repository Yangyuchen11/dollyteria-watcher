"""
Dollyteria (dollyteria.com) 网站抓取与解析模块
支持最新商品流 (/new) 与 关键词搜索 (/product-list/?keyword=...)
"""
import re
import time
import logging
from typing import List, Dict, Optional
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("dollyteria.scraper")

BASE_URL = "https://www.dollyteria.com"
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ja,zh-CN;q=0.9,en;q=0.8",
    "Referer": "https://www.dollyteria.com/",
}

# 售罄标识符 (含图片水印中的 SoldOut 编码 536f6c644f7574)
SOLDOUT_HEX_MARK = "536f6c644f7574"
SOLDOUT_TEXT_KEYWORDS = ["売り切れ", "売切", "品切れ", "在庫切れ", "sold out", "soldout"]


class DollyteriaScraper:
    def __init__(self, timeout: int = 15, max_retries: int = 2):
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

    def _get(self, url: str, params: Optional[dict] = None) -> Optional[str]:
        """带重试机制的 HTTP GET 请求"""
        last_exc = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                if resp.status_code == 200:
                    return resp.text
                logger.warning(f"请求 {url} 返回状态码 {resp.status_code}")
            except Exception as e:
                last_exc = e
                logger.warning(f"请求失败 ({attempt + 1}/{self.max_retries + 1}): {e}")
                if attempt < self.max_retries:
                    time.sleep(0.8 * (attempt + 1))
        logger.error(f"无法访问页面: {url}, 错误: {last_exc}")
        return None

    def parse_items(self, html: str) -> List[Dict]:
        """解析页面中的商品列表 HTML"""
        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        items = []

        for cell in soup.select("li.list_item_cell"):
            try:
                item_data = cell.select_one(".item_data")
                pid = item_data.get("data-product-id") if item_data else None
                if not pid:
                    # 尝试从 class 中提取 id，如 list_item_801946
                    for cls in cell.get("class", []):
                        if cls.startswith("list_item_") and cls[10:].isdigit():
                            pid = cls[10:]
                            break

                if not pid:
                    continue

                # 提取链接
                link_el = cell.select_one("a.item_data_link") or cell.select_one("a")
                href = link_el.get("href", "") if link_el else f"/product/{pid}"
                if href.startswith("/"):
                    href = BASE_URL + href

                # 提取商品名称
                name_el = cell.select_one(".goods_name") or cell.select_one(".item_name")
                name = name_el.get_text(strip=True) if name_el else ""

                # 提取型号/编号
                model_el = cell.select_one(".model_number_value")
                model_num = model_el.get_text(strip=True) if model_el else ""

                # 提取价格
                price_el = cell.select_one(".selling_price .figure") or cell.select_one(".price")
                price_str = price_el.get_text(strip=True) if price_el else ""
                digits = re.sub(r"[^\d]", "", price_str)
                price_num = int(digits) if digits else 0

                # 提取图片
                img_el = cell.select_one(".global_photo img") or cell.select_one("img")
                img_url = ""
                if img_el:
                    img_url = img_el.get("data-x2") or img_el.get("src") or ""
                    if img_url.startswith("/"):
                        img_url = BASE_URL + img_url

                # 在库判断
                # 1. 检查图片 URL 是否包含 SoldOut 水印标志
                is_sold_out_img = SOLDOUT_HEX_MARK in img_url.lower()
                # 2. 检查文本中是否显式标注售罄
                cell_text = cell.get_text()
                is_sold_out_text = any(k in cell_text for k in SOLDOUT_TEXT_KEYWORDS)

                is_in_stock = not (is_sold_out_img or is_sold_out_text)

                items.append({
                    "id": str(pid),
                    "title": name,
                    "model_number": model_num,
                    "price": price_num,
                    "price_str": price_str or f"￥{price_num:,}",
                    "url": href,
                    "img_url": img_url,
                    "is_in_stock": is_in_stock,
                    "found_at": int(time.time()),
                })
            except Exception as e:
                logger.error(f"解析单个商品单元出错: {e}")
                continue

        return items

    def fetch_new_arrivals(self, num: int = 100, in_stock_only: bool = True) -> List[Dict]:
        """
        抓取 Dollyteria 最新的上新流 (/new)
        这是最快获悉最新上架商品的方式，上新点往往第一时间出现在此
        """
        url = f"{BASE_URL}/new"
        params = {"num": str(num)}
        if in_stock_only:
            params["available"] = "1"

        html = self._get(url, params=params)
        return self.parse_items(html)

    def search_products(self, keyword: str, num: int = 100, page: int = 1, in_stock_only: bool = True) -> List[Dict]:
        """
        根据关键字在全站搜索有货商品 (/product-list/?keyword=...)
        """
        url = f"{BASE_URL}/product-list/"
        params = {
            "num": str(num),
            "keyword": keyword,
            "order": "newdate",
        }
        if in_stock_only:
            params["available"] = "1"
        if page > 1:
            params["page"] = str(page)

        html = self._get(url, params=params)
        return self.parse_items(html)

    def check_product_stock(self, product_id_or_url: str) -> Dict:
        """
        精确检查单个商品详情页的在库与价格状态
        详情页含有加购按钮 <input name="cart_add_btn" value="カートに入れる"> 即代表有货
        """
        if product_id_or_url.startswith("http"):
            url = product_id_or_url
        else:
            url = f"{BASE_URL}/product/{product_id_or_url}"

        html = self._get(url)
        if not html:
            return {"status": "error", "message": "无法请求商品详情页"}

        soup = BeautifulSoup(html, "html.parser")
        has_cart_btn = bool(soup.select_one("input[name=cart_add_btn]") or soup.select_one(".add_cart_btn"))
        has_cart_form = bool(soup.select_one("form[name=cart_form]") or soup.select_one("#cart_form"))

        price_el = soup.select_one(".selling_price .figure") or soup.select_one(".price")
        price_str = price_el.get_text(strip=True) if price_el else ""
        digits = re.sub(r"[^\d]", "", price_str)
        price_num = int(digits) if digits else 0

        title_el = soup.select_one("h1.goods_name") or soup.find("h1")
        title = title_el.get_text(strip=True) if title_el else ""

        is_in_stock = has_cart_btn and has_cart_form

        return {
            "status": "ok",
            "url": url,
            "title": title,
            "price": price_num,
            "price_str": price_str,
            "is_in_stock": is_in_stock,
        }
