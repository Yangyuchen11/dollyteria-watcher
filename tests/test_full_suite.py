"""
Dollyteria 自动询盘、监控与自动下单软件 —— 全套综合自动化测试套件 (Full Test Suite)

测试目标与覆盖范围：
1. 【商品抓取与询盘 (Scraper)】：
   - 正常情况：列表 HTML 解析 (ID、标题、型号、价格、高清图、在库/水印售罄/文本售罄)、单品详情页在库询盘 (check_product_stock)
   - 上新繁忙期：HTTP 503/502/504/429 状态码退避重试、HTTP 200 伪正常「アクセス集中/大変混み合っております/回線が混雑/ただいま混雑」排队页识别重试、网络超时恢复
2. 【上新全覆盖与多页流式检测 (Coverage & Pagination)】：
   - 大批量上新 (如一次上新 350 件跨越 4 页)：自动顺延翻页，验证 100% 覆盖全部新品 (0 漏检)，跨页目标全部命中
   - 智能早停 (Smart Early Stopping)：遇到整页均为已见商品或不足 100 件时立即停止翻页，节省请求防 503
   - 繁忙期跨页断点穿透恢复：第 1 页抓取成功但第 2 页因 503 失败时，下一轮自动穿透已见的第 1 页继续补抓第 2、3 页，杜绝跨页漏检
   - 基准线初始化 (initialize_baseline) 与严格去重：防首次启动误报、防重复通知与重复下单
   - 爆发模式 (Burst Mode) vs 日常模式 (Normal Mode)：爆发期专攻 /new 跳过关键词搜索；日常期 /new + 关键词全店搜索双向覆盖
3. 【监控规则匹配引擎 (Rule Matching)】：
   - AND (all) / OR (any) 多关键词匹配、标题+型号联合检索、大小写不敏感
   - 排除词 (exclude_keywords) 过滤、价格上下限 (min_price / max_price) 过滤、售罄过滤
   - 多规则重叠优先级：同时命中「仅通知规则」与「自动下单规则」时，优先触发自动下单规则，确保不漏单
4. 【通知系统与自动下单联动 (Notifications & Dispatch)】：
   - 仅通知规则 (auto_order=False)：触发声音、桌面弹窗、Discord、LINE、邮件、Web SSE 实时流，绝不误下单
   - 自动下单规则 (auto_order=True)：同时触发上新发现通知 + 下单成功专属高优通知 (dispatch_order_success)
   - 全局自动下单总开关关闭时的安全拦截
5. 【自动下单全生命周期 (Auto-Order Full Lifecycle)】：
   - 正常情况 Dry-Run 仿真模式：登录 -> 加购 -> 客户信息 -> 配送地址 -> 锁定支付方式 -> 确认页账单解析 -> 安全拦截 -> 自动清空购物车
   - 正常情况真实下单模式 (dry_run=False)：覆盖 代金引換(cod) / 店頭支払(store_pickup) / 銀行振込(bank_transfer)，自动勾选 policy_agree=1 并提交「購入する」直达 /cart/complete
   - 上新繁忙期加购抗压：加购遭遇连续 503、排队占位页、网络超时的高频微重试直至入车
   - 上新繁忙期结算推进抗压：入车后在 /cart/customer (含掉线自动重登)、/cart/shipping、/cart/confirm、最终提交阶段遭遇 503 的全程自动重试恢复
   - 智能售罄熔断 (Circuit Breaker)：显式文本售罄 (売り切れ/在庫不足) 与 Dollyteria 隐式 DOM 售罄 (无加购按钮仅剩 inquiry_btn) 毫秒级熔断终止重试
   - 流式流水线 (Pipeline) 与多商品并发独立会话 (Standby Workers)：第 1 页命中当场非阻塞异步开抢，多商品独立 Session 并行加购互不串车
6. 【定时调度器、归档数据库与 Web API (Scheduler, Archive & Web)】：
   - 调度器倒计时、定点前 20s 自动预热登录 Worker 池、定点开启 Burst 模式与自动恢复
   - 归档库 /new 全量同步与在售库存差集盘点 (reconcile_in_stock_ids)
   - Web API 看板一键买断 (/api/auto_order/buy) 及售罄自动翻转归档状态与 SSE 广播
"""
import os
import sys
import json
import time
import shutil
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
import requests

# 确保控制台 UTF-8 输出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import core.monitor as monitor_mod
from core.scraper import DollyteriaScraper, SOLDOUT_HEX_MARK
from core.archive_db import DollyteriaArchiveDB
from core.archiver import DollyteriaArchiver
from core.monitor import DollyteriaMonitor
from core.scheduler import DollyteriaScheduler
from hooks.auto_order import AutoOrderHook, RecaptchaTokenProvider, DEFAULT_RECAPTCHA_SITEKEY
from notifiers.manager import NotifierManager
from web.app import create_app


# =====================================================================
# 测试辅助构造函数 (Mock HTML 与 HTTP Response 生成器)
# =====================================================================

def make_response(status_code: int = 200, url: str = "https://www.dollyteria.com/", html: str = "") -> requests.Response:
    resp = requests.Response()
    resp.status_code = status_code
    resp.url = url
    resp._content = html.encode("utf-8")
    resp.encoding = "utf-8"
    return resp


def build_product_list_html(items_spec: list, total_count: int = 0) -> str:
    """根据商品规格列表生成标准 Dollyteria 商品列表页 HTML"""
    cells = []
    for sp in items_spec:
        pid = sp["id"]
        title = sp.get("title", f"Doll Product {pid}")
        model = sp.get("model_number", f"I-26-10-26-{pid}")
        price = sp.get("price", 15000)
        price_str = sp.get("price_str", f"¥{price:,}")
        is_in_stock = sp.get("is_in_stock", True)
        soldout_by_img = sp.get("soldout_by_img", False)

        if soldout_by_img:
            img_url = f"/image/{SOLDOUT_HEX_MARK}_{pid}.jpg"
        else:
            img_url = sp.get("img_url", f"/image/item_{pid}.jpg")

        soldout_text = "" if (is_in_stock or soldout_by_img) else '<span class="soldout_mark">売り切れ</span>'
        pid_attr = f'data-product-id="{pid}"' if not sp.get("fallback_class_id") else ""

        cells.append(f"""
        <li class="list_item_cell list_item_{pid}">
            <div class="item_data" {pid_attr}>
                <a class="item_data_link" href="/product/{pid}">
                    <div class="global_photo"><img data-x2="{img_url}" src="{img_url}" /></div>
                    <div class="goods_name">{title}</div>
                    <div class="model_number_value">{model}</div>
                    <div class="selling_price"><span class="figure">{price_str}</span></div>
                    {soldout_text}
                </a>
            </div>
        </li>
        """)
    count_html = f'<div class="count_number"><span class="number">{total_count}</span>件</div>' if total_count else ""
    return f"<html><body>{count_html}<ul>{''.join(cells)}</ul></body></html>"


def build_product_detail_html(pid: str, title: str, price: int, in_stock: bool = True, explicit_soldout_text: bool = False) -> str:
    """生成单品详情页 HTML (支持在库、显式文本售罄、隐式无加购按钮仅剩询问按钮售罄)"""
    if in_stock:
        action_area = f"""
        <form name="cart_form" id="cart_form" action="/product/{pid}" method="post">
            <input type="hidden" name="cart_add_{pid}" value="t" />
            <input type="submit" name="cart_add_btn" class="add_cart_btn" value="カートに入れる" />
        </form>
        """
    elif explicit_soldout_text:
        action_area = '<div class="soldout_msg">大変申し訳ございません。売り切れ 在庫が不足しております。</div>'
    else:
        # Dollyteria 典型隐式售罄：移除了 cart_add_btn，仅保留 inquiry_btn
        action_area = f"""
        <div class="inquiry_area">
            <input type="button" name="inquiry_btn" class="inquiryinput" onclick="location.href='/contact/product/{pid}'" value="商品問い合わせ" />
        </div>
        """
    return f"""
    <html><body>
        <h1 class="goods_name">{title}</h1>
        <div class="selling_price"><span class="figure">¥{price:,}</span></div>
        {action_area}
    </body></html>
    """


def build_checkout_responses(pid: str, title: str, price_str: str = "55,000円", payment_label: str = "店頭支払", need_relogin: bool = False):
    """生成从加购成功到确认页及完成页的标准响应集合"""
    resp_cart = make_response(
        200,
        "https://www.dollyteria.com/cart",
        f"<html><body><h1>ショッピングカート</h1><div>カートの中身: {pid} {title}</div><a href='/cart/customer'>レジに進む</a></body></html>"
    )
    if need_relogin:
        resp_cust_login_form = make_response(
            200,
            "https://www.dollyteria.com/cart/customer",
            "<html><body><form action='/cart/customer' method='post'><input type='password' name='password' /></form></body></html>"
        )
    else:
        resp_cust_login_form = None

    resp_cust_ready = make_response(
        200,
        "https://www.dollyteria.com/cart/customer",
        """
        <html><body>
            <form action="https://www.dollyteria.com/cart/shipping-list" method="post">
                <input type="hidden" name="shipping-id" value="99999999" />
            </form>
        </body></html>
        """
    )

    resp_shipping = make_response(
        200,
        "https://www.dollyteria.com/cart/shipping",
        """
        <html><body>
            <form action="https://www.dollyteria.com/cart/shipping" method="post">
                <select name="delivery[1][shipping_method]"><option value="1" selected>佐川急便</option></select>
                <select name="delivery[1][shipping_list]"><option value="99999999" selected>本人住所</option></select>
                <table>
                    <tr><td><input type="radio" id="pay_1" name="pay_procedure" value="1" /><label for="pay_1">銀行振込</label></td></tr>
                    <tr><td><input type="radio" id="pay_3" name="pay_procedure" value="3" /><label for="pay_3">代金引換</label></td></tr>
                    <tr><td><input type="radio" id="pay_8" name="pay_procedure" value="8" /><label for="pay_8">店頭支払</label></td></tr>
                </table>
            </form>
        </body></html>
        """
    )

    resp_confirm = make_response(
        200,
        "https://www.dollyteria.com/cart/confirm",
        f"""
        <html><body>
            <h1>ご購入内容の最終確認</h1>
            <div class="page_box">
                <a href="/product/{pid}">{title}</a>
                <div>商品合計</div><div>:</div><div>{price_str}</div>
                <div>送料</div><div>:</div><div>660円</div>
                <div>代引手数料</div><div>:</div><div>330円</div>
                <div>お支払い総額</div><div>:</div><div>{price_str}</div>
                <div>お名前</div><div>:</div><div>テスト 太郎 様</div>
                <div>ご住所</div><div>:</div><div>東京都豊島区東池袋1-1-1</div>
                <div>お支払い方法</div><div>変更</div><div>{payment_label}</div>
            </div>
            <form id="register" name="register" action="https://www.dollyteria.com/cart/confirm" method="post">
                <input type="hidden" name="csrf_token" value="tok_12345" />
                <input type="hidden" name="coupon_use" value="f" />
                <input type="checkbox" id="coupon_use_check" name="coupon_use" value="t" />
                <input type="text" id="couponCode" name="couponCode" value="" />
                <input type="submit" name="coupon" value="適用" />
                <input type="checkbox" id="policy_agree" name="policy_agree" value="1" /> 同意する
                <input type="submit" name="go-next" value="購入する" class="recaptcha_button buyinput btn_size_xxlarge btn_color_emphasis" />
            </form>
            <script>
                let s = document.createElement('script');
                s.src = 'https://www.google.com/recaptcha/api.js?render={DEFAULT_RECAPTCHA_SITEKEY}&824122141240';
            </script>
        </body></html>
        """
    )

    resp_confirm_recaptcha_err = make_response(
        200,
        "https://www.dollyteria.com/cart/confirm",
        """
        <html><body>
            <div class="error_box">
                <p class="error_status">reCAPTCHAの検証に失敗しました。再度お試しください。</p>
            </div>
        </body></html>
        """
    )

    resp_complete = make_response(
        200,
        "https://www.dollyteria.com/cart/complete",
        "<html><body><h1>ご注文完了</h1><p>ご購入ありがとうございました。</p></body></html>"
    )

    return {
        "cart": resp_cart,
        "cust_login": resp_cust_login_form,
        "cust_ready": resp_cust_ready,
        "shipping": resp_shipping,
        "confirm": resp_confirm,
        "confirm_recaptcha_err": resp_confirm_recaptcha_err,
        "complete": resp_complete,
    }


