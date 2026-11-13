"""
抢购低延迟与风控池自愈专项单元测试:
1. process_stream_items 锁外通知/归档/落盘，验证 _stream_lock 不被慢通知阻塞
2. 结算 1-step 极速直连通道 (跳过 GET /cart/customer) 与失效自动回退机制
3. 爆发期探针 _yield_if_checkout_active 短避让 + 轻降频策略 (告别 25s 长阻塞)
4. RecaptchaTokenProvider.ensure_prewarm_healthy 掉线修复与老旧槽位主动刷新
5. /new 上新流固定 20 页最大深度 (忽略旧 config.json 中遗留的 max_stream_pages=5)
"""
import os
import sys
import time
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import core.monitor as monitor_mod
from core.monitor import DollyteriaMonitor
from hooks.auto_order import AutoOrderHook, RecaptchaTokenProvider, _RecaptchaTabSlot


def _make_resp(status_code=200, url="https://www.dollyteria.com/", text=""):
    r = MagicMock()
    r.status_code = status_code
    r.url = url
    r.text = text
    r.content = text.encode("utf-8")
    return r


class TestLatencyAndPoolOptimizations(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="dollyteria_opt_test_")
        self.config_path = os.path.join(self.temp_dir, "config.json")
        self.state_path = os.path.join(self.temp_dir, "state.json")
        self.db_path = os.path.join(self.temp_dir, "archive.db")
        with open(self.config_path, "w", encoding="utf-8") as f:
            f.write('{"watches": [], "schedule": {"enabled": true}, "auto_order": {"enabled": false}}')
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

    def test_01_stream_lock_not_blocked_by_slow_notifier(self):
        """验证 process_stream_items 释放 _stream_lock 后才执行慢通知与落盘"""
        monitor = DollyteriaMonitor()
        monitor.config["watches"] = [
            {"id": "w1", "name": "TestRule", "keywords": ["TargetDoll"], "enabled": True, "auto_order": False}
        ]
        monitor.state["seen_ids"] = []

        dispatch_entered = threading.Event()
        release_dispatch = threading.Event()

        def slow_dispatch(items, rule):
            dispatch_entered.set()
            release_dispatch.wait(timeout=2.0)

        monitor.notifier.dispatch = slow_dispatch

        t1_done = threading.Event()

        def run_t1():
            monitor.process_stream_items(
                [{"id": "hit_1", "title": "TargetDoll Alpha", "price": 50000, "is_in_stock": True}],
                source_tag="探针B",
                is_burst=True,
            )
            t1_done.set()

        t1 = threading.Thread(target=run_t1, daemon=True)
        t1.start()
        self.assertTrue(dispatch_entered.wait(timeout=1.0))

        t2_start = time.time()
        matches_t2 = monitor.process_stream_items(
            [{"id": "other_2", "title": "Ordinary Item", "price": 1000, "is_in_stock": True}],
            source_tag="探针A",
            is_burst=True,
        )
        t2_elapsed = time.time() - t2_start

        release_dispatch.set()
        t1.join(timeout=2.0)

        self.assertEqual(matches_t2, [])
        self.assertLess(t2_elapsed, 0.25, f"_stream_lock 绝不应被慢通知阻塞 (实际耗时 {t2_elapsed:.3f}s)")
        self.assertIn("hit_1", monitor.state["seen_ids"])
        self.assertIn("other_2", monitor.state["seen_ids"])

    def test_02_checkout_shipping_fastpath_and_fallback(self):
        """验证 proceed_to_confirmation 1-step 直连 /cart/shipping 与失效回退逻辑"""
        shipping_html = """
        <html><body>
            <form action="https://www.dollyteria.com/cart/shipping" method="post">
                <select name="delivery[1][shipping_list]"><option value="99999999" selected>自宅</option></select>
                <select name="delivery[1][shipping_method]"><option value="1" selected>佐川急便</option></select>
                <input type="radio" name="pay_procedure" id="pay8" value="8" /><label for="pay8">店頭支払</label>
                <input type="radio" name="pay_procedure" id="pay3" value="3" /><label for="pay3">代金引換</label>
            </form>
        </body></html>
        """
        confirm_html = "<html><body><h1>ご購入内容の最終確認</h1><input type='checkbox' name='policy_agree' /></body></html>"
        login_redirect = _make_resp(200, "https://www.dollyteria.com/member-login", "<input name='password' />")

        pool_hook = AutoOrderHook(username="u@example.com", password="pwd", dry_run=False, payment_method="store_pickup")
        pool_hook.is_logged_in = True

        worker = AutoOrderHook(username="u@example.com", password="pwd", dry_run=False, payment_method="store_pickup")
        worker.is_logged_in = True
        worker._is_standby_worker = True
        pool_hook._standby_workers = [worker]

        reqs_1 = []

        def mock_req_1(method, url, **kwargs):
            reqs_1.append((method, url))
            if method == "GET" and "/cart/customer" in url:
                return _make_resp(200, "https://www.dollyteria.com/cart/shipping", shipping_html)
            if method == "POST" and "/cart/shipping" in url:
                return _make_resp(200, "https://www.dollyteria.com/cart/confirm", confirm_html)
            return _make_resp(200)

        pool_hook.session.request = mock_req_1
        ok1, _, _ = pool_hook.proceed_to_confirmation("store_pickup")
        self.assertTrue(ok1)
        self.assertEqual([m for m, _ in reqs_1], ["GET", "POST"])
        self.assertIsNotNone(pool_hook._cached_shipping_params)

        # Standby worker 借出时自动继承已缓存表单参数，2nd 次直接 1-step POST 直达
        borrowed = pool_hook.get_worker()
        self.assertIs(borrowed, worker)
        reqs_2 = []

        def mock_req_2(method, url, **kwargs):
            reqs_2.append((method, url))
            if method == "POST" and "/cart/shipping" in url:
                return _make_resp(200, "https://www.dollyteria.com/cart/confirm", confirm_html)
            return _make_resp(200)

        borrowed.session.request = mock_req_2
        ok2, _, _ = borrowed.proceed_to_confirmation("store_pickup")
        self.assertTrue(ok2)
        self.assertEqual(
            reqs_2,
            [("POST", "https://www.dollyteria.com/cart/shipping")],
            "二次下单应跳过 GET /cart/customer，仅用 1 次 POST 直达 /cart/confirm",
        )

        # 若 fast-path 未直达确认页 (例如跳转登录页)，应自动清除缓存并回退标准流程
        reqs_3 = []
        fast_post_failed_once = {"done": False}

        def mock_req_3(method, url, **kwargs):
            reqs_3.append((method, url))
            if method == "POST" and "/cart/shipping" in url and not fast_post_failed_once["done"]:
                fast_post_failed_once["done"] = True
                return login_redirect
            if method == "GET" and "/cart/customer" in url:
                return _make_resp(200, "https://www.dollyteria.com/cart/shipping", shipping_html)
            if method == "POST" and "/cart/shipping" in url:
                return _make_resp(200, "https://www.dollyteria.com/cart/confirm", confirm_html)
            return _make_resp(200)

        borrowed.session.request = mock_req_3
        ok3, _, _ = borrowed.proceed_to_confirmation("store_pickup")
        self.assertTrue(ok3)
        self.assertEqual(
            [m for m, _ in reqs_3],
            ["POST", "GET", "POST"],
            "直连失败后应清缓存并回退标准 GET /cart/customer -> POST /cart/shipping 流程",
        )

    def test_03_yield_if_checkout_active_uses_short_yield_and_throttle(self):
        """验证冲刺期短避让后转为轻降频护航，不会阻塞 25s"""
        monitor = DollyteriaMonitor()
        engine = monitor._burst_engine
        engine.burst_end_time = time.time() + 30.0
        engine._stop_event.clear()

        AutoOrderHook._active_checkouts = 1
        AutoOrderHook._checkout_started_at = time.time() - 3.0  # 已超出 2.0s 初始避让窗口
        try:
            t0 = time.time()
            stopped = engine._yield_if_checkout_active("探针B", initial_yield_sec=2.0, throttle_sleep_sec=0.12)
            elapsed = time.time() - t0
            self.assertFalse(stopped)
            self.assertLess(elapsed, 0.4, f"超出初始避让窗口后应仅轻降频休眠，耗时 {elapsed:.3f}s")
        finally:
            AutoOrderHook._active_checkouts = 0
            AutoOrderHook._checkout_started_at = 0.0

    def test_04_recaptcha_ensure_prewarm_healthy_repairs_broken_and_aged_slots(self):
        """验证 ensure_prewarm_healthy 会同时修复掉线槽位、刷新老旧槽位并保活健康槽位"""
        provider = RecaptchaTokenProvider()
        now = time.time()
        s_broken = _RecaptchaTabSlot("broken111111", "ws://broken")
        s_broken.is_fresh = False

        s_aged = _RecaptchaTabSlot("aged22222222", "ws://aged")
        s_aged.is_fresh = True
        s_aged.prepared_at = now - 250.0

        s_healthy = _RecaptchaTabSlot("healthy33333", "ws://healthy")
        s_healthy.is_fresh = True
        s_healthy.prepared_at = now - 10.0
        s_healthy.last_touch_at = now - 30.0

        provider._slots = [s_broken, s_aged, s_healthy]
        provider._ensure_browser_and_pool_ready = MagicMock(return_value=True)
        prepared_ids = []
        touched_ids = []

        def fake_prepare(slot, sitekey, force_navigate=False):
            prepared_ids.append((slot.target_id, force_navigate))
            slot.is_fresh = True
            slot.prepared_at = time.time()
            return True

        def fake_touch(slot):
            touched_ids.append(slot.target_id)
            slot.last_touch_at = time.time()

        provider._prepare_slot = fake_prepare
        provider._touch_slot_keepalive = fake_touch

        provider.ensure_prewarm_healthy(max_age_sec=180.0, background=False)

        self.assertEqual(prepared_ids, [("broken111111", True), ("aged22222222", True)])
        self.assertEqual(touched_ids, ["healthy33333"])

    def test_05_fixed_20_page_stream_depth_ignores_stale_config_5(self):
        """验证 check_once 固定 20 页最大顺延深度，不再受旧 config.json 中 max_stream_pages=5 限制"""
        monitor = DollyteriaMonitor()
        monitor.config["watches"] = [
            {"id": "w1", "name": "Rule1", "keywords": ["TargetSpecial"], "enabled": True, "search_mode": "new_stream"}
        ]
        monitor.config["schedule"]["max_stream_pages"] = 5
        monitor.state["seen_ids"] = ["old_end"]

        fetched_pages = []

        def mock_fetch(num=100, page=1, in_stock_only=True):
            fetched_pages.append(page)
            if page <= 7:
                return [
                    {
                        "id": f"p{page}_{i}",
                        "title": "TargetSpecial Item" if (page == 7 and i == 0) else f"Item {page}_{i}",
                        "price": 10000,
                        "is_in_stock": True,
                    }
                    for i in range(100)
                ]
            return [{"id": "old_end", "title": "Old Item", "price": 1000, "is_in_stock": True}]

        monitor.scraper.fetch_new_arrivals = mock_fetch
        monitor.notifier.dispatch = MagicMock()

        res = monitor.check_once(is_burst=True)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(fetched_pages, [1, 2, 3, 4, 5, 6, 7, 8])
        self.assertEqual(res["new_items_count"], 1)


if __name__ == "__main__":
    unittest.main()
