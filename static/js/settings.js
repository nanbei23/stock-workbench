/**
 * 设置页JS — Tab切换 + 设置CRUD + 导入导出 + 通知管理
 */
const API_BASE = '/api';

let currentSettings = {};
let modelProviderCache = [];
let workerPoolRows = [];

const INVESTMENT_STYLE_PRESETS = {
    conservative: {
        investment_max_single_position_pct: '10',
        investment_min_cash_pct: '20',
        investment_max_drawdown_pct: '6',
        investment_entry_preference: '右侧确认优先，要求趋势站稳、成交量温和放大、基本面和资金面同时确认。',
        investment_exit_discipline: '跌破关键支撑或基本面恶化时减仓，资金持续流出时退出；达到目标位分批止盈。',
        investment_allow_left_side: false,
        investment_allow_high_volatility: 'forbid',
        investment_custom_notes: '优先本金保护，不因短线波动追高。'
    },
    balanced: {
        investment_max_single_position_pct: '15',
        investment_min_cash_pct: '5',
        investment_max_drawdown_pct: '12',
        investment_entry_preference: '右侧确认、趋势突破、资金流入、题材催化，机会成立时分批建仓。',
        investment_exit_discipline: '逻辑失效、跌破关键位、硬止损、分批止盈。',
        investment_allow_left_side: false,
        investment_allow_high_volatility: 'cautious',
        investment_custom_notes: '平衡胜率和赔率，机会成立时分批建仓。'
    },
    aggressive: {
        investment_max_single_position_pct: '40',
        investment_min_cash_pct: '3',
        investment_max_drawdown_pct: '15',
        investment_entry_preference: '右侧突破优先，要求放量站上关键位、资金连续流入、板块或题材共振；回踩不破关键支撑后转强可加仓。',
        investment_exit_discipline: '跌破买入触发位或关键支撑先减仓，放量破位清仓；题材逻辑失效、资金连续流出或报告核心假设被证伪时退出；上涨后按压力位和目标位分批止盈。',
        investment_allow_left_side: false,
        investment_allow_high_volatility: 'allow',
        investment_custom_notes: '偏进攻，但不做无确认左侧交易；机会成立时允许集中到核心标的，必须保留明确止损和失效条件。'
    },
    speculative: {
        investment_max_single_position_pct: '50',
        investment_min_cash_pct: '0',
        investment_max_drawdown_pct: '20',
        investment_entry_preference: '强题材启动、放量突破、资金快速流入、板块情绪升温；允许小仓位左侧试错。',
        investment_exit_discipline: '触发硬止损立即退出，放量破位清仓；情绪退潮、资金转流出或题材证伪时退出。',
        investment_allow_left_side: true,
        investment_allow_high_volatility: 'allow',
        investment_custom_notes: '高波动策略必须小仓位试错、严格止损。'
    },
    custom: {}
};

function investmentField(key) {
    return document.getElementById('set-' + key);
}

function markInvestmentProfileEdited(el) {
    if (el) {
        el.dataset.userEdited = 'true';
        el.dataset.profileGenerated = '';
    }
}

function shouldApplyInvestmentPresetValue(el, overwriteLoaded) {
    if (!el) return false;
    if (el.dataset.userEdited === 'true') return false;
    if (overwriteLoaded || el.dataset.profileGenerated === 'true') return true;
    if (el.type === 'checkbox') return false;
    return !String(el.value || '').trim();
}

function setInvestmentPresetValue(key, value, overwriteLoaded) {
    const el = investmentField(key);
    if (!shouldApplyInvestmentPresetValue(el, overwriteLoaded)) return;
    if (el.type === 'checkbox') {
        el.checked = value === true || value === 'true';
    } else {
        el.value = value ?? '';
    }
    el.dataset.profileGenerated = 'true';
}

function applyInvestmentPresetValues(overwriteLoaded = false, clearManualEdits = false) {
    const presetKey = investmentField('investment_style_preset')?.value || 'aggressive';
    const preset = INVESTMENT_STYLE_PRESETS[presetKey] || {};
    Object.entries(preset).forEach(([key, value]) => {
        const el = investmentField(key);
        if (!el) return;
        if (clearManualEdits) {
            el.dataset.userEdited = '';
        }
        setInvestmentPresetValue(key, value, overwriteLoaded);
    });
}

function applyInvestmentStylePreset(force = false) {
    applyInvestmentPresetValues(force, force);
    if (force) {
        toast('success', '已套用当前风格模板');
    }
}

function maybeApplyInvestmentStylePreset(overwriteLoaded = false) {
    applyInvestmentPresetValues(Boolean(overwriteLoaded), false);
}

function applyInvestmentProfileSuggestion(settings) {
    Object.entries(settings || {}).forEach(([key, value]) => {
        const el = investmentField(key);
        if (!el) return;
        el.dataset.userEdited = '';
        if (el.type === 'checkbox') {
            el.checked = value === true || value === 'true';
        } else {
            el.value = value ?? '';
        }
        el.dataset.profileGenerated = 'true';
    });
}

async function inferInvestmentProfileFromTrades() {
    const btn = document.getElementById('inferInvestmentProfileBtn');
    const result = document.getElementById('investmentProfileInferResult');
    if (btn) btn.disabled = true;
    if (result) {
        result.className = 'test-result';
        result.textContent = '推断中...';
    }
    try {
        const resp = await fetch(`${API_BASE}/settings/investment-profile/infer`, { method: 'POST' });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || data.message || '推断失败');
        applyInvestmentProfileSuggestion(data.suggested_settings || {});
        if (result) {
            result.className = 'test-result ok';
            result.textContent = `已生成草稿，保存后生效：${data.summary || ''}`;
        }
        toast('success', '已根据交易历史生成投资风格草稿，请确认后保存');
    } catch (e) {
        if (result) {
            result.className = 'test-result error';
            result.textContent = `失败 ${e.message}`;
        }
        toast('error', '推断失败: ' + e.message);
    } finally {
        if (btn) btn.disabled = false;
    }
}

