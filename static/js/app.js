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
