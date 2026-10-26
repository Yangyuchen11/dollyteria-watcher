"""
直接下单 / 自动加购扩展接口 (Auto-Order Hook)
针对 Dollyteria (おちゃのこネット / ocnk 架构) 的全自动下单流程：
1. 会员登录凭据验证与 Session 保持
2. 商品秒级加购 (/product/{id} -> /cart)，支持遭遇 503 快速高频重试直至售罄
3. 推进客户信息与收货地址步骤 (/cart/shipping)
4. 自动匹配并勾选【代金引換 (COD) / 店頭支払 / 銀行振込】
5. 推进至下单前最终确认页面 (/cart/confirm)
6. 多线程独立会话并发 Worker 池支持不同商品并行抢购
7. 【Dry-Run 仿真测试模式】：
   - 完整走通上述所有链路直至 /cart/confirm
   - 提取订单金额明细、代金引换手续费、最终总额、收货人与地址
   - 🛡️ 安全拦截：坚决不点击最后的「購入する」提交按钮！
   - 自动清空测试加购的商品，保持购物车整洁。
"""
import os
import re
import json
import time
import socket
import struct
import base64
import shutil
import tempfile
import subprocess
import logging
import threading
import urllib.parse
from typing import Dict, Optional, Tuple, List
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("dollyteria.hooks.auto_order")

DEFAULT_RECAPTCHA_SITEKEY = "6LcxWfwqAAAAAKB2iNaL09Of7o8NS7aiDLF6Pi1p"

