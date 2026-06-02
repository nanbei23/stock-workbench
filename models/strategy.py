"""策略引擎 — 复现 HTML v1.1 网格补仓逻辑"""
import math
import sqlite3
from config import DB_PATH
from models.portfolio import get_position_summary


def _get_params(code6):
    """读取策略参数，返回 dict；未配置则返回 None"""
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    try:
        row = db.execute('SELECT * FROM strategy_params WHERE code6 = ?', (code6,)).fetchone()
        return dict(row) if row else None
    finally:
        db.close()


def _fee(price, shares, is_sell=False):
    """
    计算交易费用
    佣金 0.03% 最低 5 元，印花税 0.05% 仅卖出，过户费 0.001%
    """
    amount = price * shares
    commission = max(amount * 0.0003, 5)
    stamp = amount * 0.0005 if is_sell else 0
    transfer = amount * 0.00001
    return round(commission + stamp + transfer, 3)


def _floor_to_lot(value, lot):
    lot = float(lot or 0.001)
    if lot <= 0:
        lot = 0.001
    return round(math.floor(float(value or 0) / lot) * lot, 3)


def calc_plan_table(code6):
    """
    生成理论补仓计划表。
    逻辑：首次以 entry_price 买入 budget 对应手数；
    之后每次跌 drop_pct% 触发补仓，补仓量 = 累计持股 × add_mult（取整到 lot_size），
    直到预算耗尽。然后生成反弹卖出计划。
    返回 list[dict]: stage, trigger_price, action, shares, amount,
                     cumulative_shares, cumulative_cost, avg_price
    """
    p = _get_params(code6)
    if not p:
        return []

    budget = p['budget']
    entry = p['entry_price']
    drop_pct = p['drop_pct']
    add_mult = p['add_mult']
    bounce_pct = p['bounce_pct']
    sell_pct = p['sell_pct']
    lot = p['lot_size']
    low_water = p['low_water_manual']

    if budget <= 0 or entry <= 0:
        return []

    rows = []
    cum_shares = 0
    cum_cost = 0.0
    stage = 0

    # --- 买入阶段 ---
    # 首次建仓：用全部预算买 entry 价格
    first_shares = _floor_to_lot(budget / entry, lot)
    if first_shares <= 0:
        return []

    first_cost = first_shares * entry + _fee(entry, first_shares)
    cum_shares = first_shares
    cum_cost = first_cost
    rows.append({
        'stage': stage,
        'trigger_price': round(entry, 3),
        'action': 'buy',
        'shares': first_shares,
        'amount': round(first_cost, 3),
        'cumulative_shares': cum_shares,
        'cumulative_cost': round(cum_cost, 3),
        'avg_price': round(cum_cost / cum_shares, 4),
    })

    spent = first_cost
    ref_price = entry
    stage = 1
    max_stages = 30  # 安全上限

    while stage < max_stages:
        trigger = ref_price * (1 - drop_pct / 100)
        trigger = round(trigger, 3)
        buy_shares = max(_floor_to_lot(cum_shares * add_mult, lot), lot)
        buy_amount = buy_shares * trigger + _fee(trigger, buy_shares)

        if spent + buy_amount > budget * 1.01:  # 允许 1% 误差
            # 预算不足，用剩余买
            remaining = budget - spent
            buy_shares = _floor_to_lot(remaining / trigger, lot)
            if buy_shares <= 0:
                break
            buy_amount = buy_shares * trigger + _fee(trigger, buy_shares)

        cum_shares += buy_shares
        cum_cost += buy_amount
        spent += buy_amount

        rows.append({
            'stage': stage,
            'trigger_price': trigger,
            'action': 'buy',
            'shares': buy_shares,
            'amount': round(buy_amount, 3),
            'cumulative_shares': cum_shares,
            'cumulative_cost': round(cum_cost, 3),
            'avg_price': round(cum_cost / cum_shares, 4),
        })

        ref_price = trigger
        stage += 1

        if spent >= budget * 0.99:
            break

    # --- 卖出阶段 ---
    if cum_shares > 0:
        avg = cum_cost / cum_shares
        sell_trigger = avg * (1 + bounce_pct / 100)
        sell_trigger = round(sell_trigger, 3)
        sell_shares = _floor_to_lot(cum_shares * sell_pct / 100, lot)
        if sell_shares <= 0:
            sell_shares = lot
        sell_shares = min(sell_shares, cum_shares)
        sell_amount = sell_shares * sell_trigger - _fee(sell_trigger, sell_shares, is_sell=True)

        rows.append({
            'stage': stage,
            'trigger_price': sell_trigger,
            'action': 'sell',
            'shares': sell_shares,
            'amount': round(sell_amount, 3),
            'cumulative_shares': cum_shares - sell_shares,
            'cumulative_cost': round(cum_cost - avg * sell_shares, 3),
            'avg_price': round(avg, 4),
        })

    return rows


