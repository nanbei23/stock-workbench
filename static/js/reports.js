(function () {
    'use strict';

    const SIG_LABEL = {
        STRONG_BUY: '强烈买入',
        BUY: '买入',
        OVERWEIGHT: '增持',
        HOLD: '持有',
        UNDERWEIGHT: '减持',
        SELL: '卖出',
        STRONG_SELL: '强烈卖出'
    };

    let reports = [];
    let filtered = [];
    let snapshotsLoaded = false;
    let plansLoaded = false;
    let jobsLoaded = false;
    let modelProvidersLoaded = false;
    let modelProviders = [];
    let workerStatusCache = null;
    const selected = new Set();
    const selectedSignals = new Set();
    let reportMarketFilter = 'all';
    const collapsedReportGroups = new Set();
    const POSITION_PLAN_ROLES = [
        ['portfolio_manager', '组合经理'],
        ['risk_manager', '风控经理'],
        ['trader', '交易员'],
        ['skeptic', '反方审查'],
        ['chair', '最终裁决']
    ];

    document.addEventListener('DOMContentLoaded', initReportLibrary);

    async function initReportLibrary() {
        if (window.StockMarketPermissions?.load) await window.StockMarketPermissions.load();
        renderReportMarketFilterState();
        await loadReportLibrary();
    }

    async function requestJson(url, options) {
        const resp = await fetch(url, options || {});
        const text = await resp.text();
        const data = text ? JSON.parse(text) : {};
        if (!resp.ok) {
            throw new Error(data.detail || data.message || resp.statusText);
        }
        return data;
    }

    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
    }

    function escapeAttr(value) {
        return escapeHtml(value).replace(/`/g, '&#96;');
    }

    function formatPct(value) {
        if (value == null || value === '') return '--';
        const num = Number(value);
        if (!Number.isFinite(num)) return '--';
        return `${(num > 1 ? num : num * 100).toFixed(1)}%`;
    }

    function formatScore(value) {
        if (value == null || value === '') return '--';
        const num = Number(value);
        if (!Number.isFinite(num)) return '--';
        return (num <= 1 ? num * 100 : num).toFixed(1);
    }

    function formatTime(value) {
        if (!value) return '--';
        const d = new Date(value);
        if (Number.isNaN(d.getTime())) return String(value);
        return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    }

    function formatDate(value) {
        if (!value) return '未知日期';
        const d = new Date(value);
        if (Number.isNaN(d.getTime())) return String(value).slice(0, 10) || '未知日期';
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    }

    function reportBatchKey(report) {
        const taskId = String(report.task_id || '');
        const match = taskId.match(/^snapshot-(.+)-(\d{6})$/);
        if (match) return match[1];
        return taskId ? `task:${taskId}` : 'manual';
    }

    function reportBatchLabel(groupKey) {
        if (groupKey === 'manual') return '单次/手动报告';
        if (groupKey.startsWith('task:')) return groupKey.slice(5);
        return `批量任务 ${groupKey}`;
    }

    function stageLabel(value) {
        return { screening: '初筛', shortlist: '精选', final: '最终建仓' }[value] || value || '--';
    }

    function strategyLabel(value) {
        return {
            auto: '自动',
            full_text: '完整原文',
            summary_plus_evidence: '摘要+证据',
            candidate_screening: '候选筛选',
            single: '单模型',
            dual: '双模型',
            per_role: '按角色'
        }[value] || value || '--';
    }

    function adoptionLabel(value) {
        return {
            draft: '待确认',
            adopted: '已采纳',
            superseded: '已被替代'
        }[value] || value || '待确认';
    }

    function adoptionClass(value) {
        return {
            draft: 'signal-hold',
            adopted: 'signal-buy',
            superseded: 'signal-sell'
        }[value] || 'signal-hold';
    }

    function statusLabel(value) {
        return {
            pending: '等待',
            running: '运行中',
            completed: '已完成',
            failed: '失败',
            skipped: '已跳过',
            waiting_snapshot: '缺少快照',
            cancelled: '已取消',
            interrupted: '已中断',
            pausing: '暂停中',
            paused: '已暂停',
            quota_paused: '额度暂停',
            guard_paused: '熔断暂停',
            timeout: '超时'
        }[value] || value || '--';
    }

    function statusClass(value) {
        if (value === 'completed' || value === 'skipped') return 'signal-buy';
        if (value === 'running' || value === 'pending' || value === 'pausing' || value === 'paused' || value === 'quota_paused' || value === 'guard_paused') return 'signal-hold';
        return 'signal-sell';
    }

    function workerStateLabel(value) {
        return {
            running: '运行中',
            online: '在线',
            idle: '空闲',
            stale: '卡死/陈旧',
            offline: '离线',
            disabled: '已停用'
        }[value] || value || '--';
    }

    function formatJsonBlock(value) {
        return `<pre>${escapeHtml(JSON.stringify(value || {}, null, 2))}</pre>`;
    }

    function parseJsonish(value) {
        if (!value || typeof value !== 'string') return value || {};
        try {
            return JSON.parse(value);
        } catch (_err) {
            try {
                return JSON.parse(value.replace(/\\n/g, '\n'));
            } catch (_err2) {
                return value;
            }
        }
    }

    function humanizeReportKey(key) {
        return String(key || '')
            .replace(/_/g, ' ')
            .replace(/\b\w/g, ch => ch.toUpperCase());
    }

    function formatStructuredPrimitive(value) {
        if (value == null || value === '') return '<span class="structured-empty">暂无</span>';
        const raw = String(value).replace(/\\n/g, '\n');
        if (window.marked && /[\n#>*`-]|\*\*/.test(raw)) return window.marked.parse(raw);
        return `<span>${escapeHtml(raw)}</span>`;
    }

    function formatStructuredValue(value, depth = 0) {
        const parsed = typeof value === 'string' ? parseJsonish(value) : value;
        if (parsed == null || parsed === '') return '<span class="structured-empty">暂无</span>';
        if (depth > 6) return formatStructuredPrimitive(parsed);
        if (Array.isArray(parsed)) {
            if (!parsed.length) return '<span class="structured-empty">暂无</span>';
            return `<ul class="structured-list">${parsed.map(item => `<li>${formatStructuredValue(item, depth + 1)}</li>`).join('')}</ul>`;
        }
        if (typeof parsed === 'object') {
            const entries = Object.entries(parsed).filter(([, val]) => val !== undefined && val !== null && val !== '');
            if (!entries.length) return '<span class="structured-empty">暂无</span>';
            return `<div class="structured-report depth-${Math.min(depth, 3)}">${entries.map(([key, val]) => `
                <section class="structured-row">
                    <div class="structured-key">${escapeHtml(humanizeReportKey(key))}</div>
                    <div class="structured-value">${formatStructuredValue(val, depth + 1)}</div>
                </section>
            `).join('')}</div>`;
        }
        return formatStructuredPrimitive(parsed);
    }

    function metricCard(label, value, subtext = '') {
        return `<div class="batch-analysis-card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong>${subtext ? `<em>${escapeHtml(subtext)}</em>` : ''}</div>`;
    }

    function formatChange(value) {
        if (value == null || value === '') return '--';
        const num = Number(value);
        if (!Number.isFinite(num)) return '--';
        return `${num >= 0 ? '+' : ''}${num.toFixed(3)}%`;
    }

    function switchReportTab(tab) {
        document.querySelectorAll('#reportLibraryTabs button').forEach(btn => btn.classList.toggle('active', btn.dataset.tab === tab));
        document.querySelectorAll('.report-tab-panel').forEach(panel => panel.classList.toggle('active', panel.id === `tab-${tab}`));
        const title = document.getElementById('reportTabTitle');
        const hint = document.getElementById('reportTabHint');
        const meta = {
            reports: ['报告列表', '筛选、比较、勾选完整单股报告，并生成组合级多角色建仓建议。'],
            snapshots: ['数据快照', '查看七层数据底稿的完整性、批次、关联报告和快照详情。'],
            plans: ['建仓计划', '长期保存、回看和对比多角色建仓建议。'],
            jobs: ['批量任务', '查看数据预取、报告生成和建仓计划任务进度。']
        }[tab] || ['报告列表', ''];
        if (title) title.textContent = meta[0];
        if (hint) hint.textContent = meta[1];
        if (tab === 'snapshots' && !snapshotsLoaded) loadSnapshots();
        if (tab === 'plans' && !plansLoaded) loadPositionPlans();
        if (tab === 'jobs' && !jobsLoaded) loadBatchJobs();
    }

    async function loadWorkerRuntimeSummary() {
        const box = document.getElementById('workerRuntimeSummary');
        const panel = document.getElementById('workerStatusPanel');
        if (!box) return;
        try {
            const data = await requestJson('/api/batch-research/workers?stale_minutes=15');
            workerStatusCache = data;
            const workers = data.workers || [];
            const latestHeartbeat = workers.map(worker => worker.heartbeat_at).filter(Boolean).sort().pop();
            box.innerHTML = `
                <div><span>Worker 在线</span><strong>${Number(data.summary?.online || 0)}</strong></div>
                <div><span>运行 / 空闲</span><strong>${Number(data.summary?.running || 0)} / ${Number(data.summary?.idle || 0)}</strong></div>
                <div><span>卡死 / 离线</span><strong>${Number(data.summary?.stale || 0)} / ${Number(data.summary?.offline || 0)}</strong></div>
                <div><span>最近心跳</span><strong>${escapeHtml(formatTime(data.summary?.latest_heartbeat_at || latestHeartbeat))}</strong></div>
            `;
            if (panel) {
                panel.innerHTML = `<table class="report-library-table worker-status-table">
                    <thead><tr><th>Worker</th><th>状态</th><th>模型池</th><th>当前</th><th>进度</th><th>心跳</th></tr></thead>
                    <tbody>${workers.map(worker => {
                        const pool = (worker.model_pool || []).map(item => `${item.ready ? '' : '!'}${escapeHtml(item.name || item.provider_id)} / ${escapeHtml(item.model || '--')}`).join('<br>') || '--';
                        const counts = worker.counts || {};
                        return `<tr>
                            <td><strong>${escapeHtml(worker.name || worker.worker_id)}</strong><span>${escapeHtml(worker.worker_id)}</span></td>
                            <td><span class="report-signal ${escapeHtml(statusClass(worker.state === 'running' ? 'running' : worker.state === 'stale' || worker.state === 'offline' ? 'failed' : 'completed'))}">${escapeHtml(workerStateLabel(worker.state))}</span></td>
                            <td>${pool}</td>
                            <td>${escapeHtml(worker.current_code || '--')}<span>${escapeHtml(worker.current_model || '')}${worker.fallback_model ? ` -> ${escapeHtml(worker.fallback_model)}` : ''}</span></td>
                            <td>完成 ${Number(counts.completed || 0)} / 失败 ${Number(counts.failed || 0)}<span>等待 ${Number(counts.pending || 0)} / 待数据 ${Number(counts.waiting || 0)}</span></td>
                            <td>${escapeHtml(formatTime(worker.heartbeat_at))}</td>
                        </tr>`;
                    }).join('') || '<tr><td colspan="6" class="library-empty-state">暂无 Worker 配置或运行记录</td></tr>'}</tbody>
                </table>`;
            }
        } catch (err) {
            box.innerHTML = `
                <div><span>Worker 在线</span><strong>--</strong></div>
                <div><span>陈旧 Worker</span><strong>--</strong></div>
                <div><span>后台模式</span><strong>未知</strong></div>
                <div><span>状态</span><strong>${escapeHtml(err.message)}</strong></div>
            `;
            if (panel) panel.innerHTML = `<div class="library-empty-state">Worker 状态加载失败：${escapeHtml(err.message)}</div>`;
        }
    }

    async function loadReportLibrary() {
        const tbody = document.getElementById('reportLibraryRows');
        if (tbody) tbody.innerHTML = '<tr><td colspan="8" class="library-empty-state">正在加载...</td></tr>';
        try {
            const data = await requestJson('/api/ai/reports?limit=500');
            reports = data.reports || [];
            filterReportLibrary();
        } catch (err) {
            if (tbody) tbody.innerHTML = `<tr><td colspan="8" class="library-empty-state">加载失败：${escapeHtml(err.message)}</td></tr>`;
            updateSelectionSummary();
        }
    }

    function filterReportLibrary() {
        const text = (document.getElementById('reportFilterText')?.value || '').trim().toLowerCase();
        const sortBy = document.getElementById('reportSortBy')?.value || 'created_desc';
        const minConfidence = Number(document.getElementById('reportMinConfidence')?.value || 0);
        const maxRiskInput = document.getElementById('reportMaxRisk')?.value || '';
        const maxRisk = maxRiskInput === '' ? null : Number(maxRiskInput);
        filtered = reports.filter(report => {
            const haystack = `${report.code || ''} ${report.name || ''}`.toLowerCase();
            if (text && !haystack.includes(text)) return false;
            if (selectedSignals.size && !selectedSignals.has(report.signal)) return false;
            if (!filterByTradingMarket(report, reportMarketFilter)) return false;
            const confidence = Number(report.confidence || 0) * 100;
            if (minConfidence && confidence < minConfidence) return false;
            const risk = Number(report.risk_score == null ? 0 : report.risk_score);
            const risk100 = risk <= 1 ? risk * 100 : risk;
            if (maxRisk !== null && risk100 > maxRisk) return false;
            return true;
        });
        filtered.sort((a, b) => {
            if (sortBy === 'confidence_desc') return Number(b.confidence || 0) - Number(a.confidence || 0);
            if (sortBy === 'risk_asc') return Number(a.risk_score || 0) - Number(b.risk_score || 0);
            if (sortBy === 'risk_desc') return Number(b.risk_score || 0) - Number(a.risk_score || 0);
            return new Date(b.created_at || 0) - new Date(a.created_at || 0);
        });
        renderReportRows();
    }

    function renderSignalFilterState() {
        document.querySelectorAll('#reportSignalFilters .signal-filter-chip').forEach(btn => {
            const signal = btn.dataset.signal || '';
            btn.classList.toggle('active', signal ? selectedSignals.has(signal) : selectedSignals.size === 0);
        });
    }

    function toggleSignalFilter(signal) {
        if (!signal) {
            selectedSignals.clear();
        } else if (selectedSignals.has(signal)) {
            selectedSignals.delete(signal);
        } else {
            selectedSignals.add(signal);
        }
        renderSignalFilterState();
        filterReportLibrary();
    }

    function filterByTradingMarket(report, filter = reportMarketFilter) {
        return window.StockMarketPermissions?.matchesFilter?.(report.code, filter) ?? true;
    }

    function renderReportMarketFilterState() {
        document.querySelectorAll('#reportMarketFilters .signal-filter-chip').forEach(btn => {
            btn.classList.toggle('active', (btn.dataset.market || 'all') === reportMarketFilter);
        });
    }

    function setReportMarketFilter(filter) {
        reportMarketFilter = filter || 'all';
        renderReportMarketFilterState();
        filterReportLibrary();
    }

    function updateSelectionSummary() {
        const selectedCount = document.getElementById('selectedReportCount');
        if (selectedCount) selectedCount.textContent = String(selected.size);
        const selectAll = document.getElementById('reportSelectAll');
        if (!selectAll) return;
        const visibleIds = filtered.map(report => Number(report.id));
        const visibleSelected = visibleIds.filter(id => selected.has(id)).length;
        selectAll.checked = Boolean(visibleIds.length && visibleSelected === visibleIds.length);
        selectAll.indeterminate = Boolean(visibleSelected && visibleSelected < visibleIds.length);
    }

    function renderReportRows() {
        const countEl = document.getElementById('reportLibraryCount');
        if (countEl) countEl.textContent = `${filtered.length} / ${reports.length} 份`;
        const tbody = document.getElementById('reportLibraryRows');
        if (!tbody) return;
        if (!filtered.length) {
            tbody.innerHTML = '<tr><td colspan="8" class="library-empty-state">没有符合条件的报告</td></tr>';
            updateSelectionSummary();
            return;
        }
        const groupBy = document.getElementById('reportGroupBy')?.value || 'none';
        tbody.innerHTML = groupBy === 'none'
            ? filtered.map(renderReportRow).join('')
            : renderGroupedReportRows(groupBy);
        updateSelectionSummary();
    }

    function renderReportRow(report) {
        const signal = report.signal || 'HOLD';
        const id = Number(report.id);
        return `<tr class="${selected.has(id) ? 'selected' : ''}" onclick="previewReport(${id})">
            <td onclick="event.stopPropagation()"><input type="checkbox" data-report-id="${Number(report.id)}" ${selected.has(Number(report.id)) ? 'checked' : ''} onchange="toggleReportSelection(${Number(report.id)}, this.checked)"></td>
            <td><strong>${escapeHtml(report.name || report.code)}</strong><span>${escapeHtml(report.code)}</span></td>
            <td><span class="report-signal signal-${escapeHtml(signal.toLowerCase().replace(/_/g, '-'))}">${escapeHtml(SIG_LABEL[signal] || signal)}</span></td>
            <td>${formatPct(report.confidence)}</td>
            <td>${formatScore(report.risk_score)}</td>
            <td>${report.fact_accuracy == null ? '--' : `${Number(report.fact_accuracy).toFixed(1)}%`} / ${Number(report.hallucinations || 0)}项</td>
            <td>${escapeHtml(report.depth || 'standard')} · ${escapeHtml(report.model_mode || 'balanced')}</td>
            <td>${escapeHtml(formatTime(report.created_at))}</td>
        </tr>`;
    }

    function renderGroupedReportRows(groupBy) {
        const groups = new Map();
        for (const report of filtered) {
            const key = groupBy === 'date' ? formatDate(report.created_at) : reportBatchKey(report);
            const label = groupBy === 'date' ? key : reportBatchLabel(key);
            if (!groups.has(key)) groups.set(key, { key, label, reports: [] });
            groups.get(key).reports.push(report);
        }
        return Array.from(groups.values()).map(group => {
            const groupToken = `${groupBy}:${group.key}`;
            const collapsed = collapsedReportGroups.has(groupToken);
            const selectedCount = group.reports.filter(report => selected.has(Number(report.id))).length;
            const latest = group.reports[0]?.created_at;
            const rows = collapsed ? '' : group.reports.map(renderReportRow).join('');
            return `<tr class="report-group-header" onclick="toggleReportGroup('${escapeAttr(groupToken)}')">
                <td colspan="8">
                    <button type="button" class="report-group-toggle" aria-label="展开或折叠">${collapsed ? '+' : '-'}</button>
                    <strong>${escapeHtml(group.label)}</strong>
                    <span>${group.reports.length} 份报告 · 已选 ${selectedCount} · 最近 ${escapeHtml(formatTime(latest))}</span>
                </td>
            </tr>${rows}`;
        }).join('');
    }

    function toggleReportGroup(groupToken) {
        if (collapsedReportGroups.has(groupToken)) collapsedReportGroups.delete(groupToken);
        else collapsedReportGroups.add(groupToken);
        renderReportRows();
    }

    async function previewReport(id) {
        const meta = document.getElementById('reportPreviewMeta');
        const body = document.getElementById('reportPreview');
        if (meta) meta.textContent = `#${id}`;
        if (body) body.innerHTML = '<div class="library-empty-state">加载报告...</div>';
        try {
            const report = await requestJson(`/api/ai/reports/${encodeURIComponent(id)}`);
            if (meta) meta.textContent = `${report.name || report.code || ''} ${report.code || ''}`;
            if (!body) return;
            body.innerHTML = `
                <div class="preview-signal">
                    <span class="report-signal signal-${escapeHtml((report.signal || 'hold').toLowerCase().replace(/_/g, '-'))}">${escapeHtml(SIG_LABEL[report.signal] || report.signal || 'HOLD')}</span>
                    <span>置信度 ${formatPct(report.confidence)}</span>
                    <span>风险 ${formatScore(report.risk_score)}</span>
                </div>
                <h4>最终决策</h4>
                <div class="preview-block">${formatMarkdown(report.final_decision || report.result?.reasoning || '暂无')}</div>
                <h4>交易计划</h4>
                <div class="preview-block">${formatMarkdown(report.trader_plan || '暂无')}</div>
                <h4>风险复核</h4>
                <div class="preview-block">${formatMarkdown(report.risk_debate || '暂无')}</div>
                <div class="preview-actions">
                    <a class="btn btn-sm" href="/ai?report_id=${Number(id)}">在 AI 分析台打开</a>
                    <a class="btn btn-sm" href="/api/ai/report/${Number(id)}/pdf">PDF</a>
                </div>
            `;
        } catch (err) {
            if (body) body.innerHTML = `<div class="library-empty-state">加载失败：${escapeHtml(err.message)}</div>`;
        }
    }

    function formatMarkdown(text) {
        const parsed = typeof text === 'string' ? parseJsonish(text) : text;
        if (parsed && typeof parsed === 'object') return formatStructuredValue(parsed);
        const raw = String(parsed || '').replace(/\\n/g, '\n');
        if (window.marked) return window.marked.parse(raw);
        return `<pre>${escapeHtml(raw)}</pre>`;
    }

    function toggleReportSelection(id, checked) {
        if (checked) selected.add(id);
        else selected.delete(id);
        renderReportRows();
    }

    function toggleAllReports(checked) {
        filtered.forEach(report => {
            const id = Number(report.id);
            if (checked) selected.add(id);
            else selected.delete(id);
        });
        renderReportRows();
    }

    async function createPlanFromReportLibrary() {
        if (!selected.size) return alert('请先勾选要进入组合级讨论的完整报告');
        const modal = document.getElementById('positionPlanModal');
        if (modal) modal.classList.add('show');
        togglePlanRoleModelFields();
        await loadPlanSchedulingOptions();
    }

    function closePositionPlanModal() {
        const modal = document.getElementById('positionPlanModal');
        if (modal) modal.classList.remove('show');
    }

    async function submitPositionPlanFromModal() {
        const reportIds = [...selected];
        if (!reportIds.length) return alert('请先勾选要进入组合级讨论的完整报告');
        const selectedReports = reports.filter(report => selected.has(Number(report.id)));
        const excludedByPermission = selectedReports.filter(report => !(window.StockMarketPermissions?.isAllowed?.(report.code) ?? true));
        const modelStrategy = document.getElementById('planModelStrategyInput')?.value || 'single';
        const payload = {
            job_type: 'position_plan',
            report_ids: reportIds,
            multi_role: true,
            plan_top_n: Number(document.getElementById('planTopNInput')?.value || 10),
            stage: document.getElementById('planStageInput')?.value || 'final',
            parent_plan_id: document.getElementById('planParentInput')?.value?.trim() || null,
            context_strategy: document.getElementById('planContextInput')?.value || 'auto',
            model_strategy: modelStrategy,
            role_models: modelStrategy === 'per_role' ? collectPlanRoleModels() : {},
            allowed_worker_ids: collectCheckedValues('planWorkerGrid'),
            primary_provider_ids: collectCheckedValues('planPrimaryProviderGrid'),
            fallback_provider_ids: collectCheckedValues('planFallbackProviderGrid'),
            model_fallback_enabled: collectCheckedValues('planFallbackProviderGrid').length > 0,
            quota_exhausted_action: document.getElementById('planQuotaActionInput')?.value || 'switch_model',
            max_consecutive_failures: 5,
            max_failure_rate: 0.25,
            min_failure_rate_items: 5,
            title: document.getElementById('planTitleInput')?.value?.trim() || null
        };
        const preflight = await requestJson('/api/batch-research/preflight', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const warnings = preflight.warnings || [];
        const estimate = [
            `选择报告：${reportIds.length} 份`,
            ...(excludedByPermission.length ? [`交易权限过滤：将由后端排除 ${excludedByPermission.length} 份无权限市场报告`] : []),
            `预计模型调用：${preflight.estimated_role_calls || '--'} 次`,
            `Worker：${preflight.worker_count || '--'} 个`,
            `预计耗时：${preflight.estimated_duration_text || '--'}`,
            ...(preflight.recommendations || [])
        ].join('\n');
        if (!confirm(`${estimate}${warnings.length ? `\n\n风险提示：\n${warnings.join('\n')}` : ''}\n\n创建任务吗？`)) return;
        const resp = await requestJson('/api/batch-research/jobs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        closePositionPlanModal();
        alert(`多角色建仓建议任务已创建：${resp.job_id}\n预计模型调用：${preflight.estimated_role_calls || '--'} 次`);
        jobsLoaded = false;
        plansLoaded = false;
        switchReportTab('jobs');
    }

    function collectCheckedValues(containerId) {
        return Array.from(document.querySelectorAll(`#${containerId} input[type="checkbox"]:checked`)).map(input => input.value).filter(Boolean);
    }

    async function loadPlanSchedulingOptions() {
        await Promise.all([loadPlanModelProviders(), loadWorkerRuntimeSummary()]);
        renderPlanWorkerOptions();
        renderPlanProviderOptions();
    }

    function renderPlanWorkerOptions() {
        const grid = document.getElementById('planWorkerGrid');
        if (!grid) return;
        const workers = workerStatusCache?.workers || [];
        const enabled = workers.filter(worker => worker.enabled !== false);
        if (!enabled.length) {
            grid.innerHTML = '<div class="library-empty-state">暂无启用 Worker；留空表示由可用 worker 自动领取。</div>';
            return;
        }
        grid.innerHTML = enabled.map(worker => `
            <label class="mini-check-row">
                <input type="checkbox" value="${escapeAttr(worker.worker_id)}" checked>
                <span>${escapeHtml(worker.name || worker.worker_id)} · ${escapeHtml(workerStateLabel(worker.state))}</span>
            </label>
        `).join('');
    }

    function renderPlanProviderOptions() {
        const primary = document.getElementById('planPrimaryProviderGrid');
        const fallback = document.getElementById('planFallbackProviderGrid');
        const providerRows = (checkedFirst) => modelProviders.map((provider, index) => `
            <label class="mini-check-row">
                <input type="checkbox" value="${escapeAttr(provider.id)}" ${checkedFirst && index === 0 ? 'checked' : ''}>
                <span>${escapeHtml(provider.name || provider.id)} · ${escapeHtml(provider.default_model || provider.deep_model || provider.quick_model || '--')}</span>
            </label>
        `).join('');
        if (primary) primary.innerHTML = modelProviders.length ? providerRows(true) : '<div class="library-empty-state">暂无可用模型配置。</div>';
        if (fallback) fallback.innerHTML = modelProviders.length > 1 ? providerRows(false) : '<div class="library-empty-state">可在设置中增加备用模型池。</div>';
    }

    async function loadPlanModelProviders() {
        if (modelProvidersLoaded) return;
        const grid = document.getElementById('planRoleModelGrid');
        if (grid) grid.innerHTML = '<div class="library-empty-state">正在读取模型配置...</div>';
        try {
            const data = await requestJson('/api/model-providers');
            modelProviders = (data.providers || []).filter(provider => provider.has_api_key && provider.base_url);
            modelProvidersLoaded = true;
        } catch (err) {
            modelProviders = [];
            modelProvidersLoaded = true;
            if (grid) grid.innerHTML = `<div class="library-empty-state">模型配置加载失败：${escapeHtml(err.message)}</div>`;
            return;
        }
        renderPlanRoleModelFields();
    }

    function providerModelOptions() {
        const options = ['<option value="">继承模型策略</option>'];
        for (const provider of modelProviders) {
            const models = provider.models && provider.models.length
                ? provider.models
                : [provider.default_model || provider.deep_model || provider.quick_model].filter(Boolean);
            for (const model of models) {
                options.push(
                    `<option value="${escapeAttr(`${provider.id}::${model}`)}">${escapeHtml(provider.name || provider.id)} / ${escapeHtml(model)}</option>`
                );
            }
        }
        return options.join('');
    }

    function renderPlanRoleModelFields() {
        const grid = document.getElementById('planRoleModelGrid');
        if (!grid) return;
        if (!modelProviders.length) {
            grid.innerHTML = '<div class="library-empty-state">暂无可用于按角色选择的模型配置。请先到设置页保存第三方模型配置并获取模型列表。</div>';
            return;
        }
        const options = providerModelOptions();
        grid.innerHTML = POSITION_PLAN_ROLES.map(([roleKey, roleName]) => `
            <div class="role-model-row">
                <label for="plan-role-model-${escapeAttr(roleKey)}">${escapeHtml(roleName)}</label>
                <select class="form-input" id="plan-role-model-${escapeAttr(roleKey)}" data-role-key="${escapeAttr(roleKey)}">
                    ${options}
                </select>
            </div>
        `).join('');
    }

    function togglePlanRoleModelFields() {
        const strategy = document.getElementById('planModelStrategyInput')?.value || 'single';
        const wrap = document.getElementById('planRoleModelFields');
        if (!wrap) return;
        wrap.style.display = strategy === 'per_role' ? 'grid' : 'none';
        if (strategy === 'per_role') loadPlanModelProviders();
    }

    function collectPlanRoleModels() {
        const roleModels = {};
        document.querySelectorAll('#planRoleModelGrid select[data-role-key]').forEach(select => {
            const value = select.value || '';
            if (!value.includes('::')) return;
            const [providerId, ...modelParts] = value.split('::');
            const model = modelParts.join('::');
            if (!providerId || !model) return;
            roleModels[select.dataset.roleKey] = { provider_id: providerId, model };
        });
        return roleModels;
    }

    async function loadSnapshots() {
        const tbody = document.getElementById('snapshotRows');
        if (tbody) tbody.innerHTML = '<tr><td colspan="7" class="library-empty-state">正在加载...</td></tr>';
        try {
            const data = await requestJson('/api/reports/snapshots?limit=300');
            const snapshots = data.snapshots || [];
            renderSnapshotSummary(data.summary || {});
            snapshotsLoaded = true;
            if (!snapshots.length) {
                tbody.innerHTML = '<tr><td colspan="7" class="library-empty-state">暂无数据快照</td></tr>';
                return;
            }
            tbody.innerHTML = snapshots.map(item => {
                const layers = item.summary?.layers || item.validation?.checked_layers || [];
                const totalBytes = item.summary?.total_bytes || 0;
                return `<tr onclick="previewSnapshot(${Number(item.id)})">
                    <td><strong>${escapeHtml(item.name || item.code)}</strong><span>${escapeHtml(item.code)}</span></td>
                    <td>${item.ok ? '<span class="report-signal signal-buy">完整</span>' : '<span class="report-signal signal-sell">不完整</span>'}</td>
                    <td>${escapeHtml(layers.join(' / ') || '--')}</td>
                    <td>${Number(totalBytes || 0).toLocaleString()}</td>
                    <td>${escapeHtml(item.run_id || '--')}</td>
                    <td>${Number(item.linked_report_count || 0)}</td>
                    <td>${escapeHtml(formatTime(item.created_at))}</td>
                </tr>`;
            }).join('');
        } catch (err) {
            if (tbody) tbody.innerHTML = `<tr><td colspan="7" class="library-empty-state">加载失败：${escapeHtml(err.message)}</td></tr>`;
        }
    }

    function renderSnapshotSummary(summary) {
        const el = document.getElementById('snapshotQualitySummary');
        if (!el) return;
        const missing = Object.entries(summary.missing_layers || {})
            .slice(0, 3)
            .map(([layer, count]) => `${layer} ${count}`)
            .join(' / ') || '--';
        el.innerHTML = `
            <div><span>快照数</span><strong>${Number(summary.total || 0).toLocaleString()}</strong></div>
            <div><span>完整率</span><strong>${Number(summary.complete_rate || 0).toFixed(1)}%</strong></div>
            <div><span>不完整</span><strong>${Number(summary.incomplete || 0).toLocaleString()}</strong></div>
            <div><span>主要缺失层</span><strong>${escapeHtml(missing)}</strong></div>
        `;
    }

    async function previewSnapshot(id) {
        const meta = document.getElementById('reportPreviewMeta');
        const body = document.getElementById('reportPreview');
        if (meta) meta.textContent = `快照 #${id}`;
        if (body) body.innerHTML = '<div class="library-empty-state">加载快照...</div>';
        try {
            const item = await requestJson(`/api/reports/snapshots/${Number(id)}`);
            if (meta) meta.textContent = `${item.name || item.code} 快照 #${item.id}`;
            if (body) body.innerHTML = `
                <div class="preview-signal">
                    ${item.ok ? '<span class="report-signal signal-buy">完整</span>' : '<span class="report-signal signal-sell">不完整</span>'}
                    <span>批次 ${escapeHtml(item.run_id || '--')}</span>
                    <span>${escapeHtml(formatTime(item.created_at))}</span>
                </div>
                <h4>完整性</h4>
                <div class="preview-block">${formatJsonBlock(item.validation)}</div>
                <h4>摘要</h4>
                <div class="preview-block">${formatJsonBlock(item.summary)}</div>
                <h4>七层数据</h4>
                <div class="preview-block">${formatJsonBlock(item.snapshot)}</div>
            `;
        } catch (err) {
            if (body) body.innerHTML = `<div class="library-empty-state">加载失败：${escapeHtml(err.message)}</div>`;
        }
    }

    async function loadPositionPlans() {
        const tbody = document.getElementById('positionPlanRows');
        if (tbody) tbody.innerHTML = '<tr><td colspan="8" class="library-empty-state">正在加载...</td></tr>';
        try {
            const data = await requestJson('/api/position-plans?limit=100');
            const plans = data.plans || [];
            plansLoaded = true;
            if (!plans.length) {
                tbody.innerHTML = '<tr><td colspan="8" class="library-empty-state">暂无建仓计划</td></tr>';
                return;
            }
            tbody.innerHTML = plans.map(plan => {
                const modelConfig = plan.model_config_json || {};
                const marketCaptured = plan.market_context_captured_at || plan.decision_market_snapshot_json?.captured_at || '';
                return `<tr onclick="previewPositionPlan('${encodeURIComponent(plan.plan_id)}')">
                    <td><strong>${escapeHtml(plan.title || plan.plan_id)}</strong><span>${escapeHtml(plan.plan_id)}</span></td>
                    <td>${escapeHtml(stageLabel(plan.stage))}</td>
                    <td><span class="report-signal ${escapeHtml(adoptionClass(plan.adoption_status))}">${escapeHtml(adoptionLabel(plan.adoption_status))}</span></td>
                    <td>${escapeHtml(strategyLabel(plan.context_strategy))}</td>
                    <td>${Number(plan.candidate_count || 0)} / ${Number(plan.selected_count || 0)}</td>
                    <td>${escapeHtml(strategyLabel(plan.model_strategy))}<span>${escapeHtml(modelConfig.snapshot_model_tier || '')}</span></td>
                    <td>${escapeHtml(plan.batch_job_id || '--')}<span>${marketCaptured ? `行情 ${escapeHtml(formatTime(marketCaptured))}` : '未校准行情'}</span></td>
                    <td>${escapeHtml(formatTime(plan.created_at))}</td>
                </tr>`;
            }).join('');
        } catch (err) {
            if (tbody) tbody.innerHTML = `<tr><td colspan="8" class="library-empty-state">加载失败：${escapeHtml(err.message)}</td></tr>`;
        }
    }

    async function previewPositionPlan(encodedPlanId) {
        const planId = decodeURIComponent(encodedPlanId);
        const meta = document.getElementById('reportPreviewMeta');
        const body = document.getElementById('reportPreview');
        if (meta) meta.textContent = planId;
        if (body) body.innerHTML = '<div class="library-empty-state">加载建仓计划...</div>';
        try {
            const plan = await requestJson(`/api/position-plans/${encodeURIComponent(planId)}`);
            if (meta) meta.textContent = `${stageLabel(plan.stage)} ${plan.plan_id}`;
            const items = plan.items || [];
            const marketSnapshot = plan.decision_market_snapshot_json || {};
            const marketRows = (marketSnapshot.summary || []).slice(0, 20).map(item => {
                const day = item.day || {};
                return `<tr>
                    <td>${escapeHtml(item.name || item.code)}<span>${escapeHtml(item.code)}</span></td>
                    <td>${escapeHtml(item.status || '--')}</td>
                    <td>${Number(item.price || 0).toFixed(3)}</td>
                    <td>${Number(item.change_pct || 0).toFixed(3)}%</td>
                    <td>${Number(day.return_5d_pct || 0).toFixed(3)}%</td>
                    <td>${Number(day.return_20d_pct || 0).toFixed(3)}%</td>
                </tr>`;
            }).join('');
            const itemRows = items.map(item => `<tr>
                <td>${escapeHtml(item.name || item.code)}<span>${escapeHtml(item.code)}</span></td>
                <td>${escapeHtml(item.action)}</td>
                <td>${Number(item.suggested_amount || 0).toFixed(3)}</td>
                <td>${Number(item.position_pct || 0).toFixed(3)}%</td>
            </tr>`).join('');
            if (body) body.innerHTML = `
                <div class="preview-signal">
                    <span>${escapeHtml(stageLabel(plan.stage))}</span>
                    <span>${escapeHtml(adoptionLabel(plan.adoption_status))}</span>
                    <span>${escapeHtml(strategyLabel(plan.context_strategy))}</span>
                    <span>${escapeHtml(strategyLabel(plan.model_strategy))}</span>
                </div>
                <h4>摘要</h4>
                <div class="preview-block">${formatMarkdown(plan.summary || '暂无')}</div>
                <h4>建议明细</h4>
                <div class="preview-block"><table class="report-library-table"><tbody>${itemRows || '<tr><td>暂无明细</td></tr>'}</tbody></table></div>
                <h4>模型配置</h4>
                <div class="preview-block">${formatJsonBlock(plan.model_config_json)}</div>
                <h4>决策实时行情快照</h4>
                <div class="preview-block">
                    <div class="preview-signal">
                        <span>${escapeHtml(marketSnapshot.status || '未采集')}</span>
                        <span>${escapeHtml(formatTime(plan.market_context_captured_at || marketSnapshot.captured_at || ''))}</span>
                        <span>${Number((marketSnapshot.summary || []).length || 0)} 只</span>
                    </div>
                    <table class="report-library-table"><tbody>${marketRows || '<tr><td>暂无行情校准快照</td></tr>'}</tbody></table>
                </div>
                <h4>现金 / 持仓快照</h4>
                <div class="preview-block">${formatJsonBlock({cash: plan.cash_snapshot_json, portfolio: plan.portfolio_snapshot_json})}</div>
                <h4>采纳快照</h4>
                <div class="preview-block">${formatJsonBlock(plan.confirmed_snapshot_json)}</div>
                <div class="preview-actions">
                    <a class="btn btn-sm" href="/api/position-plans/${encodeURIComponent(planId)}/markdown" target="_blank">Markdown</a>
                    ${plan.stage === 'final' && plan.adoption_status !== 'adopted' ? `<button class="btn btn-sm btn-primary" onclick="adoptPositionPlan('${encodeURIComponent(planId)}')">采纳为最终建仓计划</button>` : ''}
                    <button class="btn btn-sm" onclick="archivePositionPlan('${encodeURIComponent(planId)}')">归档</button>
                </div>
            `;
        } catch (err) {
            if (body) body.innerHTML = `<div class="library-empty-state">加载失败：${escapeHtml(err.message)}</div>`;
        }
    }

    async function archivePositionPlan(encodedPlanId) {
        const planId = decodeURIComponent(encodedPlanId);
        if (!confirm('确认归档这份建仓计划？')) return;
        await requestJson(`/api/position-plans/${encodeURIComponent(planId)}/archive`, { method: 'POST' });
        plansLoaded = false;
        await loadPositionPlans();
    }

    async function adoptPositionPlan(encodedPlanId) {
        const planId = decodeURIComponent(encodedPlanId);
        if (!confirm('确认采纳这份最终建仓计划作为 AI 绩效基准？这不会自动写交易或下单。')) return;
        await requestJson(`/api/position-plans/${encodeURIComponent(planId)}/adopt`, { method: 'POST' });
        plansLoaded = false;
        await loadPositionPlans();
        await previewPositionPlan(encodeURIComponent(planId));
    }

    async function loadBatchJobs() {
        await loadWorkerRuntimeSummary();
        const tbody = document.getElementById('batchJobRows');
        if (tbody) tbody.innerHTML = '<tr><td colspan="8" class="library-empty-state">正在加载...</td></tr>';
        try {
            const data = await requestJson('/api/batch-research/jobs?limit=100');
            const jobs = data.jobs || [];
            jobsLoaded = true;
            if (!jobs.length) {
                tbody.innerHTML = '<tr><td colspan="8" class="library-empty-state">暂无批量任务</td></tr>';
                return;
            }
            tbody.innerHTML = jobs.map(job => {
                const total = Number(job.total_count || 0);
                const done = Number(job.completed_count || 0) + Number(job.skipped_count || 0);
                const progress = total ? `${done}/${total}` : '--';
                const canResume = ['pending', 'failed', 'interrupted', 'quota_paused', 'guard_paused'].includes(job.status);
                const canRetry = Number(job.failed_count || 0) || Number(job.waiting_count || 0);
                const canCancel = ['pending', 'running', 'quota_paused', 'guard_paused'].includes(job.status);
                const canPause = ['pending', 'running', 'pausing'].includes(job.status);
                const actions = [
                    `<button class="btn btn-sm" onclick="previewBatchJob('${encodeURIComponent(job.job_id)}')">详情</button>`,
                    canPause ? `<button class="btn btn-sm" onclick="pauseBatchJob('${escapeAttr(job.job_id)}')">暂停</button>` : '',
                    canResume ? `<button class="btn btn-sm" onclick="resumeBatchJob('${escapeAttr(job.job_id)}')">继续</button>` : '',
                    canRetry ? `<button class="btn btn-sm" onclick="retryBatchJob('${escapeAttr(job.job_id)}')">重试</button>` : '',
                    canCancel ? `<button class="btn btn-sm" onclick="cancelBatchJob('${escapeAttr(job.job_id)}')">取消</button>` : '',
                ].filter(Boolean).join('');
                return `<tr>
                    <td onclick="previewBatchJob('${encodeURIComponent(job.job_id)}')"><strong>${escapeHtml(job.name || job.job_id)}</strong><span>${escapeHtml(job.job_id)}</span></td>
                    <td>${escapeHtml(job.job_type)}</td>
                    <td><span class="report-signal ${escapeHtml(statusClass(job.status))}">${escapeHtml(statusLabel(job.status))}</span></td>
                    <td>${escapeHtml(progress)}<span>失败 ${Number(job.failed_count || 0)} / 待数据 ${Number(job.waiting_count || 0)}</span></td>
                    <td>${escapeHtml(job.current_code || '--')}</td>
                    <td>${escapeHtml(job.error || '--')}</td>
                    <td>${escapeHtml(formatTime(job.created_at))}</td>
                    <td><div class="library-action-row">${actions || '<span class="muted">--</span>'}</div></td>
                </tr>`;
            }).join('');
        } catch (err) {
            if (tbody) tbody.innerHTML = `<tr><td colspan="8" class="library-empty-state">加载失败：${escapeHtml(err.message)}</td></tr>`;
        }
    }

    async function previewBatchJob(encodedJobId) {
        const jobId = decodeURIComponent(encodedJobId);
        const meta = document.getElementById('reportPreviewMeta');
        const body = document.getElementById('reportPreview');
        if (meta) meta.textContent = jobId;
        if (body) body.innerHTML = '<div class="library-empty-state">加载批量任务...</div>';
        try {
            const job = await requestJson(`/api/batch-research/jobs/${encodeURIComponent(jobId)}`);
            if (meta) meta.textContent = `${job.name || '批量任务'} ${statusLabel(job.status)}`;
            const total = Number(job.total_count || 0);
            const done = Number(job.completed_count || 0) + Number(job.skipped_count || 0);
            const runtime = parseJsonish(job.runtime_json);
            const quota = runtime?.quota || {};
            const sources = runtime?.sources || {};
            const guard = runtime?.guard || {};
            const itemRows = (job.items || []).map(item => {
                const stepTotal = Number(item.step_total || 0);
                const stepDone = Number(item.step_completed || 0);
                const stepText = stepTotal ? `${stepDone}/${stepTotal}` : '--';
                const current = item.step_error || item.current_step || item.error || '--';
                return `<tr>
                    <td><strong>${escapeHtml(item.name || item.code)}</strong><span>${escapeHtml(item.code)}</span></td>
                    <td><span class="report-signal ${escapeHtml(statusClass(item.status))}">${escapeHtml(statusLabel(item.status))}</span></td>
                    <td>${escapeHtml(stepText)}<span>${escapeHtml(item.current_step || '--')}</span></td>
                    <td>${escapeHtml(current)}</td>
                    <td><button class="btn btn-sm" onclick="previewBatchItemSteps(${Number(item.id)})">角色</button></td>
                </tr>`;
            }).join('');
            if (body) body.innerHTML = `
                <div class="preview-signal">
                    <span class="report-signal ${escapeHtml(statusClass(job.status))}">${escapeHtml(statusLabel(job.status))}</span>
                    <span>总体 ${escapeHtml(done)}/${escapeHtml(total)}</span>
                    <span>失败 ${Number(job.failed_count || 0)}</span>
                    <span>待数据 ${Number(job.waiting_count || 0)}</span>
                </div>
                <h4>任务信息</h4>
                <div class="preview-block">${formatJsonBlock({
                    job_id: job.job_id,
                    job_type: job.job_type,
                    current_code: job.current_code,
                    error: job.error,
                    worker_id: job.worker_id,
                    heartbeat_at: job.heartbeat_at,
                    lease_owner: job.lease_owner,
                    lease_until: job.lease_until,
                    pause_requested: Boolean(Number(job.pause_requested || 0)),
                    created_at: job.created_at,
                    started_at: job.started_at,
                    completed_at: job.completed_at
                })}</div>
                <h4>质量 / 后处理</h4>
                <div class="preview-block">${formatJsonBlock({
                    quality: parseJsonish(job.quality_json),
                    post_actions: parseJsonish(job.post_actions_json),
                    guard,
                    sources,
                    runtime,
                    input_snapshots: parseJsonish(job.input_snapshot_json)
                })}</div>
                <h4>模型额度</h4>
                <div class="preview-block">${formatJsonBlock({
                    state: quota.state || 'normal',
                    current_role: quota.current_role || '',
                    model: quota.model || quota.active_model?.model || '',
                    resume_after: quota.resume_after || '',
                    latest_event: (quota.events || []).slice(-1)[0] || {}
                })}</div>
                <h4>股票级进度</h4>
                <div class="preview-block batch-item-preview">
                    <table class="report-library-table">
                        <thead><tr><th>股票</th><th>状态</th><th>角色进度</th><th>当前/错误</th><th>明细</th></tr></thead>
                        <tbody>${itemRows || '<tr><td colspan="5">暂无任务明细</td></tr>'}</tbody>
                    </table>
                </div>
                <div class="preview-actions">
                    <button class="btn btn-sm" onclick="pauseBatchJob('${escapeAttr(job.job_id)}')">暂停</button>
                    <button class="btn btn-sm" onclick="resumeBatchJob('${escapeAttr(job.job_id)}')">继续</button>
                    <button class="btn btn-sm" onclick="retryBatchJob('${escapeAttr(job.job_id)}')">重试失败</button>
                    <button class="btn btn-sm" onclick="cancelBatchJob('${escapeAttr(job.job_id)}')">取消</button>
                    <button class="btn btn-sm btn-primary" onclick="previewBatchAnalysis('${escapeAttr(job.job_id)}')">批量分析</button>
                    <button class="btn btn-sm" onclick="previewFailureGroups('${escapeAttr(job.job_id)}')">失败分组</button>
                    <button class="btn btn-sm" onclick="previewRuntimeStats('${escapeAttr(job.job_id)}')">耗时统计</button>
                    <button class="btn btn-sm" onclick="previewBatchLogs('${escapeAttr(job.job_id)}')">日志</button>
                    <button class="btn btn-sm" onclick="previewBatchArtifacts('${escapeAttr(job.job_id)}')">产物</button>
                </div>
            `;
        } catch (err) {
            if (body) body.innerHTML = `<div class="library-empty-state">加载失败：${escapeHtml(err.message)}</div>`;
        }
    }

    async function previewBatchItemSteps(itemId) {
        const body = document.getElementById('reportPreview');
        if (body) body.innerHTML = '<div class="library-empty-state">加载角色步骤...</div>';
        try {
            const data = await requestJson(`/api/batch-research/items/${Number(itemId)}/steps`);
            const rows = (data.steps || []).map(step => `<tr>
                <td><strong>${escapeHtml(step.role_name || step.role_key)}</strong><span>${escapeHtml(step.role_key)}</span></td>
                <td><span class="report-signal ${escapeHtml(statusClass(step.status))}">${escapeHtml(statusLabel(step.status))}</span></td>
                <td>${Number(step.retry_count || 0)}</td>
                <td>${escapeHtml(step.error || '--')}</td>
            </tr>`).join('');
            if (body) body.innerHTML = `
                <div class="preview-signal">
                    <span>角色步骤 ${Number(data.count || 0)}</span>
                </div>
                <h4>角色执行流水</h4>
                <div class="preview-block batch-step-preview">
                    <table class="report-library-table">
                        <thead><tr><th>角色</th><th>状态</th><th>重试</th><th>错误</th></tr></thead>
                        <tbody>${rows || '<tr><td colspan="4">暂无角色步骤</td></tr>'}</tbody>
                    </table>
                </div>
            `;
        } catch (err) {
            if (body) body.innerHTML = `<div class="library-empty-state">加载失败：${escapeHtml(err.message)}</div>`;
        }
    }

    async function previewBatchAnalysis(jobId) {
        const body = document.getElementById('reportPreview');
        const meta = document.getElementById('reportPreviewMeta');
        if (meta) meta.textContent = `${jobId} 批量分析`;
        if (body) body.innerHTML = '<div class="library-empty-state">加载批量分析...</div>';
        try {
            const data = await requestJson(`/api/batch-research/jobs/${encodeURIComponent(jobId)}/analysis`);
            const overview = data.overview || {};
            const breadth = data.breadth || {};
            const market = data.market || {};
            const signals = data.signal_distribution || {};
            const signalRows = Object.entries(signals)
                .filter(([, count]) => Number(count || 0) > 0)
                .map(([signal, count]) => `<tr>
                    <td><span class="report-signal signal-${escapeHtml(signal.toLowerCase().replace(/_/g, '-'))}">${escapeHtml(SIG_LABEL[signal] || signal)}</span></td>
                    <td>${Number(count || 0)}</td>
                    <td>${overview.total ? (Number(count || 0) / Number(overview.total || 1) * 100).toFixed(1) : '0.0'}%</td>
                </tr>`).join('');
            const industryRows = Object.entries(data.industry_groups || {})
                .sort((a, b) => Number(b[1].count || 0) - Number(a[1].count || 0))
                .map(([name, group]) => `<tr>
                    <td><strong>${escapeHtml(name)}</strong></td>
                    <td>${Number(group.count || 0)}</td>
                    <td>${Number(group.positive_signals || 0)} / ${Number(group.negative_signals || 0)}</td>
                    <td>${formatPct(group.avg_confidence)}</td>
                    <td>${Number(group.avg_risk || 0).toFixed(1)}</td>
                    <td>${formatChange(group.avg_change_pct)}</td>
                </tr>`).join('');
            const indexRows = Object.values(market.indices || {})
                .filter(item => item && typeof item === 'object' && !item.error)
                .map(item => `<tr>
                    <td>${escapeHtml(item.name || item.code || '--')}</td>
                    <td>${formatChange(item.change_pct)}</td>
                </tr>`).join('');
            const positiveRows = (data.top_positive || []).map(item => `<tr>
                <td><strong>${escapeHtml(item.name || item.code)}</strong><span>${escapeHtml(item.code)}</span></td>
                <td><span class="report-signal signal-${escapeHtml((item.signal || 'HOLD').toLowerCase().replace(/_/g, '-'))}">${escapeHtml(SIG_LABEL[item.signal] || item.signal)}</span></td>
                <td>${formatPct(item.confidence)}</td>
                <td>${formatScore(item.risk_score)}</td>
                <td>${formatChange(item.change_pct)}</td>
            </tr>`).join('');
            const riskRows = (data.top_risk || []).map(item => `<tr>
                <td><strong>${escapeHtml(item.name || item.code)}</strong><span>${escapeHtml(item.code)}</span></td>
                <td><span class="report-signal signal-${escapeHtml((item.signal || 'HOLD').toLowerCase().replace(/_/g, '-'))}">${escapeHtml(SIG_LABEL[item.signal] || item.signal)}</span></td>
                <td>${formatScore(item.risk_score)}</td>
                <td>${formatChange(item.change_pct)}</td>
            </tr>`).join('');
            if (body) body.innerHTML = `
                <div class="preview-signal">
                    <span>批量分析</span>
                    <span>${escapeHtml(formatTime(data.generated_at))}</span>
                </div>
                <div class="batch-analysis-grid">
                    ${metricCard('完成报告', `${Number(overview.completed || 0)} / ${Number(overview.total || 0)}`, `失败 ${Number(overview.failed || 0)} / 待数据 ${Number(overview.waiting || 0)}`)}
                    ${metricCard('平均置信度', formatPct(overview.avg_confidence))}
                    ${metricCard('平均风险', Number(overview.avg_risk || 0).toFixed(1))}
                    ${metricCard('上涨 / 下跌', `${Number(breadth.up || 0)} / ${Number(breadth.down || 0)}`, `上涨 ${Number(breadth.up_ratio || 0).toFixed(1)}%`)}
                    ${metricCard('大盘均值', formatChange(market.avg_change_pct), `上涨指数 ${Number(market.positive_indices || 0)} / ${Number(market.indices_count || 0)}`)}
                </div>
                <h4>观察结论</h4>
                <div class="preview-block"><ul class="batch-analysis-list">${(data.observations || []).map(item => `<li>${escapeHtml(item)}</li>`).join('') || '<li>暂无结论</li>'}</ul></div>
                <h4>信号分布</h4>
                <div class="preview-block batch-step-preview"><table class="report-library-table"><thead><tr><th>信号</th><th>数量</th><th>占比</th></tr></thead><tbody>${signalRows || '<tr><td colspan="3">暂无信号</td></tr>'}</tbody></table></div>
                <h4>行业 / 分组情况</h4>
                <div class="preview-block batch-step-preview"><table class="report-library-table"><thead><tr><th>分组</th><th>数量</th><th>正/负信号</th><th>置信度</th><th>风险</th><th>涨跌</th></tr></thead><tbody>${industryRows || '<tr><td colspan="6">暂无分组</td></tr>'}</tbody></table></div>
                <h4>大盘情况</h4>
                <div class="preview-block batch-step-preview"><table class="report-library-table"><thead><tr><th>指数</th><th>涨跌幅</th></tr></thead><tbody>${indexRows || '<tr><td colspan="2">暂无大盘数据</td></tr>'}</tbody></table></div>
                <h4>正向信号优先观察</h4>
                <div class="preview-block batch-step-preview"><table class="report-library-table"><thead><tr><th>股票</th><th>信号</th><th>置信度</th><th>风险</th><th>涨跌</th></tr></thead><tbody>${positiveRows || '<tr><td colspan="5">暂无正向信号</td></tr>'}</tbody></table></div>
                <h4>高风险样本</h4>
                <div class="preview-block batch-step-preview"><table class="report-library-table"><thead><tr><th>股票</th><th>信号</th><th>风险</th><th>涨跌</th></tr></thead><tbody>${riskRows || '<tr><td colspan="4">暂无风险数据</td></tr>'}</tbody></table></div>
            `;
        } catch (err) {
            if (body) body.innerHTML = `<div class="library-empty-state">加载失败：${escapeHtml(err.message)}</div>`;
        }
    }

    async function pauseBatchJob(jobId) {
        await requestJson(`/api/batch-research/jobs/${encodeURIComponent(jobId)}/pause`, { method: 'POST' });
        jobsLoaded = false;
        await loadBatchJobs();
        await previewBatchJob(encodeURIComponent(jobId));
    }

    async function resumeBatchJob(jobId) {
        await requestJson(`/api/batch-research/jobs/${encodeURIComponent(jobId)}/resume`, { method: 'POST' });
        jobsLoaded = false;
        await loadBatchJobs();
    }

    async function retryBatchJob(jobId, errorType = '') {
        await requestJson(`/api/batch-research/jobs/${encodeURIComponent(jobId)}/retry-failed`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(errorType ? { error_type: errorType } : {})
        });
        jobsLoaded = false;
        await loadBatchJobs();
    }

    async function previewFailureGroups(jobId) {
        const body = document.getElementById('reportPreview');
        if (body) body.innerHTML = '<div class="library-empty-state">加载失败分组...</div>';
        try {
            const data = await requestJson(`/api/batch-research/jobs/${encodeURIComponent(jobId)}/failure-groups`);
            const rows = (data.groups || []).map(group => `<tr>
                <td><strong>${escapeHtml(group.label || group.error_type)}</strong><span>${escapeHtml(group.error_type)}</span></td>
                <td>${Number(group.count || 0)}</td>
                <td>${(group.items || []).slice(0, 5).map(item => `${escapeHtml(item.name || item.code)} ${escapeHtml(item.code)}`).join('<br>') || '--'}</td>
                <td><button class="btn btn-sm" onclick="retryBatchJob('${escapeAttr(jobId)}','${escapeAttr(group.error_type)}')">只重试此类</button></td>
            </tr>`).join('');
            if (body) body.innerHTML = `
                <div class="preview-signal"><span>失败分组 ${Number((data.groups || []).length)}</span></div>
                <div class="preview-block batch-step-preview">
                    <table class="report-library-table">
                        <thead><tr><th>类型</th><th>数量</th><th>样例</th><th>操作</th></tr></thead>
                        <tbody>${rows || '<tr><td colspan="4">暂无失败项</td></tr>'}</tbody>
                    </table>
                </div>
            `;
        } catch (err) {
            if (body) body.innerHTML = `<div class="library-empty-state">加载失败：${escapeHtml(err.message)}</div>`;
        }
    }

    async function previewRuntimeStats(jobId) {
        const body = document.getElementById('reportPreview');
        if (body) body.innerHTML = '<div class="library-empty-state">加载耗时统计...</div>';
        try {
            const data = await requestJson(`/api/batch-research/jobs/${encodeURIComponent(jobId)}/runtime-stats`);
            const slowRows = (data.slowest_items || []).map(item => `<tr>
                <td>${escapeHtml(item.name || item.code)}<span>${escapeHtml(item.code)}</span></td>
                <td>${escapeHtml(statusLabel(item.status))}</td>
                <td>${(Number(item.duration_ms || 0) / 1000).toFixed(1)}s</td>
            </tr>`).join('');
            const roleRows = (data.roles || []).map(item => `<tr>
                <td>${escapeHtml(item.role_name || item.role_key)}<span>${escapeHtml(item.role_key)}</span></td>
                <td>${Number(item.count || 0)}</td>
                <td>${Number(item.failed || 0)}</td>
                <td>${(Number(item.duration_ms || 0) / 1000).toFixed(1)}s</td>
            </tr>`).join('');
            const modelRows = (data.models || []).map(item => `<tr>
                <td>${escapeHtml(item.model)}</td>
                <td>${Number(item.count || 0)}</td>
                <td>${Number(item.failed || 0)}</td>
                <td>${(Number(item.duration_ms || 0) / 1000).toFixed(1)}s</td>
            </tr>`).join('');
            if (body) body.innerHTML = `
                <div class="preview-signal">
                    <span>最慢股票 ${Number((data.slowest_items || []).length)}</span>
                    <span>Fallback ${Number((data.fallback_events || []).length)}</span>
                </div>
                <h4>最慢股票</h4>
                <div class="preview-block batch-step-preview"><table class="report-library-table"><tbody>${slowRows || '<tr><td>暂无耗时数据</td></tr>'}</tbody></table></div>
                <h4>角色耗时</h4>
                <div class="preview-block batch-step-preview"><table class="report-library-table"><thead><tr><th>角色</th><th>次数</th><th>失败</th><th>总耗时</th></tr></thead><tbody>${roleRows || '<tr><td colspan="4">暂无角色统计</td></tr>'}</tbody></table></div>
                <h4>模型耗时</h4>
                <div class="preview-block batch-step-preview"><table class="report-library-table"><thead><tr><th>模型</th><th>次数</th><th>失败</th><th>总耗时</th></tr></thead><tbody>${modelRows || '<tr><td colspan="4">暂无模型统计</td></tr>'}</tbody></table></div>
            `;
        } catch (err) {
            if (body) body.innerHTML = `<div class="library-empty-state">加载失败：${escapeHtml(err.message)}</div>`;
        }
    }

    async function previewBatchLogs(jobId) {
        const body = document.getElementById('reportPreview');
        if (body) body.innerHTML = '<div class="library-empty-state">加载运行日志...</div>';
        try {
            const data = await requestJson(`/api/batch-research/jobs/${encodeURIComponent(jobId)}/logs?limit=300`);
            const rows = (data.logs || []).map(log => `<tr>
                <td>${escapeHtml(formatTime(log.created_at))}</td>
                <td>${escapeHtml(log.level)}</td>
                <td>${escapeHtml(log.event)}</td>
                <td>${escapeHtml(log.message || '--')}</td>
            </tr>`).join('');
            if (body) body.innerHTML = `
                <div class="preview-signal"><span>运行日志 ${Number(data.count || 0)}</span></div>
                <div class="preview-block batch-step-preview">
                    <table class="report-library-table">
                        <thead><tr><th>时间</th><th>级别</th><th>事件</th><th>信息</th></tr></thead>
                        <tbody>${rows || '<tr><td colspan="4">暂无日志</td></tr>'}</tbody>
                    </table>
                </div>
            `;
        } catch (err) {
            if (body) body.innerHTML = `<div class="library-empty-state">加载失败：${escapeHtml(err.message)}</div>`;
        }
    }

    async function previewBatchArtifacts(jobId) {
        const body = document.getElementById('reportPreview');
        if (body) body.innerHTML = '<div class="library-empty-state">加载批次产物...</div>';
        try {
            const data = await requestJson(`/api/batch-research/jobs/${encodeURIComponent(jobId)}/artifacts`);
            const rows = (data.artifacts || []).map(item => `<tr>
                <td><strong>${escapeHtml(item.title || item.artifact_type)}</strong><span>${escapeHtml(item.artifact_type)}</span></td>
                <td>${escapeHtml(item.path || '--')}</td>
                <td>${escapeHtml(formatTime(item.created_at))}</td>
            </tr>`).join('');
            if (body) body.innerHTML = `
                <div class="preview-signal"><span>批次产物 ${Number(data.count || 0)}</span></div>
                <div class="preview-block batch-step-preview">
                    <table class="report-library-table">
                        <thead><tr><th>产物</th><th>路径</th><th>时间</th></tr></thead>
                        <tbody>${rows || '<tr><td colspan="3">暂无产物</td></tr>'}</tbody>
                    </table>
                </div>
            `;
        } catch (err) {
            if (body) body.innerHTML = `<div class="library-empty-state">加载失败：${escapeHtml(err.message)}</div>`;
        }
    }

    async function cancelBatchJob(jobId) {
        if (!confirm('确认取消这个批量任务？已完成的项目会保留。')) return;
        await requestJson(`/api/batch-research/jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' });
        jobsLoaded = false;
        await loadBatchJobs();
    }

    function exportReportLibrary(type) {
        const items = filtered.filter(report => selected.size === 0 || selected.has(Number(report.id)));
        if (!items.length) return alert('没有可导出的报告');
        const content = type === 'json'
            ? JSON.stringify(items, null, 2)
            : ['# AI报告库导出', '', ...items.map(report => `- ${report.name || report.code} ${report.code}: ${SIG_LABEL[report.signal] || report.signal || 'HOLD'}，置信度 ${formatPct(report.confidence)}，风险 ${formatScore(report.risk_score)}`)].join('\n');
        const blob = new Blob([content], { type: type === 'json' ? 'application/json' : 'text/markdown' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `ai-reports.${type === 'json' ? 'json' : 'md'}`;
        a.click();
        URL.revokeObjectURL(url);
    }

    window.loadReportLibrary = loadReportLibrary;
    window.filterReportLibrary = filterReportLibrary;
    window.toggleSignalFilter = toggleSignalFilter;
    window.setReportMarketFilter = setReportMarketFilter;
    window.toggleReportGroup = toggleReportGroup;
    window.switchReportTab = switchReportTab;
    window.previewReport = previewReport;
    window.previewSnapshot = previewSnapshot;
    window.previewPositionPlan = previewPositionPlan;
    window.archivePositionPlan = archivePositionPlan;
    window.adoptPositionPlan = adoptPositionPlan;
    window.toggleReportSelection = toggleReportSelection;
    window.toggleAllReports = toggleAllReports;
    window.createPlanFromReportLibrary = createPlanFromReportLibrary;
    window.closePositionPlanModal = closePositionPlanModal;
    window.submitPositionPlanFromModal = submitPositionPlanFromModal;
    window.togglePlanRoleModelFields = togglePlanRoleModelFields;
    window.exportReportLibrary = exportReportLibrary;
    window.resumeBatchJob = resumeBatchJob;
    window.pauseBatchJob = pauseBatchJob;
    window.retryBatchJob = retryBatchJob;
    window.cancelBatchJob = cancelBatchJob;
    window.previewBatchJob = previewBatchJob;
    window.previewBatchItemSteps = previewBatchItemSteps;
    window.previewBatchAnalysis = previewBatchAnalysis;
    window.previewFailureGroups = previewFailureGroups;
    window.previewRuntimeStats = previewRuntimeStats;
    window.previewBatchLogs = previewBatchLogs;
    window.previewBatchArtifacts = previewBatchArtifacts;
})();