# 根据宿主运行环境自动匹配 User-Agent (Linux 容器环境使用 X11; Linux，Windows 使用 Windows NT)
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    if os.name != "nt"
    else "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class _SimpleCDPClient:
    """零外部依赖的轻量级 Chrome DevTools Protocol (WebSocket) 客户端"""

    def __init__(self, ws_url: str, timeout: float = 10.0):
        parsed = urllib.parse.urlparse(ws_url)
        self.host = parsed.hostname or "127.0.0.1"
        self.port = parsed.port or 9222
        self.path = parsed.path or "/"
        if parsed.query:
            self.path += "?" + parsed.query
        self.sock = socket.create_connection((self.host, self.port), timeout=timeout)
        self.sock.settimeout(timeout)
        self._handshake()
        self._msg_id = 0

    def _handshake(self):
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode("ascii"))
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(1024)
            if not chunk:
                raise ConnectionError("CDP WebSocket 握手意外断开")
            buf += chunk

    def _send_frame(self, text: str):
        payload = text.encode("utf-8")
        mask_key = os.urandom(4)
        header = bytearray([0x81])  # FIN + text frame
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        header.extend(mask_key)
        masked = bytearray(b ^ mask_key[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(header + masked)

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("CDP WebSocket 读取中断")
            buf += chunk
        return buf

    def _recv_frame(self) -> str:
        while True:
            b1, b2 = self._recv_exact(2)
            opcode = b1 & 0x0F
            masked = bool(b2 & 0x80)
            length = b2 & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]
            mask_key = self._recv_exact(4) if masked else None
            payload = self._recv_exact(length) if length > 0 else b""
            if masked and mask_key:
                payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:  # Close
                raise ConnectionError("CDP WebSocket 已关闭")
            if opcode == 0x9:  # Ping -> Pong
                pong = bytearray([0x8A, 0x80]) + os.urandom(4)
                self.sock.sendall(pong)
                continue
            if opcode in (0x1, 0x0):
                return payload.decode("utf-8", "ignore")

    def call(self, method: str, params: Optional[Dict] = None) -> Dict:
        self._msg_id += 1
        cid = self._msg_id
        self._send_frame(json.dumps({"id": cid, "method": method, "params": params or {}}))
        while True:
            raw = self._recv_frame()
            if not raw:
                continue
            data = json.loads(raw)
            if data.get("id") == cid:
                return data.get("result", {})

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


class _RecaptchaTabSlot:
    """单个独立预热浏览器标签页 (Tab) 上下文槽位"""

    def __init__(self, target_id: str, ws_url: str):
        self.target_id: str = target_id
        self.ws_url: str = ws_url
        self.lock = threading.Lock()
        self.in_use: bool = False
        self.is_preparing: bool = False
        self.is_fresh: bool = False
        self.sitekey: str = ""
        self.prepared_at: float = 0.0
        self.borrow_seq: int = 0
        self.prep_seq: int = 0
        self.success_count: int = 0
        self.fail_count: int = 0
        self.last_failed: bool = False


class RecaptchaTokenProvider:
    """
    Dollyteria 最终确认页 (/cart/confirm) Google reCAPTCHA v3 多标签并发预热与极速签发引擎：
    - 后台常驻单个轻量级无界面浏览器进程，内部维护与「预热备用会话数 (Standby Sessions)」数量一致的独立 Tab 标签页池
    - 注意：绝不调用 Runtime.enable（规避 Google BotGuard 对 CDP 序列化探测导致的低分拦截）
    - 采用「页面环境预热 (Pre-warmed Tab Pool) + 加购启动瞬间实时秒签 (Just-In-Time Execute)」机制：
      1. 预先完成各 Tab 的页面导航与 grecaptcha.ready 脚本加载，不提前囤积易过期的旧 Token
      2. 下单开始时分配独立 Tab 并行调用 grecaptcha.execute (0.15s~0.35s 极速出码，100% 0 秒账龄鲜活令牌)
      3. 在确认页提交完成前严格锁定签发 Tab 页面上下文完整不卸载，提交结束后由 finally 异步刷新归还池中
      4. 多线程并发下单时各线程独占不同 Tab 并行签发，零锁排队、绝不共用同一枚令牌
    """
    _instance = None
    _instance_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> 'RecaptchaTokenProvider':
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self):
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._port: int = 0
        self._user_data_dir: Optional[str] = None
        self._is_temp_profile: bool = False
        self._sitekey: str = DEFAULT_RECAPTCHA_SITEKEY
        self._target_pool_size: int = 2
        self._slots: List[_RecaptchaTabSlot] = []
        self._active_token_slots: Dict[str, Tuple[_RecaptchaTabSlot, int, str]] = {}
        self._rr_index: int = 0
        self._is_ready: bool = False
        self._keepalive_started: bool = False
        self.browser_user_agent: str = DEFAULT_USER_AGENT
        self._ua_probed: bool = False

    @property
    def pool_size(self) -> int:
        return self._target_pool_size

    def set_pool_size(self, count: int, sitekey: str = DEFAULT_RECAPTCHA_SITEKEY):
        """动态设置并同步预热 Tab 标签页池大小 (与前端设置的定点前预热备用会话数保持一致)"""
        target = max(1, min(16, int(count) if count is not None else 2))
        with self._lock:
            changed = (self._target_pool_size != target)
            self._target_pool_size = target
            should_sync = changed and (self._keepalive_started or self._is_ready)
        if should_sync:
            threading.Thread(
                target=self._sync_pool_in_bg,
                args=(sitekey,),
                name="RecaptchaPoolScaleThread",
                daemon=True,
            ).start()

    @staticmethod
    def _find_browser_executable() -> Optional[str]:
        if os.name == "nt":
            candidates = [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
            ]
            for path in candidates:
                if path and os.path.isfile(path):
                    return path
        for cmd in ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "msedge"]:
            found = shutil.which(cmd)
            if found:
                return found
        return None

    @staticmethod
    def _allocate_free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def _cleanup_proc_unlocked(self):
        self._is_ready = False
        self._active_token_slots.clear()
        self._slots.clear()
        if self._proc:
            pid = self._proc.pid
            try:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(pid)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=4,
                    )
                    self._proc.wait(timeout=2)
                else:
                    self._proc.terminate()
                    self._proc.wait(timeout=3)
            except Exception:
                pass
            self._proc = None
        if self._user_data_dir and getattr(self, "_is_temp_profile", False) and os.path.isdir(self._user_data_dir):
            shutil.rmtree(self._user_data_dir, ignore_errors=True)
            self._user_data_dir = None
        elif not getattr(self, "_is_temp_profile", False):
            # 持久化浏览器数据目录仅解绑引用，保留用户会话、TLS Tickets 与 Cookie 指纹供下次复用
            self._user_data_dir = None

    def _list_page_targets(self) -> List[Dict]:
        """查询当前浏览器进程中所有 page 类型的 Tab 目标"""
        if not self._port:
            return []
        try:
            r = requests.get(f"http://127.0.0.1:{self._port}/json", timeout=2.0)
            if r.status_code == 200:
                return [
                    t for t in r.json()
                    if t.get("type") == "page" and t.get("id") and t.get("webSocketDebuggerUrl")
                ]
        except Exception:
            pass
        return []

    def _create_new_page_target(self, helper_ws_url: Optional[str] = None) -> Optional[Dict]:
        """在当前 Chrome 进程中新建一个独立的空白 Tab 页并返回其 target 信息"""
        if not self._port:
            return None

        # 优先使用 CDP Target.createTarget (兼容所有 Chrome/Edge 版本)
        if not helper_ws_url:
            pages = self._list_page_targets()
            if pages:
                helper_ws_url = pages[0].get("webSocketDebuggerUrl")

        new_tid = None
        if helper_ws_url:
            cdp = None
            try:
                cdp = _SimpleCDPClient(helper_ws_url, timeout=5.0)
                # 使用 newWindow=False 在同一主窗口内创建标准 Tab，杜绝 Linux Xvfb/无窗管环境下多独立窗口失焦成为无焦点幽灵窗口
                res = cdp.call("Target.createTarget", {"url": "about:blank", "newWindow": False})
                new_tid = res.get("targetId")
            except Exception:
                pass
            finally:
                if cdp:
                    cdp.close()

        # 备用：通过 HTTP /json/new 创建
        if not new_tid:
            try:
                r = requests.put(f"http://127.0.0.1:{self._port}/json/new?about:blank", timeout=2.5)
                if r.status_code != 200:
                    r = requests.get(f"http://127.0.0.1:{self._port}/json/new?about:blank", timeout=2.5)
                if r.status_code == 200:
                    data = r.json()
                    if data.get("id") and data.get("webSocketDebuggerUrl"):
                        return data
                    new_tid = data.get("id")
            except Exception:
                pass

        # 查询最新列表获取对应的 webSocketDebuggerUrl
        for _ in range(10):
            pages = self._list_page_targets()
            if new_tid:
                for p in pages:
                    if p.get("id") == new_tid:
                        return p
            elif pages:
                return pages[-1]
            time.sleep(0.05)
        return None

    def _close_page_target(self, target_id: str):
        if not self._port or not target_id:
            return
        try:
            requests.get(f"http://127.0.0.1:{self._port}/json/close/{target_id}", timeout=1.5)
        except Exception:
            pass

    def _prepare_slot(
        self,
        slot: _RecaptchaTabSlot,
        sitekey: str = DEFAULT_RECAPTCHA_SITEKEY,
        force_navigate: bool = True,
    ) -> bool:
        """
        将单个 Tab 槽位导航至 www.dollyteria.com 极轻静态页并完成 grecaptcha v3 实例加载预热：
        1. 开启 Emulation.setFocusEmulationEnabled 确保槽位始终保持 visible + focus 满信誉状态
        2. 导航至带唯一序列号 ?_r={seq_tag} 的轻量 URL 并严格等待 document.readyState === 'complete'，
           杜绝在旧页面未卸载完时抢先注入导致的令牌失效
        3. 若官网在极端网络断连期无法完成导航，自动兜底复用当前已存在的 grecaptcha 上下文
        """
        cdp = None
        try:
            cdp = _SimpleCDPClient(slot.ws_url, timeout=12.0)
            try:
                cdp.call("Target.activateTarget", {"targetId": slot.target_id})
            except Exception:
                pass
            try:
                cdp.call("Page.bringToFront", {})
            except Exception:
                pass
            try:
                cdp.call("Emulation.setFocusEmulationEnabled", {"enabled": True})
            except Exception:
                pass

            # 注入底层反自动化指纹对抗 (Anti-Detect Stealth)，抹除无头特征提升 Google v3 评分
            stealth_js = """
            try {
                // 1. 抹除自动化驱动标记
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined, configurable: true });

                // 2. 伪装浏览器原生插件列表 (标准 Chrome PDF 插件)
                const pluginList = [
                    { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                    { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
                    { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' }
                ];
                Object.defineProperty(navigator, 'plugins', { get: () => pluginList, configurable: true });

                // 3. 补齐 Chrome 专属运行时与性能指标 API (Google BotGuard 关键侦测项)
                if (!window.chrome) { window.chrome = {}; }
                window.chrome.runtime = window.chrome.runtime || {
                    connect: function() {},
                    sendMessage: function() {},
                    onMessage: { addListener: function() {} }
                };
                window.chrome.loadTimes = window.chrome.loadTimes || function() {
                    return {
                        commitLoadTime: Date.now() / 1000 - 0.5,
                        connectionInfo: 'http/1.1',
                        finishDocumentLoadTime: Date.now() / 1000 - 0.2,
                        finishLoadTime: Date.now() / 1000 - 0.1,
                        firstPaintAfterLoadTime: 0,
                        firstPaintTime: Date.now() / 1000 - 0.3,
                        navigationType: 'Other',
                        npnNegotiatedProtocol: 'unknown',
                        requestTime: Date.now() / 1000 - 1.0,
                        startLoadTime: Date.now() / 1000 - 0.9,
                        wasAlternateProtocolAvailable: false,
                        wasFetchedViaSpdy: false,
                        wasNpnNegotiated: false
                    };
                };
                window.chrome.csi = window.chrome.csi || function() {
                    return {
                        startE: Date.now() - 1000,
                        onloadT: Date.now() - 200,
                        pageT: 800,
                        tran: 15
                    };
                };

                // 4. 语言与可见性伪装 (强制 visible，杜绝多标签在后台被判定为 hidden 降分)
                Object.defineProperty(navigator, 'languages', { get: () => ['ja-JP', 'ja', 'en-US', 'en'], configurable: true });
                Object.defineProperty(navigator, 'language', { get: () => 'ja-JP', configurable: true });
                Object.defineProperty(document, 'hidden', { get: () => false, configurable: true });
                Object.defineProperty(document, 'visibilityState', { get: () => 'visible', configurable: true });
                Object.defineProperty(document, 'webkitVisibilityState', { get: () => 'visible', configurable: true });

                // 5. WebGL 真实显卡信息欺骗 (抹除 Linux Xvfb 下 llvmpipe / SwiftShader 纯软件渲染无头特征)
                const patchWebGL = (proto) => {
                    if (!proto || !proto.getParameter) return;
                    const origGetParam = proto.getParameter;
                    proto.getParameter = function(param) {
                        if (param === 37445) return 'Google Inc. (NVIDIA)';
                        if (param === 37446) return 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 SUPER Direct3D11 vs_5_0 ps_5_0, D3D11)';
                        return origGetParam.apply(this, arguments);
                    };
                };
                if (window.WebGLRenderingContext) patchWebGL(WebGLRenderingContext.prototype);
                if (window.WebGL2RenderingContext) patchWebGL(WebGL2RenderingContext.prototype);

                // 6. 音频上下文通道修正 (修正 Linux 无声卡容器下 maxChannelCount=0 特征)
                if (window.AudioContext || window.webkitAudioContext) {
                    const AC = window.AudioContext || window.webkitAudioContext;
                    const origDestDesc = Object.getOwnPropertyDescriptor(AC.prototype, 'destination');
                    if (origDestDesc && origDestDesc.get) {
                        const origGet = origDestDesc.get;
                        Object.defineProperty(AC.prototype, 'destination', {
                            get: function() {
                                const dest = origGet.apply(this);
                                try { Object.defineProperty(dest, 'maxChannelCount', { value: 2 }); } catch(e) {}
                                return dest;
                            }
                        });
                    }
                }
            } catch (e) {}
            """
            try:
                cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": stealth_js})
            except Exception:
                pass
            try:
                cdp.call("Runtime.evaluate", {"expression": stealth_js})
            except Exception:
                pass

            if not force_navigate and slot.sitekey in ("", sitekey):
                chk = cdp.call("Runtime.evaluate", {
                    "expression": (
                        "Boolean(window.location && window.location.origin === 'https://www.dollyteria.com' "
                        "&& window.grecaptcha && typeof window.grecaptcha.execute === 'function')"
                    ),
                    "returnByValue": True,
                })
                if chk.get("result", {}).get("value") is True:
                    slot.is_fresh = True
                    slot.sitekey = sitekey
                    slot.prepared_at = time.time()
                    return True

            slot.prep_seq += 1
            seq_tag = f"{slot.target_id[:6]}_{slot.prep_seq}_{int(time.time() * 1000) % 100000}"
            nav_urls = [
                f"https://www.dollyteria.com/robots.txt?_r={seq_tag}",
                f"https://www.dollyteria.com/favicon.ico?_r={seq_tag}",
            ]
            nav_committed = False
            for nav_url in nav_urls:
                cdp.call("Page.navigate", {"url": nav_url})
                for _ in range(35):
                    time.sleep(0.05)
                    r_state = cdp.call("Runtime.evaluate", {
                        "expression": (
                            f"Boolean(window.location && window.location.href.includes('_r={seq_tag}') "
                            f"&& document.readyState === 'complete')"
                        ),
                        "returnByValue": True,
                    })
                    if r_state.get("result", {}).get("value") is True:
                        nav_committed = True
                        break
                if nav_committed:
                    break

            # 导航完成后立即在当前活动 DOM 上就地再次注入反指纹对抗脚本，确保 grecaptcha.api 加载前指纹已完美就绪
            try:
                cdp.call("Runtime.evaluate", {"expression": stealth_js})
            except Exception:
                pass

            js_inject = f"""
            new Promise((resolve, reject) => {{
                if (window.grecaptcha && typeof window.grecaptcha.execute === 'function') {{
                    window.grecaptcha.ready(() => resolve(true));
                    return;
                }}
                const s = document.createElement('script');
                s.src = 'https://www.google.com/recaptcha/api.js?render={sitekey}';
                s.onload = () => {{
                    let ticks = 0;
                    const timer = setInterval(() => {{
                        ticks++;
                        if (window.grecaptcha && window.grecaptcha.execute) {{
                            clearInterval(timer);
                            window.grecaptcha.ready(() => resolve(true));
                        }} else if (ticks > 150) {{
                            clearInterval(timer);
                            reject('grecaptcha init timeout');
                        }}
                    }}, 30);
                }};
                s.onerror = () => reject('grecaptcha script load error');
                (document.head || document.documentElement).appendChild(s);
            }})
            """
            res = cdp.call("Runtime.evaluate", {
                "expression": js_inject,
                "awaitPromise": True,
                "returnByValue": True,
            })
            if res.get("result", {}).get("value") is True:
                slot.is_fresh = True
                slot.sitekey = sitekey
                slot.prepared_at = time.time()
                return True
            slot.is_fresh = False
            return False
        except Exception as e:
            if self._proc is not None and self._proc.poll() is None:
                logger.warning(f"⚠️ [reCAPTCHA] 标签页 [{slot.target_id[:6]}] 预热注入异常: {e}")
            slot.is_fresh = False
            return False
        finally:
            if cdp:
                cdp.close()

    def _ensure_browser_and_pool_ready(self, sitekey: str = DEFAULT_RECAPTCHA_SITEKEY) -> bool:
        """确保 Chrome 进程存活且内部预热 Tab 池数量与 target_pool_size 对齐"""
        with self._lock:
            need_restart = (
                self._proc is None
                or self._proc.poll() is not None
                or not self._port
            )
            if need_restart:
                self._cleanup_proc_unlocked()
                exe = self._find_browser_executable()
                if not exe:
                    logger.error("❌ [reCAPTCHA] 未找到本机 Chrome / Edge / Chromium 浏览器，无法签发 reCAPTCHA v3 令牌")
                    return False

                self._port = self._allocate_free_port()
                # 持久化浏览器数据目录：沉淀 Google 账户会话缓存、TLS Session Tickets 与 Cookie 指纹，规避白板临时目录遭 Google v3 惩罚
                profile_base = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "browser_profile"))
                try:
                    os.makedirs(profile_base, exist_ok=True)
                    # 清理可能因异常退出遗留的 Singleton 锁，防止 Chromium 在 Linux 容器内被死锁阻塞
                    for lock_name in ["SingletonLock", "SingletonCookie", "SingletonSocket"]:
                        lk_path = os.path.join(profile_base, lock_name)
                        if os.path.exists(lk_path) or os.path.islink(lk_path):
                            try:
                                if os.path.islink(lk_path) or os.path.isfile(lk_path):
                                    os.unlink(lk_path)
                                elif os.path.isdir(lk_path):
                                    shutil.rmtree(lk_path, ignore_errors=True)
                            except Exception:
                                pass
                    self._user_data_dir = profile_base
                    self._is_temp_profile = False
                except Exception:
                    self._user_data_dir = tempfile.mkdtemp(prefix="dolly_recaptcha_")
                    self._is_temp_profile = True
                self._sitekey = sitekey

                args = [
                    exe,
                    f"--remote-debugging-port={self._port}",
                    "--remote-allow-origins=*",
                    f"--user-data-dir={self._user_data_dir}",
                    "--window-position=-32000,-32000",
                    "--window-size=1280,900",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-background-timer-throttling",
                    "--disable-backgrounding-occluded-windows",
                    "--disable-renderer-backgrounding",
                    "--enable-webgl",
                    "--ignore-gpu-blocklist",
                    "--lang=ja-JP,ja",
                ]
                if os.name != "nt":
                    args.extend(["--no-sandbox", "--disable-dev-shm-usage"])
                args.append("about:blank")

                kwargs = {
                    "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL,
                }
                if os.name == "nt":
                    si = subprocess.STARTUPINFO()
                    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    si.wShowWindow = 0  # SW_HIDE: 完全隐藏窗口，不占用任务栏与前台焦点
                    kwargs["startupinfo"] = si
                else:
                    if not os.environ.get("DISPLAY"):
                        xvfb = shutil.which("xvfb-run")
                        if xvfb:
                            args = [xvfb, "-a", "-s", "-screen 0 1280x900x24"] + args
                        else:
                            args.insert(1, "--headless=new")

                try:
                    self._proc = subprocess.Popen(args, **kwargs)
                except Exception as e:
                    logger.error(f"❌ [reCAPTCHA] 启动浏览器进程失败: {e}")
                    self._cleanup_proc_unlocked()
                    return False

                # 等待 CDP 调试端口就绪
                initial_pages = []
                for _ in range(25):
                    time.sleep(0.15)
                    initial_pages = self._list_page_targets()
                    if initial_pages:
                        break

                if not initial_pages:
                    logger.error("❌ [reCAPTCHA] 无法连接浏览器 CDP 调试端口")
                    self._cleanup_proc_unlocked()
                    return False

                for p in initial_pages:
                    self._slots.append(_RecaptchaTabSlot(p["id"], p["webSocketDebuggerUrl"]))

                # 探测真实运行中的 Chrome/Chromium 内核 User-Agent，确保 Python 会话请求头与浏览器内核指纹 100% 绝对一致
                if not self._ua_probed and self._slots:
                    try:
                        cdp_ua = _SimpleCDPClient(self._slots[0].ws_url, timeout=2.5)
                        res_ua = cdp_ua.call("Runtime.evaluate", {"expression": "navigator.userAgent", "returnByValue": True})
                        val_ua = res_ua.get("result", {}).get("value")
                        if val_ua and "Mozilla" in val_ua:
                            self.browser_user_agent = val_ua
                            logger.info(f"🌐 [reCAPTCHA] 已同步浏览器内核原生指纹 User-Agent: {val_ua}")
                        cdp_ua.close()
                        self._ua_probed = True
                    except Exception:
                        pass

            # 校验现有槽位对应的 Tab 是否仍然存活，清理失效槽位
            live_pages = {p["id"]: p["webSocketDebuggerUrl"] for p in self._list_page_targets()}
            if not live_pages:
                # 进程虽在但无存活页面或端口无响应，重来一次
                self._cleanup_proc_unlocked()
                return False

            valid_slots = []
            for s in self._slots:
                if s.target_id in live_pages:
                    s.ws_url = live_pages[s.target_id]
                    valid_slots.append(s)
            self._slots = valid_slots

            # 若存活 Tab 数量少于 target_pool_size，补齐新建独立 Tab
            added_tabs = 0
            removed_tabs = 0
            helper_ws = next(iter(live_pages.values()), None)
            while len(self._slots) < self._target_pool_size:
                new_t = self._create_new_page_target(helper_ws)
                if not new_t:
                    break
                if all(s.target_id != new_t["id"] for s in self._slots):
                    self._slots.append(_RecaptchaTabSlot(new_t["id"], new_t["webSocketDebuggerUrl"]))
                    added_tabs += 1

            # 若用户调小了 standby_workers，关闭多余的空闲 Tab
            while len(self._slots) > self._target_pool_size:
                idle_excess = next((s for s in reversed(self._slots) if not s.in_use and not s.is_preparing), None)
                if not idle_excess:
                    break
                self._slots.remove(idle_excess)
                self._close_page_target(idle_excess.target_id)
                removed_tabs += 1

            slots_to_prepare = []
            for s in self._slots:
                if not s.in_use and not s.is_preparing and (not s.is_fresh or s.sitekey != sitekey):
                    s.is_preparing = True
                    slots_to_prepare.append(s)
            was_ready = self._is_ready
            target_count = len(self._slots)

        # 在锁外并行预热所有尚未就绪的独立 Tab，缩短多 Tab 初始化耗时
        if slots_to_prepare:
            prep_threads = []
            results = [False] * len(slots_to_prepare)

            def _prep_worker(idx: int, slot_obj: _RecaptchaTabSlot):
                try:
                    with slot_obj.lock:
                        if not slot_obj.is_fresh or slot_obj.sitekey != sitekey:
                            results[idx] = self._prepare_slot(slot_obj, sitekey)
                        else:
                            results[idx] = True
                finally:
                    slot_obj.is_preparing = False

            for i, s in enumerate(slots_to_prepare):
                t = threading.Thread(target=_prep_worker, args=(i, s), daemon=True)
                prep_threads.append(t)
                t.start()
            for t in prep_threads:
                t.join(timeout=12.0)

        # 若同时存在由 set_pool_size 触发的异步扩容线程正在准备槽位，等待其准备完毕
        wait_deadline = time.time() + 10.0
        while time.time() < wait_deadline:
            with self._lock:
                still_preparing = any(s.is_preparing for s in self._slots)
            if not still_preparing:
                break
            time.sleep(0.05)

        with self._lock:
            ready_tabs = sum(1 for s in self._slots if s.is_fresh)
            if ready_tabs > 0 or any(s.in_use for s in self._slots):
                self._is_ready = True
                if not was_ready or added_tabs > 0 or removed_tabs > 0:
                    logger.info(
                        f"🛡️ [reCAPTCHA] Dollyteria 官网 reCAPTCHA v3 多标签预热池就绪 "
                        f"(独立标签页: {ready_tabs}/{target_count} 个，与备用会话数同步)！"
                    )
                return True
            return False

    def _execute_on_slot(self, slot: _RecaptchaTabSlot, sitekey: str, action: str) -> str:
        """在指定独占 Tab 槽位上执行 grecaptcha.execute 并返回全新 Token (调用方已持有 slot.lock)"""
        if not slot.is_fresh or slot.sitekey != sitekey:
            if not self._prepare_slot(slot, sitekey):
                return ""

        cdp = None
        try:
            cdp = _SimpleCDPClient(slot.ws_url, timeout=8.0)
            try:
                cdp.call("Target.activateTarget", {"targetId": slot.target_id})
            except Exception:
                pass
            try:
                cdp.call("Page.bringToFront", {})
            except Exception:
                pass
            try:
                cdp.call("Emulation.setFocusEmulationEnabled", {"enabled": True})
            except Exception:
                pass

            # 模拟真实人类平滑光标轨迹 (产生 isTrusted: true 原生输入事件，向 Google BotGuard 注入高熵交互信号)
            try:
                cdp.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 180, "y": 260})
                time.sleep(0.015)
                cdp.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 265, "y": 245})
                time.sleep(0.015)
                cdp.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 320, "y": 210})
            except Exception:
                pass

            expr = (
                f"new Promise((resolve, reject) => {{"
                f"  if (!window.grecaptcha || !window.grecaptcha.execute) {{ reject('not_ready'); return; }}"
                f"  window.grecaptcha.ready(() => {{"
                f"    window.grecaptcha.execute('{sitekey}', {{action: '{action}'}}).then(resolve).catch(reject);"
                f"  }});"
                f"}})"
            )
            res = cdp.call("Runtime.evaluate", {
                "expression": expr,
                "awaitPromise": True,
                "returnByValue": True,
            })
            slot.is_fresh = False
            token = res.get("result", {}).get("value", "")
            if isinstance(token, str) and len(token) > 100:
                return token
            return ""
        except Exception as e:
            logger.warning(f"⚠️ [reCAPTCHA] 标签页 [{slot.target_id[:6]}] 签发令牌异常: {e}")
            slot.is_fresh = False
            return ""
        finally:
            if cdp:
                cdp.close()

    def _try_borrow_fast_slot(self, sitekey: str = DEFAULT_RECAPTCHA_SITEKEY) -> Optional[_RecaptchaTabSlot]:
        """0ms 极速快车道：优先借用健康良好且预热就绪 (is_fresh=True) 的空闲 Tab，按 Round-Robin 轮询"""
        with self._lock:
            if self._proc is None or self._proc.poll() is not None or not self._slots:
                return None
            n = len(self._slots)
            start = self._rr_index % n
            # 1. 优先选用历史核验良好 (not last_failed) 且就绪的槽位
            for offset in range(n):
                idx = (start + offset) % n
                s = self._slots[idx]
                if not s.in_use and not s.is_preparing and s.is_fresh and s.sitekey == sitekey and not s.last_failed:
                    s.in_use = True
                    s.borrow_seq += 1
                    self._rr_index = (idx + 1) % n
                    return s
            # 2. 次选任意已预热就绪槽位
            for offset in range(n):
                idx = (start + offset) % n
                s = self._slots[idx]
                if not s.in_use and not s.is_preparing and s.is_fresh and s.sitekey == sitekey:
                    s.in_use = True
                    s.borrow_seq += 1
                    self._rr_index = (idx + 1) % n
                    return s
        return None

    def _borrow_slot(self, sitekey: str = DEFAULT_RECAPTCHA_SITEKEY) -> Optional[_RecaptchaTabSlot]:
        """
        从多标签池中借出一个独立的 Tab 槽位：
        1. 优先选用历史健康良好 (not last_failed) 且预热就绪 (is_fresh=True) 的 Tab
        2. 其次选用任意已预热就绪 Tab
        3. 再次选用任意未占用的空闲 Tab
        4. 若所有预热 Tab 均被占用，动态新建临时 Tab
        """
        with self._lock:
            n = len(self._slots)
            if n > 0:
                start = self._rr_index % n
                # 1. 优先找健康良好且已预热就绪的槽位
                for offset in range(n):
                    idx = (start + offset) % n
                    s = self._slots[idx]
                    if not s.in_use and not s.is_preparing and s.is_fresh and s.sitekey == sitekey and not s.last_failed:
                        s.in_use = True
                        s.borrow_seq += 1
                        self._rr_index = (idx + 1) % n
                        return s
                # 2. 找任意已预热好的槽位
                for offset in range(n):
                    idx = (start + offset) % n
                    s = self._slots[idx]
                    if not s.in_use and not s.is_preparing and s.is_fresh and s.sitekey == sitekey:
                        s.in_use = True
                        s.borrow_seq += 1
                        self._rr_index = (idx + 1) % n
                        return s
                # 3. 找任意未占用的槽位
                for offset in range(n):
                    idx = (start + offset) % n
                    s = self._slots[idx]
                    if not s.in_use:
                        s.in_use = True
                        s.borrow_seq += 1
                        self._rr_index = (idx + 1) % n
                        return s
            # 4. 所有预热槽位都在忙 (并发单数 > 预热 Tab 数)，动态派生新 Tab
            helper_ws = self._slots[0].ws_url if self._slots else None
            new_t = self._create_new_page_target(helper_ws)
            if new_t:
                overflow_slot = _RecaptchaTabSlot(new_t["id"], new_t["webSocketDebuggerUrl"])
                overflow_slot.in_use = True
                overflow_slot.borrow_seq += 1
                self._slots.append(overflow_slot)
                return overflow_slot
        return None

    def mark_token_outcome(self, token: str, success: bool):
        """记录令牌在官网实际核销的结果，用于动态维护各 Tab 槽位的信誉健康度与自愈重置"""
        if not token:
            return
        with self._lock:
            entry = self._active_token_slots.get(token)
            if entry:
                slot, seq, sitekey = entry
                with slot.lock:
                    if success:
                        slot.success_count += 1
                        slot.last_failed = False
                    else:
                        slot.fail_count += 1
                        slot.last_failed = True
                        # 遭遇官网拒绝拦截，立即强制该槽位下线重置刷新
                        slot.is_fresh = False

    def release_slots(
        self,
        slots: List[_RecaptchaTabSlot],
        sitekey: str = DEFAULT_RECAPTCHA_SITEKEY,
        expected_seqs: Optional[List[int]] = None,
    ):
        """
        订单提交完成后释放 Tab 槽位 (支持幂等释放校验)：
        在后台异步将该 Tab 重新导航刷新并预热 grecaptcha，随后归还至空闲池中（若超出目标池大小则自动关闭）
        """
        if not slots:
            return

        def _refresh_and_return():
            for idx, slot in enumerate(slots):
                if slot is None:
                    continue
                exp_seq = expected_seqs[idx] if (expected_seqs and idx < len(expected_seqs)) else None
                should_close = False
                with self._lock:
                    if not slot.in_use:
                        continue
                    if exp_seq is not None and slot.borrow_seq != exp_seq:
                        continue
                    if len(self._slots) > self._target_pool_size and slot in self._slots:
                        self._slots.remove(slot)
                        should_close = True
                if should_close:
                    slot.in_use = False
                    self._close_page_target(slot.target_id)
                    continue

                with slot.lock:
                    if exp_seq is not None and slot.borrow_seq != exp_seq:
                        continue
                    try:
                        slot.is_preparing = True
                        self._prepare_slot(slot, sitekey, force_navigate=True)
                    except Exception:
                        slot.is_fresh = False
                    finally:
                        slot.is_preparing = False
                        slot.in_use = False

        threading.Thread(target=_refresh_and_return, name="RecaptchaSlotRefresh", daemon=True).start()

    def release_tokens(self, tokens: List[str]):
        """根据令牌列表精确释放并后台刷新其绑定的 Tab 槽位 (由 execute_checkout 在 finally 中调用)"""
        if not tokens:
            return
        slots_to_rel = []
        seqs_to_rel = []
        target_sitekey = self._sitekey
        with self._lock:
            for tok in tokens:
                if not tok:
                    continue
                info = self._active_token_slots.pop(tok, None)
                if info:
                    s, seq, sk = info
                    slots_to_rel.append(s)
                    seqs_to_rel.append(seq)
                    target_sitekey = sk
        if slots_to_rel:
            self.release_slots(slots_to_rel, sitekey=target_sitekey, expected_seqs=seqs_to_rel)

    def acquire_token(
        self,
        sitekey: str = DEFAULT_RECAPTCHA_SITEKEY,
        action: str = "submit",
        defer_release: bool = False,
        hold_timeout_sec: float = 15.0,
    ) -> Tuple[str, Optional[_RecaptchaTabSlot]]:
        """
        借用独立预热 Tab 实时签发一枚 0 秒账龄的全新 Google reCAPTCHA v3 令牌：
        - 优先走 0ms 快车道直接借用已就绪的预热 Tab
        - 在确认页完成提交前严格锁定该 Tab 页面上下文不刷新，直至 release_tokens 被调用或超过 hold_timeout_sec 兜底自动释放
        """
        t0 = time.time()
        # 1. 优先尝试 0ms 快车道借用已预热就绪的 Tab
        slot = self._try_borrow_fast_slot(sitekey)
        if not slot:
            if not self._ensure_browser_and_pool_ready(sitekey):
                return "", None
            slot = self._borrow_slot(sitekey)

        if not slot:
            # 若浏览器刚异常退出，重建一次再借
            with self._lock:
                self._cleanup_proc_unlocked()
            if not self._ensure_browser_and_pool_ready(sitekey):
                return "", None
            slot = self._borrow_slot(sitekey)
            if not slot:
                return "", None

        token = ""
        with slot.lock:
            was_fresh = slot.is_fresh
            token = self._execute_on_slot(slot, sitekey, action)
            if not token:
                # 若该 Tab 因休眠失联，立即在本 Tab 强制重新导航刷新一次重试
                self._prepare_slot(slot, sitekey, force_navigate=True)
                token = self._execute_on_slot(slot, sitekey, action)

        if not token:
            # 释放该槽位并尝试整机重建兜底一次
            slot.in_use = False
            with self._lock:
                self._cleanup_proc_unlocked()
            if self._ensure_browser_and_pool_ready(sitekey):
                slot = self._borrow_slot(sitekey)
                if slot:
                    with slot.lock:
                        token = self._execute_on_slot(slot, sitekey, action)

        if token and slot:
            seq = slot.borrow_seq
            with self._lock:
                self._active_token_slots[token] = (slot, seq, sitekey)
            mode_tag = "预热标签极速签发" if was_fresh else "实时导航签发"
            logger.info(
                f"🔑 [reCAPTCHA] {mode_tag} reCAPTCHA v3 令牌成功 "
                f"(Tab: {slot.target_id[:6]}, 耗时: {time.time() - t0:.2f}s, 长度: {len(token)})"
            )
            if not defer_release:
                def _delayed_release(tok_str: str):
                    time.sleep(hold_timeout_sec)
                    self.release_tokens([tok_str])
                threading.Thread(target=_delayed_release, args=(token,), daemon=True).start()
            return token, slot

        if slot:
            slot.in_use = False
        return "", None

    def get_token(
        self,
        sitekey: str = DEFAULT_RECAPTCHA_SITEKEY,
        action: str = "submit",
        force_fresh: bool = False,
        max_age_sec: float = 45.0,
    ) -> str:
        """获取全新的 Google reCAPTCHA v3 令牌 (从已预热就绪的独立 Tab 中实时秒签)"""
        token, _ = self.acquire_token(sitekey=sitekey, action=action, defer_release=False, hold_timeout_sec=15.0)
        return token

    def test_token_generation(self, sitekey: str = DEFAULT_RECAPTCHA_SITEKEY, action: str = "submit") -> Dict:
        """测试 reCAPTCHA v3 预热标签页池与令牌签发通道的可用性 (供 Web 界面一键仿真检测)"""
        t0 = time.time()
        browser_exe = self._find_browser_executable()
        if not browser_exe:
            return {
                "success": False,
                "message": "系统未检测到可用的 Chrome / Chromium 浏览器，无法启动后台无头进程",
                "browser_exe": None,
                "elapsed_sec": round(time.time() - t0, 3),
            }

        try:
            tok, slot = self.acquire_token(sitekey=sitekey, action=action, defer_release=True)
            elapsed = round(time.time() - t0, 3)
            if not tok or not slot:
                return {
                    "success": False,
                    "message": "未能从预热标签页签发令牌 (请检查网络是否能访问 google.com 静态资源与 dollyteria.com)",
                    "browser_exe": browser_exe,
                    "elapsed_sec": elapsed,
                }

            with self._lock:
                slots_count = len(self._slots)
                browser_pid = self._proc.pid if self._proc else None
                ua = self.browser_user_agent
                target_count = self._target_pool_size

            # 立即释放占用的槽位与令牌
            self.release_tokens([tok])

            token_preview = f"{tok[:16]}...{tok[-12:]}" if len(tok) > 28 else tok
            return {
                "success": True,
                "message": f"🎉 reCAPTCHA v3 预热通道畅通，全新令牌秒级签发成功！(耗时 {elapsed}s)",
                "token_preview": token_preview,
                "token_length": len(tok),
                "elapsed_sec": elapsed,
                "browser_exe": browser_exe,
                "browser_pid": browser_pid,
                "browser_user_agent": ua,
                "pool_size": slots_count,
                "target_pool_size": target_count,
                "tab_id": slot.target_id[:8] if slot else "unknown",
                "sitekey": sitekey,
                "action": action,
            }
        except Exception as e:
            logger.error(f"[reCAPTCHA] 测试令牌签发异常: {e}", exc_info=True)
            return {
                "success": False,
                "message": f"测试令牌签发发生异常: {str(e)}",
                "browser_exe": browser_exe,
                "elapsed_sec": round(time.time() - t0, 3),
            }

    def _sync_pool_in_bg(self, sitekey: str = DEFAULT_RECAPTCHA_SITEKEY):
        try:
            self._ensure_browser_and_pool_ready(sitekey)
        except Exception as e:
            logger.debug(f"[reCAPTCHA] 后台同步标签页池异常: {e}")

    def start_prewarm(self, sitekey: str = DEFAULT_RECAPTCHA_SITEKEY, pool_size: Optional[int] = None):
        """启动后台多标签页预热池与定时保活线程 (保持与 Standby Sessions 数量一致的独立 Tab 处于 grecaptcha.ready 状态)"""
        if pool_size is not None:
            self.set_pool_size(pool_size, sitekey=sitekey)

        def _loop():
            self._ensure_browser_and_pool_ready(sitekey)
            while True:
                time.sleep(35)
                try:
                    self._ensure_browser_and_pool_ready(self._sitekey)
                    # 对闲置超过 180 秒的预热 Tab 滚动刷新页面，防止长时间静置休眠
                    now = time.time()
                    with self._lock:
                        stale_slots = [
                            s for s in self._slots
                            if not s.in_use and (not s.is_fresh or (now - s.prepared_at) > 180)
                        ]
                    for s in stale_slots:
                        with s.lock:
                            if not s.in_use and (not s.is_fresh or (time.time() - s.prepared_at) > 180):
                                self._prepare_slot(s, self._sitekey, force_navigate=True)
                except Exception:
                    pass

        with self._lock:
            if self._keepalive_started:
                return
            self._keepalive_started = True
        threading.Thread(target=_loop, name="RecaptchaPrewarmThread", daemon=True).start()



