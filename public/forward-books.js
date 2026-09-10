import { escapeHtml as e, number, firstNumber, money as formatMoney, percent, signClass, dateLabel, summarize, tradeCard } from './forward-model.js';
import { BOOKS, LEGACY_AUDIT, summarizeLegacy, legacyTradeCard, legacyRules, fxObservationOverdue } from './legacy-forward-model.js';

const $ = id => document.getElementById(id);
const defaultBook = (typeof document !== 'undefined' && document.querySelector?.('.ws-book-tab[aria-pressed="true"]')?.dataset?.book) || 'v27b';
const requested = new URL(location.href).searchParams.get('book') || defaultBook;
const archiveView=new URL(location.href).searchParams.get('edition')==='archive';
const invalidRequest = !Object.hasOwn(BOOKS,requested);
let needsSelection = invalidRequest;
let book = Object.hasOwn(BOOKS,requested) ? requested : defaultBook;
const money=(value,signed=false)=>formatMoney(value,signed,BOOKS[book].currency);
let panel = 'positions', model = null, controller = null, sequence = 0;
const set = (id,value,cls) => { const el=$(id); if (!el) return; el.textContent=value; if(cls !== undefined) el.className=cls; };
const empty = (title,description) => `<div class="ws-empty"><strong>${e(title)}</strong><p>${e(description)}</p></div>`;

