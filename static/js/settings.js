/**
 * 设置页JS — Tab切换 + 设置CRUD + 导入导出 + 通知管理
 */
const API_BASE = '/api';

// ── 模型配置 ──
const MODEL_CATALOG = {
    deepseek: {
        deep: ['deepseek-v4-pro', 'deepseek-v4-flash', 'deepseek-reasoner', 'deepseek-chat'],
        quick: ['deepseek-v4-flash', 'deepseek-v4-pro', 'deepseek-chat', 'deepseek-reasoner'],
    },
    openai: {
        deep: ['o1', 'gpt-4o', 'gpt-4-turbo'],
        quick: ['gpt-4o-mini', 'gpt-4o', 'gpt-3.5-turbo'],
    },
    anthropic: {
        deep: ['claude-3-opus-20240229', 'claude-3-sonnet-20240229'],
        quick: ['claude-3-haiku-20240307', 'claude-3-sonnet-20240229'],
    },
    qwen: {
        deep: ['qwen-max', 'qwen-plus'],
        quick: ['qwen-turbo', 'qwen-plus'],
    },
    glm: {
        deep: ['glm-4-plus', 'glm-4'],
        quick: ['glm-4-flash', 'glm-4'],
    },
    xai: {
        deep: ['grok-2', 'grok-2-mini'],
        quick: ['grok-2-mini', 'grok-2'],
    },
    minimax: {
        deep: ['abab6.5-chat', 'abab6-chat'],
        quick: ['abab6.5s-chat', 'abab6-chat'],
    },
    ollama: {
        deep: ['llama3:70b', 'llama3:8b'],
        quick: ['llama3:8b', 'mistral'],
    },
    google: {
        deep: ['gemini-1.5-pro', 'gemini-1.5-flash'],
        quick: ['gemini-1.5-flash', 'gemini-1.0-pro'],
    },
};

let currentSettings = {};

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
        applySettings(currentSettings);
        updateModelOptions();
        toast('success', '设置已加载');
    } catch (e) {
        console.error('加载设置失败:', e);
    }
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
}

// ── 收集设置 ──
function collectSettings() {
    const result = {};
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

function toggleApiKey(btn) {
    const input = btn.parentElement.querySelector('input[type]');
    const isPassword = input.type === 'password';
    input.type = isPassword ? 'text' : 'password';
    btn.textContent = isPassword ? '🙈' : '👁';
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
            toast('success', '✅ 设置已保存');
            currentSettings = settings;
        } else {
            toast('error', '保存失败');
        }
    } catch (e) {
        toast('error', '保存失败: ' + e.message);
    }
}

// ── 模型下拉联动 ──
function updateModelOptions() {
    const provider = document.getElementById('set-llm_provider').value;
    const catalog = MODEL_CATALOG[provider] || MODEL_CATALOG.deepseek;
    const deepSelect = document.getElementById('set-deep_think_model');
    const quickSelect = document.getElementById('set-quick_think_model');
    const currentDeep = deepSelect.value;
    const currentQuick = quickSelect.value;
    deepSelect.innerHTML = catalog.deep.map(m => `<option value="${m}" ${m === currentDeep ? 'selected' : ''}>${m}</option>`).join('');
    quickSelect.innerHTML = catalog.quick.map(m => `<option value="${m}" ${m === currentQuick ? 'selected' : ''}>${m}</option>`).join('');
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
            resultSpan.textContent = `✓ ${data.message} (${data.latency_ms}ms)`;
        } else {
            resultSpan.className = 'test-result error';
            resultSpan.textContent = `✗ ${data.message}`;
        }
    } catch (e) {
        resultSpan.className = 'test-result error';
        resultSpan.textContent = '✗ 网络错误';
    }
}

async function testVerificationConnection() {
    const resultSpan = document.getElementById('testVerificationResult');
    resultSpan.textContent = '测试中...';
    resultSpan.className = 'test-result';
    try {
        const resp = await fetch(`${API_BASE}/settings/test-verification`, { method: 'POST' });
        const data = await resp.json();
        if (data.status === 'ok') {
            resultSpan.className = 'test-result ok';
            resultSpan.textContent = `✓ ${data.message}`;
        } else {
            resultSpan.className = 'test-result error';
            resultSpan.textContent = `✗ ${data.message}`;
        }
    } catch (e) {
        resultSpan.className = 'test-result error';
        resultSpan.textContent = '✗ 网络错误';
    }
}

// ── API密钥显示/隐藏 ──
function toggleApiKeyVisibility() {
    const el = document.getElementById('set-api_key');
    el.type = el.type === 'password' ? 'text' : 'password';
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
            btn.textContent = '✅ 已授权';
            toast('success', '通知权限已授权');
            // 测试通知
            new Notification('🐂 炒股小牛马', {
                body: '通知已启用！条件单触发时会自动提醒你。',
                icon: '🐂',
                tag: 'test',
            });
        } else {
            btn.textContent = '❌ 已拒绝';
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
            icon: '🐂',
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
        toast('success', '📦 数据已导出');
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
            toast('success', `✅ 导入完成: 自选${imp.watchlist}条/持仓${imp.portfolio}条/条件单${imp.orders}条/设置${imp.settings}条`);
            loadSettings(); // 重新加载
        } else {
            toast('error', '导入失败');
        }
    } catch (e) {
        toast('error', '文件格式错误: ' + e.message);
    }
    input.value = '';
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
    showConfirm('⚠️ 清空所有数据', '此操作不可撤销！将删除所有自选股、持仓、条件单和历史报告。', async () => {
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

// ── 账户管理 ──
async function loadAccountList() {
    try {
        const resp = await fetch(`${API_BASE}/portfolio/accounts`);
        const data = await resp.json();
        const el = document.getElementById('accountList');
        if (!el) return;
        el.innerHTML = data.accounts.map(a =>
            `<div style="display:flex;align-items:center;gap:8px;padding:4px 0;font-size:0.85rem;">
              <span style="font-weight:600;">${a.name}</span>
              <span style="color:var(--text-muted);font-size:0.75rem;">${a.broker || ''}</span>
              <span style="color:var(--text-muted);font-size:0.7rem;">(${a.id})</span>
            </div>`
        ).join('');
    } catch(e) {}
}

async function addAccount() {
    const name = prompt('账户名称（如：方正证券）:');
    if (!name) return;
    const broker = prompt('券商名称（可选）:') || '';
    try {
        const resp = await fetch(`${API_BASE}/portfolio/accounts`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, broker }),
        });
        const data = await resp.json();
        if (data.success) {
            toast('success', '✅ 账户已创建');
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
