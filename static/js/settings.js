/**
 * 设置页JS — Tab切换 + 设置CRUD + 导入导出 + 通知管理
 */
const API_BASE = '/api';

let currentSettings = {};
let modelProviderCache = [];
let workerPoolRows = [];
let currentAiSettingsPanel = 'current';

const INVESTMENT_STYLE_PRESETS = {
    conservative: {
        investment_max_single_position_pct: '10',
        investment_max_sector_position_pct: '35',
        investment_max_total_position_pct: '65',
        investment_max_single_trade_loss_pct: '1.5',
        investment_initial_entry_fraction: '0.25',
        investment_min_cash_pct: '20',
        investment_max_drawdown_pct: '6',
        investment_entry_preference: '右侧确认优先，要求趋势站稳、成交量温和放大、基本面和资金面同时确认。',
        investment_entry_strategy_name: '稳健右侧确认',
        investment_entry_required_conditions: '趋势站稳关键均线；基本面和资金面没有明显冲突。',
        investment_entry_supporting_conditions: '成交量温和放大；行业环境稳定；报告置信度高于65%。',
        investment_buy_veto_rules: '放量破位；高位急涨后追入；报告风险评分高于70；关键财务数据缺失。',
        investment_position_sizing_discipline: '首仓不超过计划仓位的四分之一，单只股票不超过单票上限。',
        investment_add_position_discipline: '只在趋势继续确认、回撤可控且不突破仓位上限时加仓。',
        investment_exit_discipline: '跌破关键支撑或基本面恶化时减仓，资金持续流出时退出；达到目标位分批止盈。',
        investment_allow_left_side: false,
        investment_allow_high_volatility: 'forbid',
        investment_custom_notes: '优先本金保护，不因短线波动追高。'
    },
    balanced: {
        investment_max_single_position_pct: '15',
        investment_max_sector_position_pct: '45',
        investment_max_total_position_pct: '75',
        investment_max_single_trade_loss_pct: '2',
        investment_initial_entry_fraction: '0.333',
        investment_min_cash_pct: '5',
        investment_max_drawdown_pct: '12',
        investment_entry_preference: '右侧确认、趋势突破、资金流入、题材催化，机会成立时分批建仓。',
        investment_entry_strategy_name: '右侧确认分批建仓',
        investment_entry_required_conditions: '趋势突破或回踩不破；资金面没有持续流出。',
        investment_entry_supporting_conditions: '站上MA10或MA20；板块同步走强；报告置信度高于60%。',
        investment_buy_veto_rules: '涨停当日追入；换手异常放大；基本面核心假设被证伪；AI报告卖出置信度高于70%。',
        investment_position_sizing_discipline: '分批建仓，首仓约三分之一，不突破单票、同板块和总仓位上限。',
        investment_add_position_discipline: '第二、三批只在回踩确认、资金继续流入且未触发否决项时执行。',
        investment_exit_discipline: '逻辑失效、跌破关键位、硬止损、分批止盈。',
        investment_allow_left_side: false,
        investment_allow_high_volatility: 'cautious',
        investment_custom_notes: '平衡胜率和赔率，机会成立时分批建仓。'
    },
    aggressive: {
        investment_max_single_position_pct: '30',
        investment_max_sector_position_pct: '50',
        investment_max_total_position_pct: '85',
        investment_max_single_trade_loss_pct: '3',
        investment_initial_entry_fraction: '0.333',
        investment_min_cash_pct: '5',
        investment_max_drawdown_pct: '12',
        investment_entry_preference: '突破后不追高，等待回踩5-8%不破、缩量确认、资金继续流入后分批试仓。',
        investment_entry_strategy_name: '突破后回踩买入',
        investment_entry_required_conditions: '突破关键阻力后回踩5-8%不破；回踩时缩量且量比低于0.8。',
        investment_entry_supporting_conditions: '股价站上MA10或MA20；主力超大单连续2日净流入；PE低于40且最近一期营收增速大于10%；板块内3只以上同步走强。除必选条件外，至少满足1条重要或加分条件。',
        investment_buy_veto_rules: '涨停当日追入；换手率高于8%；最近一期利润同比下滑超过20%；PE高于50且营收增速低于10%；AI报告卖出置信度高于70%；控股股东、大基金和外资同时减持。',
        investment_position_sizing_discipline: '分三批建仓，首仓约三分之一；单只股票不超过30%；同板块不超过50%；总仓位不超过85%。',
        investment_add_position_discipline: '第二、三批只在回踩确认、资金继续流入、未触发否决项且没有突破仓位上限时执行；禁止因为亏损扩大而无条件补仓。',
        investment_exit_discipline: '跌破买入触发位或关键支撑先减仓，放量破位清仓；题材逻辑失效、资金连续流出或报告核心假设被证伪时退出；上涨后按压力位和目标位分批止盈。',
        investment_allow_left_side: false,
        investment_allow_high_volatility: 'allow',
        investment_custom_notes: '涨停不追、腰斩不抄、亏损后不赌、盈利后不贪。等回踩买，分批建仓，单只不超30%，止损执行不犹豫。'
    },
    speculative: {
        investment_max_single_position_pct: '50',
        investment_max_sector_position_pct: '60',
        investment_max_total_position_pct: '95',
        investment_max_single_trade_loss_pct: '4',
        investment_initial_entry_fraction: '0.2',
        investment_min_cash_pct: '0',
        investment_max_drawdown_pct: '20',
        investment_entry_preference: '强题材启动、放量突破、资金快速流入、板块情绪升温；允许小仓位左侧试错。',
        investment_entry_strategy_name: '高波动小仓试错',
        investment_entry_required_conditions: '题材强度明确；流动性充足；止损位置明确。',
        investment_entry_supporting_conditions: '板块涨停梯队完整；资金连续流入；情绪周期仍在上升段。',
        investment_buy_veto_rules: '无法定义止损；流动性不足；监管或退市风险；报告核心事实缺失。',
        investment_position_sizing_discipline: '小仓试错，失败快速退出，单只和总仓位不得突破设置上限。',
        investment_add_position_discipline: '只在试错盈利、情绪延续且风险未放大时追加。',
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
    btn.closest('.settings-tabs')?.querySelectorAll('.settings-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.settings-section').forEach(s => s.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('section-' + section).classList.add('active');
    updateSettingsActionsVisibility();
}

function aiSettingsSubtab(btn, panel) {
    document.querySelectorAll('.ai-settings-subtabs .ai-settings-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.ai-settings-panel').forEach(s => s.classList.remove('active'));
    btn.classList.add('active');
    currentAiSettingsPanel = panel;
    document.getElementById('ai-panel-' + panel)?.classList.add('active');
    updateSettingsActionsVisibility();
}

function updateSettingsActionsVisibility() {
    const actions = document.getElementById('settingsActions');
    if (!actions) return;
    const aiActive = document.getElementById('section-ai')?.classList.contains('active');
    const instantSavePanels = ['library', 'workers'];
    const shouldHide = !!(aiActive && instantSavePanels.includes(currentAiSettingsPanel));
    actions.style.display = shouldHide ? 'none' : 'flex';
}

// ── 加载设置 ──
async function loadSettings() {
    try {
        const resp = await fetch(`${API_BASE}/settings`);
        currentSettings = await resp.json();
        applySettings(currentSettings);
        hydrateProviderReferenceControls();
        syncAiNameFromBaseUrl();
        syncVerificationNameFromBaseUrl();
        loadTradeMemoryEmbeddingStatus();
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
    hydrateVerificationProviderReference(settings);
}

function setAiModelOptions(models, quickSelected, deepSelected) {
    setSelectModelOptions(document.getElementById('set-ai_quick_model'), models, quickSelected);
    setSelectModelOptions(document.getElementById('set-ai_deep_model'), models, deepSelected);
}

function hydrateAiModelOptions(settings) {
    hydrateAiProviderReference(settings);
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
    syncLegacyAiProviderFields();
    syncLegacyVerificationProviderFields();
    syncLegacyEmbeddingProviderFields();
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
    const hidden = document.getElementById('set-llm_model_options');
    if (!hidden) return;
    const quickOptions = Array.from(document.getElementById('set-ai_quick_model')?.options || []).map(opt => opt.value).filter(Boolean);
    const deepOptions = Array.from(document.getElementById('set-ai_deep_model')?.options || []).map(opt => opt.value).filter(Boolean);
    hidden.value = JSON.stringify([...new Set([...quickOptions, ...deepOptions, ...extraModels].filter(Boolean))]);
}

function syncVerificationModelOptions(extraModels = []) {
    const hidden = document.getElementById('set-verification_model_options');
    if (!hidden) return;
    const existing = Array.from(document.getElementById('set-verification_model')?.options || []).map(opt => opt.value).filter(Boolean);
    hidden.value = JSON.stringify([...new Set([...existing, ...extraModels].filter(Boolean))]);
}

function providerModels(provider, {embedding = false} = {}) {
    if (!provider) return [];
    const preferred = embedding
        ? [provider.embedding_model, provider.default_model, provider.quick_model, provider.deep_model]
        : [provider.quick_model, provider.deep_model, provider.default_model];
    return [...new Set([...preferred, ...(provider.models || [])].filter(Boolean))];
}

function providerQuickModel(provider) {
    const models = providerModels(provider);
    return provider?.quick_model || provider?.default_model || provider?.deep_model || pickModel(models, 'quick') || '';
}

function providerDeepModel(provider) {
    const models = providerModels(provider);
    return provider?.deep_model || provider?.default_model || provider?.quick_model || pickModel(models, 'deep') || '';
}

function providerDefaultModel(provider) {
    const models = providerModels(provider);
    return provider?.default_model || provider?.deep_model || provider?.quick_model || pickModel(models, 'deep') || '';
}

function providerEmbeddingModel(provider) {
    const models = providerModels(provider, {embedding: true});
    return provider?.embedding_model || provider?.default_model || provider?.quick_model || pickModel(models, 'quick') || '';
}

function renderModelSummary(id, rows, emptyText) {
    const el = document.getElementById(id);
    if (!el) return;
    const clean = (rows || []).filter(row => row && row.value);
    if (!clean.length) {
        el.textContent = emptyText;
        return;
    }
    el.innerHTML = clean.map(row => `<span>${escapeHtml(row.label)}：<b>${escapeHtml(row.value)}</b></span>`).join('');
}

function renderAiRuntimeSummary(provider) {
    renderModelSummary('aiRuntimeModelSummary', [
        {label: '快速', value: providerQuickModel(provider)},
        {label: '深度', value: providerDeepModel(provider)},
        {label: '默认', value: providerDefaultModel(provider)},
    ], '请选择主分析模型源');
}

function renderVerificationSummary(provider) {
    renderModelSummary('verificationModelSummary', [
        {label: '核对', value: providerDefaultModel(provider)},
    ], '请选择核对模型源');
}

function renderEmbeddingSummary(provider) {
    renderModelSummary('embeddingModelSummary', [
        {label: '模型', value: providerEmbeddingModel(provider)},
        {label: '维度', value: String(provider?.embedding_dimensions || 1536)},
    ], '请选择向量模型源');
}

function providerOptions(selected = '', {usage = ''} = {}) {
    const providers = (modelProviderCache || []).filter(provider => {
        if (!usage) return true;
        const tags = provider.usage || [];
        return !tags.length || tags.includes(usage);
    });
    const options = providers.map(provider =>
        `<option value="${escapeAttr(provider.id)}" ${String(provider.id) === String(selected) ? 'selected' : ''}>${escapeHtml(provider.name || provider.id)}</option>`
    );
    return `<option value="">请选择模型库配置</option>${options.join('')}`;
}

function inferProviderIdFromLegacy(settings, endpointKey) {
    const current = settings?.[endpointKey] || '';
    if (!current) return '';
    const matched = (modelProviderCache || []).find(provider => String(provider.base_url || '') === String(current));
    return matched?.id || '';
}

function hydrateProviderReferenceControls() {
    if (!currentSettings) return;
    hydrateAiProviderReference(currentSettings);
    hydrateVerificationProviderReference(currentSettings);
    hydrateEmbeddingProviderReference(currentSettings);
}

function hydrateAiProviderReference(settings = currentSettings) {
    const selected = settings.ai_primary_provider_id || inferProviderIdFromLegacy(settings, 'custom_endpoint');
    const providerSelect = document.getElementById('set-ai_primary_provider_id');
    if (providerSelect) providerSelect.innerHTML = providerOptions(selected, {usage: 'ai'});
    const provider = modelProviderById(selected);
    renderAiRuntimeSummary(provider);
    syncLegacyAiProviderFields();
}

function hydrateVerificationProviderReference(settings = currentSettings) {
    const selected = settings.verification_provider_id || inferProviderIdFromLegacy(settings, 'verification_endpoint');
    const providerSelect = document.getElementById('set-verification_provider_id');
    if (providerSelect) providerSelect.innerHTML = providerOptions(selected, {usage: 'verification'});
    const provider = modelProviderById(selected);
    renderVerificationSummary(provider);
    syncLegacyVerificationProviderFields();
}

function hydrateEmbeddingProviderReference(settings = currentSettings) {
    const selected = settings.embedding_provider_id || inferProviderIdFromLegacy(settings, 'embedding_endpoint');
    const providerSelect = document.getElementById('set-embedding_provider_id');
    if (providerSelect) providerSelect.innerHTML = providerOptions(selected, {usage: 'embedding'});
    const provider = modelProviderById(selected);
    renderEmbeddingSummary(provider);
    syncLegacyEmbeddingProviderFields();
}

function onAiProviderChange() {
    const selected = document.getElementById('set-ai_primary_provider_id')?.value || '';
    const provider = modelProviderById(selected);
    renderAiRuntimeSummary(provider);
    syncLegacyAiProviderFields();
}

function onVerificationProviderChange() {
    const selected = document.getElementById('set-verification_provider_id')?.value || '';
    const provider = modelProviderById(selected);
    renderVerificationSummary(provider);
    syncLegacyVerificationProviderFields();
}

function onEmbeddingProviderChange() {
    const selected = document.getElementById('set-embedding_provider_id')?.value || '';
    const provider = modelProviderById(selected);
    renderEmbeddingSummary(provider);
    syncLegacyEmbeddingProviderFields();
}

function syncLegacyAiProviderFields() {
    const provider = modelProviderById(document.getElementById('set-ai_primary_provider_id')?.value || '');
    const quick = providerQuickModel(provider);
    const deep = providerDeepModel(provider);
    const assign = (id, value) => { const el = document.getElementById(id); if (el) el.value = value || ''; };
    assign('set-llm_name', provider?.name || '');
    assign('set-custom_endpoint', provider?.base_url || '');
    assign('set-api_key', provider?.has_api_key ? '********' : '');
    assign('set-quick_think_model', quick);
    assign('set-deep_think_model', deep);
    assign('set-llm_context_length', provider?.context_length || '');
    syncAiModelOptions(providerModels(provider));
}

function syncLegacyVerificationProviderFields() {
    const provider = modelProviderById(document.getElementById('set-verification_provider_id')?.value || '');
    const assign = (id, value) => { const el = document.getElementById(id); if (el) el.value = value || ''; };
    assign('set-verification_name', provider?.name || '');
    assign('set-verification_endpoint', provider?.base_url || '');
    assign('set-verification_api_key', provider?.has_api_key ? '********' : '');
    assign('set-verification_context_length', provider?.context_length || '');
    syncVerificationModelOptions(providerModels(provider));
}

function syncLegacyEmbeddingProviderFields() {
    const provider = modelProviderById(document.getElementById('set-embedding_provider_id')?.value || '');
    const assign = (id, value) => { const el = document.getElementById(id); if (el) el.value = value || ''; };
    assign('set-embedding_endpoint', provider?.base_url || '');
    assign('set-embedding_api_key', provider?.has_api_key ? '********' : '');
    assign('set-embedding_dimensions', provider?.embedding_dimensions || 1536);
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
            loadTradeMemoryEmbeddingStatus();
        } else {
            toast('error', '保存失败');
        }
    } catch (e) {
        toast('error', '保存失败: ' + e.message);
    }
}

function renderTradeMemoryEmbeddingStatus(data) {
    const el = document.getElementById('embeddingIndexStatus');
    if (!el) return;
    const active = Number(data.active_memories || 0);
    const indexed = Number(data.indexed_memories || 0);
    const missing = Number(data.missing_embeddings || 0);
    const coverage = Number(data.coverage_pct || 0).toFixed(active ? 1 : 0);
    const provider = data.provider_configured ? 'Embedding Key 已配置' : 'Embedding Key 未配置';
    const vec = data.sqlite_vec_available ? 'sqlite-vec 可用' : 'sqlite-vec 不可用';
    const last = data.last_indexed_at ? ` · 最近索引 ${escapeHtml(data.last_indexed_at)}` : '';
    el.className = `test-result ${missing ? 'error' : 'ok'}`;
    el.innerHTML = `覆盖率 ${coverage}% · ${indexed}/${active} 已索引 · ${missing} 缺失 · ${provider} · ${vec}${last}`;
}

function embeddingConnectionPayload() {
    return {
        provider_id: (document.getElementById('set-embedding_provider_id')?.value || '').trim(),
        api_key: (document.getElementById('set-embedding_api_key')?.value || '').trim(),
        endpoint: (document.getElementById('set-embedding_endpoint')?.value || 'https://api.openai.com/v1/embeddings').trim(),
        model: '',
        dimensions: Number(document.getElementById('set-embedding_dimensions')?.value || 1536),
    };
}

function renderEmbeddingConnectionResult(data) {
    const el = document.getElementById('embeddingConnectionResult');
    if (!el) return;
    const status = data.status || 'error';
    el.className = `test-result ${status === 'ok' ? 'ok' : 'error'}`;
    if (status === 'ok') {
        el.textContent = data.message || `连接成功 (${data.model || 'embedding'})`;
        return;
    }
    const parts = [];
    if (data.http_status) parts.push(`HTTP ${data.http_status}`);
    if (data.error_type) parts.push(`类型: ${data.error_type}`);
    if (data.error_code) parts.push(`代码: ${data.error_code}`);
    if (data.request_id) parts.push(`Request ID: ${data.request_id}`);
    const detail = parts.length ? `（${parts.map(escapeHtml).join(' · ')}）` : '';
    el.innerHTML = `${escapeHtml(data.message || '连接失败')}${detail}`;
}

async function testTradeMemoryEmbeddingConnection() {
    const el = document.getElementById('embeddingConnectionResult');
    if (el) {
        el.className = 'test-result';
        el.textContent = '测试中...';
    }
    const payload = embeddingConnectionPayload();
    if (!payload.provider_id && (!payload.api_key || payload.api_key === '********')) {
        renderEmbeddingConnectionResult({
            status: 'error',
            error_type: 'missing_api_key',
            message: '请先选择模型库中的 Embedding 配置再测试。',
        });
        return;
    }
    try {
        const resp = await fetch(`${API_BASE}/trade-memories/embeddings/test-connection`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || '测试失败');
        renderEmbeddingConnectionResult(data);
        if (data.status === 'ok') {
            toast('success', 'Embedding 连接成功');
        } else {
            toast('error', data.message || 'Embedding 连接失败');
        }
    } catch (e) {
        renderEmbeddingConnectionResult({
            status: 'error',
            error_type: 'request_error',
            message: e.message,
        });
    }
}

async function loadTradeMemoryEmbeddingStatus() {
    const el = document.getElementById('embeddingIndexStatus');
    if (el) {
        el.className = 'test-result';
        el.textContent = '读取中...';
    }
    try {
        const resp = await fetch(`${API_BASE}/trade-memories/embeddings/status`);
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || '读取失败');
        renderTradeMemoryEmbeddingStatus(data);
    } catch (e) {
        if (el) {
            el.className = 'test-result error';
            el.textContent = '读取失败: ' + e.message;
        }
    }
}