// ── Tab切换 ──
function switchSettingsTab(btn, section) {
    document.querySelectorAll('.settings-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.settings-section').forEach(s => s.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('section-' + section).classList.add('active');
}

// ── 加载设置 ──
async function loadSettings() {
    try {
        const resp = await fetch(`${API_BASE}/settings`);
        currentSettings = await resp.json();
        hydrateAiModelOptions(currentSettings);
        hydrateVerificationModelOptions(currentSettings);
        applySettings(currentSettings);
        syncAiNameFromBaseUrl();
        syncVerificationNameFromBaseUrl();
        toast('success', '设置已加载');
    } catch (e) {
        console.error('加载设置失败:', e);
    }
}

function parseModelOptions(value) {
    if (!value) return [];
    if (Array.isArray(value)) return value;
    try {
        const parsed = JSON.parse(value);
        return Array.isArray(parsed) ? parsed.filter(Boolean) : [];
    } catch (e) {
        return String(value).split(',').map(x => x.trim()).filter(Boolean);
    }
}

function verificationModelLabel(model) {
    const labels = {
        'mimo-v2.5-pro': 'MiMo v2.5 Pro',
        'deepseek-chat': 'DeepSeek Chat',
        'deepseek-reasoner': 'DeepSeek Reasoner'
    };
    return labels[model] || model;
}

function setSelectModelOptions(select, models, selected, emptyText = '请先获取模型列表') {
    if (!select) return;
    const merged = [...new Set([...(models || []), selected].filter(Boolean))];
    if (!merged.length) {
        select.innerHTML = `<option value="">${emptyText}</option>`;
        return;
    }
    select.innerHTML = merged.map(m =>
        `<option value="${escapeAttr(m)}" ${m === selected ? 'selected' : ''}>${escapeHtml(verificationModelLabel(m))}</option>`
    ).join('');
    if (selected) select.value = selected;
}

function setVerificationModelOptions(models, selected) {
    setSelectModelOptions(document.getElementById('set-verification_model'), models, selected);
}

function hydrateVerificationModelOptions(settings) {
    const stored = parseModelOptions(settings.verification_model_options);
    setVerificationModelOptions(stored, settings.verification_model || '');
}

function setAiModelOptions(models, quickSelected, deepSelected) {
    setSelectModelOptions(document.getElementById('set-quick_think_model'), models, quickSelected);
    setSelectModelOptions(document.getElementById('set-deep_think_model'), models, deepSelected);
}

function hydrateAiModelOptions(settings) {
    const stored = parseModelOptions(settings.llm_model_options);
    const hasFetchedModels = stored.length > 0;
    const quick = hasFetchedModels ? (settings.quick_think_model || '') : '';
    const deep = hasFetchedModels ? (settings.deep_think_model || '') : '';
    setAiModelOptions(stored, quick, deep);
}

function applySettings(s) {
    for (const [key, value] of Object.entries(s)) {
        const el = document.getElementById('set-' + key);
        if (!el) continue;
        if (key === 'model_mode') {
            setModelMode(value || 'balanced');
        } else if (el.type === 'checkbox') {
            el.checked = value === 'true' || value === true;
        } else if (el.tagName === 'SELECT') {
            el.value = value;
        } else {
            el.value = value;
        }
    }
    maybeApplyInvestmentStylePreset(false);
}

// ── 收集设置 ──
function collectSettings() {
    const result = {};
    syncAiModelOptions();
    syncVerificationModelOptions();
    document.querySelectorAll('[id^="set-"]').forEach(el => {
        const key = el.id.replace('set-', '');
        if (el.type === 'checkbox') {
            result[key] = el.checked ? 'true' : 'false';
        } else {
            result[key] = el.value;
        }
    });
    return result;
}

function syncAiModelOptions(extraModels = []) {
    const quickSelect = document.getElementById('set-quick_think_model');
    const deepSelect = document.getElementById('set-deep_think_model');
    const hidden = document.getElementById('set-llm_model_options');
    if (!quickSelect || !deepSelect || !hidden) return;
    const quickOptions = Array.from(quickSelect.options).map(opt => opt.value).filter(Boolean);
    const deepOptions = Array.from(deepSelect.options).map(opt => opt.value).filter(Boolean);
    hidden.value = JSON.stringify([...new Set([...quickOptions, ...deepOptions, ...extraModels].filter(Boolean))]);
}

function syncVerificationModelOptions(extraModels = []) {
    const select = document.getElementById('set-verification_model');
    const hidden = document.getElementById('set-verification_model_options');
    if (!select || !hidden) return;
    const existing = Array.from(select.options).map(opt => opt.value).filter(Boolean);
    hidden.value = JSON.stringify([...new Set([...existing, ...extraModels].filter(Boolean))]);
}

function nameFromBaseUrl(baseUrl) {
    if (!baseUrl) return '';
    try {
        const url = new URL(baseUrl);
        const host = url.hostname.replace(/^api\./, '').split('.')[0];
        return host ? `${host} verifier` : '';
    } catch (e) {
        return '';
    }
}

function syncVerificationNameFromBaseUrl() {
    const endpoint = document.getElementById('set-verification_endpoint')?.value?.trim() || '';
    const nameInput = document.getElementById('set-verification_name');
    if (!nameInput) return;
    const generated = nameFromBaseUrl(endpoint);
    if (!nameInput.value.trim() || nameInput.dataset.generated === 'true') {
        nameInput.value = generated;
        nameInput.dataset.generated = generated ? 'true' : '';
    }
}

function syncAiNameFromBaseUrl() {
    const endpoint = document.getElementById('set-custom_endpoint')?.value?.trim() || '';
    const nameInput = document.getElementById('set-llm_name');
    if (!nameInput) return;
    const generated = nameFromBaseUrl(endpoint).replace(/ verifier$/, ' analysis');
    if (!nameInput.value.trim() || nameInput.dataset.generated === 'true') {
        nameInput.value = generated;
        nameInput.dataset.generated = generated ? 'true' : '';
    }
}

function toggleApiKey(btn) {
    const input = btn.parentElement.querySelector('input[type]');
    const isPassword = input.type === 'password';
    input.type = isPassword ? 'text' : 'password';
    btn.textContent = isPassword ? '隐藏' : '显示';
}

// ── 保存设置 ──
async function saveSettings() {
    const settings = collectSettings();
    try {
        const resp = await fetch(`${API_BASE}/settings/bulk`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ settings }),
        });
        if (resp.ok) {
            toast('success', '设置已保存');
            currentSettings = settings;
        } else {
            toast('error', '保存失败');
        }
    } catch (e) {
        toast('error', '保存失败: ' + e.message);
    }
}

