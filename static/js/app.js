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

function formatMoney(n) { return n == null ? '--' : n.toLocaleString('zh-CN', {style:'currency',currency:'CNY', minimumFractionDigits: 3, maximumFractionDigits: 3}); }
function formatPct(n) { return n == null ? '--' : (n >= 0 ? '+' : '') + n.toFixed(3) + '%'; }
function formatPrice(n) { return n == null ? '--' : n.toFixed(3); }
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
  if (Math.abs(n) >= 10000) return sign + (n / 10000).toFixed(3) + '万';
  return sign + n.toFixed(3);
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
let currentLoginUser = null;

async function loadLoginSession() {
    try {
        const resp = await fetch('/api/auth/session');
        if (!resp.ok) return null;
        const data = await resp.json();
        currentLoginUser = data.user || null;
        const badge = document.getElementById('loginUserBadge');
        if (badge && currentLoginUser) {
            const name = currentLoginUser.display_name || currentLoginUser.username || currentLoginUser.id || '本机账户';
            badge.textContent = `登录账户：${name}`;
            badge.title = currentLoginUser.authenticated ? '已登录账户' : '兼容模式：本机默认登录账户';
        }
        if (
            currentLoginUser?.authenticated &&
            currentLoginUser?.must_change_credentials &&
            !['/login', '/account'].includes(window.location.pathname)
        ) {
            window.location.href = '/account';
        }
        return currentLoginUser;
    } catch(e) {
        return null;
    }
}

async function loadAccounts() {
    try {
        await loadLoginSession();
        const resp = await fetch('/api/accounts');
        const data = await resp.json();
        const sel = document.getElementById('accountSwitcher');
        if (!sel) return;
        const accounts = data.accounts || [];
        if (!accounts.some(a => a.id === currentAccountId)) {
            currentAccountId = accounts[0]?.id || currentLoginUser?.default_securities_account_id || 'default';
            localStorage.setItem('accountId', currentAccountId);
        }
        sel.innerHTML = accounts.map(a =>
            `<option value="${escapeAttr(a.id)}" ${a.id===currentAccountId?'selected':''}>证券账户：${escapeHtml(a.name)}</option>`
        ).join('');
        sel.onchange = () => {
            currentAccountId = sel.value;
            localStorage.setItem('accountId', currentAccountId);
            location.reload();
        };
    } catch(e) {}
}

async function logoutLoginUser() {
    await fetch('/api/auth/logout', { method: 'POST' });
    localStorage.removeItem('accountId');
    location.href = '/login';
}

