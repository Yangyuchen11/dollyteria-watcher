/**
 * Dollyteria 上新提醒助手 - 前端核心交互逻辑
 */

// 全局状态
let currentConfig = null;
let currentHistory = [];
let currentDropTimes = [];
let audioCtx = null;

// DOM 元素引用
const elements = {
    // 状态与指示器
    statusIndicator: document.getElementById('status-indicator'),
    statusText: document.getElementById('status-text'),
    orderModeIndicator: document.getElementById('order-mode-indicator'),
    orderModeText: document.getElementById('order-mode-text'),
    nextDropLabel: document.getElementById('next-drop-label'),
    nextDropCountdown: document.getElementById('next-drop-countdown'),
    statActiveWatches: document.getElementById('stat-active-watches'),
    statSeenCount: document.getElementById('stat-seen-count'),
    statTodayChecks: document.getElementById('stat-today-checks'),
    statTodayChecksMeta: document.getElementById('stat-today-checks-meta'),
    feedBadge: document.getElementById('feed-badge'),

    // 快捷操作按钮
    btnBurst: document.getElementById('btn-burst'),
    btnCheckNow: document.getElementById('btn-check-now'),
    btnTestSound: document.getElementById('btn-test-sound'),
    btnTestDesktop: document.getElementById('btn-test-desktop'),
    linkInitBaseline: document.getElementById('link-init-baseline'),
    linkResetSeen: document.getElementById('link-reset-seen'),

    // 监控规则
    watchesContainer: document.getElementById('watches-container'),
    btnAddWatch: document.getElementById('btn-add-watch'),

    // 调度设置
    dropTimesChips: document.getElementById('drop-times-chips'),
    dropTimesCountBadge: document.getElementById('drop-times-count-badge'),
    dropTimePreset: document.getElementById('drop-time-preset'),
    dropTimeHour: document.getElementById('drop-time-hour'),
    dropTimeMinute: document.getElementById('drop-time-minute'),
    dropTimeSecond: document.getElementById('drop-time-second'),
    btnAddDropTime: document.getElementById('btn-add-drop-time'),
    schedBurstDuration: document.getElementById('sched-burst-duration'),
    schedBurstInterval: document.getElementById('sched-burst-interval'),
    schedMaxPages: document.getElementById('sched-max-pages'),
    schedIntervalEnabled: document.getElementById('sched-interval-enabled'),
    schedIntervalMinutes: document.getElementById('sched-interval-minutes'),
    schedDailySyncEnabled: document.getElementById('sched-daily-sync-enabled'),
    schedDailySyncTime: document.getElementById('sched-daily-sync-time'),
    btnSaveSchedule: document.getElementById('btn-save-schedule'),

    // 通知设置
    notifSoundEnabled: document.getElementById('notif-sound-enabled'),
    notifDesktopEnabled: document.getElementById('notif-desktop-enabled'),
    notifDiscordEnabled: document.getElementById('notif-discord-enabled'),
    notifDiscordWebhook: document.getElementById('notif-discord-webhook'),
    notifEmailEnabled: document.getElementById('notif-email-enabled'),
    notifEmailHost: document.getElementById('notif-email-host'),
    notifEmailPort: document.getElementById('notif-email-port'),
    notifEmailUser: document.getElementById('notif-email-user'),
    notifEmailPwd: document.getElementById('notif-email-pwd'),
    notifEmailReceiver: document.getElementById('notif-email-receiver'),
    btnSaveNotifiers: document.getElementById('btn-save-notifiers'),
    btnChanTestSound: document.getElementById('btn-chan-test-sound'),
    btnChanTestDesktop: document.getElementById('btn-chan-test-desktop'),
    btnChanTestDiscord: document.getElementById('btn-chan-test-discord'),
    btnChanTestEmail: document.getElementById('btn-chan-test-email'),

    // 自动下单
    aoUsername: document.getElementById('ao-username'),
    aoPassword: document.getElementById('ao-password'),
    aoDryRun: document.getElementById('ao-dry-run'),
    aoEnabled: document.getElementById('ao-enabled'),
    aoEnabledLabel: document.getElementById('ao-enabled-label'),
    btnSaveAutoOrder: document.getElementById('btn-save-auto-order'),
    aoTestItemId: document.getElementById('ao-test-item-id'),
    aoTestPaySelect: document.getElementById('ao-test-pay-select'),
    btnTestDryRunOrder: document.getElementById('btn-test-dry-run-order'),
    btnTestRecaptchaToken: document.getElementById('btn-test-recaptcha-token'),
    aoTestResult: document.getElementById('ao-test-result'),
    aoModeBanner: document.getElementById('ao-mode-banner'),
    aoModeIcon: document.getElementById('ao-mode-icon'),
    aoModeTitle: document.getElementById('ao-mode-title'),
    aoModeDesc: document.getElementById('ao-mode-desc'),
    aoMaxWorkers: document.getElementById('ao-max-workers'),
    aoStandbyWorkers: document.getElementById('ao-standby-workers'),
    aoRetryDelay: document.getElementById('ao-retry-delay'),
    aoMaxRetries: document.getElementById('ao-max-retries'),

    // 档案库一键下单 (独立开关与仿真控制)
    quickBuyEnabled: document.getElementById('quick-buy-enabled'),
    quickBuyDryRun: document.getElementById('quick-buy-dry-run'),
    quickBuyModeTag: document.getElementById('quick-buy-mode-tag'),

    // 实时流
    feedContainer: document.getElementById('feed-container'),
    btnClearFeed: document.getElementById('btn-clear-feed'),

    // 规则 Modal
    ruleModal: document.getElementById('rule-modal'),
    modalTitle: document.getElementById('modal-title'),
    modalClose: document.getElementById('modal-close'),
    modalBtnCancel: document.getElementById('modal-btn-cancel'),
    modalBtnSave: document.getElementById('modal-btn-save'),
    modalRuleId: document.getElementById('modal-rule-id'),
    modalRuleName: document.getElementById('modal-rule-name'),
    modalRuleKeywords: document.getElementById('modal-rule-keywords'),
    modalRuleExcludes: document.getElementById('modal-rule-excludes'),
    modalRuleMinPrice: document.getElementById('modal-rule-min-price'),
    modalRuleMaxPrice: document.getElementById('modal-rule-max-price'),
    modalRuleSearchMode: document.getElementById('modal-rule-search-mode'),

    // Toast
    toastContainer: document.getElementById('toast-container'),

    // 鉴权与安全
    btnLockScreen: document.getElementById('btn-lock-screen'),
    btnQuickLock: document.getElementById('btn-quick-lock'),
    authLockOverlay: document.getElementById('auth-lock-overlay'),
    authForm: document.getElementById('auth-form'),
    authPasswordInput: document.getElementById('auth-password-input'),
    btnAuthTogglePwd: document.getElementById('btn-auth-toggle-pwd'),
    authErrorMsg: document.getElementById('auth-error-msg'),
    btnAuthLogin: document.getElementById('btn-auth-login'),
    secStatusBanner: document.getElementById('security-status-banner'),
    secBannerIcon: document.getElementById('sec-banner-icon'),
    secBannerTitle: document.getElementById('sec-banner-title'),
    secBannerDesc: document.getElementById('sec-banner-desc'),
    inputNewWebPassword: document.getElementById('input-new-web-password'),
    btnToggleNewPwd: document.getElementById('btn-toggle-new-pwd'),
    btnSaveWebPassword: document.getElementById('btn-save-web-password'),
};

// 全局运行与鉴权状态标志
let isAppInitialized = false;
let sseSource = null;
let pollTimer = null;

// 格式化倒计时
function formatSeconds(sec) {
    if (sec < 0) return '未启用';
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = sec % 60;
    return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

// 检查鉴权状态
async function checkAuthStatus() {
    try {
        const res = await fetch('/api/auth/status');
        const data = await res.json();
        updateSecurityTabUI(data.auth_required);
        if (data.auth_required && !data.authenticated) {
            showLockScreen(false);
            return false;
        } else {
            hideLockScreen();
            return true;
        }
    } catch (e) {
        console.warn('检查鉴权异常:', e);
        showLockScreen(false);
        return false;
    }
}

function showLockScreen(clearInput = false) {
    if (elements.authLockOverlay) {
        const wasHidden = elements.authLockOverlay.classList.contains('hidden');
        elements.authLockOverlay.classList.remove('hidden');
        if (clearInput && elements.authPasswordInput) {
            elements.authPasswordInput.value = '';
        }
        if (wasHidden && elements.authPasswordInput) {
            setTimeout(() => elements.authPasswordInput.focus(), 100);
        }
    }
}

function hideLockScreen() {
    if (elements.authLockOverlay) {
        elements.authLockOverlay.classList.add('hidden');
    }
    hideAuthError();
}

function showAuthError(msg) {
    if (elements.authErrorMsg) {
        elements.authErrorMsg.textContent = msg;
        elements.authErrorMsg.classList.remove('hidden');
    }
}

function hideAuthError() {
    if (elements.authErrorMsg) {
        elements.authErrorMsg.classList.add('hidden');
    }
}

function shakeLockCard() {
    const card = document.querySelector('.auth-card');
    if (card) {
        card.classList.remove('shake');
        void card.offsetWidth; // 触发重绘
        card.classList.add('shake');
    }
}

function updateSecurityTabUI(authRequired) {
    if (!elements.secStatusBanner) return;
    if (authRequired) {
        elements.secStatusBanner.className = 'security-status-banner protected';
        if (elements.secBannerIcon) elements.secBannerIcon.textContent = '🛡️';
        if (elements.secBannerTitle) elements.secBannerTitle.textContent = '访问保护状态: 已开启安全保护';
        if (elements.secBannerDesc) elements.secBannerDesc.textContent = '当前已设置 Web 访问密码，未授权的外部网络请求均已被阻断拦截。';
    } else {
        elements.secStatusBanner.className = 'security-status-banner unprotected';
        if (elements.secBannerIcon) elements.secBannerIcon.textContent = '⚠️';
        if (elements.secBannerTitle) elements.secBannerTitle.textContent = '访问保护状态: 未设置密码 (开放模式)';
        if (elements.secBannerDesc) elements.secBannerDesc.textContent = '当前未设置访问密码。若要放置在腾讯云轻量服务器公网运行，强烈建议在此设置访问密码！';
    }
}

async function doLogin() {
    const pwd = (elements.authPasswordInput ? elements.authPasswordInput.value : '').trim();
    if (!pwd) {
        showAuthError('请输入访问密码');
        shakeLockCard();
        return;
    }
    try {
        if (elements.btnAuthLogin) elements.btnAuthLogin.disabled = true;
        const res = await fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password: pwd })
        });
        const data = await res.json();
        if (res.ok && data.status === 'ok') {
            hideAuthError();
            hideLockScreen();
            showToast('解锁成功，欢迎使用！', 'success');
            if (!isAppInitialized) {
                initMainApp();
            } else {
                loadConfig();
                loadHistory();
            }
        } else {
            showAuthError(data.message || '访问密码错误');
            shakeLockCard();
        }
    } catch (e) {
        showAuthError('连接服务器失败: ' + e.message);
        shakeLockCard();
    } finally {
        if (elements.btnAuthLogin) elements.btnAuthLogin.disabled = false;
    }
}

async function doLogout() {
    if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
    }
    if (sseSource) {
        try { sseSource.close(); } catch (e) { }
        sseSource = null;
    }
    isAppInitialized = false;
    try {
        await fetch('/api/auth/logout', { method: 'POST' });
    } catch (e) { }
    showToast('已锁定当前控制台', 'info');
    showLockScreen(true);
}

async function saveWebPassword() {
    const newPwd = (elements.inputNewWebPassword ? elements.inputNewWebPassword.value : '').trim();
    try {
        const res = await fetch('/api/auth/set_password', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password: newPwd })
        });
        const data = await res.json();
        if (res.ok && data.status === 'ok') {
            showToast(data.message, 'success');
            updateSecurityTabUI(data.auth_required);
            if (elements.inputNewWebPassword) elements.inputNewWebPassword.value = '';
        } else {
            showToast(data.message || '保存密码失败', 'error');
        }
    } catch (e) {
        showToast('保存密码异常: ' + e.message, 'error');
    }
}

// 播放网页端清脆提示音 (Web Audio API 合成)
function playWebChime() {
    try {
        if (!audioCtx) {
            audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        }
        if (audioCtx.state === 'suspended') {
            audioCtx.resume();
        }
        const now = audioCtx.currentTime;
        // 连续弹奏双和弦 (G5 -> C6)
        const notes = [783.99, 1046.50];
        notes.forEach((freq, idx) => {
            const osc = audioCtx.createOscillator();
            const gain = audioCtx.createGain();
            osc.type = 'sine';
            osc.frequency.setValueAtTime(freq, now + idx * 0.12);
            gain.gain.setValueAtTime(0, now + idx * 0.12);
            gain.gain.linearRampToValueAtTime(0.3, now + idx * 0.12 + 0.02);
            gain.gain.exponentialRampToValueAtTime(0.001, now + idx * 0.12 + 0.4);
            osc.connect(gain);
            gain.connect(audioCtx.destination);
            osc.start(now + idx * 0.12);
            osc.stop(now + idx * 0.12 + 0.45);
        });
    } catch (e) {
        console.warn('播放声音异常:', e);
    }
}

// 页面消息弹窗 (Toast)
function showToast(msg, type = 'info') {
    const el = document.createElement('div');
    el.className = 'app-toast';
    const icon = type === 'success' ? '✅' : type === 'error' ? '❌' : 'ℹ️';
    el.innerHTML = `<span>${icon}</span><span>${msg}</span>`;
    elements.toastContainer.appendChild(el);
    setTimeout(() => {
        el.style.opacity = '0';
        el.style.transform = 'translateY(8px)';
        el.style.transition = 'all 0.3s';
        setTimeout(() => el.remove(), 300);
    }, 3200);
}

// 初始化选项卡切换
function initTabs() {
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const tabId = btn.getAttribute('data-tab');
            if (!tabId) return;
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
            btn.classList.add('active');
            const targetPane = document.getElementById(tabId);
            if (targetPane) targetPane.classList.add('active');
            if (tabId === 'tab-feed') {
                if (elements.feedBadge) {
                    elements.feedBadge.style.display = 'none';
                    elements.feedBadge.textContent = '0';
                }
            }
            if (tabId === 'tab-orders') {
                const ordersBadge = document.getElementById('orders-badge');
                if (ordersBadge) {
                    ordersBadge.style.display = 'none';
                    ordersBadge.textContent = '0';
                }
                loadOrders();
                loadOrderStats();
            }
            if (tabId === 'tab-terminal') {
                loadTerminalLogs();
            }
            if (tabId === 'tab-archive') {
                onArchiveTabActive();
            }
            if (tabId === 'tab-schedule') {
                pollArchiveCrawlerStatus();
            }
        });
    });
}

// 初始化核心主应用 (在鉴权通过后触发)
function initMainApp() {
    if (isAppInitialized) return;
    isAppInitialized = true;
    try { loadConfig(); } catch (e) { console.error('loadConfig err:', e); }
    try { loadHistory(); } catch (e) { console.error('loadHistory err:', e); }
    try { initArchiveModule(); } catch (e) { console.error('initArchiveModule err:', e); }
    try { pollArchiveCrawlerStatus(); } catch (e) { console.error('pollArchiveCrawlerStatus err:', e); }
    try { initTerminalModule(); } catch (e) { console.error('initTerminalModule err:', e); }
    try { initOrdersModule(); } catch (e) { console.error('initOrdersModule err:', e); }
    try { initWatchesFilter(); } catch (e) { console.error('initWatchesFilter err:', e); }
    try { initFeedFilter(); } catch (e) { console.error('initFeedFilter err:', e); }
    try { initSSE(); } catch (e) { console.error('initSSE err:', e); }
    try {
        pollStatus();
        if (pollTimer) clearInterval(pollTimer);
        pollTimer = setInterval(pollStatus, 1000);
    } catch (e) { console.error('pollStatus err:', e); }
}

// 加载配置
async function loadConfig() {
    try {
        const res = await fetch('/api/config');
        if (res.status === 401) {
            showLockScreen();
            return;
        }
        currentConfig = await res.json();
        renderWatches();
        populateScheduleForm();
        populateNotifiersForm();
        populateAutoOrderForm();
        populateQuickBuyForm();
        updateTopOrderModeIndicator();
    } catch (e) {
        showToast('读取配置失败: ' + e.message, 'error');
    }
}

// 保存配置
async function saveConfig(silent = false) {
    try {
        const res = await fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(currentConfig)
        });
        if (res.status === 401) {
            showLockScreen();
            return;
        }
        const data = await res.json();
        if (!silent) showToast(data.message || '配置已保存', 'success');
        renderWatches();
        updateTopOrderModeIndicator();
    } catch (e) {
        showToast('保存配置异常: ' + e.message, 'error');
    }
}

// 定时状态轮询 (每秒一次)
async function pollStatus() {
    try {
        const res = await fetch('/api/status');
        if (res.status === 401) {
            if (pollTimer) {
                clearInterval(pollTimer);
                pollTimer = null;
            }
            showLockScreen(false);
            return;
        }
        const data = await res.json();

        // 调度器状态
        const sched = data.scheduler || {};
        if (sched.is_bursting) {
            elements.statusIndicator.className = 'status-pill status-bursting';
            elements.statusText.textContent = `⚡ 抢购爆发模式中 (${sched.burst_remaining_sec}s)`;
        } else if (sched.is_checking) {
            elements.statusIndicator.className = 'status-pill status-busy';
            elements.statusText.textContent = '🔍 正在抓取检测...';
        } else {
            elements.statusIndicator.className = 'status-pill status-idle';
            elements.statusText.textContent = '监控运行中';
        }

        // 下次上新时刻
        if (sched.next_drop_time && sched.seconds_to_next_drop >= 0) {
            elements.nextDropLabel.textContent = sched.next_drop_time;
            elements.nextDropCountdown.textContent = `倒计时: ${formatSeconds(sched.seconds_to_next_drop)}`;
        } else {
            elements.nextDropLabel.textContent = '--:--:--';
            elements.nextDropCountdown.textContent = '未启用定点触发';
        }

        // 看板统计
        elements.statActiveWatches.textContent = data.active_watches_count ?? 0;
        elements.statSeenCount.textContent = data.seen_count ?? 0;
        if (elements.statTodayChecks) {
            elements.statTodayChecks.textContent = data.stats?.today_checks ?? 0;
        }
        if (elements.statTodayChecksMeta) {
            const total = data.stats?.total_checks;
            elements.statTodayChecksMeta.textContent = (total !== undefined)
                ? `每日 00:00 清零 (累计: ${total})`
                : '每日 00:00 自动清零';
        }

    } catch (e) {
        // 忽略轮询网络偶发错误
    }
}