async function backfillTradeMemoryEmbeddings() {
    const el = document.getElementById('embeddingIndexStatus');
    if (el) {
        el.className = 'test-result';
        el.textContent = '索引中...';
    }
    try {
        const resp = await fetch(`${API_BASE}/trade-memories/embeddings/backfill`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({account_id: 'default', limit: 200}),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || '索引失败');
        if (data.enabled === false) {
            toast('error', (data.errors || []).join('；') || 'Embedding 未配置');
        } else if ((data.errors || []).length) {
            toast('error', `索引完成但有失败: ${(data.errors || []).length}条`);
        } else {
            toast('success', `索引完成: 新增/更新${data.indexed || 0}条，跳过${data.skipped || 0}条`);
        }
        await loadTradeMemoryEmbeddingStatus();
    } catch (e) {
        toast('error', '索引失败: ' + e.message);
        await loadTradeMemoryEmbeddingStatus();
    }
}

// ── 测试API连接 ──
async function testApiConnection() {
    const resultSpan = document.getElementById('testResult');
    resultSpan.textContent = '测试中...';
    resultSpan.className = 'test-result';
    syncLegacyAiProviderFields();
    try {
        const resp = await fetch(`${API_BASE}/settings/test-llm`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                provider_id: document.getElementById('set-ai_primary_provider_id')?.value || '',
                model_tier: 'quick',
            }),
        });
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
    const providerId = document.getElementById('set-verification_provider_id')?.value?.trim() || '';
    if (!providerId) {
        resultSpan.className = 'test-result error';
        resultSpan.textContent = '失败 请先选择核对模型配置';
        return;
    }
    resultSpan.textContent = '测试中...';
    resultSpan.className = 'test-result';
    syncLegacyVerificationProviderFields();
    try {
        const resp = await fetch(`${API_BASE}/settings/test-verification`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ provider_id: providerId }),
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