window.logoutLoginUser = logoutLoginUser;
window.loadAccounts = loadAccounts;

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
    if (['/login', '/account'].includes(window.location.pathname)) return;
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
        <div class="onboarding-assets">
          <div class="onboarding-section-title">初始化自选股</div>
          <div class="onboarding-import-row">
            <input type="file" id="onboardingWatchlistMd" accept=".md,.markdown,text/markdown,text/plain">
            <button type="button" class="btn-secondary mini" data-action="import-watchlist-md">读取并导入</button>
          </div>
          <textarea id="onboardingWatchlistMdText" class="onboarding-md-text" rows="4" placeholder="如果文件选择器不可用，也可以直接粘贴 Markdown 内容。&#10;贵州茅台 600519&#10;平安银行 000001"></textarea>
          <p class="onboarding-hint">支持每行“股票名称 股票代码”或“股票名称+股票代码”，例如：贵州茅台 600519。</p>
        </div>
        <div class="onboarding-assets">
          <div class="onboarding-section-title">初始化资产</div>
          <div class="onboarding-form-grid">
            <label>
              <span>可用资金</span>
              <input type="number" min="0" step="0.001" id="onboardingCash" placeholder="例如 100000">
            </label>
            <label>
              <span>资金备注</span>
              <input type="text" id="onboardingCashNote" placeholder="例如 初始资金 / 银证转入">
            </label>
          </div>
          <div class="onboarding-position-head">
            <span>初始持仓</span>
            <button type="button" class="btn-secondary mini" data-action="add-position-row">添加一行</button>
          </div>
          <div class="onboarding-position-list" id="onboardingPositionRows">
            ${renderOnboardingPositionRow()}
          </div>
          <p class="onboarding-hint">初始持仓会按“买入交易”写入交易流水，并自动生成持仓成本。只填现金也可以。</p>
        </div>
        <footer class="onboarding-actions">
          <button class="btn-primary" type="button" data-action="save-assets">保存资产初始化</button>
          <a class="btn-secondary" href="/portfolio">打开持仓页</a>
          <a class="btn-secondary" href="/settings">打开设置</a>
          <button class="btn-secondary" type="button" data-action="hide">稍后</button>
          <button class="btn-primary" type="button" data-action="complete">完成向导</button>
        </footer>
      </section>
    `;
    const style = document.createElement('style');
    style.id = 'onboardingStyles';
    style.textContent = `
      #onboardingPanel{position:fixed;inset:0;z-index:9000;display:flex;align-items:center;justify-content:center;padding:20px;overflow:auto;}
      .onboarding-backdrop{position:absolute;inset:0;background:rgba(3,8,17,.62);backdrop-filter:blur(10px);}
      .onboarding-card{position:relative;width:min(760px,100%);max-height:calc(100vh - 40px);overflow:auto;border:1px solid rgba(111,168,220,.24);background:#0d1623;border-radius:12px;box-shadow:0 24px 80px rgba(0,0,0,.42);padding:22px;color:#e9eef7;}
      .onboarding-head{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;margin-bottom:16px;}
      .onboarding-kicker{font-size:.78rem;color:#6fa8dc;margin-bottom:6px;}
      .onboarding-head h2{font-size:1.35rem;margin:0;letter-spacing:0;}
      .onboarding-icon-btn{width:32px;height:32px;border:1px solid rgba(255,255,255,.12);background:#111d2b;color:#b8c3d7;border-radius:8px;font-size:1.1rem;cursor:pointer;}
      .onboarding-progress{height:6px;background:#111d2b;border-radius:999px;overflow:hidden;margin-bottom:16px;}
      .onboarding-progress span{display:block;height:100%;background:#6fa8dc;border-radius:999px;}
      .onboarding-steps{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;}
      .onboarding-step{display:flex;gap:12px;align-items:flex-start;padding:11px 12px;border:1px solid rgba(255,255,255,.08);border-radius:8px;background:#0a111c;}
      .onboarding-dot{width:10px;height:10px;border-radius:50%;margin-top:5px;background:#f2b84b;flex:0 0 auto;}
      .onboarding-step.done .onboarding-dot{background:#52b788;}
      .onboarding-step strong{display:block;font-size:.92rem;margin-bottom:3px;}
      .onboarding-step p{margin:0;color:#8794aa;font-size:.82rem;line-height:1.5;}
      .onboarding-assets{margin-top:14px;border:1px solid rgba(255,255,255,.08);border-radius:10px;background:#0a111c;padding:13px;}
      .onboarding-section-title{font-size:.9rem;font-weight:800;color:#e9eef7;margin-bottom:10px;}
      .onboarding-form-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px;}
      .onboarding-assets label span{display:block;font-size:.74rem;color:#8794aa;margin-bottom:5px;}
      .onboarding-assets input{width:100%;height:36px;border:1px solid rgba(255,255,255,.12);border-radius:8px;background:#101a28;color:#e9eef7;padding:0 10px;font-size:.86rem;}
      .onboarding-md-text{width:100%;margin-top:8px;border:1px solid rgba(255,255,255,.12);border-radius:8px;background:#101a28;color:#e9eef7;padding:9px 10px;font-size:.84rem;line-height:1.5;resize:vertical;min-height:84px;}
      .onboarding-import-row{display:grid;grid-template-columns:1fr auto;gap:8px;align-items:center;}
      .onboarding-import-row input[type="file"]{padding:7px 10px;}
      .onboarding-assets input:focus,.onboarding-md-text:focus{outline:none;border-color:#6fa8dc;}
      .onboarding-position-head{display:flex;justify-content:space-between;align-items:center;gap:10px;margin:6px 0 8px;color:#e9eef7;font-weight:700;font-size:.84rem;}
      .onboarding-position-list{display:grid;gap:8px;}
      .onboarding-position-row{display:grid;grid-template-columns:1fr 1.2fr .9fr .9fr 32px;gap:8px;align-items:end;}
      .onboarding-remove-row{height:36px;border:1px solid rgba(255,255,255,.12);background:#101a28;color:#8794aa;border-radius:8px;cursor:pointer;}
      .onboarding-hint{margin:9px 0 0;color:#8794aa;font-size:.76rem;line-height:1.5;}
      .onboarding-actions{display:flex;justify-content:flex-end;gap:10px;flex-wrap:wrap;margin-top:18px;}
      .onboarding-actions a,.onboarding-actions button{height:36px;padding:0 14px;border-radius:8px;border:1px solid rgba(255,255,255,.12);display:inline-flex;align-items:center;text-decoration:none;font-size:.86rem;cursor:pointer;}
      .onboarding-actions .btn-primary:first-child{margin-right:auto;}
      .onboarding-actions .btn-secondary{background:#101a28;color:#b8c3d7;}
      .onboarding-assets .btn-secondary.mini{height:30px;padding:0 10px;border:1px solid rgba(255,255,255,.12);background:#101a28;color:#b8c3d7;border-radius:8px;font-size:.78rem;cursor:pointer;}
      .onboarding-actions .btn-primary{background:#6fa8dc;color:#06101d;border-color:#6fa8dc;font-weight:700;}
      @media (max-width:760px){.onboarding-steps{grid-template-columns:1fr}.onboarding-form-grid,.onboarding-position-row{grid-template-columns:1fr}.onboarding-remove-row{grid-column:span 1;justify-self:start;width:42px}}
      @media (max-width:640px){#onboardingPanel{align-items:flex-end;padding:12px}.onboarding-card{padding:18px;max-height:calc(100vh - 24px);overflow:auto}.onboarding-import-row{grid-template-columns:1fr}.onboarding-actions{justify-content:stretch}.onboarding-actions a,.onboarding-actions button{flex:1;justify-content:center}.onboarding-actions .btn-primary:first-child{margin-right:0;flex-basis:100%;}}
    `;
    document.head.appendChild(style);
    document.body.appendChild(overlay);
    overlay.addEventListener('click', async event => {
        const action = event.target?.dataset?.action;
        if (!action) return;
        if (action === 'add-position-row') {
            document.getElementById('onboardingPositionRows')?.insertAdjacentHTML('beforeend', renderOnboardingPositionRow());
        }
        if (action === 'remove-position-row') {
            const rows = overlay.querySelectorAll('.onboarding-position-row');
            if (rows.length > 1) event.target.closest('.onboarding-position-row')?.remove();
            else {
                const row = event.target.closest('.onboarding-position-row');
                row?.querySelectorAll('input').forEach(input => { input.value = ''; });
            }
        }
        if (action === 'save-assets') {
            try {
                await saveOnboardingAssets(overlay);
            } catch (e) {
                showToast(e.message || '资产初始化保存失败', 'error');
            }
        }
        if (action === 'import-watchlist-md') {
            try {
                await importOnboardingWatchlistMarkdown(overlay);
            } catch (e) {
                showToast(e.message || '自选股导入失败', 'error');
            }
        }
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

function renderOnboardingPositionRow() {
    return `
      <div class="onboarding-position-row">
        <label><span>股票代码</span><input type="text" class="onboarding-pos-code" inputmode="numeric" maxlength="6" placeholder="000001"></label>
        <label><span>股票名称</span><input type="text" class="onboarding-pos-name" placeholder="可选"></label>
        <label><span>成本价</span><input type="number" class="onboarding-pos-cost" min="0" step="0.001" placeholder="10.50"></label>
        <label><span>数量</span><input type="number" class="onboarding-pos-shares" min="0" step="0.001" placeholder="1000"></label>
        <button type="button" class="onboarding-remove-row" title="删除此行" data-action="remove-position-row">×</button>
      </div>
    `;
}

async function saveOnboardingAssets(overlay) {
    const cashInput = overlay.querySelector('#onboardingCash');
    const cashNote = overlay.querySelector('#onboardingCashNote');
    const cashRaw = cashInput?.value?.trim();
    let savedCash = false;
    let savedPositions = 0;

    if (cashRaw) {
        const balance = Number(cashRaw);
        if (!Number.isFinite(balance) || balance < 0) {
            showToast('可用资金必须是大于等于 0 的数字', 'error');
            return;
        }
        await API.post('/api/portfolio/cash-balance', {
            account_id: currentAccountId || 'default',
            balance,
            notes: cashNote?.value?.trim() || '初始化向导录入',
        });
        savedCash = true;
    }

    for (const row of overlay.querySelectorAll('.onboarding-position-row')) {
        const code = row.querySelector('.onboarding-pos-code')?.value?.trim();
        const name = row.querySelector('.onboarding-pos-name')?.value?.trim();
        const cost = Number(row.querySelector('.onboarding-pos-cost')?.value || 0);
        const shares = Number(row.querySelector('.onboarding-pos-shares')?.value || 0);
        if (!code && !cost && !shares && !name) continue;
        if (!/^\d{6}$/.test(code || '')) {
            showToast('持仓股票代码需要 6 位数字', 'error');
            return;
        }
        if (!Number.isFinite(cost) || cost <= 0) {
            showToast(`${code} 的成本价必须大于 0`, 'error');
            return;
        }
        if (!Number.isFinite(shares) || shares <= 0) {
            showToast(`${code} 的数量必须大于 0，最多支持三位小数`, 'error');
            return;
        }
        await API.post('/api/trades', {
            code,
            name: name || code,
            direction: 'buy',
            price: cost,
            shares,
            commission: 0,
            stamp_tax: 0,
            transfer_fee: 0,
            account_id: currentAccountId || 'default',
            notes: '初始化向导录入初始持仓',
        });
        savedPositions += 1;
    }

    if (!savedCash && !savedPositions) {
        showToast('请至少填写可用资金或一条持仓', 'warning');
        return;
    }
    sessionStorage.removeItem('onboarding_hidden');
    showToast(`初始化已保存：资金 ${savedCash ? '已更新' : '未填写'}，持仓 ${savedPositions} 条`, 'success');
    const fresh = await API.get('/api/settings/onboarding');
    overlay.remove();
    document.getElementById('onboardingStyles')?.remove();
    if (!fresh.completed && fresh.pending_count) renderOnboardingPanel(fresh);
}

async function importOnboardingWatchlistMarkdown(overlay) {
    const input = overlay.querySelector('#onboardingWatchlistMd');
    const textInput = overlay.querySelector('#onboardingWatchlistMdText');
    const file = input?.files?.[0];
    const pastedContent = textInput?.value?.trim();
    if (!file && !pastedContent) {
        showToast('请先选择 Markdown 文件，或直接粘贴 Markdown 内容', 'warning');
        return;
    }
    const content = file ? await file.text() : pastedContent;
    if (!content.trim()) {
        showToast('Markdown 内容为空', 'warning');
        return;
    }
    const result = await API.post('/api/watchlist/import-md', {
        content,
        group_name: '默认',
    });
    showToast(`自选股导入完成：新增 ${result.imported || 0} 条，重复 ${result.duplicates || 0} 条，无效 ${result.invalid || 0} 行`, 'success');
    if (input) input.value = '';
    if (textInput) textInput.value = '';
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
