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
    const selected = new Set();

    document.addEventListener('DOMContentLoaded', loadReportLibrary);

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
        const signal = document.getElementById('reportFilterSignal')?.value || '';
        const sortBy = document.getElementById('reportSortBy')?.value || 'created_desc';
        const minConfidence = Number(document.getElementById('reportMinConfidence')?.value || 0);
        const maxRiskInput = document.getElementById('reportMaxRisk')?.value || '';
        const maxRisk = maxRiskInput === '' ? null : Number(maxRiskInput);
        filtered = reports.filter(report => {
            const haystack = `${report.code || ''} ${report.name || ''}`.toLowerCase();
            if (text && !haystack.includes(text)) return false;
            if (signal && report.signal !== signal) return false;
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
        tbody.innerHTML = filtered.map(report => {
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
        }).join('');
        updateSelectionSummary();
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
                <div class="preview-block">${formatMarkdown(typeof report.risk_debate === 'string' ? report.risk_debate : JSON.stringify(report.risk_debate || {}, null, 2))}</div>
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
        const raw = String(text || '');
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
        const reportIds = [...selected];
        const resp = await requestJson('/api/batch-research/jobs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ job_type: 'position_plan', report_ids: reportIds, multi_role: true, plan_top_n: 10 })
        });
        alert(`多角色建仓建议任务已创建：${resp.job_id}`);
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
    window.previewReport = previewReport;
    window.toggleReportSelection = toggleReportSelection;
    window.toggleAllReports = toggleAllReports;
    window.createPlanFromReportLibrary = createPlanFromReportLibrary;
    window.exportReportLibrary = exportReportLibrary;
})();