function pickModel(models, role) {
    const list = models || [];
    if (!list.length) return '';
    const quickHints = ['flash', 'mini', 'turbo', 'lite', 'quick', 'chat'];
    const deepHints = ['reasoner', 'pro', 'max', 'o1', 'deep', 'thinking'];
    const hints = role === 'deep' ? deepHints : quickHints;
    const found = list.find(model => hints.some(hint => String(model).toLowerCase().includes(hint)));
    return found || list[0];
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
                body: '通知已启用，异动提醒会自动发送。',
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
            : `stock-workbench-db-backup-${new Date().toISOString().slice(0, 10)}.db`;
        a.click();
        URL.revokeObjectURL(url);
        toast('success', '数据库文件已导出');
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
            toast('success', `导入完成: 自选${imp.watchlist}条/持仓${imp.portfolio}条/设置${imp.settings}条`);
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
            <div><span>备份类型</span><strong>${db.backup_type === 'sqlite' ? 'SQLite完整镜像' : 'JSON兼容备份'}</strong></div>
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
        hydrateProviderReferenceControls();
        if (!providers.length) {
            el.innerHTML = '<div class="empty-state"><p>暂无模型配置</p></div>';
            renderWorkerPoolRows();
            return;
        }
        el.innerHTML = `<div class="model-provider-grid">${providers.map(renderModelProviderCard).join('')}</div>`;
        renderWorkerPoolRows();
    } catch (e) {
        el.innerHTML = `<div class="empty-state"><p>模型配置读取失败：${escapeHtml(e.message)}</p></div>`;
    }
}

