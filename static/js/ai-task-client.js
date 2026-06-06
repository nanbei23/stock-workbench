(function (global) {
    'use strict';

    async function requestJson(url, options) {
        if (global.API && typeof global.API.request === 'function') {
            return global.API.request(url, options || {});
        }

        const response = await fetch(url, options || {});
        const text = await response.text();
        const payload = text ? JSON.parse(text) : null;
        if (!response.ok) {
            const detail = payload?.detail || payload?.message || response.statusText;
            throw new Error(Array.isArray(detail) ? detail.map(item => item.msg || String(item)).join('; ') : String(detail));
        }
        return payload;
    }

    function get(url) {
        return requestJson(url);
    }

    function post(url, payload) {
        return requestJson(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload || {})
        });
    }

    function encode(value) {
        return encodeURIComponent(value);
    }

    global.AiTaskClient = {
        start(code, payload) {
            return post(`/api/ai/analyze/${encode(code)}`, payload || {});
        },
        batch(payload) {
            return post('/api/ai/batch-analyze', payload || {});
        },
        batchReport(payload) {
            return post('/api/batch-reports', payload || {});
        },
        createBatchResearch(payload) {
            return post('/api/batch-research/jobs', payload || {});
        },
        preflightBatchResearch(payload) {
            return post('/api/batch-research/preflight', payload || {});
        },
        batchResearchJobs(params) {
            const query = params ? `?${new URLSearchParams(params).toString()}` : '';
            return get(`/api/batch-research/jobs${query}`);
        },
        batchResearchJob(jobId) {
            return get(`/api/batch-research/jobs/${encodeURIComponent(jobId)}`);
        },
        resumeBatchResearch(jobId) {
            return post(`/api/batch-research/jobs/${encodeURIComponent(jobId)}/resume`);
        },
        retryBatchResearch(jobId) {
            return post(`/api/batch-research/jobs/${encodeURIComponent(jobId)}/retry-failed`);
        },
        cancelBatchResearch(jobId) {
            return post(`/api/batch-research/jobs/${encodeURIComponent(jobId)}/cancel`);
        },
        status(taskId) {
            return get(`/api/ai/analyze/${encode(taskId)}/status`);
        },
        result(taskId) {
            return get(`/api/ai/analyze/${encode(taskId)}/result`);
        },
        cancel(taskId) {
            return post(`/api/ai/analyze/${encode(taskId)}/cancel`);
        },
        resume(taskId) {
            return post(`/api/ai/analyze/${encode(taskId)}/resume`);
        },
        queueStatus() {
            return get('/api/ai/queue/status');
        },
        activeTask() {
            return get('/api/ai/active-task');
        },
        stream(taskId) {
            return new EventSource(`/api/ai/analyze/${encode(taskId)}/stream`);
        },
        saveToGbrain(payload) {
            return post('/api/ai/gbrain/save', payload || {});
        },
        tasks(params) {
            const query = params ? `?${new URLSearchParams(params).toString()}` : '';
            return get(`/api/ai/tasks${query}`);
        },
        retry(taskId) {
            return post(`/api/ai/tasks/${encode(taskId)}/retry`);
        },
        cancelFromCenter(taskId) {
            return post(`/api/ai/tasks/${encode(taskId)}/cancel`);
        }
    };
})(window);