// ── 测试API连接 ──
async function testApiConnection() {
    const resultSpan = document.getElementById('testResult');
    resultSpan.textContent = '测试中...';
    resultSpan.className = 'test-result';
    try {
        const resp = await fetch(`${API_BASE}/settings/test-llm`, { method: 'POST' });
        const data = await resp.json();
        if (data.status === 'ok') {
            resultSpan.className = 'test-result ok';
            resultSpan.textContent = `成功 ${data.message} (${data.latency_ms}ms)`;
        } else {
            resultSpan.className = 'test-result error';
            resultSpan.textContent = `失败 ${data.message}`;
        }
    } catch (e) {
        resultSpan.className = 'test-result error';
        resultSpan.textContent = '失败 网络错误';
    }
}

async function testVerificationConnection() {
    const resultSpan = document.getElementById('testVerificationResult');
    const endpoint = document.getElementById('set-verification_endpoint')?.value?.trim() || '';
    const apiKey = document.getElementById('set-verification_api_key')?.value?.trim() || '';
    const model = document.getElementById('set-verification_model')?.value?.trim() || '';
    if (!endpoint) {
        resultSpan.className = 'test-result error';
        resultSpan.textContent = '失败 Base URL未配置';
        return;
    }
    if (!apiKey) {
        resultSpan.className = 'test-result error';
        resultSpan.textContent = '失败 API密钥未配置';
        return;
    }
    if (!model) {
        resultSpan.className = 'test-result error';
        resultSpan.textContent = '失败 请先选择核对模型';
        return;
    }
    resultSpan.textContent = '测试中...';
    resultSpan.className = 'test-result';
    try {
        const resp = await fetch(`${API_BASE}/settings/test-verification`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ endpoint, api_key: apiKey, model }),
        });
        const data = await resp.json();
        if (data.status === 'ok') {
            resultSpan.className = 'test-result ok';
            resultSpan.textContent = `成功 ${data.message}`;
        } else {
            resultSpan.className = 'test-result error';
            resultSpan.textContent = `失败 ${data.message}`;
        }
    } catch (e) {
        resultSpan.className = 'test-result error';
        resultSpan.textContent = '失败 网络错误';
    }
}

// ── 获取远程模型列表 ──
async function fetchRemoteModels() {
    const endpointInput = document.getElementById('set-custom_endpoint');
    const endpoint = endpointInput.value.trim();
    const apiKey = document.getElementById('set-api_key').value.trim();
    const resultSpan = document.getElementById('fetchModelsResult');
    const btn = document.getElementById('fetchModelsBtn');
    
    if (!endpoint) {
        resultSpan.className = 'test-result error';
        resultSpan.textContent = '失败 请先填写 Base URL';
        return;
    }
    if (!apiKey) {
        resultSpan.className = 'test-result error';
        resultSpan.textContent = '失败 请先填写 API Key';
        return;
    }
    
    syncAiNameFromBaseUrl();
    btn.disabled = true;
    btn.textContent = '获取中...';
    resultSpan.className = 'test-result';
    resultSpan.textContent = '正在根据 Base URL 获取模型...';
    
    try {
        const resp = await fetch(`${API_BASE}/settings/fetch-models`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ endpoint, api_key: apiKey })
        });
        const data = await resp.json();
        
        if (resp.ok && data.status === 'ok' && data.models.length > 0) {
            const deepSelect = document.getElementById('set-deep_think_model');
            const quickSelect = document.getElementById('set-quick_think_model');
            const currentDeep = deepSelect.value;
            const currentQuick = quickSelect.value;
            const quickSelected = data.models.includes(currentQuick) ? currentQuick : pickModel(data.models, 'quick');
            const deepSelected = data.models.includes(currentDeep) ? currentDeep : pickModel(data.models, 'deep');
            setAiModelOptions(data.models, quickSelected, deepSelected);
            syncAiModelOptions(data.models);
            
            resultSpan.className = 'test-result ok';
            resultSpan.textContent = `成功 获取到 ${data.models.length} 个模型`;
        } else {
            resultSpan.className = 'test-result error';
            resultSpan.textContent = `失败 ${data.detail || '未获取到模型'}`;
        }
    } catch (e) {
        resultSpan.className = 'test-result error';
        resultSpan.textContent = `失败 网络错误: ${e.message}`;
    } finally {
        btn.disabled = false;
        btn.textContent = '获取';
    }
}

function pickModel(models, role) {
    const list = models || [];
    if (!list.length) return '';
    const quickHints = ['flash', 'mini', 'turbo', 'lite', 'quick', 'chat'];
    const deepHints = ['reasoner', 'pro', 'max', 'o1', 'deep', 'thinking'];
    const hints = role === 'deep' ? deepHints : quickHints;
    const found = list.find(model => hints.some(hint => String(model).toLowerCase().includes(hint)));
    return found || list[0];
}