function providerUsageLabel(usage = []) {
    const labels = {ai: 'AI', verification: '核对', embedding: '记忆'};
    return (usage || []).map(item => labels[item] || item).filter(Boolean).join(' / ') || '未标记';
}

function providerModelSummary(provider, key, fallback = '-') {
    return provider?.[key] || fallback;
}

function renderModelProviderCard(p) {
    const id = escapeAttr(p.id);
    const modelCount = (p.models || []).length;
    const keyBadgeClass = p.has_api_key ? 'model-provider-badge' : 'model-provider-badge missing';
    return `<div class="model-provider-card">
        <div class="model-provider-card-header">
            <div class="model-provider-card-title">
                <b title="${escapeAttr(p.name || p.id)}">${escapeHtml(p.name || p.id)}</b>
                <small title="${escapeAttr(p.base_url || '')}">${escapeHtml(p.base_url || '')}</small>
            </div>
            <span class="${keyBadgeClass}">${p.has_api_key ? 'Key 已保存' : '无 Key'}</span>
        </div>
        <div class="model-provider-meta">
            <div><span>用途</span><span title="${escapeAttr(providerUsageLabel(p.usage || []))}">${escapeHtml(providerUsageLabel(p.usage || []))}</span></div>
            <div><span>模型数</span><span>${modelCount}</span></div>
            <div><span>快速</span><span title="${escapeAttr(providerModelSummary(p, 'quick_model'))}">${escapeHtml(providerModelSummary(p, 'quick_model'))}</span></div>
            <div><span>深度</span><span title="${escapeAttr(providerModelSummary(p, 'deep_model'))}">${escapeHtml(providerModelSummary(p, 'deep_model'))}</span></div>
            <div><span>默认</span><span title="${escapeAttr(providerModelSummary(p, 'default_model'))}">${escapeHtml(providerModelSummary(p, 'default_model'))}</span></div>
            <div><span>Embedding</span><span title="${escapeAttr(providerModelSummary(p, 'embedding_model'))}">${escapeHtml(providerModelSummary(p, 'embedding_model'))}</span></div>
        </div>
        <div class="model-provider-actions">
            <button class="btn-secondary" onclick="openModelProviderEditor('${id}')">编辑</button>
            <button class="btn-secondary" onclick="refreshModelProvider('${id}')">获取模型</button>
            <button class="btn-secondary" onclick="testModelProvider('${id}')">测试</button>
            <button class="btn-secondary" onclick="deleteModelProvider('${id}')">删除</button>
        </div>
    </div>`;
}

