/* 全局工具函数 */
async function requestJson(url, options = {}) {
  const resp = await fetch(url, options);
  const contentType = resp.headers.get('content-type') || '';
  const text = await resp.text();
  let payload = null;

  if (contentType.includes('application/json') && text) {
    try {
      payload = JSON.parse(text);
    } catch (e) {
      throw new Error(`JSON 解析失败: ${url}`);
    }
  } else if (text) {
    const label = text.replace(/\s+/g, ' ').slice(0, 80);
    throw new Error(`接口返回非 JSON: ${url}${label ? ' · ' + label : ''}`);
  }

  if (!resp.ok) {
    const detail = payload?.detail || payload?.error || payload?.message || `HTTP ${resp.status}`;
    throw new Error(`${detail}: ${url}`);
  }
  return payload || {};
}

const API = {
  request(url, options = {}) { return requestJson(url, options); },
  get(url) { return requestJson(url); },
  post(url, data) { return requestJson(url, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}); },
  put(url, data) { return requestJson(url, {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}); },
  del(url) { return requestJson(url, {method:'DELETE'}); },
};

const apiGet = API.get;
const apiPost = API.post;

window.API = API;
window.apiGet = apiGet;
window.apiPost = apiPost;
window.requestJson = requestJson;

function formatMoney(n) { return n == null ? '--' : n.toLocaleString('zh-CN', {style:'currency',currency:'CNY'}); }
function formatPct(n) { return n == null ? '--' : (n >= 0 ? '+' : '') + n.toFixed(2) + '%'; }
function formatPrice(n) { return n == null ? '--' : n.toFixed(2); }
function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, '&#96;');
}
function safeUrl(value) {
  const url = String(value ?? '').trim();
  if (!url) return '';
  try {
    const parsed = new URL(url, window.location.origin);
    return ['http:', 'https:'].includes(parsed.protocol) ? parsed.href : '';
  } catch (_) {
    return '';
  }
}
function formatPnl(n) {
  if (n == null || n === 0) return '--';
  const sign = n > 0 ? '+' : '';
  if (Math.abs(n) >= 10000) return sign + (n / 10000).toFixed(2) + '万';
  return sign + n.toFixed(2);
}

function priceClass(change) {
  if (change > 0) return 'up';
  if (change < 0) return 'down';
  return 'flat';
}

function toggleSearch() {
  const box = document.getElementById('searchBox');
  box.style.display = box.style.display === 'none' ? 'block' : 'none';
  if (box.style.display === 'block') document.getElementById('searchInput').focus();
}

function refreshAll() { location.reload(); }

function classifyAdaptiveViewport() {
  const width = window.innerWidth || document.documentElement.clientWidth || 0;
  const height = window.innerHeight || document.documentElement.clientHeight || 0;
  const ratio = width && height ? width / height : 1;
  const isPortrait = height > width;
  const isCoarse = window.matchMedia?.('(pointer: coarse)').matches || false;
  const minSide = Math.min(width, height);
  const maxSide = Math.max(width, height);
  const isTabletSize = (isCoarse && minSide >= 700 && maxSide <= 1400) || (minSide >= 760 && maxSide <= 1366);
  const classes = [
    'screen-portrait',
    'screen-landscape',
    'viewport-watch-portrait',
    'viewport-watch-portrait-156',
    'viewport-watch-portrait-18',
    'viewport-desktop-wide',
    'viewport-desktop-xl',
    'viewport-ipad',
    'viewport-ipad-portrait',
    'viewport-ipad-landscape',
    'viewport-ultrawide',
  ];
  document.body.classList.remove(...classes);
  document.body.classList.add(isPortrait ? 'screen-portrait' : 'screen-landscape');
  if (isPortrait && width >= 700 && height >= 1050 && !isTabletSize) {
    document.body.classList.add('viewport-watch-portrait');
    document.body.classList.add(width <= 940 ? 'viewport-watch-portrait-156' : 'viewport-watch-portrait-18');
  }
  if (!isPortrait && width >= 1800) document.body.classList.add('viewport-desktop-wide');
  if (!isPortrait && width >= 2400) document.body.classList.add('viewport-desktop-xl');
  if (!isPortrait && ratio >= 2.05) document.body.classList.add('viewport-ultrawide');
  if (isTabletSize) {
    document.body.classList.add('viewport-ipad');
    document.body.classList.add(isPortrait ? 'viewport-ipad-portrait' : 'viewport-ipad-landscape');
  }
  return { width, height, isPortrait, isTabletSize, autoDense: isPortrait && width >= 700 && height >= 1050 };
}