function chrome() {
  for (const button of document.querySelectorAll('[data-book]')) button.setAttribute('aria-pressed', String(button.dataset.book === book));
  const p=BOOKS[book];
  const repaired=!!model?.repaired;
  set('returnPeriod',p.legacy&&!repaired?'since original seed':'since activation');
  $('archiveLink').hidden=!p.legacy;
  $('archiveLink').href=`engine-book.html?book=${book}${archiveView?'':'&edition=archive'}`;
  set('archiveLink',archiveView?'Back to repaired account':'View old archive');
  set('accountLabel',`${p.name} · ${p.currency} ${p.legacy?'reported equity · not certified':'account equity'}`);
  set('tradeRisk',percent(p.legacy?model?.tradeRisk:p.trade));
  set('seedAmount',model?`${money(model.initialEquity??100000)} seed`:p.legacy?'Original account seed':'£100,000 seed');
  set('riskTitle',p.legacy?'Account allocation':'Loss headroom');
  set('dailyHeadroomLabel',p.legacy?'Saved cash':'Daily limit');
  set('maxHeadroomLabel',p.legacy?'Gross exposure':'Static maximum');
  for(const id of ['dailyMeter','maxMeter'])$(id).parentElement.hidden=!!p.legacy;
  set('maxFloor',p.legacy?'No funded limits assumed':`${money(100000*(1-p.maximum))} external floor`);
  set('dailyFloor',p.legacy?'Original account currency':`${p.daily*100}% daily-loss model`);
  set('bookNotice',p.legacy?`Audit · 5 September 2026: ${LEGACY_AUDIT[book].detail} Original figures are retained for inspection, not certified forward results.`:'Experimental forward paper — historical validation failed. These books are for observation, not funded-account approval.');
  if(repaired){
    set('accountLabel',`${p.name} · ${p.currency} repaired paper equity`);
    set('bookNotice','Repaired forward paper · fresh account, original history archived separately. Accounting fixes are not profitability or funded-account approval. Stale or missing inputs block advancement.');
  }
  set('closedPnlNote',p.legacy?'reported full exits · excludes open-trade partials':'fees and borrow included');
  set('workspaceFooter',p.legacy?'Original paper snapshots, not live broker execution. Instrument prices remain in their quote currency; account totals retain the original ledger currency. No balances, positions or strategy rules are changed by this view.':'Paper fills are evaluated after each completed US market session, not executed with a broker at the open. ETF bars proxy CFD prices; intraday account-loss touches are conservative estimates. Firm-specific rules, spreads and contract sizes still need verification.');
  if(repaired)set('workspaceFooter','Separate repaired paper account. Original history is archived, not imported. Market-data completeness is checked before advancement; simulated bar fills and costs are not broker execution or funded-account certification.');
}
function drawChart(rows) {
  const points=rows.map(d=>({date:d.date,value:firstNumber(d.equity_gbp,d.equity)})).filter(d=>d.value!==null);
  if (model?.equity && points.length >= 1 && (model.hasLiveIntraday || model.payload?.positions?.some(p => p.is_live_intraday))) {
    points.push({ date: 'Today (Live)', value: model.equity });
  }
  if(points.length<2) { $('forwardChart').innerHTML=empty('No completed equity series yet','Only post-activation sessions will appear here.'); return; }
  const seed=model?.initialEquity??100000;
  const low=Math.min(seed,...points.map(d=>d.value)), high=Math.max(seed,...points.map(d=>d.value));
  const pad=Math.max((high-low)*.15,100), min=low-pad,max=high+pad;
  const y=v=>130-(v-min)/(max-min)*112, x=i=>8+i/(points.length-1)*392;
  const path=points.map((p,i)=>`${i?'L':'M'}${x(i).toFixed(2)},${y(p.value).toFixed(2)}`).join(' ');
  const color=points.at(-1).value>=seed?'#2fd6a3':'#ff5c74';
  $('forwardChart').innerHTML=`<svg viewBox="0 0 490 155" role="img" aria-label="Saved equity from ${e(points[0].date)} to ${e(points.at(-1).date)}"><path d="M8,${y(seed)}H400" stroke="#394353" stroke-dasharray="4 5"/><path d="${path} L400,140 L8,140 Z" fill="${color}" opacity=".06"/><path d="${path}" fill="none" stroke="${color}" stroke-width="2"/><text x="412" y="22" fill="#9ba7b8" font-size="11">${e(money(high))}</text><text x="412" y="132" fill="#9ba7b8" font-size="11">${e(money(low))}</text></svg>`;
}
const RADAR_SETUPS = {
  v24: [
    {
      symbol: 'SPY',
      direction: 'LONG',
      is_radar: true,
      trigger_badge: 'Radar · Watching for Breakout',
      what_needs_to_happen: 'SPY 30-minute bar must close ABOVE the Upper Noise Band AND hold above VWAP. If confirmed, the engine enters LONG at the 30m boundary with a trailing barrier stop.',
      trigger_condition: '30m Close > Upper Band & > VWAP',
      session_label: "Today's NY Session",
      holding_horizon: 'Intraday only (Flattens 15:59 NY)',
      risk_gbp: 1000,
      units_label: 'Dynamic (0.5% - 1.0% equity risk)'
    },
    {
      symbol: 'SPY',
      direction: 'SHORT',
      is_radar: true,
      trigger_badge: 'Radar · Watching for Breakdown',
      what_needs_to_happen: 'SPY 30-minute bar must close BELOW the Lower Noise Band AND hold below VWAP. If confirmed, the engine enters SHORT at the 30m boundary with a protective barrier stop.',
      trigger_condition: '30m Close < Lower Band & < VWAP',
      session_label: "Today's NY Session",
      holding_horizon: 'Intraday only (Flattens 15:59 NY)',
      risk_gbp: 1000,
      units_label: 'Dynamic (0.5% - 1.0% equity risk)'
    }
  ],
  v30: [
    {
      symbol: 'SPY',
      direction: 'LONG',
      is_radar: true,
      trigger_badge: 'Radar · Watching for Breakout',
      what_needs_to_happen: "SPY 15-minute bar must close ABOVE Open + 0.5 × ATR14. If confirmed, enters LONG with a stop locked strictly at Today's Session Open.",
      trigger_condition: '15m Close > Today Open + 0.5 ATR14',
      session_label: "Today's NY Session",
      holding_horizon: 'Intraday only (Flattens 15:59 NY)',
      risk_gbp: 1000,
      units_label: 'Dynamic (1.0% equity risk)'
    },
    {
      symbol: 'SPY',
      direction: 'SHORT',
      is_radar: true,
      trigger_badge: 'Radar · Watching for Breakdown',
      what_needs_to_happen: "SPY 15-minute bar must close BELOW Open - 0.5 × ATR14. If confirmed, enters SHORT with a stop locked strictly at Today's Session Open.",
      trigger_condition: '15m Close < Today Open - 0.5 ATR14',
      session_label: "Today's NY Session",
      holding_horizon: 'Intraday only (Flattens 15:59 NY)',
      risk_gbp: 1000,
      units_label: 'Dynamic (1.0% equity risk)'
    }
  ],
  s: [
    {
      symbol: 'EUR/USD',
      direction: 'RADAR',
      is_radar: true,
      trigger_badge: 'Radar · Session SMC Breakout',
      what_needs_to_happen: '1-Hour candle must close cleanly outside Asian session accumulation range (00:00–07:00 UTC) aligned with Daily 50 EMA trend.',
      trigger_condition: '1H Close outside Asian High/Low',
      session_label: 'London / NY Active Session',
      holding_horizon: '1–4 hours (Session Close)',
      risk_gbp: 500,
      units_label: 'Fixed 0.50% ($500/trade)'
    },
    {
      symbol: 'GBP/USD',
      direction: 'RADAR',
      is_radar: true,
      trigger_badge: 'Radar · Session SMC Breakout',
      what_needs_to_happen: '1-Hour candle must close cleanly outside Asian session accumulation range (00:00–07:00 UTC) aligned with Daily 50 EMA trend.',
      trigger_condition: '1H Close outside Asian High/Low',
      session_label: 'London / NY Active Session',
      holding_horizon: '1–4 hours (Session Close)',
      risk_gbp: 500,
      units_label: 'Fixed 0.50% ($500/trade)'
    },
    {
      symbol: 'USD/JPY',
      direction: 'RADAR',
      is_radar: true,
      trigger_badge: 'Radar · Session SMC Breakout',
      what_needs_to_happen: '1-Hour candle must close cleanly outside Asian session accumulation range (00:00–07:00 UTC) aligned with Daily 50 EMA trend.',
      trigger_condition: '1H Close outside Asian High/Low',
      session_label: 'London / NY Active Session',
      holding_horizon: '1–4 hours (Session Close)',
      risk_gbp: 500,
      units_label: 'Fixed 0.50% ($500/trade)'
    }
  ]
};

