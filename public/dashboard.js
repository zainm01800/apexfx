'use strict';

// ── Formatters ────────────────────────────────────────────────────────────────

function fmtPrice(n, type) {
  if (n == null || isNaN(n)) return '—';
  if (type === 'Forex') return Number(n).toFixed(5);
  if (n >= 10000) return Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (n >= 100) return Number(n).toFixed(2);
  return Number(n).toFixed(4);
}
function fmtPct(n) {
  if (n == null || isNaN(n)) return '—';
  return `${n >= 0 ? '+' : ''}${Number(n).toFixed(2)}%`;
}
function fmtMCap(n) {
  if (!n) return '—';
  if (n >= 1e12) return `$${(n/1e12).toFixed(2)}T`;
  if (n >= 1e9)  return `$${(n/1e9).toFixed(1)}B`;
  if (n >= 1e6)  return `$${(n/1e6).toFixed(1)}M`;
  return `$${n.toLocaleString()}`;
}
function fmtNum(n, dp = 2) {
  return (n == null || isNaN(n)) ? '—' : Number(n).toFixed(dp);
}

// ── Asset type detection ──────────────────────────────────────────────────────

const CRYPTO_BASES = new Set(['BTC','ETH','SOL','ADA','XRP','DOGE','AVAX','MATIC','DOT','LINK','LTC','ATOM','UNI','AAVE','BNB','SHIB','ARB','OP','SUI','APT','INJ']);
const FOREX_CCY   = new Set(['EUR','GBP','USD','JPY','CHF','AUD','CAD','NZD','SEK','NOK','DKK','HKD','SGD','MXN','ZAR','TRY']);
const ETFS        = new Set(['SPY','QQQ','IWM','GLD','SLV','USO','TLT','HYG','LQD','XLF','XLE','XLK','XLV','XLI','XLC','ARKK','VTI','VOO','VNQ','EEM','EFA','GDX','GDXJ','XBI','IBB','DIA','SMH','SOXX']);
const FUTURES_RX  = /^(ES|NQ|CL|GC|SI|ZB|ZN|ZF|NG|HG|PL|PA|KC|CT|SB)1!$/;

function detectType(raw) {
  const s = raw.toUpperCase().trim();
  if (CRYPTO_BASES.has(s)) return 'Crypto';
  if (CRYPTO_BASES.has(s.replace(/[/-]USD[T]?$/, ''))) return 'Crypto';
  if ((s.endsWith('-USD') || s.endsWith('/USD') || s.endsWith('USDT')) && !FOREX_CCY.has(s.split(/[-/]/)[0])) return 'Crypto';
  if (/^[A-Z]{3}\/[A-Z]{3}$/.test(s) && FOREX_CCY.has(s.split('/')[0]) && FOREX_CCY.has(s.split('/')[1])) return 'Forex';
  if (FUTURES_RX.test(s)) return 'Futures';
  if (ETFS.has(s)) return 'ETF';
  return 'Stock';
}

// ── Technical indicators ─────────────────────────────────────────────────────