function applyDenseMode(enabled) {
  const adaptive = classifyAdaptiveViewport();
  const stored = localStorage.getItem('dense_watch_mode');
  const hasManualPreference = stored === 'true' || stored === 'false';
  const effective = hasManualPreference ? !!enabled : adaptive.autoDense;
  document.body.classList.toggle('dense-watch-mode', effective);
  document.body.classList.toggle('auto-dense-watch-mode', !hasManualPreference && adaptive.autoDense);
  const label = document.getElementById('denseModeName');
  if (label) label.textContent = effective ? (hasManualPreference ? '竖屏' : '自动') : '标准';
}

function toggleDenseMode() {
  const current = document.body.classList.contains('dense-watch-mode');
  const next = !current;
  localStorage.setItem('dense_watch_mode', next ? 'true' : 'false');
  applyDenseMode(next);
}

window.toggleDenseMode = toggleDenseMode;
document.addEventListener('DOMContentLoaded', () => {
  applyDenseMode(localStorage.getItem('dense_watch_mode') === 'true');
  let adaptiveTimer = null;
  window.addEventListener('resize', () => {
    clearTimeout(adaptiveTimer);
    adaptiveTimer = setTimeout(() => applyDenseMode(localStorage.getItem('dense_watch_mode') === 'true'), 120);
  });
});

// ── Account Management ──────────────────────────────────────
let currentAccountId = localStorage.getItem('accountId') || 'default';

async function loadAccounts() {
    try {
        const resp = await fetch('/api/accounts');
        const data = await resp.json();
        const sel = document.getElementById('accountSwitcher');
        if (!sel) return;
        sel.innerHTML = data.accounts.map(a =>
            `<option value="${escapeAttr(a.id)}" ${a.id===currentAccountId?'selected':''}>${escapeHtml(a.name)}</option>`
        ).join('');
        sel.onchange = () => {
            currentAccountId = sel.value;
            localStorage.setItem('accountId', currentAccountId);
            location.reload();
        };
    } catch(e) {}
}

document.addEventListener('DOMContentLoaded', loadAccounts);
document.addEventListener('DOMContentLoaded', loadOnboardingStatus);
document.addEventListener('DOMContentLoaded', initGlobalHermesPanel);

function showToast(msg, type='info') {
    let container = document.getElementById('toast-container');
    if (!container) {
      container = document.createElement('div');
      container.id = 'toast-container';
      container.style.cssText = 'position:fixed;top:20px;right:20px;z-index:9999;display:flex;flex-direction:column;gap:8px;pointer-events:none;';
      document.body.appendChild(container);
    }
    const t = document.createElement('div');
    t.style.cssText = `padding:12px 20px;border-radius:8px;font-size:0.9rem;color:#fff;background:${type==='error'?'#E07A5F':type==='success'?'#52B788':type==='warning'?'#F4A261':'#3D405B'};box-shadow:0 4px 12px rgba(0,0,0,0.3);opacity:0;transform:translateX(20px);transition:all 0.2s;pointer-events:auto;max-width:360px;`;
    t.textContent = msg;
    container.appendChild(t);
    requestAnimationFrame(() => { t.style.opacity = '1'; t.style.transform = 'translateX(0)'; });
    setTimeout(() => { t.style.opacity = '0'; t.style.transform = 'translateX(20px)'; setTimeout(() => t.remove(), 250); }, 3000);
}