// ── 获取核对模型列表 ──
async function fetchVerificationModels(options = {}) {
    const endpointInput = document.getElementById('set-verification_endpoint');
    const endpoint = endpointInput.value.trim();
    const apiKey = document.getElementById('set-verification_api_key')?.value?.trim() || '';
    const resultSpan = document.getElementById('fetchVerModelsResult');
    const btn = document.getElementById('fetchVerModelsBtn');

    if (!endpoint) {
        resultSpan.className = 'test-result error';
        resultSpan.textContent = '失败 请先填写 Base URL';
        return;
    }
    if (!apiKey) {
        resultSpan.className = 'test-result error';
        resultSpan.textContent = '失败 请先填写 API Key';
        return;
    }

    syncVerificationNameFromBaseUrl();
    btn.disabled = true;
    btn.textContent = '获取中...';
    resultSpan.className = 'test-result';
    resultSpan.textContent = '正在根据 Base URL 获取模型...';

    try {
        const resp = await fetch(`${API_BASE}/settings/fetch-models`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ endpoint, api_key: apiKey })
        });
        const data = await resp.json();

        if (resp.ok && data.status === 'ok' && data.models.length > 0) {
            const select = document.getElementById('set-verification_model');
            const currentValue = select.value;
            const selected = data.models.includes(currentValue) ? currentValue : data.models[0];
            setVerificationModelOptions(data.models, selected);
            syncVerificationModelOptions(data.models);

            resultSpan.className = 'test-result ok';
            resultSpan.textContent = `成功 获取到 ${data.models.length} 个模型`;
        } else {
            resultSpan.className = 'test-result error';
            resultSpan.textContent = `失败 ${data.detail || '未获取到模型'}`;
        }
    } catch (e) {
        resultSpan.className = 'test-result error';
        resultSpan.textContent = `失败 网络错误: ${e.message}`;
    } finally {
        btn.disabled = false;
        btn.textContent = '获取';
    }
}

// ── API密钥显示/隐藏 ──
function toggleApiKeyVisibility() {
    const el = document.getElementById('set-api_key');
    const isPassword = el.type === 'password';
    el.type = isPassword ? 'text' : 'password';
    const btn = el.parentElement?.querySelector('button');
    if (btn) btn.textContent = isPassword ? '隐藏' : '显示';
}

// ── 浏览器通知 ──
function requestNotifyPermission() {
    if (!('Notification' in window)) {
        toast('error', '浏览器不支持通知');
        return;
    }
    Notification.requestPermission().then(perm => {
        const btn = document.getElementById('btnNotifyPerm');
        if (perm === 'granted') {
            btn.textContent = '已授权';
            toast('success', '通知权限已授权');
            // 测试通知
            new Notification('炒股小牛马', {
                body: '通知已启用！条件单触发时会自动提醒你。',
                tag: 'test',
            });
        } else {
            btn.textContent = '已拒绝';
            toast('error', '通知权限被拒绝');
        }
    });
}

function onNotifyToggle(el) {
    if (el.checked && Notification.permission !== 'granted') {
        el.checked = false;
        toast('error', '请先点击「请求权限」开启通知');
        return;
    }
    // 同步到localStorage供全局轮询使用
    localStorage.setItem('browser_notify_enabled', el.checked ? 'true' : 'false');
}

// ── 发送浏览器通知 ──
function sendBrowserNotification(title, body, tag) {
    if (Notification.permission === 'granted') {
        new Notification(title, {
            body: body,
            tag: tag || 'stock',
            requireInteraction: true,
        });
    }
}

