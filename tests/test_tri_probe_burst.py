"""
三路协同爆发探测引擎单元测试 (Tri-Probe Burst Engine Tests)
测试分工协同、session 交替错峰探测、超时熔断与跨探针内存排重
"""
import time
import threading
import unittest
from unittest.mock import MagicMock, patch
from core.monitor import DollyteriaMonitor, TriProbeBurstEngine


class TestTriProbeBurstEngine(unittest.TestCase):
    def setUp(self):
        self.monitor = DollyteriaMonitor()
        self.monitor.config["watches"] = [
            {
                "id": "w_sdgr",
                "name": "SDGr/ジョー・マーチ",
                "keywords": ["SDGr", "ジョー・マーチ"],
                "enabled": True,
                "search_mode": "new_stream",
                "match_mode": "all",
                "auto_order": True,
            },
            {
                "id": "w_wig",
                "name": "SDM 假发",
                "keywords": ["SDM", "ウィッグ"],
                "enabled": True,
                "search_mode": "new_stream",
                "match_mode": "all",
                "auto_order": True,
            }
        ]
        self.monitor.config["schedule"]["burst_interval_sec"] = 0.05
        self.monitor.config["schedule"]["burst_probe_timeout_sec"] = 2.0
        self.monitor.state["seen_ids"] = []
        self.monitor.state["history"] = []

    def tearDown(self):
        if self.monitor.is_burst_probes_running():
            self.monitor.stop_burst_probes()

    def test_01_probes_lifecycle_start_and_stop(self):
        """测试三路并发探针能够正确启动和安全停止退出"""
        self.assertFalse(self.monitor.is_burst_probes_running())
        self.monitor.start_burst_probes(duration_sec=3)
        self.assertTrue(self.monitor.is_burst_probes_running())

        # 检查线程池中的三个工作线程
        thread_names = [t.name for t in self.monitor._burst_engine._threads if t.is_alive()]
        self.assertIn("BurstWorker_A_Full100", thread_names)
        self.assertIn("BurstWorker_B_Fast30", thread_names)
        self.assertIn("BurstWorker_C_Fast30", thread_names)

        # 停止并验证
        self.monitor.stop_burst_probes()
        self.assertFalse(self.monitor.is_burst_probes_running())
        time.sleep(0.2)
        alive_after = [t.name for t in self.monitor._burst_engine._threads if t.is_alive()]
        self.assertEqual(alive_after, [])

    def test_02_cross_verification_and_deduplication(self):
        """
        测试先锋探针 (B/C) 率先发现商品触发下单后，
        全量保底探针 (A) 随后拉取 100 件时能自动排重，且能补充拉取 30 件以后的新商品
        """
        ordered_ids = []
        self.monitor._execute_auto_orders_concurrent = lambda tasks, wait_completion=False: ordered_ids.extend([it["id"] for it, _ in tasks])
        self.monitor.notifier.dispatch = MagicMock()

        # 探针 B/C 抓取到的前 30 件 (包含 806588)
        fast_30_items = [
            {"id": "806588", "title": "SDGr ジョー・マーチ 幼SD", "price": 99000, "is_in_stock": True},
            {"id": "806589", "title": "普通商品1", "price": 1000, "is_in_stock": True},
        ]

        # 1. 探针 B 先行上报 30 件
        matches_b = self.monitor.process_stream_items(fast_30_items, source_tag="探针B-30件", is_burst=True)
        self.assertEqual(len(matches_b), 1)
        self.assertEqual(matches_b[0]["id"], "806588")
        self.assertEqual(ordered_ids, ["806588"], "探针B应立刻零延迟触发 806588 的下单")
        self.assertIn("806588", self.monitor.state["seen_ids"])

        # 探针 A 随后抓取到的 100 件 (包含 806588，以及位于第 45 位的 806599 SDM 假发)
        heavy_100_items = [
            {"id": "806588", "title": "SDGr ジョー・マーチ 幼SD", "price": 99000, "is_in_stock": True},
            {"id": "806589", "title": "普通商品1", "price": 1000, "is_in_stock": True},
            {"id": "806599", "title": "SDM ウィッグ 假发", "price": 3500, "is_in_stock": True},
        ]

        # 2. 探针 A 随后上报 100 件
        matches_a = self.monitor.process_stream_items(heavy_100_items, source_tag="探针A-100件", is_burst=True)
        self.assertEqual(len(matches_a), 1)
        self.assertEqual(matches_a[0]["id"], "806599", "探针A应自动跳过已见过的 806588，仅触发第 45 件 806599")
        self.assertEqual(ordered_ids, ["806588", "806599"], "订单列表中绝不能出现重复的 806588")

        # 3. 探针 C 再次抓取到相同数据，应全部跳过
        matches_c = self.monitor.process_stream_items(fast_30_items, source_tag="探针C-30件", is_burst=True)
        self.assertEqual(matches_c, [], "探针C面对已见商品应完全早停静默，不重复触发")
        self.assertEqual(ordered_ids, ["806588", "806599"])

    def test_03_burst_engine_with_mocked_network(self):
        """测试在模拟网络环境下，A/B/C 三个真实工作线程在后台并发协同探测"""
        fetched_calls = []

        def mock_fetch(num=100, page=1, in_stock_only=True, timeout=None, session=None, max_retries=None):
            fetched_calls.append({"num": num, "timeout": timeout, "has_session": session is not None})
            if num == 30 and len(fetched_calls) >= 2:
                # 探针 B 或 C 在第 2 次探测时拿到商品
                return [{"id": "fast_hit_1", "title": "SDGr ジョー・マーチ 限定", "price": 99000, "is_in_stock": True}]
            return []

        self.monitor.scraper.fetch_new_arrivals = mock_fetch
        ordered_ids = []
        self.monitor._execute_auto_orders_concurrent = lambda tasks, wait_completion=False: ordered_ids.extend([it["id"] for it, _ in tasks])
        self.monitor.notifier.dispatch = MagicMock()

        # 启动爆发探测 0.5 秒
        self.monitor.start_burst_probes(duration_sec=0.5)
        time.sleep(0.4)
        self.monitor.stop_burst_probes()

        # 验证探针发起的请求特征
        nums_called = [c["num"] for c in fetched_calls]
        self.assertIn(100, nums_called, "探针A必须请求过 100 件")
        self.assertIn(30, nums_called, "探针B/C必须请求过 30 件")
        for c in fetched_calls:
            self.assertEqual(c["timeout"], 2.0, "爆发期所有探针 timeout 必须是 2.0s")
            self.assertTrue(c["has_session"], "探针必须使用各自独立的 Session")

        # 验证轻量先锋命中触发了下单
        self.assertIn("fast_hit_1", ordered_ids)

    def test_04_worker_b_guards_page1_and_worker_c_cascades(self):
        """
        验证规则：
        1. 探针 B 始终死守第 1 页 (1~30件)，无论上新多大，绝不翻页；
        2. 探针 C 在第 1 页抓到上新数据流后，立即顺延向后拉取第 2 页 (31~60件)、第 3 页 (61~90件)；
        3. 探针 C 顺延扫完后自动重置回第 1 页继续与 B 交替。
        """
        call_records = []
        c_page_sequence = []

        def mock_fetch(num=100, page=1, in_stock_only=True, timeout=None, session=None, max_retries=None):
            th_name = threading.current_thread().name
            call_records.append((th_name, page, num))

            if th_name == "BurstWorker_B_Fast30":
                # B 探针抓到的数据
                return [{"id": f"b_item_{i}", "title": f"B商品 {i}", "price": 1000, "is_in_stock": True} for i in range(30)]

            if th_name == "BurstWorker_C_Fast30":
                c_page_sequence.append(page)
                if page == 1:
                    # 第 1 页发现新商品，且满 30 件
                    return [{"id": f"c_p1_{i}", "title": f"C_P1商品 {i}", "price": 2000, "is_in_stock": True} for i in range(30)]
                elif page == 2:
                    # 第 2 页满 30 件，且命中规则商品
                    return [{"id": "806588", "title": "SDGr ジョー・マーチ 幼SD", "price": 99000, "is_in_stock": True}] + [
                        {"id": f"c_p2_{i}", "title": f"C_P2商品 {i}", "price": 3000, "is_in_stock": True} for i in range(29)
                    ]
                else:
                    # 第 3 页不足 30 件 (接驳完成)
                    return [{"id": f"c_p3_{i}", "title": f"C_P3商品 {i}", "price": 4000, "is_in_stock": True} for i in range(10)]

            return []

        self.monitor.scraper.fetch_new_arrivals = mock_fetch
        ordered_ids = []
        self.monitor._execute_auto_orders_concurrent = lambda tasks, wait_completion=False: ordered_ids.extend([it["id"] for it, _ in tasks])
        self.monitor.notifier.dispatch = MagicMock()

        self.monitor.start_burst_probes(duration_sec=0.6)
        time.sleep(0.45)
        self.monitor.stop_burst_probes()

        # 1. 验证探针 B 的行为：所有调用必须都是 page == 1！
        b_calls = [page for th, page, num in call_records if th == "BurstWorker_B_Fast30"]
        self.assertTrue(len(b_calls) > 0, "探针 B 必须执行了请求")
        self.assertTrue(all(p == 1 for p in b_calls), f"探针 B 必须死守第 1 页，实际请求页码: {b_calls}")

        # 2. 验证探针 C 的行为：在第 1 页抓到后，顺延抓取了第 2 页！
        self.assertIn(1, c_page_sequence, "探针 C 必须先抓取第 1 页")
        self.assertIn(2, c_page_sequence, "探针 C 在第 1 页发现上新后必须顺延抓取第 2 页")

        # 3. 验证探针 C 顺延到第 2 页时抓取到的 806588 成功被下单！
        self.assertIn("806588", ordered_ids, "探针 C 顺延至第 2 页时必须成功命中并下单位于第 2 页的商品")


if __name__ == "__main__":
    unittest.main()
