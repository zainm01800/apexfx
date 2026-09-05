import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {BOOKS,summarizeLegacy,legacyTradeCard,legacyRules} from '../public/legacy-forward-model.js';
import {forwardFixture} from './fixtures/forward-ui.mjs';
const saved=book=>({book_id:book,metadata:{book_id:book,account_currency:BOOKS[book].currency,initial_equity:100000,paper_only:true,broker_enabled:false},daily:[{date:'2026-09-04',equity:100100,cash:99900,day_pnl:20,cum_pnl:100,drawdown:0.01,state_extra:{params:{max_risk_per_trade:.0085}}}],positions:[],trades:[],pending:[]});
test('all eight books have a direct selectable profile',()=>assert.deepEqual(Object.keys(BOOKS),['v6','v10','a','b','c','r','s','f']));
test('legacy GBP totals use saved equity, not added floating profit',()=>{
 const m=summarizeLegacy(saved('c'),'c');assert.equal(m.equity,100100);assert.equal(m.openPnl,200);assert.equal(m.pnl,100);assert.equal(m.tradeRisk,.0085);assert.equal(m.maxFloor,null);
});
test('USD cash-funded holdings are not confused with floating profit',()=>{
 const d=saved('r');d.daily[0].cash=1000;d.positions=[{units:2,entry_price:100,last_px:110}];const m=summarizeLegacy(d,'r');assert.equal(m.openPnl,20);assert.equal(m.currency,'USD');
});
test('missing, wrong-currency or mismatched books fail closed',()=>{
 for(const change of [d=>d.metadata.account_currency='USD',d=>d.book_id='a',d=>delete d.daily[0].equity,d=>d.metadata.initial_equity=null,d=>d.positions=[null]]){const d=saved('c');change(d);assert.throws(()=>summarizeLegacy(d,'c'));}
});
test('Book F uses supplied unrealized P&L, not investment value',()=>{
 const d=saved('f');d.positions=[{unrealized_pnl:28.02,total_pnl:198.02}];assert.equal(summarizeLegacy(d,'f').openPnl,28.02);
});
test('cards preserve stops, targets, partials and saved actions',()=>{
 const card=legacyTradeCard({instrument:'AAPL',direction:'long',entry_price:100,last_px:110,stop:105,target:120,units:2,tms_p1:true,tms_be:true,tms_log:[{action:'chandelier_trail',new_sl:105}]},'positions','b');
 assert.match(card,/Take profit/);assert.match(card,/>120</);assert.match(card,/Triggered/);assert.match(card,/chandelier trail/);assert.match(card,/\+20.00 quote/);assert.doesNotMatch(card,/£20/);assert.doesNotMatch(card,/After 5 completed/);
});
test('nested pending proposals and SMC trigger conditions are retained',()=>{
 assert.match(legacyTradeCard({instrument:'AAPL',dec:100,pos:{stop_price:90,target_price:115,rationale:'Trend confirmed',direction:'long'}},'pending','c'),/Trend confirmed/);
 assert.match(legacyTradeCard({pair:'GBP\/USD',trigger_price:1.3547,condition:'1H Close > Asian high'},'pending','s'),/1H Close &gt; Asian high/);
});
test('legacy content is escaped and missing stops are not V14 stops',()=>{
 const card=legacyTradeCard({symbol:'<script>x</script>',rationale:'<img onerror=x>'},'positions','f');assert.doesNotMatch(card,/<script>|<img /);assert.match(card,/Not supplied/);assert.doesNotMatch(card,/ATR20/);
 assert.match(legacyTradeCard({instrument:'SMH'},'positions','r'),/Not used · monthly rebalance/);
 assert.doesNotMatch(legacyRules(summarizeLegacy(saved('a'),'a'),'a'),/Fixed stop at 1.5/);
});
test('dashboard controller loads and switches every book without navigation or stale profile fields',async()=>{
 const html=fs.readFileSync(new URL('../public/engine-book.html',import.meta.url),'utf8');
 const element=(dataset={})=>({dataset,style:{},parentElement:{hidden:false},value:'',textContent:'',innerHTML:'',hidden:false,disabled:false,attrs:{},listeners:{},setAttribute(k,v){this.attrs[k]=v;},addEventListener(k,fn){this.listeners[k]=fn;},focus(){},click(){this.listeners.click?.();}});
 const els=Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],element()]));
 const books=[...html.matchAll(/data-book="([^"]+)"/g)].map(m=>element({book:m[1]}));
 const tabs=[...html.matchAll(/data-panel="([^"]+)"/g)].map(m=>element({panel:m[1]}));
 const originals=Object.fromEntries(['document','location','history','fetch','setInterval'].map(k=>[k,globalThis[k]]));
 const calls=[];
 try {
  globalThis.document={getElementById:id=>{assert.ok(els[id],`Missing HTML id ${id}`);return els[id];},querySelectorAll:s=>s==='[data-book]'?books:tabs,addEventListener(){},hidden:false};
  globalThis.location={href:'https://example.test/engine-book.html?book=c',replace(){assert.fail('Must not redirect older books');}};
  globalThis.history={replaceState(){}};globalThis.setInterval=()=>0;
  globalThis.fetch=async url=>{const id=new URL(url,'https://example.test').searchParams.get('book');calls.push(id);return {ok:true,json:async()=>BOOKS[id].legacy?saved(id):forwardFixture(id)};};
  await import('../public/forward-books.js');
  const settled=async()=>{for(let i=0;i<10&&els.refreshBook.disabled;i++)await new Promise(setImmediate);assert.equal(els.bookError.hidden,true,els.bookError.textContent);};
  await settled();assert.match(els.accountLabel.textContent,/Book C/);
  for(const button of books){button.click();await settled();const p=BOOKS[button.dataset.book];assert.match(els.accountLabel.textContent,new RegExp(p.name));assert.match(els.accountLabel.textContent,new RegExp(p.currency));assert.equal(els.riskTitle.textContent,p.legacy?'Account allocation':'Loss headroom');for(const tab of tabs)tab.click();}
  assert.ok(calls.includes('a')&&calls.includes('f')&&calls.includes('v10'));
 } finally {for(const [k,v] of Object.entries(originals))if(v===undefined)delete globalThis[k];else globalThis[k]=v;}
});