// 渲染监控规则卡片
function renderWatches() {
    if (!currentConfig || !currentConfig.watches) return;
    elements.watchesContainer.innerHTML = '';

    currentConfig.watches.forEach((rule, idx) => {
        const card = document.createElement('div');

        const matchMode = rule.match_mode || 'all';
        const connector = matchMode === 'all'
            ? '<span class="tag-connector" title="必须同时包含">+</span >'
            : '<span class="tag-connector" title="或者">|</span>';
        const matchBadge = matchMode === 'all'
            ? `<button type="button" class="tag-match-mode btn-toggle-mode and-badge" data-idx="${idx}" title="点击可直接切换 AND / OR 模式">AND 全部满足 ⇋</button>`
            : `<button type="button" class="tag-match-mode btn-toggle-mode or-badge" data-idx="${idx}" title="点击可直接切换 AND / OR 模式">OR 任一即可 ⇋</button>`;

        const kws = (rule.keywords || []).map(k => `<span class="tag-kw">${escapeHtml(k)}</span>`).join(connector);
        const excludes = (rule.exclude_keywords || []).map(k => `<span class="tag-exclude">🚫 ${escapeHtml(k)}</span>`).join('');

        let priceTag = '';
        if (rule.min_price || rule.max_price) {
            const minStr = rule.min_price ? `￥${rule.min_price.toLocaleString()}` : '0';
            const maxStr = rule.max_price ? `￥${rule.max_price.toLocaleString()}` : '不限';
            priceTag = `<span class="tag-price">💰 ${minStr} ~ ${maxStr}</span>`;
        }

        const modeMap = {
            'both': '上新流 + 全店搜索',
            'new_stream': '仅上新流 (/new)',
            'keyword_search': '仅关键词搜索'
        };

        const autoOrderEnabled = Boolean(rule.auto_order);
        const globalAoEnabled = Boolean(currentConfig.auto_order?.enabled);

        let modeCardClass = 'mode-notify';
        let statusBadgeHtml = '';

        if (!rule.enabled) {
            modeCardClass = 'mode-paused';
            statusBadgeHtml = '<span class="rule-status-badge status-paused" title="规则已停用，不参与检测与提醒">⏸️ 已暂停监控</span>';
        } else if (autoOrderEnabled) {
            if (globalAoEnabled) {
                modeCardClass = 'mode-live-buy';
                statusBadgeHtml = '<span class="rule-status-badge status-live-buy" title="已加入自动购买名单，当前处于自动下单模式，命中时将自动以真实订单下单！">🚨 真实抢购</span>';
            } else {
                modeCardClass = 'mode-live-buy';
                statusBadgeHtml = '<span class="rule-status-badge status-standby" title="已开启自动购买，但真实自动下单未开启，当前仅发送提醒">🛒 自动购买就绪 (未启动真实下单)</span>';
            }
        } else {
            modeCardClass = 'mode-notify';
            statusBadgeHtml = '<span class="rule-status-badge status-notify-only" title="仅消息提醒模式，绝不自动下单">🔔 提醒模式</span>';
        }

        card.className = `watch-card ${rule.enabled ? '' : 'disabled'} ${modeCardClass}`;

        card.innerHTML = `
            <div class="watch-card-body">
                <!-- 第一行：左上角开关胶囊 -->
                <div class="watch-card-top">
                    <div class="watch-switch-cluster">
                        <div class="watch-switch-item" title="监控与提醒总开关：开启后持续检测新上架并发送通知">
                            <span class="switch-icon">🔔</span>
                            <span class="switch-text">提醒</span>
                            <label class="switch switch-sm">
                                <input type="checkbox" class="watch-toggle" data-idx="${idx}" ${rule.enabled ? 'checked' : ''}>
                                <span class="slider"></span>
                            </label>
                        </div>
                        <div class="switch-divider"></div>
                        <div class="watch-switch-item ${!rule.enabled ? 'disabled' : ''}" title="专属自动购买开关：开启时此商品纳入自动购买名单；关闭时无论全局设置如何仅发送提醒">
                            <span class="switch-icon">🛒</span>
                            <span class="switch-text">自动购买</span>
                            <label class="switch switch-sm">
                                <input type="checkbox" class="watch-auto-buy-toggle" data-idx="${idx}" ${autoOrderEnabled ? 'checked' : ''} ${!rule.enabled ? 'disabled' : ''}>
                                <span class="slider slider-emerald"></span>
                            </label>
                        </div>
                    </div>
                </div>

                <!-- 第二行：商品名称 (开关的正下方独立成行) -->
                <div class="watch-name-row">
                    <span class="watch-name">${escapeHtml(rule.name)}</span>
                </div>

                <!-- 第三行：检索模式与匹配胶囊 (横向同行排列) -->
                <div class="watch-badges-row">
                    ${matchBadge}
                    <span class="watch-mode-tag">${modeMap[rule.search_mode] || '综合检索'}</span>
                </div>

                <!-- 第四行：关键词胶囊 (独立成行，防止过长引起高度跳动) -->
                <div class="watch-tag-list">
                    ${kws}
                    ${excludes}
                </div>

                <!-- 第五行：价格设置独立行 (仅设置了价格时显示，未设置价格时不显示) -->
                ${priceTag ? `<div class="watch-price-row">${priceTag}</div>` : ''}
            </div>

            <!-- 底部操作栏：左下角状态徽标 + 右下角编辑与删除 (保持居底) -->
            <div class="watch-card-footer">
                <div class="watch-footer-status">
                    ${statusBadgeHtml}
                </div>
                <div style="display:flex;gap:8px;">
                    <button class="btn btn-sm btn-ghost btn-edit-rule" data-idx="${idx}">编辑</button>
                    <button class="btn btn-sm btn-ghost text-danger btn-delete-rule" data-idx="${idx}">删除</button>
                </div>
            </div>
        `;

        elements.watchesContainer.appendChild(card);
    });

    // 绑定卡片内快速切换模式按钮
    elements.watchesContainer.querySelectorAll('.btn-toggle-mode').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const idx = parseInt(e.currentTarget.dataset.idx);
            const currentMode = currentConfig.watches[idx].match_mode || 'all';
            const newMode = currentMode === 'all' ? 'any' : 'all';
            currentConfig.watches[idx].match_mode = newMode;
            saveConfig(true);
            renderWatches();
            showToast(`已切换规则 [${currentConfig.watches[idx].name}] 为: ${newMode === 'all' ? 'AND 全部同时满足' : 'OR 满足任一词即可'}`, 'info');
        });
    });

    // 绑定卡片内按钮事件
    elements.watchesContainer.querySelectorAll('.watch-toggle').forEach(chk => {
        chk.addEventListener('change', (e) => {
            const idx = parseInt(e.target.dataset.idx);
            currentConfig.watches[idx].enabled = e.target.checked;
            saveConfig(true);
            renderWatches();
        });
    });

    elements.watchesContainer.querySelectorAll('.watch-auto-buy-toggle').forEach(chk => {
        chk.addEventListener('change', (e) => {
            const idx = parseInt(e.target.dataset.idx);
            const isChecked = e.target.checked;
            currentConfig.watches[idx].auto_order = isChecked;
            saveConfig(true);
            renderWatches();
            const ruleName = currentConfig.watches[idx].name;
            if (isChecked) {
                const globalOn = currentConfig.auto_order?.enabled;
                showToast(`已将 [${ruleName}] 加入自动购买名单${globalOn ? '（已启动真实自动下单）' : '（注：需在自动下单界面启动真实自动下单）'}`, 'success');
            } else {
                showToast(`已将 [${ruleName}] 设为【提醒模式】（命中时绝不自动下单）`, 'info');
            }
        });
    });

    elements.watchesContainer.querySelectorAll('.btn-edit-rule').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const idx = parseInt(e.target.dataset.idx);
            openRuleModal(currentConfig.watches[idx]);
        });
    });

    elements.watchesContainer.querySelectorAll('.btn-delete-rule').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const idx = parseInt(e.target.dataset.idx);
            if (confirm(`确定要删除规则 "${currentConfig.watches[idx].name}" 吗？`)) {
                currentConfig.watches.splice(idx, 1);
                saveConfig();
            }
        });
    });
}

// 初始化时分秒精细下拉选择器
function initTimeSelects() {
    if (!elements.dropTimeHour || !elements.dropTimeMinute || !elements.dropTimeSecond) return;
    elements.dropTimeHour.innerHTML = '';
    elements.dropTimeMinute.innerHTML = '';
    elements.dropTimeSecond.innerHTML = '';

    for (let i = 0; i < 24; i++) {
        const val = String(i).padStart(2, '0');
        const opt = document.createElement('option');
        opt.value = val;
        opt.textContent = val;
        if (i === 21) opt.selected = true; // 默认推荐 21:00:00
        elements.dropTimeHour.appendChild(opt);
    }
    for (let i = 0; i < 60; i++) {
        const val = String(i).padStart(2, '0');
        const optM = document.createElement('option');
        optM.value = val;
        optM.textContent = val;
        elements.dropTimeMinute.appendChild(optM);

        const optS = document.createElement('option');
        optS.value = val;
        optS.textContent = val;
        elements.dropTimeSecond.appendChild(optS);
    }
}

// 渲染已配置定点时刻标签流
function renderDropTimesChips() {
    if (!elements.dropTimesChips) return;
    elements.dropTimesChips.innerHTML = '';

    if (elements.dropTimesCountBadge) {
        elements.dropTimesCountBadge.textContent = `${currentDropTimes.length} 个时刻`;
    }

    if (currentDropTimes.length === 0) {
        elements.dropTimesChips.innerHTML = `
            <div class="drop-time-empty">
                <span>⚠️ 暂未添加定点时刻，请在下方选择后点击「➕ 添加到列表」</span>
            </div>
        `;
        return;
    }

    currentDropTimes.forEach(timeStr => {
        const chip = document.createElement('div');
        chip.className = 'drop-time-chip';
        chip.innerHTML = `
            <span class="chip-icon">⏰</span>
            <span class="chip-time">${escapeHtml(timeStr)}</span>
            <button type="button" class="chip-del-btn" data-time="${escapeHtml(timeStr)}" title="移除此时刻">×</button>
        `;
        elements.dropTimesChips.appendChild(chip);
    });

    // 绑定删除按钮
    elements.dropTimesChips.querySelectorAll('.chip-del-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const timeToRemove = e.currentTarget.dataset.time;
            currentDropTimes = currentDropTimes.filter(t => t !== timeToRemove);
            renderDropTimesChips();
            showToast(`已移除时刻 [${timeToRemove}]，请点击右上角「保存调度配置」生效`, 'info');
        });
    });
}

// 动态刷新自动下单运行状态横幅
function updateAoModeBanner() {
    if (!elements.aoModeBanner) return;
    const isEnabled = elements.aoEnabled ? elements.aoEnabled.checked : Boolean(currentConfig.auto_order?.enabled);

    updateTopOrderModeIndicator();
    const enabledLabel = document.getElementById('ao-enabled-label');
    if (enabledLabel) {
        enabledLabel.innerHTML = isEnabled
            ? '<span style="color:#f87171;font-weight:700;">🚨 启动真实自动下单 (已开启)</span>'
            : '<span>启动真实自动下单 (已关闭)</span>';
    }

    if (!isEnabled) {
        elements.aoModeBanner.className = 'ao-mode-banner idle';
        if (elements.aoModeIcon) elements.aoModeIcon.textContent = '🔔';
        if (elements.aoModeTitle) elements.aoModeTitle.textContent = '当前运行状态：提醒模式 (不下单)';
        if (elements.aoModeDesc) elements.aoModeDesc.textContent = '真实自动下单已关闭。当监控命中匹配的新商品时，仅触发声音、桌面弹窗、Discord 与邮件提醒，绝不自动加购下单。';
    } else {
        elements.aoModeBanner.className = 'ao-mode-banner warning';
        if (elements.aoModeIcon) elements.aoModeIcon.textContent = '🚨';
        if (elements.aoModeTitle) elements.aoModeTitle.textContent = '当前运行状态：自动下单模式 (真实下单)';
        if (elements.aoModeDesc) elements.aoModeDesc.textContent = '【重要警示】已启动真实自动下单！当监控命中匹配的全新在售商品时，系统将全自动推进至确认页并正式提交官方订单并扣款购买！请务必确认您需要真实抢购。';
    }
}

// 调度表单同步
function populateScheduleForm() {
    const s = currentConfig?.schedule || {};
    currentDropTimes = [...(s.drop_times || [])];
    renderDropTimesChips();
    if (elements.schedBurstDuration) elements.schedBurstDuration.value = s.burst_duration_sec ?? 180;
    if (elements.schedBurstInterval) elements.schedBurstInterval.value = s.burst_interval_sec ?? 0.3;
    if (elements.schedMaxPages) elements.schedMaxPages.value = s.max_stream_pages ?? 5;
    if (elements.schedIntervalEnabled) elements.schedIntervalEnabled.checked = s.interval_enabled ?? true;
    if (elements.schedIntervalMinutes) elements.schedIntervalMinutes.value = s.interval_minutes ?? 5;

    const dailySync = s.daily_stock_sync || {};
    if (elements.schedDailySyncEnabled) elements.schedDailySyncEnabled.checked = dailySync.enabled ?? true;
    if (elements.schedDailySyncTime) elements.schedDailySyncTime.value = dailySync.time || '05:00:00';
    updateDailySyncBadge();
}

function updateDailySyncBadge() {
    const badge = document.getElementById('sched-sync-status-badge');
    const isEnabled = elements.schedDailySyncEnabled ? elements.schedDailySyncEnabled.checked : true;
    const timeVal = (elements.schedDailySyncTime?.value || '05:00:00').trim();
    const timeShort = timeVal.length >= 5 ? timeVal.slice(0, 5) : timeVal;
    if (badge) {
        if (isEnabled) {
            badge.textContent = `⏰ 每日 ${timeShort} 自动盘点`;
            badge.style.opacity = '1';
        } else {
            badge.textContent = '⏸️ 每日自动盘点已停用';
            badge.style.opacity = '0.65';
        }
    }
}

// 通知表单同步
function populateNotifiersForm() {
    const n = currentConfig?.notifications || {};
    elements.notifSoundEnabled.checked = n.sound_enabled ?? true;
    elements.notifDesktopEnabled.checked = n.desktop_enabled ?? true;

    const discord = n.discord || {};
    elements.notifDiscordEnabled.checked = discord.enabled ?? false;
    elements.notifDiscordWebhook.value = discord.webhook_url || '';

    const email = n.email || {};
    elements.notifEmailEnabled.checked = email.enabled ?? false;
    elements.notifEmailHost.value = email.smtp_host || '';
    elements.notifEmailPort.value = email.smtp_port || 465;
    elements.notifEmailUser.value = email.smtp_user || '';
    elements.notifEmailPwd.value = email.smtp_password || '';
    elements.notifEmailReceiver.value = email.receiver_email || '';
}

// 自动下单表单同步
function populateAutoOrderForm() {
    const ao = currentConfig?.auto_order || {};
    if (elements.aoUsername) elements.aoUsername.value = ao.username || '';
    if (elements.aoPassword) elements.aoPassword.value = ao.password || '';
    if (elements.aoEnabled) elements.aoEnabled.checked = Boolean(ao.enabled);

    const pm = ao.payment_method || 'cod';
    const radioCod = document.getElementById('ao-pay-cod');
    const radioBank = document.getElementById('ao-pay-bank');
    const radioStore = document.getElementById('ao-pay-store');
    if (radioCod && radioStore) {
        if (pm === 'store_pickup' || pm === 'store') radioStore.checked = true;
        else if (pm === 'bank_transfer' || pm === 'bank' || pm === '銀行振込') {
            if (radioBank) radioBank.checked = true;
        } else radioCod.checked = true;
    }
    if (elements.aoTestPaySelect) {
        elements.aoTestPaySelect.value = (pm === 'bank' || pm === '銀行振込') ? 'bank_transfer' : pm;
    }
    if (elements.aoMaxWorkers) elements.aoMaxWorkers.value = ao.max_workers ?? 6;
    if (elements.aoStandbyWorkers) elements.aoStandbyWorkers.value = ao.standby_workers ?? 2;
    if (elements.aoRetryDelay) elements.aoRetryDelay.value = ao.retry_delay_sec ?? 0.15;
    if (elements.aoMaxRetries) elements.aoMaxRetries.value = ao.max_retries ?? 35;
    updateAoModeBanner();
}

// 全站档案商品一键下单独立配置同步
function populateQuickBuyForm() {
    const qb = currentConfig?.quick_buy || {};
    if (elements.quickBuyEnabled) elements.quickBuyEnabled.checked = qb.enabled ?? false;
    if (elements.quickBuyDryRun) elements.quickBuyDryRun.checked = qb.dry_run ?? true;
    updateQuickBuyStatusTag();
}

function updateQuickBuyStatusTag() {
    const tag = elements.quickBuyModeTag || document.getElementById('quick-buy-mode-tag');
    const enabled = elements.quickBuyEnabled?.checked ?? false;
    const dryRun = elements.quickBuyDryRun?.checked ?? true;
    if (tag) {
        if (!enabled) {
            tag.className = 'status-pill status-idle';
            tag.textContent = '⚪ 一键下单未启用';
        } else if (dryRun) {
            tag.className = 'status-pill status-busy';
            tag.textContent = '🛡️ 一键模拟下单';
        } else {
            tag.className = 'status-pill status-bursting';
            tag.textContent = '🚨 一键真实下单';
        }
    }

    // 同步更新实时发现流所有卡片上的一键购买按钮状态
    const feedBuyBtns = document.querySelectorAll('.btn-feed-quick-buy');
    feedBuyBtns.forEach(btn => {
        if (!enabled) {
            btn.classList.add('qb-disabled');
            btn.title = '⚡ 一键购买 (功能未开启，请先在【自动下单】设置中启动一键下单)';
        } else {
            btn.classList.remove('qb-disabled');
            btn.title = dryRun
                ? '⚡ 点击执行一键购买 (一键模拟下单模式)'
                : '⚡ 点击执行一键购买 (🚨 一键真实下单模式)';
        }
    });
}