function modelProviderById(id) {
    return (modelProviderCache || []).find(provider => String(provider.id) === String(id));
}

function providerModelsText(provider) {
    return (provider?.models || []).join('\n');
}

function parseProviderModelsText(value) {
    return String(value || '')
        .split(/[\n,]/)
        .map(item => item.trim())
        .filter(Boolean);
}

function setProviderEditUsage(usage) {
    const usageSet = new Set(usage || []);
    document.getElementById('providerEditUsageAi').checked = usageSet.has('ai');
    document.getElementById('providerEditUsageVerification').checked = usageSet.has('verification');
    document.getElementById('providerEditUsageEmbedding').checked = usageSet.has('embedding');
    onProviderEditUsageChange();
}

function inferProviderEditUsage(provider) {
    const explicit = (provider?.usage || []).filter(Boolean);
    if (explicit.length) return explicit;
    if (!provider) return ['ai'];
    const usage = [];
    if ((provider.models || []).length || provider.quick_model || provider.deep_model || provider.default_model) {
        usage.push('ai');
    }
    if (provider.embedding_model) {
        usage.push('embedding');
    }
    return usage.length ? usage : ['ai'];
}

function providerEditChatEnabled() {
    return !!(document.getElementById('providerEditUsageAi')?.checked || document.getElementById('providerEditUsageVerification')?.checked);
}

function setProviderEditModelSelectOptions(models, selected = {}) {
    const list = models || [];
    setSelectModelOptions(document.getElementById('providerEditQuickModel'), list, selected.quick || pickModel(list, 'quick'), '请先获取模型');
    setSelectModelOptions(document.getElementById('providerEditDeepModel'), list, selected.deep || pickModel(list, 'deep'), '请先获取模型');
    setSelectModelOptions(document.getElementById('providerEditDefaultModel'), list, selected.default || selected.deep || selected.quick || pickModel(list, 'deep'), '请先获取模型');
}

function onProviderEditUsageChange() {
    const chatEnabled = providerEditChatEnabled();
    const embeddingEnabled = !!document.getElementById('providerEditUsageEmbedding')?.checked;
    const chatFields = document.getElementById('providerEditChatModelFields');
    const embeddingFields = document.getElementById('providerEditEmbeddingFields');
    const fetchRow = document.getElementById('providerEditFetchRow');
    if (chatFields) chatFields.style.display = chatEnabled ? 'grid' : 'none';
    if (fetchRow) fetchRow.style.display = chatEnabled ? 'flex' : 'none';
    if (embeddingFields) embeddingFields.style.display = embeddingEnabled ? 'grid' : 'none';
    if (embeddingEnabled && !document.getElementById('providerEditEmbeddingModel')?.value) {
        document.getElementById('providerEditEmbeddingModel').value = 'text-embedding-v4';
    }
}

function openModelProviderEditor(id = '') {
    const provider = id ? modelProviderById(id) : null;
    document.getElementById('providerEditTitle').textContent = provider ? '编辑模型源' : '新增模型源';
    document.getElementById('providerEditId').value = provider?.id || '';
    document.getElementById('providerEditName').value = provider?.name || '';
    document.getElementById('providerEditBaseUrl').value = provider?.base_url || '';
    document.getElementById('providerEditApiKey').value = provider?.has_api_key ? '********' : '';
    document.getElementById('providerEditModels').value = providerModelsText(provider);
    setProviderEditModelSelectOptions(provider?.models || [], {
        quick: provider?.quick_model || '',
        deep: provider?.deep_model || '',
        default: provider?.default_model || '',
    });
    document.getElementById('providerEditContextLength').value = provider?.context_length || '';
    document.getElementById('providerEditEmbeddingModel').value = provider?.embedding_model || '';
    document.getElementById('providerEditEmbeddingDimensions').value = provider?.embedding_dimensions || 1536;
    setProviderEditUsage(inferProviderEditUsage(provider));
    const result = document.getElementById('providerEditFetchModelsResult');
    if (result) {
        result.className = 'test-result';
        result.textContent = provider?.models?.length ? `已有 ${provider.models.length} 个模型` : '未获取';
    }
    document.getElementById('providerEditModal').classList.add('show');
}

function closeProviderEditModal() {
    document.getElementById('providerEditModal')?.classList.remove('show');
}

function collectProviderEditPayload() {
    const usage = ['Ai', 'Verification', 'Embedding']
        .map(name => {
            const input = document.getElementById(`providerEditUsage${name}`);
            return input?.checked ? input.value : '';
        })
        .filter(Boolean);
    return {
        name: document.getElementById('providerEditName')?.value || '',
        base_url: document.getElementById('providerEditBaseUrl')?.value || '',
        api_key: document.getElementById('providerEditApiKey')?.value || '',
        models: parseProviderModelsText(document.getElementById('providerEditModels')?.value || ''),
        quick_model: document.getElementById('providerEditQuickModel')?.value || '',
        deep_model: document.getElementById('providerEditDeepModel')?.value || '',
        default_model: document.getElementById('providerEditDefaultModel')?.value || '',
        context_length: document.getElementById('providerEditContextLength')?.value || '',
        embedding_model: document.getElementById('providerEditEmbeddingModel')?.value || '',
        embedding_dimensions: Number(document.getElementById('providerEditEmbeddingDimensions')?.value || 1536),
        usage,
    };
}