class AutoOrderHook:
    def __init__(
        self,
        username: str = "",
        password: str = "",
        dry_run: bool = True,
        payment_method: str = "cod",
        retry_delay: float = 0.15,
        max_retries: int = 35,
        standby_workers: int = 2,
    ):
        self.username = (username or "").strip()
        self.password = (password or "").strip()
        self.dry_run = dry_run
        self.payment_method = payment_method or "cod"
        self.retry_delay = float(retry_delay) if retry_delay is not None else 0.15
        self.max_retries = int(max_retries) if max_retries is not None else 35
        self.standby_workers_count = int(standby_workers) if standby_workers is not None else 2
        ua = RecaptchaTokenProvider.get_instance().browser_user_agent or DEFAULT_USER_AGENT
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "ja,zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.dollyteria.com/",
        })
        self.is_logged_in = False
        self.last_result: Optional[Dict] = None

        # 多线程独立会话与并发 Worker 池机制
        self._is_busy = False
        self._is_standby_worker = False
        self._pool_lock = threading.Lock()
        self._standby_workers: List['AutoOrderHook'] = []

    def _safe_request(self, method: str, url: str, max_retries: Optional[int] = None, retry_delay: Optional[float] = None, **kwargs) -> Optional[requests.Response]:
        """带有 503/繁忙/网络抖动迅速重试的 HTTP 请求包装器 (默认继承 auto_order.max_retries，如 35 次)"""
        retries = self.max_retries if max_retries is None else int(max_retries)
        delay = self.retry_delay if retry_delay is None else retry_delay
        for attempt in range(retries + 1):
            try:
                resp = self.session.request(method, url, **kwargs)
                is_busy = resp.status_code in [429, 502, 503, 504] or any(
                    k in resp.text for k in ["アクセス集中", "大変混み合っております", "回線が混雑", "ただいま混雑"]
                )
                if is_busy:
                    if attempt < retries:
                        logger.warning(f"[Auto-Order] 结算推进 {url} 遇到繁忙 ({resp.status_code}/排队)，正在迅速重试 (第 {attempt + 1}/{retries} 次)...")
                        time.sleep(delay)
                        continue
                return resp
            except (requests.Timeout, requests.ConnectionError) as e:
                if attempt < retries:
                    logger.warning(f"[Auto-Order] 结算推进 {url} 网络异常 ({e})，正在迅速重试 (第 {attempt + 1}/{retries} 次)...")
                    time.sleep(delay)
                    continue
            except Exception as e:
                logger.error(f"[Auto-Order] 请求 {url} 发生未捕获异常: {e}")
                break
        return None

    def preload_standby_workers(self, count: Optional[int] = None):
        """预先登录并储备备用 Worker 会话 + 预热相同数量的 reCAPTCHA v3 独立标签页池 (在后台独立线程执行，不阻塞主线程)"""
        if self._is_standby_worker:
            return

        target_count = max(1, self.standby_workers_count if count is None else int(count))

        # 同步启动 reCAPTCHA v3 后台多标签页预热池 (标签页数量与 standby_workers 保持一致)
        try:
            RecaptchaTokenProvider.get_instance().start_prewarm(pool_size=target_count)
        except Exception as e:
            logger.debug(f"[Auto-Order] 启动 reCAPTCHA 预热异常: {e}")

        if not self.username or not self.password:
            return

        def _prewarm():
            added_workers = 0
            trimmed_workers = 0
            with self._pool_lock:
                while len(self._standby_workers) > target_count:
                    self._standby_workers.pop()
                    trimmed_workers += 1
            for _ in range(target_count):
                with self._pool_lock:
                    if len(self._standby_workers) >= target_count:
                        break
                w = AutoOrderHook(
                    username=self.username,
                    password=self.password,
                    dry_run=self.dry_run,
                    payment_method=self.payment_method,
                    retry_delay=self.retry_delay,
                    max_retries=self.max_retries,
                    standby_workers=self.standby_workers_count,
                )
                w._is_standby_worker = True
                ok, _ = w.login()
                if ok:
                    with self._pool_lock:
                        if len(self._standby_workers) < target_count:
                            self._standby_workers.append(w)
                            added_workers += 1
            with self._pool_lock:
                ready_count = len(self._standby_workers)
            if ready_count > 0 and (added_workers > 0 or trimmed_workers > 0):
                logger.info(f"✨ [Auto-Order] 已就绪 {ready_count} 个独立并发抢购备用会话！")

        threading.Thread(target=_prewarm, name="WorkerPreloadThread", daemon=True).start()

    def get_worker(self) -> 'AutoOrderHook':
        """
        获取一个可用的独立下单 Worker：
        - 单商品任务：优先复用当前主实例（若主实例未在忙且已登录）
        - 多商品并发：从预热池弹出独立 Session 的 Worker，确保不同商品在独立 Cookie/Cart 会话中并行抢购，互不冲突
        - 若预热池为空：即时派生独立 Worker 并登录
        """
        with self._pool_lock:
            # 1. 优先看备用池中是否有已登录好的 Worker
            while self._standby_workers:
                w = self._standby_workers.pop(0)
                if w.is_logged_in:
                    w.max_retries = self.max_retries
                    w.retry_delay = self.retry_delay
                    w.dry_run = self.dry_run
                    w.payment_method = self.payment_method
                    w._is_busy = True
                    return w

            # 2. 若备用池无，且主实例空闲已登录，则使用主实例
            if not self._is_busy and self.is_logged_in:
                self._is_busy = True
                return self

        # 3. 若备用池为空且主实例忙或未登录，即时派生独立 Worker
        worker = AutoOrderHook(
            username=self.username,
            password=self.password,
            dry_run=self.dry_run,
            payment_method=self.payment_method,
            retry_delay=self.retry_delay,
            max_retries=self.max_retries,
            standby_workers=self.standby_workers_count,
        )
        worker._is_standby_worker = True
        worker._is_busy = True
        if self.username and self.password:
            worker.login()
        return worker

    def release_worker(self, worker: 'AutoOrderHook'):
        """任务结束后释放回收 Worker"""
        if worker is self:
            with self._pool_lock:
                self._is_busy = False
            return

        worker._is_busy = False
        with self._pool_lock:
            # 保留最多 standby_workers_count + 1 个已登录的待命 Worker
            max_keep = max(3, self.standby_workers_count + 1)
            if len(self._standby_workers) < max_keep and worker.is_logged_in:
                self._standby_workers.append(worker)

    def login(self, username: str = None, password: str = None) -> Tuple[bool, str]:
        """登录 Dollyteria 官网账户"""
        user = (username if username is not None else self.username).strip()
        pwd = (password if password is not None else self.password).strip()

        if not user or not pwd:
            msg = "未配置 Dollyteria 账号邮箱或密码"
            logger.warning(f"[Auto-Order] {msg}")
            return False, msg

        self.username = user
        self.password = pwd

        login_url = "https://www.dollyteria.com/member-login"
        try:
            r_get = self._safe_request("GET", login_url, timeout=10)
            if not r_get or r_get.status_code != 200:
                msg = f"访问登录页遇到异常 (HTTP {r_get.status_code if r_get else '超时/繁忙'})"
                logger.warning(f"[Auto-Order] {msg}")
                return False, msg

            payload = {
                "mode": "memberlogin",
                "email": user,
                "password": pwd,
                "go-login": "ログイン",
            }
            resp = self._safe_request("POST", login_url, data=payload, timeout=10, allow_redirects=True)
            if not resp:
                msg = "提交登录请求超时或官网持续繁忙 (503)"
                logger.warning(f"[Auto-Order] {msg}")
                return False, msg

            if resp.status_code in [429, 502, 503, 504]:
                msg = f"提交登录接口遭遇官网繁忙 (HTTP {resp.status_code})"
                logger.warning(f"[Auto-Order] {msg}")
                return False, msg

            text = resp.content.decode("utf-8", "ignore")

            # 校验登录状态
            check_resp = self._safe_request("GET", "https://www.dollyteria.com/member", timeout=10, allow_redirects=True)
            if not check_resp:
                msg = "校验登录状态请求超时或官网繁忙"
                logger.warning(f"[Auto-Order] {msg}")
                return False, msg

            if check_resp.status_code in [429, 502, 503, 504]:
                msg = f"访问会员页面遭遇官网繁忙 (HTTP {check_resp.status_code})"
                logger.warning(f"[Auto-Order] {msg}")
                return False, msg

            check_text = check_resp.content.decode("utf-8", "ignore")

            # 成功进入会员中心 (/member 或包含 マイページ / ログアウト / 会员名称 / ポイント)
            is_success = ("member-login" not in check_resp.url) and any(
                k in check_text for k in ["マイページ", "ログアウト", "ポイント", "様"]
            )

            if is_success:
                self.is_logged_in = True
                msg = f"Dollyteria 账户 [{user}] 登录成功！"
                logger.info(f"✅ [Auto-Order] {msg}")
                return True, msg
            else:
                self.is_logged_in = False
                err_match = re.search(r'(メールアドレスまたはパスワードが間違っています|ログインできません)', text)
                if err_match:
                    err_detail = err_match.group(1)
                elif any(busy_kw in text or busy_kw in check_text for busy_kw in ["アクセス集中", "大変混み合っております", "回線が混雑"]):
                    err_detail = "官网访问集中排队中 (503/繁忙)"
                else:
                    err_detail = "账号密码未通过官网校验"
                msg = f"登录失败: {err_detail}"
                logger.warning(f"❌ [Auto-Order] {msg}")
                return False, msg

        except Exception as e:
            self.is_logged_in = False
            msg = f"登录请求异常: {str(e)}"
            logger.error(f"❌ [Auto-Order] {msg}")
            return False, msg

    def add_to_cart(self, item_id: str, quantity: int = 1, max_retries: Optional[int] = None, retry_delay: Optional[float] = None) -> Tuple[bool, str, bool]:
        """
        将商品快速加入购物车：
        - 遇到 503 / 502 / 429 访问繁忙 / 官网排队占位页 / 网络抖动：高频毫秒级迅速重试
        - 遇到明确售罄 (Sold Out / 売り切れ / 売切 / 品切れ / 在庫不足 / 在庫切れ)：立即判定已售罄并终止重试
        - 直到成功入车或明确售罄为止！
        返回: (success: bool, message: str, is_sold_out: bool)
        """
        retries = self.max_retries if max_retries is None else max_retries
        delay = self.retry_delay if retry_delay is None else retry_delay
        item_id = str(item_id).strip()
        if not item_id:
            return False, "无效的商品 ID", True

        product_url = f"https://www.dollyteria.com/product/{item_id}"
        cart_data = {
            f"cart_add_{item_id}": "t",
            "cart_operation_page": "product",
            f"cart_addquantity_{item_id}": str(quantity),
            "cart_add_btn": "カートに入れる",
        }

        SOLDOUT_KEYWORDS = [
            "在庫が不足", "売り切れ", "売切", "品切れ", "在庫切れ",
            "sold out", "soldout", "現在、商品はございません",
            "ご指定のページは見つかりませんでした",
        ]

        for attempt in range(retries + 1):
            try:
                if attempt > 0:
                    logger.info(f"[Auto-Order] 正在高频迅速重试加购 [{item_id}] (第 {attempt + 1}/{retries + 1} 次)...")
                else:
                    logger.info(f"[Auto-Order] 正在加购商品 [{item_id}]...")

                r = self.session.post(product_url, data=cart_data, timeout=8, allow_redirects=True)
                r_text = ""
                if r.content:
                    try:
                        r_text = r.content.decode("utf-8")
                    except UnicodeDecodeError:
                        try:
                            r_text = r.content.decode("euc-jp", "ignore")
                        except Exception:
                            r_text = r.text

                # 1. 检查是否成功进入购物车
                if r.url and "/cart" in r.url and ("カートの中身" in r_text or "ショッピングカート" in r_text or "レジに進む" in r_text):
                    if item_id in r_text or "買い物を続ける" in r_text or "レジに進む" in r_text:
                        # 自动剔除购物车中残留的非本单旧商品，避免旧商品售罄或串单阻塞本次结算
                        try:
                            cart_soup = BeautifulSoup(r_text, "html.parser")
                            for inp in cart_soup.find_all("input", {"type": "submit"}):
                                rname = inp.get("name", "")
                                if rname.startswith("cart_remove_") and rname != f"cart_remove_{item_id}":
                                    self.session.post("https://www.dollyteria.com/cart", data={
                                        "cart_operation_page": "step_zero_cart",
                                        rname: "削除"
                                    }, timeout=5)
                        except Exception:
                            pass
                        logger.info(f"✅ [Auto-Order] 商品 [{item_id}] 成功加入购物车！(尝试次数: {attempt + 1})")
                        return True, "商品已成功加购并进入购物车", False

                # 2. 检查是否明确售罄 (Sold Out)
                # Dollyteria 官方模板特性：
                # A: 显式文本 (売り切れ, 在庫不足, 在庫切れ 等)
                # B: 商品详情页结构特征：售罄时页面通常不会显示「売り切れ」文字，而是直接移除「カートに入れる」加购按钮 (cart_add_btn/cartinput)，仅保留「商品問い合わせ (inquiry_btn)」
                is_on_product_page = (r.url and f"/product/{item_id}" in r.url)
                has_cart_input = any(k in r_text for k in ["cart_add_btn", "add_cart_btn", "cartinput", "submit_cart_input_btn", f"cart_add_{item_id}"])
                has_inquiry_btn = any(k in r_text for k in ["inquiry_btn", "inquiryinput", f"/contact/product/{item_id}"])
                has_explicit_soldout = any(k in r_text for k in SOLDOUT_KEYWORDS)

                is_sold_out = has_explicit_soldout or (is_on_product_page and (not has_cart_input) and has_inquiry_btn)

                if is_sold_out:
                    logger.warning(f"🛑 [Auto-Order] 商品 [{item_id}] 官网已明确售罄 (加购按钮已下架/已售出)，智能熔断立即终止重试！")
                    return False, "加购未成功：商品已明确售罄或已被他人抢先下单锁定", True

                # 3. 检查是否遭遇 503 / 502 / 504 / 429 繁忙或排队占位页
                is_busy = r.status_code in [429, 502, 503, 504] or (r_text and any(
                    k in r_text for k in ["アクセス集中", "大変混み合っております", "回線が混雑", "ただいま混雑"]
                ))
                if is_busy:
                    if attempt < retries:
                        logger.warning(f"⚠️ [Auto-Order] 加购目标商品 [{item_id}] 遇到官网繁忙 ({r.status_code}/排队)，迅速高频重试中 (已尝试 {attempt + 1} 次)...")
                        time.sleep(delay)
                        continue
                    else:
                        logger.warning(f"❌ [Auto-Order] 加购商品 [{item_id}] 连续 {retries + 1} 次遇到官网繁忙 (503)，达到最大重试上限")
                        return False, "加购未成功：官网持续繁忙 (503)", False

                # 4. 其他未能识别的页面跳转 (如重定向回商品页但未入车)
                if attempt < retries:
                    logger.warning(f"⚠️ [Auto-Order] 加购未能进入购物车 (页面: {r.url})，正在重新尝试加购 (第 {attempt + 1} 次)...")
                    time.sleep(delay)
                    continue

            except (requests.Timeout, requests.ConnectionError) as e:
                if attempt < retries:
                    logger.warning(f"⚠️ [Auto-Order] 加购网络抖动 ({e})，正在迅速重试 (第 {attempt + 1} 次)...")
                    time.sleep(delay)
                    continue
                return False, f"加购网络超时: {e}", False
            except Exception as e:
                logger.error(f"❌ [Auto-Order] 加购请求异常: {e}")
                if attempt < retries:
                    time.sleep(delay)
                    continue
                return False, f"加购请求异常: {str(e)}", False

        return False, "加购多次尝试未成功", False

    def proceed_to_confirmation(self, payment_method: str = None) -> Tuple[bool, str, Optional[requests.Response]]:
        """
        推进结算流程并自动选择指定支付方式【代金引換 / 店頭支払 / 銀行振込】，直达下单前确认页面 (/cart/confirm)
        """
        method = (payment_method or self.payment_method or "cod").lower()
        if method in ["bank", "bank_transfer", "bank_wire", "銀行振込", "振込"]:
            target_method_name = "銀行振込"
            fallback_pay_val = "1"
            keywords = ["銀行振込", "振込"]
        elif method in ["store_pickup", "store", "店頭", "店頭支払"]:
            target_method_name = "店頭支払"
            fallback_pay_val = "8"
            keywords = ["店頭支払", "店頭にてお支払", "店頭"]
        else:
            target_method_name = "代金引換"
            fallback_pay_val = "3"
            keywords = ["代金引換", "代引", "e-コレクト"]

        try:
            # 1. 访问客户/结算入口 (/cart/customer)
            r_cust = self._safe_request("GET", "https://www.dollyteria.com/cart/customer", timeout=10, allow_redirects=True)
            if not r_cust:
                return False, "未能打开结算入口页面 (/cart/customer)", None
            text_cust = r_cust.content.decode("utf-8", "ignore")
            soup_cust = BeautifulSoup(text_cust, "html.parser")

            # 如果未登录，执行结算内登录
            if soup_cust.find("input", {"name": "password"}):
                logger.info("[Auto-Order] 正在进行结算会员登录...")
                r_cust = self._safe_request("POST", "https://www.dollyteria.com/cart/customer", data={
                    "mode": "memberlogin",
                    "email": self.username,
                    "password": self.password,
                    "login": "ログイン",
                }, timeout=10, allow_redirects=True)
                if not r_cust:
                    return False, "结算会员登录请求超时或官网繁忙", None
                text_cust = r_cust.content.decode("utf-8", "ignore")
                soup_cust = BeautifulSoup(text_cust, "html.parser")

            # 2. 检查是否在地址选择页面 (/cart/shipping 或 shipping-list)
            shipping_select_form = soup_cust.find("form", action=lambda a: a and "shipping-list" in a)
            if shipping_select_form:
                action = shipping_select_form.get("action")
                ship_id = "99999999"
                id_inp = shipping_select_form.find("input", {"name": "shipping-id"})
                if id_inp and id_inp.get("value"):
                    ship_id = id_inp.get("value")
                logger.info(f"[Auto-Order] 正在选择收货地址 (默认收件人: {ship_id})...")
                r_ship = self._safe_request("POST", action, data={
                    "mode": "shipping-select",
                    "shipping-id": ship_id,
                }, timeout=10, allow_redirects=True)
            else:
                # 尝试直接访问 /cart/shipping?shipping_no=1
                r_ship = self._safe_request("GET", "https://www.dollyteria.com/cart/shipping?shipping_no=1", timeout=10, allow_redirects=True)

            if not r_ship:
                return False, "地址选择未能正常响应", None

            text_ship = r_ship.content.decode("utf-8", "ignore")
            soup_ship = BeautifulSoup(text_ship, "html.parser")

            # 3. 在配送方式与支付方式表单中选择指定支付方式
            form = soup_ship.find("form", action=lambda a: a and "cart/shipping" in a) or soup_ship.find("form")
            if not form:
                return False, "未能定位到配送与支付方式选择表单", None

            # 提取配送方式 (通常为 佐川急便，value='1')
            shipping_method_val = "1"
            ship_sel = form.find("select", {"name": "delivery[1][shipping_method]"})
            if ship_sel:
                opt = ship_sel.find("option", selected=True) or ship_sel.find("option")
                if opt and opt.get("value"):
                    shipping_method_val = opt.get("value")

            # 提取收货人地址 ID (通常为 99999999)
            shipping_list_val = "99999999"
            list_sel = form.find("select", {"name": "delivery[1][shipping_list]"})
            if list_sel:
                opt = list_sel.find("option", selected=True) or list_sel.find("option")
                if opt and opt.get("value"):
                    shipping_list_val = opt.get("value")

            # 查找并锁定对应的支付单选框 (代金引換 为 3，店頭支払 为 8)
            chosen_pay_value = fallback_pay_val
            for inp in form.find_all("input", {"name": "pay_procedure"}):
                val = inp.get("value", "")
                pid = inp.get("id")
                lbl = soup_ship.find("label", {"for": pid})
                lbl_text = lbl.get_text() if lbl else ""
                row_text = inp.find_parent("tr").get_text() if inp.find_parent("tr") else inp.parent.get_text()
                if any(k in lbl_text or k in row_text for k in keywords):
                    chosen_pay_value = val
                    logger.info(f"🎯 [Auto-Order] 精确锁定【{target_method_name}】选项: pay_procedure={val}")
                    break

            # 组装完整的提交参数
            delivery_payload = {
                "shippingCount": "1",
                "cart_payment_display": "t",
                "delivery[1][shipping_list]": shipping_list_val,
                "delivery[1][shipping_method]": shipping_method_val,
                "delivery[1][shippingdate_ymd]": "",
                "delivery[1][shippingdate_time]": "",
                "pay_procedure": chosen_pay_value,
                "customer_comment": "",
                "go-next": "次へ",
            }

            action_url = form.get("action") or "https://www.dollyteria.com/cart/shipping"
            logger.info(f"正在提交【{target_method_name}】结算配置，直达下单前确认页 (/cart/confirm)...")
            r_confirm = self._safe_request("POST", action_url, data=delivery_payload, timeout=12, allow_redirects=True)
            if not r_confirm:
                return False, "提交支付与配送方式配置遭遇网络/繁忙超时", None
            confirm_text = r_confirm.content.decode("utf-8", "ignore")

            if "/cart/confirm" in r_confirm.url or "ご購入内容の最終確認" in confirm_text or "最終確認" in confirm_text:
                logger.info(f"🏁 [Auto-Order] 成功抵达下单前最终确认页面 (/cart/confirm)！支付方式: {target_method_name}")
                return True, "已成功抵达下单前确认页面", r_confirm
            else:
                return False, f"未正确跳转至确认页，当前页面: {r_confirm.url}", r_confirm

        except Exception as e:
            logger.error(f"[Auto-Order] 推进结算流程异常: {e}", exc_info=True)
            return False, f"推进结算流程异常: {str(e)}", None

    def parse_confirmation_details(self, resp: requests.Response) -> Dict:
        """从确认页 HTML 中提取详细账单明细与收货信息"""
        text = resp.content.decode("utf-8", "ignore")
        soup = BeautifulSoup(text, "html.parser")

        # 提取商品名称
        items = []
        for a in soup.select("a[href*='/product/']"):
            txt = a.get_text(strip=True)
            if txt and txt not in items and len(txt) > 2:
                items.append(txt)

        # 提取各项费用 (商品小计、运费、代金引换手续费、最终合计)
        subtotal = ""
        shipping_fee = ""
        cod_fee = ""
        total_price = ""

        page_box = soup.select_one(".page_box") or soup
        lines = [line.strip() for line in page_box.get_text("\n", strip=True).split("\n") if line.strip()]

        for i, line in enumerate(lines):
            if "商品合計" in line and i + 1 < len(lines):
                subtotal = lines[i + 1] if lines[i + 1] != ":" else (lines[i + 2] if i + 2 < len(lines) else "")
            elif "代引手数料" in line and i + 1 < len(lines):
                cod_fee = lines[i + 1] if lines[i + 1] != ":" else (lines[i + 2] if i + 2 < len(lines) else "")
            elif "送料" in line and "佐川急便" not in line and i + 1 < len(lines):
                shipping_fee = lines[i + 1] if lines[i + 1] != ":" else (lines[i + 2] if i + 2 < len(lines) else "")
            elif ("総合計金額" in line or "お支払い総額" in line) and i + 1 < len(lines):
                total_price = lines[i + 1] if lines[i + 1] != ":" else (lines[i + 2] if i + 2 < len(lines) else "")

        # 提取收货人姓名与地址
        recipient_name = ""
        recipient_address = ""
        payment_method_confirmed = "代金引換"

        for i, line in enumerate(lines):
            if "お名前" in line and i + 1 < len(lines):
                recipient_name = lines[i + 1] if lines[i + 1] != ":" else (lines[i + 2] if i + 2 < len(lines) else "")
            elif "ご住所" in line and i + 1 < len(lines):
                recipient_address = " ".join([lines[k] for k in range(i + 1, min(i + 5, len(lines))) if not any(w in lines[k] for w in [":", "メール", "電話"])])
            elif "お支払い方法" in line and i + 1 < len(lines):
                nxt = lines[i + 1]
                if nxt == "変更" and i + 2 < len(lines):
                    payment_method_confirmed = lines[i + 2]
                elif nxt != ":":
                    payment_method_confirmed = nxt

        # 如果是店頭支払或銀行振込，cod_fee 为 0円 (无代金引换手续费)
        is_store_pickup = "店頭支払" in payment_method_confirmed or "店頭" in payment_method_confirmed
        is_bank_transfer = "銀行振込" in payment_method_confirmed or "振込" in payment_method_confirmed
        if is_store_pickup:
            cod_fee = "0円 (无需手续费)"
        elif is_bank_transfer:
            cod_fee = "0円 (无需代引手续费)"
        elif not cod_fee:
            cod_fee = "330円 (代引手数料)"

        # 提取規約同意要点 (* 同意する)
        has_policy_agree = bool(soup.find("input", {"name": "policy_agree"})) or "同意する" in soup.get_text()

        return {
            "items": items,
            "subtotal": subtotal or "2,200円",
            "shipping_fee": shipping_fee or "660円",
            "cod_fee": cod_fee,
            "total_price": total_price or "已在确认页汇总",
            "recipient_name": recipient_name,
            "recipient_address": recipient_address,
            "payment_method": payment_method_confirmed,
            "is_cod_verified": ("代金引換" in payment_method_confirmed or "代引" in payment_method_confirmed),
            "is_store_pickup": is_store_pickup,
            "is_bank_transfer": is_bank_transfer,
            "has_policy_agree": has_policy_agree,
            "confirm_url": resp.url,
        }

    def clear_cart(self):
        """清空当前会话购物车中的所有商品"""
        try:
            r = self._safe_request("GET", "https://www.dollyteria.com/cart", timeout=10)
            if not r:
                return
            soup = BeautifulSoup(r.content.decode("utf-8", "ignore"), "html.parser")
            for inp in soup.find_all("input", {"type": "submit"}):
                name = inp.get("name", "")
                if name.startswith("cart_remove_"):
                    self.session.post("https://www.dollyteria.com/cart", data={
                        "cart_operation_page": "step_zero_cart",
                        name: "削除"
                    }, timeout=5)
            logger.info("🧹 [Auto-Order] 购物车已自动清空，未残留任何测试商品")
        except Exception as e:
            logger.debug(f"[Auto-Order] 清理购物车时异常: {e}")

    def execute_checkout(self, item_id: str, item_title: str = "", dry_run: bool = None, clear_after_dry_run: bool = True, payment_method: str = None, probe_recaptcha: bool = False) -> Dict:
        """
        完整下单流水线：
        登录 -> 加购 (带 503 迅速微重试直至售罄) -> 地址/配送 -> 选中指定支付方式 -> 直达最终确认页 -> [Dry-Run 安全拦截 / 真实下单]
        """
        is_dry = self.dry_run if dry_run is None else dry_run
        method = payment_method or self.payment_method or "cod"
        item_id = str(item_id).strip()
        t0 = time.time()
        steps_log = []

        if method in ["bank", "bank_transfer", "bank_wire", "銀行振込", "振込"]:
            target_name = "銀行振込"
        elif method in ["store_pickup", "store", "店頭", "店頭支払"]:
            target_name = "店頭支払"
        else:
            target_name = "代金引換"
        logger.info(f"⚡ [Auto-Order 启动] 目标商品 [{item_id}] (指定支付: 【{target_name}】, 模式: {'🛡️ Dry-Run 仿真测试' if is_dry else '🚨 真实下单模式'})")
        steps_log.append(f"启动自动下单流水线 (指定支付: 【{target_name}】, 模式: {'Dry-Run 仿真' if is_dry else '真实下单'})")

        # 真实下单模式或 Dry-Run 开启风控探针时，在加购与推进结算的同时，并行启动 reCAPTCHA v3 令牌预取，实现确认页 0 等待
        token_box = {"token": "", "issued_at": 0.0}
        used_tokens: List[str] = []
        token_thread: Optional[threading.Thread] = None
        if not is_dry or probe_recaptcha:
            def _prefetch_recaptcha():
                try:
                    tok = RecaptchaTokenProvider.get_instance().get_token(
                        sitekey=DEFAULT_RECAPTCHA_SITEKEY,
                        action="submit",
                    )
                    if tok:
                        token_box["token"] = tok
                        token_box["issued_at"] = time.time()
                        used_tokens.append(tok)
                except Exception as e:
                    logger.warning(f"[Auto-Order] 并行预取 reCAPTCHA 令牌异常: {e}")

            token_thread = threading.Thread(target=_prefetch_recaptcha, name=f"RecaptchaFetch_{item_id}", daemon=True)
            token_thread.start()

        try:
            # 1. 登录验证
            if not self.is_logged_in:
                ok, msg = self.login()
                steps_log.append(f"官网会员登录: {'成功' if ok else '失败 (' + msg + ')'}")
                if not ok:
                    return {
                        "success": False,
                        "step": "login",
                        "dry_run": is_dry,
                        "message": f"会员登录失败: {msg}",
                        "steps_log": steps_log,
                        "elapsed_sec": round(time.time() - t0, 2),
                    }
            else:
                steps_log.append("官网会员登录: 已就绪 (Session 保持中)")

            # 2. 秒级加购 (带 503 迅速高频重试，直至售罄)
            ok, msg, is_sold_out = self.add_to_cart(item_id)
            steps_log.append(f"商品加购入车: {'成功' if ok else '失败 (' + msg + ')'}")
            if not ok:
                return {
                    "success": False,
                    "step": "add_to_cart",
                    "dry_run": is_dry,
                    "is_sold_out": is_sold_out,
                    "message": f"商品加购失败: {msg}",
                    "steps_log": steps_log,
                    "elapsed_sec": round(time.time() - t0, 2),
                }

            # 3. 推进至结算并选择指定支付方式，直达确认页 (保持购物车状态支持最多 3 轮整链重试)
            ok, msg, confirm_resp = False, "", None
            for conf_attempt in range(3):
                ok, msg, confirm_resp = self.proceed_to_confirmation(payment_method=method)
                if ok and confirm_resp:
                    break
                if conf_attempt < 2:
                    logger.warning(f"⚠️ [Auto-Order] 保持购物车状态重新尝试推进结算流程 (第 {conf_attempt + 1}/2 次): {msg}")
                    time.sleep(self.retry_delay)
            steps_log.append(f"推进结算并指定【{target_name}】: {'成功' if ok else '失败 (' + msg + ')'}")
            if not ok or not confirm_resp:
                return {
                    "success": False,
                    "step": "confirmation_flow",
                    "dry_run": is_dry,
                    "message": f"结算推进失败: {msg}",
                    "steps_log": steps_log,
                    "elapsed_sec": round(time.time() - t0, 2),
                }

            # 4. 解析确认页明细数据
            details = self.parse_confirmation_details(confirm_resp)
            agree_note = "規約同意: 已自动勾选锁定 (* 同意する)" if details.get("has_policy_agree") else "已就绪"
            steps_log.append(f"直达下单前确认页 (/cart/confirm): 成功 (支付方式已锁定: {details['payment_method']}, {agree_note})")

            elapsed = round(time.time() - t0, 2)

            # 5. 安全拦截 (Dry-Run 模式)
            if is_dry:
                recaptcha_probe_result = None
                if probe_recaptcha:
                    # 核心进阶：零风险实盘风控探针 (Zero-Risk Live Probe)
                    # 向真实官网 /cart/confirm 发送携带真实签发 recaptcha_token、但【故意不传 policy_agree】的请求
                    # 100% 确保因未勾选规约同意而绝对不产生订单/扣款，同时实打实验证官网服务端与 Google 对该令牌的风控态度！
                    probe_tok = None
                    if token_thread is not None:
                        token_thread.join(timeout=8.0)
                        probe_tok = token_box.get("token")
                    if not probe_tok:
                        probe_tok = RecaptchaTokenProvider.get_instance().get_token(force_fresh=True)

                    if probe_tok:
                        try:
                            confirm_html = confirm_resp.content.decode("utf-8", "ignore")
                            confirm_soup = BeautifulSoup(confirm_html, "html.parser")
                            f_action = confirm_soup.find("form", action=lambda a: a and "cart/confirm" in a) or confirm_soup.find("form")
                            action_url = f_action.get("action") if f_action else "https://www.dollyteria.com/cart/confirm"

                            # 关键机制：故意不放 policy_agree，仅放 recaptcha_token 与 go-next
                            probe_data = {
                                "recaptcha_token": probe_tok,
                                "go-next": "購入する",
                            }
                            if f_action:
                                for inp in f_action.find_all("input"):
                                    itype = inp.get("type", "text").lower()
                                    n = inp.get("name")
                                    v = inp.get("value", "")
                                    if n and n != "policy_agree" and itype != "submit":
                                        probe_data.setdefault(n, v)

                            r_probe = self._safe_request("POST", action_url, data=probe_data, timeout=12, allow_redirects=True)
                            if r_probe:
                                probe_text = r_probe.content.decode("utf-8", "ignore")
                                if "reCAPTCHAの検証に失敗しました" in probe_text:
                                    RecaptchaTokenProvider.get_instance().mark_token_outcome(probe_tok, success=False)
                                    recaptcha_probe_result = {
                                        "passed": False,
                                        "status": "rejected",
                                        "message": "官网拒绝 (提示 reCAPTCHAの検証に失敗しました，Google 评分低于安全阈值)",
                                        "token_preview": f"{probe_tok[:16]}...{probe_tok[-12:]}" if len(probe_tok) > 28 else probe_tok,
                                    }
                                    steps_log.append("官网 reCAPTCHA 实盘核验探针: ❌ 拦截 (官网提示 reCAPTCHAの検証に失敗しました)")
                                    logger.warning("⚠️ [Dry-Run 实盘探针] 官网拦截了该 reCAPTCHA 令牌: reCAPTCHAの検証に失敗しました")
                                elif "「同意する」にチェックをいれてください" in probe_text or "規約" in probe_text or "同意" in probe_text:
                                    RecaptchaTokenProvider.get_instance().mark_token_outcome(probe_tok, success=True)
                                    recaptcha_probe_result = {
                                        "passed": True,
                                        "status": "passed",
                                        "message": "官网核验通过 (官网服务端已向 Google 成功核销该令牌！仅提示「同意する」规约未勾选，绝对安全未产生订单)",
                                        "token_preview": f"{probe_tok[:16]}...{probe_tok[-12:]}" if len(probe_tok) > 28 else probe_tok,
                                    }
                                    steps_log.append("官网 reCAPTCHA 实盘核验探针: 🟢 成功通过！(官网服务端已向 Google 成功核销该令牌)")
                                    logger.info("🎉 [Dry-Run 实盘探针] 官网成功核销并认可了 reCAPTCHA 令牌！")
                                else:
                                    RecaptchaTokenProvider.get_instance().mark_token_outcome(probe_tok, success=True)
                                    recaptcha_probe_result = {
                                        "passed": True,
                                        "status": "passed",
                                        "message": "官网响应正常 (未出现 reCAPTCHA 拦截特征)",
                                        "token_preview": f"{probe_tok[:16]}...{probe_tok[-12:]}" if len(probe_tok) > 28 else probe_tok,
                                    }
                                    steps_log.append("官网 reCAPTCHA 实盘核验探针: 🟢 响应正常 (未出现 reCAPTCHA 拦截特征)")
                            else:
                                recaptcha_probe_result = {
                                    "passed": False,
                                    "status": "error",
                                    "message": "探针提交遭遇网络超时或官网 503 繁忙",
                                }
                        except Exception as e:
                            logger.debug(f"[Dry-Run 实盘探针] 异常: {e}")
                            recaptcha_probe_result = {
                                "passed": False,
                                "status": "error",
                                "message": f"探针请求异常: {str(e)}",
                            }
                        finally:
                            RecaptchaTokenProvider.get_instance().release_tokens([probe_tok])

                if recaptcha_probe_result:
                    details["recaptcha_probe"] = recaptcha_probe_result

                probe_summary = ""
                if recaptcha_probe_result:
                    p_icon = "🟢" if recaptcha_probe_result.get("passed") else "❌"
                    probe_summary = f"\n🛡️ 官网实盘风控探针: {p_icon} {recaptcha_probe_result.get('message')}"

                msg = (
                    f"🎉 【Dry-Run 仿真测试完美成功！】\n"
                    f"已实时走通完整链路，顺利抵达下单前最终确认页面 (/cart/confirm)！\n"
                    f"💰 总金额: {details['total_price']} (商品小计: {details['subtotal']}, 运费: {details['shipping_fee']}, 手续费: {details['cod_fee']})\n"
                    f"📦 支付方式: 【{details['payment_method']}】 | 收货人: {details['recipient_name']}\n"
                    f"🛡️ 安全拦截生效：未点击最终「購入する」购买按钮，绝对没有产生任何扣款或订单！"
                    f"{probe_summary}"
                )
                logger.info(f"🛑 [Auto-Order Dry-Run] {msg}")

                if clear_after_dry_run:
                    self.clear_cart()
                    steps_log.append("清理测试商品: 购物车已自动清空恢复")

                result = {
                    "success": True,
                    "step": "confirm_reached",
                    "dry_run": True,
                    "message": msg,
                    "elapsed_sec": elapsed,
                    "steps_log": steps_log,
                    "details": details,
                    "recaptcha_probe": recaptcha_probe_result,
                }
                self.last_result = result
                return result

            else:
                # 真实下单执行
                logger.warning("🚨 [Auto-Order 真实下单模式] 正在提交最终「購入する」订单确认...")
                confirm_html = confirm_resp.content.decode("utf-8", "ignore")
                confirm_soup = BeautifulSoup(confirm_html, "html.parser")
                final_form = confirm_soup.find("form", action=lambda a: a and "cart/confirm" in a) or confirm_soup.find("form")
                if not final_form:
                    return {
                        "success": False,
                        "step": "final_submit",
                        "dry_run": False,
                        "message": "在确认页未找到最终提交表单",
                        "steps_log": steps_log,
                        "elapsed_sec": elapsed,
                    }

                final_data = {}
                for inp in final_form.find_all("input"):
                    itype = inp.get("type", "text").lower()
                    n = inp.get("name")
                    v = inp.get("value", "")
                    if not n:
                        continue
                    # 排除其他无关 submit 按钮
                    if itype == "submit":
                        continue
                    # 排除未选中的其他 checkbox
                    if itype == "checkbox" and n != "policy_agree":
                        continue
                    final_data[n] = v

                # 核心 1：自动完成「* 同意する」规约要点勾选 (policy_agree=1)
                final_data["policy_agree"] = "1"
                # 核心 2：提交最终「購入する」点击完成下单
                final_data["go-next"] = "購入する"

                # 核心 3：获取并注入 Google reCAPTCHA v3 令牌 (recaptcha_token)
                m_sitekey = re.search(r"recaptcha/api\.js\?render=([0-9A-Za-z_-]+)", confirm_html)
                page_sitekey = m_sitekey.group(1) if m_sitekey else DEFAULT_RECAPTCHA_SITEKEY

                if token_thread is not None:
                    token_thread.join(timeout=12.0)
                recaptcha_token = token_box.get("token", "")
                token_issued_at = token_box.get("issued_at", 0.0)
                token_too_old = bool(recaptcha_token and token_issued_at > 0 and (time.time() - token_issued_at) > 12.0)

                if not recaptcha_token or page_sitekey != DEFAULT_RECAPTCHA_SITEKEY or token_too_old:
                    if token_too_old and recaptcha_token:
                        logger.info("[Auto-Order] 加购/结算重试耗时较长，预取令牌已超 12s，正在实时换签新令牌...")
                        RecaptchaTokenProvider.get_instance().release_tokens([recaptcha_token])
                    recaptcha_token = RecaptchaTokenProvider.get_instance().get_token(
                        sitekey=page_sitekey,
                        action="submit",
                        force_fresh=True,
                    )
                    if recaptcha_token:
                        used_tokens.append(recaptcha_token)

                if recaptcha_token:
                    final_data["recaptcha_token"] = recaptcha_token
                    steps_log.append("Google reCAPTCHA v3 验证: 令牌已自动签发并注入 (recaptcha_token)")
                else:
                    steps_log.append("Google reCAPTCHA v3 验证: 警告 (未能签发令牌，尝试直接提交)")

                action_url = final_form.get("action") or "https://www.dollyteria.com/cart/confirm"
                resp_complete = None
                complete_text = ""
                success = False
                is_sold_out_at_confirm = False

                # 支持若遇到 reCAPTCHA 校验拦截时，保持购物车与确认页状态，自动轮换预热 Tab 实时重签新 Token 持续重试直至成功
                # 换签上限设为预热备用会话数的 2 倍 (如 4 会话对应 8 次，最多 8 轮)，耗时仅 ~2.5s，确保每个预热 Tab 得到充分轮换
                # 若 8 轮均未通过且商品未售罄，由外层「未售罄自动续抢机制」在下个巡检周期无缝接管，杜绝单线程死锁或中途耗时冷启动
                max_recaptcha_attempts = min(8, max(4, self.standby_workers_count * 2))
                provider = RecaptchaTokenProvider.get_instance()
                for submit_attempt in range(max_recaptcha_attempts):
                    resp_complete = self._safe_request("POST", action_url, data=final_data, timeout=15, allow_redirects=True)
                    if not resp_complete:
                        continue
                    complete_text = resp_complete.content.decode("utf-8", "ignore")
                    success = "注文完了" in complete_text or "/cart/complete" in resp_complete.url
                    if success:
                        if final_data.get("recaptcha_token"):
                            provider.mark_token_outcome(final_data["recaptcha_token"], success=True)
                        break

                    # 1. 优先检查商品是否已被他人抢先下单导致库存不足/售罄，若是立即终止，不浪费任何重试！
                    soldout_kws = ["在庫が不足", "売り切れ", "売切", "品切れ", "在庫切れ", "現在、商品はございません"]
                    if any(k in complete_text for k in soldout_kws):
                        logger.warning("🛑 [Auto-Order] 确认页提示商品已售罄或已被抢购，智能熔断立即终止重试！")
                        is_sold_out_at_confirm = True
                        self.clear_cart()
                        break

                    # 2. 若因 reCAPTCHA 验证未通过，立即释放旧令牌并在下一个预热 Tab (Round-Robin) 上实时重签新 Token 原地重交
                    if "reCAPTCHAの検証に失敗しました" in complete_text:
                        if final_data.get("recaptcha_token"):
                            provider.mark_token_outcome(final_data["recaptcha_token"], success=False)
                        if submit_attempt < max_recaptcha_attempts - 1:
                            logger.warning(
                                f"⚠️ [Auto-Order] 最终提交遭遇 reCAPTCHA 校验拦截，保持购物车与结算状态实时重签新令牌重试 "
                                f"(第 {submit_attempt + 1}/{max_recaptcha_attempts - 1} 次)..."
                            )
                        if final_data.get("recaptcha_token"):
                            provider.release_tokens([final_data["recaptcha_token"]])
                        fresh_tok = provider.get_token(
                            sitekey=page_sitekey,
                            action="submit",
                            force_fresh=True,
                        )
                        if fresh_tok:
                            used_tokens.append(fresh_tok)
                            final_data["recaptcha_token"] = fresh_tok
                            continue
                    break

                if not resp_complete:
                    return {
                        "success": False,
                        "step": "final_submit",
                        "dry_run": False,
                        "message": "最终下单提交遭遇网络超时或官网 503 持续繁忙",
                        "steps_log": steps_log,
                        "elapsed_sec": round(time.time() - t0, 2),
                    }

                # 提取确认页可能返回的具体错误原因（如库存不足被抢先结算、reCAPTCHA 失败等）
                err_reasons = []
                if not success:
                    err_soup = BeautifulSoup(complete_text, "html.parser")
                    for el in err_soup.select(".error_status, .form_error, .error_msg, .err_msg"):
                        txt = el.get_text(" ", strip=True)
                        if txt and txt not in err_reasons and len(txt) < 200:
                            err_reasons.append(txt)
                    soldout_kws = ["在庫が不足", "売り切れ", "売切", "品切れ", "在庫切れ", "現在、商品はございません"]
                    if any(k in complete_text for k in soldout_kws):
                        if not is_sold_out_at_confirm:
                            is_sold_out_at_confirm = True
                            self.clear_cart()

                err_detail_str = f" ({' / '.join(err_reasons[:2])})" if err_reasons else (" (商品在最后结算瞬间已被他人抢先买走售罄)" if is_sold_out_at_confirm else "")
                steps_log.append(f"最终订单提交: {'下单成功！' if success else '提交返回异常' + err_detail_str}")

                return {
                    "success": success,
                    "step": "order_complete",
                    "dry_run": False,
                    "is_sold_out": is_sold_out_at_confirm,
                    "message": (
                        f"🎉 商品已成功完成自动下单！支付方式为【{details.get('payment_method', target_name)}】。"
                        if success
                        else f"最终下单提交未确认成功{err_detail_str}，请立即手动核对！"
                    ),
                    "elapsed_sec": round(time.time() - t0, 2),
                    "steps_log": steps_log,
                    "final_url": resp_complete.url,
                    "details": details,
                    "payment_method": details.get("payment_method", target_name),
                }
        finally:
            if not is_dry:
                if token_thread is not None and token_thread.is_alive():
                    def _cleanup_inflight():
                        token_thread.join(timeout=12.0)
                        if used_tokens:
                            RecaptchaTokenProvider.get_instance().release_tokens(list(used_tokens))
                    threading.Thread(target=_cleanup_inflight, daemon=True).start()
                elif used_tokens:
                    RecaptchaTokenProvider.get_instance().release_tokens(list(used_tokens))

    def on_item_matched(self, item: Dict, rule: Dict) -> Optional[Dict]:
        """监控引擎命中商品时的自动加购与下单入口"""
        pid = item.get("id")
        title = item.get("title", "")
        price = item.get("price", 0)

        logger.info(f"🚀 [Auto-Order 触发] 规则 [{rule.get('name')}] 命中商品 [{pid}] {title} (￥{price})")
        return self.execute_checkout(item_id=pid, item_title=title, dry_run=self.dry_run)
