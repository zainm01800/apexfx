import { PROFILES, summarize, money, percent, signClass, escapeHtml as e, dateLabel, firstNumber } from './forward-model.js';
const $=id=>document.getElementById(id);
async function get(book) { const r=await fetch(`/api/paper?table=state&book=${book}&limit=500`,{cache:'no-store'}); if(!r.ok)throw new Error('Saved ledger unavailable');return r.json(); }
async function load(){
  $('refreshCompare').disabled=true;
  const cards=await Promise.all(['v6','v10','v24','v30'].map(async book=>{
    const p=PROFILES[book];
    try { const m=summarize(await get(book),book);
      return `<article class="ws-compare-card"><div class="ws-title-row"><h2>${p.name}</h2><span class="paper-pill">${p.maximum*100}% STATIC</span></div><span class="ws-value">${money(m.equity)}</span><p class="${signClass(m.pnl)}">${money(m.pnl,true)} · ${percent(m.pnl/100000)} since activation</p><dl class="ws-trade-grid"><div><dt>Open positions</dt><dd>${m.payload.positions.length}</dd></div><div><dt>Closed trades</dt><dd>${m.payload.trades.length}</dd></div><div><dt>Peak drawdown</dt><dd>${percent(m.maxDD)}</dd></div></dl><p class="ws-meta">${e(m.meta.status||m.state.status||'Paper only')} · ${m.sessions} sessions<br>Ledger: ${dateLabel(m.through)}</p><a class="ws-btn" href="engine-book.html?book=${book}">Inspect ${p.name} →</a></article>`;
    }catch{return `<article class="ws-compare-card"><h2>${p.name}</h2><span class="ws-value">—</span><p>The saved paper ledger is unavailable or has not been activated. No balance is being assumed.</p><a class="ws-btn" href="engine-book.html?book=${book}">Inspect ${p.name} →</a></article>`;}
  }));$('comparisonCards').innerHTML=cards.join('');$('refreshCompare').disabled=false;
}
let legacyLoaded=false;
async function loadLegacy(){
  if(!$('legacyComparison').open||legacyLoaded)return;legacyLoaded=true;
  $('legacyRows').innerHTML='<tr><td colspan="6">Loading saved ledgers…</td></tr>';
  const rows=await Promise.all(['a','b','c','r','s','f'].map(async book=>{
    const currency=['a','b','c'].includes(book)?'GBP':'USD';
    const title=`<a href="engine-book.html?book=${book}">Book ${book.toUpperCase()}</a>`;
    try{const d=await get(book),last=d.daily?.at(-1),equity=firstNumber(last?.equity);if(equity===null)throw new Error();return `<tr><td>${title}</td><td>${currency}</td><td>${money(equity,false,currency)}</td><td>${percent((equity-100000)/100000)}</td><td>${d.positions?.length??'—'}</td><td>${dateLabel(last.date)}</td></tr>`;}catch{return `<tr><td>${title}</td><td>${currency}</td><td colspan="4">Ledger unavailable</td></tr>`;}
  }));$('legacyRows').innerHTML=rows.join('');
}
$('legacyComparison').addEventListener('toggle',loadLegacy);
$('refreshCompare').addEventListener('click',()=>{load();legacyLoaded=false;loadLegacy();});load();
