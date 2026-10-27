"""
实时运行终端日志管理模块 (Terminal Log Manager)
捕获系统全量运行日志并维护高性能环形内存缓冲 (Ring Buffer)，
支持动态分级检索、关键词即时过滤、100/300/1000 行限制切换与 SSE 实时广播。
"""
import sys
import time
import logging
import threading
from collections import deque
from typing import List, Dict, Optional, Callable, Any

class TerminalLogEntry:
    def __init__(self, entry_id: int, timestamp: float, level: str, logger_name: str, message: str, formatted: str):
        self.id = entry_id
        self.timestamp = timestamp
        self.level = level.upper()
        self.logger_name = logger_name
        self.message = message
        self.formatted = formatted

    def to_dict(self) -> Dict[str, Any]:
        time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp))
        time_short = time.strftime("%H:%M:%S", time.localtime(self.timestamp))
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "time": time_str,
            "time_short": time_short,
            "level": self.level,
            "logger": self.logger_name,
            "message": self.message,
            "formatted": self.formatted,
        }


class TerminalLogHandler(logging.Handler):
    """自定义日志处理器，将所有 Python 核心组件日志接入终端管理器"""
    def __init__(self, manager: "TerminalLogManager"):
        super().__init__()
        self.manager = manager
        self.setFormatter(logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S"
        ))

    def emit(self, record: logging.LogRecord):
        try:
            formatted = self.format(record)
            msg = record.getMessage()
            self.manager.add_log(
                level=record.levelname,
                logger_name=record.name,
                message=msg,
                formatted=formatted,
                timestamp=record.created
            )
        except Exception:
            self.handleError(record)


class StdoutTee:
    """拦截控制台标准输出并转发至 TerminalLogManager，同时保证原有控制台输出"""
    def __init__(self, original_stream, manager: "TerminalLogManager", level: str = "INFO"):
        self.original_stream = original_stream
        self.manager = manager
        self.level = level
        self._buffer = ""
        self._lock = threading.Lock()

    def write(self, s):
        try:
            self.original_stream.write(s)
            self.original_stream.flush()
        except Exception:
            pass

        if not s:
            return

        with self._lock:
            self._buffer += str(s)
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                line = line.strip("\r")
                clean = line.strip()
                if clean:
                    # 避免拦截 Python logging 标准日志输出 (已被 TerminalLogHandler 捕获)，防止出现重复日志
                    is_log_format = (
                        (" [INFO] " in clean or " [WARNING] " in clean or " [ERROR] " in clean or " [DEBUG] " in clean) and
                        ("dollyteria" in clean or "console" in clean or clean.startswith("20") or (len(clean) >= 8 and clean[2] == ':' and clean[5] == ':'))
                    )
                    if "dollyteria.terminal" not in clean and not is_log_format:
                        self.manager.add_log(
                            level=self.level,
                            logger_name="console",
                            message=clean
                        )

    def flush(self):
        try:
            self.original_stream.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self.original_stream, name)