async function fetchProviderEditModels() {
    const endpoint = document.getElementById('providerEditBaseUrl')?.value?.trim() || '';
    const apiKey = document.getElementById('providerEditApiKey')?.value?.trim() || '';
    const resultSpan = document.getElementById('providerEditFetchModelsResult');
    const btn = document.getElementById('providerEditFetchModelsBtn');
    if (!endpoint) {
        if (resultSpan) {
            resultSpan.className = 'test-result error';
            resultSpan.textContent = '失败 请先填写 Base URL';
        }
        return;
    }
    if (!apiKey) {
        if (resultSpan) {
            resultSpan.className = 'test-result error';
            resultSpan.textContent = '失败 请先填写 API Key';
        }
        return;
    }
    if (btn) {
        btn.disabled = true;
        btn.textContent = '获取中...';
    }
    if (resultSpan) {
        resultSpan.className = 'test-result';
        resultSpan.textContent = '正在获取模型...';
    }
    try {
        const resp = await fetch(`${API_BASE}/settings/fetch-models`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({endpoint, api_key: apiKey}),
        });
        const data = await resp.json();
        if (!resp.ok || data.status !== 'ok' || !Array.isArray(data.models) || !data.models.length) {
            throw new Error(data.detail || '未获取到模型');
        }
        document.getElementById('providerEditModels').value = data.models.join('\n');
        setProviderEditModelSelectOptions(data.models, {
            quick: document.getElementById('providerEditQuickModel')?.value || '',
            deep: document.getElementById('providerEditDeepModel')?.value || '',
            default: document.getElementById('providerEditDefaultModel')?.value || '',
        });
        if (resultSpan) {
            resultSpan.className = 'test-result ok';
            resultSpan.textContent = `成功 ${data.models.length} 个模型`;
        }
    } catch (e) {
        if (resultSpan) {
            resultSpan.className = 'test-result error';
            resultSpan.textContent = `失败 ${e.message}`;
        }
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = '获取模型';
        }
    }
}