// 规则 Modal 弹窗逻辑
function openRuleModal(rule = null) {
    const matchMode = rule ? (rule.match_mode || 'all') : 'all';
    const radioAll = document.getElementById('mode-match-all');
    const radioAny = document.getElementById('mode-match-any');
    if (radioAll && radioAny) {
        if (matchMode === 'all') radioAll.checked = true;
        else radioAny.checked = true;
    }

    if (rule) {
        elements.modalTitle.textContent = '编辑监控规则';
        elements.modalRuleId.value = rule.id || '';
        elements.modalRuleName.value = rule.name || '';
        elements.modalRuleKeywords.value = (rule.keywords || []).join(', ');
        elements.modalRuleExcludes.value = (rule.exclude_keywords || []).join(', ');
        elements.modalRuleMinPrice.value = rule.min_price || 0;
        elements.modalRuleMaxPrice.value = rule.max_price || 0;
        elements.modalRuleSearchMode.value = rule.search_mode || 'both';
        const autoOrderChk = document.getElementById('modal-rule-auto-order');
        if (autoOrderChk) autoOrderChk.checked = Boolean(rule.auto_order);
    } else {
        elements.modalTitle.textContent = '添加监控规则';
        elements.modalRuleId.value = 'w_' + Date.now();
        elements.modalRuleName.value = '';
        elements.modalRuleKeywords.value = '';
        elements.modalRuleExcludes.value = '';
        elements.modalRuleMinPrice.value = 0;
        elements.modalRuleMaxPrice.value = 0;
        elements.modalRuleSearchMode.value = 'both';
        const autoOrderChk = document.getElementById('modal-rule-auto-order');
        if (autoOrderChk) autoOrderChk.checked = false;
    }
    elements.ruleModal.style.display = 'flex';
}

function closeRuleModal() {
    elements.ruleModal.style.display = 'none';
}

function saveRuleFromModal() {
    const name = elements.modalRuleName.value.trim();
    if (!name) {
        alert('请输入规则名称');
        return;
    }
    const rawKws = elements.modalRuleKeywords.value;
    const keywords = rawKws.split(/[,，\n]+/).map(k => k.trim()).filter(Boolean);
    if (keywords.length === 0) {
        alert('请至少输入一个匹配关键词');
        return;
    }

    const rawExcludes = elements.modalRuleExcludes.value;
    const exclude_keywords = rawExcludes.split(/[,，\n]+/).map(k => k.trim()).filter(Boolean);

    const minPrice = parseInt(elements.modalRuleMinPrice.value) || 0;
    const maxPrice = parseInt(elements.modalRuleMaxPrice.value) || 0;
    const searchMode = elements.modalRuleSearchMode.value;
    const ruleId = elements.modalRuleId.value || ('w_' + Date.now());

    const selectedRadio = document.querySelector('input[name="modal-rule-match-mode"]:checked');
    const matchMode = selectedRadio ? selectedRadio.value : 'all';

    const autoOrderVal = document.getElementById('modal-rule-auto-order')?.checked ?? false;

    const existingIdx = currentConfig.watches.findIndex(r => r.id === ruleId);
    const ruleObj = {
        id: ruleId,
        name: name,
        keywords: keywords,
        exclude_keywords: exclude_keywords,
        min_price: minPrice,
        max_price: maxPrice,
        search_mode: searchMode,
        match_mode: matchMode,
        auto_order: autoOrderVal,
        enabled: existingIdx >= 0 ? currentConfig.watches[existingIdx].enabled : true
    };

    if (existingIdx >= 0) {
        currentConfig.watches[existingIdx] = ruleObj;
    } else {
        currentConfig.watches.push(ruleObj);
    }

    saveConfig();
    closeRuleModal();
}

// 渲染发现商品卡片流
function renderProductCard(item, prepend = true) {
    // 隐藏空白提示
    const empty = elements.feedContainer.querySelector('.empty-state');
    if (empty) empty.remove();

    const isStock = (item.is_in_stock !== false && item.is_in_stock !== 0);

    const card = document.createElement('div');
    card.className = `product-card ${isStock ? '' : 'is-sold-out'}`;
    card.setAttribute('data-id', String(item.id || ''));

    const imgSrc = item.img_url ? `/img_proxy?url=${encodeURIComponent(item.img_url)}` : '';
    const imgHtml = imgSrc
        ? `<img class="product-img" src="${imgSrc}" loading="lazy" alt="${escapeHtml(item.title)}">`
        : `<div style="height:100%;display:flex;align-items:center;justify-content:center;color:#64748b;">无缩略图</div>`;

    const soldOutOverlay = !isStock
        ? `<div class="product-soldout-overlay">
               <div class="soldout-watermark-text">SoldOut 売切</div>
           </div>`
        : '';

    const qbCfg = currentConfig?.quick_buy || {};
    const isQbEnabled = Boolean(qbCfg.enabled);
    const isQbDry = qbCfg.dry_run !== false;
    let qbBtnClass = isQbEnabled ? '' : 'qb-disabled';
    let qbBtnTitle = isQbEnabled
        ? (isQbDry ? '⚡ 点击执行一键购买 (一键模拟下单模式)' : '⚡ 点击执行一键购买 (🚨 一键真实下单模式)')
        : '⚡ 一键购买 (功能未开启，请先在【自动下单】设置中启动一键下单)';
    let qbBtnText = '⚡ 一键购买';
    let qbDisabledAttr = '';

    if (!isStock) {
        qbBtnClass += ' btn-secondary';
        qbBtnTitle = '官网该商品已售罄';
        qbBtnText = '已售罄';
        qbDisabledAttr = 'disabled';
    }

    const stockBadge = isStock
        ? `<span class="product-stock-badge">● 在库</span>`
        : `<span class="product-stock-badge is-sold-out">● 售罄</span>`;

    const timeTag = isStock
        ? `<span class="product-time-tag">即时在库</span>`
        : `<span class="product-time-tag is-sold-out">已售罄</span>`;

    card.innerHTML = `
        <div class="product-img-wrapper">
            ${soldOutOverlay}
            ${imgHtml}
            ${stockBadge}
            <span class="product-rule-badge">${escapeHtml(item.rule_name || '命中规则')}</span>
        </div>
        <div class="product-body">
            <div>
                <div class="product-title" title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</div>
                <div class="product-model">${escapeHtml(item.model_number || '')}</div>
            </div>
            <div class="product-footer">
                <div class="product-price-row">
                    <span class="product-price">${escapeHtml(item.price_str || '￥' + item.price)}</span>
                    ${timeTag}
                </div>
                <div class="product-card-actions">
                    <button type="button" class="btn btn-sm btn-warning btn-feed-quick-buy ${qbBtnClass}"
                        data-id="${escapeHtml(item.id)}"
                        data-title="${escapeHtml(item.title || '')}"
                        data-price="${escapeHtml(item.price_str || '￥' + item.price)}"
                        ${qbDisabledAttr}
                        title="${qbBtnTitle}">
                        ${qbBtnText}
                    </button>
                    <a href="${item.url}" target="_blank" rel="noopener noreferrer" class="btn btn-sm btn-ghost" title="在官网新标签页查看商品详情">
                        官网 ↗
                    </a>
                </div>
            </div>
        </div>
    `;

    if (prepend) {
        elements.feedContainer.prepend(card);
    } else {
        elements.feedContainer.appendChild(card);
    }
}

// 加载历史已发现商品
async function loadHistory() {
    try {
        const res = await fetch('/api/history');
        const list = await res.json();
        currentHistory = list;
        if (list && list.length > 0) {
            const empty = elements.feedContainer.querySelector('.empty-state');
            if (empty) empty.remove();
            list.forEach(item => renderProductCard(item, false));
        }
    } catch (e) {
        console.warn('加载历史商品失败:', e);
    }
}

// 初始化 SSE 实时监听
function initSSE() {
    const evtSource = new EventSource('/api/events');
    evtSource.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (data.type === 'connected') return;

            // 收到自动下单成功事件 (静默弹窗，不播放声音，且若为真实下单立即标记卡片售罄)
            if (data.type === 'order_success') {
                const item = data.item || {};
                if (item.id && !data.order_result?.dry_run) {
                    markFeedProductSoldOut(item.id);
                }
                showToast(`🎉 【自动下单成功】已成功为您抢到: ${item.title || item.id}！`, 'success');
                return;
            }

            // 收到终端实时日志事件
            if (data.type === 'terminal_log' && data.log) {
                onNewTerminalLog(data.log);
                return;
            }

            // 收到新购买记录事件
            if (data.type === 'order_recorded' && data.order) {
                onNewOrderRecorded(data.order);
                return;
            }

            // 收到商品售罄状态同步事件
            if (data.type === 'item_sold_out' && data.item_id) {
                markFeedProductSoldOut(data.item_id);
                return;
            }

            // 收到上新提醒事件
            if (data.items && data.items.length > 0) {
                playWebChime();
                showToast(`🔔 发现 ${data.items.length} 件新商品上架！`, 'success');

                // 增加 Badge 提示计数
                const currentTab = document.querySelector('.tab-btn.active')?.dataset.tab;
                if (currentTab !== 'tab-feed') {
                    elements.feedBadge.style.display = 'inline-block';
                    const count = parseInt(elements.feedBadge.textContent || '0') + data.items.length;
                    elements.feedBadge.textContent = count;
                }

                // 渲染到实时流顶部
                data.items.forEach(it => {
                    it.rule_name = data.rule_name;
                    renderProductCard(it, true);
                });
            }
        } catch (e) {
            console.error('SSE 数据解析错误:', e);
        }
    };
    evtSource.onerror = () => {
        // SSE 会自动尝试重连
    };
}

// 辅助转义 HTML
function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