class TerminalLogManager:
    _instance: Optional["TerminalLogManager"] = None
    _lock = threading.Lock()

    def __init__(self, max_buffer_size: int = 2000):
        self.max_buffer_size = max_buffer_size
        self._buffer: deque[TerminalLogEntry] = deque(maxlen=max_buffer_size)
        self._seq_id = 0
        self._listeners: List[Callable[[Dict[str, Any]], None]] = []
        self._handler: Optional[TerminalLogHandler] = None
        self._is_installed = False
        self._buf_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "TerminalLogManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def install(self):
        """将日志 Handler 挂载至根 Logger 与各大组件 Logger，拦截系统运行日志"""
        if self._is_installed:
            return
        with self._lock:
            if self._is_installed:
                return
            self._handler = TerminalLogHandler(self)
            self._handler.setLevel(logging.INFO)

            # 确保 root 挂载 Handler 并接收 INFO (所有子 Logger 如 dollyteria 自动向上冒泡冒给 root，避免重复添加导致双倍输出)
            root_logger = logging.getLogger()
            root_logger.setLevel(logging.INFO)
            if self._handler not in root_logger.handlers:
                root_logger.addHandler(self._handler)

            dolly_logger = logging.getLogger("dollyteria")
            dolly_logger.setLevel(logging.INFO)

            # 捕获控制台 stdout/stderr 输出
            try:
                if not isinstance(sys.stdout, StdoutTee):
                    sys.stdout = StdoutTee(sys.stdout, self, level="INFO")
                if not isinstance(sys.stderr, StdoutTee):
                    sys.stderr = StdoutTee(sys.stderr, self, level="ERROR")
            except Exception:
                pass

            self._is_installed = True
            
            # 预热并写入启动状态基础日志，确保一打开终端立即能看到系统就绪状态
            t_str = time.strftime("%H:%M:%S")
            self.add_log(
                level="INFO",
                logger_name="dollyteria.system",
                message="🎀 Dollyteria 智能上新监控与秒级自动下单引擎已启动就绪",
                formatted=f"{t_str} [INFO] dollyteria.system: 🎀 Dollyteria 智能上新监控与秒级自动下单引擎已启动就绪"
            )
            self.add_log(
                level="INFO",
                logger_name="dollyteria.terminal",
                message="💻 实时运行终端已连接，正在监听核心调度器、网络询盘与自动抢购流水线...",
                formatted=f"{t_str} [INFO] dollyteria.terminal: 💻 实时运行终端已连接，正在监听核心调度器、网络询盘与自动抢购流水线..."
            )

    def seed_status(self, monitor, scheduler):
        """向终端注入当前系统的实时参数状态日志"""
        try:
            watches = monitor.config.get("watches", [])
            active_watches = [w for w in watches if w.get("enabled", True)]
            seen_count = len(monitor.state.get("seen_ids", []))
            ao_cfg = monitor.config.get("auto_order", {})
            ao_enabled = ao_cfg.get("enabled", False)
            ao_dry = ao_cfg.get("dry_run", True)
            sched_status = scheduler.get_status() if scheduler else {}

            self.add_log(
                level="INFO",
                logger_name="dollyteria.monitor",
                message=f"已加载监控规则 {len(watches)} 条 (当前启用: {len(active_watches)} 条) | 历史已知商品基准线: {seen_count} 件"
            )
            self.add_log(
                level="INFO",
                logger_name="dollyteria.auto_order",
                message=f"自动下单状态: {'🟢 已开启' if ao_enabled else '⚪ 已关闭'} (模式: {'🧪 仿真模拟 DRY-RUN' if ao_dry else '🚨 真实实盘购买'} | 备用 Worker 池: {ao_cfg.get('standby_workers', 2)} 个)"
            )
            if sched_status:
                self.add_log(
                    level="INFO",
                    logger_name="dollyteria.scheduler",
                    message=f"定点调度器运行状态: {sched_status.get('state', 'running')} | 下次定点爆发上新: {sched_status.get('next_drop', '无')} (倒计时: {sched_status.get('next_drop_countdown', '--')})"
                )
        except Exception:
            pass

    def add_listener(self, callback: Callable[[Dict[str, Any]], None]):
        """注册 SSE 或 WebSocket 推送回调"""
        with self._buf_lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[Dict[str, Any]], None]):
        with self._buf_lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def add_log(self, level: str, logger_name: str, message: str, formatted: Optional[str] = None, timestamp: Optional[float] = None) -> Dict[str, Any]:
        """追加单条日志到环形缓冲区，并分发至监听者"""
        ts = timestamp or time.time()
        time_str = time.strftime("%H:%M:%S", time.localtime(ts))
        if formatted is None:
            formatted = f"{time_str} [{level.upper()}] {logger_name}: {message}"

        with self._buf_lock:
            # 防御性去重：若极短时间内 (0.08s 内) 收到同一 logger 的相同级别相同日志，判定为重复投递并忽略
            if self._buffer:
                last = self._buffer[-1]
                if (last.logger_name == logger_name and 
                    last.message == message and 
                    last.level == level.upper() and 
                    abs(ts - last.timestamp) < 0.08):
                    return last.to_dict()

            self._seq_id += 1
            entry = TerminalLogEntry(
                entry_id=self._seq_id,
                timestamp=ts,
                level=level,
                logger_name=logger_name,
                message=message,
                formatted=formatted
            )
            self._buffer.append(entry)
            data = entry.to_dict()
            listeners = list(self._listeners)

        for cb in listeners:
            try:
                cb(data)
            except Exception:
                pass

        return data

    def get_logs(
        self,
        limit: int = 100,
        level: Optional[str] = None,
        keyword: Optional[str] = None,
        since_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        获取日志列表
        - limit: 100, 300, 1000 等，上限 1000
        - level: ALL / INFO / WARNING / ERROR 筛选
        - keyword: 关键词匹配
        - since_id: 增量拉取
        """
        # 强制上限最多 1000 行
        limit = max(1, min(int(limit), 1000))
        level_filter = level.strip().upper() if level and level.strip().upper() != "ALL" else None
        kw_filter = keyword.strip().lower() if keyword and keyword.strip() else None

        with self._buf_lock:
            entries = list(self._buffer)
            total_buffered = len(entries)

        # 过滤
        filtered: List[TerminalLogEntry] = []
        for e in entries:
            if since_id is not None and e.id <= since_id:
                continue
            if level_filter:
                if level_filter == "WARN" and e.level == "WARNING":
                    pass
                elif e.level != level_filter:
                    continue
            if kw_filter:
                if kw_filter not in e.message.lower() and kw_filter not in e.logger_name.lower():
                    continue
            filtered.append(e)

        # 取最近的 limit 行
        result_entries = filtered[-limit:]
        logs = [e.to_dict() for e in result_entries]

        return {
            "status": "ok",
            "total_buffered": total_buffered,
            "returned_count": len(logs),
            "limit": limit,
            "logs": logs,
        }

    def clear(self):
        """清空日志缓冲区"""
        with self._buf_lock:
            self._buffer.clear()
            self._seq_id = 0
        self.add_log(
            level="INFO",
            logger_name="dollyteria.terminal",
            message="终端日志输出已被用户清空。",
            formatted=f"{time.strftime('%H:%M:%S')} [INFO] dollyteria.terminal: 终端日志输出已被用户清空。"
        )