async function loadOnboardingStatus() {
    if (sessionStorage.getItem('onboarding_hidden') === '1') return;
    try {
        const data = await API.get('/api/settings/onboarding');
        if (!data || data.completed || !data.pending_count) return;
        renderOnboardingPanel(data);
    } catch (e) {
        console.warn('首次配置引导加载失败', e);
    }
}

function renderOnboardingPanel(data) {
    if (document.getElementById('onboardingPanel')) return;
    const overlay = document.createElement('div');
    overlay.id = 'onboardingPanel';
    overlay.innerHTML = `
      <div class="onboarding-backdrop"></div>
      <section class="onboarding-card" role="dialog" aria-modal="true" aria-labelledby="onboardingTitle">
        <header class="onboarding-head">
          <div>
            <div class="onboarding-kicker">首次配置</div>
            <h2 id="onboardingTitle">把工作台配置完整</h2>
          </div>
          <button class="onboarding-icon-btn" type="button" aria-label="关闭" data-action="hide">×</button>
        </header>
        <div class="onboarding-progress">
          <span style="width:${Math.max(0, Math.min(100, 100 - data.pending_count * 20))}%"></span>
        </div>
        <div class="onboarding-steps">
          ${(data.steps || []).map(step => `
            <div class="onboarding-step ${step.status === 'ok' ? 'done' : 'pending'}">
              <span class="onboarding-dot"></span>
              <div>
                <strong>${escapeHtml(step.label)}</strong>
                <p>${escapeHtml(step.message || '')}</p>
              </div>
            </div>
          `).join('')}
        </div>
        <footer class="onboarding-actions">
          <a class="btn-secondary" href="/portfolio">持仓现金</a>
          <a class="btn-secondary" href="/settings">打开设置</a>
          <button class="btn-secondary" type="button" data-action="hide">稍后</button>
          <button class="btn-primary" type="button" data-action="complete">完成向导</button>
        </footer>
      </section>
    `;
    const style = document.createElement('style');
    style.id = 'onboardingStyles';
    style.textContent = `
      #onboardingPanel{position:fixed;inset:0;z-index:9000;display:flex;align-items:center;justify-content:center;padding:20px;}
      .onboarding-backdrop{position:absolute;inset:0;background:rgba(3,8,17,.62);backdrop-filter:blur(10px);}
      .onboarding-card{position:relative;width:min(560px,100%);border:1px solid rgba(111,168,220,.24);background:#0d1623;border-radius:12px;box-shadow:0 24px 80px rgba(0,0,0,.42);padding:22px;color:#e9eef7;}
      .onboarding-head{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;margin-bottom:16px;}
      .onboarding-kicker{font-size:.78rem;color:#6fa8dc;margin-bottom:6px;}
      .onboarding-head h2{font-size:1.35rem;margin:0;letter-spacing:0;}
      .onboarding-icon-btn{width:32px;height:32px;border:1px solid rgba(255,255,255,.12);background:#111d2b;color:#b8c3d7;border-radius:8px;font-size:1.1rem;cursor:pointer;}
      .onboarding-progress{height:6px;background:#111d2b;border-radius:999px;overflow:hidden;margin-bottom:16px;}
      .onboarding-progress span{display:block;height:100%;background:#6fa8dc;border-radius:999px;}
      .onboarding-steps{display:grid;gap:10px;}
      .onboarding-step{display:flex;gap:12px;align-items:flex-start;padding:11px 12px;border:1px solid rgba(255,255,255,.08);border-radius:8px;background:#0a111c;}
      .onboarding-dot{width:10px;height:10px;border-radius:50%;margin-top:5px;background:#f2b84b;flex:0 0 auto;}
      .onboarding-step.done .onboarding-dot{background:#52b788;}
      .onboarding-step strong{display:block;font-size:.92rem;margin-bottom:3px;}
      .onboarding-step p{margin:0;color:#8794aa;font-size:.82rem;line-height:1.5;}
      .onboarding-actions{display:flex;justify-content:flex-end;gap:10px;flex-wrap:wrap;margin-top:18px;}
      .onboarding-actions a,.onboarding-actions button{height:36px;padding:0 14px;border-radius:8px;border:1px solid rgba(255,255,255,.12);display:inline-flex;align-items:center;text-decoration:none;font-size:.86rem;cursor:pointer;}
      .onboarding-actions .btn-secondary{background:#101a28;color:#b8c3d7;}
      .onboarding-actions .btn-primary{background:#6fa8dc;color:#06101d;border-color:#6fa8dc;font-weight:700;}
      @media (max-width:640px){#onboardingPanel{align-items:flex-end;padding:12px}.onboarding-card{padding:18px}.onboarding-actions{justify-content:stretch}.onboarding-actions a,.onboarding-actions button{flex:1;justify-content:center;}}
    `;
    document.head.appendChild(style);
    document.body.appendChild(overlay);
    overlay.addEventListener('click', async event => {
        const action = event.target?.dataset?.action;
        if (!action) return;
        if (action === 'hide') {
            sessionStorage.setItem('onboarding_hidden', '1');
            overlay.remove();
        }
        if (action === 'complete') {
            try {
                await API.post('/api/settings/onboarding/complete', {});
                overlay.remove();
                showToast('首次配置向导已完成', 'success');
            } catch (e) {
                showToast(e.message || '保存失败', 'error');
            }
        }
    });
}

