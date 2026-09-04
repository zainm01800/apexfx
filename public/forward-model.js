// Pure presentation helpers. Never prices, sizes, or executes a trade.
export const PROFILES = Object.freeze({
  v6: { name: 'Book V6', daily: .03, maximum: .06, trade: .0075, aggregate: .015, gross: 1.5, nameCap: .5 },
  v10: { name: 'Book V10', daily: .05, maximum: .10, trade: .0085, aggregate: .0255, gross: 2, nameCap: .75 },
});
export const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
export function number(value) { return (typeof value === 'number' || (typeof value === 'string' && value.trim() !== '')) && Number.isFinite(Number(value)) ? Number(value) : null; }
export function firstNumber(...values) { return values.map(number).find(v => v !== null) ?? null; }
export const money = (value, signed = false, currency = 'GBP') => {
  const n = number(value);
  if (n === null) return '—';
  return (signed && n > 0 ? '+' : '') + new Intl.NumberFormat('en-GB', {style:'currency', currency, maximumFractionDigits:2}).format(n);
};
export const percent = value => number(value) === null ? '—' : `${(Number(value) * 100).toFixed(2)}%`;
export const signClass = value => number(value) === null || Number(value) === 0 ? '' : Number(value) > 0 ? 'ws-positive' : 'ws-negative';
export function dateLabel(value, withTime = false) {
  if (!value || Number.isNaN(new Date(value).getTime())) return '—';
  return new Intl.DateTimeFormat('en-GB', { timeZone:'Europe/London', day:'2-digit', month:'short', year:'numeric', ...(withTime ? {hour:'2-digit', minute:'2-digit', timeZoneName:'short'} : {}) }).format(new Date(value));
}
export function summarize(payload, book) {
  const rows = value => Array.isArray(value) && value.every(row => row !== null && typeof row === 'object' && !Array.isArray(row));
  if (!payload || payload.book_id !== book || !['daily','positions','trades','pending'].every(key => rows(payload[key]))) throw new Error('The paper ledger has an invalid identity or shape.');
  const state = payload.state || {}, meta = payload.metadata || {};
  const expected = book === 'v6' ? 'strict_3_6_static' : 'standard_5_10_static';
  if (!Object.hasOwn(PROFILES,book) || meta.book_id !== book || meta.account_currency !== 'GBP' || meta.profile !== expected || meta.paper_only !== true || meta.broker_enabled !== false || meta.initial_equity !== 100000) throw new Error('The saved ledger does not match the selected GBP paper profile.');
  const daily = [...payload.daily].sort((a,b) => String(a.date).localeCompare(String(b.date)));
  const latest = daily.at(-1) || {};
  const equity = firstNumber(latest.equity_gbp, latest.equity, state.equity_gbp, state.equity);
  const cash = firstNumber(latest.cash_gbp, latest.cash, state.cash_gbp, state.cash);
  if (equity === null || cash === null) throw new Error('The saved ledger is missing its GBP balance.');
  const tradePnl = payload.trades.map(t => firstNumber(t.net_pnl_gbp, t.pnl_gbp, t.pnl));
  const closedPnl = tradePnl.every(n => n !== null) ? tradePnl.reduce((a,b) => a+b,0) : null;
  const openPnl = firstNumber(latest.open_pnl_gbp, latest.open_pnl, state.unrealized_pnl_gbp, state.open_pnl_gbp, equity - cash);
  const floors = latest.floors || state.floors || {};
  const dailyFloor = firstNumber(latest.external_daily_floor, latest.external_daily, floors.external_daily, state.external_daily_floor);
  const maxFloor = firstNumber(latest.external_maximum_floor, latest.external_maximum, floors.external_maximum, state.external_maximum_floor);
  const drawdowns = daily.map(d => firstNumber(d.drawdown_from_peak, d.drawdown)).filter(n => n !== null);
  const maxDD = drawdowns.length ? Math.max(...drawdowns) : firstNumber(state.max_drawdown, state.max_drawdown_from_peak);
  return {payload, state, meta, daily, latest, equity, cash, closedPnl, openPnl, dailyFloor, maxFloor, maxDD,
    pnl: equity - 100000, dayPnl: firstNumber(latest.day_pnl_gbp, latest.day_pnl, state.day_pnl_gbp),
    winRate: tradePnl.length && tradePnl.every(n => n !== null) ? tradePnl.filter(n => n > 0).length / tradePnl.length : null,
    activation: meta.activation_recorded_at_utc || meta.activated_at_utc || meta.activation_time_utc || state.activated_at_utc || state.created_at_utc,
    through: meta.last_processed_session || state.last_processed_session || state.last_processed_date || latest.date,
    sessions: firstNumber(meta.session_count, latest.metrics?.session_count, state.forward_sessions, state.sessions_processed, meta.forward_sessions) ?? daily.filter(d => !d.is_seed && d.kind !== 'seed').length,
  };
}
export function tradeCard(t, kind = 'positions') {
  const e = escapeHtml;
  const symbol = t.instrument || t.symbol || 'Unknown symbol';
  const short = String(t.direction).toLowerCase() === 'short' || number(t.direction) === -1;
  const long = String(t.direction).toLowerCase() === 'long' || number(t.direction) === 1;
  const side = short ? 'SHORT' : long ? 'LONG' : 'UNKNOWN';
  const entry = firstNumber(t.entry_price, t.entry_price_usd);
  const stop = firstNumber(t.stop_price, t.stop, t.stop_price_usd, t.initial_stop);
  const last = firstNumber(t.last_px, t.last_price, t.mark_price, t.last_price_usd);
  const pnl = kind === 'trades' ? firstNumber(t.net_pnl_gbp, t.pnl_gbp, t.pnl) : firstNumber(t.unrealized_pnl_gbp, t.open_pnl_gbp, t.open_pnl, t.pnl_gbp);
  const price = n => n === null ? '—' : '$' + n.toFixed(2);
  const units = number(t.units) === null ? 'At fill' : Number(t.units).toLocaleString('en-GB',{maximumFractionDigits:4});
  const evidence = t.evidence || t.signal_evidence || {};
  const reason = t.signal_rationale || t.entry_reason || t.reason || evidence.reason || 'See the saved decision evidence; no rationale was supplied.';
  const exitDate = t.scheduled_exit_session || t.scheduled_exit_date || t.time_exit_session;
  const field = (label,value) => `<div><dt>${e(label)}</dt><dd>${e(value)}</dd></div>`;
  return `<article class="ws-trade"><div class="ws-trade-head"><div><span class="ws-symbol">${e(symbol)}</span><span class="ws-direction ${short ? 'short' : ''}">${side}</span><div class="ws-meta">${kind === 'pending' ? 'Pending · next eligible open' : kind === 'trades' ? 'Closed ' + dateLabel(t.exit_time || t.exit_date) : dateLabel(t.entry_time || t.entry_date || t.entry_session)}</div></div><div class="ws-trade-pnl ${signClass(pnl)}">${kind === 'pending' ? 'Queued' : money(pnl,true)}</div></div>
    <dl class="ws-trade-grid">${field('Entry · USD',kind === 'pending' ? 'Next-open simulation' : price(entry))}${field(kind === 'pending' ? 'Eligible open session' : kind === 'trades' ? 'Exit · USD' : 'Official mark · USD',kind === 'pending' ? dateLabel(t.eligible_fill_session) : price(kind === 'trades' ? firstNumber(t.exit_price,t.exit_price_usd) : last))}${field('Stop loss · USD',stop === null && kind === 'pending' ? '1.5 × prior ATR20 at fill' : price(stop))}${field('Units',units)}${field('Time exit',exitDate ? dateLabel(exitDate) : 'After 5 completed sessions')}${field(kind === 'trades' ? 'Exit reason' : 'Current stop risk · GBP',kind === 'trades' ? String(t.exit_reason || 'Not supplied').replaceAll('_',' ') : money(firstNumber(t.current_risk_gbp,t.stop_risk_gbp)))}</dl>
    <details><summary>Decision, risk &amp; management details</summary><p>${e(reason)}</p><dl class="ws-trade-grid">${field('Decision session',dateLabel(t.decision_date || t.decision_session))}${field('Recorded before fill',dateLabel(t.decision_recorded_at_utc,true))}${field('Prior VIX',firstNumber(t.lagged_vix,t.vix,evidence.vix) ?? '—')}${field('ATR20 · USD',price(firstNumber(t.decision_atr,t.atr20,t.atr,evidence.atr20)))}${field('Initial risk · GBP',money(firstNumber(t.initial_total_risk,t.initial_total_risk_gbp,t.initial_risk_gbp,t.entry_risk_gbp)))}${field('Take-profit / partials','Not used by this strategy')}${field('Entry date',dateLabel(t.entry_time || t.entry_date))}${field('Entry fee · GBP',money(firstNumber(t.entry_fee,t.entry_fee_gbp)))}${field('Borrow charged · GBP',money(firstNumber(t.borrow_cost,t.borrow_cost_gbp)))}</dl><p>Fixed protective stop, scheduled time exit and account-risk guards. A stop is not a guaranteed fill price; gaps and modelled slippage can increase a loss.</p></details></article>`;
}