class IsolatedMonitorTestCase(unittest.TestCase):
    """测试基类：自动隔离临时目录，保护真实 config.json / state.json / archive.db 不被污染，并拦截真实外部通知"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="dollyteria_test_")
        self.config_path = os.path.join(self.temp_dir, "config.json")
        self.state_path = os.path.join(self.temp_dir, "state.json")
        self.db_path = os.path.join(self.temp_dir, "archive.db")

        # 写入干净的初始测试配置 (默认不填账号密码以防 __init__ 自动启动真实网络登录线程)
        init_cfg = {
            "watches": [],
            "schedule": {
                "enabled": True,
                "drop_times": ["20:00:00", "21:00:00"],
                "burst_duration_sec": 180,
                "burst_interval_sec": 0.3,
                "interval_minutes": 5,
                "interval_enabled": True,
                "max_stream_pages": 5,
            },
            "notifications": {
                "sound_enabled": False,
                "desktop_enabled": False,
                "discord": {"enabled": False, "webhook_url": ""},
                "line": {"enabled": False, "token": ""},
                "email": {"enabled": False, "smtp_host": "", "smtp_port": 465, "smtp_user": "", "smtp_password": "", "receiver_email": ""},
            },
            "auto_order": {
                "enabled": True,
                "dry_run": True,
                "username": "",
                "password": "",
                "payment_method": "cod",
                "max_workers": 6,
                "standby_workers": 2,
                "retry_delay_sec": 0.01,
                "max_retries": 5,
            },
            "quick_buy": {
                "enabled": True,
                "dry_run": True,
            },
            "web_password": "",
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(init_cfg, f, ensure_ascii=False, indent=2)

        self.p_cfg = patch.object(monitor_mod, "CONFIG_PATH", self.config_path)
        self.p_state = patch.object(monitor_mod, "STATE_PATH", self.state_path)
        self.p_db = patch.object(monitor_mod, "ARCHIVE_DB_PATH", self.db_path)
        self.p_cfg.start()
        self.p_state.start()
        self.p_db.start()

    def tearDown(self):
        self.p_cfg.stop()
        self.p_state.stop()
        self.p_db.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)


# =====================================================================
# 1. 商品抓取、HTML解析与上新繁忙期抗压测试 (Scraper)
# =====================================================================

class TestScraperNormalAndBusy(IsolatedMonitorTestCase):

    def test_01_normal_html_parsing_and_stock_detection(self):
        """测试正常情况下的商品列表 HTML 解析：在库商品、图片水印售罄、文本售罄、备用 class ID 提取"""
        scraper = DollyteriaScraper(timeout=5, max_retries=2)
        html = build_product_list_html([
            {"id": "805001", "title": "DearSD/めぐ 本体", "model_number": "I-26-10-26-001", "price": 68000, "is_in_stock": True},
            {"id": "805002", "title": "SDGIRL/アナイス", "model_number": "I-26-10-26-002", "price": 55000, "is_in_stock": True, "soldout_by_img": True},
            {"id": "805003", "title": "Blythe ネオブライス", "model_number": "I-26-10-26-003", "price": 22000, "is_in_stock": False},
            {"id": "805004", "title": "SDM コーディネートモデル", "model_number": "I-26-10-26-004", "price": 48000, "is_in_stock": True, "fallback_class_id": True},
        ], total_count=4)

        items = scraper.parse_items(html)
        self.assertEqual(len(items), 4)

        # 805001: 正常在库
        self.assertEqual(items[0]["id"], "805001")
        self.assertEqual(items[0]["price"], 68000)
        self.assertTrue(items[0]["is_in_stock"])
        self.assertTrue(items[0]["url"].endswith("/product/805001"))

        # 805002: 包含 536f6c644f7574 水印图片 -> 判定为售罄
        self.assertEqual(items[1]["id"], "805002")
        self.assertFalse(items[1]["is_in_stock"])

        # 805003: 包含 売り切れ 文本 -> 判定为售罄
        self.assertEqual(items[2]["id"], "805003")
        self.assertFalse(items[2]["is_in_stock"])

        # 805004: 无 data-product-id 属性，从 list_item_805004 class 兜底提取 ID
        self.assertEqual(items[3]["id"], "805004")
        self.assertTrue(items[3]["is_in_stock"])

    def test_02_single_product_stock_inquiry(self):
        """测试单品详情页精确询盘接口 (check_product_stock)：在库 vs 售罄"""
        scraper = DollyteriaScraper(timeout=5, max_retries=1)

        in_stock_html = build_product_detail_html("805001", "DearSD/めぐ", 68000, in_stock=True)
        sold_out_html = build_product_detail_html("805002", "SDGIRL/アナイス", 55000, in_stock=False)

        with patch.object(scraper, "_get", return_value=in_stock_html):
            res_in = scraper.check_product_stock("805001")
            self.assertEqual(res_in["status"], "ok")
            self.assertTrue(res_in["is_in_stock"])
            self.assertEqual(res_in["price"], 68000)

        with patch.object(scraper, "_get", return_value=sold_out_html):
            res_out = scraper.check_product_stock("https://www.dollyteria.com/product/805002")
            self.assertEqual(res_out["status"], "ok")
            self.assertFalse(res_out["is_in_stock"])

    def test_03_busy_period_http_503_502_429_retry_recovery(self):
        """测试上新繁忙期抓取 /new 遭遇 HTTP 503 / 502 / 429 时自动弹性退避重试并成功恢复"""
        scraper = DollyteriaScraper(timeout=5, max_retries=3)
        valid_html = build_product_list_html([{"id": "806001", "title": "DearSD 新品", "price": 70000, "is_in_stock": True}])

        resp_503 = make_response(503, html="Service Unavailable")
        resp_502 = make_response(502, html="Bad Gateway")
        resp_429 = make_response(429, html="Too Many Requests")
        resp_200 = make_response(200, html=valid_html)

        with patch.object(scraper.session, "get", side_effect=[resp_503, resp_502, resp_429, resp_200]) as mock_get, \
             patch("core.scraper.time.sleep"):
            items = scraper.fetch_new_arrivals(num=100, page=1, in_stock_only=True)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["id"], "806001")
            self.assertEqual(mock_get.call_count, 4)
            self.assertFalse(scraper.last_fetch_failed)

    def test_04_busy_period_200_queue_page_retry_recovery(self):
        """测试上新繁忙期遭遇 HTTP 200 伪正常排队占位页（アクセス集中 / 大変混み合っております / ただいま混雑）自动重试恢复"""
        scraper = DollyteriaScraper(timeout=5, max_retries=3)
        valid_html = build_product_list_html([{"id": "806002", "title": "SDGIRL 新品", "price": 65000, "is_in_stock": True}])

        resp_busy_1 = make_response(200, html="<html><body>ただいまアクセス集中により大変混み合っております。しばらくお待ちください。</body></html>")
        resp_busy_2 = make_response(200, html="<html><body>ただいま混雑しております。回線が混雑しています。</body></html>")
        resp_ok = make_response(200, html=valid_html)

        with patch.object(scraper.session, "get", side_effect=[resp_busy_1, resp_busy_2, resp_ok]) as mock_get, \
             patch("core.scraper.time.sleep"):
            items = scraper.fetch_new_arrivals(num=100, page=1)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["id"], "806002")
            self.assertEqual(mock_get.call_count, 3)
            self.assertFalse(scraper.last_fetch_failed)

    def test_05_network_timeout_and_exhaustion_handling(self):
        """测试网络超时抖动重试恢复，以及连续繁忙耗尽重试次数时安全返回空列表并标记 last_fetch_failed"""
        scraper = DollyteriaScraper(timeout=5, max_retries=2)
        valid_html = build_product_list_html([{"id": "806003", "title": "Blythe 新品", "price": 25000, "is_in_stock": True}])

        # 场景 A: 前两次网络异常，第 3 次成功
        with patch.object(
            scraper.session,
            "get",
            side_effect=[requests.Timeout("Read timed out"), requests.ConnectionError("Connection reset"), make_response(200, html=valid_html)]
        ), patch("core.scraper.time.sleep"):
            items = scraper.fetch_new_arrivals()
            self.assertEqual(len(items), 1)
            self.assertFalse(scraper.last_fetch_failed)

        # 场景 B: 连续 3 次 503 耗尽重试次数 -> 安全降级返回 [] 且标记 last_fetch_failed = True
        with patch.object(
            scraper.session,
            "get",
            side_effect=[make_response(503), make_response(503), make_response(503)]
        ), patch("core.scraper.time.sleep"):
            items_fail = scraper.fetch_new_arrivals()
            self.assertEqual(items_fail, [])
            self.assertTrue(scraper.last_fetch_failed)


# =====================================================================
# 2. 上新全覆盖、多页流式检测与断点穿透测试 (Coverage & Pagination)
# =====================================================================

class TestNewArrivalCoverageAndPagination(IsolatedMonitorTestCase):

    def test_06_massive_drop_350_items_across_4_pages_100pct_coverage(self):
        """
        测试大批量上新 (一次上新 350 件新品跨越 4 页)：
        - 第 1 页：100 件全新商品 (含目标商品 #15)
        - 第 2 页：100 件全新商品 (含目标商品 #142)
        - 第 3 页：100 件全新商品 (含目标商品 #288)
        - 第 4 页：50 件全新商品 (含目标商品 #349) + 50 件昨日旧库存 (已在 seen_ids 中)
        - 第 5 页：100 件昨日旧库存 (全部在 seen_ids 中)
        验证：
        1. 350 件全新商品 100% 全部进入 seen_ids 与 archive.db (0 漏检)
        2. 分布在 4 个不同页面的 4 件目标监控新品全部被精准检测命中，全部触发通知与自动下单！
        """
        monitor = DollyteriaMonitor()
        monitor.config["watches"] = [
            {
                "id": "w_target",
                "name": "高优抢购目标 (DearSD)",
                "keywords": ["DearSD", "限定"],
                "exclude_keywords": ["ジャンク"],
                "min_price": 50000,
                "max_price": 90000,
                "enabled": True,
                "search_mode": "new_stream",
                "match_mode": "all",
                "auto_order": True,
            }
        ]
        monitor.config["schedule"]["max_stream_pages"] = 5
        monitor.config["auto_order"]["enabled"] = True

        # 昨日已有的 150 件旧商品 (编号 old_351 ~ old_500)
        old_ids = [f"old_{i}" for i in range(351, 501)]
        monitor.state["seen_ids"] = list(old_ids)

        target_indices = {15, 142, 288, 349}

        def make_new_item(idx: int):
            is_target = idx in target_indices
            return {
                "id": f"new_{idx}",
                "title": f"DearSD 限定 テストモデル #{idx}" if is_target else f"一般ドール用品 #{idx}",
                "model_number": f"I-26-10-26-{idx:03d}",
                "price": 68000 if is_target else 3500,
                "price_str": "¥68,000" if is_target else "¥3,500",
                "url": f"https://www.dollyteria.com/product/new_{idx}",
                "img_url": f"https://www.dollyteria.com/pic/new_{idx}.jpg",
                "is_in_stock": True,
            }

        def make_old_item(idx: int):
            return {
                "id": f"old_{idx}",
                "title": f"昨日旧商品 #{idx}",
                "model_number": f"I-26-10-25-{idx:03d}",
                "price": 2000,
                "price_str": "¥2,000",
                "url": f"https://www.dollyteria.com/product/old_{idx}",
                "img_url": "",
                "is_in_stock": True,
            }

        page1 = [make_new_item(i) for i in range(1, 101)]
        page2 = [make_new_item(i) for i in range(101, 201)]
        page3 = [make_new_item(i) for i in range(201, 301)]
        page4 = [make_new_item(i) for i in range(301, 351)] + [make_old_item(i) for i in range(351, 401)]
        page5 = [make_old_item(i) for i in range(401, 501)]

        pages_map = {1: page1, 2: page2, 3: page3, 4: page4, 5: page5}
        fetched_pages = []

        def mock_fetch_new(num=100, page=1, in_stock_only=True):
            fetched_pages.append(page)
            return pages_map.get(page, [])

        ordered_item_ids = []
        notified_item_ids = []

        def mock_execute_orders(tasks, wait_completion=False):
            for it, r in tasks:
                ordered_item_ids.append(it["id"])

        def mock_dispatch_notify(items, rule):
            for it in items:
                notified_item_ids.append(it["id"])

        monitor.scraper.fetch_new_arrivals = mock_fetch_new
        monitor._execute_auto_orders_concurrent = mock_execute_orders
        monitor.notifier.dispatch = mock_dispatch_notify

        res = monitor.check_once(is_burst=True)

        self.assertEqual(res["status"], "ok")
        # 验证翻页到第 5 页发现整页无新品后停止
        self.assertEqual(fetched_pages, [1, 2, 3, 4, 5])
        # 验证 350 件全新商品 100% 全部被检测并记录到 seen_ids
        seen_after = set(monitor.state["seen_ids"])
        for i in range(1, 351):
            self.assertIn(f"new_{i}", seen_after, f"新品 new_{i} 未被覆盖！")

        # 验证跨 4 页的 4 件目标商品全部命中并触发了下单与通知
        expected_targets = ["new_15", "new_142", "new_288", "new_349"]
        self.assertEqual(ordered_item_ids, expected_targets)
        self.assertEqual(notified_item_ids, expected_targets)
        self.assertEqual(res["new_items_count"], 4)

    def test_07_smart_early_stopping_on_seen_page(self):
        """测试智能早停机制：当第 1 页全部为已见商品时，仅请求第 1 页即刻早停，绝不请求第 2~5 页"""
        monitor = DollyteriaMonitor()
        monitor.config["watches"] = [
            {"id": "w1", "name": "Rule 1", "keywords": ["Blythe"], "enabled": True, "search_mode": "new_stream"}
        ]
        page1 = [{"id": f"seen_{i}", "title": f"Item {i}", "price": 1000, "is_in_stock": True} for i in range(1, 101)]
        monitor.state["seen_ids"] = [f"seen_{i}" for i in range(1, 101)]

        mock_fetch = MagicMock(return_value=page1)
        monitor.scraper.fetch_new_arrivals = mock_fetch

        res = monitor.check_once(is_burst=True)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(mock_fetch.call_count, 1, "第 1 页全为已见商品时应立即早停，不应继续翻页")

    def test_08_multi_page_busy_interruption_and_penetration_recovery(self):
        """
        测试繁忙期跨页断点穿透恢复：
        - 一次上新 200 件跨越 2 页（第 1 页 1~100，第 2 页 101~200 含目标商品 new_188）
        - 第 1 轮检测：成功拉取第 1 页（1~100 已记入 seen_ids），拉取第 2 页时遭遇连续 503 失败中断
        - 第 2 轮检测：第 1 页虽然已全部在 seen_ids 中 (page_new_items == 0)，但系统记得第 2 页曾中断，
          自动穿透第 1 页继续抓取第 2 页，成功捕获并下单 new_188！
        """
        monitor = DollyteriaMonitor()
        monitor.config["watches"] = [
            {
                "id": "w_sd",
                "name": "SD 目标",
                "keywords": ["SDGIRL"],
                "enabled": True,
                "search_mode": "new_stream",
                "match_mode": "any",
                "auto_order": True,
            }
        ]
        monitor.config["auto_order"]["enabled"] = True
        monitor.state["seen_ids"] = [f"old_{i}" for i in range(1, 101)]

        page1 = [{"id": f"new_{i}", "title": f"Normal {i}", "price": 2000, "is_in_stock": True} for i in range(1, 101)]
        page2 = [
            {"id": f"new_{i}", "title": "SDGIRL アナイス Target" if i == 188 else f"Normal {i}", "price": 60000, "is_in_stock": True}
            for i in range(101, 201)
        ]
        page3 = [{"id": f"old_{i}", "title": f"Old {i}", "price": 1000, "is_in_stock": True} for i in range(1, 101)]

        round_num = [1]

        def mock_fetch(num=100, page=1, in_stock_only=True):
            if round_num[0] == 1:
                if page == 1:
                    monitor.scraper.last_fetch_failed = False
                    return page1
                elif page == 2:
                    # 第 1 轮第 2 页遭遇 503 彻底失败
                    monitor.scraper.last_fetch_failed = True
                    return []
            else:
                monitor.scraper.last_fetch_failed = False
                if page == 1:
                    return page1
                elif page == 2:
                    return page2
                elif page == 3:
                    return page3
            return []

        ordered_ids = []
        monitor.scraper.fetch_new_arrivals = mock_fetch
        monitor._execute_auto_orders_concurrent = lambda tasks, wait_completion=False: ordered_ids.extend(it["id"] for it, _ in tasks)
        monitor.notifier.dispatch = MagicMock()

        # 执行第 1 轮 (第 2 页 503 中断)
        res1 = monitor.check_once(is_burst=True)
        self.assertEqual(res1["new_items_count"], 0)
        self.assertEqual(monitor._incomplete_from_page, 2)

        # 执行第 2 轮 (自动穿透第 1 页，成功捕获第 2 页的 new_188！)
        round_num[0] = 2
        res2 = monitor.check_once(is_burst=True)
        self.assertEqual(res2["new_items_count"], 1)
        self.assertEqual(ordered_ids, ["new_188"])
        self.assertEqual(monitor._incomplete_from_page, 0)

    def test_09_baseline_initialization_and_deduplication(self):
        """测试基准线初始化 (initialize_baseline) 与连续轮询防重复触发机制"""
        monitor = DollyteriaMonitor()
        monitor.config["watches"] = [
            {"id": "w_blythe", "name": "Blythe", "keywords": ["Blythe"], "enabled": True, "search_mode": "both", "match_mode": "any", "auto_order": True}
        ]
        monitor.config["auto_order"]["enabled"] = True

        existing_items = [
            {"id": "base_1", "title": "Blythe 既有库存 1", "price": 15000, "is_in_stock": True},
            {"id": "base_2", "title": "Blythe 既有库存 2", "price": 18000, "is_in_stock": True},
        ]
        monitor.scraper.fetch_new_arrivals = MagicMock(return_value=existing_items)
        monitor.scraper.search_products = MagicMock(return_value=existing_items)
        monitor._execute_auto_orders_concurrent = MagicMock()
        monitor.notifier.dispatch = MagicMock()

        # 1. 建立基准线
        monitor.initialize_baseline()
        self.assertIn("base_1", monitor.state["seen_ids"])
        self.assertIn("base_2", monitor.state["seen_ids"])

        # 2. 巡检时既有库存不应触发任何报警或下单
        res_initial = monitor.check_once(is_burst=False)
        self.assertEqual(res_initial["new_items_count"], 0)
        monitor._execute_auto_orders_concurrent.assert_not_called()

        # 3. 出现 1 件真正的新品 new_999
        drop_items = [{"id": "new_999", "title": "Blythe 全新上架限定", "price": 25000, "is_in_stock": True}] + existing_items
        monitor.scraper.fetch_new_arrivals = MagicMock(return_value=drop_items)

        res_drop_1 = monitor.check_once(is_burst=True)
        self.assertEqual(res_drop_1["new_items_count"], 1)
        self.assertEqual(monitor._execute_auto_orders_concurrent.call_count, 1)

        # 4. 0.3 秒后下一轮高频检测再次看到 new_999，绝不重复下单或重复通知
        res_drop_2 = monitor.check_once(is_burst=True)
        self.assertEqual(res_drop_2["new_items_count"], 0)
        self.assertEqual(monitor._execute_auto_orders_concurrent.call_count, 1)

    def test_10_burst_mode_vs_normal_mode_search_strategy(self):
        """测试爆发模式 (专攻 /new) 与日常平缓模式 (/new + 关键词全站搜索补漏) 的策略切换"""
        monitor = DollyteriaMonitor()
        monitor.config["watches"] = [
            {
                "id": "w_kw",
                "name": "全店搜索规则",
                "keywords": ["SDM", "OF"],
                "enabled": True,
                "search_mode": "both",
                "match_mode": "all",
                "auto_order": True,
            }
        ]
        monitor.config["auto_order"]["enabled"] = True

        kw_only_item = [{"id": "kw_777", "title": "ボークス SDM ドレス OFセット", "price": 6500, "is_in_stock": True}]
        monitor.scraper.fetch_new_arrivals = MagicMock(return_value=[])
        monitor.scraper.search_products = MagicMock(return_value=kw_only_item)
        monitor._execute_auto_orders_concurrent = MagicMock()
        monitor.notifier.dispatch = MagicMock()

        # 爆发模式：跳过 search_products
        res_burst = monitor.check_once(is_burst=True)
        self.assertEqual(res_burst["new_items_count"], 0)
        monitor.scraper.search_products.assert_not_called()

        # 日常模式：执行 search_products("SDM OF") 并命中下单
        res_normal = monitor.check_once(is_burst=False)
        self.assertEqual(res_normal["new_items_count"], 1)
        monitor.scraper.search_products.assert_called_once_with("SDM OF", num=100, page=1, in_stock_only=True)
        monitor._execute_auto_orders_concurrent.assert_called_once()


# =====================================================================
# 3. 监控规则精准匹配引擎与优先级测试 (Rule Matching Engine)
# =====================================================================

class TestWatchRuleMatchingEngine(IsolatedMonitorTestCase):

    def test_11_keyword_match_modes_all_and_any(self):
        """测试 match_mode='all' (AND) 与 'any' (OR) 以及标题+型号跨字段大小写不敏感匹配"""
        monitor = DollyteriaMonitor()

        rule_all = {
            "keywords": ["DearSD", "めぐ"],
            "exclude_keywords": [],
            "min_price": 0,
            "max_price": 0,
            "match_mode": "all",
        }
        rule_any = {
            "keywords": ["ブライス", "Blythe"],
            "exclude_keywords": [],
            "min_price": 0,
            "max_price": 0,
            "match_mode": "any",
        }

        # 同时包含 DearSD 和 めぐ (大小写混合) -> 命中 rule_all
        self.assertTrue(monitor._matches_rule({"title": "ボークス dearsd/めぐ 本体", "model_number": "DSD-01", "price": 65000, "is_in_stock": True}, rule_all))
        # 仅包含 DearSD 不含 めぐ -> 不命中 rule_all
        self.assertFalse(monitor._matches_rule({"title": "ボークス DearSD/キラ 本体", "model_number": "DSD-02", "price": 65000, "is_in_stock": True}, rule_all))

        # 仅包含 Blythe -> 命中 rule_any
        self.assertTrue(monitor._matches_rule({"title": "Neo blythe Doll", "model_number": "", "price": 20000, "is_in_stock": True}, rule_any))
        # 仅包含 ブライス -> 命中 rule_any
        self.assertTrue(monitor._matches_rule({"title": "ネオブライス 本体", "model_number": "", "price": 20000, "is_in_stock": True}, rule_any))

    def test_12_exclude_keywords_and_price_boundaries(self):
        """测试排除词过滤 (難あり/ジャンク)、价格区间上下限过滤、以及售罄过滤"""
        monitor = DollyteriaMonitor()
        rule = {
            "keywords": ["SDGIRL", "アナイス"],
            "exclude_keywords": ["難あり", "ジャンク", "ウィッグのみ"],
            "min_price": 45000,
            "max_price": 80000,
            "match_mode": "all",
        }

        # 1. 完全符合条件 -> True
        self.assertTrue(monitor._matches_rule({"title": "SDGIRL/アナイス2nd", "price": 58000, "is_in_stock": True}, rule))
        # 2. 命中排除词「難あり」 -> False (防止买到瑕疵/残次品)
        self.assertFalse(monitor._matches_rule({"title": "【難あり】SDGIRL/アナイス2nd", "price": 58000, "is_in_stock": True}, rule))
        # 3. 价格低于 min_price (如仅售假发/配件 3500 日元) -> False (防止把配件当成本体下单)
        self.assertFalse(monitor._matches_rule({"title": "SDGIRL/アナイス デフォルトウィッグ", "price": 3500, "is_in_stock": True}, rule))
        # 4. 价格高于 max_price (超出预算 95000 日元) -> False
        self.assertFalse(monitor._matches_rule({"title": "SDGIRL/アナイス フルセット", "price": 95000, "is_in_stock": True}, rule))
        # 5. 已售罄 (is_in_stock=False) -> False
        self.assertFalse(monitor._matches_rule({"title": "SDGIRL/アナイス2nd", "price": 58000, "is_in_stock": False}, rule))

    def test_13_overlapping_rules_auto_order_priority(self):
        """
        测试多规则重叠时的自动下单高优先级保障：
        若用户配置了规则 A (宽泛监控 'SD'，仅通知 auto_order=False，排在列表第 1 位)
        和规则 B (精准监控 'SDGIRL アナイス'，自动下单 auto_order=True，排在列表第 2 位)，
        当『SDGIRL アナイス』上架时，必须优先匹配规则 B 并触发自动下单，绝不能被规则 A 截胡导致漏单！
        """
        monitor = DollyteriaMonitor()
        monitor.config["watches"] = [
            {
                "id": "rule_broad_notify_only",
                "name": "宽泛 SD 提醒 (不自动买)",
                "keywords": ["SD"],
                "exclude_keywords": [],
                "min_price": 0,
                "max_price": 0,
                "enabled": True,
                "search_mode": "new_stream",
                "match_mode": "any",
                "auto_order": False,
            },
            {
                "id": "rule_specific_auto_buy",
                "name": "精准 アナイス 抢购 (自动下单)",
                "keywords": ["SDGIRL", "アナイス"],
                "exclude_keywords": [],
                "min_price": 40000,
                "max_price": 90000,
                "enabled": True,
                "search_mode": "new_stream",
                "match_mode": "all",
                "auto_order": True,
            },
        ]
        monitor.config["auto_order"]["enabled"] = True

        item = {"id": "807777", "title": "SDGIRL/アナイス2nd 本体", "price": 62000, "is_in_stock": True}
        monitor.scraper.fetch_new_arrivals = MagicMock(return_value=[item])
        ordered_tasks = []
        monitor._execute_auto_orders_concurrent = lambda tasks, wait_completion=False: ordered_tasks.extend(tasks)
        monitor.notifier.dispatch = MagicMock()

        res = monitor.check_once(is_burst=True)
        self.assertEqual(res["new_items_count"], 1)
        self.assertEqual(len(ordered_tasks), 1, "应当优先命中 auto_order=True 的规则并触发自动下单")
        self.assertEqual(ordered_tasks[0][1]["id"], "rule_specific_auto_buy")


# =====================================================================
# 4. 通知系统全渠道触发与自动下单联动测试 (Notifications & Dispatch)
# =====================================================================

class TestNotificationAndAutoOrderDispatch(IsolatedMonitorTestCase):

    def test_14_notify_only_rule_triggers_all_channels_without_ordering(self):
        """测试仅通知规则 (auto_order=False)：命中新品时触发所有已启用通知通道与 SSE，且绝不触发自动下单"""
        monitor = DollyteriaMonitor()
        monitor.config["watches"] = [
            {
                "id": "w_notify_only",
                "name": "Blythe 仅通知监控",
                "keywords": ["Blythe"],
                "enabled": True,
                "search_mode": "new_stream",
                "match_mode": "any",
                "auto_order": False,
            }
        ]
        monitor.config["auto_order"]["enabled"] = True

        # 启用全部 5 个通知渠道并 Mock 底层发送函数
        monitor.notifier.sound.enabled = True
        monitor.notifier.desktop.enabled = True
        monitor.notifier.discord.enabled = True
        monitor.notifier.discord.webhook_url = "https://discord.com/api/webhooks/123/abc"
        monitor.notifier.line.enabled = True
        monitor.notifier.line.token = "mock_line_token"
        monitor.notifier.email.enabled = True
        monitor.notifier.email.smtp_host = "smtp.mock.com"
        monitor.notifier.email.smtp_user = "bot@mock.com"
        monitor.notifier.email.smtp_password = "secret"

        called_channels = []
        monitor.notifier.sound._play_chime = lambda: called_channels.append("sound")
        monitor.notifier.desktop._show_windows_toast = lambda t, m, u: called_channels.append("desktop")
        monitor.notifier.discord._send_webhook = lambda p: (called_channels.append("discord"), True)[1]
        monitor.notifier.line._send_line_notify = lambda m, image_thumbnail="": (called_channels.append("line"), True)[1]
        monitor.notifier.email._send_email = lambda s, h: (called_channels.append("email"), True)[1]

        sse_events = []
        monitor.notifier.add_listener(lambda evt: sse_events.append(evt))

        monitor.scraper.fetch_new_arrivals = MagicMock(return_value=[
            {"id": "808001", "title": "Blythe 限定モデル", "price": 28000, "price_str": "¥28,000", "url": "https://www.dollyteria.com/product/808001", "img_url": "https://www.dollyteria.com/pic/1.jpg", "is_in_stock": True}
        ])
        monitor._execute_auto_orders_concurrent = MagicMock()

        res = monitor.check_once(is_burst=True)
        time.sleep(0.15)  # 等待异步通知线程完成

        self.assertEqual(res["new_items_count"], 1)
        # 验证未触发自动下单
        monitor._execute_auto_orders_concurrent.assert_not_called()
        # 验证 5 大通知渠道 + SSE 全部被触发
        for ch in ["sound", "desktop", "discord", "line", "email"]:
            self.assertIn(ch, called_channels, f"通知渠道 {ch} 未被触发")
        self.assertEqual(len(sse_events), 1)
        self.assertEqual(sse_events[0]["items"][0]["id"], "808001")

    def test_15_auto_order_rule_triggers_both_discovery_and_order_success_notifications(self):
        """测试自动下单规则 (auto_order=True)：命中新品时既触发上新发现通知，又在抢购成功后分发【自动下单成功】专属通知"""
        monitor = DollyteriaMonitor()
        monitor.config["watches"] = [
            {
                "id": "w_auto_buy",
                "name": "DearSD 自动抢购",
                "keywords": ["DearSD"],
                "enabled": True,
                "search_mode": "new_stream",
                "match_mode": "any",
                "auto_order": True,
            }
        ]
        monitor.config["auto_order"]["enabled"] = True

        # 开启桌面、Discord、LINE、Email 的成功下单通知
        monitor.notifier.desktop.enabled = True
        monitor.notifier.discord.enabled = True
        monitor.notifier.discord.webhook_url = "https://discord.com/api/webhooks/123/abc"
        monitor.notifier.line.enabled = True
        monitor.notifier.line.token = "mock_line_token"
        monitor.notifier.email.enabled = True
        monitor.notifier.email.smtp_host = "smtp.mock.com"
        monitor.notifier.email.smtp_user = "bot@mock.com"
        monitor.notifier.email.smtp_password = "secret"

        order_notify_channels = []
        monitor.notifier.desktop._show_windows_toast = lambda t, m, u: order_notify_channels.append(("desktop", t))
        monitor.notifier.discord._send_webhook = lambda p: (order_notify_channels.append(("discord", p["embeds"][0]["title"])), True)[1]
        monitor.notifier.line._send_line_notify = lambda m, image_thumbnail="": (order_notify_channels.append(("line", m)), True)[1]
        monitor.notifier.email._send_email = lambda s, h: (order_notify_channels.append(("email", s)), True)[1]

        sse_events = []
        monitor.notifier.add_listener(lambda evt: sse_events.append(evt))

        target_item = {
            "id": "808002",
            "title": "DearSD/めぐ 美品",
            "model_number": "I-26-10-26-888",
            "price": 68000,
            "price_str": "¥68,000",
            "url": "https://www.dollyteria.com/product/808002",
            "img_url": "https://www.dollyteria.com/pic/808002.jpg",
            "is_in_stock": True,
        }
        monitor.scraper.fetch_new_arrivals = MagicMock(return_value=[target_item])

        # Mock Worker 返回抢购成功结果
        fake_worker = MagicMock()
        fake_worker.on_item_matched.return_value = {
            "success": True,
            "dry_run": False,
            "step": "order_complete",
            "elapsed_sec": 0.42,
            "payment_method": "店頭支払",
            "details": {"total_price": "68,000円", "payment_method": "店頭支払", "recipient_name": "Yanagi"},
        }
        monitor.auto_order.get_worker = MagicMock(return_value=fake_worker)
        monitor.auto_order.release_worker = MagicMock()

        res = monitor.check_once(is_burst=True)
        time.sleep(0.25)  # 等待异步抢购线程池与通知线程执行完毕

        self.assertEqual(res["new_items_count"], 1)
        fake_worker.on_item_matched.assert_called_once()
        # 验证 SSE 收到了 2 条事件：1 条上新发现事件 + 1 条 order_success 事件
        event_types = [e.get("type", "discovery") for e in sse_events]
        self.assertIn("discovery", event_types)
        self.assertIn("order_success", event_types)

        # 验证各渠道收到了【自动下单成功】通知
        ch_names = [c[0] for c in order_notify_channels]
        for expected_ch in ["desktop", "discord", "line", "email"]:
            self.assertIn(expected_ch, ch_names, f"下单成功通知未送达渠道: {expected_ch}")

    def test_16_global_auto_order_switch_disabled_safety(self):
        """测试全局 auto_order.enabled=False 时，即便单条规则 auto_order=True 也严格只发通知不下单"""
        monitor = DollyteriaMonitor()
        monitor.config["watches"] = [
            {"id": "w1", "name": "Rule 1", "keywords": ["DearSD"], "enabled": True, "auto_order": True}
        ]
        monitor.config["auto_order"]["enabled"] = False

        monitor.scraper.fetch_new_arrivals = MagicMock(return_value=[
            {"id": "808003", "title": "DearSD 本体", "price": 60000, "is_in_stock": True}
        ])
        monitor._execute_auto_orders_concurrent = MagicMock()
        monitor.notifier.dispatch = MagicMock()

        res = monitor.check_once(is_burst=True)
        self.assertEqual(res["new_items_count"], 1)
        monitor._execute_auto_orders_concurrent.assert_not_called()
        monitor.notifier.dispatch.assert_called_once()


# =====================================================================
# 5. 自动下单全生命周期测试：正常情况 + 繁忙期 503 重试 + 售罄熔断 (AutoOrderHook)
# =====================================================================

class TestAutoOrderCheckoutFullLifecycle(IsolatedMonitorTestCase):

    def test_17_normal_checkout_dry_run_full_flow_and_cart_cleanup(self):
        """
        测试正常情况下的 Dry-Run 仿真下单全链路：
        登录 -> 加购 -> 客户信息 -> 配送地址 -> 锁定支付方式 -> 抵达确认页提取账单明细 -> 安全拦截不点最终按钮 -> 自动清空购物车
        """
        hook = AutoOrderHook(
            username="buyer@example.com",
            password="secret_password",
            dry_run=True,
            payment_method="cod",
            retry_delay=0.01,
            max_retries=3,
        )

        resps = build_checkout_responses("809001", "DearSD/めぐ フルセット", "68,000円", payment_label="代金引換")
        resp_login_page = make_response(200, "https://www.dollyteria.com/member-login", "<html>Login</html>")
        resp_login_post = make_response(200, "https://www.dollyteria.com/member", "<html>マイページ ログアウト テスト様</html>")
        resp_member_check = make_response(200, "https://www.dollyteria.com/member", "<html>マイページ ログアウト ポイント テスト様</html>")
        resp_cart_cleanup_get = make_response(
            200,
            "https://www.dollyteria.com/cart",
            '<html><body><input type="submit" name="cart_remove_809001" value="削除" /></body></html>'
        )
        resp_cart_cleanup_post = make_response(200, "https://www.dollyteria.com/cart", "<html>Empty</html>")

        post_calls = []

        def mock_request(method, url, **kwargs):
            if method == "GET" and "member-login" in url:
                return resp_login_page
            if method == "POST" and "member-login" in url:
                return resp_login_post
            if method == "GET" and url.endswith("/member"):
                return resp_member_check
            if method == "GET" and "/cart/customer" in url:
                return resps["cust_ready"]
            if method == "POST" and "shipping-list" in url:
                return resps["shipping"]
            if method == "POST" and "/cart/shipping" in url:
                post_calls.append(("shipping", kwargs.get("data", {})))
                return resps["confirm"]
            if method == "GET" and url.endswith("/cart"):
                return resp_cart_cleanup_get
            if method == "POST" and "/cart/confirm" in url:
                post_calls.append(("FINAL_SUBMIT_SHOULD_NOT_HAPPEN", kwargs.get("data", {})))
                return resps["complete"]
            return make_response(200)

        def mock_post(url, data=None, **kwargs):
            post_calls.append(("post", url, data))
            if "/product/809001" in url:
                return resps["cart"]
            if url.endswith("/cart"):
                return resp_cart_cleanup_post
            return make_response(200)

        hook.session.request = mock_request
        hook.session.post = mock_post

        result = hook.execute_checkout("809001", "DearSD/めぐ フルセット", dry_run=True, clear_after_dry_run=True)

        self.assertTrue(result["success"])
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["step"], "confirm_reached")
        self.assertEqual(result["details"]["payment_method"], "代金引換")
        self.assertTrue(result["details"]["is_cod_verified"])
        self.assertTrue(result["details"]["has_policy_agree"])
        self.assertIn("テスト 太郎", result["details"]["recipient_name"])

        # 验证选择了 pay_procedure=3 (代金引換)
        shipping_posts = [c for c in post_calls if c[0] == "shipping"]
        self.assertEqual(len(shipping_posts), 1)
        self.assertEqual(shipping_posts[0][1]["pay_procedure"], "3")

        # 验证 Dry-Run 绝未提交最终确认表单，且执行了购物车清理 (cart_remove_809001)
        final_submits = [c for c in post_calls if c[0] == "FINAL_SUBMIT_SHOULD_NOT_HAPPEN"]
        self.assertEqual(len(final_submits), 0, "Dry-Run 模式下绝不能提交最终购买请求！")
        cart_removes = [c for c in post_calls if c[0] == "post" and c[2] and "cart_remove_809001" in c[2]]
        self.assertEqual(len(cart_removes), 1, "Dry-Run 结束后应自动清空测试商品")

    def test_18_normal_checkout_real_order_submission_all_payment_methods(self):
        """
        测试真实下单模式 (dry_run=False) 全链路及 3 种支付方式 (代金引換 / 店頭支払 / 銀行振込)：
        严格校验：
        1. 正确选择对应 pay_procedure (3 / 8 / 1)
        2. 自动勾选 policy_agree='1'、提交 go-next='購入する'
        3. 排除无关的优惠券 checkbox (coupon_use 保持 'f'，不误触 coupon='適用' 提交按钮)
        4. 必须携带非空的 Google reCAPTCHA v3 令牌 (recaptcha_token)，若缺失则模拟官网返回 reCAPTCHA 报错拦截
        """
        payment_cases = [
            ("cod", "3", "代金引換"),
            ("store_pickup", "8", "店頭支払"),
            ("bank_transfer", "1", "銀行振込"),
        ]

        mock_recaptcha_token = "0cAFcWeA_valid_recaptcha_v3_token_" + ("x" * 200)

        for method_code, expected_pay_val, expected_label in payment_cases:
            with self.subTest(payment_method=method_code):
                hook = AutoOrderHook(
                    username="buyer@example.com",
                    password="secret_password",
                    dry_run=False,
                    payment_method=method_code,
                    retry_delay=0.01,
                    max_retries=3,
                )
                hook.is_logged_in = True  # 模拟已预登录就绪
                resps = build_checkout_responses("809002", "SDGIRL/アナイス", "55,000円", payment_label=expected_label)

                captured_shipping_data = {}
                captured_final_data = {}

                def mock_request(method, url, **kwargs):
                    if method == "GET" and "/cart/customer" in url:
                        return resps["cust_ready"]
                    if method == "POST" and "shipping-list" in url:
                        return resps["shipping"]
                    if method == "POST" and "/cart/shipping" in url:
                        captured_shipping_data.update(kwargs.get("data", {}))
                        return resps["confirm"]
                    if method == "POST" and "/cart/confirm" in url:
                        data = kwargs.get("data", {})
                        captured_final_data.update(data)
                        # 模拟 Dollyteria 官网真实校验：缺少 recaptcha_token 或 policy_agree!=1 则拒绝完成订单！
                        if not data.get("recaptcha_token") or data.get("policy_agree") != "1":
                            return resps["confirm_recaptcha_err"]
                        return resps["complete"]
                    return make_response(200)

                hook.session.request = mock_request
                hook.session.post = MagicMock(return_value=resps["cart"])

                with patch.object(RecaptchaTokenProvider.get_instance(), "get_token", return_value=mock_recaptcha_token):
                    res = hook.execute_checkout("809002", "SDGIRL/アナイス", dry_run=False, payment_method=method_code)

                self.assertTrue(res["success"], f"支付方式 {method_code} 真实下单应当成功: {res}")
                self.assertFalse(res["dry_run"])
                self.assertEqual(res["step"], "order_complete")
                self.assertEqual(captured_shipping_data.get("pay_procedure"), expected_pay_val)
                # 验证最终提交自动勾选了规约同意 (policy_agree=1)、購入する、以及注入了 recaptcha_token
                self.assertEqual(captured_final_data.get("policy_agree"), "1")
                self.assertEqual(captured_final_data.get("go-next"), "購入する")
                self.assertEqual(captured_final_data.get("csrf_token"), "tok_12345")
                self.assertEqual(captured_final_data.get("coupon_use"), "f", "不应误勾选未使用的优惠券 checkbox")
                self.assertNotIn("coupon", captured_final_data, "不应包含优惠券『適用』submit 按钮")
                self.assertEqual(captured_final_data.get("recaptcha_token"), mock_recaptcha_token)

    def test_19_busy_period_add_to_cart_503_and_queue_page_retries(self):
        """
        测试上新繁忙期加购抗压能力：
        加购请求连续遭遇 HTTP 503 -> HTTP 502 -> 200「アクセス集中」排队页 -> 网络 Timeout -> 第 5 次成功进入购物车
        """
        hook = AutoOrderHook(username="u@e.com", password="p", retry_delay=0.01, max_retries=6)

        resp_503 = make_response(503, "https://www.dollyteria.com/product/809003", "503 Service Unavailable")
        resp_502 = make_response(502, "https://www.dollyteria.com/product/809003", "502 Bad Gateway")
        resp_queue = make_response(200, "https://www.dollyteria.com/product/809003", "<html>アクセス集中により大変混み合っております</html>")
        resp_cart_ok = make_response(200, "https://www.dollyteria.com/cart", "<html>ショッピングカート 809003 レジに進む</html>")

        hook.session.post = MagicMock(side_effect=[
            resp_503,
            resp_502,
            resp_queue,
            requests.Timeout("Connection timed out"),
            resp_cart_ok,
        ])

        ok, msg, is_sold_out = hook.add_to_cart("809003")
        self.assertTrue(ok)
        self.assertFalse(is_sold_out)
        self.assertEqual(hook.session.post.call_count, 5)

    def test_20_busy_period_checkout_steps_503_resilience_and_relogin(self):
        """
        测试上新繁忙期结算推进全链路抗压恢复：
        商品入车后，在：
        1. 访问 /cart/customer 遭遇 503 + 会话过期要求重登
        2. 提交结算内登录遭遇 502 后重试成功
        3. 选择配送地址遭遇「ただいま混雑」排队页后重试成功
        4. 提交支付方式进确认页遭遇 Timeout 后重试成功
        5. 最终提交「購入する」遭遇 503 后重试成功完成订单！
        验证：入车后的商品在结算任意环节遇到 503 均能顽强重试直至下单成功！
        """
        hook = AutoOrderHook(
            username="buyer@example.com",
            password="secret_password",
            dry_run=False,
            payment_method="store_pickup",
            retry_delay=0.01,
            max_retries=5,
        )
        hook.is_logged_in = True
        resps = build_checkout_responses("809004", "DearSD/キラ", "72,000円", payment_label="店頭支払", need_relogin=True)

        resp_503 = make_response(503, html="Service Unavailable")
        resp_502 = make_response(502, html="Bad Gateway")
        resp_busy_html = make_response(200, html="<html>ただいま混雑しております。しばらくお待ちください。</html>")

        # 按顺序排列 _safe_request 的响应序列
        request_sequence = [
            # 1. GET /cart/customer: 第 1 次 503，第 2 次返回要求重登的页面
            resp_503,
            resps["cust_login"],
            # 2. POST /cart/customer (结算内重登): 第 1 次 502，第 2 次成功进入地址选择
            resp_502,
            resps["cust_ready"],
            # 3. POST /cart/shipping-list: 第 1 次返回 200 排队页，第 2 次成功进入配送支付表单
            resp_busy_html,
            resps["shipping"],
            # 4. POST /cart/shipping (提交支付方式): 第 1 次网络超时，第 2 次抵达确认页
            requests.Timeout("Timeout on shipping submit"),
            resps["confirm"],
            # 5. POST /cart/confirm (最终提交 購入する): 第 1 次 503，第 2 次成功抵达 /cart/complete
            resp_503,
            resps["complete"],
        ]

        hook.session.post = MagicMock(return_value=resps["cart"])
        hook.session.request = MagicMock(side_effect=request_sequence)

        with patch.object(RecaptchaTokenProvider.get_instance(), "get_token", return_value="0cAFcWeA_valid_token_" + ("y" * 200)):
            res = hook.execute_checkout("809004", "DearSD/キラ", dry_run=False, payment_method="store_pickup")
        self.assertTrue(res["success"], f"结算途中遭遇 503 应自动重试恢复并完成下单，实际结果: {res}")
        self.assertEqual(res["step"], "order_complete")
        self.assertEqual(hook.session.request.call_count, 10)

    def test_21_sold_out_circuit_breaker_explicit_and_implicit_dom(self):
        """
        测试智能售罄熔断 (Sold-Out Circuit Breaker)：
        - 场景 A：第 1 次 503，第 2 次返回显式文本「売り切れ / 在庫が不足」 -> 立即熔断终止重试 (is_sold_out=True)
        - 场景 B：第 1 次 503，第 2 次返回 Dollyteria 详情页隐式售罄结构 (无 cart_add_btn，仅剩 inquiry_btn 商品询问按钮) -> 立即熔断终止重试 (is_sold_out=True)
        """
        hook = AutoOrderHook(username="u@e.com", password="p", retry_delay=0.01, max_retries=10)
        resp_503 = make_response(503, "https://www.dollyteria.com/product/809005", "503 Busy")

        # 场景 A: 显式文本售罄
        html_explicit_soldout = build_product_detail_html("809005", "Sold Doll A", 50000, in_stock=False, explicit_soldout_text=True)
        resp_explicit_soldout = make_response(200, "https://www.dollyteria.com/product/809005", html_explicit_soldout)

        hook.session.post = MagicMock(side_effect=[resp_503, resp_explicit_soldout])
        ok_a, msg_a, sold_out_a = hook.add_to_cart("809005")
        self.assertFalse(ok_a)
        self.assertTrue(sold_out_a)
        self.assertEqual(hook.session.post.call_count, 2, "检测到显式售罄后应立即熔断，不得继续重试剩余 9 次")

        # 场景 B: Dollyteria 隐式 DOM 售罄 (移除加购按钮，仅保留商品询问按钮 inquiry_btn)
        html_implicit_soldout = build_product_detail_html("809006", "Sold Doll B", 60000, in_stock=False, explicit_soldout_text=False)
        resp_implicit_soldout = make_response(200, "https://www.dollyteria.com/product/809006", html_implicit_soldout)

        hook.session.post = MagicMock(side_effect=[resp_503, resp_implicit_soldout])
        ok_b, msg_b, sold_out_b = hook.add_to_cart("809006")
        self.assertFalse(ok_b)
        self.assertTrue(sold_out_b)
        self.assertEqual(hook.session.post.call_count, 2, "检测到详情页无加购按钮仅剩询问按钮时应立即判定售罄并熔断")

    def test_22_pipeline_non_blocking_and_concurrent_multi_item_workers(self):
        """
        测试流式非阻塞抢购流水线 (Pipeline) 与多商品并发独立 Session 隔离：
        1. 同时命中 3 件不同商品时，分配 3 个独立的 AutoOrderHook Worker (独立 Session 对象) 在多线程并行抢购
        2. 即使后台抢购耗时 0.3 秒，主监控 check_once() 也能在几毫秒内立即返回，绝不阻塞下一轮 /new 抓取
        """
        monitor = DollyteriaMonitor()
        monitor.config["watches"] = [
            {"id": "w_all", "name": "全匹配抢购", "keywords": ["TargetDoll"], "enabled": True, "search_mode": "new_stream", "match_mode": "any", "auto_order": True}
        ]
        monitor.config["auto_order"]["enabled"] = True

        # 准备 3 个已登录的备用 Worker，每个拥有独立 Session
        w1 = AutoOrderHook(username="u@e.com", password="p", dry_run=True)
        w2 = AutoOrderHook(username="u@e.com", password="p", dry_run=True)
        w3 = AutoOrderHook(username="u@e.com", password="p", dry_run=True)
        for w in (w1, w2, w3):
            w.is_logged_in = True
            w._is_standby_worker = True
        monitor.auto_order._standby_workers = [w1, w2, w3]

        used_session_ids = set()
        used_thread_names = set()
        completed_items = []
        lock = threading.Lock()

        def make_worker_match_fn(worker_inst):
            def _on_match(item, rule):
                with lock:
                    used_session_ids.add(id(worker_inst.session))
                    used_thread_names.add(threading.current_thread().name)
                time.sleep(0.15)  # 模拟网络加购与结算耗时
                with lock:
                    completed_items.append(item["id"])
                return {"success": True, "dry_run": True, "details": {"total_price": "50,000円"}}
            return _on_match

        for w in (w1, w2, w3):
            w.on_item_matched = make_worker_match_fn(w)

        items_3 = [
            {"id": "9001", "title": "TargetDoll Alpha", "price": 50000, "is_in_stock": True},
            {"id": "9002", "title": "TargetDoll Beta", "price": 52000, "is_in_stock": True},
            {"id": "9003", "title": "TargetDoll Gamma", "price": 54000, "is_in_stock": True},
        ]
        monitor.scraper.fetch_new_arrivals = MagicMock(return_value=items_3)
        monitor.notifier.dispatch = MagicMock()
        monitor.notifier.dispatch_order_success = MagicMock()

        t0 = time.time()
        res = monitor.check_once(is_burst=True)
        check_elapsed = time.time() - t0

        # 验证 check_once 非阻塞极速返回 (远小于 Worker 的 0.15s 耗时)
        self.assertEqual(res["new_items_count"], 3)
        self.assertLess(check_elapsed, 0.10, "check_once 应当异步非阻塞返回，不等待抢购线程结束")

        # 等待后台并发抢购完成
        time.sleep(0.25)
        self.assertEqual( sorted(completed_items), ["9001", "9002", "9003"])
        self.assertEqual(len(used_session_ids), 3, "3 件并发抢购商品必须使用 3 个完全独立的 requests.Session 会话以防串车")
        self.assertGreaterEqual(len(used_thread_names), 2, "应当在多个并发线程中并行执行")


# =====================================================================
# 6. 定时调度器、归档数据库与 Web API 集成测试 (Scheduler, Archive & Web)
# =====================================================================

class TestSchedulerArchiveAndWebAPI(IsolatedMonitorTestCase):

    def test_23_scheduler_drop_time_prewarm_and_burst_lifecycle(self):
        """测试定时调度器：倒计时计算、手动/定点开启爆发模式并自动预热备用 Worker 会话池"""
        monitor = DollyteriaMonitor()
        monitor.config["schedule"] = {
            "enabled": True,
            "drop_times": ["20:00:00", "21:00:00"],
            "burst_duration_sec": 60,
            "burst_interval_sec": 0.2,
            "interval_enabled": True,
            "interval_minutes": 5,
        }
        scheduler = DollyteriaScheduler(monitor)

        # 1. 验证倒计时计算
        next_label, remaining_sec = scheduler.get_next_drop_countdown()
        self.assertIn(next_label, ["20:00:00", "21:00:00"])
        self.assertGreaterEqual(remaining_sec, 0)

        # 2. 验证触发爆发模式时自动预热 Worker 会话池
        monitor.auto_order.preload_standby_workers = MagicMock()
        scheduler.trigger_burst(duration_sec=10)
        self.assertTrue(scheduler.is_bursting)
        monitor.auto_order.preload_standby_workers.assert_called_once()

        status = scheduler.get_status()
        self.assertTrue(status["is_bursting"])
        self.assertGreater(status["burst_remaining_sec"], 0)

    def test_24_archive_db_sync_and_stock_reconciliation(self):
        """测试归档数据库：/new 全量同步 (含在售与售罄) 及全站在售盘点差集自动翻转售罄状态"""
        monitor = DollyteriaMonitor()

        stream_items = [
            {"id": "7001", "title": "DearSD 在售 A", "model_number": "M-1", "price": 60000, "is_in_stock": True},
            {"id": "7002", "title": "DearSD 在售 B", "model_number": "M-2", "price": 62000, "is_in_stock": True},
            {"id": "7003", "title": "SDGIRL 售罄 C", "model_number": "M-3", "price": 55000, "is_in_stock": False},
        ]
        monitor.scraper.fetch_new_arrivals = MagicMock(return_value=stream_items)

        # 1. 同步 /new 至归档库
        sync_res = monitor.sync_new_arrivals_to_archive(max_pages=2)
        self.assertEqual(sync_res["status"], "ok")
        self.assertEqual(sync_res["inserted"], 3)

        stats = monitor.archive_db.get_stats()
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["in_stock"], 2)
        self.assertEqual(stats["sold_out"], 1)

        # 2. 模拟盘点：线上仅剩 7001 在售，7002 已被买走下架 -> reconcile_in_stock_ids 自动将 7002 标记为售罄
        flipped = monitor.archive_db.reconcile_in_stock_ids({"7001"})
        self.assertEqual(flipped, 1)
        p_7002 = monitor.archive_db.get_product_by_id("7002")
        self.assertFalse(p_7002["is_in_stock"])

    def test_25_web_api_quick_buy_and_sold_out_sse_broadcast(self):
        """
        测试 Web 控制台 REST API 与全站档案一键买断 (/api/auto_order/buy)：
        1. 一键买断成功时返回订单详情并推送成功通知
        2. 一键买断发现商品刚被他人抢走 (is_sold_out=True) 时，自动更新 archive.db 为已售罄并广播 item_sold_out 事件
        """
        monitor = DollyteriaMonitor()
        monitor.config["auto_order"]["username"] = "buyer@example.com"
        monitor.config["auto_order"]["password"] = "secret_pwd"
        monitor.config["quick_buy"]["enabled"] = True
        monitor.config["quick_buy"]["dry_run"] = False

        # 先在归档库写入一件在售商品 6001
        monitor.archive_db.upsert_batch([
            {"id": "6001", "title": "DearSD 档案在售商品", "price": 65000, "is_in_stock": True}
        ])

        scheduler = DollyteriaScheduler(monitor)
        app = create_app(monitor, scheduler)
        client = app.test_client()

        # 测试 /health 与 /api/status
        self.assertEqual(client.get("/health").status_code, 200)
        st_resp = client.get("/api/status")
        self.assertEqual(st_resp.status_code, 200)

        # 场景 A: 一键买断遭遇商品已售罄 -> 自动将 archive.db 中 6001 翻转为 is_in_stock=False
        mock_worker_soldout = MagicMock()
        mock_worker_soldout.execute_checkout.return_value = {
            "success": False,
            "step": "add_to_cart",
            "is_sold_out": True,
            "message": "商品已明确售罄",
        }
        monitor.auto_order.get_worker = MagicMock(return_value=mock_worker_soldout)
        monitor.auto_order.release_worker = MagicMock()

        buy_resp_1 = client.post("/api/auto_order/buy", json={"item_id": "6001", "item_title": "DearSD 档案在售商品"})
        self.assertEqual(buy_resp_1.status_code, 200)
        data_1 = buy_resp_1.get_json()
        self.assertFalse(data_1["success"])
        self.assertTrue(data_1["is_sold_out"])
        # 验证数据库中 6001 已被自动标记为售罄
        self.assertFalse(monitor.archive_db.get_product_by_id("6001")["is_in_stock"])

        # 场景 B: 一键买断成功 -> 触发 dispatch_order_success 且自动将已买下的孤品标记为售罄
        monitor.archive_db.upsert_batch([
            {"id": "6002", "title": "DearSD 档案在售商品 2", "price": 68000, "is_in_stock": True}
        ])
        mock_worker_ok = MagicMock()
        mock_worker_ok.execute_checkout.return_value = {
            "success": True,
            "dry_run": False,
            "step": "order_complete",
            "elapsed_sec": 0.38,
            "payment_method": "代金引換",
            "details": {"total_price": "68,000円", "payment_method": "代金引換"},
        }
        monitor.auto_order.get_worker = MagicMock(return_value=mock_worker_ok)
        monitor.notifier.dispatch_order_success = MagicMock()

        buy_resp_2 = client.post("/api/auto_order/buy", json={"item_id": "6002", "item_title": "DearSD 档案在售商品 2", "dry_run": False})
        self.assertEqual(buy_resp_2.status_code, 200)
        self.assertTrue(buy_resp_2.get_json()["success"])
        monitor.notifier.dispatch_order_success.assert_called_once()
        self.assertFalse(monitor.archive_db.get_product_by_id("6002")["is_in_stock"], "真实下单购买成功后应立即将该商品标记为已售罄")


# =====================================================================
# 7. 真实下单可靠性专项强化测试：reCAPTCHA v3 令牌自愈、确认页残影隔离、并发预取与实弹探针
# =====================================================================

class TestRealOrderReliabilityAndRecaptcha(IsolatedMonitorTestCase):

    def test_26_real_order_recaptcha_rejection_auto_resign_and_retry_recovery(self):
        """
        测试真实下单最后一步 (/cart/confirm) 遭遇 reCAPTCHA 令牌失效或拒签时的自动重签自愈：
        1. 初次提交时携带旧令牌 expired_token，被官网返回「reCAPTCHAの検証に失敗しました。再度お試しください。」拦截
        2. 验证 execute_checkout 自动捕获该特征，调用 get_token(force_fresh=True) 实时重签全新令牌 fresh_token_v2
        3. 自动携带 fresh_token_v2 瞬时重交 POST /cart/confirm 并成功完成下单 (/cart/complete)！
        """
        hook = AutoOrderHook(
            username="buyer@example.com",
            password="secret_password",
            dry_run=False,
            payment_method="store_pickup",
            retry_delay=0.01,
            max_retries=3,
        )
        hook.is_logged_in = True
        resps = build_checkout_responses("809101", "SDGIRL/アナイス リアル注文テスト", "70,000円", payment_label="店頭支払")

        submitted_tokens = []

        def mock_request(method, url, **kwargs):
            if method == "GET" and "/cart/customer" in url:
                return resps["cust_ready"]
            if method == "POST" and "shipping-list" in url:
                return resps["shipping"]
            if method == "POST" and "/cart/shipping" in url:
                return resps["confirm"]
            if method == "POST" and "/cart/confirm" in url:
                data = kwargs.get("data", {})
                tok = data.get("recaptcha_token", "")
                submitted_tokens.append(tok)
                if tok == "expired_token_v1":
                    # 第 1 次模拟令牌过期被 Dollyteria 拒绝
                    return resps["confirm_recaptcha_err"]
                elif tok == "fresh_token_v2" and data.get("policy_agree") == "1":
                    # 第 2 次重签新令牌后通过验证，成功跳转 /cart/complete
                    return resps["complete"]
                return resps["confirm_recaptcha_err"]
            return make_response(200)

        hook.session.request = mock_request
        hook.session.post = MagicMock(return_value=resps["cart"])

        token_calls = []

        def fake_get_token(sitekey=DEFAULT_RECAPTCHA_SITEKEY, action="submit", force_fresh=False, max_age_sec=45.0):
            token_calls.append(force_fresh)
            return "fresh_token_v2" if force_fresh else "expired_token_v1"

        with patch.object(RecaptchaTokenProvider.get_instance(), "get_token", side_effect=fake_get_token):
            res = hook.execute_checkout("809101", "SDGIRL/アナイス リアル注文テスト", dry_run=False, payment_method="store_pickup")

        self.assertTrue(res["success"], f"首次令牌失效后应自动重签新令牌并重试成功: {res}")
        self.assertEqual(res["step"], "order_complete")
        self.assertEqual(submitted_tokens, ["expired_token_v1", "fresh_token_v2"])
        self.assertIn(True, token_calls, "检测到 reCAPTCHA 拦截后必须以 force_fresh=True 强制重签新令牌")

    def test_27_real_order_confirm_soldout_diagnosis_and_stale_cart_isolation(self):
        """
        测试真实下单两大极端边界可靠性：
        - 场景 A：加购时购物车内残留有上一单失败留下的旧商品 (cart_remove_OLD888)，验证 add_to_cart 入车瞬间自动剔除旧商品，绝不串单或因旧品售罄卡死结算
        - 场景 B：商品顺利抵达 /cart/confirm，但在点击「購入する」瞬间被他人抢先一步完成结账，官网返回「在庫が不足しております」错误提示：
          验证系统精准提取日文报错原因、标记 is_sold_out=True，并自动调用 clear_cart() 清理失效购物车
        """
        hook = AutoOrderHook(
            username="buyer@example.com",
            password="secret_password",
            dry_run=False,
            payment_method="store_pickup",
            retry_delay=0.01,
            max_retries=3,
        )
        hook.is_logged_in = True

        # 购物车中同时存在残留旧商品 OLD888 与本次新加购商品 809102
        cart_with_stale_item_html = """
        <html><body>
            <h1>ショッピングカート</h1>
            <div>カートの中身: OLD888 809102</div>
            <input type="submit" name="cart_remove_OLD888" value="削除" />
            <input type="submit" name="cart_remove_809102" value="削除" />
            <a href="/cart/customer">レジに進む</a>
        </body></html>
        """
        resp_cart_mixed = make_response(200, "https://www.dollyteria.com/cart", cart_with_stale_item_html)

        post_records = []

        def mock_post(url, data=None, **kwargs):
            post_records.append((url, data))
            return resp_cart_mixed

        hook.session.post = mock_post
        ok, _, _ = hook.add_to_cart("809102")
        self.assertTrue(ok)
        # 验证自动发送了针对残留商品 OLD888 的删除请求，且未误删当前商品 809102
        removed_names = [d for u, d in post_records if u.endswith("/cart") and d and any(k.startswith("cart_remove_") for k in d)]
        self.assertEqual(len(removed_names), 1)
        self.assertIn("cart_remove_OLD888", removed_names[0])
        self.assertNotIn("cart_remove_809102", removed_names[0])

        # 场景 B: 在 /cart/confirm 最终提交瞬间遭遇他人抢先结账导致库存不足
        resps = build_checkout_responses("809102", "DearSD/めぐ ラスト1点", "68,000円", payment_label="店頭支払")
        resp_confirm_soldout = make_response(
            200,
            "https://www.dollyteria.com/cart/confirm",
            """
            <html><body>
                <div class="error_box">
                    <p class="error_status">大変申し訳ございません。商品の在庫が不足しております。</p>
                </div>
            </body></html>
            """
        )

        def mock_request(method, url, **kwargs):
            if method == "GET" and "/cart/customer" in url:
                return resps["cust_ready"]
            if method == "POST" and "shipping-list" in url:
                return resps["shipping"]
            if method == "POST" and "/cart/shipping" in url:
                return resps["confirm"]
            if method == "POST" and "/cart/confirm" in url:
                return resp_confirm_soldout
            return make_response(200)

        hook.session.request = mock_request
        hook.clear_cart = MagicMock()

        with patch.object(RecaptchaTokenProvider.get_instance(), "get_token", return_value="valid_token_xyz"):
            res = hook.execute_checkout("809102", "DearSD/めぐ ラスト1点", dry_run=False, payment_method="store_pickup")

        self.assertFalse(res["success"])
        self.assertTrue(res["is_sold_out"], "确认页提示库存不足时应准确标记 is_sold_out=True")
        self.assertIn("在庫が不足しております", res["message"], "返回消息应透传确认页的具体错误原因")
        hook.clear_cart.assert_called_once()

    def test_28_real_order_parallel_recaptcha_prefetch_zero_extra_latency(self):
        """
        测试真实下单时的并发预取架构 (Parallel Token Prefetch)：
        - 模拟加购 + 推进结算耗时 0.15s，同时模拟签发 reCAPTCHA 令牌耗时 0.15s
        - 验证两者并行执行，总耗时显著低于串行相加 (0.30s)，实现确认页 0 额外等待直接提交
        """
        hook = AutoOrderHook(
            username="buyer@example.com",
            password="secret_password",
            dry_run=False,
            payment_method="store_pickup",
            retry_delay=0.01,
            max_retries=2,
        )
        hook.is_logged_in = True
        resps = build_checkout_responses("809103", "SDGIRL 速度テスト", "60,000円", payment_label="店頭支払")

        def slow_add_to_cart( url, data=None, **kwargs):
            time.sleep(0.12)
            return resps["cart"]

        def slow_get_token(*args, **kwargs):
            time.sleep(0.12)
            return "parallel_prefetched_token_999"

        def mock_request(method, url, **kwargs):
            if method == "GET" and "/cart/customer" in url:
                return resps["cust_ready"]
            if method == "POST" and "shipping-list" in url:
                return resps["shipping"]
            if method == "POST" and "/cart/shipping" in url:
                return resps["confirm"]
            if method == "POST" and "/cart/confirm" in url:
                self.assertEqual(kwargs.get("data", {}).get("recaptcha_token"), "parallel_prefetched_token_999")
                return resps["complete"]
            return make_response(200)

        hook.session.post = slow_add_to_cart
        hook.session.request = mock_request

        with patch.object(RecaptchaTokenProvider.get_instance(), "get_token", side_effect=slow_get_token):
            t0 = time.time()
            res = hook.execute_checkout("809103", "SDGIRL 速度テスト", dry_run=False, payment_method="store_pickup")
            elapsed = time.time() - t0

        self.assertTrue(res["success"])
        # 并行执行耗时应接近单边 0.12s，远小于串行的 0.24s+
        self.assertLess(elapsed, 0.22, f"reCAPTCHA 令牌预取应当与加购结算并行重叠执行，实际耗时 {elapsed:.3f}s")

    def test_29_live_dollyteria_confirm_page_recaptcha_v3_verification(self):
        """
        【实盘联调级安全验证】连接真实 Dollyteria 官网 (/cart/confirm) 检验 RecaptchaTokenProvider 签发的令牌：
        - 使用真实浏览器内核签发 Google reCAPTCHA v3 令牌 (验证冷启动签发 + 预热缓存 0.00s 秒取)
        - 若真实 config.json 配置了有效官网账号，则自动加购 1 件在库商品推进至真实 /cart/confirm 页面
        - 安全探针机制：向真实 /cart/confirm 提交携带 recaptcha_token 但【故意不传 policy_agree】的请求
          (100% 保证官网因未勾选同意规约而绝不生成订单，同时触发官网服务端对 recaptcha_token 的真实验证！)
        - 断言：真实官网返回页面中绝不包含「reCAPTCHAの検証に失敗しました」，证明令牌 100% 通过官网风控！
        """
        provider = RecaptchaTokenProvider.get_instance()
        browser_exe = provider._find_browser_executable()
        if not browser_exe:
            self.skipTest("当前环境未安装 Chrome/Edge/Chromium，跳过真实浏览器令牌联调")

        try:
            # 1. 验证多标签页预热池大小同步 (如设置 standby_workers=2 则创建 2 个独立预热 Tab) 及多线程并发独立签发
            provider.set_pool_size(2)
            ok_pool = provider._ensure_browser_and_pool_ready()
            self.assertTrue(ok_pool, "多标签预热池应当成功就绪")
            self.assertEqual(len(provider._slots), 2, "预热 Tab 数量应与配置的备用会话数 (2) 严格一致")

            # 验证 2 个线程同时调用 get_token 时在各自的预热 Tab 上并行极速出码，且令牌互不相同
            concurrent_tokens = [None, None]
            t_start = time.time()

            def _fetch_tok(idx):
                concurrent_tokens[idx] = provider.get_token()

            th1 = threading.Thread(target=_fetch_tok, args=(0,))
            th2 = threading.Thread(target=_fetch_tok, args=(1,))
            th1.start()
            th2.start()
            th1.join(timeout=8)
            th2.join(timeout=8)
            parallel_elapsed = time.time() - t_start

            tok1, tok2 = concurrent_tokens[0], concurrent_tokens[1]
            self.assertTrue(isinstance(tok1, str) and len(tok1) > 1000, "线程 1 应成功签发真实 reCAPTCHA v3 令牌")
            self.assertTrue(isinstance(tok2, str) and len(tok2) > 1000, "线程 2 应成功签发真实 reCAPTCHA v3 令牌")
            self.assertNotEqual(tok1, tok2, "多线程并发下单绝不能共用同一枚 reCAPTCHA 令牌")
            self.assertLess(parallel_elapsed, 1.0, f"预热 Tab 并发实时签发应当在 1.0s 内极速完成，实际耗时 {parallel_elapsed:.3f}s")

            # 2. 读取真实 config.json 账号向官网 /cart/confirm 发起无损探针验证
            real_cfg_path = os.path.join(PROJECT_ROOT, "config.json")
            if not os.path.exists(real_cfg_path):
                return
            with open(real_cfg_path, "r", encoding="utf-8") as f:
                real_cfg = json.load(f)
            user = (real_cfg.get("auto_order", {}).get("username") or "").strip()
            pwd = (real_cfg.get("auto_order", {}).get("password") or "").strip()
            if not user or not pwd:
                return

            scraper = DollyteriaScraper()
            in_stock_items = scraper.fetch_new_arrivals(num=10, in_stock_only=True)
            if not in_stock_items:
                return
            test_pid = in_stock_items[0]["id"]

            live_hook = AutoOrderHook(username=user, password=pwd, dry_run=True, payment_method="store_pickup")
            ok_login, _ = live_hook.login()
            if not ok_login:
                return
            try:
                ok_cart, _, _ = live_hook.add_to_cart(test_pid)
                self.assertTrue(ok_cart, "实盘加购测试商品应成功")
                ok_conf, _, r_conf = live_hook.proceed_to_confirmation(payment_method="store_pickup")
                self.assertTrue(ok_conf and r_conf is not None, "实盘推进至 /cart/confirm 应成功")

                # 故意不传 policy_agree（确保官网绝不生成真实订单），同时传入真实签发的 tok2 检验官网 reCAPTCHA 校验结果
                r_probe = live_hook.session.post(
                    "https://www.dollyteria.com/cart/confirm",
                    data={
                        "recaptcha_token": tok2,
                        "coupon_use": "f",
                        "couponCode": "",
                        "go-next": "購入する",
                    },
                    timeout=12,
                    allow_redirects=True,
                )
                probe_text = r_probe.content.decode("utf-8", "ignore")
                self.assertNotIn(
                    "reCAPTCHAの検証に失敗しました",
                    probe_text,
                    "真实官网 /cart/confirm 应当 100% 认可 RecaptchaTokenProvider 签发的令牌，不得出现 reCAPTCHA 验证失败！",
                )
                self.assertIn(
                    "「同意する」にチェックをいれてください",
                    probe_text,
                    "安全探针未勾选同意规约，官网应当仅提示勾选同意而绝不产生真实扣款或订单",
                )
            finally:
                live_hook.clear_cart()
        finally:
            provider._cleanup_proc_unlocked()

    def test_30_checkout_faster_than_tab_token_signing_no_race_condition(self):
        """
        测试边界场景 A：【加购 + 选支付方式】快于【Tab 同步调用与令牌签发】
        - 模拟加购与推进结算极快完成 (仅耗时 0.01s 即可到达 /cart/confirm)
        - 模拟后台 Tab 签发 reCAPTCHA 令牌较慢 (耗时 0.25s 才返回)
        - 验证：
          1. execute_checkout 到达 /cart/confirm 时会通过 token_thread.join() 安全等待令牌签发完成
          2. 绝不会因加购更快而漏传令牌、报错或重复发起多余签发请求
          3. 最终提交结束时自动调用 release_tokens 归还 Tab 槽位
        """
        hook = AutoOrderHook(
            username="buyer@example.com",
            password="secret_password",
            dry_run=False,
            payment_method="store_pickup",
            retry_delay=0.01,
            max_retries=2,
        )
        hook.is_logged_in = True
        resps = build_checkout_responses("809201", "SD13 极速加购快于令牌测试", "45,000円", payment_label="店頭支払")

        timeline = []
        get_token_calls = []
        released_tokens = []

        def fast_add_to_cart(url, data=None, **kwargs):
            timeline.append(("add_to_cart_done", time.time()))
            return resps["cart"]

        def slower_tab_get_token(*args, **kwargs):
            get_token_calls.append(kwargs.get("force_fresh", False))
            time.sleep(0.25)
            timeline.append(("token_signed", time.time()))
            return "delayed_tab_token_after_fast_checkout_777"

        def track_release_tokens(tokens):
            released_tokens.extend(tokens)
            timeline.append(("token_released", time.time()))

        def mock_request(method, url, **kwargs):
            if method == "GET" and "/cart/customer" in url:
                return resps["cust_ready"]
            if method == "POST" and "shipping-list" in url:
                return resps["shipping"]
            if method == "POST" and "/cart/shipping" in url:
                timeline.append(("confirm_page_reached", time.time()))
                return resps["confirm"]
            if method == "POST" and "/cart/confirm" in url:
                timeline.append(("confirm_post_submitted", time.time()))
                self.assertEqual(
                    kwargs.get("data", {}).get("recaptcha_token"),
                    "delayed_tab_token_after_fast_checkout_777",
                    "即使加购与选支付方式比 Tab 签发更快，/cart/confirm 提交前也必须同步等待并注入完整令牌",
                )
                return resps["complete"]
            return make_response(200)

        hook.session.post = fast_add_to_cart
        hook.session.request = mock_request

        provider = RecaptchaTokenProvider.get_instance()
        with patch.object(provider, "get_token", side_effect=slower_tab_get_token), \
             patch.object(provider, "release_tokens", side_effect=track_release_tokens):
            res = hook.execute_checkout("809201", "SD13 极速加购快于令牌测试", dry_run=False, payment_method="store_pickup")

        self.assertTrue(res["success"], "加购快于令牌签发时应当平稳等待并下单成功")
        self.assertEqual(len(get_token_calls), 1, "应当仅使用并行预取线程签发的 1 枚令牌，不触发多余重签")
        event_names = [e[0] for e in timeline]
        # 验证真实执行时序：到达确认页 -> 等待令牌签发完毕 -> 提交最终订单 -> 释放 Tab 槽位
        self.assertLess(
            event_names.index("confirm_page_reached"),
            event_names.index("token_signed"),
            "测试应当准确复现『到达确认页快于令牌签发完成』的时序",
        )
        self.assertLess(
            event_names.index("token_signed"),
            event_names.index("confirm_post_submitted"),
            "最终 POST /cart/confirm 必须在令牌签发完成后才发起",
        )
        self.assertIn("delayed_tab_token_after_fast_checkout_777", released_tokens, "下单完成后必须立即释放持有的令牌与 Tab")

    def test_31_checkout_slower_than_tab_signing_holds_tab_and_refreshes_if_expired(self):
        """
        测试边界场景 B：【加购 + 选支付方式】慢于【Tab 令牌签发】(例如遭遇 503 重试)
        - 子场景 B1：Tab 在 0.02s 内极速签发完毕，而加购 + 选支付方式因 503 重试耗时 0.20s
          验证：在 POST /cart/confirm 完成前，预热 Tab 绝不会被提前释放或刷新；直到订单提交完成后才触发 release_tokens。
        - 子场景 B2：极端 503 阻塞导致加购 + 选支付方式耗时超过 12s 阈值
          验证：execute_checkout 检测到预取令牌已老化 (>12s)，在提交 /cart/confirm 前自动释放旧令牌并重签全新令牌。
        """
        hook = AutoOrderHook(
            username="buyer@example.com",
            password="secret_password",
            dry_run=False,
            payment_method="store_pickup",
            retry_delay=0.05,
            max_retries=5,
        )
        hook.is_logged_in = True
        resps = build_checkout_responses("809202", "SDGr 503重试慢于令牌测试", "88,000円", payment_label="店頭支払")

        # --- 子场景 B1：令牌 0.01s 签好，加购经历 2 次 503 后才成功 ---
        cart_attempts = {"count": 0}
        released_at_confirm = {"released_before_post": False}
        released_list = []

        def retry_503_add_to_cart(url, data=None, **kwargs):
            cart_attempts["count"] += 1
            if cart_attempts["count"] <= 2:
                time.sleep(0.06)
                return make_response(503, html="503 Service Temporarily Unavailable")
            return resps["cart"]

        def fast_tab_get_token(*args, **kwargs):
            return "fast_signed_token_held_during_503"

        def track_release(tokens):
            released_list.extend(tokens)

        def mock_request_b1(method, url, **kwargs):
            if method == "GET" and "/cart/customer" in url:
                return resps["cust_ready"]
            if method == "POST" and "shipping-list" in url:
                return resps["shipping"]
            if method == "POST" and "/cart/shipping" in url:
                return resps["confirm"]
            if method == "POST" and "/cart/confirm" in url:
                # 在提交 /cart/confirm 的瞬间，令牌绝不能已经被提前释放！
                if released_list:
                    released_at_confirm["released_before_post"] = True
                self.assertEqual(kwargs.get("data", {}).get("recaptcha_token"), "fast_signed_token_held_during_503")
                return resps["complete"]
            return make_response(200)

        hook.session.post = retry_503_add_to_cart
        hook.session.request = mock_request_b1

        provider = RecaptchaTokenProvider.get_instance()
        with patch.object(provider, "get_token", side_effect=fast_tab_get_token), \
             patch.object(provider, "release_tokens", side_effect=track_release):
            res_b1 = hook.execute_checkout("809202", "SDGr 503重试慢于令牌测试", dry_run=False, payment_method="store_pickup")

        self.assertTrue(res_b1["success"])
        self.assertFalse(
            released_at_confirm["released_before_post"],
            "在加购/结算重试期间及 POST /cart/confirm 完成前，绝不能提前释放/刷新已签发令牌的 Tab",
        )
        self.assertEqual(released_list, ["fast_signed_token_held_during_503"], "POST /cart/confirm 完成后应立即释放 Tab 槽位")

        # --- 子场景 B2：模拟加购重试极久导致预取令牌老化 (>12s)，验证自动换签新令牌 ---
        token_seq = iter(["stale_token_over_12s", "fresh_token_after_long_503"])
        released_b2 = []
        real_time = time.time
        fake_clock = {"offset": 0.0}

        def mocked_time():
            return real_time() + fake_clock["offset"]

        def add_to_cart_long_delay(url, data=None, **kwargs):
            # 模拟加购耗时 13.5 秒（令并行预取的 첫枚令牌超过 12s 保鲜阈值）
            time.sleep(0.03)
            fake_clock["offset"] += 13.5
            return resps["cart"]

        def mock_request_b2(method, url, **kwargs):
            if method == "GET" and "/cart/customer" in url:
                return resps["cust_ready"]
            if method == "POST" and "shipping-list" in url:
                return resps["shipping"]
            if method == "POST" and "/cart/shipping" in url:
                return resps["confirm"]
            if method == "POST" and "/cart/confirm" in url:
                self.assertEqual(
                    kwargs.get("data", {}).get("recaptcha_token"),
                    "fresh_token_after_long_503",
                    "当加购重试耗时过长 (>12s) 时，应当自动弃用老化令牌并换签全新令牌提交",
                )
                return resps["complete"]
            return make_response(200)

        hook.session.post = add_to_cart_long_delay
        hook.session.request = mock_request_b2

        with patch("hooks.auto_order.time.time", side_effect=mocked_time), \
             patch.object(provider, "get_token", side_effect=lambda *a, **kw: next(token_seq)), \
             patch.object(provider, "release_tokens", side_effect=lambda toks: released_b2.extend(toks)):
            res_b2 = hook.execute_checkout("809202", "SDGr 503超长重试自动换签测试", dry_run=False, payment_method="store_pickup")

        self.assertTrue(res_b2["success"])
        self.assertIn("stale_token_over_12s", released_b2, "老化的预取令牌应当被立即释放")
        self.assertIn("fresh_token_after_long_503", released_b2, "新签发的令牌在下单结束后也应当被释放")

    def test_32_live_multi_tab_dynamic_scaling_and_parallel_execute_checkout(self):
        """
        【真实 Chrome 多标签池 + 并行下单全链路压测】
        1. 验证前端修改 Standby Sessions (例如从 2 扩容到 3) 时：
           - 已就绪的 2 个 Tab 保持原地不动 (0 闪断)
           - 立即在后台同一个 Chrome 进程中新增第 3 个预热 Tab
        2. 启动 3 个并发下单线程同时调用真实 hook.execute_checkout(dry_run=False)：
           - 线程 0：模拟加购极快 (0.01s)，快于真实 Chrome Tab 签发令牌 (~0.25s)
           - 线程 1：模拟加购适中 (0.15s)，与真实 Chrome Tab 签发令牌几乎同时完成
           - 线程 2：模拟加购较慢 (0.35s)，慢于真实 Chrome Tab 签发令牌
        3. 断言：
           - 3 个并发下单线程全部 100% 成功，各自拿到真实 Chrome 签发的独立 reCAPTCHA v3 令牌 (长度 > 1000 且互不相同)
           - 在各自 POST /cart/confirm 执行期间，对应的 Chrome Tab 保持锁定 (`in_use=True`) 未被提前刷新
           - 3 个下单全部返回后，所有 Tab 均被释放并触发后台原地刷新回填
        """
        provider = RecaptchaTokenProvider.get_instance()
        browser_exe = provider._find_browser_executable()
        if not browser_exe:
            self.skipTest("当前环境未安装 Chrome/Edge/Chromium，跳过真实浏览器多标签并行下单测试")

        try:
            # 第一步：初始化 2 个预热 Tab
            provider.set_pool_size(2)
            self.assertTrue(provider._ensure_browser_and_pool_ready())
            with provider._lock:
                initial_ws_urls = [s.ws_url for s in provider._slots]
            self.assertEqual(len(initial_ws_urls), 2)

            # 第二步：模拟用户在前端将 Standby Sessions 从 2 修改为 3，验证增量扩容不打断原有 2 个 Tab
            provider.set_pool_size(3)
            # 等待后台增量扩容线程完成第 3 个 Tab 的创建与预热
            deadline = time.time() + 15.0
            while time.time() < deadline:
                with provider._lock:
                    ready_slots = [s for s in provider._slots if s.is_fresh]
                if len(ready_slots) >= 3:
                    break
                time.sleep(0.1)

            with provider._lock:
                expanded_ws_urls = [s.ws_url for s in provider._slots if s.is_fresh]
            self.assertEqual(len(expanded_ws_urls), 3, "从 2 扩容到 3 后应当有 3 个就绪的预热 Tab")
            for old_ws in initial_ws_urls:
                self.assertIn(old_ws, expanded_ws_urls, "扩容时原有的 2 个预热 Tab 必须保持原样不重建")

            # 第三步：3 个线程以不同加购速度并发执行完整 execute_checkout，使用真实 Chrome 多 Tab 池签发令牌
            delays = [0.01, 0.15, 0.35]  # 分别代表：快于签发、同步于签发、慢于签发
            results = [None, None, None]
            submitted_tokens = [None, None, None]
            slot_locked_during_confirm = [False, False, False]

            def _run_concurrent_checkout(idx: int):
                pid = f"80930{idx}"
                hook = AutoOrderHook(
                    username=f"buyer{idx}@example.com",
                    password="secret_password",
                    dry_run=False,
                    payment_method="store_pickup",
                    retry_delay=0.01,
                    max_retries=2,
                )
                hook.is_logged_in = True
                resps = build_checkout_responses(pid, f"并发商品 #{idx}", "50,000円", payment_label="店頭支払")

                def simulated_add_to_cart(url, data=None, **kwargs):
                    time.sleep(delays[idx])
                    return resps["cart"]

                def simulated_request(method, url, **kwargs):
                    if method == "GET" and "/cart/customer" in url:
                        return resps["cust_ready"]
                    if method == "POST" and "shipping-list" in url:
                        return resps["shipping"]
                    if method == "POST" and "/cart/shipping" in url:
                        return resps["confirm"]
                    if method == "POST" and "/cart/confirm" in url:
                        tok = kwargs.get("data", {}).get("recaptcha_token", "")
                        submitted_tokens[idx] = tok
                        # 检查此时该令牌绑定的 Tab 是否正处于锁定保护状态 (in_use=True)
                        with provider._lock:
                            bound_entry = provider._active_token_slots.get(tok)
                            if bound_entry is not None and bound_entry[0].in_use:
                                slot_locked_during_confirm[idx] = True
                        return resps["complete"]
                    return make_response(200)

                hook.session.post = simulated_add_to_cart
                hook.session.request = simulated_request
                results[idx] = hook.execute_checkout(pid, f"并发商品 #{idx}", dry_run=False, payment_method="store_pickup")

            threads = [threading.Thread(target=_run_concurrent_checkout, args=(i,)) for i in range(3)]
            for th in threads:
                th.start()
            for th in threads:
                th.join(timeout=15.0)

            for i in range(3):
                self.assertIsNotNone(results[i], f"通道 {i} 下单应返回结果")
                self.assertTrue(results[i]["success"], f"通道 {i} (加购延迟 {delays[i]}s) 应当下单成功")
                self.assertTrue(
                    isinstance(submitted_tokens[i], str) and len(submitted_tokens[i]) > 1000,
                    f"通道 {i} 必须注入真实 Chrome 签发的有效 reCAPTCHA 令牌",
                )
                self.assertTrue(
                    slot_locked_during_confirm[i],
                    f"通道 {i} 在 POST /cart/confirm 提交瞬间，其对应的 Chrome Tab 必须处于锁定保护状态 (in_use=True)，防止被后台刷新冲掉",
                )

            # 3 个并发通道拿到的真实令牌必须两两互不相同
            self.assertEqual(len(set(submitted_tokens)), 3, "3 个并发下单通道必须各自使用独立 Tab 签发互不相同的令牌")

            # 下单全部完成后，_active_token_slots 应当已被清空（所有 Tab 均已释放并进入刷新回填）
            with provider._lock:
                self.assertEqual(len(provider._active_token_slots), 0, "所有并发下单结束后，持有的令牌与 Tab 应当全部释放归还")
        finally:
            provider._cleanup_proc_unlocked()


if __name__ == "__main__":
    print("=" * 80)
    print("🚀 启动 Dollyteria 自动询盘、上新监控与自动下单全套功能测试套件 (32 项全场景含实盘 reCAPTCHA 与多 Tab 并发时序联调)")
    print("=" * 80)
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    print("=" * 80)
    if result.wasSuccessful():
        print(f"🎉 全部 {result.testsRun} 项全链路测试 100% 通过 (PASSED)！软件在常规、高峰繁忙期与真实下单确认环节均表现正常！")
    else:
        print(f"❌ 测试存在未通过项：失败 {len(result.failures)} 项，错误 {len(result.errors)} 项")
    print("=" * 80)
    sys.exit(0 if result.wasSuccessful() else 1)