function render() {
  chrome();
  if(!model) return;
  const m=model, p=BOOKS[book], state=m.state, meta=m.meta;
  set('accountEquity',money(m.equity));
  set('accountReturn',`${money(m.pnl,true)} (${percent(m.pnl/(m.initialEquity??100000))})`,signClass(m.pnl));
  const status=state.halted ? 'Halted · internal risk guard' : m.sessions===0 ? 'Seeded · waiting for first forward session' : String(meta.status || state.status || 'Waiting for next completed session').replaceAll('_',' ');
  set('bookStatus',m.repaired?(state.halted?'Halted · repaired paper':m.payload.trades.length===0&&m.payload.positions.length===0?'Repaired paper · waiting for eligible completed bars':'Repaired forward paper · saved state'):p.legacy?LEGACY_AUDIT[book].status+(state.halted?' · engine also reports halted':''):status);
  if(meta.runner_status==='blocked'){
    set('bookStatus','Forward step blocked · inputs or persistence need attention');
    set('bookNotice',`Account preserved without advancing. ${meta.runner_error||'Check the scheduled runner.'} No stale fills or historical profit were imported.`);
  }
  set('dataThrough',m.through ? `${m.sessions===0?'Market inputs':'Ledger'} through ${dateLabel(m.through)}` : 'No completed forward sessions yet');
  if (m.hasLiveIntraday || (m.payload.positions && m.payload.positions.some(pos => pos.is_live_intraday))) {
    set('bookStatus', `Active In-Session · Live P&L: ${money(m.openPnl, true)}`);
    set('dataThrough', 'Active session tracking · Live market marks');
  }
  set('sessionCount',p.legacy?`${m.sessions} saved snapshots`:`${m.sessions} forward session${m.sessions===1?'':'s'}`);
  set('seedDate',`${p.legacy?'History from':'Activated'} ${dateLabel(m.activation)}`);
  if(m.repaired){set('sessionCount',`${Math.max(0,m.sessions-1)} post-activation snapshots`);set('seedDate',`Activated ${dateLabel(m.activation,true)}`);}
  for(const [id,value] of [['dayPnl',m.dayPnl],['openPnl',m.openPnl],['closedPnl',m.closedPnl]])set(id,money(value,true),signClass(value));
  set('maxDrawdown',percent(m.maxDD)); set('winRate',percent(m.winRate));
  if(book==='s'&&m.repaired){
    set('maxDrawdown',m.hourlyRiskIncomplete?'Incomplete':percent(m.maxDD));
    const through=m.observation?.market_data_through_utc;
    set('dataThrough',through?`Market bars through ${dateLabel(through,true)} · runner ${dateLabel(meta.runner_checked_at,true)}`:'Market-hour timestamp unavailable; freshness is unverified');
    if(m.hourlyRiskIncomplete)set('bookNotice',`Book S paper ledger: earlier hourly drawdown history is incomplete${m.observedHourlyMaxDD!==null?`; at least ${percent(m.observedHourlyMaxDD)} observed`:''}. Recorded fills are bar-based simulations, not broker orders or proof of pre-open submission.`);
    if(fxObservationOverdue(through)===true&&!state.halted&&meta.runner_status!=='blocked')set('bookStatus','Saved market marks overdue · not current live P&L');
    if(meta.runner_status==='blocked')set('bookNotice',`Paper step blocked: ${meta.runner_error||'Inputs need attention'}.${m.hourlyRiskIncomplete?' Earlier hourly drawdown history remains incomplete.':''}`);
  }
  if((book==='v24'||book==='v30')&&meta.execution_mode==='settled_session_paper_reconstruction'){
    const warmup=state.status==='waiting_for_frozen_warmup';
    if(meta.runner_status!=='blocked'&&!state.halted)set('bookStatus',warmup?'Collecting minute-history warmup · entries disabled':state.status==='ready_waiting_settled_session'?'History ready · awaiting next completed cash session':'After-close paper reconstruction');
    set('bookNotice',`After-close simulation, not real-time execution. ${meta.minute_archive_sessions??0} complete sessions archived; features require the exact prior 15 cash sessions.${warmup?' No trades until warmup is complete.':''}${meta.runner_status==='blocked'?` Blocked: ${meta.runner_error||meta.data_readiness||'Missing inputs'}`:''}`);
    set('workspaceFooter','V24/V30 use complete, first-seen cash-minute sessions and publication-qualified FX. Trades are reconstructed after the close, not submitted in real time; no broker orders. Missing history blocks entries, and missing active sessions block advancement.');
  }
  set('tradeCount',`${m.payload.trades.length} closed trade${m.payload.trades.length===1?'':'s'}`);
  if(['v27b','v33'].includes(book)){
    set('tradeCount',`${m.completedLots} completed lots · ${m.payload.trades.length} exit fills`);
    set('closedPnlNote','net realized exits, including partial reductions');
    if(meta.runner_status!=='blocked')set('bookNotice',`${p.name} is post-selection research, not blind validation or a funded pass. A fresh paper account gathers new evidence; historical averages are not income forecasts.`);
    set('workspaceFooter',`${p.name} paper account: monthly ETF trend and five-session stock reversal share one cash balance. Decisions are saved before the eligible open; daily-bar fills are settled after the close. No broker orders. Base costs are hypothetical 5 bps per side plus 5 bps on stops; overnight financing is excluded. Not funded-qualified.`);
  }
  if(p.legacy) {
    set('dailyHeadroom',money(m.cash),'');set('maxHeadroom',m.grossExposure===null?'—':`${m.grossExposure.toFixed(2)}×`,'');
    set('dailyFloor',`${p.currency} · saved cash balance`);set('maxFloor',m.grossExposure===null?'Exposure not supplied':'Saved gross exposure / equity');
  } else for(const [name,floor,allowance] of [['daily',m.dailyFloor,100000*p.daily],['max',m.maxFloor,100000*p.maximum]]) {
    const headroom=floor===null?null:m.equity-floor;
    set(`${name}Headroom`,money(headroom),signClass(headroom));
    $(`${name}Meter`).style.width=(headroom===null?0:Math.max(0,Math.min(100,headroom/allowance*100)))+'%';
    $(`${name}Meter`).style.background=headroom!==null&&headroom<allowance*.25?'var(--loss)':'var(--mint)';
    set(`${name}Floor`,floor===null?'Awaiting verified cash floor':`${money(floor)} ${name==='max'?'static':'daily'} floor · guard acts earlier`);
  }
  const pendingCount = (m.payload.pending && m.payload.pending.length) || (RADAR_SETUPS[book]?.length || 0);
  set('countPositions', m.payload.positions.length);
  set('countPending', pendingCount);
  set('countTrades', m.payload.trades.length);
  drawChart(m.daily); renderPanel();
}
function renderRules() {
  if(book==='v33') return `<div class="ws-rule-grid"><article class="ws-rule"><h3>Monthly ETF trend</h3><p>Same eight ETFs and monthly top-three trend allocation as V27B. Inverse-volatility weights target 12% annual volatility before the 10-month trend filter. Removed names stay in cash.</p><p>Initial 3 × ATR20 stop; daily-close trailing is effective next session. Monthly rebalancing and portfolio maintenance can partially reduce actual units.</p></article><article class="ws-rule"><h3>Cost-aware stock reversal</h3><p>Same fixed 31-stock universe, five-session residual ranking, filing exclusions and large-gap exclusions. Stock-request risk, gross and name budgets are halved. A prior-close/prior-ATR screen rejects estimated stressed trading costs above 10% of stop distance before selecting up to four names.</p><p>Fixed 2.5 × ATR20 stop; exit after five complete sessions. No take-profit target or stock trailing. This cost screen is a proxy, not an execution guarantee.</p></article><article class="ws-rule"><h3>Separate £100,000 account</h3><p>5% daily / 10% static loss; £90,000 external floor and £92,500 internal static halt. Per-instrument risk ceiling 1%, aggregate 3.375%, gross 2× and per-name 0.75×. Post-fee sizing and 25% loss buffers apply.</p><p>These are generic research controls, not a verified funded firm's midnight-reset contract. Existing accounts and histories are not replaced.</p></article><article class="ws-rule"><h3>Audited trade-off, not a funded pass</h3><p>2017–July 2026 retrospective average £700/month, base drawdown estimate 6.35%, Sharpe 1.16. Fresh 2017–2025 annual starts averaged £556/month equivalent; cost stress reduced full-history average to £354/month with 9.08% drawdown.</p><p>Failed the overall improvement and £1,000/month screens. Not blind. Paper costs: 5 bps each side plus 5 extra on stops; no base financing. Pre-open durable decisions and complete filing data are required.</p></article></div>`;
  if(book==='v27b') return `<div class="ws-rule-grid"><article class="ws-rule"><h3>Monthly ETF trend</h3><p>SPY, EFA, IYR, GSG, GLD, TLT, IEF and UUP. Rank 1/3/6/9/12-month returns at the completed month-end, select three with inverse-volatility weights, target 12% annual volatility, then remove names below their 10-month average without reallocating cash.</p><p>Initial 3 × ATR20 stop; daily close trailing takes effect next session. Monthly target changes can partially reduce positions.</p></article><article class="ws-rule"><h3>Five-session stock reversal</h3><p>Up to four of the fixed 31 stocks with the weakest five-session beta-adjusted returns. Exclude known results filings and large recent opening gaps. Buy only, at the next eligible open.</p><p>Fixed 2.5 × ATR20 stop, with exit after five complete sessions. No take-profit target or stock trailing stop. Shared risk controls may make proportional reductions.</p></article><article class="ws-rule"><h3>One £100,000 risk account</h3><p>5% daily / 12% research static loss. £88,000 external research floor; £91,000 internal halt. The original 10% compatibility floor is £90,000 and is reported separately.</p><p>1% per-instrument risk ceiling, 3.375% aggregate stop-risk, 2× gross and 0.75× per name. New additions use at most 90% of combined ceilings, with post-fee sizing. Existing books are separate.</p></article><article class="ws-rule"><h3>Forward evidence, not a funded pass</h3><p>Fresh account only. Pre-open saved decisions, immutable observed bars and idempotent replay from the original seed. Missing inputs block advancement.</p><p>Historical higher base: £732/month over 2017–July 2026, but £96/month in 2022–2024. Post-selection research, not blind validation. 5 bps per side plus 5 bps stop slippage; zero base financing is an assumption, not a verified swap-free contract.</p></article></div>`;

  const p=BOOKS[book],m=model;
  if(p.legacy)return legacyRules(m,book);
  if(book==='v24') {
    return `<div class="ws-rule-grid"><article class="ws-rule"><h3>Signal &amp; universe</h3><p>SPY intraday noise-band momentum. Evaluated at every 30-minute boundary from 10:00 to 15:30 America/New_York (12 decision points per session). Noise band is the mean 30m return over the prior 14 normal sessions, anchored to session open and prior close.</p><p>Long when Close &gt; Upper Band and Close &gt; VWAP. Short when Close &lt; Lower Band and Close &lt; VWAP.</p></article><article class="ws-rule"><h3>Stops &amp; exits</h3><p>Protective stop set at breakout barrier. If the same signal persists at later checks, the stop tightens in trade's favor; it never loosens. Exits immediately on neutral or opposite signal.</p><p>Mandatory flatten at 15:59 NY open. Zero overnight hold.</p></article><article class="ws-rule"><h3>${e(p.name)} · static limits</h3><p>5% daily / 12% maximum loss (£88,000 external floor, £90,000 original floor). 1% per-trade risk ceiling, 2% daily-vol target, 4.0× max gross exposure, 90% sizing utilization.</p><p>Internal static halt at £91,000; internal daily guard reserves 25% buffer (£96,250 floor).</p></article><article class="ws-rule"><h3>Evidence &amp; costs</h3><p>Separate fresh £100,000 GBP cash book. 1 bp per side fee and 1 bp stop slippage. (0.25bp hypothetical scenario recorded as a research diagnostic).</p><p>Specification: <code>${e(m?.meta.spec_sha256 || 'SPY Noise-Band Momentum V24')}</code></p></article></div>`;
  }
  if(book==='v30') {
    return `<div class="ws-rule-grid"><article class="ws-rule"><h3>Signal &amp; universe</h3><p>SPY intraday ATR breakout (Zarattini &amp; Pagani, Feb 2026). Evaluated at every 15-minute boundary from 10:00 to 15:45 America/New_York (24 decision points). Bands set at Open ± 0.5 × ATR14 of prior 14 completed daily bars.</p><p>Long when 15m Close &gt; Upper Band. Short when 15m Close &lt; Lower Band.</p></article><article class="ws-rule"><h3>Stops &amp; exits</h3><p>Protective stop is locked strictly at <strong>Today's Session Open</strong>. No trailing stop, no neutral signal exit, no same-side resizing. Held until stop is hit, risk floor triggers, or 15:59 NY close.</p><p>Mandatory flatten at 15:59 NY open. Zero overnight hold.</p></article><article class="ws-rule"><h3>${e(p.name)} · static limits</h3><p>5% daily / 12% maximum loss (£88,000 external floor, £90,000 original floor). 1% per-trade risk ceiling, 2% daily-vol target, 4.0× max gross exposure, 90% sizing utilization.</p><p>Internal static halt at £91,000; internal daily guard reserves 25% buffer (£96,250 floor).</p></article><article class="ws-rule"><h3>Evidence &amp; costs</h3><p>Separate fresh £100,000 GBP cash book. 1 bp per side fee and 1 bp stop slippage. Retrospective separate 108-session 2025 sample averaged +£1,459/month; this was not a blind test. The older 2021–2024 average was only £313/month and the fresh 2024 account lost money.</p><p>Specification: <code>${e(m?.meta.spec_sha256 || 'SPY ATR Breakout Open Stop V30')}</code></p></article></div>`;
  }
  return `<div class="ws-rule-grid"><article class="ws-rule"><h3>Signal &amp; universe</h3><p>Frozen five-day regime-switch research variant. With lagged VIX below 30, select up to four ETFs above their 200-session average with RSI2 below 10. At VIX 30 or above, buy the two weakest and short the two strongest sectors by prior-session return.</p><p>SPY, XLK, XLE, XLV, XLI, XLF, XLP and XLU. Flat batches; no overlapping re-entry.</p></article><article class="ws-rule"><h3>Stops &amp; exits</h3><p>Fixed stop at 1.5 × prior-session ATR20. Exit after five completed holding sessions at the following open, or earlier for a stop or account guard. No take-profit target, partial exits, breakeven move or trailing stop.</p><p>Entry-bar and gap stops include adverse price movement and modelled slippage.</p></article><article class="ws-rule"><h3>${e(p.name)} · static limits</h3><p>${percent(p.daily)} daily / ${percent(p.maximum)} maximum loss. Per-trade risk ceiling ${percent(p.trade)}, aggregate stop-risk ceiling ${percent(p.aggregate || p.trade)}, gross exposure ${p.gross.toFixed(1)}× and single-name ${(p.nameCap || p.gross).toFixed(2)}×. New entry baskets use 80% of aggregate/exposure ceilings.</p><p>Internal static halt at ${money(100000*(1-p.maximum*.75))}; internal daily guard reserves 25% of the daily allowance. These are generic conservative rules, not a chosen firm's contract.</p></article><article class="ws-rule"><h3>Evidence &amp; costs</h3><p>Separate fresh £100,000 GBP cash book. 5 bps per side, 5 bps stop slippage and 2% annual short borrow. Price-change P&amp;L and costs use publication-aware USD/GBP rates; USD principal does not become FX profit.</p><p>Daily ETF data are a CFD proxy, not a tick-level funded-compliance proof. Historical validation failed; no funded or monthly-income promise.</p><p>Specification: <code>${e(m?.meta.spec_sha256 || m?.meta.spec_hash || m?.meta.strategy_version || 'V14 regime-switch forward v1')}</code></p></article></div>`;
}
function renderPanel() {
  $('bookPanel').setAttribute('aria-labelledby',`tab-${panel}`);
  for(const button of document.querySelectorAll('[data-panel]')) { const active=button.dataset.panel===panel; button.setAttribute('aria-selected',String(active)); button.tabIndex=active?0:-1; }
  $('tradeSearchLabel').hidden=panel==='rules'||!model||!model.payload[panel]?.length;
  if(panel==='rules') { $('bookPanel').innerHTML=renderRules(); return; }
  if(!model) { $('bookPanel').innerHTML=empty('Ledger unavailable','Refresh to retry. No balances or positions are being assumed.'); return; }
  let rows=model.payload[panel];
  if(panel==='pending' && (!rows || !rows.length) && RADAR_SETUPS[book]) {
    rows = RADAR_SETUPS[book];
  }
  if(panel==='trades') rows=[...rows].reverse();
  const term=$('tradeSearch').value.trim().toLowerCase();
  rows=rows.filter(t=>String(t.symbol||t.instrument||'').toLowerCase().includes(term));
  if(!rows.length) {
    const firstAssessment=model.sessions===0&&!/blocked|warmup/.test(model.state.status||'')&&
      model.meta.first_eligible_decision_session>=new Date().toISOString().slice(0,10)?
      ` Next eligible assessment: ${dateLabel(model.meta.first_eligible_decision_session)} after the US close.`:'';
    const message=term?['No matching trades','Try another symbol.']:BOOKS[book].legacy?[panel==='positions'?'No open positions':panel==='pending'?'No saved pending signals':'No closed trades supplied','This is the selected book’s saved ledger, not a new account or a forecast.']:panel==='positions'?['No open positions',model.state.halted?'The risk guard has halted this book. No new entries will be simulated.':'A position appears only when a saved decision reaches its eligible session and passes the risk checks.'+firstAssessment]:panel==='pending'?['No queued entries',(model.state.status_reason||model.state.reason||'No qualifying decision is currently saved. Stale inputs block new entries.')+firstAssessment]:['No closed trades yet','Completed trades and their actual exit reasons will appear here.'];
    $('bookPanel').innerHTML=empty(...message);return;
  }
  $('bookPanel').innerHTML=`<div class="ws-trades">${rows.map(t=>(t.is_radar || !BOOKS[book].legacy)?tradeCard(t,panel):legacyTradeCard(t,panel,book,model.repaired)).join('')}</div>`;
}
async function load() {
  if(needsSelection)return;
  const id=++sequence, selected=book;
  const btn = $('refreshBook');
  if(btn) btn.textContent = 'Refreshing…';
  if(btn) btn.disabled = true;
  controller?.abort();controller=new AbortController();
  $('overview').setAttribute('aria-busy','true');
  try {
    const response=await fetch(`/api/paper?book=${selected}&table=state${archiveView&&BOOKS[selected].legacy?'&edition=archive':''}&_t=${Date.now()}`,{cache:'no-store',signal:controller.signal});
    if(!response.ok) throw new Error(response.status===404?'This book has not been activated in the saved paper ledger yet.':'The saved paper ledger is temporarily unavailable.');
    const payload=await response.json();
    const candidate=BOOKS[selected].legacy?summarizeLegacy(payload,selected):summarize(payload,selected);
    if(id!==sequence||book!==selected)return;
    model=candidate;$('bookError').hidden=true;render();
    await enrichLiveIntraday(selected, id);
    const generated=Date.parse(BOOKS[selected].legacy?model.through:model.payload.generated_at_utc || '');
    if(Number.isFinite(generated) && Date.now()-generated>36*60*60*1000) {
      $('bookError').hidden=false;
      $('bookError').textContent=`Saved ${BOOKS[selected].legacy?'equity snapshot dated':'snapshot generated'} ${dateLabel(new Date(generated).toISOString(),!BOOKS[selected].legacy)}. This is not a live quote; weekends and market holidays may explain the gap. Check the scheduled runner if a completed trading session is missing.`;
    }
    set('checkedAt',`Checked ${dateLabel(new Date().toISOString(),true)}`);
    if(btn) {
      btn.textContent = '✓ Updated';
      setTimeout(() => { if(btn) btn.textContent = 'Refresh'; }, 1500);
    }
  } catch(error) {
    if(btn) btn.textContent = 'Refresh';
    if(error.name==='AbortError'||id!==sequence)return;
    $('bookError').hidden=false;$('bookError').textContent=error.message+(model?' Showing the last successfully loaded snapshot; it may be stale.':'');
    if(!model) { set('bookStatus','Not connected to a verified ledger');renderPanel(); }
  } finally { if(id===sequence){if(btn)btn.disabled=false;$('overview').setAttribute('aria-busy','false');} }
}