function calcSMA(c, p) {
  return c.length < p ? null : c.slice(-p).reduce((a,b)=>a+b,0)/p;
}
function calcEMA(c, p) {
  if (c.length < p) return null;
  const k = 2/(p+1);
  let e = c.slice(0,p).reduce((a,b)=>a+b,0)/p;
  for (let i = p; i < c.length; i++) e = c[i]*k + e*(1-k);
  return e;
}
function calcRSI(c, p = 14) {
  if (c.length < p+2) return null;
  const d = c.slice(1).map((v,i)=>v-c[i]);
  let ag=0, al=0;
  const start = Math.max(0, d.length - p*3);
  for (let i = start; i < start+p; i++) { const x=d[i]||0; x>0?ag+=x:al-=x; }
  ag/=p; al/=p;
  for (let i = start+p; i < d.length; i++) {
    const x=d[i]; ag=(ag*(p-1)+Math.max(0,x))/p; al=(al*(p-1)+Math.max(0,-x))/p;
  }
  return al===0 ? 100 : Math.round(100 - 100/(1+ag/al));
}
function calcMACD(c) {
  const e12=calcEMA(c,12), e26=calcEMA(c,26);
  return (e12&&e26) ? e12-e26 : null;
}
function calcATR(bars, p=14) {
  if (bars.length < p+1) return null;
  const tr = bars.slice(1).map((b,i)=>Math.max(b.high-b.low,Math.abs(b.high-bars[i].close),Math.abs(b.low-bars[i].close)));
  return tr.slice(-p).reduce((a,b)=>a+b,0)/p;
}
function findSR(bars) {
  const r = bars.slice(-40);
  return { support: Math.min(...r.map(b=>b.low)), resistance: Math.max(...r.map(b=>b.high)) };
}
function calcVolTrend(bars) {
  if (bars.length < 20) return 'normal';
  const r5  = bars.slice(-5).reduce((s,b)=>s+b.volume,0)/5;
  const a20 = bars.slice(-20).reduce((s,b)=>s+b.volume,0)/20;
  return r5>a20*1.4?'rising':r5<a20*0.6?'falling':'normal';
}
function getTrend(c, sma20, sma50) {
  if (!sma20||!sma50) return 'sideways';
  const p = c[c.length-1];
  if (p>sma20&&sma20>sma50) return 'bullish';
  if (p<sma20&&sma20<sma50) return 'bearish';
  return p>sma50 ? 'mildly bullish' : 'mildly bearish';
}

// ── API calls ─────────────────────────────────────────────────────────────────

async function fetchCandles(sym, type) {
  const to=Math.floor(Date.now()/1000), from=to-210*86400;
  const r = await fetch(`/api/candles?sym=${encodeURIComponent(sym)}&type=${encodeURIComponent(type)}&tf=1d&from=${from}&to=${to}`);
  if (!r.ok) throw new Error(`Price data unavailable (HTTP ${r.status})`);
  const d = await r.json();
  if (d.error) throw new Error(d.error);
  return d;
}
async function fetchNews(sym, type) {
  try {
    const r = await fetch(`/api/news?sym=${encodeURIComponent(sym)}&type=${encodeURIComponent(type)}`);
    if (!r.ok) return [];
    const d = await r.json();
    return Array.isArray(d) ? d : [];
  } catch { return []; }
}
async function fetchQuote(sym, type) {
  try {
    const r = await fetch(`/api/quote?sym=${encodeURIComponent(sym)}&type=${encodeURIComponent(type)}`);
    if (!r.ok) return null;
    return r.json();
  } catch { return null; }
}

// ── Market pulse ──────────────────────────────────────────────────────────────

async function loadPulse(sym, type, elId) {
  try {
    const to=Math.floor(Date.now()/1000), from=to-5*86400;
    const r = await fetch(`/api/candles?sym=${encodeURIComponent(sym)}&type=${encodeURIComponent(type)}&tf=1d&from=${from}&to=${to}`);
    if (!r.ok) return;
    const bars = await r.json();
    if (!Array.isArray(bars)||bars.length<2) return;
    const el=document.getElementById(elId); if (!el) return;
    const curr=bars[bars.length-1].close, prev=bars[bars.length-2].close;
    const pct=(curr-prev)/prev*100;
    el.classList.remove('loading');
    el.querySelector('.pulse-price').textContent = type==='Forex'?curr.toFixed(5):curr>=100?curr.toFixed(2):curr.toFixed(4);
    const ce=el.querySelector('.pulse-change');
    ce.textContent=fmtPct(pct); ce.className=`pulse-change ${pct>=0?'up':'down'}`;
    el.onclick=()=>quickPick(sym);
  } catch {}
}
function initPulse() {
  loadPulse('SPY','ETF','pulse-SPY');
  loadPulse('QQQ','ETF','pulse-QQQ');
  loadPulse('BTC/USD','Crypto','pulse-BTC');
  loadPulse('EUR/USD','Forex','pulse-EUR');
  loadPulse('GC1!','Futures','pulse-GOLD');
}

// ── UI helpers ────────────────────────────────────────────────────────────────