// 绑定各操作事件
function bindEvents() {
    // 立即检测
    if (elements.btnCheckNow) {
        elements.btnCheckNow.addEventListener('click', async () => {
            elements.btnCheckNow.disabled = true;
            showToast('正在执行全网检测，请稍候...', 'info');
            try {
                const res = await fetch('/api/check_now', { method: 'POST' });
                const data = await res.json();
                if (data.status === 'ok') {
                    showToast(`检测完成！扫描 ${data.scanned_count} 件商品，发现 ${data.new_items_count} 件新上架`, 'success');
                } else {
                    showToast(data.message || '检测状态异常', 'error');
                }
            } catch (e) {
                showToast('检测失败: ' + e.message, 'error');
            } finally {
                elements.btnCheckNow.disabled = false;
            }
        });
    }

    // 开启高频抢购模式 (Burst)
    if (elements.btnBurst) {
        elements.btnBurst.addEventListener('click', async () => {
            try {
                const res = await fetch('/api/trigger_burst', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ duration: 180 })
                });
                const data = await res.json();
                showToast(data.message, 'success');
            } catch (e) {
                showToast('开启抢购模式失败: ' + e.message, 'error');
            }
        });
    }

    // 测试提示音
    if (elements.btnTestSound) {
        elements.btnTestSound.addEventListener('click', () => {
            playWebChime();
            fetch('/api/test_sound', { method: 'POST' });
            showToast('已播放测试音', 'info');
        });
    }
    if (elements.btnChanTestSound) {
        elements.btnChanTestSound.addEventListener('click', () => {
            playWebChime();
            fetch('/api/test_sound', { method: 'POST' });
            showToast('已播放测试音', 'info');
        });
    }

    // 测试 Windows 桌面气泡弹窗
    if (elements.btnTestDesktop) {
        elements.btnTestDesktop.addEventListener('click', async () => {
            const res = await fetch('/api/test_desktop', { method: 'POST' });
            const d = await res.json();
            showToast(d.message, 'info');
        });
    }
    if (elements.btnChanTestDesktop) {
        elements.btnChanTestDesktop.addEventListener('click', async () => {
            const res = await fetch('/api/test_desktop', { method: 'POST' });
            const d = await res.json();
            showToast(d.message, 'info');
        });
    }

    // 测试 Discord Webhook
    if (elements.btnChanTestDiscord) {
        elements.btnChanTestDiscord.addEventListener('click', async () => {
            const webhookUrl = elements.notifDiscordWebhook?.value.trim() || '';
            if (!webhookUrl) {
                showToast('请先输入 Discord Webhook 网址', 'error');
                return;
            }
            showToast('正在向 Discord 发送测试消息，请稍候...', 'info');
            try {
                const res = await fetch('/api/test_discord', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ webhook_url: webhookUrl })
                });
                if (!res.ok) {
                    showToast(`接口请求失败 (${res.status})：请在终端按 Ctrl+C 重启 python main.py`, 'error');
                    return;
                }
                const d = await res.json();
                showToast(d.message, d.status === 'ok' ? 'success' : 'error');
            } catch (e) {
                showToast('发送测试异常: ' + e.message, 'error');
            }
        });
    }

    // 测试邮件
    if (elements.btnChanTestEmail) {
        elements.btnChanTestEmail.addEventListener('click', async () => {
            const host = elements.notifEmailHost?.value.trim() || '';
            const user = elements.notifEmailUser?.value.trim() || '';
            const pwd = elements.notifEmailPwd?.value.trim() || '';
            const port = parseInt(elements.notifEmailPort?.value) || 465;
            const receiver = elements.notifEmailReceiver?.value.trim() || user;

            if (!host || !user || !pwd) {
                showToast('请先填写完整 SMTP 服务器、邮箱账号和授权码', 'error');
                return;
            }

            showToast('正在发送测试邮件，请稍候...', 'info');
            const res = await fetch('/api/test_email', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    smtp_host: host,
                    smtp_port: port,
                    smtp_user: user,
                    smtp_password: pwd,
                    receiver_email: receiver
                })
            });
            const d = await res.json();
            showToast(d.message, d.status === 'ok' ? 'success' : 'error');
        });
    }

    // 建立库存基准线
    if (elements.linkInitBaseline) {
        elements.linkInitBaseline.addEventListener('click', async () => {
            if (!confirm('确定将当前线上所有在售商品收录为基准吗？\n收录后，当前已有商品将不会报警，后续仅有全新上架或新补货时才会提醒。')) return;
            showToast('正在建立库存基准线，请稍候...', 'info');
            const res = await fetch('/api/init_baseline', { method: 'POST' });
            const d = await res.json();
            showToast(d.message, 'success');
        });
    }

    // 清空历史已记录商品
    if (elements.linkResetSeen) {
        elements.linkResetSeen.addEventListener('click', async () => {
            if (!confirm('确定要清空历史已记录商品吗？清空后，下次检测时所有匹配的在售商品都会被视为新商品并触发提醒。')) return;
            const res = await fetch('/api/reset_seen', { method: 'POST' });
            const d = await res.json();
            showToast(d.message, 'info');
        });
    }

    // 清空前端实时流展示
    if (elements.btnClearFeed) {
        elements.btnClearFeed.addEventListener('click', async () => {
            try {
                await fetch('/api/clear_history', { method: 'POST' });
            } catch (e) { }
            elements.feedContainer.innerHTML = `
                <div class="empty-state">
                    <div class="empty-icon">🎀</div>
                    <div class="empty-title">暂未发现新商品</div>
                    <div class="empty-desc">当监控触发且发现符合您关键字的有货商品时，将第一时间在此处卡片化展示并发出警报！</div>
                </div>
            `;
            showToast('已清空列表展示与历史记录', 'info');
        });
    }

    // 预设时段下拉切换
    if (elements.dropTimePreset) {
        elements.dropTimePreset.addEventListener('change', () => {
            const val = elements.dropTimePreset.value;
            if (!val) return;
            const parts = val.split(':');
            if (parts.length === 3) {
                if (elements.dropTimeHour) elements.dropTimeHour.value = parts[0];
                if (elements.dropTimeMinute) elements.dropTimeMinute.value = parts[1];
                if (elements.dropTimeSecond) elements.dropTimeSecond.value = parts[2];
            }
        });
    }

    // 新增定点时刻按钮
    if (elements.btnAddDropTime) {
        elements.btnAddDropTime.addEventListener('click', () => {
            const h = (elements.dropTimeHour?.value || '00').padStart(2, '0');
            const m = (elements.dropTimeMinute?.value || '00').padStart(2, '0');
            const s = (elements.dropTimeSecond?.value || '00').padStart(2, '0');
            const timeStr = `${h}:${m}:${s}`;

            if (currentDropTimes.includes(timeStr)) {
                showToast(`定点时刻 [${timeStr}] 已经在列表中`, 'error');
                return;
            }

            currentDropTimes.push(timeStr);
            currentDropTimes.sort();
            renderDropTimesChips();
            showToast(`已添加时刻 [${timeStr}]，点击右上角「保存调度配置」生效`, 'success');
        });
    }

    // 保存调度设置
    if (elements.btnSaveSchedule) {
        elements.btnSaveSchedule.addEventListener('click', () => {
            currentConfig.schedule = currentConfig.schedule || {};
            currentConfig.schedule.drop_times = [...currentDropTimes];
            currentConfig.schedule.burst_duration_sec = parseInt(elements.schedBurstDuration?.value) || 180;
            currentConfig.schedule.burst_interval_sec = parseFloat(elements.schedBurstInterval?.value) || 0.3;
            currentConfig.schedule.max_stream_pages = parseInt(elements.schedMaxPages?.value) || 5;
            currentConfig.schedule.interval_enabled = elements.schedIntervalEnabled?.checked ?? true;
            currentConfig.schedule.interval_minutes = parseFloat(elements.schedIntervalMinutes?.value) || 5;
            currentConfig.schedule.daily_stock_sync = {
                enabled: elements.schedDailySyncEnabled?.checked ?? true,
                time: (elements.schedDailySyncTime?.value || '').trim() || '05:00:00'
            };
            saveConfig();
        });
    }

    if (elements.schedDailySyncEnabled) {
        elements.schedDailySyncEnabled.addEventListener('change', updateDailySyncBadge);
    }
    if (elements.schedDailySyncTime) {
        elements.schedDailySyncTime.addEventListener('input', updateDailySyncBadge);
    }

    // 保存通知设置
    if (elements.btnSaveNotifiers) {
        elements.btnSaveNotifiers.addEventListener('click', () => {
            currentConfig.notifications = currentConfig.notifications || {};
            currentConfig.notifications.sound_enabled = elements.notifSoundEnabled.checked;
            currentConfig.notifications.desktop_enabled = elements.notifDesktopEnabled.checked;
            currentConfig.notifications.discord = {
                enabled: elements.notifDiscordEnabled.checked,
                webhook_url: elements.notifDiscordWebhook.value.trim()
            };
            const port = parseInt(elements.notifEmailPort.value) || 465;
            currentConfig.notifications.email = {
                enabled: elements.notifEmailEnabled.checked,
                smtp_host: elements.notifEmailHost.value.trim(),
                smtp_port: port,
                smtp_user: elements.notifEmailUser.value.trim(),
                smtp_password: elements.notifEmailPwd.value.trim(),
                receiver_email: elements.notifEmailReceiver.value.trim(),
                use_ssl: port === 465
            };
            saveConfig();
        });
    }

    // 保存自动下单设置
    if (elements.btnSaveAutoOrder) {
        elements.btnSaveAutoOrder.addEventListener('click', () => {
            const selectedPayRadio = document.querySelector('input[name="ao-payment-method"]:checked');
            const payMethod = selectedPayRadio ? selectedPayRadio.value : 'cod';
            const isEnabled = elements.aoEnabled ? elements.aoEnabled.checked : false;
            currentConfig.auto_order = {
                username: elements.aoUsername.value.trim(),
                password: elements.aoPassword.value.trim(),
                dry_run: false,
                enabled: isEnabled,
                payment_method: payMethod,
                max_workers: parseInt(elements.aoMaxWorkers?.value) || 6,
                standby_workers: parseInt(elements.aoStandbyWorkers?.value) || 2,
                retry_delay_sec: parseFloat(elements.aoRetryDelay?.value) || 0.15,
                max_retries: parseInt(elements.aoMaxRetries?.value) || 35
            };
            if (elements.quickBuyEnabled) {
                if (!currentConfig.quick_buy) currentConfig.quick_buy = {};
                currentConfig.quick_buy.enabled = elements.quickBuyEnabled.checked;
                currentConfig.quick_buy.dry_run = elements.quickBuyDryRun ? elements.quickBuyDryRun.checked : true;
            }
            saveConfig();
            updateAoModeBanner();
        });
    }

    // 自动下单开关联动实时状态横幅与即时保存
    // 顶部下单模式指示器点击快捷跳转
    if (elements.orderModeIndicator) {
        elements.orderModeIndicator.addEventListener('click', () => {
            const tabBtn = document.querySelector('.tab-btn[data-tab="tab-auto-order"]');
            if (tabBtn) tabBtn.click();
        });
    }

    if (elements.aoEnabled) {
        elements.aoEnabled.addEventListener('change', () => {
            if (!currentConfig.auto_order) currentConfig.auto_order = {};
            if (elements.aoEnabled.checked) {
                const confirmed = confirm(
                    "⚠️ 重要安全提示：\n\n" +
                    "您正在开启【启动真实自动下单】！\n" +
                    "开启后，一旦监控命中您开启自动购买的商品，系统将在确认页直接正式提交官方真实订单并扣款购买！\n\n" +
                    "确定要开启真实自动下单吗？"
                );
                if (!confirmed) {
                    elements.aoEnabled.checked = false;
                    return;
                }
                currentConfig.auto_order.enabled = true;
                currentConfig.auto_order.dry_run = false;
                updateAoModeBanner();
                saveConfig(true);
                renderWatches();
                showToast('🚨 已启动真实自动下单！命中监控时将向官网直接提交订单', 'warning');
            } else {
                currentConfig.auto_order.enabled = false;
                currentConfig.auto_order.dry_run = false;
                updateAoModeBanner();
                saveConfig(true);
                renderWatches();
                showToast('🔔 已切换为提醒模式（不下单）', 'info');
            }
        });
    }

    // 执行 Dry-Run 下单前最终确认页仿真测试
    if (elements.btnTestDryRunOrder) {
        elements.btnTestDryRunOrder.addEventListener('click', async () => {
            const username = elements.aoUsername.value.trim();
            const password = elements.aoPassword.value.trim();
            if (!username || !password) {
                showToast('请先输入 Dollyteria 官网登录邮箱与密码', 'error');
                return;
            }

            const itemId = elements.aoTestItemId ? elements.aoTestItemId.value.trim() : '';
            const payMethod = elements.aoTestPaySelect ? elements.aoTestPaySelect.value : (
                document.querySelector('input[name="ao-payment-method"]:checked')?.value || 'cod'
            );
            const payName = payMethod === 'store_pickup' ? '店頭支払' : (payMethod === 'bank_transfer' ? '銀行振込' : '代金引換');

            elements.btnTestDryRunOrder.disabled = true;
            elements.btnTestDryRunOrder.textContent = '⏳ 正在执行实盘风控探针测试...';
            showToast(`正在执行【${payName}】全流程仿真测试 (含官网 reCAPTCHA 实盘核验探针)...`, 'info');

            if (elements.aoTestResult) {
                elements.aoTestResult.style.display = 'block';
                elements.aoTestResult.innerHTML = `
                    <div style="display:flex;align-items:center;gap:10px;color:var(--text-secondary);">
                        <span class="status-pulse" style="width:10px;height:10px;background:#38bdf8;border-radius:50%;display:inline-block;"></span>
                        正在执行登录、秒级加购、锁定【${escapeHtml(payName)}】支付并向官网发送零风险实盘探针检验令牌...
                    </div>
                `;
            }

            try {
                const res = await fetch('/api/test_auto_order', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username, password, item_id: itemId, payment_method: payMethod, probe_recaptcha: true })
                });

                if (!res.ok) {
                    showToast(`测试接口返回错误 (${res.status})，请重启终端里的 python main.py`, 'error');
                    if (elements.aoTestResult) {
                        elements.aoTestResult.innerHTML = `<div style="color:#f87171;">测试请求失败 (${res.status})，请重启终端中的 python main.py 以加载新接口</div>`;
                    }
                    return;
                }

                const data = await res.json();
                if (data.success && data.details) {
                    showToast('🎉 Dry-Run 仿真测试成功！已顺利抵达最终确认页', 'success');
                    const d = data.details;
                    const stepsHtml = (data.steps_log || []).map(s => `<li>${escapeHtml(s)}</li>`).join('');

                    let probeCardHtml = '';
                    if (d.recaptcha_probe) {
                        const p = d.recaptcha_probe;
                        const isPass = p.passed;
                        probeCardHtml = `
                            <div class="ao-summary-item" style="grid-column: 1 / -1; background: ${isPass ? 'rgba(52, 211, 153, 0.08)' : 'rgba(248, 113, 113, 0.12)'}; border: 1px solid ${isPass ? 'rgba(52, 211, 153, 0.3)' : 'rgba(248, 113, 113, 0.3)'}; margin-top: 4px;">
                                <div class="label" style="color: ${isPass ? '#34d399' : '#f87171'}; font-weight: bold; font-size: 12px;">
                                    ${isPass ? '🛡️ 官网实盘风控探针核验：🟢 成功通过 (官方已认可并核销令牌)' : '⚠️ 官网实盘风控探针核验：❌ 遭遇官网拦截'}
                                </div>
                                <div class="value" style="font-size: 13px; color: ${isPass ? '#34d399' : '#f87171'}; margin-top: 4px;">
                                    ${escapeHtml(p.message || '')}
                                </div>
                                <div style="font-size: 11px; color: var(--text-muted); margin-top: 4px;">
                                    探针令牌特征: <span style="font-family: monospace;">${escapeHtml(p.token_preview || '--')}</span> • 采用未勾选规约的零风险实盘探针技术，绝不产生实际扣款或订单！
                                </div>
                            </div>
                        `;
                    }

                    elements.aoTestResult.innerHTML = `
                        <div class="ao-res-header">
                            <span class="ao-res-status success">✅ 仿真测试成功：已安全抵达下单前最终确认页面 (/cart/confirm)</span>
                            <span style="font-size:12px;color:var(--text-muted);">耗时: ${data.elapsed_sec}s</span>
                        </div>
                        <div class="ao-grid-summary">
                            <div class="ao-summary-item">
                                <div class="label">测试目标商品</div>
                                <div class="value">${escapeHtml(d.items && d.items[0] ? d.items[0] : '已加购商品')}</div>
                            </div>
                            <div class="ao-summary-item">
                                <div class="label">支付方式 (已锁定)</div>
                                <div class="value" style="color:#34d399;">${escapeHtml(d.payment_method || '代金引換')}</div>
                            </div>
                            <div class="ao-summary-item">
                                <div class="label">商品小计</div>
                                <div class="value">${escapeHtml(d.subtotal || '--')}</div>
                            </div>
                            <div class="ao-summary-item">
                                <div class="label">配送运费</div>
                                <div class="value">${escapeHtml(d.shipping_fee || '--')}</div>
                            </div>
                            <div class="ao-summary-item">
                                <div class="label">代金引换手续费</div>
                                <div class="value">${escapeHtml(d.cod_fee || '--')}</div>
                            </div>
                            <div class="ao-summary-item">
                                <div class="label">最终结算总计</div>
                                <div class="value" style="color:#fbbf24;font-size:15px;">${escapeHtml(d.total_price || '--')}</div>
                            </div>
                            <div class="ao-summary-item">
                                <div class="label">规约同意要点 (* 同意する)</div>
                                <div class="value" style="color:#34d399;">✓ policy_agree=1 (自动勾选)</div>
                            </div>
                            ${probeCardHtml}
                        </div>
                        <div style="font-size:12px;color:var(--text-secondary);margin-bottom:6px;">
                            <strong>收货人与地址：</strong>${escapeHtml(d.recipient_name || '')} • ${escapeHtml(d.recipient_address || '')}
                        </div>
                        <ul class="ao-steps-list">
                            ${stepsHtml}
                        </ul>
                        <div class="ao-safety-banner">
                            🛡️ <strong>安全测试防护已生效：</strong>程序已在最终「購入する」确认按钮前严格安全拦截，未产生任何实际扣款或订单！测试商品已自动从购物车移出清空。
                        </div>
                    `;
                } else {
                    showToast(data.message || '测试未通过', 'error');
                    const stepsHtml = (data.steps_log || []).map(s => `<li>${escapeHtml(s)}</li>`).join('');
                    elements.aoTestResult.innerHTML = `
                        <div class="ao-res-header">
                            <span class="ao-res-status error">❌ 仿真流程在步骤 [${escapeHtml(data.step || 'unknown')}] 受阻</span>
                            <span style="font-size:12px;color:var(--text-muted);">耗时: ${data.elapsed_sec || 0}s</span>
                        </div>
                        <div style="color:#f87171;font-size:13px;margin-bottom:10px;">${escapeHtml(data.message || '执行受阻')}</div>
                        <ul class="ao-steps-list">${stepsHtml}</ul>
                    `;
                }
            } catch (err) {
                showToast('执行仿真测试发生异常: ' + err.message, 'error');
                if (elements.aoTestResult) {
                    elements.aoTestResult.innerHTML = `<div style="color:#f87171;">测试异常: ${escapeHtml(err.message)}</div>`;
                }
            } finally {
                elements.btnTestDryRunOrder.disabled = false;
                elements.btnTestDryRunOrder.textContent = '🧪 仿真下单 (含实盘风控探针)';
            }
        });
    }

    // 测试 reCAPTCHA v3 预热池与令牌签发通道 (秒级验证通道可用性，无需加购或下单)
    if (elements.btnTestRecaptchaToken) {
        elements.btnTestRecaptchaToken.addEventListener('click', async () => {
            elements.btnTestRecaptchaToken.disabled = true;
            const originalText = elements.btnTestRecaptchaToken.textContent;
            elements.btnTestRecaptchaToken.textContent = '⏳ 正在签发测试令牌...';
            showToast('正在向后台预热池请求签发全新 Google reCAPTCHA v3 令牌...', 'info');

            if (elements.aoTestResult) {
                elements.aoTestResult.style.display = 'block';
                elements.aoTestResult.innerHTML = `
                    <div style="display:flex;align-items:center;gap:10px;color:var(--text-secondary);">
                        <span class="status-pulse" style="width:10px;height:10px;background:#38bdf8;border-radius:50%;display:inline-block;"></span>
                        正在调度 Chromium 预热标签页执行 grecaptcha.execute 并提取鲜活令牌...
                    </div>
                `;
            }

            try {
                const res = await fetch('/api/test_recaptcha', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ action: 'submit' })
                });

                if (!res.ok) {
                    showToast(`测试令牌接口返回错误 (${res.status})`, 'error');
                    if (elements.aoTestResult) {
                        elements.aoTestResult.innerHTML = `<div style="color:#f87171;">请求失败 (${res.status})，请重启终端中的 python main.py</div>`;
                    }
                    return;
                }

                const data = await res.json();
                if (data.success) {
                    showToast(`🎉 reCAPTCHA 令牌测试成功！(耗时 ${data.elapsed_sec}s)`, 'success');
                    elements.aoTestResult.innerHTML = `
                        <div class="ao-res-header">
                            <span class="ao-res-status success">🟢 Google reCAPTCHA v3 预热池与令牌通道检测通过</span>
                            <span style="font-size:12px;color:var(--text-muted);">出码耗时: ${data.elapsed_sec}s</span>
                        </div>
                        <div class="ao-grid-summary">
                            <div class="ao-summary-item">
                                <div class="label">预热标签页池状态</div>
                                <div class="value" style="color:#34d399;">✓ ${data.pool_size || 1} / ${data.target_pool_size || 1} 个 Standby Tabs 就绪</div>
                            </div>
                            <div class="ao-summary-item">
                                <div class="label">分配调度槽位 (Tab)</div>
                                <div class="value">${escapeHtml(data.tab_id || 'Tab-01')}</div>
                            </div>
                            <div class="ao-summary-item">
                                <div class="label">浏览器内核进程</div>
                                <div class="value" style="font-size:12px;">${escapeHtml(data.browser_exe ? data.browser_exe.split(/[\\\\/]/).pop() : 'Chromium')} (PID: ${data.browser_pid || '--'})</div>
                            </div>
                            <div class="ao-summary-item">
                                <div class="label">指纹 User-Agent 对齐</div>
                                <div class="value" style="color:#38bdf8;font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escapeHtml(data.browser_user_agent || '')}">✓ ${escapeHtml(data.browser_user_agent ? data.browser_user_agent.substring(0, 36) + '...' : '已对齐')}</div>
                            </div>
                            <div class="ao-summary-item">
                                <div class="label">令牌签发状态</div>
                                <div class="value" style="color:#fbbf24;">✓ 成功签发 (${data.token_length} 字符)</div>
                            </div>
                            <div class="ao-summary-item">
                                <div class="label">令牌特征预览</div>
                                <div class="value" style="font-family:monospace;font-size:11px;color:#a78bfa;">${escapeHtml(data.token_preview || '')}</div>
                            </div>
                        </div>
                        <div class="ao-safety-banner" style="background:rgba(56,189,248,0.08);border-left:3px solid #38bdf8;">
                            💡 <strong>令牌机制说明：</strong>Google reCAPTCHA v3 采用官方原生单次消费 (Single-Use) 机制，仅在最终提交购买确认时由 Dollyteria 官方服务端向 Google 验证并核销。本次测试已验证后台无头浏览器、多标签并发池、Google 官方脚本与通信通道 100% 畅通且秒级出码，具备实盘抢购能力！测试后测试槽位已毫秒级释放归还。
                        </div>
                    `;
                } else {
                    showToast(data.message || '测试令牌签发未通过', 'error');
                    elements.aoTestResult.innerHTML = `
                        <div class="ao-res-header">
                            <span class="ao-res-status error">❌ Google reCAPTCHA v3 令牌签发通道受阻</span>
                            <span style="font-size:12px;color:var(--text-muted);">耗时: ${data.elapsed_sec || 0}s</span>
                        </div>
                        <div style="color:#f87171;font-size:13px;margin-bottom:10px;">${escapeHtml(data.message || '签发失败')}</div>
                        <div class="ao-safety-banner">
                            ⚠️ <strong>排查建议：</strong>请检查宿主机或 Docker 容器内是否安装有 Chrome / Chromium，以及后台网络是否能正常访问 google.com 静态资源与 dollyteria.com。
                        </div>
                    `;
                }
            } catch (err) {
                showToast('测试令牌发生异常: ' + err.message, 'error');
                if (elements.aoTestResult) {
                    elements.aoTestResult.innerHTML = `<div style="color:#f87171;">测试异常: ${escapeHtml(err.message)}</div>`;
                }
            } finally {
                elements.btnTestRecaptchaToken.disabled = false;
                elements.btnTestRecaptchaToken.textContent = originalText;
            }
        });
    }

    // 添加规则与 Modal 事件
    if (elements.btnAddWatch) elements.btnAddWatch.addEventListener('click', () => openRuleModal());
    if (elements.modalClose) elements.modalClose.addEventListener('click', closeRuleModal);
    if (elements.modalBtnCancel) elements.modalBtnCancel.addEventListener('click', closeRuleModal);
    if (elements.modalBtnSave) elements.modalBtnSave.addEventListener('click', saveRuleFromModal);

    // 鉴权与安全相关事件绑定
    if (elements.btnLockScreen) elements.btnLockScreen.addEventListener('click', doLogout);
    if (elements.btnQuickLock) elements.btnQuickLock.addEventListener('click', doLogout);
    if (elements.authForm) {
        elements.authForm.addEventListener('submit', (e) => {
            e.preventDefault();
            doLogin();
        });
    }
    if (elements.btnAuthLogin) elements.btnAuthLogin.addEventListener('click', doLogin);
    if (elements.btnSaveWebPassword) elements.btnSaveWebPassword.addEventListener('click', saveWebPassword);

    // 锁屏密码框回车键直接提交
    if (elements.authPasswordInput) {
        elements.authPasswordInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                doLogin();
            }
        });
    }

    // 显示/隐藏密码明文切换 (密文时显示 🙈 遮眼，明文时显示 👁️ 睁眼)
    if (elements.btnAuthTogglePwd && elements.authPasswordInput) {
        elements.btnAuthTogglePwd.addEventListener('click', () => {
            const isPwd = elements.authPasswordInput.type === 'password';
            elements.authPasswordInput.type = isPwd ? 'text' : 'password';
            elements.btnAuthTogglePwd.textContent = isPwd ? '👁️' : '🙈';
            elements.btnAuthTogglePwd.title = isPwd ? '点击隐藏密码' : '点击显示明文';
        });
    }
    if (elements.btnToggleNewPwd && elements.inputNewWebPassword) {
        elements.btnToggleNewPwd.addEventListener('click', () => {
            const isPwd = elements.inputNewWebPassword.type === 'password';
            elements.inputNewWebPassword.type = isPwd ? 'text' : 'password';
            elements.btnToggleNewPwd.textContent = isPwd ? '👁️' : '🙈';
            elements.btnToggleNewPwd.title = isPwd ? '点击隐藏密码' : '点击显示明文';
        });
    }
}