// ── 导出数据 ──
async function exportData() {
    try {
        const resp = await fetch(`${API_BASE}/settings/export`);
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        // 从Content-Disposition获取文件名
        const disposition = resp.headers.get('Content-Disposition');
        a.download = disposition
            ? disposition.split('filename=')[1].replace(/"/g, '')
            : `stock-workbench-backup-${new Date().toISOString().slice(0, 10)}.json`;
        a.click();
        URL.revokeObjectURL(url);
        toast('success', '数据已导出');
    } catch (e) {
        toast('error', '导出失败: ' + e.message);
    }
}

// ── 导入数据 ──
async function importData(input) {
    const file = input.files[0];
    if (!file) return;
    try {
        const text = await file.text();
        const data = JSON.parse(text);
        const resp = await fetch(`${API_BASE}/settings/import`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        const result = await resp.json();
        if (resp.ok) {
            const imp = result.imported;
            toast('success', `导入完成: 自选${imp.watchlist}条/持仓${imp.portfolio}条/条件单${imp.orders}条/设置${imp.settings}条`);
            loadSettings(); // 重新加载
        } else {
            toast('error', '导入失败');
        }
    } catch (e) {
        toast('error', '文件格式错误: ' + e.message);
    }
    input.value = '';
}

function formatBytes(bytes) {
    if (!bytes) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB'];
    let value = Number(bytes);
    let idx = 0;
    while (value >= 1024 && idx < units.length - 1) {
        value /= 1024;
        idx += 1;
    }
    return `${value.toFixed(idx === 0 ? 0 : 1)} ${units[idx]}`;
}

async function loadBackupStatus() {
    const el = document.getElementById('backupStatusPanel');
    if (!el) return;
    try {
        const resp = await fetch(`${API_BASE}/settings/backup/status`);
        const data = await resp.json();
        const migrations = data.migrations || {};
        const db = data.database || {};
        const latest = (data.backups || [])[0];
        const pending = migrations.pending || [];
        el.innerHTML = `<div class="backup-status-grid">
            <div><span>数据库版本</span><strong>${migrations.latest_applied || 0}/${migrations.latest_known || 0}</strong></div>
            <div><span>迁移状态</span><strong class="${pending.length ? 'down' : 'up'}">${pending.length ? `待迁移 ${pending.join(',')}` : '已最新'}</strong></div>
            <div><span>数据库大小</span><strong>${formatBytes(db.size_bytes || 0)}</strong></div>
            <div><span>最近备份</span><strong>${latest ? escapeHtml(latest.filename) : '暂无'}</strong></div>
        </div>`;
    } catch (e) {
        el.innerHTML = '<div class="empty-state"><p>数据库状态读取失败</p></div>';
    }
}

async function loadModelProviders() {
    const el = document.getElementById('modelProviderList');
    if (!el) return;
    try {
        const resp = await fetch(`${API_BASE}/model-providers`);
        const data = await resp.json();
        const providers = data.providers || [];
        modelProviderCache = providers;
        if (!providers.length) {
            el.innerHTML = '<div class="empty-state"><p>暂无模型配置</p></div>';
            renderWorkerPoolRows();
            return;
        }
        el.innerHTML = providers.map(p => `<div class="quality-report-row">
            <div>
              <b>${escapeHtml(p.name || p.id)}</b>
              <small>${escapeHtml(p.base_url || '')} · ${(p.models || []).length} 个模型 · ${p.has_api_key ? '已保存 Key' : '无 Key'}</small>
            </div>
            <button class="btn-secondary" onclick="refreshModelProvider('${escapeAttr(p.id)}')">刷新模型</button>
            <button class="btn-secondary" onclick="testModelProvider('${escapeAttr(p.id)}')">测试</button>
            <button class="btn-secondary" onclick="applyModelProvider('${escapeAttr(p.id)}','ai')">用于AI</button>
            <button class="btn-secondary" onclick="applyModelProvider('${escapeAttr(p.id)}','verification')">用于核对</button>
            <button class="btn-secondary" onclick="deleteModelProvider('${escapeAttr(p.id)}')">删除</button>
        </div>`).join('');
        renderWorkerPoolRows();
    } catch (e) {
        el.innerHTML = `<div class="empty-state"><p>模型配置读取失败：${escapeHtml(e.message)}</p></div>`;
    }
}

function providerCheckboxes(selected = []) {
    const selectedSet = new Set(selected || []);
    if (!modelProviderCache.length) {
        return '<span class="text-muted">请先保存模型配置</span>';
    }
    return modelProviderCache.map(provider => `<label class="worker-provider-option">
        <input type="checkbox" value="${escapeAttr(provider.id)}" ${selectedSet.has(provider.id) ? 'checked' : ''}>
        <span>${escapeHtml(provider.name || provider.id)}</span>
    </label>`).join('');
}

function renderWorkerPoolRows() {
    const el = document.getElementById('workerPoolList');
    if (!el) return;
    if (!workerPoolRows.length) {
        el.innerHTML = '<div class="empty-state"><p>暂无 Worker 配置</p></div>';
        return;
    }
    el.innerHTML = workerPoolRows.map((worker, index) => `<div class="worker-pool-row" data-index="${index}">
        <label class="worker-enabled"><input type="checkbox" data-field="enabled" ${worker.enabled !== false ? 'checked' : ''}>启用</label>
        <input class="setting-input" data-field="id" value="${escapeAttr(worker.id || '')}" placeholder="worker-id">
        <input class="setting-input" data-field="name" value="${escapeAttr(worker.name || '')}" placeholder="显示名称">
        <select class="setting-select" data-field="model_tier">
            <option value="deep" ${(worker.model_tier || 'deep') === 'deep' ? 'selected' : ''}>深度模型</option>
            <option value="quick" ${worker.model_tier === 'quick' ? 'selected' : ''}>快速模型</option>
        </select>
        <input class="setting-input" type="number" min="1" step="1" data-field="sleep_seconds" value="${escapeAttr(worker.sleep_seconds || 5)}" placeholder="轮询秒">
        <input class="setting-input" type="number" min="1" step="1" data-field="stale_minutes" value="${escapeAttr(worker.stale_minutes || 15)}" placeholder="超时分钟">
        <div class="worker-provider-list">${providerCheckboxes(worker.provider_ids || [])}</div>
        <button class="btn-secondary" onclick="removeWorkerPoolRow(${index})">删除</button>
    </div>`).join('');
}

async function loadWorkerPoolConfig() {
    try {
        const resp = await fetch(`${API_BASE}/worker-pool/config`);
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || '读取失败');
        workerPoolRows = data.workers || [];
        renderWorkerPoolRows();
    } catch (e) {
        toast('error', 'Worker 配置读取失败: ' + e.message);
    }
}

function addWorkerPoolRow() {
    const next = workerPoolRows.length + 1;
    workerPoolRows.push({
        id: `worker-${next}`,
        name: `Worker ${next}`,
        enabled: true,
        provider_ids: [],
        model_tier: 'deep',
        sleep_seconds: 5,
        stale_minutes: 15,
    });
    renderWorkerPoolRows();
}

function removeWorkerPoolRow(index) {
    workerPoolRows.splice(index, 1);
    renderWorkerPoolRows();
}

function collectWorkerPoolRows() {
    return Array.from(document.querySelectorAll('#workerPoolList .worker-pool-row')).map(row => {
        const field = name => row.querySelector(`[data-field="${name}"]`);
        return {
            id: field('id')?.value?.trim() || '',
            name: field('name')?.value?.trim() || '',
            enabled: !!field('enabled')?.checked,
            model_tier: field('model_tier')?.value || 'deep',
            sleep_seconds: Number(field('sleep_seconds')?.value || 5),
            stale_minutes: Number(field('stale_minutes')?.value || 15),
            provider_ids: Array.from(row.querySelectorAll('.worker-provider-list input:checked')).map(input => input.value),
        };
    });
}

async function saveWorkerPoolConfig() {
    try {
        const payload = {workers: collectWorkerPoolRows()};
        const resp = await fetch(`${API_BASE}/worker-pool/config`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || '保存失败');
        workerPoolRows = data.workers || [];
        renderWorkerPoolRows();
        toast('success', 'Worker 模型池配置已保存');
    } catch (e) {
        toast('error', 'Worker 配置保存失败: ' + e.message);
    }
}

async function saveCurrentModelProvider() {
    const models = parseModelOptions(document.getElementById('set-llm_model_options')?.value || '[]');
    const payload = {
        name: document.getElementById('set-llm_name')?.value || '',
        base_url: document.getElementById('set-custom_endpoint')?.value || '',
        api_key: document.getElementById('set-api_key')?.value || '',
        models,
        quick_model: document.getElementById('set-quick_think_model')?.value || '',
        deep_model: document.getElementById('set-deep_think_model')?.value || '',
        default_model: document.getElementById('set-deep_think_model')?.value || document.getElementById('set-quick_think_model')?.value || '',
        context_length: document.getElementById('set-llm_context_length')?.value || '',
        apply_to: 'ai',
    };
    if (!payload.base_url) {
        toast('error', '请先填写 Base URL');
        return;
    }
    try {
        const resp = await fetch(`${API_BASE}/model-providers`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || '保存失败');
        toast('success', '模型配置已保存并应用到AI引擎');
        await loadSettings();
        loadModelProviders();
    } catch (e) {
        toast('error', '保存失败: ' + e.message);
    }
}

async function saveVerificationModelProvider() {
    const models = parseModelOptions(document.getElementById('set-verification_model_options')?.value || '[]');
    const payload = {
        name: document.getElementById('set-verification_name')?.value || '',
        base_url: document.getElementById('set-verification_endpoint')?.value || '',
        api_key: document.getElementById('set-verification_api_key')?.value || '',
        models,
        default_model: document.getElementById('set-verification_model')?.value || '',
        quick_model: '',
        deep_model: document.getElementById('set-verification_model')?.value || '',
        context_length: document.getElementById('set-verification_context_length')?.value || '',
        apply_to: 'verification',
    };
    if (!payload.base_url) {
        toast('error', '请先填写核对 Base URL');
        return;
    }
    try {
        const resp = await fetch(`${API_BASE}/model-providers`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || '保存失败');
        toast('success', '核对模型配置已保存并应用到旁观者核对');
        await loadSettings();
        loadModelProviders();
    } catch (e) {
        toast('error', '保存失败: ' + e.message);
    }
}

async function applyModelProvider(id, target) {
    try {
        const resp = await fetch(`${API_BASE}/model-providers/${id}/apply`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({target}),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || '应用失败');
        toast('success', target === 'verification' ? '已应用到旁观者核对' : '已应用到AI引擎');
        await loadSettings();
    } catch (e) {
        toast('error', '应用失败: ' + e.message);
    }
}

async function refreshModelProvider(id) {
    try {
        const resp = await fetch(`${API_BASE}/model-providers/${id}/refresh`, {method: 'POST'});
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || '刷新失败');
        toast('success', `已刷新 ${data.models?.length || 0} 个模型`);
        loadModelProviders();
    } catch (e) {
        toast('error', '刷新失败: ' + e.message);
    }
}

