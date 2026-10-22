"""
定时调度器模块 (Scheduler)
支持定点准时触发 (如 20:00:00 / 21:00:00) + 上新密集爆发检测 (Burst Mode) + 日常平缓轮询
"""
import time
import threading
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Tuple
from core.monitor import DollyteriaMonitor

logger = logging.getLogger("dollyteria.scheduler")


class DollyteriaScheduler:
    def __init__(self, monitor: DollyteriaMonitor):
        self.monitor = monitor
        self.running = False
        self._thread: Optional[threading.Thread] = None

        # 爆发模式状态
        self.is_bursting = False
        self.burst_end_time = 0.0
        self.last_burst_check = 0.0

        # 平缓轮询状态
        self.last_interval_check = 0.0

        # 记录已触发的定点，避免同一分钟内多次触发
        self._triggered_today_markers = set()

    def start(self):
        """启动后台调度线程"""
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._run_loop, name="SchedulerThread", daemon=True)
        self._thread.start()
        logger.info("⏰ Dollyteria 定时调度器已启动")

    def stop(self):
        """停止调度线程"""
        self.running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        logger.info("⏰ Dollyteria 定时调度器已停止")

    def _get_next_drop_info(self) -> Tuple[Optional[str], int]:
        """
        计算下一个定点上新触发时间及剩余秒数
        返回: (格式化时间如 '20:00:00', 倒计时秒数)
        """
        sched_cfg = self.monitor.config.get("schedule", {})
        if not sched_cfg.get("enabled", True):
            return (None, -1)

        drop_times = sched_cfg.get("drop_times", [])
        if not drop_times:
            return (None, -1)

        now = datetime.now()
        candidates = []

        for dt_str in drop_times:
            parts = [int(p) for p in dt_str.strip().split(":") if p.strip().isdigit()]
            if not parts:
                continue
            hour = parts[0]
            minute = parts[1] if len(parts) > 1 else 0
            second = parts[2] if len(parts) > 2 else 0

            # 构造今日的目标时间
            target_today = now.replace(hour=hour, minute=minute, second=second, microsecond=0)
            if target_today > now:
                candidates.append((dt_str, target_today))
            else:
                # 明日同一时间
                target_tomorrow = target_today + timedelta(days=1)
                candidates.append((dt_str, target_tomorrow))

        if not candidates:
            return (None, -1)

        candidates.sort(key=lambda x: x[1])
        next_label, next_dt = candidates[0]
        remaining = int((next_dt - now).total_seconds())
        return (next_label, remaining)

    def trigger_burst(self, duration_sec: Optional[int] = None):
        """立即手动开启爆发模式 (如测试或手动赶上新)"""
        sched_cfg = self.monitor.config.get("schedule", {})
        dur = duration_sec or sched_cfg.get("burst_duration_sec", 180)
        self.is_bursting = True
        self.burst_end_time = time.time() + dur
        logger.info(f"⚡ 爆发模式 (Burst Mode) 已开启！将持续高频检测 {dur} 秒。")
        # 预先登录并储备备用 Worker 会话，保障爆发抢购零秒出击
        if hasattr(self.monitor, "auto_order") and self.monitor.auto_order:
            self.monitor.auto_order.preload_standby_workers(count=2)

    def _run_loop(self):
        """调度主循环：每秒唤醒一次评估时间"""
        while self.running:
            try:
                now = datetime.now()
                now_ts = time.time()
                sched_cfg = self.monitor.config.get("schedule", {})

                if not sched_cfg.get("enabled", True):
                    time.sleep(1)
                    continue

                # 1. 检查爆发模式 (Burst Mode)
                if self.is_bursting:
                    if now_ts < self.burst_end_time:
                        burst_interval = float(sched_cfg.get("burst_interval_sec", 2.0))
                        if now_ts - self.last_burst_check >= burst_interval:
                            self.last_burst_check = now_ts
                            logger.info(f"⚡ [爆发模式] 正在进行高频检测 (剩余 {int(self.burst_end_time - now_ts)}s)...")
                            self.monitor.check_once(is_burst=True)
                    else:
                        self.is_bursting = False
                        logger.info("⚡ 爆发模式持续时间结束，恢复正常巡检模式。")

                # 2. 检查定点准时触发 (如 20:00:00 / 21:00:00)
                drop_times = sched_cfg.get("drop_times", [])
                today_str = now.strftime("%Y-%m-%d")

                # 智能前置预热：若距离下一轮定点上新仅剩 20 秒，提前登录预热备用 Worker 会话
                _, rem_drop_sec = self.get_next_drop_countdown()
                if 1 <= rem_drop_sec <= 20:
                    if hasattr(self.monitor, "auto_order") and self.monitor.auto_order:
                        self.monitor.auto_order.preload_standby_workers(count=2)

                for dt_str in drop_times:
                    parts = [int(p) for p in dt_str.strip().split(":") if p.strip().isdigit()]
                    if not parts:
                        continue
                    hour = parts[0]
                    minute = parts[1] if len(parts) > 1 else 0
                    second = parts[2] if len(parts) > 2 else 0

                    marker = f"{today_str}_{hour:02d}:{minute:02d}:{second:02d}"

                    # 当前时间与目标时间是否吻合 (允许秒级命中)
                    is_time_hit = (now.hour == hour and now.minute == minute and abs(now.second - second) <= 1)

                    if is_time_hit and marker not in self._triggered_today_markers:
                        self._triggered_today_markers.add(marker)
                        logger.info(f"🎯 准时命中定点上新时刻 [{dt_str}]！启动爆发抢购监控！")
                        self.trigger_burst(sched_cfg.get("burst_duration_sec", 180))
                        break

                # 3. 检查日常平缓周期巡检 (当非爆发期时)
                if not self.is_bursting and sched_cfg.get("interval_enabled", True):
                    interval_min = float(sched_cfg.get("interval_minutes", 5))
                    interval_sec = interval_min * 60.0
                    if now_ts - self.last_interval_check >= interval_sec:
                        self.last_interval_check = now_ts
                        logger.info(f"⏰ 执行日常平缓巡检 (每 {interval_min} 分钟一次)...")
                        self.monitor.check_once(is_burst=False)

                # 跨天重置触发标记集合，防止常驻运行内存增长
                if not hasattr(self, "_last_date") or self._last_date != today_str:
                    self._last_date = today_str
                    self._triggered_today_markers.clear()

                # 4. 每日清晨在库全量盘点与售罄更新 (默认 05:00:00，仅约 8MB 流量)
                daily_sync_cfg = sched_cfg.get("daily_stock_sync", {})
                if daily_sync_cfg.get("enabled", True):
                    sync_time_str = daily_sync_cfg.get("time", "05:00:00").strip()
                    parts = [int(p) for p in sync_time_str.split(":") if p.strip().isdigit()]
                    if len(parts) >= 2:
                        shour = parts[0]
                        smin = parts[1]
                        ssec = parts[2] if len(parts) > 2 else 0
                        sync_marker = f"{today_str}_stocksync_{shour:02d}:{smin:02d}:{ssec:02d}"
                        is_sync_hit = (now.hour == shour and now.minute == smin and abs(now.second - ssec) <= 1)
                        if is_sync_hit and sync_marker not in self._triggered_today_markers:
                            self._triggered_today_markers.add(sync_marker)
                            logger.info(f"⏰ 准时命中每日在库盘点时刻 [{sync_time_str}]！正在启动差集更新...")
                            if hasattr(self.monitor, "archiver") and self.monitor.archiver:
                                self.monitor.archiver.start_stock_sync(delay=1.0)

            except Exception as e:
                logger.error(f"调度循环异常: {e}", exc_info=True)

            # 动态微休眠：爆发抢购期使用 50ms 超高速唤醒，精准执行 0.3s 等高频轮询；平常巡检期使用 0.5s 低功耗休眠
            loop_sleep = 0.05 if self.is_bursting else 0.5
            time.sleep(loop_sleep)

    def get_status(self) -> Dict:
        """获取当前调度器运行状态及倒计时信息"""
        next_label, seconds_to_next = self._get_next_drop_info()
        burst_remaining = max(0, int(self.burst_end_time - time.time())) if self.is_bursting else 0

        return {
            "running": self.running,
            "is_bursting": self.is_bursting,
            "burst_remaining_sec": burst_remaining,
            "next_drop_time": next_label,
            "seconds_to_next_drop": seconds_to_next,
            "last_check_time": self.monitor.last_check_time,
            "last_check_status": self.monitor.last_check_status,
            "is_checking": self.monitor.is_checking,
        }
