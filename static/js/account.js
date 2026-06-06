const ACCOUNT_API_BASE = '/api';

function accountToast(type, message) {
    if (typeof showToast === 'function') {
        showToast(message, type);
        return;
    }
    alert(message);
}

async function accountFetchJson(url, options = {}) {
    const resp = await fetch(url, options);
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
        throw new Error(data.detail || data.error || `HTTP ${resp.status}`);
    }
    return data;
}

async function loadAccountManagement() {
    try {
        const sessionData = await accountFetchJson(`${ACCOUNT_API_BASE}/auth/session`);
        const user = sessionData.user || {};
        const notice = document.getElementById('initialCredentialNotice');
        if (notice) notice.style.display = user.must_change_credentials ? 'block' : 'none';
        const username = document.getElementById('accountProfileUsername');
        const displayName = document.getElementById('accountProfileDisplayName');
        if (username) username.value = user.username || '';
        if (displayName) displayName.value = user.display_name || '';
        const loginPanel = document.getElementById('loginAccountPanel');
        if (loginPanel) {
            loginPanel.innerHTML = `<div style="display:flex;gap:8px;align-items:center;font-size:0.85rem;">
              <span style="color:var(--text-muted);">当前登录账户</span>
              <strong>${escapeHtml(user.display_name || user.username || user.id || 'admin')}</strong>
              <span style="color:var(--text-muted);">${user.authenticated ? '已登录' : '兼容模式'}</span>
            </div>`;
        }
        await loadLoginUsers();
        await loadSecuritiesAccounts();
    } catch (e) {
        accountToast('error', '账户信息加载失败: ' + e.message);
    }
}

async function saveAccountProfile(event) {
    event.preventDefault();
    const payload = {
        username: document.getElementById('accountProfileUsername').value.trim(),
        display_name: document.getElementById('accountProfileDisplayName').value.trim(),
        password: document.getElementById('accountProfilePassword').value,
    };
    if (!payload.username) {
        accountToast('error', '请输入用户名');
        return;
    }
    if (!payload.password) delete payload.password;
    try {
        const data = await accountFetchJson(`${ACCOUNT_API_BASE}/auth/profile`, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload),
        });
        accountToast('success', '账户资料已保存');
        document.getElementById('accountProfilePassword').value = '';
        if (typeof loadLoginSession === 'function') await loadLoginSession();
        await loadAccountManagement();
        if (!data.user?.must_change_credentials) history.replaceState(null, '', '/account');
    } catch (e) {
        accountToast('error', '保存失败: ' + e.message);
    }
}

async function loadLoginUsers() {
    const usersData = await accountFetchJson(`${ACCOUNT_API_BASE}/auth/users`);
    const userList = document.getElementById('loginUserList');
    if (!userList) return;
    userList.innerHTML = (usersData.users || []).map(u =>
        `<div style="display:flex;align-items:center;gap:8px;padding:4px 0;font-size:0.85rem;">
          <span style="color:var(--text-muted);font-size:0.75rem;">登录账户</span>
          <span style="font-weight:600;">${escapeHtml(u.display_name || u.username || u.id)}</span>
          <span style="color:var(--text-muted);font-size:0.75rem;">${escapeHtml(u.username || '')}</span>
          <span style="color:var(--text-muted);font-size:0.7rem;">${escapeHtml(u.status || '')}</span>
          ${u.id === 'admin' ? '' : `<button class="btn-secondary danger" style="font-size:0.75rem;" onclick="deleteLoginUser('${escapeAttr(u.id)}')">停用</button>`}
        </div>`
    ).join('');
}

async function saveLoginUser(event) {
    event.preventDefault();
    const payload = {
        username: document.getElementById('loginUserUsername').value.trim(),
        display_name: document.getElementById('loginUserDisplayName').value.trim(),
        password: document.getElementById('loginUserPassword').value,
    };
    if (!payload.username) {
        accountToast('error', '请输入登录用户名');
        return;
    }
    try {
        await accountFetchJson(`${ACCOUNT_API_BASE}/auth/users`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload),
        });
        accountToast('success', '登录账户已创建');
        document.getElementById('loginUserManagementPanel').reset();
        await loadLoginUsers();
    } catch (e) {
        accountToast('error', '新增失败: ' + e.message);
    }
}