async function testModelProvider(id) {
    try {
        const resp = await fetch(`${API_BASE}/model-providers/${id}/test`, {method: 'POST'});
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || '测试失败');
        if (data.status === 'ok') {
            toast('success', `连接成功 ${data.model} (${data.latency_ms}ms)`);
        } else {
            toast('error', `连接失败 ${data.message || ''}`);
        }
    } catch (e) {
        toast('error', '测试失败: ' + e.message);
    }
}

function deleteModelProvider(id) {
    showConfirm('删除模型配置', '删除后不会影响当前已填写的设置，但配置池中将不可再应用。确定删除吗？', async () => {
        try {
            const resp = await fetch(`${API_BASE}/model-providers/${id}`, {method: 'DELETE'});
            const data = await resp.json();
            if (!resp.ok) throw new Error(data.detail || '删除失败');
            toast('success', '模型配置已删除');
            loadModelProviders();
        } catch (e) {
            toast('error', '删除失败: ' + e.message);
        }
    });
}

async function loadDataHealth() {
    const el = document.getElementById('dataHealthPanel');
    if (!el) return;
    try {
        const resp = await fetch(`${API_BASE}/data-health`);
        const data = await resp.json();
        el.innerHTML = `<div class="backup-status-grid">${(data.checks || []).map(item => `
            <div>
              <span>${escapeHtml(item.label)}</span>
              <strong class="${item.status === 'ok' ? 'up' : 'down'}">${item.status === 'ok' ? '正常' : '需处理'}</strong>
              <small>${escapeHtml(item.message || '')}</small>
              ${renderDataHealthDetails(item.details)}
            </div>`).join('')}</div>`;
    } catch (e) {
        el.innerHTML = `<div class="empty-state"><p>数据健康检查失败：${escapeHtml(e.message)}</p></div>`;
    }
}

async function loadDataAudit() {
    const el = document.getElementById('dataAuditPanel');
    if (!el) return;
    try {
        const resp = await fetch(`${API_BASE}/data-audit`);
        const data = await resp.json();
        const s = data.summary || {};
        const warnings = data.warnings || [];
        el.innerHTML = `<div class="quality-subtitle">数据审计中心</div>
        <div class="backup-status-grid">
            <div><span>审计分</span><strong class="${data.ok ? 'up' : 'down'}">${data.score || 0}</strong><small>${data.ok ? '数据可信' : '存在待处理项'}</small></div>
            <div><span>可修复项</span><strong class="${data.fixable_count ? 'down' : 'up'}">${data.fixable_count || 0}</strong><small>可使用一键修复</small></div>
            <div><span>持仓/交易</span><strong>${s.position_count || 0}/${s.trade_count || 0}</strong><small>持仓股票 / 交易流水</small></div>
            <div><span>AI/Hermes</span><strong>${s.report_count || 0}/${s.hermes_write_count || 0}</strong><small>报告 / Hermes写库</small></div>
        </div>
        ${warnings.length ? `<div class="quality-subtitle">审计提示</div>${warnings.slice(0, 6).map(item => `<div class="ai-task-error">${escapeHtml(item)}</div>`).join('')}` : '<div class="empty-state"><p>暂无审计提示</p></div>'}`;
    } catch (e) {
        el.innerHTML = `<div class="empty-state"><p>数据审计失败：${escapeHtml(e.message)}</p></div>`;
    }
}