def calc_next_triggers(code6):
    """
    根据实际持仓，计算下一个买入/卖出触发价和数量。
    """
    p = _get_params(code6)
    if not p:
        return {}

    pos = get_position_summary(code6)
    shares = pos['shares']
    avg_price = pos['avg_price']
    cost_basis = pos['cost_basis']
    drop_pct = p['drop_pct']
    add_mult = p['add_mult']
    bounce_pct = p['bounce_pct']
    sell_pct = p['sell_pct']
    lot = p['lot_size']
    budget = p['budget']
    entry = p['entry_price']

    result = {
        'next_buy_price': None, 'next_buy_shares': 0, 'next_buy_amount': 0,
        'next_sell_price': None, 'next_sell_shares': 0,
    }

    # 未建仓 → 首次买入
    if shares <= 0:
        first_shares = _floor_to_lot(budget / entry, lot) if budget > 0 and entry > 0 else 0
        result['next_buy_price'] = round(entry, 3) if entry > 0 else None
        result['next_buy_shares'] = first_shares
        result['next_buy_amount'] = round(first_shares * entry, 3) if first_shares > 0 else 0
        return result

    # 下一个买入触发价：用 last_buy_price 或 entry 作参考
    ref = pos['last_buy_price'] or entry
    next_buy_price = round(ref * (1 - drop_pct / 100), 3)
    next_buy_shares = max(_floor_to_lot(shares * add_mult, lot), lot)
    spent = cost_basis
    buy_amount = next_buy_shares * next_buy_price
    if spent + buy_amount > budget * 1.01:
        remaining = budget - spent
        next_buy_shares = _floor_to_lot(remaining / next_buy_price, lot)

    if next_buy_shares > 0:
        result['next_buy_price'] = next_buy_price
        result['next_buy_shares'] = next_buy_shares
        result['next_buy_amount'] = round(next_buy_shares * next_buy_price, 3)

    # 卖出触发价
    next_sell_price = round(avg_price * (1 + bounce_pct / 100), 3) if avg_price > 0 else None
    next_sell_shares = max(_floor_to_lot(shares * sell_pct / 100, lot), lot)
    next_sell_shares = min(next_sell_shares, shares)

    result['next_sell_price'] = next_sell_price
    result['next_sell_shares'] = next_sell_shares

    return result


def calc_pnl(code6, current_price):
    """
    计算盈亏：market_value, float_pnl, net_sell_value,
              net_return_pct, target_price, rebound_pct
    """
    p = _get_params(code6)
    pos = get_position_summary(code6)
    shares = pos['shares']
    cost = pos['cost_basis']
    avg = pos['avg_price']

    market_value = round(current_price * shares, 3)
    float_pnl = round(market_value - cost, 3)
    net_sell_fee = _fee(current_price, shares, is_sell=True)
    net_sell_value = round(market_value - net_sell_fee, 3)
    net_return_pct = round((net_sell_value - cost) / cost * 100, 3) if cost > 0 else 0

    target_price = None
    rebound_pct = None
    if p and p.get('target_profit_pct') and avg > 0:
        target_price = round(avg * (1 + p['target_profit_pct'] / 100), 3)
    if p and p.get('low_water_manual') and p['low_water_manual'] > 0:
        rebound_pct = round((current_price - p['low_water_manual']) / p['low_water_manual'] * 100, 3)
    elif shares > 0 and avg > 0:
        rebound_pct = round((current_price - avg) / avg * 100, 3)

    return {
        'market_value': market_value,
        'float_pnl': float_pnl,
        'net_sell_value': net_sell_value,
        'net_return_pct': net_return_pct,
        'target_price': target_price,
        'rebound_pct': rebound_pct,
    }


def get_strategy_state(current_price, next_buy, next_sell):
    """
    根据当前价与下一触发价，返回策略状态：
    watch / near_buy / buy / near_sell / sell
    """
    if current_price is None:
        return 'watch'

    nb = next_buy.get('price') if isinstance(next_buy, dict) else next_buy
    ns = next_sell.get('price') if isinstance(next_sell, dict) else next_sell

    # 买入触发
    if nb is not None and current_price <= nb:
        return 'buy'
    # 买入附近（2% 以内）
    if nb is not None and current_price <= nb * 1.02:
        return 'near_buy'
    # 卖出触发
    if ns is not None and current_price >= ns:
        return 'sell'
    # 卖出附近（2% 以内）
    if ns is not None and current_price >= ns * 0.98:
        return 'near_sell'

    return 'watch'
