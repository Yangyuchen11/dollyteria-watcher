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
    schedIntervalEnabled: document.getElementById('sched-interval-enabled'),
    schedIntervalMinutes: document.getElementById('sched-interval-minutes'),
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
    btnSaveAutoOrder: document.getElementById('btn-save-auto-order'),
    aoTestItemId: document.getElementById('ao-test-item-id'),
    aoTestPaySelect: document.getElementById('ao-test-pay-select'),
    btnTestDryRunOrder: document.getElementById('btn-test-dry-run-order'),
    aoTestResult: document.getElementById('ao-test-result'),
    aoModeBanner: document.getElementById('ao-mode-banner'),
    aoModeIcon: document.getElementById('ao-mode-icon'),
    aoModeTitle: document.getElementById('ao-mode-title'),
    aoModeDesc: document.getElementById('ao-mode-desc'),

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
};

// 格式化倒计时
function formatSeconds(sec) {
    if (sec < 0) return '未启用';
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = sec % 60;
    return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
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
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
            btn.classList.add('active');
            const targetPane = document.getElementById(tabId);
            if (targetPane) targetPane.classList.add('active');
            if (tabId === 'tab-feed') {
                elements.feedBadge.style.display = 'none';
                elements.feedBadge.textContent = '0';
            }
        });
    });
}

// 加载配置
async function loadConfig() {
    try {
        const res = await fetch('/api/config');
        currentConfig = await res.json();
        renderWatches();
        populateScheduleForm();
        populateNotifiersForm();
        populateAutoOrderForm();
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
        const data = await res.json();
        if (!silent) showToast(data.message || '配置已保存', 'success');
        renderWatches();
    } catch (e) {
        showToast('保存配置异常: ' + e.message, 'error');
    }
}