function renderDataHealthDetails(details) {
    if (!Array.isArray(details) || !details.length) return '';
    return `<ul class="data-health-details">${details.slice(0, 4).map(item => {
        const parts = [];
        if (item.table_name) parts.push(item.table_name);
        if (item.account_id !== undefined) parts.push(`账户 ${item.account_id || '空'}`);
        if (item.code) parts.push(`${item.name || item.code} ${item.code}`);
        if (item.expected_shares !== undefined) parts.push(`应为 ${item.expected_shares} 股，当前 ${item.actual_shares} 股`);
        if (item.configured_cash !== undefined) parts.push(`设置 ${item.configured_cash}，流水 ${item.ledger_cash ?? '缺失'}`);
        if (item.count !== undefined) parts.push(`${item.count} 条`);
        return `<li>${escapeHtml(parts.join(' · ') || JSON.stringify(item))}</li>`;
    }).join('')}</ul>`;
}

async function loadSystemDiagnostics() {
    const el = document.getElementById('systemDiagnosticsPanel');
    if (!el) return;
    try {
        const resp = await fetch(`${API_BASE}/system-diagnostics`);
        const data = await resp.json();
        const s = data.summary || {};
        const warnings = data.warnings || [];
        el.innerHTML = `<div class="backup-status-grid">
            <div><span>模型配置</span><strong>${s.model_provider_count || 0}</strong><small>AI ${s.ai_model_count || 0} / 核对 ${s.verification_model_count || 0}</small></div>
            <div><span>任务样本</span><strong>${s.task_count || 0}</strong><small>最近200个任务</small></div>
            <div><span>风险提示</span><strong class="${s.risk_warning_count ? 'down' : 'up'}">${s.risk_warning_count || 0}</strong><small>持仓集中度/账户暴露</small></div>
            <div><span>系统状态</span><strong class="${s.warning_count ? 'down' : 'up'}">${s.warning_count ? '需处理' : '正常'}</strong><small>${escapeHtml(data.generated_at || '')}</small></div>
        </div>
        <div class="quality-subtitle">诊断提示</div>
        ${warnings.length ? warnings.map(item => `<div class="ai-task-error">${escapeHtml(item)}</div>`).join('') : '<div class="empty-state"><p>暂无诊断提示</p></div>'}`;
    } catch (e) {
        el.innerHTML = `<div class="empty-state"><p>系统诊断失败：${escapeHtml(e.message)}</p></div>`;
    }
}

async function fixDataHealth() {
    try {
        const resp = await fetch(`${API_BASE}/data-health/fix`, {method: 'POST'});
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || '修复失败');
        toast('success', `已修复：过期单 ${data.expired_orders || 0} 个，账户引用 ${data.account_refs_fixed || 0} 条，重算持仓 ${data.portfolio_recalculated || 0} 只`);
        loadDataHealth();
    } catch (e) {
        toast('error', '修复失败: ' + e.message);
    }
}

async function loadHermesToolPolicy() {
    const el = document.getElementById('hermesToolPolicyPanel');
    if (!el) return;
    try {
        const resp = await fetch(`${API_BASE}/hermes/tool-policy`);
        const data = await resp.json();
        const tools = data.tools || [];
        el.innerHTML = tools.map(item => `<div class="quality-report-row hermes-policy-row">
            <div>
              <b>${escapeHtml(hermesToolLabel(item.tool))}</b>
              <small>${escapeHtml(item.description || item.tool)}</small>
            </div>
            <select class="setting-select hermes-tool-policy" data-tool="${escapeAttr(item.tool)}">
              <option value="draft" ${item.mode === 'draft' ? 'selected' : ''}>草稿确认</option>
              <option value="disabled" ${item.mode === 'disabled' ? 'selected' : ''}>禁用</option>
            </select>
        </div>`).join('') || '<div class="empty-state"><p>暂无 Hermes 工具</p></div>';
    } catch (e) {
        el.innerHTML = `<div class="empty-state"><p>工具权限加载失败：${escapeHtml(e.message)}</p></div>`;
    }
}

function hermesToolLabel(tool) {
    return {
        add_watchlist: '添加自选股',
        record_trade: '记录交易',
        set_position: '校准持仓',
        create_conditional_order: '创建条件单',
    }[tool] || tool;
}

async function saveHermesToolPolicy() {
    const policy = {};
    document.querySelectorAll('.hermes-tool-policy').forEach(el => {
        policy[el.dataset.tool] = el.value;
    });
    try {
        const resp = await fetch(`${API_BASE}/hermes/tool-policy`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({policy}),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || '保存失败');
        toast('success', 'Hermes 工具权限已保存');
        loadHermesToolPolicy();
    } catch (e) {
        toast('error', '保存失败: ' + e.message);
    }
}

async function loadWorkspaceTemplates() {
    const el = document.getElementById('workspaceTemplateList');
    if (!el) return;
    try {
        const resp = await fetch(`${API_BASE}/workspace-templates`);
        const data = await resp.json();
        const templates = data.templates || [];
        el.innerHTML = templates.map(t => `<div class="quality-report-row">
            <div>
              <b>${escapeHtml(t.name)}</b>
              <small>${escapeHtml(t.description || '')}</small>
            </div>
            <button class="btn-secondary" onclick="applyWorkspaceTemplate('${escapeAttr(t.id)}')">应用</button>
        </div>`).join('') || '<div class="empty-state"><p>暂无模板</p></div>';
    } catch (e) {
        el.innerHTML = `<div class="empty-state"><p>模板读取失败：${escapeHtml(e.message)}</p></div>`;
    }
}

async function saveWorkspaceTemplate() {
    const name = prompt('模板名称:') || '';
    if (!name) return;
    try {
        const resp = await fetch(`${API_BASE}/workspace-templates`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({name}),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || '保存失败');
        toast('success', '模板已保存');
        loadWorkspaceTemplates();
    } catch (e) {
        toast('error', '保存失败: ' + e.message);
    }
}

