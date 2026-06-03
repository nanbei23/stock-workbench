(function () {
    'use strict';

    const MARKET_OPTIONS = [
        { key: 'tradable', label: '可交易' },
        { key: 'all', label: '全部' },
        { key: 'main', label: '主板' },
        { key: 'gem', label: '创业板' },
        { key: 'star', label: '科创板' },
        { key: 'bse', label: '北交所' },
        { key: 'unknown', label: '未知' }
    ];
    const DEFAULT_SETTINGS = {
        trade_market_main: 'true',
        trade_market_gem: 'true',
        trade_market_star: 'true',
        trade_market_bse: 'true'
    };
    let cachedSettings = { ...DEFAULT_SETTINGS };

    function normalizeCode(code) {
        const raw = String(code || '').replace(/\D/g, '');
        return raw.length >= 6 ? raw.slice(-6) : raw;
    }

    function classify(code) {
        const code6 = normalizeCode(code);
        if (/^(688|689)/.test(code6)) return { key: 'star', label: '科创板' };
        if (/^(300|301)/.test(code6)) return { key: 'gem', label: '创业板' };
        if (/^(8|4|920)/.test(code6)) return { key: 'bse', label: '北交所' };
        if (/^(600|601|603|605|000|001|002|003)/.test(code6)) return { key: 'main', label: '主板' };
        return { key: 'unknown', label: '未知' };
    }

    function allowedKeys(settings = cachedSettings) {
        const keys = ['unknown'];
        ['main', 'gem', 'star', 'bse'].forEach(key => {
            const value = settings[`trade_market_${key}`];
            if (value === true || String(value ?? 'true').toLowerCase() === 'true') keys.push(key);
        });
        return new Set(keys);
    }

    function isAllowed(code, settings = cachedSettings) {
        return allowedKeys(settings).has(classify(code).key);
    }

    function matchesFilter(code, filter = 'tradable', settings = cachedSettings) {
        const market = classify(code);
        if (!filter || filter === 'all') return true;
        if (filter === 'tradable') return allowedKeys(settings).has(market.key);
        return market.key === filter;
    }

    function filterStocks(items, filter = 'tradable', settings = cachedSettings) {
        return (items || []).filter(item => matchesFilter(item.code, filter, settings));
    }

    async function load() {
        try {
            const resp = await fetch('/api/settings');
            if (resp.ok) {
                cachedSettings = { ...DEFAULT_SETTINGS, ...(await resp.json()) };
            }
        } catch (_err) {
            cachedSettings = { ...DEFAULT_SETTINGS };
        }
        return cachedSettings;
    }

    window.StockMarketPermissions = {
        MARKET_OPTIONS,
        DEFAULT_SETTINGS,
        classify,
        allowedKeys,
        isAllowed,
        matchesFilter,
        filterStocks,
        load,
        settings: () => cachedSettings
    };
})();