// ==================== 全站商品档案与收录模块 ====================
const archiveState = {
    page: 1,
    pageSize: 30,
    keyword: '',
    inStock: 'all',
    isNew: 'all',
    presetKeywords: [],
    selectedPresetKeywords: [],
    presetMatchMode: 'all',
    sortBy: 'id_desc',
    totalPages: 1,
    totalCount: 0,
    isLoading: false,
    hasLoadedOnce: false,
    crawlerPollTimer: null,
};

function onArchiveTabActive() {
    pollArchiveCrawlerStatus();
    if (!archiveState.hasLoadedOnce) {
        loadArchiveProducts(1);
    }
    if (!archiveState.crawlerPollTimer) {
        archiveState.crawlerPollTimer = setInterval(pollArchiveCrawlerStatus, 2000);
    }
}

async function pollArchiveCrawlerStatus() {
    try {
        const res = await fetch('/api/archive/stats');
        if (res.status === 401) return;
        const data = await res.json();

        // 统计数据
        const stats = data.archive_stats || {};
        const elTotal = document.getElementById('arch-stat-total');
        const elInStock = document.getElementById('arch-stat-in-stock');
        const elSoldOut = document.getElementById('arch-stat-sold-out');
        const elProgress = document.getElementById('arch-stat-progress');
        const elProgressSub = document.getElementById('arch-stat-progress-sub');
        const elFill = document.getElementById('crawler-progress-fill');
        const elProgressText = document.getElementById('crawler-progress-text');
        const elSpeedText = document.getElementById('crawler-speed-text');

        if (elTotal) elTotal.textContent = (stats.total || 0).toLocaleString();
        if (elInStock) elInStock.textContent = (stats.in_stock || 0).toLocaleString();
        if (elSoldOut) elSoldOut.textContent = (stats.sold_out || 0).toLocaleString();
        if (elProgress) elProgress.textContent = `${data.progress_pct || 0}%`;

        // 盘点时间与状态条显示
        const elSyncTime = document.getElementById('arch-sync-last-time');
        const elSyncDetail = document.getElementById('arch-sync-last-detail');
        const cfgTime = (elements.schedDailySyncTime?.value || '05:00:00').trim().slice(0, 5) || '05:00';
        if (elSyncTime) {
            if (stats.last_stock_sync_at && stats.last_stock_sync_at > 0) {
                const syncDate = new Date(stats.last_stock_sync_at * 1000);
                const timeStr = syncDate.toLocaleString('zh-CN', { hour12: false });
                elSyncTime.textContent = `上次盘点: ${timeStr}`;
            } else {
                elSyncTime.textContent = `上次盘点: 暂无盘点记录 (每日 ${cfgTime} 自动执行)`;
            }
        }
        if (elSyncDetail) {
            if (stats.last_stock_sync_at && stats.last_stock_sync_at > 0) {
                elSyncDetail.textContent = `在线在售存活: ${stats.last_stock_sync_total_live || 0} 件 | 本次新识别售罄: ${stats.last_stock_sync_sold_count || 0} 件`;
            } else {
                elSyncDetail.textContent = '仅抓取官网在售清单差集比对，单次仅约 8MB 流量';
            }
        }
        updateDailySyncBadge();

        // 按钮元素
        const btnStart = document.getElementById('btn-crawler-start');
        const btnPause = document.getElementById('btn-crawler-pause');
        const btnResume = document.getElementById('btn-crawler-resume');
        const btnStop = document.getElementById('btn-crawler-stop');

        const status = data.status || 'idle';
        const isStockSync = data.mode === 'stock_sync';

        if (elProgressSub) {
            if (isStockSync && status === 'running') {
                elProgressSub.textContent = '🔄 盘点在库中...';
            } else if (isStockSync && status === 'finished') {
                elProgressSub.textContent = '✅ 盘点完成';
            } else {
                const statusMap = {
                    'idle': '就绪待命',
                    'running': '⚡ 正在采集...',
                    'paused': '⏸️ 已暂停',
                    'finished': '✅ 采集完成',
                    'error': '❌ 异常中断',
                };
                elProgressSub.textContent = statusMap[status] || status;
            }
        }

        if (elFill) {
            elFill.style.width = `${Math.min(100, Math.max(0, data.progress_pct || 0))}%`;
        }

        if (elProgressText) {
            if (status === 'running') {
                if (isStockSync) {
                    elProgressText.textContent = `[在库盘点中] 正在核验第 ${data.current_page} / ${data.total_pages || '?'} 页在售商品 (已扫描在售 ${data.total_scraped || 0} 件)...`;
                } else {
                    elProgressText.textContent = `正在抓取第 ${data.current_page} / ${data.total_pages || '?'} 页 (累计抓取 ${data.total_scraped || 0} 件，新增 ${data.new_inserted || 0}，更新 ${data.updated_existing || 0})`;
                }
            } else if (status === 'paused') {
                elProgressText.textContent = `任务暂停在第 ${data.current_page} / ${data.total_pages || '?'} 页 (随时可点击继续)`;
            } else if (status === 'finished') {
                if (isStockSync) {
                    elProgressText.textContent = `在库盘点完成！成功识别并标记 ${data.sold_out_flipped ?? stats.last_stock_sync_sold_count ?? 0} 件售罄商品 (当前在线在售: ${stats.in_stock || 0})`;
                } else {
                    elProgressText.textContent = `已完成全站爬取！共收录 ${stats.total || 0} 件商品 (在售: ${stats.in_stock || 0})`;
                }
            } else if (status === 'error') {
                elProgressText.textContent = `任务出错: ${data.last_error || '网络或接口异常'}`;
            } else {
                elProgressText.textContent = `准备就绪 (全站约 40,500 件商品，单页 100 件平缓抓取)`;
            }
        }

        if (elSpeedText) {
            elSpeedText.textContent = `单页延时: ${data.delay_sec || 1.2}s`;
        }

        // 按钮显示状态联动
        if (btnStart && btnPause && btnResume && btnStop) {
            if (status === 'running') {
                btnStart.style.display = 'none';
                btnPause.style.display = 'inline-flex';
                btnResume.style.display = 'none';
                btnStop.style.display = 'inline-flex';
            } else if (status === 'paused') {
                btnStart.style.display = 'none';
                btnPause.style.display = 'none';
                btnResume.style.display = 'inline-flex';
                btnStop.style.display = 'inline-flex';
            } else {
                btnStart.style.display = 'inline-flex';
                btnPause.style.display = 'none';
                btnResume.style.display = 'none';
                btnStop.style.display = 'none';
            }
        }
    } catch (e) {
        console.error('pollArchiveCrawlerStatus error:', e);
    }
}

async function loadArchiveProducts(page = 1) {
    if (archiveState.isLoading) return;
    archiveState.isLoading = true;
    archiveState.hasLoadedOnce = true;
    archiveState.page = Math.max(1, page);

    const grid = document.getElementById('archive-products-grid');
    const empty = document.getElementById('archive-empty');
    const pagination = document.getElementById('archive-pagination');
    const matchCount = document.getElementById('archive-match-count');

    if (grid) {
        grid.innerHTML = '<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--text-muted); font-size: 14px;">⏳ 正在检索档案库商品...</div>';
    }

    try {
        const params = new URLSearchParams({
            keyword: archiveState.keyword,
            in_stock: archiveState.inStock,
            is_new: archiveState.isNew,
            preset_keywords: archiveState.selectedPresetKeywords.join(','),
            preset_match_mode: archiveState.presetMatchMode,
            sort_by: archiveState.sortBy,
            page: String(archiveState.page),
            page_size: String(archiveState.pageSize),
        });

        const res = await fetch(`/api/archive/products?${params.toString()}`);
        if (res.status === 401) {
            showLockScreen();
            return;
        }
        const data = await res.json();

        archiveState.totalPages = data.total_pages || 1;
        archiveState.totalCount = data.total || 0;

        if (matchCount) {
            matchCount.textContent = `共 ${data.total.toLocaleString()} 件匹配`;
        }

        archiveState.lastItems = data.items || [];
        renderArchiveProducts(archiveState.lastItems);

        if (pagination) {
            if (data.total > 0 && data.total_pages > 1) {
                pagination.style.display = 'flex';
                const cur = document.getElementById('arch-page-current');
                const tot = document.getElementById('arch-page-total');
                const prevBtn = document.getElementById('btn-arch-prev');
                const nextBtn = document.getElementById('btn-arch-next');
                if (cur) cur.textContent = data.page;
                if (tot) tot.textContent = data.total_pages;
                if (prevBtn) prevBtn.disabled = data.page <= 1;
                if (nextBtn) nextBtn.disabled = data.page >= data.total_pages;
            } else {
                pagination.style.display = 'none';
            }
        }

        if (empty) {
            empty.style.display = data.total === 0 ? 'block' : 'none';
        }
    } catch (e) {
        console.error('loadArchiveProducts error:', e);
        if (grid) grid.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: #f87171;">检索异常: ${escapeHtml(e.message)}</div>`;
    } finally {
        archiveState.isLoading = false;
    }
}