function showSection(id) {
  ['loadingSection','errorSection','resultsSection'].forEach(s=>{
    document.getElementById(s).style.display = s===id?'':'none';
  });
}
function hideAll() {
  ['loadingSection','errorSection','resultsSection'].forEach(s=>{ document.getElementById(s).style.display='none'; });
}
function setStep(n) {
  for (let i=1;i<=5;i++) {
    const el=document.getElementById(`ls${i}`); if (!el) continue;
    el.className=i<n?'loader-step done':i===n?'loader-step active':'loader-step';
  }
}
function showError(msg) {
  document.getElementById('errorMsg').textContent=msg;
  showSection('errorSection');
  document.getElementById('analyseBtn').disabled=false;
}
function resetState() {
  hideAll();
  document.getElementById('symInput').value='';
  updateTypePill('');
  document.getElementById('analyseBtn').disabled=false;
  document.getElementById('heroSection').scrollIntoView({behavior:'smooth'});
}
function updateTypePill(sym) {
  const pill=document.getElementById('typePill');
  if (!sym.trim()) { pill.className='type-pill'; pill.textContent=''; return; }
  const t=detectType(sym);
  pill.className=`type-pill ${t.toLowerCase()}`; pill.textContent=t;
}
function quickPick(sym) {
  document.getElementById('symInput').value=sym;
  updateTypePill(sym);
  startResearch();
}

// ── Verdict CSS class ─────────────────────────────────────────────────────────

function verdictCls(v) {
  const s=v.toLowerCase().replace(/ /g,'_');
  return ['buy','strong_buy','speculative_buy'].some(x=>s.includes(x.replace('_','')))||s==='buy'||s==='strong_buy'||s==='speculative_buy' ? s : s;
}

// ── Render ────────────────────────────────────────────────────────────────────

