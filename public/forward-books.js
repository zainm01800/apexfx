import { PROFILES, escapeHtml as e, number, firstNumber, money, percent, signClass, dateLabel, summarize, tradeCard } from './forward-model.js';

const $ = id => document.getElementById(id);
const requested = new URL(location.href).searchParams.get('book') || 'v6';
if (['a','b','c','r','s','f'].includes(requested)) location.replace(`legacy-book.html?book=${requested}`);
const invalidRequest = !Object.hasOwn(PROFILES,requested)&&!['a','b','c','r','s','f'].includes(requested);
let needsSelection = invalidRequest;
let book = Object.hasOwn(PROFILES,requested) ? requested : 'v6';
let panel = 'positions', model = null, controller = null, sequence = 0;
const set = (id,value,cls) => { const el=$(id); if (!el) return; el.textContent=value; if(cls !== undefined) el.className=cls; };
const empty = (title,description) => `<div class="ws-empty"><strong>${e(title)}</strong><p>${e(description)}</p></div>`;

function chrome() {
  for (const button of document.querySelectorAll('[data-book]')) button.setAttribute('aria-pressed', String(button.dataset.book === book));
  set('accountLabel',`${PROFILES[book].name} · GBP account equity`);
  set('tradeRisk',percent(PROFILES[book].trade));
  set('maxFloor',`${money(100000*(1-PROFILES[book].maximum))} external floor`);
  set('dailyFloor',`${PROFILES[book].daily*100}% daily-loss model`);
}
function drawChart(rows) {
  const points=rows.map(d=>({date:d.date,value:firstNumber(d.equity_gbp,d.equity)})).filter(d=>d.value!==null);
  if(points.length<2) { $('forwardChart').innerHTML=empty('No completed equity series yet','Only post-activation sessions will appear here.'); return; }
  const low=Math.min(100000,...points.map(d=>d.value)), high=Math.max(100000,...points.map(d=>d.value));
  const pad=Math.max((high-low)*.15,100), min=low-pad,max=high+pad;
  const y=v=>130-(v-min)/(max-min)*112, x=i=>8+i/(points.length-1)*392;
  const path=points.map((p,i)=>`${i?'L':'M'}${x(i).toFixed(2)},${y(p.value).toFixed(2)}`).join(' ');
  const color=points.at(-1).value>=100000?'#2fd6a3':'#ff5c74';
  $('forwardChart').innerHTML=`<svg viewBox="0 0 490 155" role="img" aria-label="Official forward equity from ${e(points[0].date)} to ${e(points.at(-1).date)}"><path d="M8,${y(100000)}H400" stroke="#394353" stroke-dasharray="4 5"/><path d="${path} L400,140 L8,140 Z" fill="${color}" opacity=".06"/><path d="${path}" fill="none" stroke="${color}" stroke-width="2"/><text x="412" y="22" fill="#9ba7b8" font-size="11">${e(money(high))}</text><text x="412" y="132" fill="#9ba7b8" font-size="11">${e(money(low))}</text></svg>`;
}
function render() {
  chrome();
  if(!model) return;
  const m=model, p=PROFILES[book], state=m.state, meta=m.meta;
  set('accountEquity',money(m.equity));
  set('accountReturn',`${money(m.pnl,true)} (${percent(m.pnl/100000)})`,signClass(m.pnl));
  const status=state.halted ? 'Halted · internal risk guard' : m.sessions===0 ? 'Seeded · waiting for first forward session' : String(meta.status || state.status || 'Waiting for next completed session').replaceAll('_',' ');
  set('bookStatus',status);
  set('dataThrough',m.through ? `${m.sessions===0?'Market inputs':'Ledger'} through ${dateLabel(m.through)}` : 'No completed forward sessions yet');
  set('sessionCount',`${m.sessions} forward session${m.sessions===1?'':'s'}`);
  set('seedDate',`Activated ${dateLabel(m.activation)}`);
  for(const [id,value] of [['dayPnl',m.dayPnl],['openPnl',m.openPnl],['closedPnl',m.closedPnl]])set(id,money(value,true),signClass(value));
  set('maxDrawdown',percent(m.maxDD)); set('winRate',percent(m.winRate));
  set('tradeCount',`${m.payload.trades.length} closed trade${m.payload.trades.length===1?'':'s'}`);
  for(const [name,floor,allowance] of [['daily',m.dailyFloor,100000*p.daily],['max',m.maxFloor,100000*p.maximum]]) {
    const headroom=floor===null?null:m.equity-floor;
    set(`${name}Headroom`,money(headroom),signClass(headroom));
    $(`${name}Meter`).style.width=(headroom===null?0:Math.max(0,Math.min(100,headroom/allowance*100)))+'%';
    $(`${name}Meter`).style.background=headroom!==null&&headroom<allowance*.25?'var(--loss)':'var(--mint)';
    set(`${name}Floor`,floor===null?'Awaiting verified cash floor':`${money(floor)} ${name==='max'?'static':'daily'} floor · guard acts earlier`);
  }
  for(const [kind,id] of [['positions','countPositions'],['pending','countPending'],['trades','countTrades']])set(id,m.payload[kind].length);
  drawChart(m.daily); renderPanel();
}
function renderRules() {
  const p=PROFILES[book],m=model;
  return `<div class="ws-rule-grid"><article class="ws-rule"><h3>Signal &amp; universe</h3><p>Frozen five-day regime-switch research variant. With lagged VIX below 30, select up to four ETFs above their 200-session average with RSI2 below 10. At VIX 30 or above, buy the two weakest and short the two strongest sectors by prior-session return.</p><p>SPY, XLK, XLE, XLV, XLI, XLF, XLP and XLU. Flat batches; no overlapping re-entry.</p></article><article class="ws-rule"><h3>Stops &amp; exits</h3><p>Fixed stop at 1.5 × prior-session ATR20. Exit after five completed holding sessions at the following open, or earlier for a stop or account guard. No take-profit target, partial exits, breakeven move or trailing stop.</p><p>Entry-bar and gap stops include adverse price movement and modelled slippage.</p></article><article class="ws-rule"><h3>${e(p.name)} · static limits</h3><p>${percent(p.daily)} daily / ${percent(p.maximum)} maximum loss. Per-trade risk ceiling ${percent(p.trade)}, aggregate stop-risk ceiling ${percent(p.aggregate)}, gross exposure ${p.gross.toFixed(1)}× and single-name ${p.nameCap.toFixed(2)}×. New entry baskets use 80% of aggregate/exposure ceilings.</p><p>Internal static halt at ${money(100000*(1-p.maximum*.75))}; internal daily guard reserves 25% of the daily allowance. These are generic conservative rules, not a chosen firm's contract.</p></article><article class="ws-rule"><h3>Evidence &amp; costs</h3><p>Separate fresh £100,000 GBP cash book. 5 bps per side, 5 bps stop slippage and 2% annual short borrow. Price-change P&amp;L and costs use publication-aware USD/GBP rates; USD principal does not become FX profit.</p><p>Daily ETF data are a CFD proxy, not a tick-level funded-compliance proof. Historical validation failed; no funded or monthly-income promise.</p><p>Specification: <code>${e(m?.meta.spec_sha256 || m?.meta.spec_hash || m?.meta.strategy_version || 'V14 regime-switch forward v1')}</code></p></article></div>`;
}
function renderPanel() {
  $('bookPanel').setAttribute('aria-labelledby',`tab-${panel}`);
  for(const button of document.querySelectorAll('[data-panel]')) { const active=button.dataset.panel===panel; button.setAttribute('aria-selected',String(active)); button.tabIndex=active?0:-1; }
  $('tradeSearchLabel').hidden=panel==='rules'||!model||!model.payload[panel]?.length;
  if(panel==='rules') { $('bookPanel').innerHTML=renderRules(); return; }
  if(!model) { $('bookPanel').innerHTML=empty('Ledger unavailable','Refresh to retry. No balances or positions are being assumed.'); return; }
  let rows=model.payload[panel];
  if(panel==='trades') rows=[...rows].reverse();
  const term=$('tradeSearch').value.trim().toLowerCase();
  rows=rows.filter(t=>String(t.symbol||t.instrument||'').toLowerCase().includes(term));
  if(!rows.length) {
    const firstAssessment=model.sessions===0&&model.meta.first_eligible_decision_session?` Next eligible assessment: ${dateLabel(model.meta.first_eligible_decision_session)} after the US close.`:'';
    const message=term?['No matching trades','Try another symbol.']:panel==='positions'?['No open positions',model.state.halted?'The risk guard has halted this book. No new entries will be simulated.':'A position appears only when a saved decision reaches its eligible session and passes the risk checks.'+firstAssessment]:panel==='pending'?['No queued entries',(model.state.status_reason||model.state.reason||'No qualifying decision is currently saved. Stale inputs block new entries.')+firstAssessment]:['No closed trades yet','Completed trades and their actual exit reasons will appear here.'];
    $('bookPanel').innerHTML=empty(...message);return;
  }
  $('bookPanel').innerHTML=`<div class="ws-trades">${rows.map(t=>tradeCard(t,panel)).join('')}</div>`;
}
async function load() {
  if(needsSelection)return;
  const id=++sequence, selected=book;
  controller?.abort();controller=new AbortController();
  $('refreshBook').disabled=true;$('overview').setAttribute('aria-busy','true');
  try {
    const response=await fetch(`/api/paper?book=${selected}&table=state`,{cache:'no-store',signal:controller.signal});
    if(!response.ok) throw new Error(response.status===404?'This book has not been activated in the saved paper ledger yet.':'The saved paper ledger is temporarily unavailable.');
    const candidate=summarize(await response.json(),selected);
    if(id!==sequence||book!==selected)return;
    model=candidate;$('bookError').hidden=true;render();
    const generated=Date.parse(model.payload.generated_at_utc || '');
    if(Number.isFinite(generated) && Date.now()-generated>36*60*60*1000) {
      $('bookError').hidden=false;
      $('bookError').textContent=`Saved snapshot generated ${dateLabel(model.payload.generated_at_utc,true)}. This is not a live quote; weekends and market holidays may explain the gap. Check the scheduled runner if a completed trading session is missing.`;
    }
    set('checkedAt',`Checked ${dateLabel(new Date().toISOString(),true)}`);
  } catch(error) {
    if(error.name==='AbortError'||id!==sequence)return;
    $('bookError').hidden=false;$('bookError').textContent=error.message+(model?' Showing the last successfully loaded snapshot; it may be stale.':'');
    if(!model) { set('bookStatus','Not connected to a verified ledger');renderPanel(); }
  } finally { if(id===sequence){$('refreshBook').disabled=false;$('overview').setAttribute('aria-busy','false');} }
}
function changeBook(next) {
  if(!Object.hasOwn(PROFILES,next)||(next===book&&model))return;
  needsSelection=false;book=next;model=null;const url=new URL(location.href);url.searchParams.set('book',book);history.replaceState(null,'',url);
  for(const id of ['accountEquity','accountReturn','dayPnl','openPnl','closedPnl','maxDrawdown','winRate','dailyHeadroom','maxHeadroom'])set(id,'—');
  for(const id of ['countPositions','countPending','countTrades'])set(id,'0');
  set('bookStatus','Loading authoritative state…');set('dataThrough','');set('sessionCount','— sessions');set('seedDate','Activation: —');set('tradeCount','—');
  $('forwardChart').innerHTML=empty('Loading','');$('dailyMeter').style.width='0%';$('maxMeter').style.width='0%';$('tradeSearch').value='';
  chrome();renderPanel();load();
}
if(invalidRequest){ $('bookError').hidden=false;$('bookError').textContent='Unknown book. Choose Book V6 or Book V10.'; }
for(const button of document.querySelectorAll('[data-book]'))button.addEventListener('click',()=>changeBook(button.dataset.book));
for(const button of document.querySelectorAll('[data-panel]')) {
  button.addEventListener('click',()=>{panel=button.dataset.panel;$('tradeSearch').value='';renderPanel();});
  button.addEventListener('keydown',event=>{const tabs=[...document.querySelectorAll('[data-panel]')];let i=tabs.indexOf(button);if(event.key==='ArrowRight')i=(i+1)%tabs.length;else if(event.key==='ArrowLeft')i=(i+tabs.length-1)%tabs.length;else if(event.key==='Home')i=0;else if(event.key==='End')i=tabs.length-1;else return;event.preventDefault();tabs[i].click();tabs[i].focus();});
}
$('tradeSearch').addEventListener('input',renderPanel);
$('legacyBook').addEventListener('change',event=>{if(['a','b','c','r','s','f'].includes(event.target.value))location.href=`legacy-book.html?book=${event.target.value}`;});
$('refreshBook').addEventListener('click',load);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)load();});
setInterval(()=>{if(!document.hidden)load();},60000);
chrome();if(!invalidRequest)load();