function renderArchiveProducts(items) {
    const grid = document.getElementById('archive-products-grid');
    if (!grid) return;

    if (!items || items.length === 0) {
        grid.innerHTML = '';
        return;
    }

    const qbCfg = currentConfig?.quick_buy || {};
    const isQbEnabled = Boolean(qbCfg.enabled);
    const isQbDry = qbCfg.dry_run !== false;
    const qbBtnClass = isQbEnabled ? '' : 'qb-disabled';
    const qbBtnTitle = isQbEnabled
        ? (isQbDry ? '⚡ 点击执行一键购买 (一键模拟下单模式)' : '⚡ 点击执行一键购买 (🚨 一键真实下单模式)')
        : '⚡ 一键购买 (功能未开启，请先在【自动下单】设置中启动一键下单)';

    grid.innerHTML = items.map(it => {
        const isStock = it.is_in_stock;

        const soldOutOverlay = !isStock
            ? `<div class="arch-soldout-overlay">
                   <div class="soldout-watermark-text">SoldOut 売切</div>
               </div>`
            : '';

        const newBadge = it.is_new
            ? `<div class="arch-card-new-badge">✨ 新品</div>`
            : '';

        const imgSrc = it.img_url || 'https://www.dollyteria.com/html/template/default/img/common/no_image.gif';
        const priceStr = it.price_str || `￥${(it.price || 0).toLocaleString()}`;
        const modelStr = it.model_number ? escapeHtml(it.model_number) : '常规';

        return `
            <div class="arch-card ${isStock ? '' : 'is-sold-out'}" data-id="${escapeHtml(it.id)}">
                <div class="arch-card-thumb-wrap">
                    ${newBadge}
                    ${soldOutOverlay}
                    <img src="${escapeHtml(imgSrc)}"
                         alt="${escapeHtml(it.title || '')}"
                         class="arch-card-img"
                         loading="lazy"
                         onerror="this.onerror=null;this.src='https://www.dollyteria.com/html/template/default/img/common/no_image.gif'">
                </div>
                <div class="arch-card-body">
                    <div class="arch-card-meta">
                        <span class="arch-card-model" title="型号编号">${modelStr}</span>
                        <span class="arch-card-id">#${escapeHtml(it.id)}</span>
                    </div>
                    <div class="arch-card-title" title="${escapeHtml(it.title || '')}">${escapeHtml(it.title || '无标题商品')}</div>
                    <div class="arch-card-footer">
                        <div class="arch-card-price-row">
                            <span class="arch-card-price">${escapeHtml(priceStr)}</span>
                            <a href="${escapeHtml(it.url || '#')}" target="_blank" rel="noopener noreferrer" class="arch-card-link-btn" title="在新标签页查看官网商品详情">
                                官网 ↗
                            </a>
                        </div>
                        <div class="arch-card-actions">
                            ${isStock ? `
                                <button type="button" class="btn-card-action btn-card-cart" data-id="${escapeHtml(it.id)}" title="在新标签页直接将此商品加入您的浏览器网页版购物车">
                                    🛒 加购物车
                                </button>
                                <button type="button" class="btn-card-action btn-card-buy ${qbBtnClass}" data-id="${escapeHtml(it.id)}" data-title="${escapeHtml(it.title || '')}" data-price="${escapeHtml(priceStr)}" title="${qbBtnTitle}">
                                    ⚡ 一键购买
                                </button>
                            ` : `
                                <button type="button" class="btn-card-action btn-card-disabled" disabled title="该商品当前已售罄，无法加购">
                                    ⚪ 已售罄
                                </button>
                            `}
                        </div>
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

function initArchiveModule() {
    // 档案库一键下单独立开关与仿真模式绑定
    if (elements.quickBuyEnabled) {
        elements.quickBuyEnabled.addEventListener('change', () => {
            if (!currentConfig.quick_buy) currentConfig.quick_buy = {};
            currentConfig.quick_buy.enabled = elements.quickBuyEnabled.checked;
            currentConfig.quick_buy.dry_run = elements.quickBuyDryRun ? elements.quickBuyDryRun.checked : true;
            saveConfig(true);
            updateQuickBuyStatusTag();
            showToast(elements.quickBuyEnabled.checked ? '已启动一键下单功能' : '已关闭一键下单功能', 'info');
            if (archiveState.lastItems && archiveState.lastItems.length > 0) {
                renderArchiveProducts(archiveState.lastItems);
            }
        });
    }

    if (elements.quickBuyDryRun) {
        elements.quickBuyDryRun.addEventListener('change', () => {
            const isChecked = elements.quickBuyDryRun.checked;
            if (!isChecked && elements.quickBuyEnabled?.checked) {
                const confirmed = confirm(
                    "⚠️ 重要安全提示：\n\n" +
                    "您正在关闭一键下单的 Dry-Run (安全仿真测试) 模式！\n" +
                    "关闭后，点击商品卡片上的【⚡ 一键购买】将直接向 Dollyteria 官网提交真实订单购买！\n\n" +
                    "确定要切换为真实购买下单模式吗？"
                );
                if (!confirmed) {
                    elements.quickBuyDryRun.checked = true;
                    return;
                }
            }
            if (!currentConfig.quick_buy) currentConfig.quick_buy = {};
            currentConfig.quick_buy.dry_run = elements.quickBuyDryRun.checked;
            saveConfig(true);
            updateQuickBuyStatusTag();
            showToast(elements.quickBuyDryRun.checked ? '已切换为一键模拟下单' : '⚠️ 已切换为一键真实下单', elements.quickBuyDryRun.checked ? 'info' : 'warning');
            if (archiveState.lastItems && archiveState.lastItems.length > 0) {
                renderArchiveProducts(archiveState.lastItems);
            }
        });
    }

    // 启动采集
    const btnStart = document.getElementById('btn-crawler-start');
    if (btnStart) {
        btnStart.addEventListener('click', async () => {
            const delaySelect = document.getElementById('crawler-delay-select');
            const startPageInput = document.getElementById('crawler-start-page');
            const delay = delaySelect ? parseFloat(delaySelect.value) : 1.2;
            const startPageVal = startPageInput ? startPageInput.value.trim() : '';

            try {
                showToast('正在启动全站后台采集...', 'info');
                const res = await fetch('/api/archive/crawler/start', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        delay: delay,
                        start_page: startPageVal ? parseInt(startPageVal) : null,
                    }),
                });
                const data = await res.json();
                showToast(data.message || '采集已启动');
                pollArchiveCrawlerStatus();
            } catch (err) {
                showToast('启动采集失败: ' + err.message, 'error');
            }
        });
    }

    // 暂停采集
    const btnPause = document.getElementById('btn-crawler-pause');
    if (btnPause) {
        btnPause.addEventListener('click', async () => {
            try {
                const res = await fetch('/api/archive/crawler/pause', { method: 'POST' });
                const data = await res.json();
                showToast(data.message || '采集已暂停');
                pollArchiveCrawlerStatus();
            } catch (err) {
                showToast('暂停采集失败: ' + err.message, 'error');
            }
        });
    }

    // 继续采集
    const btnResume = document.getElementById('btn-crawler-resume');
    if (btnResume) {
        btnResume.addEventListener('click', async () => {
            try {
                const res = await fetch('/api/archive/crawler/resume', { method: 'POST' });
                const data = await res.json();
                showToast(data.message || '采集已恢复');
                pollArchiveCrawlerStatus();
            } catch (err) {
                showToast('恢复采集失败: ' + err.message, 'error');
            }
        });
    }

    // 停止采集
    const btnStop = document.getElementById('btn-crawler-stop');
    if (btnStop) {
        btnStop.addEventListener('click', async () => {
            if (!confirm('确定停止当前的爬取任务吗？已抓取的数据会安全保留在本地 SQLite 数据库中。')) return;
            try {
                const res = await fetch('/api/archive/crawler/stop', { method: 'POST' });
                const data = await res.json();
                showToast(data.message || '采集已停止');
                pollArchiveCrawlerStatus();
            } catch (err) {
                showToast('停止采集失败: ' + err.message, 'error');
            }
        });
    }

    // 立即同步官网最新上新流 (/new) 至档案库
    const btnSyncNewStream = document.getElementById('btn-sync-new-stream');
    if (btnSyncNewStream) {
        btnSyncNewStream.addEventListener('click', async () => {
            const maxPages = currentConfig?.schedule?.max_stream_pages || 5;
            try {
                btnSyncNewStream.disabled = true;
                btnSyncNewStream.innerHTML = '<span>⏳ 正在拉取新品档案...</span>';
                showToast(`正在高速拉取官网 /new 最新上新流 (前 ${maxPages * 100} 件新品)，请稍候...`, 'info');
                const res = await fetch('/api/archive/sync_new_stream', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ max_pages: maxPages })
                });
                const data = await res.json();
                if (data.status === 'ok') {
                    showToast(data.message, 'success');
                    pollArchiveCrawlerStatus();
                    loadArchiveProducts(1);
                } else {
                    showToast(data.message || '同步上新流失败', 'error');
                }
            } catch (err) {
                showToast('同步上新流网络异常: ' + err.message, 'error');
            } finally {
                btnSyncNewStream.disabled = false;
                btnSyncNewStream.innerHTML = '<span>⚡ 同步最新上新至档案</span>';
            }
        });
    }

    // 立即执行在售库存盘点与售罄更新
    const btnSyncStock = document.getElementById('btn-sync-stock-now');
    if (btnSyncStock) {
        btnSyncStock.addEventListener('click', async () => {
            if (!confirm('确定立即启动在库商品全量盘点吗？\n系统将以轻量模式抓取官网在售清单并做差集对比，自动将已被买走/下架的商品精准标记为已售罄 (仅需约 8MB 流量，耗时约 3~4 分钟)。')) return;
            try {
                btnSyncStock.disabled = true;
                const res = await fetch('/api/archive/sync_stock', { method: 'POST' });
                const data = await res.json();
                if (data.status === 'ok') {
                    showToast('已启动在售库存盘点！正在后台扫描对齐售罄数据...', 'success');
                } else {
                    showToast(data.message || '启动盘点失败', 'warning');
                }
                pollArchiveCrawlerStatus();
            } catch (err) {
                showToast('启动盘点异常: ' + err.message, 'error');
            } finally {
                btnSyncStock.disabled = false;
            }
        });
    }

    // 同步档案库在售商品为监控基准
    const btnSyncBaseline = document.getElementById('btn-sync-baseline-from-archive');
    if (btnSyncBaseline) {
        btnSyncBaseline.addEventListener('click', async () => {
            if (!confirm('确定将档案数据库中所有在售商品同步为监控基准线吗？\n同步后，当前所有库存商品都不会触发报警，后续仅有全新上架或新补货时才会提醒。')) return;
            try {
                showToast('正在同步监控基准线...', 'info');
                const res = await fetch('/api/archive/apply_baseline', { method: 'POST' });
                const data = await res.json();
                showToast(data.message || '基准线同步成功！');
                if (typeof fetchStatus === 'function') fetchStatus();
            } catch (err) {
                showToast('同步基准线失败: ' + err.message, 'error');
            }
        });
    }

    // 关键词搜索
    const searchInput = document.getElementById('archive-search-kw');
    const searchBtn = document.getElementById('btn-archive-search');
    const clearBtn = document.getElementById('btn-archive-clear-search');

    if (searchInput) {
        searchInput.addEventListener('input', () => {
            if (clearBtn) clearBtn.style.display = searchInput.value ? 'block' : 'none';
        });
        searchInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                archiveState.keyword = searchInput.value.trim();
                loadArchiveProducts(1);
            }
        });
    }

    if (searchBtn && searchInput) {
        searchBtn.addEventListener('click', () => {
            archiveState.keyword = searchInput.value.trim();
            loadArchiveProducts(1);
        });
    }

    if (clearBtn && searchInput) {
        clearBtn.addEventListener('click', () => {
            searchInput.value = '';
            clearBtn.style.display = 'none';
            archiveState.keyword = '';
            loadArchiveProducts(1);
        });
    }

    // 库存状态过滤 Chip
    const chips = document.querySelectorAll('#filter-stock-chips .filter-chip');
    chips.forEach(chip => {
        chip.addEventListener('click', () => {
            chips.forEach(c => c.classList.remove('active'));
            chip.classList.add('active');
            archiveState.inStock = chip.getAttribute('data-stock') || 'all';
            loadArchiveProducts(1);
        });
    });

    // 新品状态过滤 Chip
    const newChips = document.querySelectorAll('#filter-new-chips .filter-chip');
    newChips.forEach(chip => {
        chip.addEventListener('click', () => {
            newChips.forEach(c => c.classList.remove('active'));
            chip.classList.add('active');
            archiveState.isNew = chip.getAttribute('data-new') || 'all';
            loadArchiveProducts(1);
        });
    });

    // 初始化预设关键词模块
    initPresetKeywords();

    // 排序变更
    const sortSelect = document.getElementById('archive-sort-select');
    if (sortSelect) {
        sortSelect.addEventListener('change', (e) => {
            archiveState.sortBy = e.target.value;
            loadArchiveProducts(1);
        });
    }

    // 分页上一页 / 下一页
    const btnPrev = document.getElementById('btn-arch-prev');
    const btnNext = document.getElementById('btn-arch-next');
    if (btnPrev) {
        btnPrev.addEventListener('click', () => {
            if (archiveState.page > 1) {
                loadArchiveProducts(archiveState.page - 1);
            }
        });
    }
    if (btnNext) {
        btnNext.addEventListener('click', () => {
            if (archiveState.page < archiveState.totalPages) {
                loadArchiveProducts(archiveState.page + 1);
            }
        });
    }

    // 页码跳转
    const jumpInput = document.getElementById('input-arch-jump');
    const jumpBtn = document.getElementById('btn-arch-jump');
    const doJump = () => {
        if (!jumpInput) return;
        const val = parseInt(jumpInput.value);
        if (!isNaN(val) && val >= 1 && val <= archiveState.totalPages) {
            loadArchiveProducts(val);
        } else {
            showToast(`请输入 1 到 ${archiveState.totalPages} 之间的有效页码`, 'warning');
        }
    };
    if (jumpBtn) jumpBtn.addEventListener('click', doJump);
    if (jumpInput) {
        jumpInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                doJump();
            }
        });
    }

    // 绑定卡片上的「🛒 加购物车」与「⚡ 一键购买」点击事件代理
    const productsGrid = document.getElementById('archive-products-grid');
    if (productsGrid) {
        productsGrid.addEventListener('click', (e) => {
            const btnBuy = e.target.closest('.btn-card-buy');
            if (btnBuy) {
                e.preventDefault();
                e.stopPropagation();
                const id = btnBuy.getAttribute('data-id');
                const title = btnBuy.getAttribute('data-title');
                const price = btnBuy.getAttribute('data-price');
                quickBuyItem(id, title, price, btnBuy);
                return;
            }

            const btnCart = e.target.closest('.btn-card-cart');
            if (btnCart) {
                e.preventDefault();
                e.stopPropagation();
                const id = btnCart.getAttribute('data-id');
                addToWebCart(id);
                return;
            }
        });
    }

    // 绑定实时发现流卡片上的「⚡ 一键购买」点击事件代理
    if (elements.feedContainer) {
        elements.feedContainer.addEventListener('click', (e) => {
            const btnBuy = e.target.closest('.btn-feed-quick-buy');
            if (btnBuy) {
                e.preventDefault();
                e.stopPropagation();
                const id = btnBuy.getAttribute('data-id');
                const title = btnBuy.getAttribute('data-title');
                const price = btnBuy.getAttribute('data-price');
                quickBuyItem(id, title, price, btnBuy);
            }
        });
    }

    // 绑定购买结果弹窗关闭
    const buyModal = document.getElementById('buy-modal');
    const buyModalClose = document.getElementById('buy-modal-close');
    const buyModalOk = document.getElementById('buy-modal-ok');
    const closeBuyModal = () => { if (buyModal) buyModal.style.display = 'none'; };
    if (buyModalClose) buyModalClose.addEventListener('click', closeBuyModal);
    if (buyModalOk) buyModalOk.addEventListener('click', closeBuyModal);
    if (buyModal) {
        buyModal.addEventListener('click', (e) => {
            if (e.target === buyModal) closeBuyModal();
        });
    }
}

// 直接提交表单至 Dollyteria 官网，将商品 100% 同步加入当前浏览器的网页版购物车
function addToWebCart(itemId) {
    itemId = String(itemId).trim();
    if (!itemId) return;

    // 动态创建隐式表单提交至官网产品页
    const form = document.createElement('form');
    form.action = `https://www.dollyteria.com/product/${itemId}`;
    form.method = 'POST';
    form.target = '_blank';
    form.style.display = 'none';

    const inpAdd = document.createElement('input');
    inpAdd.type = 'hidden';
    inpAdd.name = `cart_add_${itemId}`;
    inpAdd.value = 't';
    form.appendChild(inpAdd);

    const inpPage = document.createElement('input');
    inpPage.type = 'hidden';
    inpPage.name = 'cart_operation_page';
    inpPage.value = 'product';
    form.appendChild(inpPage);

    const inpQty = document.createElement('input');
    inpQty.type = 'hidden';
    inpQty.name = `cart_addquantity_${itemId}`;
    inpQty.value = '1';
    form.appendChild(inpQty);

    const inpBtn = document.createElement('input');
    inpBtn.type = 'hidden';
    inpBtn.name = 'cart_add_btn';
    inpBtn.value = 'カートに入れる';
    form.appendChild(inpBtn);

    document.body.appendChild(form);
    form.submit();
    setTimeout(() => form.remove(), 800);

    showToast(`🛒 已向 Dollyteria 官网提交加购请求！正在新标签页打开您的网页版购物车...`, 'success');
}

// 将指定商品卡片标记为售罄状态 (同步水印遮罩、售罄徽章与按钮禁用)
function markFeedProductSoldOut(itemId) {
    itemId = String(itemId).trim();
    if (!itemId) return;

    // 1. 查找上新实时流中的卡片
    const cards = document.querySelectorAll(`.product-card[data-id="${itemId}"], .btn-feed-quick-buy[data-id="${itemId}"]`);
    cards.forEach(el => {
        const card = el.classList.contains('product-card') ? el : el.closest('.product-card');
        if (!card) return;

        card.classList.add('is-sold-out');

        // 添加 SoldOut 水印遮罩 (若尚未存在)
        const imgWrap = card.querySelector('.product-img-wrapper');
        if (imgWrap && !imgWrap.querySelector('.product-soldout-overlay')) {
            const overlay = document.createElement('div');
            overlay.className = 'product-soldout-overlay';
            overlay.innerHTML = '<div class="soldout-watermark-text">SoldOut 売切</div>';
            imgWrap.prepend(overlay);
        }

        // 更新徽章状态
        const badge = card.querySelector('.product-stock-badge');
        if (badge) {
            badge.className = 'product-stock-badge is-sold-out';
            badge.textContent = '● 售罄';
        }

        const timeTag = card.querySelector('.product-time-tag');
        if (timeTag) {
            timeTag.className = 'product-time-tag is-sold-out';
            timeTag.textContent = '已售罄';
        }

        // 禁用并更新一键购买按钮
        const btnBuy = card.querySelector('.btn-feed-quick-buy');
        if (btnBuy) {
            btnBuy.disabled = true;
            btnBuy.classList.remove('btn-warning');
            btnBuy.classList.add('btn-secondary');
            btnBuy.textContent = '已售罄';
            btnBuy.title = '官网该商品已售罄';
        }
    });

    // 2. 同时若在全站档案卡片流中，也联动打上售罄状态并替换底部按钮为「⚪ 已售罄」
    const archCards = document.querySelectorAll('.arch-card');
    archCards.forEach(card => {
        const cardId = card.getAttribute('data-id');
        const link = card.querySelector(`a[href*="/product/${itemId}"]`);
        const buyBtn = card.querySelector(`.btn-card-buy[data-id="${itemId}"]`);
        if (cardId === itemId || link || buyBtn) {
            card.classList.add('is-sold-out');
            const thumbWrap = card.querySelector('.arch-card-thumb-wrap');
            if (thumbWrap && !thumbWrap.querySelector('.arch-soldout-overlay')) {
                const overlay = document.createElement('div');
                overlay.className = 'arch-soldout-overlay';
                overlay.innerHTML = '<div class="soldout-watermark-text">SoldOut 売切</div>';
                thumbWrap.prepend(overlay);
            }
            const actionsWrap = card.querySelector('.arch-card-actions');
            if (actionsWrap) {
                actionsWrap.innerHTML = `
                    <button type="button" class="btn-card-action btn-card-disabled" disabled title="该商品当前已售罄，无法加购">
                        ⚪ 已售罄
                    </button>
                `;
            }
        }
    });

    // 3. 同步更新前端内存中的档案列表与历史列表缓存，防止重绘时回退为在售
    if (Array.isArray(archiveState.lastItems)) {
        archiveState.lastItems.forEach(it => {
            if (String(it.id) === itemId) {
                it.is_in_stock = 0;
            }
        });
    }
    if (Array.isArray(currentHistory)) {
        currentHistory.forEach(it => {
            if (String(it.id) === itemId) {
                it.is_in_stock = false;
            }
        });
    }
}

// 调用后台自动下单接口执行一键购买
async function quickBuyItem(itemId, itemTitle, itemPrice, btnEl) {
    itemId = String(itemId).trim();
    if (!itemId) return;

    // 检查档案库一键下单独立开关
    const qbCfg = currentConfig?.quick_buy || {};
    if (!qbCfg.enabled) {
        showToast('一键下单功能未开启，请先在上方「一键下单设置」开启【启用一键下单开关】', 'warning');
        return;
    }

    const aoCfg = currentConfig?.auto_order || {};
    if (!aoCfg.username || !aoCfg.password) {
        showToast('请先在「🛒 自动下单接口」标签页配置 Dollyteria 账号邮箱与登录密码', 'warning');
        return;
    }

    // 独立使用 quick_buy 的仿真模式设置，完全与 auto_order.dry_run 隔离
    const isDry = qbCfg.dry_run !== false;
    const payMethod = aoCfg.payment_method || 'cod';
    let payName = '代金引換 (货到付款)';
    if (payMethod === 'store_pickup' || payMethod === 'store') payName = '店頭支払 (到店付款)';
    else if (payMethod === 'bank_transfer' || payMethod === 'bank' || payMethod === '銀行振込') payName = '銀行振込 (银行汇款)';

    const modeTip = isDry
        ? '【🛡️ Dry-Run 仿真测试模式】\n系统将自动模拟登录、秒级加购、选择地址与支付方式全流程，并在最终购买确认前安全拦截，提取详细费用明细，绝对不会扣款或产生订单！'
        : '【🚨 真实下单模式 (警告)】\n系统将以您的账号自动在 Dollyteria 官网提交真实订单购买！';

    const confirmMsg = `确定要对以下商品发起一键购买吗？\n\n` +
        `📦 商品名称: ${itemTitle}\n` +
        `🏷️ 商品编号: #${itemId} | 价格: ${itemPrice}\n` +
        `💳 锁定支付: 【${payName}】\n` +
        `⚙️ 当前模式: ${modeTip}`;

    if (!confirm(confirmMsg)) return;

    const originalText = btnEl ? btnEl.innerHTML : '';
    if (btnEl) {
        btnEl.disabled = true;
        btnEl.innerHTML = '⏳ 购买中...';
    }

    showToast(`正在对商品 #${itemId} 执行自动下单流水线，请稍候...`, 'info');

    let markedSoldOut = false;
    try {
        const res = await fetch('/api/auto_order/buy', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                item_id: itemId,
                item_title: itemTitle,
                dry_run: isDry,
                payment_method: payMethod,
            }),
        });

        const data = await res.json();
        if (data.success) {
            // 若为真实下单购买成功，该孤品已被本账号买断，立即将前端卡片置为 SoldOut 已售罄状态
            if (!isDry) {
                markFeedProductSoldOut(itemId);
                markedSoldOut = true;
            }
            showToast(isDry ? '🎉 仿真测试成功！已顺利抵达最终确认页' : '🎉 真实下单购买成功！商品已标记为售罄', 'success');
            showBuyResultModal(data, itemTitle, itemId);
        } else {
            // 若发现已售罄，立即将前端卡片置为 SoldOut 状态
            if (data.is_sold_out) {
                markFeedProductSoldOut(itemId);
                markedSoldOut = true;
            }
            showToast(data.message || '购买流程中断，请检查账号或库存状态', 'error');
            if (data.steps_log) {
                showBuyResultModal(data, itemTitle, itemId);
            }
        }
    } catch (err) {
        showToast('发起自动购买异常: ' + err.message, 'error');
    } finally {
        if (btnEl && !markedSoldOut && document.body.contains(btnEl)) {
            btnEl.disabled = false;
            btnEl.innerHTML = originalText;
        }
    }
}