function renderResults({ sym, type, candles, quote, news, analysis: a }) {
  const closes = candles.map(c=>c.close);
  const curr   = closes[closes.length-1];
  const prev   = closes[closes.length-2];
  const chgPct = (curr-prev)/prev*100;
  const dp     = type==='Forex'?5:4;

  // ── Verdict card ──
  setText('vcSymbol', sym.toUpperCase());
  setText('vcName',   quote?.name || type);
  const tb=document.getElementById('vcTypeBadge');
  tb.textContent=type; tb.className=`vc-type-badge ${type.toLowerCase()}`;

  setText('vcPrice', fmtPrice(curr,type));
  const ce=document.getElementById('vcChg');
  ce.textContent=fmtPct(chgPct); ce.className=`vc-chg ${chgPct>=0?'up':'down'}`;

  const rawVerdict = (a.verdict||'HOLD').toUpperCase().replace(/ /g,'_');
  const vbadge=document.getElementById('verdictBadge');
  vbadge.textContent=rawVerdict.replace(/_/g,' ');
  vbadge.className=`verdict-badge ${rawVerdict.toLowerCase()}`;

  const conf=Math.min(100,Math.max(0,Number(a.confidence_score)||50));
  setText('confPct', `${conf}%`);
  const fill=document.getElementById('confFill');
  fill.style.width='0%';
  const fc=rawVerdict.toLowerCase();
  fill.className=`conf-fill ${fc}`;
  requestAnimationFrame(()=>setTimeout(()=>{ fill.style.width=`${conf}%`; },80));

  setText('vcThesis',     a.executive_summary || '');
  setText('confNotHigher', a.why_confidence_not_higher ? `Why not higher: ${a.why_confidence_not_higher}` : '');

  // ── Stats grid ──
  const rsi   = calcRSI(closes);
  const sma20 = calcSMA(closes,20);
  const sma50 = calcSMA(closes,50);
  const sma200= calcSMA(closes,200);
  const {support, resistance} = findSR(candles);
  const atr   = calcATR(candles);
  const chg7d = closes.length>7  ? (curr-closes[closes.length-8]) /closes[closes.length-8] *100 : null;
  const chg30d= closes.length>30 ? (curr-closes[closes.length-31])/closes[closes.length-31]*100 : null;

  document.getElementById('statsGrid').innerHTML = [
    { l:'RSI (14)',   v:rsi!=null?String(rsi):'—',           c:rsi?(rsi>70?'down':rsi<30?'up':'neutral'):'' },
    { l:'7-Day',      v:chg7d!=null?fmtPct(chg7d):'—',       c:chg7d!=null?(chg7d>=0?'up':'down'):'' },
    { l:'30-Day',     v:chg30d!=null?fmtPct(chg30d):'—',     c:chg30d!=null?(chg30d>=0?'up':'down'):'' },
    { l:'Support',    v:fmtPrice(support,type),               c:'' },
    { l:'Resistance', v:fmtPrice(resistance,type),            c:'' },
    { l:'ATR (14)',   v:atr!=null?fmtPrice(atr,type):'—',    c:'' },
  ].map(s=>`<div class="stat-item"><div class="stat-label">${s.l}</div><div class="stat-value ${s.c}">${s.v}</div></div>`).join('');

  // ── Macro ──
  const reg = (a.macro_regime||'').toLowerCase().replace(/[_ ]/g,'-');
  const rb=document.getElementById('regimeBadge');
  rb.textContent = a.macro_regime ? a.macro_regime.replace(/-/g,' ').toUpperCase() : '';
  rb.className=`regime-badge ${reg}`;
  setText('macroText', a.macro_environment||'');

  // ── Technical ──
  const macd    = calcMACD(closes);
  const volTrnd = calcVolTrend(candles);
  const trend   = getTrend(closes,sma20,sma50);

  document.getElementById('indicatorsStrip').innerHTML = [
    { l:'Trend',  v:trend,   c:trend.includes('bull')?'bull':trend.includes('bear')?'bear':'neutral' },
    { l:'MACD',   v:macd!=null?(macd>0?'Bullish':'Bearish'):'—', c:macd!=null?(macd>0?'bull':'bear'):'neutral' },
    { l:'SMA20',  v:sma20 ?(curr>sma20 ?'Above':'Below'):'—', c:sma20 ?(curr>sma20 ?'bull':'bear'):'neutral' },
    { l:'SMA50',  v:sma50 ?(curr>sma50 ?'Above':'Below'):'—', c:sma50 ?(curr>sma50 ?'bull':'bear'):'neutral' },
    { l:'SMA200', v:sma200?(curr>sma200?'Above':'Below'):'—', c:sma200?(curr>sma200?'bull':'bear'):'neutral' },
    { l:'Volume', v:volTrnd, c:volTrnd==='rising'?'bull':volTrnd==='falling'?'bear':'neutral' },
  ].map(c=>`<div class="ind-chip"><span class="ic-lbl">${c.l}</span><span class="ic-val ${c.c}">${c.v}</span></div>`).join('');

  setText('techText', a.technical_analysis||'');

  // ── Fundamental ──
  const fundTitle = type==='Forex'?'Macro & FX Context':type==='Crypto'?'On-Chain & Market Context':'Fundamental Analysis';
  setText('fundTitle', fundTitle);
  const val=(a.valuation||'').toLowerCase().replace(/ /g,'-');
  const vb=document.getElementById('valuationBadge');
  vb.textContent=a.valuation?a.valuation.replace(/-/g,' ').toUpperCase():'';
  vb.className=`valuation-badge ${val}`;

  if (quote && type==='Stock') {
    document.getElementById('fundGrid').innerHTML = [
      {k:'Market Cap',     v:fmtMCap(quote.marketCap)},
      {k:'P/E (TTM)',      v:quote.pe?fmtNum(quote.pe,1)+'x':'—'},
      {k:'Forward P/E',   v:quote.forwardPE?fmtNum(quote.forwardPE,1)+'x':'—'},
      {k:'EPS (TTM)',      v:quote.eps?'$'+fmtNum(quote.eps):'—'},
      {k:'52W High',       v:quote.week52High?'$'+fmtNum(quote.week52High):'—'},
      {k:'52W Low',        v:quote.week52Low ?'$'+fmtNum(quote.week52Low) :'—'},
      {k:'Beta',           v:fmtNum(quote.beta)},
      {k:'Rev Growth',     v:quote.revenueGrowth?fmtPct(quote.revenueGrowth*100):'—'},
      {k:'Analyst Target', v:quote.targetMeanPrice?'$'+fmtNum(quote.targetMeanPrice):'—'},
      {k:'Div Yield',      v:quote.dividendYield?fmtPct(quote.dividendYield*100):'—'},
    ].map(i=>`<div class="fund-item"><span class="fund-key">${i.k}</span><span class="fund-val">${i.v}</span></div>`).join('');
  } else {
    document.getElementById('fundGrid').innerHTML='';
  }
  setText('fundText', a.fundamental_analysis||'');

  // ── Sentiment ──
  const sc=(a.sentiment_condition||'').toLowerCase().replace(/ /g,'-');
  const sb=document.getElementById('sentimentBadge');
  sb.textContent=a.sentiment_condition?a.sentiment_condition.replace(/-/g,' ').toUpperCase():'';
  sb.className=`sentiment-badge ${sc}`;
  setText('sentText', a.sentiment_analysis||'');

  // ── Catalysts + news ──
  setText('catalystText', a.catalyst_analysis||'');
  document.getElementById('newsGrid').innerHTML = news.slice(0,4).map(n=>`
    <a class="news-item" href="${n.link||'#'}" target="_blank" rel="noopener noreferrer">
      <div class="news-title">${n.title||''}</div>
      <div class="news-meta">${n.source||''} · ${n.date?new Date(n.date).toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'}):''}</div>
    </a>`).join('');

  // ── Scenarios ──
  const ev=(a.expected_value||'neutral').toLowerCase().replace(/ /g,'-');
  const evb=document.getElementById('evBadge');
  evb.textContent=`EV: ${(a.expected_value||'Neutral').replace(/-/g,' ').toUpperCase()}`;
  evb.className=`ev-badge ${ev}`;

  const sc_data = a.scenarios || {};
  document.getElementById('scenarioGrid').innerHTML = [
    { key:'bull',    label:'Bull',    data:sc_data.bull    },
    { key:'base',    label:'Base',    data:sc_data.base    },
    { key:'bear',    label:'Bear',    data:sc_data.bear    },
    { key:'extreme', label:'Extreme', data:sc_data.extreme },
  ].map(s=>{
    const d=s.data||{};
    const chg=d.upside||d.change||d.downside||'';
    return `<div class="scenario-card ${s.key}">
      <div class="sc-header">
        <span class="sc-label">${s.label}</span>
        <span class="sc-prob">${d.probability!=null?d.probability+'%':'—'}</span>
      </div>
      <div class="sc-target">${d.target||'—'}</div>
      <div class="sc-change">${chg}</div>
      <div class="sc-desc">${d.description||''}</div>
    </div>`;
  }).join('');

  // ── Risk ──
  setText('riskText', a.risk_analysis||'');

  // ── Time horizons ──
  document.getElementById('horizonGrid').innerHTML = [
    { label:'Short-Term (days–4 wks)', text: a.short_term_outlook },
    { label:'Medium-Term (1–3 months)', text: a.medium_term_outlook },
    { label:'Long-Term (3–12 months)', text: a.long_term_outlook },
  ].map(h=>`<div class="horizon-card"><div class="horizon-label">${h.label}</div><div class="horizon-text">${h.text||'—'}</div></div>`).join('');

  // ── Trade levels ──
  document.getElementById('tradeLevels').innerHTML = `
    <div class="trade-level"><div class="tl-label">Entry Zone</div><div class="tl-value entry">${a.entry_zone||'—'}</div></div>
    <div class="trade-level"><div class="tl-label">Stop Loss</div><div class="tl-value stop">${a.stop_loss||'—'}</div></div>
    <div class="trade-level"><div class="tl-label">Target Price</div><div class="tl-value target">${a.target_price||'—'}</div></div>
    <div class="trade-level"><div class="tl-label">Risk : Reward</div><div class="tl-value rr">${a.risk_reward||'—'}</div></div>`;

  // ── Strategy grid ──
  document.getElementById('strategyGrid').innerHTML = [
    { l:'Entry Strategy',        t:a.entry_strategy },
    { l:'Position Sizing',       t:a.position_sizing },
    { l:'Stop Loss Logic',       t:a.stop_loss_logic },
    { l:'Profit Taking',         t:a.profit_taking_logic },
    { l:'Hedging',               t:a.hedging_considerations },
  ].filter(x=>x.t).map(x=>`
    <div class="strategy-item">
      <div class="strategy-label">${x.l}</div>
      <div class="strategy-text">${x.t}</div>
    </div>`).join('');

  setText('tradeTf', a.timeframe?`Suggested holding period: ${a.timeframe}`:'');

  // ── Key reasons ──
  document.getElementById('keyReasonsList').innerHTML =
    (a.key_reasons||[]).map(r=>`<li>${r}</li>`).join('');

  // ── Invalidation ──
  document.getElementById('invalidationList').innerHTML =
    (a.invalidation_conditions||[]).map(c=>`<li>${c}</li>`).join('');
  setText('changeViewText', a.what_would_change_view||'');

  showSection('resultsSection');
  setTimeout(()=>document.getElementById('resultsSection').scrollIntoView({behavior:'smooth',block:'start'}),50);
}

