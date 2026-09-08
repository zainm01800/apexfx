export const PROFILES = Object.freeze({
  v6: { name: 'Book V6', daily: .03, maximum: .06, trade: .0075, aggregate: .015, gross: 1.5, nameCap: .5 },
  v10: { name: 'Book V10', daily: .05, maximum: .10, trade: .0085, aggregate: .0255, gross: 2, nameCap: .75 },
  v24: { name: 'Book V24', label: 'SPY Noise-Band Momentum', daily: .05, maximum: .12, original_maximum: .10, trade: .01, vol_target: .02, gross: 4.0, nameCap: 4.0, profile: 'higher_5_12_static' },
  v30: { name: 'Book V30', label: 'SPY ATR Breakout', daily: .05, maximum: .12, original_maximum: .10, trade: .01, vol_target: .02, gross: 4.0, nameCap: 4.0, profile: 'higher_5_12_static' },
  v27b: { name: 'Book V27B', label: 'Joint trend / reversal', daily: .05, maximum: .12, original_maximum: .10, trade: .01, aggregate: .03375, gross: 2, nameCap: .75, profile: 'higher_5_12_joint' },
  v33: { name: 'Book V33', label: 'Cost-aware trend / reversal', daily: .05, maximum: .10, original_maximum: .10, trade: .01, aggregate: .03375, gross: 2, nameCap: .75, profile: 'higher_5_10_cost_aware_joint' },
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
  if (!value) return '—';
  let s = String(value).trim();
  if (/^\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}/.test(s)) s = s.replace(' ', 'T') + (s.endsWith('Z') || s.includes('+') ? '' : 'Z');
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return '—';
  return new Intl.DateTimeFormat('en-GB', { timeZone:'Europe/London', day:'2-digit', month:'short', year:'numeric', ...(withTime ? {hour:'2-digit', minute:'2-digit', timeZoneName:'short'} : {}) }).format(d);
}
export function summarize(payload, book) {
  const rows = value => Array.isArray(value) && value.every(row => row !== null && typeof row === 'object' && !Array.isArray(row));
  if (!payload || payload.book_id !== book || !['daily','positions','trades','pending'].every(key => rows(payload[key]))) throw new Error('The paper ledger has an invalid identity or shape.');
  const state = payload.state || {}, meta = payload.metadata || {};
  const expected = PROFILES[book]?.profile || (book === 'v6' ? 'strict_3_6_static' : 'standard_5_10_static');
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
  const completedIds = new Set(payload.trades.filter(t => t.lot_fully_closed === true).map(t => t.lot_id));
  const lotProfits = [...completedIds].map(id => payload.trades.filter(t => t.lot_id === id).reduce((sum,t) => sum + Number(t.net_pnl_gbp),0));
  const jointBook = ['v27b','v33'].includes(book);
  const wins = jointBook ? lotProfits : tradePnl;
  return {payload, state, meta, daily, latest, equity, cash, closedPnl, openPnl, dailyFloor, maxFloor, maxDD,
    pnl: equity - 100000, dayPnl: firstNumber(latest.day_pnl_gbp, latest.day_pnl, state.day_pnl_gbp),
    winRate: wins.length && wins.every(n => n !== null && Number.isFinite(n)) ? wins.filter(n => n > 0).length / wins.length : null,
    completedLots: jointBook ? lotProfits.length : payload.trades.length,
    activation: meta.activation_recorded_at_utc || meta.activated_at_utc || meta.activation_time_utc || state.activated_at_utc || state.created_at_utc,
    // A seed date proves activation, not successful market-data retrieval.
    through: meta.last_processed_session || state.last_processed_session || state.last_processed_date ||
      meta.last_input_session || state.last_input_session || meta.last_data_as_of ||
      daily.filter(d => !d.is_seed && d.kind !== 'seed').at(-1)?.date || null,
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
  const exitLabel = exitDate ? dateLabel(exitDate) : t.management_rule || ((t.instrument === 'SPY' && !exitDate) ? 'Intraday · 15:59 NY flat' : 'After 5 completed sessions');
  const stopPolicy = t.stop_atr_multiple ? `${t.stop_atr_multiple} × prior ATR20 at fill` : t.instrument === 'SPY' ? 'Barrier at fill' : '1.5 × prior ATR20 at fill';
  const management = t.management_rule ? `${t.management_rule}. ${t.partials_policy || ''}.` : 'Fixed protective stop, scheduled time exit and account-risk guards.';
  const unconfirmed = kind === 'pending' && t.decision_durability === 'unconfirmed_not_executable';
  const field = (label,value) => `<div><dt>${e(label)}</dt><dd>${e(value)}</dd></div>`;
  return `<article class="ws-trade"><div class="ws-trade-head"><div><span class="ws-symbol">${e(symbol)}</span><span class="ws-direction ${short ? 'short' : ''}">${side}</span><div class="ws-meta">${kind === 'pending' ? (unconfirmed ? 'Unconfirmed · cannot execute' : 'Pending · next eligible open') : kind === 'trades' ? 'Closed ' + dateLabel(t.exit_time || t.exit_date, true) : 'Entered ' + dateLabel(t.entry_time || t.entry_date || t.entry_session, true)}</div></div><div class="ws-trade-pnl ${signClass(pnl)}">${kind === 'pending' ? (unconfirmed ? 'Not executable' : 'Queued') : money(pnl,true)}</div></div>
    <dl class="ws-trade-grid">
    ${field('Entry · USD',kind === 'pending' ? (unconfirmed ? 'Requires durable pre-open confirmation' : 'Next-open simulation') : price(entry))}
    ${field(kind === 'pending' ? 'Eligible open session' : kind === 'trades' ? 'Exit · USD' : 'Official mark · USD',kind === 'pending' ? dateLabel(t.eligible_fill_session, true) : price(kind === 'trades' ? firstNumber(t.exit_price,t.exit_price_usd) : last))}
    ${field('Entered at',dateLabel(t.entry_time || t.entry_date || t.entry_session, true))}
    ${kind === 'trades' ? field('Exited at',dateLabel(t.exit_time || t.exit_date, true)) : field('Holding',t.holding_hours != null ? Number(t.holding_hours).toFixed(1)+' hrs' : t.sessions_held ? t.sessions_held+' sessions' : 'Active')}
    ${field('Stop loss · USD',stop === null && kind === 'pending' ? stopPolicy : price(stop))}
    ${field('Units',units)}
    ${field('Time exit',exitLabel)}
    ${field(kind === 'trades' ? 'Exit reason' : 'Current stop risk · GBP',kind === 'trades' ? String(t.exit_reason || 'Not supplied').replaceAll('_',' ') : money(firstNumber(t.current_risk_gbp,t.stop_risk_gbp)))}
    ${kind === 'trades' && (t.holding_hours != null || t.sessions_held != null) ? field('Duration',t.holding_hours != null ? Number(t.holding_hours).toFixed(1)+' hrs' : t.sessions_held+' sessions') : ''}
    </dl>
    <details><summary>Decision, risk &amp; management details</summary><p>${e(reason)}</p><dl class="ws-trade-grid">${field('Decision session',dateLabel(t.decision_date || t.decision_session || t.entry_time, true))}${field('Decision recorded at',t.decision_recorded_at_utc ? dateLabel(t.decision_recorded_at_utc,true) : 'Not supplied')}${field(t.sleeve ? 'Strategy sleeve' : 'Prior VIX',t.sleeve || firstNumber(t.lagged_vix,t.vix,evidence.vix) || '—')}${field('ATR20 · USD',price(firstNumber(t.decision_atr,t.atr14,t.atr20,t.atr,evidence.atr20)) ?? '—')}${field('Initial risk · GBP',money(firstNumber(t.initial_total_risk,t.initial_total_risk_gbp,t.initial_risk_gbp,t.entry_risk_gbp)))}${field('Take-profit / partials',t.partials_policy || 'Not used by this strategy')}${field('Entry timestamp',dateLabel(t.entry_time || t.entry_date, true))}${field('Exit timestamp',dateLabel(t.exit_time || t.exit_date, true))}${field('Entry fee · GBP',money(firstNumber(t.entry_fee,t.entry_fee_gbp,t.entry_fee_remaining_gbp)))}${field('Borrow charged · GBP',money(firstNumber(t.borrow_cost,t.borrow_cost_gbp)))}</dl><p>${e(management)} A stop is not a guaranteed fill price; gaps and modelled slippage can increase a loss.</p></details></article>`;
}