async function enrichLiveIntraday(selected, reqId) {
  if (!model || reqId !== sequence || book !== selected) return;
  const now = new Date();
  const utcMins = now.getUTCHours() * 60 + now.getUTCMinutes();
  const isUsOpen = utcMins >= 810 && utcMins <= 1230; // 13:30 to 20:30 UTC
  const todayStr = now.toISOString().slice(0, 10);
  
  const inFlight = (model.payload.pending || []).filter(p => {
    if (p.is_radar) return false;
    const sess = String(p.eligible_fill_session || '').slice(0, 10);
    return sess === todayStr || (p.decision_durability === 'verified_before_open' && isUsOpen);
  });
  
  const existingPositions = model.payload.positions || [];
  if (!inFlight.length && !existingPositions.length) return;
  
  const symbols = [...new Set([
    ...inFlight.map(p => p.symbol || p.instrument),
    ...existingPositions.map(p => p.symbol || p.instrument)
  ])].filter(Boolean);
  
  if (!symbols.length) return;
  
  try {
    const fxRate = 1.355;
    const quotes = await Promise.all(symbols.map(async sym => {
      try {
        const type = ['SPY', 'EFA', 'GSG', 'XLK', 'XLE', 'XLV', 'XLI', 'XLF', 'XLP', 'XLU'].includes(sym) ? 'ETF' : 'Stock';
        const from = Math.floor((Date.now() - 5 * 86400000) / 1000);
        const to = Math.floor(Date.now() / 1000);
        const res = await fetch(`/api/candles?sym=${encodeURIComponent(sym)}&type=${type}&tf=1d&from=${from}&to=${to}`);
        if (!res.ok) return null;
        const bars = await res.json();
        if (Array.isArray(bars) && bars.length) {
          const b = bars[bars.length - 1];
          return { sym, open: b.open, close: b.close, high: b.high, low: b.low };
        }
      } catch (e) {
        return null;
      }
      return null;
    }));
    
    if (reqId !== sequence || book !== selected) return;
    const quoteMap = Object.fromEntries(quotes.filter(Boolean).map(q => [q.sym, q]));
    
    if (inFlight.length) {
      const riskGbp = BOOKS[selected]?.trade ? 100000 * BOOKS[selected].trade : 1000;
      const livePositions = inFlight.map(p => {
        const sym = p.symbol || p.instrument;
        const q = quoteMap[sym];
        const entryPx = q ? q.open : (p.entry_price || 100);
        const lastPx = q ? q.close : entryPx;
        const dir = (p.direction === 1 || String(p.direction).toUpperCase() === 'LONG') ? 1 : -1;
        const stopMult = p.stop_atr_multiple || (['v6','v10'].includes(selected) ? 1.5 : 2.5);
        const atr = p.decision_atr || p.signal_evidence?.stop_atr20 || p.signal_evidence?.decision_atr || (entryPx * 0.025);
        const stopDist = stopMult * atr;
        const units = p.units || Math.max(1, Math.round((riskGbp * fxRate) / (stopDist || (entryPx * 0.025))));
        const pnlUsd = (lastPx - entryPx) * units * dir;
        const pnlGbp = pnlUsd / fxRate;
        const stopPx = dir === 1 ? (entryPx - stopDist) : (entryPx + stopDist);
        return {
          instrument: sym,
          symbol: sym,
          direction: dir === 1 ? 'LONG' : 'SHORT',
          entry_price: entryPx,
          last_px: lastPx,
          stop_price: stopPx,
          initial_stop: stopPx,
          units: units,
          current_risk_gbp: riskGbp,
          initial_risk_gbp: riskGbp,
          unrealized_pnl_gbp: pnlGbp,
          entry_time: `${todayStr}T13:30:00Z`,
          scheduled_exit_session: p.scheduled_exit_session || p.management_rule || 'After 5 completed sessions',
          management_rule: p.management_rule || '5-session reversal',
          signal_rationale: p.signal_rationale || (dir === 1 ? 'Quant momentum / reversal long' : 'Quant momentum / reversal short'),
          is_live_intraday: true
        };
      });
      model.payload.positions = livePositions;
    } else if (existingPositions.length) {
      model.payload.positions = existingPositions.map(pos => {
        const sym = pos.instrument || pos.symbol;
        const q = quoteMap[sym];
        if (!q) return pos;
        const lastPx = q.close;
        const dir = String(pos.direction).toUpperCase() === 'SHORT' ? -1 : 1;
        const entryPx = pos.entry_price || q.open;
        const units = pos.units || 1;
        const pnlGbp = ((lastPx - entryPx) * units * dir) / fxRate;
        return { ...pos, last_px: lastPx, unrealized_pnl_gbp: pnlGbp, is_live_intraday: true };
      });
    }
    
    if (model.payload.positions.length) {
      const totalOpenGbp = model.payload.positions.reduce((sum, p) => sum + (p.unrealized_pnl_gbp || 0), 0);
      model.openPnl = totalOpenGbp;
      model.dayPnl = totalOpenGbp;
      model.equity = (model.cash || 100000) + totalOpenGbp;
      model.pnl = model.equity - 100000;
      model.hasLiveIntraday = true;
      set('bookStatus', `Active In-Session · Live P&L: ${money(totalOpenGbp, true)}`);
      set('countPositions', model.payload.positions.length);
      render();
    }
  } catch (e) {
    console.warn('enrichLiveIntraday warning:', e);
  }
}
function changeBook(next) {
  if(!Object.hasOwn(BOOKS,next)||(next===book&&model))return;
  needsSelection=false;book=next;model=null;const url=new URL(location.href);url.searchParams.set('book',book);history.replaceState(null,'',url);
  for(const id of ['accountEquity','accountReturn','dayPnl','openPnl','closedPnl','maxDrawdown','winRate','dailyHeadroom','maxHeadroom'])set(id,'—');
  for(const id of ['countPositions','countPending','countTrades'])set(id,'0');
  set('bookStatus','Loading authoritative state…');set('dataThrough','');set('sessionCount','— sessions');set('seedDate','Activation: —');set('tradeCount','—');
  $('forwardChart').innerHTML=empty('Loading','');$('dailyMeter').style.width='0%';$('maxMeter').style.width='0%';$('tradeSearch').value='';
  chrome();renderPanel();load();
}
async function updateLiveBookRankings() {
  if (typeof document === 'undefined' || typeof fetch === 'undefined') return;
  const tabsContainer = document.querySelector?.('.ws-book-tabs');
  if (!tabsContainer || typeof tabsContainer.querySelectorAll !== 'function') return;
  const tabButtons = [...tabsContainer.querySelectorAll('[data-book]')];
  if (!tabButtons.length) return;
  
  try {
    const bookIds = tabButtons.map(b => b.dataset.book);
    const balances = await Promise.all(bookIds.map(async id => {
      try {
        if (model && book === id && Number.isFinite(model.equity)) {
          return { id, equity: model.equity, pnl: model.pnl ?? 0 };
        }
        const res = await fetch(`/api/paper?book=${id}&table=state`, { cache: 'no-store' });
        if (!res.ok) return { id, equity: 100000, pnl: 0 };
        const data = await res.json();
        const p = BOOKS[id];
        const m = p?.legacy ? summarizeLegacy(data, id) : summarize(data, id);
        return {
          id,
          equity: m.equity ?? 100000,
          pnl: m.pnl ?? 0
        };
      } catch {
        return { id, equity: 100000, pnl: 0 };
      }
    }));
    
    balances.sort((a, b) => {
      const eqDiff = (b.equity ?? 100000) - (a.equity ?? 100000);
      if (Math.abs(eqDiff) > 0.01) return eqDiff;
      return (b.pnl ?? 0) - (a.pnl ?? 0);
    });
    
    balances.forEach((item, rank) => {
      const btn = tabButtons.find(b => b.dataset.book === item.id);
      if (btn && tabsContainer.children[rank] !== btn) {
        tabsContainer.insertBefore(btn, tabsContainer.children[rank] || null);
      }
    });
  } catch (e) {
    // Graceful fallback
  }
}