function setText(id, val) {
  const el=document.getElementById(id);
  if (el) el.textContent=val;
}

// ── Main research flow ────────────────────────────────────────────────────────

async function startResearch() {
  const raw=document.getElementById('symInput').value.trim();
  if (!raw) { document.getElementById('symInput').focus(); return; }

  const sym=raw.toUpperCase(), type=detectType(sym);

  document.getElementById('analyseBtn').disabled=true;
  document.getElementById('loaderSym').textContent=sym;
  showSection('loadingSection');
  for (let i=1;i<=5;i++) document.getElementById(`ls${i}`).className='loader-step';
  setStep(1);

  try {
    const candles = await fetchCandles(sym, type);
    if (!candles||candles.length<30) throw new Error(`No price data found for "${sym}". Check the symbol and try again.`);
    setStep(2);

    const closes  = candles.map(c=>c.close);
    const curr    = closes[closes.length-1];
    const dp      = type==='Forex'?5:4;
    const rsi     = calcRSI(closes);
    const macd    = calcMACD(closes);
    const sma20   = calcSMA(closes,20);
    const sma50   = calcSMA(closes,50);
    const sma200  = calcSMA(closes,200);
    const {support, resistance} = findSR(candles);
    const atr     = calcATR(candles);
    const volTrnd = calcVolTrend(candles);
    const trend   = getTrend(closes,sma20,sma50);
    const chg1d   = ((curr-closes[closes.length-2])/closes[closes.length-2]*100).toFixed(2);
    const chg7d   = closes.length>7  ? ((curr-closes[closes.length-8]) /closes[closes.length-8] *100).toFixed(2) : null;
    const chg30d  = closes.length>30 ? ((curr-closes[closes.length-31])/closes[closes.length-31]*100).toFixed(2) : null;

    setStep(3);
    const [news, quote] = await Promise.all([
      fetchNews(sym, type),
      ['Stock','ETF'].includes(type) ? fetchQuote(sym,type) : Promise.resolve(null),
    ]);

    setStep(4);

    const newsText = news.slice(0,8).map(n=>`• ${n.title} (${n.source||'news'}, ${n.date?new Date(n.date).toLocaleDateString():'recent'})`).join('\n');

    let fundBlock = '';
    if (quote && type==='Stock') {
      fundBlock = `
FUNDAMENTAL DATA:
• Market Cap: ${fmtMCap(quote.marketCap)}
• P/E (TTM): ${quote.pe?fmtNum(quote.pe,1)+'x':'N/A'}  |  Forward P/E: ${quote.forwardPE?fmtNum(quote.forwardPE,1)+'x':'N/A'}
• EPS (TTM): ${quote.eps?'$'+fmtNum(quote.eps):'N/A'}
• 52-Week Range: $${fmtNum(quote.week52Low)} – $${fmtNum(quote.week52High)}
• Beta: ${fmtNum(quote.beta)}
• Revenue Growth (YoY): ${quote.revenueGrowth?fmtPct(quote.revenueGrowth*100):'N/A'}
• Earnings Growth: ${quote.earningsGrowth?fmtPct(quote.earningsGrowth*100):'N/A'}
• Analyst Mean Target: ${quote.targetMeanPrice?'$'+fmtNum(quote.targetMeanPrice):'N/A'}
• Analyst Rating: ${quote.recommendationKey||'N/A'}`;
    }

    // ── Institutional-grade prompt ──────────────────────────────────────────
    const prompt = `You are an elite institutional-grade market analyst combining the reasoning styles of top hedge fund analysts, macro strategists, quantitative traders, forensic accountants, behavioral psychologists, and long-term investors.

Your task is to maximize risk-adjusted decision quality using probabilistic reasoning.
You must NEVER default to bullish or bearish bias. Challenge every assumption.
Verdict must be one of EXACTLY: STRONG_BUY, BUY, SPECULATIVE_BUY, HOLD, WAIT, REDUCE_EXPOSURE, AVOID, SHORT, SPECULATIVE_SHORT, HEDGE, NO_EDGE
If no clear edge exists, return NO_EDGE — never force a conclusion.

══════════════════════════════════════════════════
ASSET: ${sym}  |  TYPE: ${type}
══════════════════════════════════════════════════
Current Price:  ${curr.toFixed(dp)}
Daily Change:   ${chg1d}%
7-Day Return:   ${chg7d!==null?chg7d+'%':'N/A'}
30-Day Return:  ${chg30d!==null?chg30d+'%':'N/A'}

TECHNICALS (180-day daily OHLCV):
• Trend (SMA crossover): ${trend}
• RSI(14): ${rsi??'N/A'}${rsi?(rsi>70?' — OVERBOUGHT WARNING':rsi<30?' — OVERSOLD':rsi>60?' — Bullish momentum':rsi<40?' — Bearish momentum':''):''}
• MACD: ${macd!==null?(macd>0?`+${macd.toFixed(dp)} POSITIVE — bullish momentum`:`${macd.toFixed(dp)} NEGATIVE — bearish momentum`):'N/A'}
• vs SMA20  (${sma20?.toFixed(dp)||'N/A'}): ${sma20?(curr>sma20?'ABOVE':'BELOW'):' N/A'}
• vs SMA50  (${sma50?.toFixed(dp)||'N/A'}): ${sma50?(curr>sma50?'ABOVE':'BELOW'):' N/A'}
• vs SMA200 (${sma200?.toFixed(dp)||'N/A'}): ${sma200?(curr>sma200?'ABOVE — long-term uptrend':'BELOW — long-term downtrend'):' N/A'}
• Key Support:    ${support.toFixed(dp)}
• Key Resistance: ${resistance.toFixed(dp)}
• ATR(14): ${atr?.toFixed(dp)||'N/A'}  |  Volume trend: ${volTrnd}
${fundBlock}
RECENT NEWS & CATALYSTS (last 14 days):
${newsText||'No recent news available.'}

══════════════════════════════════════════════════
ANALYSIS FRAMEWORK — perform in this order:

1. MACRO REGIME: Assess current interest rate environment, inflation trajectory, central bank stance (Fed/ECB/BOJ), QT/QE, recession probability, credit conditions, USD strength, global risk appetite. Determine if we are risk-on/off/late-cycle/euphoric. Explain how this SPECIFICALLY impacts ${sym}.

2. ASSET OVERVIEW: What does this company/asset do? Competitive moat, market position, industry dynamics, management quality, long-term viability. For crypto: tokenomics, utility, ecosystem health.

3. FUNDAMENTALS: ${type==='Stock'?'Revenue/earnings growth trajectory, margin trends, FCF generation, debt levels, dilution risk, insider/institutional activity, valuation vs peers and history. Is it cheap for a reason or expensive for a reason?':'For forex: interest rate differentials, current account, economic momentum. For crypto: on-chain activity, adoption, protocol revenue.'}

4. TECHNICALS: Multi-timeframe view. What does the daily structure say? Weekly trend? Is momentum building or exhausting? Are we near key liquidity zones? Breakout or breakdown probability?

5. SENTIMENT: Is the trade crowded? Retail vs institutional positioning. Contrarian signals. Fear/greed. Short interest. Are analysts too bullish or too bearish?

6. CATALYSTS: What are the next material catalysts? Earnings dates, product launches, macro events, regulatory decisions. Which risks are underpriced by the market?

7. RISK: AGGRESSIVELY challenge the bullish and bearish thesis. What could cause a 30%+ drawdown? Hidden risks, black swans, correlation risks, narrative collapse.

8. SCENARIOS: Assign realistic probabilities. Probabilities MUST sum to 100%.

9. DECISION: Apply rigorous decision logic. NO_EDGE is a valid and often correct answer.

══════════════════════════════════════════════════
Respond ONLY with this exact JSON (no markdown, no text outside JSON):

{
  "verdict": "...",
  "confidence_level": "Low|Moderate|High|Very High",
  "confidence_score": <0-100>,
  "executive_summary": "3-4 sentence institutional overview of the opportunity/risk",
  "macro_environment": "3-4 sentences on macro regime and specific impact on this asset",
  "macro_regime": "risk-on|risk-off|late-cycle|recessionary|expansionary|euphoric|fearful|liquidity-driven|fundamentally-driven",
  "fundamental_analysis": "3-4 sentences on fundamentals, valuation, competitive position",
  "valuation": "undervalued|fairly-valued|overvalued|irrationally-priced",
  "technical_analysis": "3-4 sentences on full technical picture across timeframes",
  "sentiment_analysis": "2-3 sentences on positioning, crowding, contrarian signals",
  "sentiment_condition": "excessively-bullish|excessively-bearish|complacent|euphoric|fearful|neutral",
  "catalyst_analysis": "2-3 sentences on material upcoming catalysts and underpriced risks",
  "risk_analysis": "3-4 sentences on key risks and why the thesis could fail",
  "scenarios": {
    "bull":    { "probability": <int>, "target": "<price>", "upside": "<pct>",    "description": "<1 sentence>" },
    "base":    { "probability": <int>, "target": "<price>", "change": "<pct>",    "description": "<1 sentence>" },
    "bear":    { "probability": <int>, "target": "<price>", "downside": "<pct>",  "description": "<1 sentence>" },
    "extreme": { "probability": <int>, "target": "<price>", "downside": "<pct>",  "description": "<1 sentence>" }
  },
  "expected_value": "positive|slightly-positive|neutral|slightly-negative|negative",
  "risk_reward": "<e.g. 2.5:1>",
  "short_term_outlook":  "1-2 sentences on days-to-4-week outlook",
  "medium_term_outlook": "1-2 sentences on 1-3 month outlook",
  "long_term_outlook":   "1-2 sentences on 3-12 month outlook",
  "key_reasons": ["<reason 1>", "<reason 2>", "<reason 3>", "<reason 4>"],
  "invalidation_conditions": ["<condition 1>", "<condition 2>", "<condition 3>"],
  "entry_zone": "<price or range>",
  "stop_loss": "<price>",
  "target_price": "<price>",
  "entry_strategy": "How and when to build the position",
  "position_sizing": "Recommended sizing relative to portfolio and why",
  "stop_loss_logic": "Why this stop level and what it protects against",
  "profit_taking_logic": "When and how to take profits, scaling strategy",
  "hedging_considerations": "Any hedges worth considering",
  "timeframe": "<holding period>",
  "what_would_change_view": "What specific development would flip this thesis",
  "why_confidence_not_higher": "What uncertainty prevents higher confidence"
}`;

    const aiRes = await fetch('/api/ai', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ prompt, model:'llama-3.3-70b-versatile', max_tokens:3500, temperature:0.1, timeoutMs:58000 }),
    });
    if (!aiRes.ok) {
      const e=await aiRes.json().catch(()=>({}));
      throw new Error(e.error||`AI service error (HTTP ${aiRes.status})`);
    }
    const aiData = await aiRes.json();
    if (aiData.error) throw new Error(aiData.error);

    setStep(5);

    let analysis;
    try {
      const m=aiData.text.match(/\{[\s\S]*\}/);
      if (!m) throw new Error('no JSON');
      analysis=JSON.parse(m[0]);
    } catch {
      throw new Error('AI returned an unexpected response format. Please try again.');
    }

    renderResults({ sym, type, candles, quote, news, analysis });
    document.getElementById('analyseBtn').disabled=false;

  } catch (err) {
    showError(err.message||'An unexpected error occurred. Please try again.');
  }
}

// ── Init ──────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', ()=>{
  initPulse();
  const inp=document.getElementById('symInput');
  inp.addEventListener('input',  ()=>updateTypePill(inp.value));
  inp.addEventListener('keydown', e=>{ if(e.key==='Enter') startResearch(); });
  document.getElementById('analyseBtn').addEventListener('click', startResearch);
});