async function saveModelProviderEdit() {
    const id = document.getElementById('providerEditId')?.value || '';
    const payload = collectProviderEditPayload();
    if (!payload.base_url) {
        toast('error', '请填写 Base URL');
        return;
    }
    if (!payload.usage.length) {
        toast('error', '请选择模型源用途');
        return;
    }
    const chatEnabled = payload.usage.includes('ai') || payload.usage.includes('verification');
    if (chatEnabled && !payload.models.length) {
        toast('error', 'AI/核对模型源请先获取模型列表');
        return;
    }
    if (payload.usage.includes('embedding') && !payload.embedding_model) {
        toast('error', '请填写 Embedding 模型');
        return;
    }
    try {
        const resp = await fetch(id ? `${API_BASE}/model-providers/${id}` : `${API_BASE}/model-providers`, {
            method: id ? 'PUT' : 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || '保存失败');
        closeProviderEditModal();
        toast('success', '模型配置已保存');
        await loadModelProviders();
    } catch (e) {
        toast('error', '保存失败: ' + e.message);
    }
}

function workerProviderById(id) {
    return (modelProviderCache || []).find(provider => String(provider.id) === String(id));
}

function workerProviderLabel(id) {
    const provider = workerProviderById(id);
    return provider ? (provider.name || provider.id) : (id || '-');
}

function workerProviderSelectOptions(selected = '', emptyText = '不使用') {
    const selectedValue = String(selected || '');
    const providers = (modelProviderCache || []).filter(provider => {
        const usage = provider.usage || [];
        return !usage.length || usage.includes('ai') || usage.includes('verification');
    });
    const options = [`<option value="">${escapeHtml(emptyText)}</option>`];
    providers.forEach(provider => {
        const id = String(provider.id || '');
        options.push(`<option value="${escapeAttr(id)}" ${id === selectedValue ? 'selected' : ''}>${escapeHtml(provider.name || provider.id)}</option>`);
    });
    if (selectedValue && !providers.some(provider => String(provider.id) === selectedValue)) {
        options.push(`<option value="${escapeAttr(selectedValue)}" selected>${escapeHtml(selectedValue)}（已保存）</option>`);
    }
    return options.join('');
}

function workerProviders(worker) {
    const providerIds = (worker.provider_ids || []).filter(Boolean);
    return {
        primary: providerIds[0] || '',
        fallback: providerIds[1] || '',
    };
}

function renderWorkerProviderSummary(worker) {
    const providers = workerProviders(worker);
    const primary = providers.primary ? workerProviderLabel(providers.primary) : '未关联';
    const fallback = providers.fallback ? workerProviderLabel(providers.fallback) : '无备用';
    return `<span class="worker-provider-summary">
        <b title="${escapeAttr(primary)}">${escapeHtml(primary)}</b>
        <small title="${escapeAttr(fallback)}">备用：${escapeHtml(fallback)}</small>
    </span>`;
}

function renderWorkerPoolCard(worker, index) {
    const id = escapeAttr(worker.id || `worker-${index + 1}`);
    const enabled = worker.enabled !== false;
    const tier = (worker.model_tier || 'deep') === 'quick' ? '快速模型' : '深度模型';
    return `<div class="model-provider-card worker-pool-card" data-index="${index}">
        <div class="model-provider-card-header">
            <div class="model-provider-card-title">
                <b title="${escapeAttr(worker.name || worker.id || id)}">${escapeHtml(worker.name || worker.id || id)}</b>
                <small title="${escapeAttr(worker.id || id)}">${escapeHtml(worker.id || id)}</small>
            </div>
            <span class="model-provider-badge ${enabled ? '' : 'missing'}">${enabled ? '启用' : '停用'}</span>
        </div>
        <div class="model-provider-meta">
            <div><span>模型档位</span><span>${escapeHtml(tier)}</span></div>
            <div><span>模型源</span>${renderWorkerProviderSummary(worker)}</div>
            <div><span>轮询间隔</span><span>${Number(worker.sleep_seconds || 5)} 秒</span></div>
            <div><span>陈旧阈值</span><span>${Number(worker.stale_minutes || 15)} 分钟</span></div>
        </div>
        <div class="model-provider-actions">
            <button class="btn-secondary" onclick="openWorkerPoolEditor(${index})">编辑</button>
            <button class="btn-secondary" onclick="removeWorkerPoolRow(${index})">删除</button>
        </div>
    </div>`;
}

function renderWorkerPoolRows() {
    const el = document.getElementById('workerPoolList');
    if (!el) return;
    if (!workerPoolRows.length) {
        el.innerHTML = '<div class="empty-state"><p>暂无 Worker 配置</p></div>';
        return;
    }
    el.innerHTML = `<div class="model-provider-grid">${workerPoolRows.map(renderWorkerPoolCard).join('')}</div>`;
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
    openWorkerPoolEditor();
}

function openWorkerPoolEditor(index = '') {
    const isEdit = index !== '' && index !== null && index !== undefined;
    const worker = isEdit ? workerPoolRows[Number(index)] : null;
    const next = workerPoolRows.length + 1;
    const draft = worker || {
        id: `worker-${next}`,
        name: `Worker ${next}`,
        enabled: true,
        provider_ids: [],
        model_tier: 'deep',
        sleep_seconds: 5,
        stale_minutes: 15,
    };
    const providers = workerProviders(draft);
    document.getElementById('workerEditTitle').textContent = worker ? '编辑 Worker' : '新增 Worker';
    document.getElementById('workerEditIndex').value = worker ? String(index) : '';
    document.getElementById('workerEditId').value = draft.id || '';
    document.getElementById('workerEditName').value = draft.name || '';
    document.getElementById('workerEditEnabled').value = draft.enabled === false ? 'false' : 'true';
    document.getElementById('workerEditModelTier').value = draft.model_tier === 'quick' ? 'quick' : 'deep';
    document.getElementById('workerEditPrimaryProviderId').innerHTML = workerProviderSelectOptions(providers.primary, '请选择主模型源');
    document.getElementById('workerEditFallbackProviderId').innerHTML = workerProviderSelectOptions(providers.fallback, '不使用备用模型源');
    document.getElementById('workerEditSleepSeconds').value = draft.sleep_seconds || 5;
    document.getElementById('workerEditStaleMinutes').value = draft.stale_minutes || 15;
    document.getElementById('workerPoolEditorModal')?.classList.add('show');
}

function closeWorkerPoolEditor() {
    document.getElementById('workerPoolEditorModal')?.classList.remove('show');
}

function removeWorkerPoolRow(index) {
    showConfirm('删除 Worker', '删除后该 Worker 不会再由模型池脚本启动。确定删除吗？', async () => {
        workerPoolRows.splice(index, 1);
        await saveWorkerPoolConfig();
    });
}

function collectWorkerPoolRows() {
    return workerPoolRows.map(worker => ({
        id: worker.id || '',
        name: worker.name || '',
        enabled: worker.enabled !== false,
        model_tier: worker.model_tier === 'quick' ? 'quick' : 'deep',
        sleep_seconds: Number(worker.sleep_seconds || 5),
        stale_minutes: Number(worker.stale_minutes || 15),
        provider_ids: (worker.provider_ids || []).filter(Boolean),
    }));
}

function collectWorkerPoolEditPayload() {
    const primary = document.getElementById('workerEditPrimaryProviderId')?.value || '';
    const fallback = document.getElementById('workerEditFallbackProviderId')?.value || '';
    return {
        id: document.getElementById('workerEditId')?.value?.trim() || '',
        name: document.getElementById('workerEditName')?.value?.trim() || '',
        enabled: document.getElementById('workerEditEnabled')?.value !== 'false',
        model_tier: document.getElementById('workerEditModelTier')?.value === 'quick' ? 'quick' : 'deep',
        sleep_seconds: Number(document.getElementById('workerEditSleepSeconds')?.value || 5),
        stale_minutes: Number(document.getElementById('workerEditStaleMinutes')?.value || 15),
        provider_ids: [...new Set([primary, fallback].filter(Boolean))],
    };
}

async function saveWorkerPoolEdit() {
    const indexValue = document.getElementById('workerEditIndex')?.value || '';
    const payload = collectWorkerPoolEditPayload();
    if (!payload.id) {
        toast('error', '请填写 Worker ID');
        return;
    }
    if (!payload.provider_ids.length) {
        toast('error', '请至少选择一个主模型源');
        return;
    }
    if (indexValue === '') {
        workerPoolRows.push(payload);
    } else {
        workerPoolRows[Number(indexValue)] = payload;
    }
    closeWorkerPoolEditor();
    await saveWorkerPoolConfig();
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
        const identity = (data.checks || []).find(item => item.key === 'identity_integrity');
        const identityRows = identity ? `
            <div>
              <span>登录账户完整性</span>
              <strong class="${identity.status === 'ok' ? 'up' : 'down'}">${identity.status === 'ok' ? '正常' : '需处理'}</strong>
              <small>${escapeHtml(identity.message || '')}</small>
              ${renderDataHealthDetails(identity.details)}
            </div>` : '';
        el.innerHTML = `<div class="backup-status-grid">${(data.checks || []).filter(item => item.key !== 'identity_integrity').map(item => `
            <div>
              <span>${escapeHtml(item.label)}</span>
              <strong class="${item.status === 'ok' ? 'up' : 'down'}">${item.status === 'ok' ? '正常' : '需处理'}</strong>
              <small>${escapeHtml(item.message || '')}</small>
              ${renderDataHealthDetails(item.details)}
            </div>`).join('')}${identityRows}</div>`;
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
        if (item.orphan_securities_account_ids !== undefined) parts.push(`异常证券账户 ${item.orphan_securities_account_ids}`);
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
    showConfirm('恢复最近数据库备份', '将使用最近一次 SQLite 备份文件替换当前整个数据库。系统会先自动保留一份恢复前备份，但当前未入备份的数据会被回滚。确定继续吗？', async () => {
        try {
            const resp = await fetch(`${API_BASE}/settings/backup/restore-latest`, { method: 'POST' });
            const data = await resp.json();
            if (!resp.ok) throw new Error(data.detail || '恢复失败');
            toast('success', `数据库恢复完成: ${data.filename || '最近备份'}`);
            loadSettings();
            loadBackupStatus();
        } catch (e) {
            toast('error', '恢复失败: ' + e.message);
        }
    });
}

function restoreUploadedDatabase(input) {
    const file = input.files && input.files[0];
    if (!file) return;
    input.value = '';
    showConfirm(
        '上传数据库文件恢复',
        `将使用「${file.name}」替换当前整个 SQLite 数据库。系统会先自动备份当前数据库，但恢复后建议重启服务。确定继续吗？`,
        async () => {
            try {
                const form = new FormData();
                form.append('file', file);
                const resp = await fetch(`${API_BASE}/settings/backup/restore-upload`, {
                    method: 'POST',
                    body: form,
                });
                const data = await resp.json();
                if (!resp.ok) throw new Error(data.detail || '恢复失败');
                toast('success', `数据库恢复完成: ${data.filename || file.name}${data.restart_required ? '，请重启服务' : ''}`);
                await loadSettings();
                await loadBackupStatus();
            } catch (e) {
                toast('error', '上传恢复失败: ' + e.message);
            }
        }
    );
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
    showConfirm('清空所有数据', '此操作不可撤销！将删除所有自选股、持仓和历史报告。', async () => {
        try {
            const resp = await fetch(`${API_BASE}/settings/clear-all`, { method: 'POST' });
            const data = await resp.json();
            const c = data.cleared;
            toast('success', `已清空: 自选${c.watchlist}/持仓${c.portfolio}/交易${c.trades}/报告${c.analysis_reports}`);
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
loadTradeMemoryEmbeddingStatus();
loadDataAudit();
loadDataHealth();
loadSystemDiagnostics();
loadWorkspaceTemplates();

// ── 账户管理 ──
async function loadAccountList() {
    try {
        const sessionResp = await fetch(`${API_BASE}/auth/session`);
        const sessionData = await sessionResp.json();
        const user = sessionData.user || {};
        const loginPanel = document.getElementById('loginAccountPanel');
        if (loginPanel) {
            loginPanel.innerHTML = `<div style="display:flex;gap:8px;align-items:center;font-size:0.85rem;">
              <span style="color:var(--text-muted);">登录账户</span>
              <strong>${escapeHtml(user.display_name || user.username || user.id || '本机账户')}</strong>
              <span style="color:var(--text-muted);">${user.authenticated ? '已登录' : '本机默认账户'}</span>
            </div>`;
        }
        const usersResp = await fetch(`${API_BASE}/auth/users`);
        const usersData = await usersResp.json();
        const userList = document.getElementById('loginUserList');
        if (userList) {
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
        const resp = await fetch(`${API_BASE}/accounts`);
        const data = await resp.json();
        const el = document.getElementById('accountList');
        if (!el) return;
        el.innerHTML = data.accounts.map(a =>
            `<div style="display:flex;align-items:center;gap:8px;padding:4px 0;font-size:0.85rem;">
              <span style="color:var(--text-muted);font-size:0.75rem;">证券账户</span>
              <span style="font-weight:600;">${escapeHtml(a.name)}</span>
              <span style="color:var(--text-muted);font-size:0.75rem;">${escapeHtml(a.broker || '')}</span>
              <span style="color:var(--text-muted);font-size:0.7rem;">(${escapeHtml(a.id)})</span>
              <button class="btn-secondary" style="font-size:0.75rem;" onclick="editSecuritiesAccount('${escapeAttr(a.id)}','${escapeAttr(a.name)}','${escapeAttr(a.broker || '')}','${escapeAttr(a.notes || '')}')">编辑</button>
              <button class="btn-secondary danger" style="font-size:0.75rem;" onclick="deleteSecuritiesAccount('${escapeAttr(a.id)}')">停用</button>
            </div>`
        ).join('');
    } catch(e) {}
}

async function saveLoginUser(event) {
    event.preventDefault();
    const payload = {
        username: document.getElementById('loginUserUsername').value.trim(),
        display_name: document.getElementById('loginUserDisplayName').value.trim(),
        password: document.getElementById('loginUserPassword').value,
    };
    if (!payload.username) {
        toast('error', '请输入登录用户名');
        return;
    }
    try {
        const resp = await fetch(`${API_BASE}/auth/users`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (!resp.ok) {
            toast('error', '新增失败: ' + (data.detail || data.error || ''));
            return;
        }
        toast('success', '登录账户已创建');
        document.getElementById('loginUserManagementPanel').reset();
        loadAccountList();
    } catch(e) {
        toast('error', '新增失败: ' + e.message);
    }
}

async function deleteLoginUser(id) {
    try {
        const resp = await fetch(`${API_BASE}/auth/users/${encodeURIComponent(id)}`, { method: 'DELETE' });
        const data = await resp.json();
        if (!resp.ok || !data.success) {
            toast('error', '停用失败: ' + (data.detail || data.error || ''));
            return;
        }
        toast('success', '登录账户已停用');
        loadAccountList();
    } catch(e) {
        toast('error', '停用失败: ' + e.message);
    }
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
        toast('error', '请输入证券账户名称');
        return;
    }
    const method = id ? 'PUT' : 'POST';
    const url = id ? `${API_BASE}/accounts/${encodeURIComponent(id)}` : `${API_BASE}/accounts`;
    try {
        const resp = await fetch(url, {
            method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (!resp.ok || !data.success) {
            toast('error', '保存失败: ' + (data.detail || data.error || ''));
            return;
        }
        toast('success', '证券账户已保存');
        document.getElementById('securitiesAccountForm').reset();
        document.getElementById('securitiesAccountId').value = '';
        loadAccountList();
        if (typeof loadAccounts === 'function') loadAccounts();
    } catch(e) {
        toast('error', '保存失败: ' + e.message);
    }
}

async function deleteSecuritiesAccount(id) {
    try {
        const resp = await fetch(`${API_BASE}/accounts/${encodeURIComponent(id)}`, { method: 'DELETE' });
        const data = await resp.json();
        if (!resp.ok || !data.success) {
            toast('error', '停用失败: ' + (data.detail || data.error || ''));
            return;
        }
        toast('success', '证券账户已停用');
        loadAccountList();
        if (typeof loadAccounts === 'function') loadAccounts();
    } catch(e) {
        toast('error', '停用失败: ' + e.message);
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
    aiSettingsSubtab,
    loadModelProviders,
    openModelProviderEditor,
    closeProviderEditModal,
    saveModelProviderEdit,
    fetchProviderEditModels,
    onProviderEditUsageChange,
    onAiProviderChange,
    onVerificationProviderChange,
    onEmbeddingProviderChange,
    syncLegacyAiProviderFields,
    syncLegacyVerificationProviderFields,
    syncLegacyEmbeddingProviderFields,
    refreshModelProvider,
    testModelProvider,
    deleteModelProvider,
    loadWorkerPoolConfig,
    addWorkerPoolRow,
    openWorkerPoolEditor,
    closeWorkerPoolEditor,
    saveWorkerPoolEdit,
    removeWorkerPoolRow,
    saveWorkerPoolConfig,
    loadHermesToolPolicy,
    saveHermesToolPolicy,
    loadTradeMemoryEmbeddingStatus,
    testTradeMemoryEmbeddingConnection,
    renderEmbeddingConnectionResult,
    backfillTradeMemoryEmbeddings,
    loadDataAudit,
    loadDataHealth,
    loadSystemDiagnostics,
    fixDataHealth,
    loadWorkspaceTemplates,
    saveWorkspaceTemplate,
    applyWorkspaceTemplate,
});
