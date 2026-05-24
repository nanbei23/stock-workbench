/* 全局工具函数 */
const API = {
  async get(url) { const r = await fetch(url); return r.json(); },
  async post(url, data) { const r = await fetch(url, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}); return r.json(); },
  async put(url, data) { const r = await fetch(url, {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}); return r.json(); },
  async del(url) { const r = await fetch(url, {method:'DELETE'}); return r.json(); },
};

function formatMoney(n) { return n == null ? '--' : n.toLocaleString('zh-CN', {style:'currency',currency:'CNY'}); }
function formatPct(n) { return n == null ? '--' : (n >= 0 ? '+' : '') + n.toFixed(2) + '%'; }
function formatPrice(n) { return n == null ? '--' : n.toFixed(2); }
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
        const resp = await fetch('/api/portfolio/accounts');
        const data = await resp.json();
        const sel = document.getElementById('accountSwitcher');
        if (!sel) return;
        sel.innerHTML = data.accounts.map(a =>
            `<option value="${a.id}" ${a.id===currentAccountId?'selected':''}>${a.name}</option>`
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
    const t = document.createElement('div');
    t.style.cssText = `position:fixed;top:20px;right:20px;z-index:9999;padding:12px 20px;border-radius:8px;font-size:0.9rem;color:#fff;background:${type==='error'?'#E07A5F':type==='success'?'#52B788':'#3D405B'};box-shadow:0 4px 12px rgba(0,0,0,0.3);animation:fadeIn 0.3s;`;
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => { t.style.opacity = '0'; t.style.transition = 'opacity 0.3s'; setTimeout(() => t.remove(), 300); }, 3000);
}
