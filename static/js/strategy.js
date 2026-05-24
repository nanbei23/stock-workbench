/* 策略引擎前端 — Phase 3 实现 */

async function loadStrategy(code) {
  if (!code) return;
  try {
    const [state, params, pnl] = await Promise.all([
      API.get(`/api/strategy/${code}/state`),
      API.get(`/api/strategy/${code}/params`),
      API.get(`/api/strategy/${code}/pnl`)
    ]);

    // State badge
    const badge = document.getElementById('stateBadge');
    if (badge) {
      badge.textContent = state.state || '--';
      badge.className = 'state-badge ' + (state.state || '');
    }
    const desc = document.getElementById('stateDesc');
    if (desc) desc.textContent = state.description || '';

    // Key prices
    const triggers = state.triggers || {};
    setText2('kpBuy', formatPrice(triggers.next_buy_price));
    setText2('kpStop', formatPrice(triggers.stop_loss_price));
    setText2('kpProfit', formatPrice(triggers.target_sell_price));
    setText2('kpNextBuy', formatPrice(triggers.next_buy_price));
    setText2('kpNextSell', formatPrice(triggers.next_sell_price));

    // Plan table
    const plan = state.plan || [];
    const planBody = document.getElementById('planBody');
    if (planBody) {
      planBody.innerHTML = plan.map((p, i) => `
        <tr>
          <td>${i + 1}</td>
          <td>${formatPrice(p.trigger_price)}</td>
          <td>${p.shares}</td>
          <td class="${p.action === 'buy' ? 'price-up' : 'price-down'}">${p.action === 'buy' ? '买入' : '卖出'}</td>
          <td>${p.cumulative_shares}</td>
        </tr>
      `).join('');
    }

    // P&L estimate
    if (pnl) {
      setText2('pnlCost', formatMoney(pnl.total_cost));
      setText2('pnlTarget', formatMoney(pnl.target_profit));
      setText2('pnlCommission', formatMoney(pnl.commission));
      setText2('pnlStamp', formatMoney(pnl.stamp_tax));
    }

    // Params inputs
    if (params) {
      setVal('paramBudget', params.budget);
      setVal('paramEntry', params.entry_price);
      setVal('paramDropPct', params.drop_pct);
      setVal('paramAddMult', params.add_mult);
      setVal('paramBouncePct', params.bounce_pct);
      setVal('paramSellPct', params.sell_pct);
    }
  } catch (e) {
    console.error('loadStrategy error:', e);
  }
}

async function saveStrategyParams() {
  const code = currentCode;
  if (!code) return;
  const data = {
    budget: parseFloat(document.getElementById('paramBudget').value) || 0,
    entry_price: parseFloat(document.getElementById('paramEntry').value) || 0,
    drop_pct: parseFloat(document.getElementById('paramDropPct').value) || 0,
    add_mult: parseFloat(document.getElementById('paramAddMult').value) || 0,
    bounce_pct: parseFloat(document.getElementById('paramBouncePct').value) || 0,
    sell_pct: parseFloat(document.getElementById('paramSellPct').value) || 0,
  };
  await API.put(`/api/strategy/${code}/params`, data);
  loadStrategy(code);
}

/* helpers */
function setText2(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val ?? '--';
}

function setVal(id, val) {
  const el = document.getElementById(id);
  if (el) el.value = val || '';
}