// 渲染并展示一键购买明细弹窗
function showBuyResultModal(data, itemTitle, itemId) {
    const modal = document.getElementById('buy-modal');
    const modalTitle = document.getElementById('buy-modal-title');
    const modalBody = document.getElementById('buy-modal-body');
    if (!modal || !modalBody) return;

    const isSuccess = Boolean(data.success);
    const isDry = Boolean(data.dry_run);
    const d = data.details || {};
    const stepsHtml = (data.steps_log || []).map(s => `<li>${escapeHtml(s)}</li>`).join('');

    if (modalTitle) {
        modalTitle.textContent = isSuccess
            ? (isDry ? '🛡️ 一键购买仿真测试成功 (Dry-Run)' : '🎉 一键下单成功 (真实订单)')
            : '❌ 一键购买执行受阻';
    }

    let summaryHtml = '';
    if (isSuccess && data.details) {
        summaryHtml = `
            <div class="ao-grid-summary">
                <div class="ao-summary-item">
                    <div class="label">目标商品</div>
                    <div class="value">${escapeHtml(d.items && d.items[0] ? d.items[0] : (itemTitle || '#' + itemId))}</div>
                </div>
                <div class="ao-summary-item">
                    <div class="label">支付方式 (已锁定)</div>
                    <div class="value" style="color:#34d399;">${escapeHtml(d.payment_method || '代金引換')}</div>
                </div>
                <div class="ao-summary-item">
                    <div class="label">商品小计</div>
                    <div class="value">${escapeHtml(d.subtotal || '--')}</div>
                </div>
                <div class="ao-summary-item">
                    <div class="label">配送运费</div>
                    <div class="value">${escapeHtml(d.shipping_fee || '--')}</div>
                </div>
                <div class="ao-summary-item">
                    <div class="label">手续费</div>
                    <div class="value">${escapeHtml(d.cod_fee || '--')}</div>
                </div>
                <div class="ao-summary-item">
                    <div class="label">最终结算总额</div>
                    <div class="value" style="color:#fbbf24;font-size:15px;font-weight:700;">${escapeHtml(d.total_price || '--')}</div>
                </div>
            </div>
            <div style="font-size:12px;color:var(--text-secondary);margin:8px 0 12px 0;">
                <strong>收货人与地址：</strong>${escapeHtml(d.recipient_name || '')} • ${escapeHtml(d.recipient_address || '')}
            </div>
            ${isDry ? `
                <div class="ao-safety-banner" style="margin-bottom:12px;">
                    🛡️ <strong>安全拦截机制已生效：</strong>程序已在最终「購入する」购买按钮前自动拦截，未产生任何实际扣款或订单！测试商品已自动从购物车移出清空。
                </div>
            ` : `
                <div class="ao-safety-banner" style="margin-bottom:12px; background:rgba(16,185,129,0.15); border-color:#10b981;">
                    🎉 <strong>真实订单已成功提交！</strong>请前往 Dollyteria 官网会员中心或注册邮箱查收订单确认邮件。
                </div>
            `}
        `;
    } else {
        summaryHtml = `
            <div style="color:#f87171;font-size:13px;margin-bottom:12px;padding:10px;background:rgba(239,68,68,0.1);border-radius:6px;border:1px solid rgba(239,68,68,0.2);">
                ${escapeHtml(data.message || '下单流程受阻，未能抵达最终确认页')}
            </div>
        `;
    }

    modalBody.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid rgba(255,255,255,0.08);">
            <span style="font-weight:700;font-size:13px;color:${isSuccess ? '#34d399' : '#f87171'};">
                ${isSuccess ? '✅ 全自动结算链路推进成功' : '❌ 步骤 [' + escapeHtml(data.step || 'unknown') + '] 遇到阻碍'}
            </span>
            <span style="font-size:12px;color:var(--text-muted);">耗时: ${data.elapsed_sec || 0}s</span>
        </div>
        ${summaryHtml}
        <div style="font-size:12px;font-weight:600;color:var(--text-secondary);margin-bottom:6px;">执行流水线步骤日志：</div>
        <ul class="ao-steps-list" style="max-height:160px;overflow-y:auto;">
            ${stepsHtml}
        </ul>
    `;

    modal.style.display = 'flex';
}

// 页面加载启动
document.addEventListener('DOMContentLoaded', async () => {
    try { initTimeSelects(); } catch (e) { console.error('initTimeSelects err:', e); }
    try { initTabs(); } catch (e) { console.error('initTabs err:', e); }
    try { bindEvents(); } catch (e) { console.error('bindEvents err:', e); }
    try {
        const isAuthed = await checkAuthStatus();
        if (isAuthed) {
            initMainApp();
        }
    } catch (e) {
        console.error('checkAuthStatus err:', e);
    }
});


/* ==========================================================================
   实时运行终端模块 (Real-time Terminal Module)
   ========================================================================== */

let terminalLimit = 100;
let terminalLevel = 'ALL';
let terminalKeyword = '';
let terminalAutoScroll = true;
let isUserScrolledUp = false;
let terminalLogs = [];

function initTerminalModule() {
    const screen = document.getElementById('terminal-screen');
    const btnClear = document.getElementById('btn-term-clear');
    const btnCopy = document.getElementById('btn-term-copy');
    const pill = document.getElementById('term-scroll-pill');
    const autoScrollToggle = document.getElementById('term-autoscroll-toggle');
    const autoScrollText = document.getElementById('term-autoscroll-text');
    const levelSelect = document.getElementById('term-level-select');
    const searchInput = document.getElementById('term-search-input');
    const limitBtns = document.querySelectorAll('#term-limit-group .btn-segment');

    if (!screen) return;

    // 清屏
    if (btnClear) btnClear.addEventListener('click', () => clearTerminal());

    // 复制全部日志
    if (btnCopy) {
        btnCopy.addEventListener('click', () => {
            const text = terminalLogs.map(l => l.formatted || `[${l.time}] [${l.level}] ${l.logger}: ${l.message}`).join('\n');
            if (!text) {
                showToast('当前暂无可复制的终端日志', 'warning');
                return;
            }
            navigator.clipboard.writeText(text).then(() => {
                showToast('📋 已成功复制终端全部日志到剪贴板', 'success');
            }).catch(() => {
                showToast('复制失败，请手动选取文字', 'error');
            });
        });
    }

    // 自动滚屏切换
    if (autoScrollToggle) {
        autoScrollToggle.addEventListener('change', () => {
            terminalAutoScroll = autoScrollToggle.checked;
            if (autoScrollText) {
                autoScrollText.textContent = terminalAutoScroll ? '⏬ 自动滚屏: 开启' : '⏸️ 自动滚屏: 暂停';
            }
            if (terminalAutoScroll) {
                isUserScrolledUp = false;
                if (pill) pill.style.display = 'none';
                scrollTerminalToBottom();
            }
        });
    }

    // 监听滚轮上下滚动，智能检测是否向上查阅历史
    screen.addEventListener('scroll', () => {
        const distFromBottom = screen.scrollHeight - screen.scrollTop - screen.clientHeight;
        if (distFromBottom > 35) {
            isUserScrolledUp = true;
            if (pill && terminalAutoScroll) pill.style.display = 'block';
        } else {
            isUserScrolledUp = false;
            if (pill) pill.style.display = 'none';
        }
    });

    // 点击浮动提示条跳至底部
    if (pill) {
        pill.addEventListener('click', () => {
            isUserScrolledUp = false;
            pill.style.display = 'none';
            scrollTerminalToBottom(true);
        });
    }

    // 切换 100 / 300 / 1000 行限制
    limitBtns.forEach(b => {
        b.addEventListener('click', () => {
            limitBtns.forEach(btn => btn.classList.remove('active'));
            b.classList.add('active');
            const lim = parseInt(b.getAttribute('data-limit') || '100', 10);
            terminalLimit = lim;
            const maxLabel = document.getElementById('term-max-limit-label');
            if (maxLabel) maxLabel.textContent = String(lim);
            loadTerminalLogs();
        });
    });

    // 级别筛选
    if (levelSelect) {
        levelSelect.addEventListener('change', () => {
            terminalLevel = levelSelect.value;
            loadTerminalLogs();
        });
    }

    // 关键词筛选
    if (searchInput) {
        let searchTimer = null;
        searchInput.addEventListener('input', () => {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(() => {
                terminalKeyword = searchInput.value.trim();
                loadTerminalLogs();
            }, 250);
        });
    }

    // Ctrl+L 清屏快捷键
    window.addEventListener('keydown', (e) => {
        const activeTab = document.querySelector('.tab-btn.active')?.dataset.tab;
        if (activeTab === 'tab-terminal' && e.ctrlKey && e.key.toLowerCase() === 'l') {
            e.preventDefault();
            clearTerminal();
        }
    });

    // 页面启动时预载一次日志
    loadTerminalLogs();

    // 当用户停留在终端面板时，每 2 秒轮询更新日志
    setInterval(() => {
        const activeTab = document.querySelector('.tab-btn.active')?.dataset.tab;
        if (activeTab === 'tab-terminal') {
            loadTerminalLogs();
        }
    }, 2000);
}

async function loadTerminalLogs() {
    try {
        const url = `/api/terminal/logs?limit=${terminalLimit}&level=${terminalLevel}&keyword=${encodeURIComponent(terminalKeyword)}`;
        const res = await fetch(url);
        if (!res.ok) {
            console.error('加载终端日志接口异常, HTTP 状态码:', res.status);
            const screen = document.getElementById('terminal-screen');
            if (screen && (!terminalLogs || terminalLogs.length === 0)) {
                screen.innerHTML = `
                    <div class="term-welcome-line" style="color:#f87171;">
                        <span class="term-prefix">[WARNING]</span> 后台终端服务尚未就绪或正在重启更新中 (HTTP ${res.status})，正在重连...
                    </div>
                `;
            }
            return;
        }
        const data = await res.json();
        terminalLogs = data.logs || [];

        const bufBadge = document.getElementById('term-buffer-badge');
        if (bufBadge) bufBadge.textContent = `缓冲 ${data.total_buffered || 0} 行`;

        renderTerminalLogs();
        if (terminalAutoScroll && !isUserScrolledUp) {
            scrollTerminalToBottom(false);
        }
    } catch (e) {
        console.error('加载终端日志失败:', e);
    }
}

function renderTerminalLogs() {
    const screen = document.getElementById('terminal-screen');
    const countLabel = document.getElementById('term-rendered-count');
    if (!screen) return;

    if (!terminalLogs || terminalLogs.length === 0) {
        screen.innerHTML = `
            <div class="term-welcome-line">
                <span class="term-prefix">[SYSTEM]</span> 终端运行就绪。暂无匹配条件的日志输出。
            </div>
        `;
        if (countLabel) countLabel.textContent = '0';
        return;
    }

    let html = `
        <div class="term-welcome-line">
            <span class="term-prefix">[SYSTEM]</span> 🎀 Dollyteria 智能上新监控与自动下单引擎终端已连接。支持 100/300/1000 行切换与上下滚动查看。
        </div>
    `;

    terminalLogs.forEach(entry => {
        html += buildTerminalRowHtml(entry);
    });

    screen.innerHTML = html;
    if (countLabel) countLabel.textContent = String(terminalLogs.length);
}

function buildTerminalRowHtml(entry) {
    const lvl = (entry.level || 'INFO').toUpperCase();
    let lvlClass = 'term-lvl-info';
    let msgClass = '';
    if (lvl === 'WARN' || lvl === 'WARNING') {
        lvlClass = 'term-lvl-warn';
        msgClass = 'is-warn';
    } else if (lvl === 'ERROR' || lvl === 'CRITICAL') {
        lvlClass = 'term-lvl-error';
        msgClass = 'is-error';
    }

    const t = entry.time_short || (entry.time ? entry.time.slice(11, 19) : '--:--:--');
    const loggerName = entry.logger || 'root';
    const msg = escapeHtml(entry.message || '');

    return `
        <div class="term-row" data-id="${entry.id || 0}">
            <span class="term-time">[${t}]</span>
            <span class="term-lvl ${lvlClass}">${lvl.slice(0, 4)}</span>
            <span class="term-logger">[${escapeHtml(loggerName)}]</span>
            <span class="term-msg ${msgClass}">${msg}</span>
        </div>
    `;
}

function onNewTerminalLog(logEntry) {
    if (!logEntry) return;

    // 更新缓冲统计
    const bufBadge = document.getElementById('term-buffer-badge');
    if (bufBadge) {
        const cur = parseInt(bufBadge.textContent.replace(/\D/g, '') || '0', 10);
        bufBadge.textContent = `缓冲 ${cur + 1} 行`;
    }

    // 检查是否符合当前筛选条件
    if (terminalLevel !== 'ALL') {
        const lvl = (logEntry.level || '').toUpperCase();
        if (terminalLevel === 'WARN' && lvl !== 'WARNING' && lvl !== 'WARN') return;
        if (terminalLevel !== 'WARN' && lvl !== terminalLevel) return;
    }
    if (terminalKeyword) {
        const kw = terminalKeyword.toLowerCase();
        const msg = (logEntry.message || '').toLowerCase();
        const lgr = (logEntry.logger || '').toLowerCase();
        if (!msg.includes(kw) && !lgr.includes(kw)) return;
    }

    terminalLogs.push(logEntry);
    if (terminalLogs.length > terminalLimit) {
        terminalLogs.shift();
    }

    const screen = document.getElementById('terminal-screen');
    const activeTab = document.querySelector('.tab-btn.active')?.dataset.tab;
    if (!screen || activeTab !== 'tab-terminal') return;

    // 追加到 DOM
    const rowDiv = document.createElement('div');
    rowDiv.innerHTML = buildTerminalRowHtml(logEntry);
    const child = rowDiv.firstElementChild;
    if (child) screen.appendChild(child);

    // 移除超出上限的多余旧节点
    const rows = screen.querySelectorAll('.term-row');
    if (rows.length > terminalLimit) {
        for (let i = 0; i < rows.length - terminalLimit; i++) {
            rows[i].remove();
        }
    }

    const countLabel = document.getElementById('term-rendered-count');
    if (countLabel) countLabel.textContent = String(Math.min(rows.length, terminalLimit));

    if (terminalAutoScroll) {
        if (!isUserScrolledUp) {
            scrollTerminalToBottom(false);
        } else {
            const pill = document.getElementById('term-scroll-pill');
            if (pill) pill.style.display = 'block';
        }
    }
}

function scrollTerminalToBottom(smooth = false) {
    const screen = document.getElementById('terminal-screen');
    if (!screen) return;
    if (smooth) {
        screen.scrollTo({ top: screen.scrollHeight, behavior: 'smooth' });
    } else {
        screen.scrollTop = screen.scrollHeight;
    }
}

async function clearTerminal() {
    try {
        await fetch('/api/terminal/clear', { method: 'POST' });
        terminalLogs = [];
        const screen = document.getElementById('terminal-screen');
        if (screen) {
            screen.innerHTML = `
                <div class="term-welcome-line">
                    <span class="term-prefix">[SYSTEM]</span> 终端日志已清屏。
                </div>
            `;
        }
        const countLabel = document.getElementById('term-rendered-count');
        if (countLabel) countLabel.textContent = '0';
        showToast('终端显示与日志缓冲已清空', 'info');
    } catch (e) {
        console.error('清空终端异常:', e);
    }
}


/* ==========================================================================
   购买记录模块 (Orders & Purchase Records Module)
   ========================================================================== */

let ordersCurrentPage = 1;
const ordersPageSize = 15;
let ordersFilterStatus = 'all';
let ordersFilterType = 'all';
let ordersFilterKeyword = '';

function initOrdersModule() {
    const statusSelect = document.getElementById('filter-order-status');
    const typeSelect = document.getElementById('filter-order-type');
    const kwInput = document.getElementById('filter-order-keyword');
    const btnRefresh = document.getElementById('btn-refresh-orders');
    const btnExport = document.getElementById('btn-export-orders');
    const btnClear = document.getElementById('btn-clear-orders');
    const detailModal = document.getElementById('order-detail-modal');
    const detailClose = document.getElementById('order-detail-close');
    const detailOk = document.getElementById('order-detail-btn-ok');

    if (statusSelect) {
        statusSelect.addEventListener('change', () => {
            ordersFilterStatus = statusSelect.value;
            ordersCurrentPage = 1;
            loadOrders();
        });
    }

    if (typeSelect) {
        typeSelect.addEventListener('change', () => {
            ordersFilterType = typeSelect.value;
            ordersCurrentPage = 1;
            loadOrders();
        });
    }

    if (kwInput) {
        let kwTimer = null;
        kwInput.addEventListener('input', () => {
            clearTimeout(kwTimer);
            kwTimer = setTimeout(() => {
                ordersFilterKeyword = kwInput.value.trim();
                ordersCurrentPage = 1;
                loadOrders();
            }, 300);
        });
    }

    if (btnRefresh) {
        btnRefresh.addEventListener('click', () => {
            loadOrders();
            loadOrderStats();
            showToast('已刷新购买记录流水', 'info');
        });
    }

    if (btnExport) {
        btnExport.addEventListener('click', () => {
            window.location.href = '/api/orders/export?format=csv';
        });
    }

    if (btnClear) {
        btnClear.addEventListener('click', async () => {
            if (!confirm('确定要清空全部购买与抢购历史记录吗？此操作无法撤销。')) return;
            try {
                const res = await fetch('/api/orders/clear', { method: 'POST' });
                const d = await res.json();
                if (d.status === 'ok') {
                    showToast('已成功清空购买记录', 'success');
                    loadOrders();
                    loadOrderStats();
                } else {
                    showToast(d.message || '清空失败', 'error');
                }
            } catch (e) {
                showToast('清空请求异常: ' + e.message, 'error');
            }
        });
    }

    if (detailClose) detailClose.addEventListener('click', () => { if (detailModal) detailModal.style.display = 'none'; });
    if (detailOk) detailOk.addEventListener('click', () => { if (detailModal) detailModal.style.display = 'none'; });
    if (detailModal) {
        detailModal.addEventListener('click', (e) => {
            if (e.target === detailModal) detailModal.style.display = 'none';
        });
    }
}

async function loadOrderStats() {
    try {
        const res = await fetch('/api/orders/stats');
        if (!res.ok) return;
        const s = await res.json();

        const elTotal = document.getElementById('stat-orders-total');
        const elSuccess = document.getElementById('stat-orders-success');
        const elDry = document.getElementById('stat-orders-dry-run');
        const elSoldout = document.getElementById('stat-orders-soldout');
        const elSpent = document.getElementById('stat-orders-spent');

        if (elTotal) elTotal.textContent = String(s.total || 0);
        if (elSuccess) elSuccess.textContent = String(s.success_count || 0);
        if (elDry) elDry.textContent = String(s.dry_run_count || 0);
        if (elSoldout) elSoldout.textContent = String(s.soldout_count || 0);
        if (elSpent) {
            const spent = parseInt(s.total_spent || 0, 10);
            elSpent.textContent = `￥${spent.toLocaleString()}`;
        }
    } catch (e) {
        console.error('加载订单统计异常:', e);
    }
}

async function loadOrders() {
    const container = document.getElementById('orders-container');
    const pagination = document.getElementById('orders-pagination');
    if (!container) return;

    try {
        const url = `/api/orders?status=${ordersFilterStatus}&trigger_type=${ordersFilterType}&keyword=${encodeURIComponent(ordersFilterKeyword)}&page=${ordersCurrentPage}&page_size=${ordersPageSize}`;
        const res = await fetch(url);
        if (!res.ok) return;
        const data = await res.json();
        const orders = data.orders || [];

        if (orders.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <div class="empty-icon">🛍️</div>
                    <div class="empty-title">暂未查询到符合条件的购买记录</div>
                    <div class="empty-desc">当监控规则自动触发加购下单、或在【全数据收录】中点击一键抢购时，详细流水与执行回执将记录于此处。</div>
                </div>
            `;
            if (pagination) pagination.style.display = 'none';
            return;
        }

        let html = '';
        orders.forEach(o => {
            html += buildOrderCardHtml(o);
        });
        container.innerHTML = html;

        // 绑定卡片上的按钮事件
        container.querySelectorAll('.btn-order-detail').forEach(btn => {
            btn.addEventListener('click', () => {
                const orderId = btn.getAttribute('data-id');
                const order = orders.find(x => x.id === orderId);
                if (order) openOrderDetailModal(order);
            });
        });

        container.querySelectorAll('.btn-order-delete').forEach(btn => {
            btn.addEventListener('click', async () => {
                const orderId = btn.getAttribute('data-id');
                if (!confirm(`确定要删除此条记录 #${orderId} 吗？`)) return;
                try {
                    const r = await fetch('/api/orders/delete', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ id: orderId }),
                    });
                    const d = await r.json();
                    if (d.status === 'ok') {
                        showToast('记录已删除', 'info');
                        loadOrders();
                        loadOrderStats();
                    }
                } catch (e) {
                    showToast('删除失败: ' + e.message, 'error');
                }
            });
        });

        // 渲染分页
        renderOrdersPagination(data.total, data.page, data.total_pages);
    } catch (e) {
        console.error('加载订单列表失败:', e);
    }
}