async function applyWorkspaceTemplate(id) {
    try {
        const resp = await fetch(`${API_BASE}/workspace-templates/${id}/apply`, {method: 'POST'});
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || '应用失败');
        toast('success', '模板已应用');
        await loadSettings();
    } catch (e) {
        toast('error', '应用失败: ' + e.message);
    }
}

async function createBackup() {
    try {
        const resp = await fetch(`${API_BASE}/settings/backup/create`, { method: 'POST' });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || '备份失败');
        toast('success', `已创建备份: ${data.filename}`);
        loadBackupStatus();
    } catch (e) {
        toast('error', '备份失败: ' + e.message);
    }
}

function restoreLatestBackup() {
    showConfirm('恢复最近备份', '将把最近一次备份中的自选、持仓、条件单和设置导入当前数据库。建议先创建新备份。确定继续吗？', async () => {
        try {
            const resp = await fetch(`${API_BASE}/settings/backup/restore-latest`, { method: 'POST' });
            const data = await resp.json();
            if (!resp.ok) throw new Error(data.detail || '恢复失败');
            const imp = data.imported || {};
            toast('success', `恢复完成: 自选${imp.watchlist || 0}/持仓${imp.portfolio || 0}/条件单${imp.orders || 0}/设置${imp.settings || 0}`);
            loadSettings();
            loadBackupStatus();
        } catch (e) {
            toast('error', '恢复失败: ' + e.message);
        }
    });
}

// ── 重置设置 ──
async function resetSettings() {
    showConfirm('重置设置', '确定要将所有设置恢复为默认值吗？', async () => {
        try {
            await fetch(`${API_BASE}/settings/reset`, { method: 'POST' });
            await loadSettings();
            toast('success', '已重置为默认设置');
        } catch (e) {
            toast('error', '重置失败');
        }
    });
}

// ── 清空数据 ──
function confirmClearAll() {
    showConfirm('清空所有数据', '此操作不可撤销！将删除所有自选股、持仓、条件单和历史报告。', async () => {
        try {
            const resp = await fetch(`${API_BASE}/settings/clear-all`, { method: 'POST' });
            const data = await resp.json();
            const c = data.cleared;
            toast('success', `已清空: 自选${c.watchlist}/持仓${c.portfolio}/交易${c.trades}/条件单${c.conditional_orders}/报告${c.analysis_reports}`);
        } catch (e) {
            toast('error', '清空失败');
        }
    });
}

// ── 确认弹窗 ──
function showConfirm(title, message, onConfirm) {
    document.getElementById('confirmTitle').textContent = title;
    document.getElementById('confirmMessage').textContent = message;
    document.getElementById('confirmOverlay').classList.add('show');
    const actionBtn = document.getElementById('confirmAction');
    actionBtn.onclick = () => {
        closeConfirm();
        onConfirm();
    };
}

function closeConfirm() {
    document.getElementById('confirmOverlay').classList.remove('show');
}

// ── Toast通知 ──
function toast(type, message) {
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = message;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 3000);
}

// ── 初始化 ──
loadSettings();
loadAccountList();
loadBackupStatus();
loadModelProviders();
loadWorkerPoolConfig();
loadHermesToolPolicy();
loadDataAudit();
loadDataHealth();
loadSystemDiagnostics();
loadWorkspaceTemplates();

// ── 账户管理 ──
async function loadAccountList() {
    try {
        const resp = await fetch(`${API_BASE}/accounts`);
        const data = await resp.json();
        const el = document.getElementById('accountList');
        if (!el) return;
        el.innerHTML = data.accounts.map(a =>
            `<div style="display:flex;align-items:center;gap:8px;padding:4px 0;font-size:0.85rem;">
              <span style="font-weight:600;">${escapeHtml(a.name)}</span>
              <span style="color:var(--text-muted);font-size:0.75rem;">${escapeHtml(a.broker || '')}</span>
              <span style="color:var(--text-muted);font-size:0.7rem;">(${escapeHtml(a.id)})</span>
            </div>`
        ).join('');
    } catch(e) {}
}

async function addAccount() {
    const name = prompt('账户名称（如：方正证券）:');
    if (!name) return;
    const broker = prompt('券商名称（可选）:') || '';
    try {
        const resp = await fetch(`${API_BASE}/accounts`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, broker }),
        });
        const data = await resp.json();
        if (data.success) {
            toast('success', '账户已创建');
            loadAccountList();
            loadAccounts(); // refresh the global switcher too
        } else {
            toast('error', '创建失败: ' + (data.error || ''));
        }
    } catch(e) {
        toast('error', '创建失败: ' + e.message);
    }
}

// ============================================================
// Phase 6: 模型模式选择
// ============================================================

function setModelMode(mode) {
    // 更新按钮状态
    document.querySelectorAll('.model-mode-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.mode === mode);
    });
    // 更新隐藏input
    document.getElementById('set-model_mode').value = mode;
}

// 页面加载时恢复模型模式
document.addEventListener('DOMContentLoaded', () => {
    const saved = localStorage.getItem('model_mode') || 'balanced';
    setModelMode(saved);
});

// 保存时包含模型模式
const _origSaveSettings = typeof saveSettings === 'function' ? saveSettings : null;
if (_origSaveSettings) {
    // 会在saveSettings中自动读取set-model_mode的值
}

Object.assign(window, {
    loadModelProviders,
    saveCurrentModelProvider,
    saveVerificationModelProvider,
    applyModelProvider,
    refreshModelProvider,
    testModelProvider,
    deleteModelProvider,
    loadWorkerPoolConfig,
    addWorkerPoolRow,
    removeWorkerPoolRow,
    saveWorkerPoolConfig,
    loadHermesToolPolicy,
    saveHermesToolPolicy,
    loadDataAudit,
    loadDataHealth,
    loadSystemDiagnostics,
    fixDataHealth,
    loadWorkspaceTemplates,
    saveWorkspaceTemplate,
    applyWorkspaceTemplate,
});