// 定时状态轮询 (每秒一次)
async function pollStatus() {
    try {
        const res = await fetch('/api/status');
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
            elements.statusText.textContent = '● 监控运行中';
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
        card.className = `watch-card ${rule.enabled ? '' : 'disabled'}`;

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

        const modeHint = matchMode === 'all' 
            ? '📌 模式：必须同时包含以上所有词 (联合搜索)' 
            : '📌 模式：包含以上任意一个词即可';

        card.innerHTML = `
            <div>
                <div class="watch-card-top">
                    <div class="watch-title-group">
                        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                            <div class="watch-name">${escapeHtml(rule.name)}</div>
                            ${matchBadge}
                        </div>
                        <span class="watch-mode-tag">${modeMap[rule.search_mode] || '综合检索'}</span>
                    </div>
                    <label class="switch">
                        <input type="checkbox" class="watch-toggle" data-idx="${idx}" ${rule.enabled ? 'checked' : ''}>
                        <span class="slider"></span>
                    </label>
                </div>
                <div class="watch-tag-list">
                    ${kws}
                    ${excludes}
                    ${priceTag}
                </div>
                <div class="watch-mode-hint">${modeHint}</div>
            </div>
            <div class="watch-card-footer">
                <span style="font-size:12px;color:var(--text-muted);">规则 #${idx + 1}</span>
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
    const isEnabled = elements.aoEnabled ? elements.aoEnabled.checked : false;
    const isDryRun = elements.aoDryRun ? elements.aoDryRun.checked : true;

    if (!isEnabled) {
        elements.aoModeBanner.className = 'ao-mode-banner idle';
        if (elements.aoModeIcon) elements.aoModeIcon.textContent = '🔔';
        if (elements.aoModeTitle) elements.aoModeTitle.textContent = '当前运行状态：仅提醒通知模式';
        if (elements.aoModeDesc) elements.aoModeDesc.textContent = '自动下单执行已关闭。当监控命中匹配的新商品时，仅触发声音、桌面弹窗、Discord 与邮件提醒，绝不自动加购下单。';
    } else if (isDryRun) {
        elements.aoModeBanner.className = 'ao-mode-banner safe';
        if (elements.aoModeIcon) elements.aoModeIcon.textContent = '🛡️';
        if (elements.aoModeTitle) elements.aoModeTitle.textContent = '当前运行状态：Dry-Run 仿真测试模式 (绝对安全，不产生实际订单)';
        if (elements.aoModeDesc) elements.aoModeDesc.textContent = '自动下单已启用，但受 Dry-Run 仿真安全保护：命中商品时自动登录加购、选择地址并指定支付方式，推进至【下单前确认页 (/cart/confirm)】后自动安全拦截停止，绝不提交真实订单或扣款！测试后自动清空购物车。';
    } else {
        elements.aoModeBanner.className = 'ao-mode-banner warning';
        if (elements.aoModeIcon) elements.aoModeIcon.textContent = '🚨';
        if (elements.aoModeTitle) elements.aoModeTitle.textContent = '当前运行状态：真实自动下单模式 (将直接提交官方订单！)';
        if (elements.aoModeDesc) elements.aoModeDesc.textContent = '【重要警示】已解除 Dry-Run 安全仿真保护！当监控命中匹配的全新在售商品时，系统将全自动推进至确认页并在确认页直接点击「購入する」正式提交订单（支付方式为所选的代金引换或店頭支付）！请务必确认您需要真实抢购。';
    }
}

// 调度表单同步
function populateScheduleForm() {
    const s = currentConfig?.schedule || {};
    currentDropTimes = [...(s.drop_times || [])];
    renderDropTimesChips();
    if (elements.schedBurstDuration) elements.schedBurstDuration.value = s.burst_duration_sec ?? 180;
    if (elements.schedBurstInterval) elements.schedBurstInterval.value = s.burst_interval_sec ?? 2.0;
    if (elements.schedIntervalEnabled) elements.schedIntervalEnabled.checked = s.interval_enabled ?? true;
    if (elements.schedIntervalMinutes) elements.schedIntervalMinutes.value = s.interval_minutes ?? 5;
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
    if (elements.aoDryRun) elements.aoDryRun.checked = ao.dry_run ?? true;
    if (elements.aoEnabled) elements.aoEnabled.checked = ao.enabled ?? false;

    const pm = ao.payment_method || 'cod';
    const radioCod = document.getElementById('ao-pay-cod');
    const radioStore = document.getElementById('ao-pay-store');
    if (radioCod && radioStore) {
        if (pm === 'store_pickup') radioStore.checked = true;
        else radioCod.checked = true;
    }
    if (elements.aoTestPaySelect) {
        elements.aoTestPaySelect.value = pm;
    }
    updateAoModeBanner();
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
    } else {
        elements.modalTitle.textContent = '添加监控规则';
        elements.modalRuleId.value = 'w_' + Date.now();
        elements.modalRuleName.value = '';
        elements.modalRuleKeywords.value = '';
        elements.modalRuleExcludes.value = '';
        elements.modalRuleMinPrice.value = 0;
        elements.modalRuleMaxPrice.value = 0;
        elements.modalRuleSearchMode.value = 'both';
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

    const card = document.createElement('div');
    card.className = 'product-card';

    const imgSrc = item.img_url ? `/img_proxy?url=${encodeURIComponent(item.img_url)}` : '';
    const imgHtml = imgSrc 
        ? `<img class="product-img" src="${imgSrc}" loading="lazy" alt="${escapeHtml(item.title)}">` 
        : `<div style="height:100%;display:flex;align-items:center;justify-content:center;color:#64748b;">无缩略图</div>`;

    card.innerHTML = `
        <div class="product-img-wrapper">
            ${imgHtml}
            <span class="product-stock-badge">● 在库</span>
            <span class="product-rule-badge">${escapeHtml(item.rule_name || '命中规则')}</span>
        </div>
        <div class="product-body">
            <div>
                <div class="product-title" title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</div>
                <div class="product-model">${escapeHtml(item.model_number || '')}</div>
            </div>
            <div class="product-footer">
                <div class="product-price">${escapeHtml(item.price_str || '￥' + item.price)}</div>
                <a href="${item.url}" target="_blank" rel="noopener noreferrer" class="btn btn-sm btn-primary">
                    查看商品 ↗
                </a>
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
            } catch (e) {}
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
            currentConfig.schedule.burst_interval_sec = parseFloat(elements.schedBurstInterval?.value) || 2.0;
            currentConfig.schedule.interval_enabled = elements.schedIntervalEnabled?.checked ?? true;
            currentConfig.schedule.interval_minutes = parseFloat(elements.schedIntervalMinutes?.value) || 5;
            saveConfig();
        });
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
            currentConfig.auto_order = {
                username: elements.aoUsername.value.trim(),
                password: elements.aoPassword.value.trim(),
                dry_run: elements.aoDryRun.checked,
                enabled: elements.aoEnabled.checked,
                payment_method: payMethod
            };
            saveConfig();
            updateAoModeBanner();
        });
    }

    // 自动下单开关联动实时状态横幅
    if (elements.aoEnabled) {
        elements.aoEnabled.addEventListener('change', () => {
            updateAoModeBanner();
        });
    }
    if (elements.aoDryRun) {
        elements.aoDryRun.addEventListener('change', () => {
            if (!elements.aoDryRun.checked && elements.aoEnabled?.checked) {
                const confirmed = confirm(
                    "⚠️ 重要安全提示：\n\n" +
                    "您正在关闭 Dry-Run (安全仿真) 模式！\n" +
                    "关闭后，一旦监控命中您设置的商品关键词，系统将在确认页直接点击「購入する」提交真实订单并完成购买！\n\n" +
                    "确定要解除安全保护吗？"
                );
                if (!confirmed) {
                    elements.aoDryRun.checked = true;
                }
            }
            updateAoModeBanner();
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
            const payName = payMethod === 'store_pickup' ? '店頭支払' : '代金引換';

            elements.btnTestDryRunOrder.disabled = true;
            elements.btnTestDryRunOrder.textContent = '⏳ 正在全流程仿真测试中...';
            showToast(`正在执行【${payName}】全流程仿真测试，直达确认页 (/cart/confirm)...`, 'info');

            if (elements.aoTestResult) {
                elements.aoTestResult.style.display = 'block';
                elements.aoTestResult.innerHTML = `
                    <div style="display:flex;align-items:center;gap:10px;color:var(--text-secondary);">
                        <span class="status-pulse" style="width:10px;height:10px;background:#38bdf8;border-radius:50%;display:inline-block;"></span>
                        正在执行登录、秒级加购、收件人选择并锁定【${escapeHtml(payName)}】支付方式...
                    </div>
                `;
            }

            try {
                const res = await fetch('/api/test_auto_order', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username, password, item_id: itemId, payment_method: payMethod })
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
                elements.btnTestDryRunOrder.textContent = '🧪 执行仿真测试';
            }
        });
    }

    // 添加规则与 Modal 事件
    if (elements.btnAddWatch) elements.btnAddWatch.addEventListener('click', () => openRuleModal());
    if (elements.modalClose) elements.modalClose.addEventListener('click', closeRuleModal);
    if (elements.modalBtnCancel) elements.modalBtnCancel.addEventListener('click', closeRuleModal);
    if (elements.modalBtnSave) elements.modalBtnSave.addEventListener('click', saveRuleFromModal);
}

// 页面加载启动
document.addEventListener('DOMContentLoaded', () => {
    try { initTimeSelects(); } catch (e) { console.error('initTimeSelects err:', e); }
    try { initTabs(); } catch (e) { console.error('initTabs err:', e); }
    try { loadConfig(); } catch (e) { console.error('loadConfig err:', e); }
    try { bindEvents(); } catch (e) { console.error('bindEvents err:', e); }
    try { loadHistory(); } catch (e) { console.error('loadHistory err:', e); }
    try { initSSE(); } catch (e) { console.error('initSSE err:', e); }
    try {
        pollStatus();
        setInterval(pollStatus, 1000);
    } catch (e) { console.error('pollStatus err:', e); }
});