function buildOrderCardHtml(o) {
    const isDry = Boolean(o.dry_run);
    const status = o.status || 'FAILED';

    let statusBadge = '';
    if (status === 'SUCCESS') {
        statusBadge = `<span class="badge-order-status status-success">🟢 实盘下单成功</span>`;
    } else if (status === 'DRY_RUN_SUCCESS') {
        statusBadge = `<span class="badge-order-status status-dryrun">🔵 仿真模拟成功</span>`;
    } else if (status === 'SOLDOUT') {
        statusBadge = `<span class="badge-order-status status-soldout">🛑 官方已售罄</span>`;
    } else {
        statusBadge = `<span class="badge-order-status status-failed">🔴 下单未完成</span>`;
    }

    let triggerTag = '';
    if (o.trigger_type === 'auto') {
        triggerTag = `<span class="badge-tag" style="background:rgba(139,92,246,0.15);color:#a78bfa;">📌 监控自动抢购: ${escapeHtml(o.rule_name || '默认规则')}</span>`;
    } else if (o.trigger_type === 'quick_buy') {
        triggerTag = `<span class="badge-tag" style="background:rgba(236,72,153,0.15);color:#f472b6;">⚡ 看板一键抢购</span>`;
    } else {
        triggerTag = `<span class="badge-tag" style="background:rgba(56,189,248,0.15);color:#38bdf8;">🧪 接口联调测试</span>`;
    }

    const imgUrl = o.img_url ? `/img_proxy?url=${encodeURIComponent(o.img_url)}` : '';
    const thumbHtml = imgUrl
        ? `<img src="${escapeHtml(imgUrl)}" class="order-thumb" alt="商品" loading="lazy" onerror="this.src='/static/favicon.ico'">`
        : `<span style="font-size:24px;">🧸</span>`;

    const priceText = o.price_str || (o.price ? `￥${Number(o.price).toLocaleString()}` : '--');
    const payMethodMap = { cod: '代金引換 (货到付款)', credit: 'クレジットカード', bank: '銀行振込', store: '店頭支払' };
    const payText = payMethodMap[o.payment_method] || o.payment_method || '默认方式';

    return `
        <div class="order-card" data-id="${o.id}">
            <div class="order-thumb-wrap">
                ${thumbHtml}
            </div>
            <div class="order-info">
                <div class="order-title-line">
                    ${statusBadge}
                    <a href="${escapeHtml(o.url || 'https://www.dollyteria.com/product/' + o.item_id)}" target="_blank" class="order-title" title="${escapeHtml(o.title)}">
                        [#${escapeHtml(o.item_id)}] ${escapeHtml(o.title)}
                    </a>
                </div>
                <div class="order-meta-line">
                    <span style="font-weight:700;color:#fbbf24;">${escapeHtml(priceText)}</span>
                    <span class="sep">•</span>
                    <span>${triggerTag}</span>
                    <span class="sep">•</span>
                    <span>支付: ${escapeHtml(payText)}</span>
                    <span class="sep">•</span>
                    <span>耗时: ⚡ ${o.duration_ms || 0}ms</span>
                    <span class="sep">•</span>
                    <span>${escapeHtml(o.time_str || '')}</span>
                </div>
                ${o.message ? `<div style="font-size:12px;color:var(--text-secondary);margin-top:4px;">反馈: ${escapeHtml(o.message)}</div>` : ''}
            </div>
            <div class="order-actions">
                <button class="btn btn-sm btn-ghost btn-order-detail" data-id="${o.id}" title="查看详细执行流水与官网回执">
                    <span>📄 详情回执</span>
                </button>
                <a href="${escapeHtml(o.url || 'https://www.dollyteria.com/product/' + o.item_id)}" target="_blank" class="btn btn-sm btn-secondary" title="在官网打开商品详情页">
                    <span>🌐 官网页面</span>
                </a>
                <button class="btn btn-sm btn-danger-soft btn-order-delete" data-id="${o.id}" title="删除此记录">
                    <span>🗑️</span>
                </button>
            </div>
        </div>
    `;
}

function renderOrdersPagination(total, page, totalPages) {
    const el = document.getElementById('orders-pagination');
    if (!el) return;
    if (totalPages <= 1) {
        el.style.display = 'none';
        return;
    }
    el.style.display = 'flex';
    let html = `
        <div class="pagination-info">共 ${total} 条记录，第 ${page} / ${totalPages} 页</div>
        <div class="pagination-btns">
            <button class="btn btn-xs btn-ghost" ${page <= 1 ? 'disabled' : ''} id="btn-order-page-prev">上一页</button>
            <span class="page-current">${page}</span>
            <button class="btn btn-xs btn-ghost" ${page >= totalPages ? 'disabled' : ''} id="btn-order-page-next">下一页</button>
        </div>
    `;
    el.innerHTML = html;

    const prev = document.getElementById('btn-order-page-prev');
    const next = document.getElementById('btn-order-page-next');
    if (prev && page > 1) {
        prev.addEventListener('click', () => {
            ordersCurrentPage--;
            loadOrders();
        });
    }
    if (next && page < totalPages) {
        next.addEventListener('click', () => {
            ordersCurrentPage++;
            loadOrders();
        });
    }
}

function openOrderDetailModal(order) {
    const modal = document.getElementById('order-detail-modal');
    const body = document.getElementById('order-detail-body');
    if (!modal || !body) return;

    const details = order.raw_details || {};
    const stepsLog = details.steps_log || [];

    let stepsHtml = '';
    if (stepsLog && stepsLog.length > 0) {
        stepsHtml = stepsLog.map(s => `<li>${escapeHtml(s)}</li>`).join('');
    } else {
        stepsHtml = `<li>${escapeHtml(order.message || '无额外流水日志')}</li>`;
    }

    body.innerHTML = `
        <div class="order-detail-grid">
            <div class="order-detail-key">订单流水号:</div>
            <div class="order-detail-val"><code>${escapeHtml(order.id)}</code></div>

            <div class="order-detail-key">商品名称与ID:</div>
            <div class="order-detail-val">
                <strong>[#${escapeHtml(order.item_id)}]</strong> ${escapeHtml(order.title)}
            </div>

            <div class="order-detail-key">下单状态:</div>
            <div class="order-detail-val">
                <strong>${escapeHtml(order.status)}</strong> (${order.dry_run ? '仿真模拟 DRY-RUN' : '实盘真实下单'})
            </div>

            <div class="order-detail-key">支付方式:</div>
            <div class="order-detail-val">${escapeHtml(order.payment_method || 'cod')}</div>

            <div class="order-detail-key">触发来源:</div>
            <div class="order-detail-val">${escapeHtml(order.rule_name || order.trigger_type || 'auto')}</div>

            <div class="order-detail-key">执行耗时:</div>
            <div class="order-detail-val">⚡ ${order.duration_ms || 0} ms (${order.retries || 0} 轮重试)</div>

            <div class="order-detail-key">记录时间:</div>
            <div class="order-detail-val">${escapeHtml(order.time_str || '')}</div>
        </div>

        <div style="font-size:12px;font-weight:600;color:var(--text-secondary);margin-bottom:6px;">抢购链路流水与官网返回:</div>
        <ul class="order-steps-log">
            ${stepsHtml}
        </ul>
    `;

    modal.style.display = 'flex';
}

function onNewOrderRecorded(order) {
    if (!order) return;
    const currentTab = document.querySelector('.tab-btn.active')?.dataset.tab;

    // Toast 提示
    const isSuccess = order.status === 'SUCCESS' || order.status === 'DRY_RUN_SUCCESS';
    const title = `🛍️ 下单结果: [${order.item_id}] ${order.status}`;
    showToast(title, isSuccess ? 'success' : 'warning');

    if (currentTab !== 'tab-orders') {
        const badge = document.getElementById('orders-badge');
        if (badge) {
            badge.style.display = 'inline-block';
            const count = parseInt(badge.textContent || '0', 10) + 1;
            badge.textContent = String(count);
        }
    } else {
        loadOrders();
        loadOrderStats();
    }
}


/* ==========================================================================
   监控规则快速检索与批量控制 (Watches Filter & Batch Controls)
   ========================================================================== */

function initWatchesFilter() {
    const input = document.getElementById('watch-filter-keyword');
    const btnEnableAll = document.getElementById('btn-batch-enable-watches');
    const btnDisableAll = document.getElementById('btn-batch-disable-watches');

    if (input) {
        input.addEventListener('input', () => {
            const kw = input.value.trim().toLowerCase();
            const cards = document.querySelectorAll('#watches-container .watch-card');
            cards.forEach(card => {
                const text = card.textContent.toLowerCase();
                card.style.display = (!kw || text.includes(kw)) ? '' : 'none';
            });
        });
    }

    if (btnEnableAll) {
        btnEnableAll.addEventListener('click', async () => {
            if (!currentConfig || !currentConfig.watches) return;
            currentConfig.watches.forEach(w => w.enabled = true);
            await saveConfig();
            renderWatches();
            showToast('已一键开启全部监控规则', 'success');
        });
    }

    if (btnDisableAll) {
        btnDisableAll.addEventListener('click', async () => {
            if (!currentConfig || !currentConfig.watches) return;
            currentConfig.watches.forEach(w => w.enabled = false);
            await saveConfig();
            renderWatches();
            showToast('已一键暂停全部监控规则', 'info');
        });
    }
}


/* ==========================================================================
   上新发现库存筛选与检索 (Feed Filter & Actions)
   ========================================================================== */

function initFeedFilter() {
    const filterBtns = document.querySelectorAll('#feed-stock-filter-group .btn-segment');
    const searchInput = document.getElementById('feed-search-input');
    const btnCheckNow = document.getElementById('btn-check-now-feed');

    filterBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            filterBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            applyFeedFilter();
        });
    });

    if (searchInput) {
        searchInput.addEventListener('input', () => applyFeedFilter());
    }

    if (btnCheckNow) {
        btnCheckNow.addEventListener('click', async () => {
            btnCheckNow.disabled = true;
            btnCheckNow.innerHTML = `<span>⏳ 检测中...</span>`;
            try {
                const res = await fetch('/api/check_now', { method: 'POST' });
                const d = await res.json();
                showToast(`检测完成: 扫描 ${d.scanned_count || 0} 件，命中 ${d.new_items_count || 0} 件`, 'info');
            } catch (e) {
                showToast('检测失败: ' + e.message, 'error');
            } finally {
                btnCheckNow.disabled = false;
                btnCheckNow.innerHTML = `<span>🔍 立即检测一次</span>`;
            }
        });
    }

}

function applyFeedFilter() {
    const activeFilterBtn = document.querySelector('#feed-stock-filter-group .btn-segment.active');
    const filterType = activeFilterBtn ? activeFilterBtn.getAttribute('data-filter') : 'all';
    const searchInput = document.getElementById('feed-search-input');
    const kw = searchInput ? searchInput.value.trim().toLowerCase() : '';

    const cards = document.querySelectorAll('#feed-container .product-card');
    cards.forEach(card => {
        let matchFilter = true;
        const isSoldOut = card.classList.contains('sold-out') || card.querySelector('.badge-sold-out');

        if (filterType === 'in_stock') {
            matchFilter = !isSoldOut;
        } else if (filterType === 'sold_out') {
            matchFilter = Boolean(isSoldOut);
        }

        let matchKw = true;
        if (kw) {
            matchKw = card.textContent.toLowerCase().includes(kw);
        }

        card.style.display = (matchFilter && matchKw) ? '' : 'none';
    });
}

// ==================== 预设关键词筛选与管理模块 ====================
async function initPresetKeywords() {
    // 1. 先尝试从 localStorage 恢复
    const localKw = localStorage.getItem('dollyteria_archive_preset_kw');
    if (localKw) {
        try {
            const parsed = JSON.parse(localKw);
            if (Array.isArray(parsed) && parsed.length > 0) {
                archiveState.presetKeywords = parsed;
            }
        } catch (e) {
            archiveState.presetKeywords = [];
        }
    }
    const localMode = localStorage.getItem('dollyteria_preset_match_mode');
    if (localMode === 'any' || localMode === 'all') {
        archiveState.presetMatchMode = localMode;
    }

    updatePresetModeUI();
    renderPresetKeywordChips();

    // 2. 从服务端获取最新同步的关键词列表并对齐
    try {
        const res = await fetch('/api/archive/preset_keywords');
        if (res.ok) {
            const data = await res.json();
            if (data.status === 'ok' && Array.isArray(data.keywords)) {
                archiveState.presetKeywords = data.keywords;
                localStorage.setItem('dollyteria_archive_preset_kw', JSON.stringify(data.keywords));
                renderPresetKeywordChips();
            }
        }
    } catch (err) {
        console.warn('获取服务端预设关键词失败，使用本地缓存:', err);
    }

    // 3. 组合模式切换 (AND / OR)
    const modeBtns = document.querySelectorAll('#preset-mode-switch .btn-mode');
    modeBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const mode = btn.getAttribute('data-mode') || 'all';
            if (archiveState.presetMatchMode !== mode) {
                archiveState.presetMatchMode = mode;
                localStorage.setItem('dollyteria_preset_match_mode', mode);
                updatePresetModeUI();
                if (archiveState.selectedPresetKeywords.length > 1) {
                    loadArchiveProducts(1);
                }
            }
        });
    });

    // 4. 添加关键词输入框与按钮
    const inputNewKw = document.getElementById('input-new-preset-kw');
    const btnAddKw = document.getElementById('btn-add-preset-kw');

    const doAddKeyword = async () => {
        if (!inputNewKw) return;
        const kw = inputNewKw.value.trim();
        if (!kw) {
            showToast('请输入关键词内容', 'warning');
            return;
        }
        if (archiveState.presetKeywords.includes(kw)) {
            showToast(`关键词 "${kw}" 已在预设列表中`, 'info');
            inputNewKw.value = '';
            return;
        }

        archiveState.presetKeywords.push(kw);
        renderPresetKeywordChips();
        inputNewKw.value = '';
        savePresetKeywords();
        showToast(`已添加预设筛选词: "${kw}"`, 'success');
    };

    if (btnAddKw) {
        btnAddKw.addEventListener('click', doAddKeyword);
    }
    if (inputNewKw) {
        inputNewKw.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                doAddKeyword();
            }
        });
    }

}

function updatePresetModeUI() {
    const modeBtns = document.querySelectorAll('#preset-mode-switch .btn-mode');
    modeBtns.forEach(btn => {
        if (btn.getAttribute('data-mode') === archiveState.presetMatchMode) {
            btn.classList.add('active');
        } else {
            btn.classList.remove('active');
        }
    });
}

function renderPresetKeywordChips() {
    const container = document.getElementById('preset-keywords-container');
    if (!container) return;

    if (!archiveState.presetKeywords || archiveState.presetKeywords.length === 0) {
        container.innerHTML = '<span style="font-size: 12px; color: var(--text-muted); padding: 4px 0;">暂无预设词，可在右侧输入框添加常用搜索词</span>';
        return;
    }

    container.innerHTML = archiveState.presetKeywords.map(kw => {
        const isSelected = archiveState.selectedPresetKeywords.includes(kw);
        return `
            <button type="button" class="filter-chip preset-chip ${isSelected ? 'active' : ''}" data-kw="${escapeHtml(kw)}" title="点击多选切换筛选 [${escapeHtml(kw)}]">
                <span class="preset-chip-text">${escapeHtml(kw)}</span>
                <span class="btn-del-preset" title="删除预设词 [${escapeHtml(kw)}]" data-del-kw="${escapeHtml(kw)}">&times;</span>
            </button>
        `;
    }).join('');

    container.querySelectorAll('.preset-chip').forEach(chip => {
        const kw = chip.getAttribute('data-kw');
        const delBtn = chip.querySelector('.btn-del-preset');

        chip.addEventListener('click', (e) => {
            if (e.target === delBtn || delBtn.contains(e.target)) return;

            const idx = archiveState.selectedPresetKeywords.indexOf(kw);
            if (idx >= 0) {
                archiveState.selectedPresetKeywords.splice(idx, 1);
                chip.classList.remove('active');
            } else {
                archiveState.selectedPresetKeywords.push(kw);
                chip.classList.add('active');
            }


            // 即时触发库存状态、新品状态与关键词的三组组合筛选
            loadArchiveProducts(1);
        });

        if (delBtn) {
            delBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                removePresetKeyword(kw);
            });
        }
    });
}

async function removePresetKeyword(kw) {
    archiveState.presetKeywords = archiveState.presetKeywords.filter(k => k !== kw);
    const selIdx = archiveState.selectedPresetKeywords.indexOf(kw);
    const wasSelected = selIdx >= 0;
    if (wasSelected) {
        archiveState.selectedPresetKeywords.splice(selIdx, 1);
    }
    renderPresetKeywordChips();
    savePresetKeywords();
    showToast(`已删除预设关键词: "${kw}"`, 'info');
    if (wasSelected) {
        loadArchiveProducts(1);
    }
}

async function savePresetKeywords() {
    localStorage.setItem('dollyteria_archive_preset_kw', JSON.stringify(archiveState.presetKeywords));
    try {
        await fetch('/api/archive/preset_keywords', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ keywords: archiveState.presetKeywords })
        });
    } catch (e) {
        console.warn('保存预设关键词到服务端异常:', e);
    }
}

// ==================== 顶部下单模式指示器 ====================
function updateTopOrderModeIndicator() {
    const indicator = elements.orderModeIndicator || document.getElementById('order-mode-indicator');
    const textEl = elements.orderModeText || document.getElementById('order-mode-text');
    if (!indicator || !textEl) return;

    const ao = currentConfig?.auto_order || {};
    const isEnabled = Boolean(ao.enabled);

    indicator.classList.remove('mode-dry-run', 'mode-real', 'mode-disabled');

    if (isEnabled) {
        indicator.classList.add('mode-real');
        textEl.textContent = '🚨 自动下单模式';
        indicator.title = '当前处于自动下单模式 (命中规则将直接向官网提交真实订单)，点击直达设置';
    } else {
        indicator.classList.add('mode-disabled');
        textEl.textContent = '🔔 提醒模式';
        indicator.title = '当前处于提醒模式 (命中规则仅发送通知提醒，不下单)，点击直达设置';
    }
}