async function deleteLoginUser(id) {
    try {
        const data = await accountFetchJson(`${ACCOUNT_API_BASE}/auth/users/${encodeURIComponent(id)}`, {method: 'DELETE'});
        if (!data.success) throw new Error(data.detail || data.error || '停用失败');
        accountToast('success', '登录账户已停用');
        await loadLoginUsers();
    } catch (e) {
        accountToast('error', '停用失败: ' + e.message);
    }
}

async function loadSecuritiesAccounts() {
    const data = await accountFetchJson(`${ACCOUNT_API_BASE}/accounts`);
    const el = document.getElementById('accountList');
    if (!el) return;
    const accounts = data.accounts || [];
    el.innerHTML = accounts.length ? accounts.map(a =>
        `<div style="display:flex;align-items:center;gap:8px;padding:4px 0;font-size:0.85rem;">
          <span style="color:var(--text-muted);font-size:0.75rem;">证券账户</span>
          <span style="font-weight:600;">${escapeHtml(a.name)}</span>
          <span style="color:var(--text-muted);font-size:0.75rem;">${escapeHtml(a.broker || '')}</span>
          <span style="color:var(--text-muted);font-size:0.7rem;">(${escapeHtml(a.id)})</span>
          <button class="btn-secondary" style="font-size:0.75rem;" onclick="editSecuritiesAccount('${escapeAttr(a.id)}','${escapeAttr(a.name)}','${escapeAttr(a.broker || '')}','${escapeAttr(a.notes || '')}')">编辑</button>
          <button class="btn-secondary danger" style="font-size:0.75rem;" onclick="deleteSecuritiesAccount('${escapeAttr(a.id)}')">删除</button>
        </div>`
    ).join('') : '<div class="empty-state"><p>暂无证券账户</p></div>';
}

function editSecuritiesAccount(id, name, broker, notes) {
    document.getElementById('securitiesAccountId').value = id || '';
    document.getElementById('securitiesAccountName').value = name || '';
    document.getElementById('securitiesAccountBroker').value = broker || '';
    document.getElementById('securitiesAccountNotes').value = notes || '';
}

async function saveSecuritiesAccount(event) {
    event.preventDefault();
    const id = document.getElementById('securitiesAccountId').value;
    const payload = {
        name: document.getElementById('securitiesAccountName').value.trim(),
        broker: document.getElementById('securitiesAccountBroker').value.trim(),
        notes: document.getElementById('securitiesAccountNotes').value.trim(),
    };
    if (!payload.name) {
        accountToast('error', '请输入证券账户名称');
        return;
    }
    const method = id ? 'PUT' : 'POST';
    const url = id ? `${ACCOUNT_API_BASE}/accounts/${encodeURIComponent(id)}` : `${ACCOUNT_API_BASE}/accounts`;
    try {
        const data = await accountFetchJson(url, {
            method,
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload),
        });
        if (!data.success) throw new Error(data.detail || data.error || '保存失败');
        accountToast('success', '证券账户已保存');
        document.getElementById('securitiesAccountForm').reset();
        document.getElementById('securitiesAccountId').value = '';
        await loadSecuritiesAccounts();
        if (typeof loadAccounts === 'function') await loadAccounts();
    } catch (e) {
        accountToast('error', '保存失败: ' + e.message);
    }
}

async function deleteSecuritiesAccount(id) {
    try {
        const data = await accountFetchJson(`${ACCOUNT_API_BASE}/accounts/${encodeURIComponent(id)}`, {method: 'DELETE'});
        if (!data.success) throw new Error(data.detail || data.error || '删除失败');
        accountToast('success', '证券账户已删除');
        await loadSecuritiesAccounts();
        if (typeof loadAccounts === 'function') await loadAccounts();
    } catch (e) {
        accountToast('error', '删除失败: ' + e.message);
    }
}

window.saveAccountProfile = saveAccountProfile;
window.saveLoginUser = saveLoginUser;
window.deleteLoginUser = deleteLoginUser;
window.editSecuritiesAccount = editSecuritiesAccount;
window.saveSecuritiesAccount = saveSecuritiesAccount;
window.deleteSecuritiesAccount = deleteSecuritiesAccount;

document.addEventListener('DOMContentLoaded', loadAccountManagement);
