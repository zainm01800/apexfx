import {PROFILES, escapeHtml as e, firstNumber as n, number, money, percent, dateLabel} from './forward-model.js';

export const BOOKS = Object.freeze({
  ...Object.fromEntries(Object.entries(PROFILES).map(([id,p])=>[id,{...p,currency:'GBP',legacy:false}])),
  a:{name:'Book A',label:'252-day trend',currency:'GBP',legacy:true},
  b:{name:'Book B',label:'252-day + Spill50',currency:'GBP',legacy:true},
  c:{name:'Book C',label:'Multi-horizon trend',currency:'GBP',legacy:true},
  r:{name:'Book R',label:'Monthly ETF momentum',currency:'USD',legacy:true},
  s:{name:'Book S',label:'Session SMC',currency:'USD',legacy:true},
  f:{name:'Book F',label:'Prop shield',currency:'USD',legacy:true},
});
// Dated engineering findings, not inferred from a successful fetch or fresh timestamp.
// Revalidate the runner and accounting before clearing any of these warnings.
export const LEGACY_AUDIT = Object.freeze({
  a:{status:'Not forward-ready · accounting repair required',detail:'Mixed quote-currency P&L is summed without reliable GBP conversion. GBP-labelled balances, profit, drawdown and win rate are unverified.'},
  b:{status:'Not forward-ready · accounting repair required',detail:'Mixed quote-currency P&L is summed without reliable GBP conversion. GBP-labelled balances, profit, drawdown and win rate are unverified.'},
  c:{status:'Not forward-ready · accounting repair required',detail:'Mixed quote-currency P&L is summed without reliable GBP conversion. GBP-labelled balances, profit, drawdown and win rate are unverified.'},
  r:{status:'Forward operation unverified · stale runner output',detail:'The audited USD cash-plus-holdings snapshot reconciles, but the checked scheduled run made no progress beyond 2 September. Monthly rebalance uses no position stop or target; funded-loss compliance is not established.'},
  s:{status:'Not forward-ready · backfilled research history',detail:'This history was backfilled, not an untouched forward test. FX sizing and restart accounting defects invalidate its performance evidence. No Book S step is wired into the checked GitHub nightly workflow.'},
  f:{status:'Not forward-ready · profit-accounting defect',detail:'Added pyramid units use the original entry price instead of their actual added-lot price. Reported performance is unverified; the audited equity history stopped on 19 August despite newer position timestamps.'},
});
const rows = v=>Array.isArray(v)&&v.every(x=>x&&typeof x==='object'&&!Array.isArray(x));
export function summarizeLegacy(payload,book) {
  const p=BOOKS[book],meta=payload?.metadata;
  if(!p?.legacy||payload?.book_id!==book||meta?.book_id!==book||meta.account_currency!==p.currency||meta.paper_only!==true||meta.broker_enabled!==false||!['daily','positions','trades','pending'].every(k=>rows(payload[k])))throw Error('The saved ledger does not match this paper book.');
  const daily=[...payload.daily].sort((a,b)=>String(a.date).localeCompare(String(b.date)));
  const latest=daily.at(-1)||{},extra=latest.state_extra||{};
  const equity=n(latest.equity),cash=n(latest.cash),initialEquity=n(meta.initial_equity,extra.initial_equity);
  if(equity===null||cash===null||initialEquity===null||initialEquity<=0)throw Error('The saved ledger is missing its account balances.');
  const profits=payload.trades.map(t=>n(t.net_pnl_gbp,t.net_pnl_usd,t.pnl));
  const open=payload.positions.map(t=>{
    const supplied=n(t.unrealized_pnl,t.open_pnl,t.unrealized_pnl_gbp,t.unrealized_pnl_usd);
    if(supplied!==null)return supplied;
    if(book==='r'&&[t.units,t.last_px,t.entry_price].every(v=>number(v)!==null))return Number(t.units)*(Number(t.last_px)-Number(t.entry_price));
    return null;
  });
  const openPnl=n(latest.open_pnl,latest.unrealized_pnl,['a','b','c'].includes(book)?equity-cash:null,open.every(v=>v!==null)?open.reduce((a,b)=>a+b,0):null);
  const dds=daily.map(d=>n(d.drawdown_from_peak,d.drawdown)).filter(v=>v!==null).map(Math.abs);
  const maximum=n(latest.metrics?.max_drawdown);
  const repaired=meta.accounting_version==='quote_cash_v2'&&!meta.archived;
  return {payload,meta,state:{...extra,halted:meta.halted},daily,latest,equity,cash,initialEquity,currency:p.currency,
    repaired,
    pnl:equity-initialEquity,dayPnl:n(latest.day_pnl),openPnl,
    closedPnl:profits.every(v=>v!==null)?profits.reduce((a,b)=>a+b,0):null,
    winRate:profits.length&&profits.every(v=>v!==null)?profits.filter(v=>v>0).length/profits.length:null,
    maxDD:dds.length||maximum!==null?Math.max(...dds,Math.abs(maximum??0)):null,
    activation:meta.activation_recorded_at_utc||daily[0]?.date,through:latest.date,
    sessions:daily.length,tradeRisk:n(meta.trade_risk_fraction,extra.params?.max_risk_per_trade),
    grossExposure:n(latest.gross_exposure_x),dailyFloor:null,maxFloor:null};
}
const field=(label,value)=>`<div><dt>${e(label)}</dt><dd>${e(value)}</dd></div>`;
export function legacyTradeCard(raw,kind,book,repaired=false) {
  const t={...raw,...(raw.pos||{})},p=BOOKS[book],symbol=t.instrument||t.symbol||t.pair||'Unknown symbol';
  const dir=String(t.direction||'').toLowerCase(),side=['long','short'].includes(dir)?dir.toUpperCase():number(t.direction)===1?'LONG':number(t.direction)===-1?'SHORT':'UNKNOWN';
  const entry=n(t.entry_price),mark=n(t.last_px,t.current_price),stop=n(t.stop,t.stop_loss,t.stop_price),target=n(t.target,t.target_price,t.take_profit);
  const price=v=>v===null?'Not supplied':Number(v).toLocaleString('en-GB',{maximumFractionDigits:6});
  const explicitPnl=kind==='trades'?n(t.net_pnl_gbp,t.net_pnl_usd,t.pnl):n(t.unrealized_pnl_gbp,t.unrealized_pnl_usd,t.unrealized_pnl,t.open_pnl);
  const rawPnl=entry!==null&&mark!==null&&number(t.units)!==null&&side!=='UNKNOWN'?(mark-entry)*Number(t.units)*(side==='SHORT'?-1:1):null;
  const pnl=explicitPnl!==null?money(explicitPnl,true,p.currency):rawPnl===null?'—':`${rawPnl>0?'+':''}${rawPnl.toFixed(2)} quote`;
  const flag=v=>v===true?'Triggered':v===false?'Not triggered':'Not supplied';
  const actions=Array.isArray(t.tms_log)?t.tms_log:[];
  const reason=t.rationale||t.signal_rationale||t.entry_reason||t.condition||t.exit_rule||t.strategy||'No decision rationale was saved with this position.';
  return `<article class="ws-trade"><div class="ws-trade-head"><div><span class="ws-symbol">${e(symbol)}</span><span class="ws-direction ${side==='SHORT'?'short':''}">${side}</span><div class="ws-meta">${kind==='pending'?'Pending · '+e(t.status||'saved signal'):kind==='trades'?'Closed '+dateLabel(t.exit_time||t.exit_date):dateLabel(t.entry_time||t.entry_date)}</div></div><div><div class="ws-trade-pnl">${kind==='pending'?'Pending':e(pnl)}</div><small class="ws-meta">${kind==='pending'?'Not a filled position':explicitPnl!==null?p.currency+' saved P&L':'Price move · before costs'}</small></div></div><dl class="ws-trade-grid">
  ${field(kind==='pending'?'Trigger / decision price':'Entry · quote',price(kind==='pending'?n(t.trigger_price,raw.dec):entry))}
  ${field(kind==='trades'?'Exit · quote':'Saved mark · quote',price(kind==='trades'?n(t.exit_price):mark))}
  ${field('Stop loss · quote',stop===null&&book==='r'?'Not used · monthly rebalance':price(stop))}
  ${field('Take profit · quote',target===null&&book==='r'?'Not used · monthly rebalance':price(target))}
  ${field('Units',price(n(t.units)))}${field(kind==='trades'?'Exit reason':'Bars held',kind==='trades'?String(t.exit_reason||'Not supplied').replaceAll('_',' '):n(t.bars_open)??'—')}
  </dl><details><summary>Decision, risk &amp; management details</summary><p>${e(reason)}</p><dl class="ws-trade-grid">
  ${field('Initial stop · quote',price(n(t.initial_stop)))}${field('First partial',flag(t.tms_p1??t.partial_taken))}${field('Second partial',flag(t.tms_p2))}${field('Breakeven move',flag(t.tms_be))}${field('Position risk fraction',percent(n(t.risk_fraction)))}${field('Exit / rebalance rule',t.exit_rule||t.next_rebalance_date||'See saved stop, target and management history')}
  ${field('Entry date',dateLabel(t.entry_time||t.entry_date))}${field('Decision date',dateLabel(t.decision_date))}${field('Last saved',dateLabel(t.updated_at,true))}</dl>
  ${actions.length?`<ul>${actions.map(a=>`<li>${e(String(a.action||'Management event').replaceAll('_',' '))}${n(a.price,a.new_sl)!==null?' · '+e(price(n(a.price,a.new_sl))):''}</li>`).join('')}</ul>`:'<p>No management events supplied in this snapshot.</p>'}
  <p>${repaired?'Repaired forward ledger: account-currency P&amp;L uses versioned cash accounting. Prices, stops and targets remain in quote units. Paper costs and bar fills are estimates, not broker execution.':'Audit · 5 September 2026: '+e(LEGACY_AUDIT[book].detail)+' Saved P&amp;L is the original engine’s claim, not an independently corrected result.'}</p><p>Unconverted price-move P&amp;L is not account-currency profit. Missing fields are not replaced with another book’s rules.</p></details></article>`;
}
export function legacyRules(m,book) {
  const p=BOOKS[book],params=m?.latest.state_extra?.params||{};
  if(m?.repaired)return `<div class="ws-rule-grid"><article class="ws-rule"><h3>${e(p.name)} · repaired forward paper</h3><p>Separate ${p.currency}100,000 account activated ${dateLabel(m.activation,true)}. Old trades and profits were not imported. The original history remains in the archive.</p></article><article class="ws-rule"><h3>Execution &amp; accounting</h3><p>${book==='r'?'Monthly ETF decisions fill at the next common-session open with 5 bps per side. No per-position stop or take-profit.':book==='s'?'Closed-hour session-breakout signals queue for the next hourly open. Fixed FX units, spread costs, entry-bar/gap stops and a 16-hour time exit. A missed market hour can delay that exit.':book==='f'?'Close-based momentum signals queue for the next open. Partial profits enter cash once; pyramid additions have separate entry lots. Stops include gaps; 5 bps per side is a cost proxy.':'Original trend signals with GBP-converted sizing, cash P&L and exposure. ISWD prices are in pence. Entry bars receive stop protection; lower partial triggers precede the full target.'}</p></article><article class="ws-rule"><h3>Readiness limits</h3><p>Fresh inputs and authoritative state are required. Failed restores do not reseed; concurrent stale writes are rejected. A seeded account is not evidence of successful future trading.</p></article><article class="ws-rule"><h3>Not funded-approved</h3><p>These repairs address accounting and execution defects, not profitability. V6/V10 loss limits do not apply to these books. Hourly/daily bars cannot prove every intraday funded-rule touch. No monthly-income promise.</p></article></div>`;
  return `<div class="ws-rule-grid"><article class="ws-rule"><h3>${e(p.name)} · ${e(p.label)}</h3><p>Original saved paper ledger in ${p.currency}. Its strategy and history have not been changed by this layout update.</p><p>${book==='r'?'Monthly ETF rebalancing; no per-position stop or target is recorded.':book==='s'?'Session SMC signals; saved pending cards show the recorded trigger conditions.':'Stops, targets, partials and management events are shown only when supplied by the original engine.'}</p></article><article class="ws-rule"><h3>Saved strategy parameters</h3>${Object.keys(params).length?`<dl class="ws-trade-grid">${Object.entries(params).map(([k,v])=>field(k.replaceAll('_',' '),Array.isArray(v)?v.join(', '):String(v))).join('')}</dl>`:'<p>No strategy parameters were supplied in this snapshot.</p>'}</article><article class="ws-rule"><h3>Account risk</h3><p>V6/V10’s 3%/6% and 5%/10% limits do not apply to this older book. No funded-loss headroom is invented when the saved ledger does not provide it.</p></article><article class="ws-rule"><h3>Evidence &amp; timing</h3><p>Historical saved account values, not live broker execution or a new forward-test seed. Different currencies, start dates and data histories are not directly comparable. This page does not certify the older strategy or its reported performance.</p><p>Latest equity snapshot: ${dateLabel(m?.through)}. ${m?.meta.atomic_snapshot===false?'Positions and daily history are read from separate original tables.':''}</p></article></div>`;
}