function toggleGlobalHermesPanel(force) {
    const panel = document.getElementById('globalHermesPanel');
    if (!panel) return;
    const shouldOpen = typeof force === 'boolean' ? force : !panel.classList.contains('open');
    panel.classList.toggle('open', shouldOpen);
    panel.setAttribute('aria-hidden', shouldOpen ? 'false' : 'true');
    if (shouldOpen) {
        setTimeout(() => document.getElementById('globalHermesInput')?.focus(), 80);
    }
}

function appendGlobalHermesMessage(role, message) {
    const log = document.getElementById('globalHermesLog');
    if (!log) return;
    const node = document.createElement('div');
    node.className = `global-hermes-message ${role}`;
    node.textContent = message;
    log.appendChild(node);
    log.scrollTop = log.scrollHeight;
}

async function sendGlobalHermesMessage() {
    const input = document.getElementById('globalHermesInput');
    if (!input) return;
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    appendGlobalHermesMessage('user', text);
    appendGlobalHermesMessage('assistant pending', '正在交给 Hermes 识别...');
    try {
        const sessionId = localStorage.getItem('global_hermes_session_id') || null;
        const data = await API.post('/api/hermes/message', { message: text, session_id: sessionId });
        if (data.session_id) localStorage.setItem('global_hermes_session_id', data.session_id);
        const pending = document.querySelector('.global-hermes-message.pending');
        if (pending) pending.remove();
        appendGlobalHermesMessage('assistant', data.message || data.reply || data.summary || '已生成响应，请在完整对话台查看详情。');
        if (data.draft || data.task) {
            appendGlobalHermesMessage('assistant', '检测到需要确认的草稿或多步任务，建议打开完整对话台继续确认。');
        }
    } catch (e) {
        const pending = document.querySelector('.global-hermes-message.pending');
        if (pending) pending.remove();
        appendGlobalHermesMessage('assistant', `发送失败：${e.message}`);
    }
}

function initGlobalHermesPanel() {
    const input = document.getElementById('globalHermesInput');
    if (!input) return;
    input.addEventListener('keydown', event => {
        if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            sendGlobalHermesMessage();
        }
    });
}

window.toggleGlobalHermesPanel = toggleGlobalHermesPanel;
window.sendGlobalHermesMessage = sendGlobalHermesMessage;