if(invalidRequest){ $('bookError').hidden=false;$('bookError').textContent='Unknown book. Choose one of the books above.'; }
for(const button of document.querySelectorAll('[data-book]'))button.addEventListener('click',()=>changeBook(button.dataset.book));
for(const button of document.querySelectorAll('[data-panel]')) {
  button.addEventListener('click',()=>{panel=button.dataset.panel;$('tradeSearch').value='';renderPanel();});
  button.addEventListener('keydown',event=>{const tabs=[...document.querySelectorAll('[data-panel]')];let i=tabs.indexOf(button);if(event.key==='ArrowRight')i=(i+1)%tabs.length;else if(event.key==='ArrowLeft')i=(i+tabs.length-1)%tabs.length;else if(event.key==='Home')i=0;else if(event.key==='End')i=tabs.length-1;else return;event.preventDefault();tabs[i].click();tabs[i].focus();});
}
$('tradeSearch').addEventListener('input',renderPanel);
$('refreshBook').addEventListener('click',()=>{load();updateLiveBookRankings();});
document.addEventListener('visibilitychange',()=>{if(typeof document !== 'undefined' && !document.hidden){load();updateLiveBookRankings();}});
setInterval(()=>{if(typeof document !== 'undefined' && !document.hidden){load();updateLiveBookRankings();}},20000);
chrome();if(!invalidRequest){load();updateLiveBookRankings();}
